#!/usr/bin/env python3
"""check_handoff_coverage — every portal product must have a token delivery path.

WHY THIS EXISTS. On 2026-08-20 seven repos were found reading the
`fuzefront-registration` Secret with NO entry in governance/credential-handoff.json:
fuzecontact, fuzehub, fuzekeys, fuzemarket, fuzepicker, fuzesales, fuzeservice.

Six of them ALREADY HAD a sealed manifest committed, hand-sealed once, at five
different ad-hoc paths. That is what made this invisible: they look provisioned.
The file is right there in the repo. But a hand-seal works exactly once — with no
registry entry nothing can ever re-seal it, so the moment the token rotates every
one of them silently goes stale and starts failing with a credential that is
present and wrong. fuzemarket was caught doing exactly that:

    [register] FATAL: GET /apps/market returned unexpected HTTP 401:
    {"error":"Invalid token."}

THE STRUCTURAL CAUSE, which is what this check is really aimed at:
sdlc-bootstrap's `portal-registration` capability installs the whole CONSUMER
side — check-registration.mjs, sync-chart-files.sh, the seeded manifest, the
fail-closed init container — and nothing installs the PLATFORM side. Bootstrap
provisions a repo to DEMAND a token and provisions nobody to DELIVER one. An
absent registry entry produces no run, no log line and no failure, so the gap
opens in silence and stays open until a pod nobody is tailing starts 401ing.

WHAT IT CHECKS. For every repo in the org whose `.fuze/manifest.json` says it is
a portal product, assert governance/credential-handoff.json has an enabled entry
targeting its namespace. The "is a portal product" test mirrors
sdlc-bootstrap's portal_registration.declared() exactly — opt out with
`portal.registers: false`, or by being tier governance/infra — so this cannot
disagree with the installer about who needs a token.

NOT a substitute for deriving the registry from the manifests, which would make
the gap structurally impossible rather than merely detected. It is the cheap half
that needs no change to how credentials flow.

Usage:  check_handoff_coverage.py [--org izzywdev] [--json]
Requires: gh (authenticated). Exits 1 on any uncovered product.
"""
import argparse
import json
import subprocess
import sys


def gh_json(*args):
    """Run gh and parse JSON, failing loudly — a silent [] here would report all-clear."""
    proc = subprocess.run(("gh",) + args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"::error::gh {' '.join(args)} failed: {proc.stderr.strip()}\n")
        sys.exit(1)
    return json.loads(proc.stdout or "null")


def manifest_for(org, repo):
    """Fetch .fuze/manifest.json via the API. Absent manifest -> not onboarded -> not our problem."""
    proc = subprocess.run(
        ("gh", "api", f"repos/{org}/{repo}/contents/.fuze/manifest.json",
         "--jq", ".content"),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    import base64
    try:
        return json.loads(base64.b64decode(proc.stdout.strip()))
    except Exception as exc:
        sys.stderr.write(f"::warning::{repo}: .fuze/manifest.json did not parse ({exc})\n")
        return None


def is_portal_product(mf):
    """Mirrors sdlc-bootstrap caps/portal_registration.declared(). Keep the two in step:
    if they disagree, this check either nags repos that opted out or misses ones that did not."""
    portal = mf.get("portal")
    if isinstance(portal, dict) and portal.get("registers") is False:
        return False, "portal.registers is false"
    if mf.get("tier") in ("governance", "infra"):
        return False, f"tier={mf.get('tier')}"
    return True, "portal product"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="izzywdev")
    ap.add_argument("--registry", default="governance/credential-handoff.json")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args()

    with open(args.registry, encoding="utf-8") as fh:
        registry = json.load(fh)
    covered = {
        e["target"]["namespace"]
        for e in registry["handoffs"]
        if e.get("enabled") and "registration" in e["id"]
    }
    # THE PUBLISHER IS NOT A CONSUMER. FuzeFront's repo references
    # fuzefront-registration because it CREATES the Secret -- the seed Job that mints
    # the token lives there. Code search cannot tell "mints it" from "consumes it", so
    # without this the platform repo is reported as a product missing its own delivery
    # path, which is nonsense and would train readers to ignore the finding.
    #
    # Derived from the registry's own source.namespace values rather than hardcoding
    # "fuzefront": if the source ever moves, the exclusion follows it instead of
    # silently protecting the wrong repo.
    sources = {
        e["source"]["namespace"]
        for e in registry["handoffs"]
        if isinstance(e.get("source"), dict) and e["source"].get("namespace")
    }

    # A DISABLED entry is deliberately NOT coverage. fuzequality is disabled because its
    # repo cannot be resolved; if that repo comes back it must be re-enabled, and this
    # check is the thing that will say so rather than letting it pass as "listed".
    disabled = {
        e["target"]["namespace"]
        for e in registry["handoffs"]
        if not e.get("enabled") and "registration" in e["id"]
    }

    repos = gh_json("repo", "list", args.org, "--limit", "200", "--json", "name", "--jq", "[.[].name]")
    if not repos:
        sys.stderr.write("::error::gh returned no repositories — refusing to report all-clear on an empty scan.\n")
        sys.exit(1)

    # WHICH REPOS ACTUALLY CONSUME THE SECRET. One org-wide code search, not one call
    # per repo. This is the difference between a LIVE gap and a LATENT one:
    #   - references the Secret + no entry -> its pod fails today. ERROR.
    #   - portal product, no reference yet  -> will need an entry when it wires up,
    #                                          but nothing is broken now. WARNING.
    # Without this split the check fails repos that have not adopted registration yet,
    # which is enforcement ahead of adoption -- the mistake that made gate-identifier
    # block every rollout PR. A gate nobody can satisfy gets `|| true` bolted on and
    # then protects nothing.
    # Extract names in PYTHON, not with --jq. The first version used
    # `--jq '[.[].repository.name]'` and crashed in CI with
    # `AttributeError: 'NoneType' object has no attribute 'lower'` -- the jq
    # produced nulls for entries whose shape did not match. The unit tests could
    # not catch it because they stub gh_json, so they exercise the logic and never
    # the gh boundary. Parsing defensively here means a schema surprise degrades to
    # the guarded "search failed" error below instead of a traceback.
    raw = gh_json("search", "code", "fuzefront-registration",
                  "--owner", args.org, "--limit", "100", "--json", "repository") or []
    consumers = set()
    for hit in raw:
        if not isinstance(hit, dict):
            continue
        repo_obj = hit.get("repository") or {}
        name = repo_obj.get("name") or repo_obj.get("nameWithOwner", "").rpartition("/")[2]
        if name:
            consumers.add(name.lower())
    if not consumers:
        # Never downgrade every finding to a warning because search came back empty --
        # that turns a real outage into a quiet notice. Same reasoning as the empty-repo
        # guard above.
        sys.stderr.write(
            "::error::code search returned no consumers of fuzefront-registration. "
            "At least one is known to exist, so this is a search failure, not a clean "
            "fleet — refusing to reclassify live gaps as warnings.\n")
        sys.exit(1)

    missing, latent, skipped, ok = [], [], [], []
    for repo in sorted(repos):
        mf = manifest_for(args.org, repo)
        if mf is None:
            continue  # not onboarded to the SDLC at all
        wanted, why = is_portal_product(mf)
        ns = repo.lower()
        if ns in sources:
            skipped.append((repo, "publisher of the Secret, not a consumer"))
        elif not wanted:
            skipped.append((repo, why))
        elif ns in covered:
            ok.append(repo)
        elif ns in consumers:
            missing.append((repo, ns, "entry exists but is DISABLED" if ns in disabled else "no entry"))
        else:
            latent.append((repo, ns))

    if args.json:
        print(json.dumps({"ok": ok, "missing": [list(m) for m in missing],
                          "latent": [list(l) for l in latent],
                          "skipped": [list(s) for s in skipped]}, indent=2))
    else:
        print(f"portal products with a delivery path : {len(ok)}")
        print(f"opted out / not a product            : {len(skipped)}")
        for repo, why in skipped:
            print(f"    skip {repo} ({why})")
        if latent:
            print(f"\n{len(latent)} portal product(s) not consuming the Secret yet (will need an entry):")
            for repo, ns in latent:
                print(f"    ~ {repo} (namespace {ns})")
                print(f"::warning title=handoff-coverage::{repo} is a portal product with no hand-off entry. "
                      f"Nothing is broken yet -- it does not reference fuzefront-registration -- but it will "
                      f"need one the moment it wires registration up.")
        if missing:
            print(f"\n{len(missing)} portal product(s) CONSUMING the Secret with NO delivery path:\n")
            for repo, ns, why in missing:
                print(f"  ✗ {repo} (namespace {ns}) — {why}")
            print(
                "\nAdd an entry to governance/credential-handoff.json for each. Take the\n"
                "manifestPath from the repo AS IT IS -- the six found in 2026-08-20 used five\n"
                "different layouts, and a normalised path writes a secret nothing syncs.\n"
            )

    if missing:
        for repo, ns, why in missing:
            print(f"::error title=handoff-coverage::{repo} reads fuzefront-registration but has {why} in the hand-off registry")
        return 1
    print("\n✓ every portal product consuming fuzefront-registration has an enabled hand-off entry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
