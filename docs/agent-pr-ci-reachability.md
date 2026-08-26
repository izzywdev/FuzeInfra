# Agent PR CI reachability

When an agent (claude-code-action, the maintain/sync workflows, the auto-PR
opener) produces a pull request, that PR's CI must actually **run**. Two distinct
faults leave an agent PR with no CI, and both look identical to a human — "my PR
has no checks" is indistinguishable from "there are no PRs". Both are caused by
the **same thing**: the agent performed its git operation (opening the PR, or
pushing the head commit) as `github-actions[bot]` / with the default
`GITHUB_TOKEN`, instead of a collaborator-backed identity.

## The two mechanisms

### Mechanism 1 — `action_required` (public repos)

The `pull_request` runs **are created**, then held at conclusion
`action_required`, waiting on a human to click *Approve* in the Actions tab.

GitHub's *fork-PR approval* policy (`Settings → Actions → General → Fork pull
request workflows from outside collaborators`) gates any workflow run whose
**triggering actor** is classified as a first-time/outside contributor.
`github-actions[bot]` is never a recognised collaborator, so every event it
triggers on a PR is frozen — even on a **same-repo** branch, which is not a fork
at all.

Proof (FuzeFront, a public repo):

| PR | PR author | **triggering_actor of the runs** | result |
|----|-----------|----------------------------------|--------|
| #811 | github-actions[bot] | `izzywdev` (a human pushed the head) | 21 runs, all green |
| #812 | github-actions[bot] | `github-actions[bot]` | 17 runs, all `action_required` |

The gate is keyed to **who triggered the event, not who authored the PR**. The
repo's policy was already at `first_time_contributors` (the middle of the three
levels) — narrowing it further is not possible and would not help; widening it to
"nobody" is forbidden (see *Security property* below).

### Mechanism 2 — runs never created (private repos)

Fork-PR approval **cannot apply to a private repo**
(`422: Fork PR approval is not allowed for private repositories`). Instead, the
`pull_request` runs are simply **never created**. GitHub's recursion guard states
that a push performed with `GITHUB_TOKEN` does not trigger new workflow runs — so
an agent that pushes the PR head with `GITHUB_TOKEN` produces a PR head with zero
check runs.

Proof (FuzeContact, a private repo): PR #63's head — a merge commit pushed by an
agent — had **zero** check runs two hours after the push. Only the push-triggered
auto-PR workflow ran. No `pull_request` runs existed at all.

## The fix (owned by FuzeSDLC, propagated fleet-wide)

Every workflow that **opens an agent PR** or **pushes to an agent PR branch** must
authenticate as a **collaborator-backed identity**, never `github-actions[bot]` /
`GITHUB_TOKEN`:

1. **Preferred — a GitHub App installation token.** One org-wide GitHub App
   (Contents: write, Pull requests: write) installed on every repo; workflows mint
   a per-run token with `actions/create-github-app-token`. Commits/PRs are
   attributed to `fuze-agent[bot]`, a first-class write collaborator, so:
   - runs are **created** (an App push is not the `GITHUB_TOKEN` identity the
     recursion guard suppresses) → cures Mechanism 2, and
   - the triggering actor is a recognised write collaborator → not gated by
     fork-PR approval → cures Mechanism 1.
2. **Acceptable fallback** — a dedicated bot *account* added as a collaborator,
   with a fine-grained PAT.

This is a change to the **canonical managed workflows** in FuzeSDLC
(`claude-auto-pr`, `governance-sync`, `a2a-maintain`, `mcp-maintain`, and any
other workflow that pushes to a PR branch) plus the `hardening-convention.md`
statement, propagated to every repo. It is not a per-repo click.

## Security property preserved after the change

The fork-PR approval gate stays **on** and at `first_time_contributors`. What
changes is only the *identity the agent uses* — from an unrecognised bot to a
known write collaborator. Therefore:

- **Still covered:** a genuine pull request from an outside contributor's fork
  still has an unrecognised triggering actor, so its workflows are still held for
  human approval before they can run with repository secrets. The anti-secret-
  exfiltration purpose of the gate is intact. We do **not** set approval to
  "nobody".
- **No longer (incorrectly) covered:** first-party agent PRs — same-repo branches
  produced by our own automation — are no longer frozen. They were never the
  threat the gate exists for; freezing them only meant the harden gate merged
  un-run (which is how a doc error reached FuzeFront `main` and took
  `plan.fuzefront.com` down).

Net: the gate now discriminates by *trust* (outside fork vs. first-party agent)
instead of by *actor spelling* (`github-actions[bot]` vs. human).

## The tripwire

The fix being in place is not self-evident — a repo whose agent PRs silently never
run CI looks exactly like a healthy repo. `agent-pr-ci-reachability.yml`
(evaluated by `.github/detect-agent-pr-ci.py`, unit-tested offline in
`tests/test_agent_pr_ci_reachability.py`) runs hourly on every repo and **fails**
if any open agent PR is GATED or MISSING, so a regression is caught the same hour
rather than the next time someone happens to look at the Actions tab.
