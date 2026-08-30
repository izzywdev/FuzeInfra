"""Offline guards for the capability-delegation helper (Phase 1b of CAPABILITY_DELEGATION.md).

These lock the two things that make cross-session delegation *safe* rather than a
privilege-escalation footgun, and that a design doc alone cannot prove:

  1. the **envelope** round-trips exactly (the machine-readable header the callee authorizes
     against), including a body that itself contains `]`; and
  2. authorization is **fail-closed** — default DENY, and DENY whenever the sender isn't on
     the callee's `providesTo` allowlist or the capability isn't one it honors.

Plus: the capability→environment registry only points at environments this repo actually
declares (`.fuze/manifest.json` roles.environments), and a not-wired capability resolves to
None (so a caller can't "delegate anywhere"). No cluster, no network.
"""

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
_HELPER = ROOT / "agent-templates/orchestration/capability_delegation.py"


def _load():
    spec = importlib.util.spec_from_file_location("capability_delegation", _HELPER)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves field types via sys.modules[cls.__module__].
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cd = _load()


def _manifest_environments() -> set:
    m = json.loads((ROOT / ".fuze/manifest.json").read_text())
    return set(m["roles"]["environments"])


# --- envelope ------------------------------------------------------------------------

def test_envelope_round_trips():
    line = cd.build_envelope("session_A", "kubectl.read", "get pods -n fuzeinfra", corr="c1")
    env = cd.parse_envelope(line)
    assert env is not None
    assert env.frm == "session_A"
    assert env.cap == "kubectl.read"
    assert env.corr == "c1"
    assert env.reply_to == "session_A"  # defaults to sender
    assert env.body == "get pods -n fuzeinfra"


def test_body_may_contain_bracket():
    # A body with `]` must not be swallowed into the last header value.
    env = cd.parse_envelope("[A2A from=session_A corr=c1 reply_to=session_A cap=gitops.edit] bump [v2]")
    assert env is not None
    assert env.cap == "gitops.edit"
    assert env.body == "bump [v2]"


def test_header_key_order_is_irrelevant():
    env = cd.parse_envelope("[A2A cap=kubectl.read from=session_X corr=z9] hi")
    assert env is not None and env.frm == "session_X" and env.cap == "kubectl.read"


def test_non_envelope_returns_none():
    assert cd.parse_envelope("just a normal turn") is None
    assert cd.parse_envelope("") is None
    assert cd.parse_envelope(None) is None


def test_build_requires_from_and_cap():
    for bad in (("", "cap"), ("session_A", "")):
        try:
            cd.build_envelope(bad[0], bad[1], "body")
            assert False, "expected ValueError for missing from/cap"
        except ValueError:
            pass


# --- authorization (fail-closed) -----------------------------------------------------

def _env(frm="session_A", cap="kubectl.read"):
    return cd.Envelope(frm=frm, cap=cap, body="")


def test_empty_provides_to_denies_everything():
    # Mirrors the manifest default providesTo == [] (accept no callers).
    d = cd.authorize(_env(), provides_to=[], allowed_caps=["kubectl.read"])
    assert d.allowed is False
    assert bool(d) is False


def test_sender_not_on_allowlist_denied():
    d = cd.authorize(_env(frm="session_STRANGER"),
                     provides_to=["session_A"], allowed_caps=["kubectl.read"])
    assert d.allowed is False


def test_capability_not_honored_denied():
    # Sender is allowed, but asks for a capability the callee doesn't honor -> DENY
    # (capabilities are pre-agreed named operations, never arbitrary commands).
    d = cd.authorize(_env(cap="gitops.edit"),
                     provides_to=["session_A"], allowed_caps=["kubectl.read"])
    assert d.allowed is False


def test_allowed_when_sender_and_cap_match():
    d = cd.authorize(_env(), provides_to=["session_A"], allowed_caps=["kubectl.read"])
    assert d.allowed is True
    assert bool(d) is True


def test_none_envelope_denied():
    assert cd.authorize(None, provides_to=["session_A"], allowed_caps=["kubectl.read"]).allowed is False


# --- registry ------------------------------------------------------------------------

def test_wired_capabilities_point_at_declared_environments():
    declared = _manifest_environments()
    for cap, entry in cd.CAPABILITY_REGISTRY.items():
        env = entry["environment"]
        if env is None:
            continue  # not-wired-yet capability (e.g. github.secret.provision)
        assert env in declared, f"{cap} → {env!r} is not a declared roles.environment"


def test_not_wired_capability_resolves_to_none():
    # A caller MUST treat None as "cannot delegate yet", never "delegate anywhere".
    assert cd.capability_environment("github.secret.provision") is None
    assert cd.capability_environment("totally.unknown.cap") is None


def test_capability_environment_lookup():
    assert cd.capability_environment("kubectl.read") == "selfhosted-devops"
    assert cd.capability_environment("gitops.edit") == "cloud-devops"


# --- path selection ------------------------------------------------------------------

def test_local_caller_uses_subscription_path_no_agent_id():
    p = cd.select_path(caller_is_local=True)
    assert p["path"] == "claude-code-session"
    assert p["needs_agent_id"] is False
    assert "subscription" in p["billing"]


def test_non_local_caller_uses_handoff_mcp_api_path():
    p = cd.select_path(caller_is_local=False)
    assert p["path"] == "handoff-mcp"
    assert p["needs_agent_id"] is True
    assert "API" in p["billing"]
