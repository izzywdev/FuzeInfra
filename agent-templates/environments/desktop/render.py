#!/usr/bin/env python3
"""Render a Managed-Agents environment JSON into the three fields the Claude Code
**desktop/web cloud environment** dialog takes.

Why this exists: the desktop environment picker's "Cloud" section is NOT the
Managed-Agents `/v1/environments` API. It is a claude.ai account resource with no
public API, and its config shape is completely different — `name`, a network-access
level, an `.env` block, and a Bash **setup script**. There is no `packages` field.

So one source of truth (`../cloud-*.json`) feeds two consumers:

    ../cloud-fuze.json  ──┬──► providers/provision.py  ──► POST /v1/environments
                          └──► render.py               ──► fuze.setup.sh + .domains.txt + .env
                                                             (paste into claude.ai/code)

Usage:
    python render.py                      # regenerate every mapped environment
    python render.py --check              # exit 1 if the committed output is stale (CI)
    python render.py --env cloud-devops   # just one

Setup-script constraints this generator respects (per
https://code.claude.com/docs/en/cloud-environments.md):
  * **Exit zero** — a non-zero exit makes the session fail to start, so every
    install is `|| true`.
  * **Finish within ~5 minutes** — independent package managers run in parallel
    with `&`/`wait`; only apt is serialised, because dpkg takes a global lock.
  * **Runs as root**, before Claude Code launches, and only when no cached
    environment snapshot exists.
  * Binaries go to `/usr/local/bin` (via `GOBIN`), not `$HOME/go/bin`, so they are
    on `PATH` for whatever user Claude's commands run as.
  * **No GitHub release downloads.** The GitHub proxy scopes release-asset requests
    to repositories attached to the session, so pulling a release from an unattached
    repo returns 403. Go tools install through `proxy.golang.org` instead.
"""
import argparse
import json
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_DIR = os.path.dirname(HERE)

# basename of ../<key>.json  ->  the desktop environment it renders to.
#   picker_name : the Name field, i.e. what shows in the desktop picker.
#   env         : non-secret .env lines. NEVER put credentials here — cloud
#                 environments have no secrets store and every value is readable
#                 by anyone using the environment.
#   extras      : shell snippets for tools no package-manager field can express.
MAPPING = {
    "cloud-fuze": {
        "picker_name": "Fuze",
        "summary": "General FuzeOne agentic-dev environment: the shared toolchain every "
                   "domain environment builds on.",
        "env": [
            ("FUZE_ENV_ROLE", "fuze", "Which FuzeOne environment this session is running in."),
            ("PYTHONUNBUFFERED", "1", "Stream pytest/python output instead of buffering it."),
        ],
        "extras": [],
        "report": ["pytest", "yamllint", "check-jsonschema", "prettier"],
    },
    "cloud-devops": {
        "picker_name": "DevOps",
        "summary": "GitOps slice: edit Helm/Argo/values, lint + validate, open a PR. "
                   "Deliberately has NO prod cluster access.",
        "env": [
            ("FUZE_ENV_ROLE", "devops", "Which FuzeOne environment this session is running in."),
            ("FUZE_GITOPS_ONLY", "true",
             "Hard boundary marker: this env edits manifests and opens PRs. It holds no "
             "kubeconfig; direct kubectl against prod stays on fuzeinfra-selfhosted-devops."),
            ("HELM_DIFF_COLOR", "false", "Plain-text helm output reads better in a transcript."),
            ("FUZE_A2A_BRIDGE", "1",
             "Opt this env into the A2A bridge SessionStart hook (a2a-bridge/). Cloud-only: "
             "starts a cloudflared quick tunnel + inbound server for cloud<->cloud messaging."),
        ],
        "extras": ["helm"],
        "needs_cloudflared": True,
        "report": ["yamllint", "check-jsonschema", "cloudflared"],
    },
}

# Tools that no `config.packages` field can express, keyed by name.
EXTRAS = {
    # helm is not in the Ubuntu archive and its releases live on get.helm.sh (hence
    # that host in cloud-devops.json's allowed_hosts). The version is resolved at
    # runtime from helm-latest-version so there is no pin to go stale, and a
    # go-install fallback means a failed download still leaves a usable helm.
    "helm": """( echo "[setup] helm"
  HELM_VER="$(curl -fsSL https://get.helm.sh/helm-latest-version 2>/dev/null | tr -d '[:space:]')"
  if [ -n "${HELM_VER:-}" ] \\
     && curl -fsSL "https://get.helm.sh/helm-${HELM_VER}-linux-amd64.tar.gz" -o /tmp/helm.tgz \\
     && tar -xzf /tmp/helm.tgz -C /tmp linux-amd64/helm; then
    install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm || true
  else
    echo "[setup] helm: get.helm.sh unavailable, building from source" >&2
    GOBIN=/usr/local/bin go install helm.sh/helm/v3/cmd/helm@latest || true
  fi ) &""",
}


def load(basename):
    with open(os.path.join(ENV_DIR, f"{basename}.json"), encoding="utf-8") as f:
        return json.load(f)


def render_setup(basename, spec, doc):
    """Build the Bash setup script for the environment dialog's Setup script field."""
    pkgs = doc["config"].get("packages", {})
    L = [
        "#!/bin/bash",
        f"# {spec['picker_name']} — Claude Code cloud environment setup script.",
        f"# {spec['summary']}",
        "#",
        f"# GENERATED from agent-templates/environments/{basename}.json by",
        "# agent-templates/environments/desktop/render.py — do not edit by hand.",
        "# Paste into the Setup script field of the environment dialog at claude.ai/code.",
        "#",
        "# Must exit 0: a non-zero exit makes the session fail to start, so every install",
        "# is || true. Independent installs run in parallel to stay under the ~5 min budget.",
        "set -u",
        "",
    ]

    if pkgs.get("apt"):
        L += [
            "# apt is serialised — dpkg holds a global lock, so parallel installs deadlock.",
            'echo "[setup] apt"',
            "apt-get update -qq || true",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            + " ".join(pkgs["apt"]) + " || true",
            "",
        ]

    if spec.get("needs_cloudflared"):
        # Serial (uses dpkg). GitHub release assets are 403'd by the session proxy for
        # unattached repos, so cloudflared installs from Cloudflare's apt repo instead
        # (pkg.cloudflare.com, under the *.cloudflare.com allowlist).
        L += [
            'echo "[setup] cloudflared"',
            "install -d -m 0755 /usr/share/keyrings",
            "curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg "
            "-o /usr/share/keyrings/cloudflare-main.gpg || true",
            "echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
            "https://pkg.cloudflare.com/cloudflared any main' "
            "> /etc/apt/sources.list.d/cloudflared.list",
            "apt-get update -qq || true",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflared || true",
            "",
        ]

    parallel = []
    if pkgs.get("pip"):
        # Ubuntu 24.04 marks its Python as externally managed (PEP 668), so a root
        # `pip install` can refuse outright. Retry with --break-system-packages before
        # giving up — the session VM is disposable, so there is nothing to protect.
        # shlex.quote each spec so version constraints with shell metachars survive,
        # e.g. `mcp>=1.9,<2` would otherwise be read as redirections.
        spec_pkgs = " ".join(shlex.quote(p) for p in pkgs["pip"])
        parallel.append(
            '( echo "[setup] pip"; pip install --quiet --no-input ' + spec_pkgs
            + " || pip install --quiet --no-input --break-system-packages " + spec_pkgs
            + " || true ) &"
        )
    if pkgs.get("npm"):
        parallel.append(
            '( echo "[setup] npm"; npm install -g --silent '
            + " ".join(shlex.quote(p) for p in pkgs["npm"]) + " || true ) &"
        )
    for mod in pkgs.get("go", []):
        # GOBIN=/usr/local/bin so the binary is on PATH for every user, not just root.
        parallel.append(
            f'( echo "[setup] go {mod}"; GOBIN=/usr/local/bin go install {mod} || true ) &'
        )
    for mod in pkgs.get("cargo", []):
        parallel.append(f'( echo "[setup] cargo {mod}"; cargo install --quiet {mod} || true ) &')
    for mod in pkgs.get("gem", []):
        parallel.append(f'( echo "[setup] gem {mod}"; gem install --silent {mod} || true ) &')
    parallel += [EXTRAS[name] for name in spec["extras"]]

    if parallel:
        L += ["# Independent of each other — run concurrently, then wait."] + parallel + ["wait", ""]

    L += [
        "# Leave a record in the session log of what actually landed.",
        'echo "[setup] installed:"',
        "for b in " + " ".join(_expected_binaries(spec, pkgs)) + "; do",
        '  printf "  %-12s %s\\n" "$b" "$(command -v "$b" 2>/dev/null || echo MISSING)"',
        "done",
        "",
        "# Always succeed: a failed optional install must not block the session.",
        "exit 0",
        "",
    ]
    return "\n".join(L)


def _expected_binaries(spec, pkgs):
    """Binaries worth reporting at the end of the script.

    apt/go/extras names map 1:1 to a binary. pip and npm names often don't (a
    distribution can install zero or several console scripts), so those come from the
    mapping's explicit `report` list rather than being guessed from the package name.
    """
    out = list(pkgs.get("apt", []))
    out += [m.rsplit("/", 1)[-1].split("@", 1)[0] for m in pkgs.get("go", [])]
    out += list(spec["extras"])
    out += list(spec.get("report", []))
    return out or ["git"]


def render_domains(basename, spec, doc):
    """The Allowed domains field — only needed when networking.allowed_hosts is non-empty."""
    hosts = doc["config"].get("networking", {}).get("allowed_hosts") or []
    header = [
        f"# {spec['picker_name']} — Allowed domains for the cloud environment dialog.",
        f"# GENERATED from {basename}.json by render.py — do not edit by hand.",
        "#",
    ]
    if not hosts:
        header += [
            "# No extra hosts needed: set Network access to **Trusted**, which already covers",
            "# package registries, GitHub, and the cloud SDKs. Leave this list unused.",
            "",
        ]
        return "\n".join(header)
    header += [
        "# Set Network access to **Custom**, paste the hosts below one per line, and CHECK",
        '# "Also include default list of common package managers" — the installs below still',
        "# need PyPI / npm / proxy.golang.org / the Ubuntu archive.",
        "#",
        "# GitHub traffic and MCP connector traffic do NOT go through this allowlist.",
        "",
    ]
    return "\n".join(header + hosts + [""])


def render_env(basename, spec, doc):
    """The Environment variables field (.env format). Non-secret values only."""
    # Kept deliberately short: this block is pasted into the dialog, so the header has
    # to be the warning the next editor needs and nothing more. The full rationale
    # (why GITHUB_TOKEN/ANTHROPIC_API_KEY are absent, why MCP is per-session) is in
    # this directory's README.md.
    L = [
        f"# {spec['picker_name']} — generated from {basename}.json (agent-templates/environments).",
        "# NO SECRETS: every value here is readable by anyone who uses this environment.",
        "# GITHUB_TOKEN is deliberately unset so the GitHub proxy injects credentials instead.",
        "",
    ]
    for key, val, why in spec["env"]:
        L += [f"# {why}", f"{key}={val}", ""]
    return "\n".join(L)


TARGETS = (
    ("setup.sh", render_setup),
    ("domains.txt", render_domains),
    ("env", render_env),
)


def outputs(basename):
    spec = MAPPING[basename]
    doc = load(basename)
    stem = spec["picker_name"].lower()
    return {f"{stem}.{ext}": fn(basename, spec, doc) for ext, fn in TARGETS}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", action="append", choices=sorted(MAPPING),
                    help="only render these (default: all)")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if committed output differs from freshly rendered output")
    args = ap.parse_args()

    stale = []
    for basename in args.env or sorted(MAPPING):
        for name, body in outputs(basename).items():
            path = os.path.join(HERE, name)
            current = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    current = f.read()
            if args.check:
                if current != body:
                    stale.append(name)
                continue
            if current == body:
                print(f"[ok]      {name}")
                continue
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            print(f"[written] {name}")

    if args.check:
        if stale:
            print("STALE (re-run `python render.py`): " + ", ".join(stale), file=sys.stderr)
            return 1
        print("[ok] committed desktop projection matches the environment JSONs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
