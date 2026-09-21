"""Capability delegation — the deterministic pieces a session and a callee share.

This is the Phase-1b helper for ``CAPABILITY_DELEGATION.md``: the parts of cross-session
delegation that are *pure logic* (envelope format, capability→environment lookup, the
fail-closed authorization check, and the caller-side path selection) live here so every
agent does them **identically** and they can be unit-tested offline. The transport itself
(spawning a session, firing a trigger) is a set of ``claude-code-remote`` MCP tool calls an
agent makes from its own tool namespace — those are documented step-by-step in
``CAPABILITY_DELEGATION_RUNBOOK.md``; this module produces/validates the payloads that flow
through them.

No third-party dependencies (stdlib only) so it imports in any sandbox and in CI.

Envelope (every delegated turn starts with this line — ``CAPABILITY_DELEGATION.md`` §3):

    [A2A from=<sender session_id> corr=<uuid> reply_to=<sender session_id> cap=<capability>] <body>

Challenge handshake (proves a peer is who they claim before registering via a2a_set_peer):

    Caller  → make_challenge(my_session_id)  → sends envelope to peer (cap=ping, body=nonce)
    Callee  → echo_challenge(env, my_id)     → replies with cap=ping.echo, body=nonce, corr=nonce
    Caller  → verify_echo(echo_env, nonce)   → True only when cap+corr+body all match

    ping/ping.echo bypass authorize() intentionally: they happen BEFORE peer registration,
    proving identity, not after it. A callee MUST respond to any well-formed ping with an echo
    and MUST NOT act on any other cap from an unregistered sender.

CLI (for eyeballing / scripting; the logic is the importable functions):

    python capability_delegation.py envelope  --from session_A --cap kubectl.read --body "get pods -n fuzeinfra"
    python capability_delegation.py parse     "[A2A from=session_A corr=... reply_to=session_A cap=kubectl.read] get pods"
    python capability_delegation.py registry  [--cap kubectl.read]
    python capability_delegation.py authorize --from session_A --cap kubectl.read \
        --provides-to session_A --allow-cap kubectl.read
    python capability_delegation.py challenge --from session_A
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional


# --------------------------------------------------------------------------------------
# Capability → owning environment registry (mirrors CAPABILITY_DELEGATION.md §5).
#
# Keys are the `cap=` tokens that appear in the envelope; they name PRE-AGREED operations,
# never arbitrary commands. `environment` is the `environment_id` (from
# `.fuze/manifest.json` roles.environments) that OWNS the credential for that capability —
# the caller spawns a session there (or finds a live one) and delegates; it never receives
# the credential itself. `read_only` flags capabilities safe to auto-honor; the rest keep
# their existing human/GitOps gate (§4.4). `environment: None` means "not wired to any env
# today" — delegation for it must fail closed until Phase 3 provisions the credential.
# --------------------------------------------------------------------------------------
CAPABILITY_REGISTRY: dict[str, dict] = {
    "kubectl.read": {
        "environment": "selfhosted-devops",
        "read_only": True,
        "notes": "prod cluster read (get/logs); prefer the read-only cluster-query.yml for pure reads",
    },
    "gitops.edit": {
        "environment": "cloud-devops",
        "read_only": False,
        "notes": "Helm/Argo/values edit + PR only; FUZE_GITOPS_ONLY, no kubeconfig, never direct prod apply",
    },
    "backend.build": {"environment": "cloud-backend", "read_only": False, "notes": "backend slice"},
    "frontend.build": {"environment": "cloud-frontend", "read_only": False, "notes": "frontend slice"},
    "qa.run": {"environment": "cloud-qa", "read_only": False, "notes": "test/QA slice"},
    "exec.decision": {
        "environment": "cloud-exec",
        "read_only": False,
        "notes": "tenants Exec-{ceo,cto,cfo,ciso}; governed by the frozen exec A2A card contract",
    },
    "github.secret.provision": {
        "environment": None,  # NOT wired to any managed-agent env today (§5).
        "read_only": False,
        "notes": "cloud-devops has `gh` but GITHUB_TOKEN is unset; needs a secret-write token added "
        "to the owning env in Phase 3 before a delegate can actually do it",
    },
    "database.provision": {
        "environment": None,  # NOT wired to any managed-agent env today.
        "read_only": False,
        "notes": "the second half of FuzeInfra's infra-platform A2A tenant "
        "(.claude/skills/fuzeinfra-platform-expert) -- no handler exists yet that turns a "
        "request into an actual data-tier reconciliation. Naming this entry is the "
        "declared intent, same shape as github.secret.provision above; wiring it needs a "
        "real owning environment and a call path into the data-tier reconciler before any "
        "delegate may honor it as done rather than as UNSUPPORTED.",
    },
}


# Header value tokens are non-whitespace AND non-`]` — so a body that itself contains a
# `]` (e.g. "bump image [v2]") can't be swallowed into the last header value.
_VAL = r"[^\]\s]+"
ENVELOPE_RE = re.compile(
    r"^\[A2A"
    rf"(?=[^\]]*\bfrom=(?P<frm>{_VAL}))"
    rf"(?=[^\]]*\bcorr=(?P<corr>{_VAL}))"
    rf"(?=[^\]]*\bcap=(?P<cap>{_VAL}))"
    rf"(?:[^\]]*\breply_to=(?P<reply_to>{_VAL}))?"
    r"[^\]]*\]\s?(?P<body>.*)$",
    re.DOTALL,
)


@dataclass
class Envelope:
    """A parsed / to-be-built delegation envelope."""

    frm: str
    cap: str
    body: str
    corr: str = field(default_factory=lambda: str(uuid.uuid4()))
    reply_to: Optional[str] = None

    def __post_init__(self) -> None:
        # reply_to defaults to the sender: the callee fires its reply back at `from`.
        if self.reply_to is None:
            self.reply_to = self.frm

    def render(self) -> str:
        return (
            f"[A2A from={self.frm} corr={self.corr} "
            f"reply_to={self.reply_to} cap={self.cap}] {self.body}"
        )


def build_envelope(
    frm: str,
    cap: str,
    body: str,
    corr: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> str:
    """Render the envelope line for a delegated turn. `corr` is generated if omitted."""
    if not frm or not cap:
        raise ValueError("both `from` (sender session_id) and `cap` are required")
    env = Envelope(frm=frm, cap=cap, body=body, reply_to=reply_to)
    if corr:
        env.corr = corr
    return env.render()


def parse_envelope(text: str) -> Optional[Envelope]:
    """Parse a delegated turn's opening envelope. Returns None if it isn't one.

    Order-independent for the header keys, and tolerant of a body that itself contains a
    ``]`` — only the FIRST ``]`` closes the header.
    """
    if text is None:
        return None
    m = ENVELOPE_RE.match(text.strip())
    if not m:
        return None
    return Envelope(
        frm=m.group("frm"),
        cap=m.group("cap"),
        body=m.group("body"),
        corr=m.group("corr"),
        reply_to=m.group("reply_to") or m.group("frm"),
    )


def capability_environment(cap: str) -> Optional[str]:
    """The environment_id that owns `cap`, or None if unknown/not-wired.

    None is returned both for an unknown capability and for a known-but-unwired one
    (`github.secret.provision` today) — a caller MUST treat None as "cannot delegate this
    yet", never as "delegate anywhere".
    """
    entry = CAPABILITY_REGISTRY.get(cap)
    return entry["environment"] if entry else None


@dataclass
class Decision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:  # so `if authorize(...):` reads naturally
        return self.allowed


def authorize(
    envelope: Envelope,
    provides_to: Iterable[str],
    allowed_caps: Iterable[str],
) -> Decision:
    """The CALLEE's fail-closed check (CAPABILITY_DELEGATION.md §4). Default DENY.

    A request is honored only if BOTH hold:
      1. the sender (`envelope.frm`) is on `provides_to` — the callee-owned allowlist
         (`.fuze/manifest.json` `providesTo`, currently `[]` = accept no callers), and
      2. `envelope.cap` is one of `allowed_caps` — a pre-agreed, capability-scoped
         operation this callee honors (never an arbitrary command string).

    An empty `provides_to` denies everything (matches the fail-closed manifest default).
    This function AUTHORIZES only; it never executes and never returns a credential — the
    caller of this function maps an allowed `cap` to its own vetted action and returns a
    result/summary only.
    """
    provides_to = set(provides_to or ())
    allowed_caps = set(allowed_caps or ())

    if envelope is None:
        return Decision(False, "no envelope — refusing (fail-closed)")
    if not provides_to:
        return Decision(False, "providesTo is empty — accept no callers (fail-closed default)")
    if envelope.frm not in provides_to:
        return Decision(False, f"sender {envelope.frm!r} not on providesTo allowlist — refused")
    if envelope.cap not in allowed_caps:
        return Decision(
            False,
            f"capability {envelope.cap!r} not in this callee's allowed set — refused "
            "(capabilities are pre-agreed named operations, not arbitrary commands)",
        )
    return Decision(True, f"sender allowed and capability {envelope.cap!r} is honored")


# --------------------------------------------------------------------------------------
# Challenge handshake — proves peer identity before a2a_set_peer registration.
#
# Use this BEFORE registering an unknown peer, not as a substitute for authorize().
# The three-step flow:
#   1. caller:   line, nonce = make_challenge(my_session_id)
#                → send `line` as the peer's next turn
#   2. callee:   echo = echo_challenge(parse_envelope(incoming), my_session_id)
#                → send `echo` back to the caller (reply_to address from the envelope)
#   3. caller:   ok = verify_echo(parse_envelope(echo_line), nonce)
#                → True ⟹ peer proved it controls that session; call a2a_set_peer
# --------------------------------------------------------------------------------------

CHALLENGE_CAP = "ping"
CHALLENGE_ECHO_CAP = "ping.echo"


def make_challenge(sender_session_id: str) -> tuple[str, str]:
    """Build a ping challenge envelope. Returns (envelope_line, nonce).

    Send `envelope_line` to the peer as its next turn.  When the peer replies, pass the
    reply text to ``parse_envelope`` then ``verify_echo(..., nonce)``.
    """
    nonce = str(uuid.uuid4())
    line = build_envelope(frm=sender_session_id, cap=CHALLENGE_CAP, body=nonce, corr=nonce)
    return line, nonce


def echo_challenge(ping_env: Optional[Envelope], responder_session_id: str) -> Optional[str]:
    """If ``ping_env`` is a ping challenge, build and return the echo reply envelope.

    Returns None if ``ping_env`` is None or its cap is not ``ping`` — so a callee can
    call this unconditionally and only send a reply when it returns a non-None string.
    The echo carries the original nonce as both ``corr`` and ``body`` so the caller can
    verify either field.
    """
    if ping_env is None or ping_env.cap != CHALLENGE_CAP:
        return None
    return build_envelope(
        frm=responder_session_id,
        cap=CHALLENGE_ECHO_CAP,
        body=ping_env.corr,
        corr=ping_env.corr,
        reply_to=ping_env.reply_to,
    )


def verify_echo(echo_env: Optional[Envelope], expected_nonce: str) -> bool:
    """Verify a ping echo from a peer. Returns True only when all three hold:

      - ``echo_env.cap == "ping.echo"``
      - ``echo_env.corr == expected_nonce``  (the UUID we generated)
      - ``echo_env.body.strip() == expected_nonce``  (the peer echoed the nonce in the body)

    A False result means the peer is not who it claims — do NOT call a2a_set_peer.
    """
    if echo_env is None:
        return False
    return (
        echo_env.cap == CHALLENGE_ECHO_CAP
        and echo_env.corr == expected_nonce
        and (echo_env.body or "").strip() == expected_nonce
    )


def select_path(caller_is_local: bool) -> dict:
    """Which transport a caller uses, keyed on where the CALLER runs (§2b).

    Local/desktop → spawn a Claude Code session in the target environment by name
    ("DevOps"): subscription/plan usage, no agent_id, the unblocked+cheaper path.
    Non-local → handoff-mcp `spawn_agent(role)`: API-billed, needs credit + populated id
    maps. Both carry the same envelope and the same fail-closed authz.
    """
    if caller_is_local:
        return {
            "path": "claude-code-session",
            "how": 'spawn a Claude Code session in the target environment by name ("DevOps") '
            "or create_session(environment_id=<owning env>)",
            "billing": "subscription/plan usage",
            "needs_agent_id": False,
        }
    return {
        "path": "handoff-mcp",
        "how": 'spawn_agent("<role>", task, reply_to_session_id=<self>)',
        "billing": "Anthropic API credit",
        "needs_agent_id": True,
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("envelope", help="render a delegation envelope line")
    pe.add_argument("--from", dest="frm", required=True)
    pe.add_argument("--cap", required=True)
    pe.add_argument("--body", default="")
    pe.add_argument("--corr")
    pe.add_argument("--reply-to")

    pp = sub.add_parser("parse", help="parse an envelope line to JSON")
    pp.add_argument("text")

    pr = sub.add_parser("registry", help="show the capability→environment registry")
    pr.add_argument("--cap", help="look up a single capability")

    pa = sub.add_parser("authorize", help="run the fail-closed callee check")
    pa.add_argument("--from", dest="frm", required=True)
    pa.add_argument("--cap", required=True)
    pa.add_argument("--provides-to", nargs="*", default=[])
    pa.add_argument("--allow-cap", dest="allow_caps", nargs="*", default=[])

    pc = sub.add_parser("challenge", help="build a ping challenge envelope (step 1 of handshake)")
    pc.add_argument("--from", dest="frm", required=True, help="your session_id")

    args = p.parse_args(argv)

    if args.cmd == "envelope":
        print(build_envelope(args.frm, args.cap, args.body, corr=args.corr, reply_to=args.reply_to))
        return 0

    if args.cmd == "parse":
        env = parse_envelope(args.text)
        if env is None:
            print("not an A2A envelope", file=sys.stderr)
            return 1
        print(json.dumps(env.__dict__, indent=2))
        return 0

    if args.cmd == "registry":
        if args.cap:
            entry = CAPABILITY_REGISTRY.get(args.cap)
            if entry is None:
                print(f"unknown capability {args.cap!r}", file=sys.stderr)
                return 1
            print(json.dumps({args.cap: entry}, indent=2))
            return 0
        print(json.dumps(CAPABILITY_REGISTRY, indent=2))
        return 0

    if args.cmd == "authorize":
        env = Envelope(frm=args.frm, cap=args.cap, body="")
        d = authorize(env, provides_to=args.provides_to, allowed_caps=args.allow_caps)
        print(json.dumps({"allowed": d.allowed, "reason": d.reason}, indent=2))
        return 0 if d.allowed else 2

    if args.cmd == "challenge":
        line, nonce = make_challenge(args.frm)
        print(json.dumps({"envelope": line, "nonce": nonce}, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
