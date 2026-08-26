"""
Detect agent PRs whose CI is unreachable — the two failure modes that both look
identical to a human ("my PR has no CI") but have different causes:

  GATED    (Mechanism 1, public repos): the `pull_request` runs WERE created but
           are held at conclusion `action_required`. GitHub does this when the
           actor that triggered the event is classified as a first-time/outside
           contributor — which is what happens when the PR head is pushed or the
           PR is opened by `github-actions[bot]` / the default GITHUB_TOKEN. The
           fork-PR approval gate (correctly) stops untrusted forks; it also (as a
           side effect) freezes bot-authored same-repo agent PRs. Nobody clicks
           Approve, so the whole gate runs un-run.

  MISSING  (Mechanism 2, private repos, where fork-PR approval cannot apply): the
           `pull_request` runs are NEVER created. A push made with GITHUB_TOKEN
           does not trigger new workflow runs (GitHub's recursion guard), so an
           agent that pushes the PR head with GITHUB_TOKEN produces a PR with zero
           check runs. A repo whose agent PRs silently never run CI looks exactly
           like a repo with no PRs — this is the detector that tells them apart.

Both are cured by the same fix: agent git operations must run under a
collaborator-backed identity (a GitHub App installation token or a dedicated bot
PAT), never `github-actions[bot]` / GITHUB_TOKEN. This script does not fix that —
it is the standing tripwire that FAILS if the condition ever returns, so the fix
staying in place is observable rather than assumed.

Pure evaluation (`evaluate`) is separated from all I/O so it is unit-tested
offline with fixtures (tests/test_agent_pr_ci_reachability.py). The workflow
agent-pr-ci-reachability.yml fetches the live data via `gh api` and feeds it here
on stdin.

Input (stdin JSON):
  {
    "now": <epoch seconds>,
    "grace_minutes": 20,
    "agent_branch_prefixes": ["claude/", "fuze/"],
    "agent_authors": ["github-actions[bot]"],
    "prs": [
      {
        "number": 812,
        "head_sha": "…",
        "head_ref": "claude/…",
        "author": "github-actions[bot]",
        "head_committed_at": <epoch seconds | null>,
        "runs": [
          {"event": "pull_request", "status": "completed",
           "conclusion": "action_required", "name": "Backend Tests",
           "triggering_actor": "github-actions[bot]"}
        ]
      }
    ]
  }

Outputs to GITHUB_OUTPUT (if set): found=true|false, gated=N, missing=N.
Writes a human table to GITHUB_STEP_SUMMARY (if set) and stdout.
Exit code: 1 if any GATED or MISSING finding, else 0.
"""
import json
import os
import sys


def _is_agent_pr(pr, branch_prefixes, authors):
    ref = (pr.get("head_ref") or "")
    if any(ref.startswith(p) for p in branch_prefixes):
        return True
    return (pr.get("author") or "") in authors


def evaluate(payload):
    """Pure: classify each agent PR. Returns list of findings.

    A finding is {"number", "head_ref", "kind": "gated"|"missing", "detail"}.
    Clean agent PRs (CI created and not gated) and non-agent PRs produce nothing.
    """
    now = payload.get("now") or 0
    grace_s = int(payload.get("grace_minutes", 20)) * 60
    prefixes = payload.get("agent_branch_prefixes") or ["claude/"]
    authors = set(payload.get("agent_authors") or ["github-actions[bot]"])
    findings = []

    for pr in payload.get("prs", []):
        if not _is_agent_pr(pr, prefixes, authors):
            continue
        runs = pr.get("runs") or []
        pr_runs = [r for r in runs if r.get("event") == "pull_request"]

        # GATED: any pull_request run held at action_required. This is decisive and
        # not age-sensitive — a held run is held regardless of how fresh it is.
        gated_runs = [
            r for r in pr_runs
            if (r.get("conclusion") or r.get("status")) == "action_required"
        ]
        if gated_runs:
            names = ", ".join(sorted({r.get("name", "?") for r in gated_runs}))
            actor = next(
                (r.get("triggering_actor") for r in gated_runs
                 if r.get("triggering_actor")), "unknown")
            findings.append({
                "number": pr.get("number"),
                "head_ref": pr.get("head_ref"),
                "kind": "gated",
                "detail": f"{len(gated_runs)} run(s) action_required "
                          f"(triggering_actor={actor}): {names}",
            })
            # A PR that is gated is, by definition, not also "missing" — the runs
            # exist. Do not double-report.
            continue

        # MISSING: zero pull_request runs on the head, and the head is old enough
        # that CI would have appeared by now. Age-gated to avoid flagging a head
        # that was pushed seconds ago and is still spinning up. If we have no
        # timestamp we cannot bound the age, so we do NOT flag (a detector that
        # fails CI must not cry wolf); the workflow always supplies one.
        if not pr_runs:
            committed = pr.get("head_committed_at")
            if committed is not None and (now - committed) >= grace_s:
                age_min = int((now - committed) / 60)
                findings.append({
                    "number": pr.get("number"),
                    "head_ref": pr.get("head_ref"),
                    "kind": "missing",
                    "detail": f"0 pull_request runs on head {age_min}m after it "
                              f"was pushed — CI never triggered (GITHUB_TOKEN "
                              f"recursion guard?)",
                })

    return findings


def _emit(findings):
    gated = [f for f in findings if f["kind"] == "gated"]
    missing = [f for f in findings if f["kind"] == "missing"]

    lines = []
    if findings:
        lines.append("## ❌ Agent PRs with unreachable CI\n")
        lines.append("| PR | branch | kind | detail |")
        lines.append("|----|--------|------|--------|")
        for f in findings:
            lines.append(
                f"| #{f['number']} | `{f['head_ref']}` | **{f['kind']}** | "
                f"{f['detail']} |")
        lines.append("")
        lines.append(
            "Agent PRs must be pushed/opened under a collaborator-backed identity "
            "(GitHub App installation token or dedicated bot PAT), never "
            "`github-actions[bot]` / GITHUB_TOKEN. See "
            "`docs/agent-pr-ci-reachability.md`.")
    else:
        lines.append("## ✅ All agent PRs have reachable CI")
        lines.append("")
        lines.append("No `action_required`-gated runs and no agent PR head "
                     "missing its `pull_request` runs.")
    report = "\n".join(lines) + "\n"

    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"found={'true' if findings else 'false'}\n")
            fh.write(f"gated={len(gated)}\n")
            fh.write(f"missing={len(missing)}\n")
    return len(findings)


def main(argv=None):
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"::error::could not parse input JSON: {exc}", file=sys.stderr)
        return 2
    findings = evaluate(payload)
    n = _emit(findings)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
