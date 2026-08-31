#!/usr/bin/env python3
"""Fleet-wide GitHub secrets inventory for the Fuze repos.

Answers three questions that are impossible to hold in your head once the fleet
passes ~20 repos:

  1. WHAT secrets exist, and where (repo Actions secrets, Dependabot secrets,
     per-environment secrets, plus Actions variables for context).
  2. WHICH secrets are supposed to be propagated to every repo from the hubs
     (FuzeSDLC / FuzeInfra / FuzeFront) and are MISSING somewhere.
  3. WHICH secrets must carry their own per-repo value vs. which may share one
     value fleet-wide.

(2) and (3) are policy, not something the API can tell you, so they are declared
in `governance/secrets-policy.json` and this script reconciles reality against it.

Only secret NAMES and timestamps are ever read -- the GitHub API does not expose
Actions secret values at all, and this script never reads, prints or writes one.

DELIBERATELY A LOCAL SCRIPT, NOT A WORKFLOW. FuzeInfra is public, so its Actions
logs and artifacts are public; the full fleet secret-name inventory is a useful
map for an attacker (it names every provider and integration we use) even though
no value leaks. Run it from a workstation with `gh` authenticated as an account
that has admin on the repos.

Usage:
    python scripts/secrets_inventory.py                       # markdown to stdout
    python scripts/secrets_inventory.py --json out.json --markdown out.md
    python scripts/secrets_inventory.py --owner izzywdev --match "(?i)^fuze"

Exit codes:
    0  inventory produced (drift is reported, not fatal)
    1  hard failure (gh missing / not authenticated / bad policy)
    2  --strict was passed and required-secret drift was found
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

DEFAULT_OWNER = "izzywdev"
DEFAULT_MATCH = r"(?i)^(fuze|mendys)"
DEFAULT_POLICY = "governance/secrets-policy.json"
WORKFLOW_DIR = ".github/workflows"

#: Minted per run by GitHub itself; never configured, so never "missing".
BUILT_IN = {"GITHUB_TOKEN"}
SECRET_REF = re.compile(r"secrets\.([A-Z][A-Z0-9_]{2,})")
#: `uses: <owner>/<repo>/.github/workflows/<file>@<ref>` -- a reusable-workflow call.
#: Deliberately NOT pinned to FuzeSDLC: three repos (FuzeFront, MendysRobotics,
#: MendysRoboticsWP) still call the pre-FuzeSDLC reusables in izzywdev/AITools with
#: `secrets: inherit`. A FuzeSDLC-only pattern silently resolved none of those, which is
#: exactly the class of gap this scan exists to close.
REUSABLE_CALL = re.compile(r"uses:\s*([\w.-]+)/([\w.-]+)/\.github/workflows/([\w.-]+)@")

# --------------------------------------------------------------------------
# gh plumbing
# --------------------------------------------------------------------------


class GhError(RuntimeError):
    def __init__(self, path: str, status: str, body: str):
        super().__init__(f"{path}: {status}")
        self.path = path
        self.status = status
        self.body = body


def gh_json(path: str, *, paginate: bool = False):
    """Call `gh api <path>` and return parsed JSON.

    Paginates manually (per_page=100) rather than using `gh api --paginate`,
    which concatenates page objects for object-returning endpoints such as
    /actions/secrets and is a pain to reassemble.
    """
    if not paginate:
        return _gh_once(path)

    sep = "&" if "?" in path else "?"
    page, merged, key = 1, None, None
    while True:
        chunk = _gh_once(f"{path}{sep}per_page=100&page={page}")
        if merged is None:
            merged = chunk
            key = _list_key(chunk)
        else:
            if key is None:
                return merged
            merged[key].extend(chunk.get(key, []))
        if key is None or len(chunk.get(key, [])) < 100:
            return merged
        page += 1


def _list_key(obj):
    if not isinstance(obj, dict):
        return None
    for key in ("secrets", "variables", "environments"):
        if isinstance(obj.get(key), list):
            return key
    return None


def gh_raw(path: str) -> str:
    """Fetch a file's raw body through the contents API. Empty string on failure."""
    proc = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw", path],
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _gh_once(path: str):
    proc = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        status = "404" if "Not Found" in err else "403" if "Forbidden" in err else "error"
        raise GhError(path, status, err[:300])
    return json.loads(proc.stdout or "{}")


def preflight() -> None:
    if shutil.which("gh") is None:
        sys.exit("[fatal] GitHub CLI (gh) not found on PATH: https://cli.github.com/")
    # Retried: `gh auth status` reads the OS keyring, which returns a spurious failure under
    # concurrent gh invocations (e.g. a parallel fleet audit running in another shell). One
    # flaky read should not abort an inventory that is otherwise perfectly able to run.
    for attempt in range(3):
        if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0:
            return
        if attempt < 2:
            time.sleep(2)
    sys.exit("[fatal] gh is not authenticated. Run: gh auth login")


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def list_repos(owner: str, match: str, include_archived: bool) -> list:
    proc = subprocess.run(
        [
            "gh", "repo", "list", owner, "--limit", "500",
            "--json", "name,visibility,isArchived,defaultBranchRef",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"[fatal] gh repo list failed: {proc.stderr.strip()}")
    rx = re.compile(match)
    repos = []
    for r in json.loads(proc.stdout):
        if not rx.search(r["name"]):
            continue
        if r["isArchived"] and not include_archived:
            continue
        repos.append(
            {
                "name": r["name"],
                "visibility": r["visibility"].lower(),
                "archived": r["isArchived"],
                "defaultBranch": (r.get("defaultBranchRef") or {}).get("name"),
            }
        )
    return sorted(repos, key=lambda r: r["name"].lower())


def _safe(path: str, *, paginate: bool = False, default=None):
    try:
        return gh_json(path, paginate=paginate), None
    except GhError as exc:
        return default, {"path": exc.path, "status": exc.status, "detail": exc.body}


def _callee_refs(owner: str, repo: str, filename: str, canonical: str, cache: dict) -> set:
    """Secret names a called reusable workflow needs.

    A caller with `secrets: inherit` needs the CALLEE's secrets even though its own file
    names none -- telegram-pr-merged.yml is the standing example: 22 repos carry it, it
    mentions no secret, and it hard-fails without TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.
    Scanning only each repo's own text misses every one of those.

    FuzeSDLC is resolved from the local `--canonical` checkout (it is private, and this
    avoids a network round-trip per caller); anything else is fetched once and cached.
    """
    key = f"{owner}/{repo}/{filename}"
    if key in cache:
        return cache[key]
    body = ""
    if repo.lower() == "fuzesdlc" and canonical:
        for sub in (".github/workflows", "workflow-templates"):
            path = os.path.join(canonical, sub, filename)
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
                break
    if not body:
        body = gh_raw(f"repos/{owner}/{repo}/contents/{WORKFLOW_DIR}/{filename}")
    cache[key] = set(SECRET_REF.findall(body)) - BUILT_IN
    return cache[key]


def scan_workflow_refs(full: str, files: list, canonical: str) -> dict:
    """{workflow filename: [secret names it needs]} for one repo, inherit-calls resolved."""
    refs, cache = {}, {}
    for fname in files:
        body = gh_raw(f"repos/{full}/contents/{WORKFLOW_DIR}/{fname}")
        if not body:
            continue
        names = set(SECRET_REF.findall(body)) - BUILT_IN
        if "secrets: inherit" in body:
            for owner_, repo_, callee in REUSABLE_CALL.findall(body):
                names |= _callee_refs(owner_, repo_, callee, canonical, cache)
        if names:
            refs[fname] = sorted(names)
    return refs


def collect_repo(owner: str, repo: dict, *, scan: bool = True, canonical: str = "") -> dict:
    full = f"{owner}/{repo['name']}"
    out = dict(repo)
    out["full_name"] = full
    out["errors"] = []

    def note(err):
        if err:
            out["errors"].append(err)

    secrets, err = _safe(f"repos/{full}/actions/secrets", paginate=True, default={"secrets": []})
    note(err)
    out["actions_secrets"] = [
        {"name": s["name"], "updated_at": s.get("updated_at"), "created_at": s.get("created_at")}
        for s in (secrets or {}).get("secrets", [])
    ]

    dep, err = _safe(f"repos/{full}/dependabot/secrets", paginate=True, default={"secrets": []})
    note(err)
    out["dependabot_secrets"] = [
        {"name": s["name"], "updated_at": s.get("updated_at")}
        for s in (dep or {}).get("secrets", [])
    ]

    variables, err = _safe(
        f"repos/{full}/actions/variables", paginate=True, default={"variables": []}
    )
    note(err)
    out["variables"] = sorted(v["name"] for v in (variables or {}).get("variables", []))

    envs, err = _safe(f"repos/{full}/environments", paginate=True, default={"environments": []})
    note(err)
    out["environments"] = {}
    for env in (envs or {}).get("environments", []) or []:
        ename = env["name"]
        esec, err = _safe(
            f"repos/{full}/environments/{ename}/secrets", paginate=True, default={"secrets": []}
        )
        note(err)
        out["environments"][ename] = sorted(s["name"] for s in (esec or {}).get("secrets", []))

    # Onboarding marker: does the repo carry the FuzeSDLC manifest?
    manifest, _ = _safe(f"repos/{full}/contents/.fuze/manifest.json")
    out["onboarded"] = manifest is not None

    # Workflow file names, so a `conditional` rule can scope itself to the repos that
    # actually carry the workflow needing the secret rather than to the whole fleet.
    listing, _ = _safe(f"repos/{full}/contents/{WORKFLOW_DIR}")
    out["workflows"] = sorted(
        e["name"] for e in (listing or []) if e.get("name", "").endswith((".yml", ".yaml"))
    ) if isinstance(listing, list) else []

    # What the repo's workflows actually ASK FOR. Declared policy says what SHOULD be
    # there; this says what the running CI will look for and not find.
    out["workflow_refs"] = (
        scan_workflow_refs(full, out["workflows"], canonical) if scan else {}
    )
    referenced = {n for names in out["workflow_refs"].values() for n in names}
    present = {s["name"] for s in out["actions_secrets"]}
    present |= {n for names in out["environments"].values() for n in names}
    out["referenced"] = sorted(referenced)
    out["referenced_missing"] = sorted(referenced - present)
    out["unreferenced"] = sorted(present - referenced) if scan else []

    return out


def collect_owner_level(owner: str) -> dict:
    """Org-level secrets, when the owner is an organisation.

    A personal account has no org secret store, so this is empty for izzywdev --
    which is itself the finding that makes fan-out propagation necessary.
    """
    who, _ = _safe(f"users/{owner}")
    kind = (who or {}).get("type", "User")
    if kind != "Organization":
        return {
            "type": kind,
            "org_secrets": [],
            "note": "personal account: no org-level secret store",
        }
    secrets, _ = _safe(f"orgs/{owner}/actions/secrets", paginate=True, default={"secrets": []})
    return {
        "type": kind,
        "org_secrets": [
            {"name": s["name"], "visibility": s.get("visibility"), "updated_at": s.get("updated_at")}
            for s in (secrets or {}).get("secrets", [])
        ],
    }


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

PROPAGATION = {"fleet", "conditional", "hub-only", "per-repo", "external"}
SHARING = {"shared-value", "unique-value", "n/a"}


def load_policy(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {"rules": [], "_missing": path}
    with open(path, encoding="utf-8") as fh:
        policy = json.load(fh)
    for rule in policy.get("rules", []):
        label = rule.get("name") or rule.get("pattern")
        if rule.get("propagation") not in PROPAGATION:
            sys.exit(f"[fatal] policy rule {label}: bad propagation {rule.get('propagation')!r}")
        if rule.get("sharing") not in SHARING:
            sys.exit(f"[fatal] policy rule {label}: bad sharing {rule.get('sharing')!r}")
        if "pattern" in rule:
            rule["_rx"] = re.compile(rule["pattern"])
    return policy


def match_rule(policy: dict, name: str):
    """Exact names win over patterns; first matching pattern otherwise."""
    for rule in policy.get("rules", []):
        if rule.get("name") == name:
            return rule
    for rule in policy.get("rules", []):
        if rule.get("_rx") and rule["_rx"].search(name):
            return rule
    return None


def in_scope(rule: dict, repo: dict) -> bool:
    """Is a `fleet`/`conditional` rule expected to apply to this repo?

    Scoping precedence: an explicit `repos` allowlist, else `requiresWorkflow`
    (the repo carries one of the named workflow files), else -- for `fleet` only --
    every repo minus `exceptRepos`. A `conditional` rule with neither `repos` nor
    `requiresWorkflow` has no derivable scope, so it reports nothing rather than
    claiming the whole fleet is missing it.
    """
    if rule.get("onboardedOnly") and not repo["onboarded"]:
        return False
    only = rule.get("repos")
    if only is not None:
        return repo["name"] in only
    needs = rule.get("requiresWorkflow")
    if needs:
        return bool(set(needs) & set(repo.get("workflows") or []))
    if rule["propagation"] == "conditional":
        return False
    return repo["name"] not in (rule.get("exceptRepos") or [])


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def build_report(owner: str, repos: list, owner_level: dict, policy: dict) -> dict:
    index = {}
    for repo in repos:
        for kind, entries in (
            ("actions", repo["actions_secrets"]),
            ("dependabot", repo["dependabot_secrets"]),
        ):
            for entry in entries:
                slot = index.setdefault(
                    entry["name"], {"name": entry["name"], "repos": {}, "environments": {}}
                )
                slot["repos"].setdefault(repo["name"], {})[kind] = entry.get("updated_at")
        for env, names in repo["environments"].items():
            for name in names:
                slot = index.setdefault(name, {"name": name, "repos": {}, "environments": {}})
                slot["environments"].setdefault(repo["name"], []).append(env)

    rows = []
    for name in sorted(index):
        slot = index[name]
        rule = match_rule(policy, name)
        present = sorted(set(slot["repos"]) | set(slot["environments"]))
        row = {
            "name": name,
            "present_in": present,
            "count": len(present),
            "environments": slot["environments"],
            "classified": rule is not None,
            "propagation": (rule or {}).get("propagation", "UNCLASSIFIED"),
            "sharing": (rule or {}).get("sharing", "UNKNOWN"),
            "source": (rule or {}).get("source", "?"),
            "severity": (rule or {}).get("severity", "-"),
            "fleet_sourced": (rule or {}).get("fleetSourced"),
            "notes": (rule or {}).get("notes", ""),
            "missing_from": [],
            "unexpected_in": [],
        }
        if rule and rule["propagation"] in ("fleet", "conditional"):
            row["missing_from"] = [
                r["name"] for r in repos if in_scope(rule, r) and r["name"] not in present
            ]
        if rule and rule["propagation"] == "hub-only":
            allowed = set(rule.get("repos") or ([rule["source"]] if rule.get("source") else []))
            row["unexpected_in"] = [r for r in present if r not in allowed]
        rows.append(row)

    # A declared fleet secret that exists nowhere yet still needs provisioning.
    for rule in policy.get("rules", []):
        name = rule.get("name")
        if not name or name in index or rule["propagation"] not in ("fleet", "conditional"):
            continue
        rows.append(
            {
                "name": name,
                "present_in": [],
                "count": 0,
                "environments": {},
                "classified": True,
                "propagation": rule["propagation"],
                "sharing": rule["sharing"],
                "source": rule.get("source", "?"),
                "severity": rule.get("severity", "-"),
                "fleet_sourced": rule.get("fleetSourced"),
                "notes": rule.get("notes", ""),
                "missing_from": [r["name"] for r in repos if in_scope(rule, r)],
                "unexpected_in": [],
            }
        )

    rows.sort(key=lambda r: (r["propagation"], -r["count"], r["name"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "owner": owner,
        "owner_level": owner_level,
        "policy": (
            f"MISSING: {policy['_missing']}" if policy.get("_missing") else policy.get("version")
        ),
        "repos": repos,
        "secrets": rows,
    }


def _fmt(values: list, limit: int = 8) -> str:
    if not values:
        return "—"
    shown = ", ".join(f"`{v}`" for v in values[:limit])
    return shown if len(values) <= limit else f"{shown} …(+{len(values) - limit})"


def render_markdown(report: dict) -> str:
    repos = report["repos"]
    rows = report["secrets"]
    out = []
    add = out.append

    add("# Fuze fleet — GitHub secrets inventory")
    add("")
    add(
        f"Generated {report['generated_at']} · owner `{report['owner']}` · "
        f"{len(repos)} repos · {len(rows)} distinct secret names"
    )
    add("")
    ol = report["owner_level"]
    add(
        f"Owner-level store: **{ol.get('type')}** — {len(ol.get('org_secrets', []))} org secrets"
        + (f" _({ol['note']})_" if ol.get("note") else "")
    )
    add("")

    add("## Per-repo totals")
    add("")
    add("| Repo | Vis | Onboarded | Actions | Dependabot | Env secrets | Variables | Errors |")
    add("|---|---|---|---|---|---|---|---|")
    for r in repos:
        env_total = sum(len(v) for v in r["environments"].values())
        env_cell = f"{env_total} ({', '.join(r['environments'])})" if r["environments"] else "0"
        add(
            f"| `{r['name']}` | {r['visibility']} | {'yes' if r['onboarded'] else 'no'} | "
            f"{len(r['actions_secrets'])} | {len(r['dependabot_secrets'])} | {env_cell} | "
            f"{len(r['variables'])} | {len(r['errors'])} |"
        )
    add("")

    drift = [r for r in rows if r["missing_from"]]
    add("## Propagation gaps — fleet secrets missing from repos")
    add("")
    if not drift:
        add(
            "None. Every secret the policy marks `fleet`/`conditional` is present "
            "everywhere it is required."
        )
    else:
        add(
            "`auto` = the name is in FuzeSDLC's `FLEET_SOURCED`, so "
            "`provision-secrets.yml` (mode=apply) can close the gap on its own. "
            "`manual` = no central source value exists yet — add a `SRC_<NAME>` line to that "
            "workflow first, or it stays a hand-set secret forever."
        )
        add("")
        add("| Secret | Source | Sharing | Impact | Provision | Present | Missing from |")
        add("|---|---|---|---|---|---|---|")
        for r in drift:
            fs = {True: "auto", False: "manual"}.get(r.get("fleet_sourced"), "—")
            add(
                f"| `{r['name']}` | {r['source']} | {r['sharing']} | {r['severity']} | {fs} | "
                f"{r['count']} | {_fmt(r['missing_from'], 12)} |"
            )
    add("")

    leak = [r for r in rows if r["unexpected_in"]]
    if leak:
        add("## Hub-only secrets found outside their hub")
        add("")
        add(
            "These are declared as belonging to one repo. A copy elsewhere is extra blast "
            "radius to rotate — confirm it is deliberate or delete it."
        )
        add("")
        add("| Secret | Hub | Also in |")
        add("|---|---|---|")
        for r in leak:
            add(f"| `{r['name']}` | {r['source']} | {_fmt(r['unexpected_in'], 12)} |")
        add("")

    wanted = [(r["name"], r["referenced_missing"]) for r in repos if r.get("referenced_missing")]
    if wanted:
        by_secret = {}
        for repo, names in wanted:
            for name in names:
                by_secret.setdefault(name, []).append(repo)
        add("## Referenced but absent — what CI asks for and will not find")
        add("")
        add(
            "Derived from the workflow bodies themselves (including `secrets: inherit` into "
            "FuzeSDLC reusables), not from the policy. A name here is referenced by a workflow "
            "in that repo and has no value set, so the step fails, skips, or silently degrades."
        )
        add("")
        add("| Secret | Repos | Where |")
        add("|---|---|---|")
        for name in sorted(by_secret, key=lambda n: (-len(by_secret[n]), n)):
            hits = by_secret[name]
            add(f"| `{name}` | {len(hits)} | {_fmt(hits, 10)} |")
        add("")

    orphaned = [(r["name"], r["unreferenced"]) for r in repos if r.get("unreferenced")]
    if orphaned:
        add("## Present but referenced by no workflow")
        add("")
        add(
            "Not automatically deletable: many are consumed by the DEPLOYED application "
            "(sealed into a k8s Secret, read at runtime) rather than by CI. Confirm the "
            "consumer before removing any of these."
        )
        add("")
        add("| Repo | Secrets |")
        add("|---|---|")
        for repo, names in orphaned:
            add(f"| `{repo}` | {_fmt(names, 10)} |")
        add("")

    unclassified = [r for r in rows if not r["classified"]]
    add("## Unclassified secrets (no policy rule)")
    add("")
    if not unclassified:
        add("None — every observed secret has a policy rule.")
    else:
        add(
            f"{len(unclassified)} secret names have no rule in the policy file. Each needs a "
            "propagation + sharing decision, then a rule in `governance/secrets-policy.json`."
        )
        add("")
        add("| Secret | Repos | Where |")
        add("|---|---|---|")
        for r in unclassified:
            add(f"| `{r['name']}` | {r['count']} | {_fmt(r['present_in'], 6)} |")
    add("")

    add("## Full classification")
    add("")
    add("| Secret | Propagation | Sharing | Source | Repos | Where |")
    add("|---|---|---|---|---|---|")
    for r in rows:
        add(
            f"| `{r['name']}` | {r['propagation']} | {r['sharing']} | {r['source']} | "
            f"{r['count']} | {_fmt(r['present_in'], 5)} |"
        )
    add("")

    env_rows = [(r["name"], repo, envs) for r in rows for repo, envs in r["environments"].items()]
    if env_rows:
        add("## Environment-scoped secrets")
        add("")
        add("| Secret | Repo | Environments |")
        add("|---|---|---|")
        for name, repo, envs in sorted(env_rows):
            add(f"| `{name}` | `{repo}` | {', '.join(envs)} |")
        add("")

    errors = [(r["name"], e) for r in repos for e in r["errors"]]
    if errors:
        add("## Collection errors")
        add("")
        add("| Repo | Endpoint | Status |")
        add("|---|---|---|")
        for repo, err in errors:
            add(f"| `{repo}` | `{err['path']}` | {err['status']} |")
        add("")

    return "\n".join(out)


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fleet-wide GitHub secrets inventory (names only, never values).",
    )
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument(
        "--match", default=DEFAULT_MATCH, help="regex over repo names (default: %(default)s)"
    )
    ap.add_argument("--include-archived", action="store_true")
    ap.add_argument(
        "--onboarded-only", action="store_true", help="only repos carrying .fuze/manifest.json"
    )
    ap.add_argument("--policy", default=DEFAULT_POLICY)
    ap.add_argument("--json", dest="json_out", help="write the full machine-readable report here")
    ap.add_argument("--markdown", dest="md_out", help="write the human report here (default stdout)")
    ap.add_argument(
        "--no-scan-workflows",
        dest="scan",
        action="store_false",
        help="skip reading workflow bodies (much faster, but loses the referenced-but-absent report)",
    )
    ap.add_argument(
        "--canonical",
        default="",
        help="path to a FuzeSDLC checkout, so `secrets: inherit` calls into its reusables "
        "can be resolved to the secrets they actually need",
    )
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--strict", action="store_true", help="exit 2 if any required secret is missing")
    args = ap.parse_args()

    preflight()
    policy = load_policy(args.policy)
    if policy.get("_missing"):
        print(
            f"[warn] policy file {policy['_missing']} not found — everything reports UNCLASSIFIED",
            file=sys.stderr,
        )

    repos = list_repos(args.owner, args.match, args.include_archived)
    if not repos:
        sys.exit(f"[fatal] no repos of {args.owner} matched {args.match!r}")
    print(f"[info] scanning {len(repos)} repos...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        collected = list(
            pool.map(
                lambda r: collect_repo(args.owner, r, scan=args.scan, canonical=args.canonical),
                repos,
            )
        )
    if args.scan and not args.canonical:
        print(
            "[warn] --canonical not given: `secrets: inherit` into FuzeSDLC reusables cannot be "
            "resolved, so the referenced-but-absent report will understate the gaps",
            file=sys.stderr,
        )
    if args.onboarded_only:
        collected = [r for r in collected if r["onboarded"]]

    report = build_report(args.owner, collected, collect_owner_level(args.owner), policy)

    if args.json_out:
        parent = os.path.dirname(os.path.abspath(args.json_out))
        os.makedirs(parent, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"[info] wrote {args.json_out}", file=sys.stderr)

    md = render_markdown(report)
    if args.md_out:
        parent = os.path.dirname(os.path.abspath(args.md_out))
        os.makedirs(parent, exist_ok=True)
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
        print(f"[info] wrote {args.md_out}", file=sys.stderr)
    else:
        print(md)

    missing = sum(len(r["missing_from"]) for r in report["secrets"])
    if missing:
        print(f"[warn] {missing} repo/secret propagation gaps", file=sys.stderr)
    return 2 if (args.strict and missing) else 0


if __name__ == "__main__":
    sys.exit(main())
