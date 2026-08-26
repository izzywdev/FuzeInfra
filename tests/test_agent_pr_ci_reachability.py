"""Executable invariants for the agent-PR CI-reachability detector.

The detector exists because two distinct faults produce the SAME user-visible
symptom — "my agent PR has no CI" — and a repo in either state is
indistinguishable from a healthy repo that simply has no open PRs:

  * GATED   — `pull_request` runs created but frozen at `action_required`
              (bot/​GITHUB_TOKEN triggered the event on a public repo; the
              fork-PR approval gate holds it). Observed on FuzeFront #812.
  * MISSING — `pull_request` runs never created at all (agent pushed the head
              with GITHUB_TOKEN; the recursion guard suppresses the event on a
              private repo where fork-PR approval cannot apply). Observed on
              FuzeContact #63.

These tests EXECUTE the real `evaluate()` the CI workflow calls — not a grep of
the workflow — so a refactor that quietly stops flagging one of the two modes
fails here. The three properties that must hold:

  1. GATED must be caught even on a fresh head (a held run is held regardless of
     age) — the age grace applies ONLY to MISSING.
  2. MISSING must be caught once past the grace window, and must NOT fire inside
     it (a head pushed seconds ago is still spinning up — flagging it would make
     the tripwire cry wolf and train people to ignore red).
  3. A clean agent PR (runs present, none gated) and non-agent PRs produce
     nothing — the detector is silent when the fix is in place.

Offline: pure function over fixture dicts. No network, no gh, cross-platform.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".github/detect-agent-pr-ci.py"

spec = importlib.util.spec_from_file_location("detect_agent_pr_ci", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
evaluate = mod.evaluate

NOW = 1_000_000_000
GRACE_MIN = 20
OLD = NOW - (GRACE_MIN + 5) * 60      # comfortably past the grace window
FRESH = NOW - 2 * 60                  # inside the grace window


def _payload(prs, grace_minutes=GRACE_MIN):
    return {
        "now": NOW,
        "grace_minutes": grace_minutes,
        "agent_branch_prefixes": ["claude/", "fuze/"],
        "agent_authors": ["github-actions[bot]"],
        "prs": prs,
    }


def _kinds(findings):
    return {(f["number"], f["kind"]) for f in findings}


# ── Mechanism 1: GATED ──────────────────────────────────────────────────────

def test_gated_action_required_is_flagged():
    """The FuzeFront #812 shape: pull_request runs held at action_required."""
    pr = {
        "number": 812, "head_ref": "claude/fuze-registration",
        "author": "github-actions[bot]", "head_committed_at": OLD,
        "runs": [
            {"event": "pull_request", "status": "completed",
             "conclusion": "action_required", "name": "Backend Tests",
             "triggering_actor": "github-actions[bot]"},
            {"event": "pull_request", "status": "completed",
             "conclusion": "action_required", "name": "Harden Gate",
             "triggering_actor": "github-actions[bot]"},
        ],
    }
    findings = evaluate(_payload([pr]))
    assert _kinds(findings) == {(812, "gated")}


def test_gated_is_caught_even_when_head_is_fresh():
    """Property 1: a held run is held regardless of age — grace is MISSING-only.
    A fresh gated PR that this missed would let the whole gate merge un-run."""
    pr = {
        "number": 813, "head_ref": "claude/x", "author": "github-actions[bot]",
        "head_committed_at": FRESH,
        "runs": [{"event": "pull_request", "status": "completed",
                  "conclusion": "action_required", "name": "CI",
                  "triggering_actor": "github-actions[bot]"}],
    }
    assert _kinds(evaluate(_payload([pr]))) == {(813, "gated")}


# ── Mechanism 2: MISSING ────────────────────────────────────────────────────

def test_missing_runs_past_grace_is_flagged():
    """The FuzeContact #63 shape: zero pull_request runs on an aged head."""
    pr = {
        "number": 63, "head_ref": "claude/release-yml-github-token",
        "author": "github-actions[bot]", "head_committed_at": OLD,
        "runs": [{"event": "push", "status": "completed", "conclusion": "success",
                  "name": "Auto-PR from claude branches"}],
    }
    assert _kinds(evaluate(_payload([pr]))) == {(63, "missing")}


def test_missing_inside_grace_is_not_flagged():
    """Property 2: a head pushed seconds ago is still spinning up — not a fault."""
    pr = {
        "number": 64, "head_ref": "claude/y", "author": "github-actions[bot]",
        "head_committed_at": FRESH, "runs": [],
    }
    assert evaluate(_payload([pr])) == []


def test_missing_without_timestamp_does_not_cry_wolf():
    """No timestamp => cannot bound age => must not flag (false red is worse than
    a miss for a tripwire that fails CI). The workflow always supplies one."""
    pr = {
        "number": 65, "head_ref": "claude/z", "author": "github-actions[bot]",
        "head_committed_at": None, "runs": [],
    }
    assert evaluate(_payload([pr])) == []


# ── Silence when healthy / out of scope ─────────────────────────────────────

def test_clean_agent_pr_is_silent():
    """Property 3: runs present and none gated => the fix is working => no noise."""
    pr = {
        "number": 900, "head_ref": "claude/good", "author": "github-actions[bot]",
        "head_committed_at": OLD,
        "runs": [{"event": "pull_request", "status": "completed",
                  "conclusion": "success", "name": "Backend Tests",
                  "triggering_actor": "fuze-agent[bot]"}],
    }
    assert evaluate(_payload([pr])) == []


def test_in_progress_runs_are_not_missing_or_gated():
    """Queued/in-progress pull_request runs mean CI is reachable and running."""
    pr = {
        "number": 901, "head_ref": "claude/running", "author": "someone",
        "head_committed_at": OLD,
        "runs": [{"event": "pull_request", "status": "in_progress",
                  "conclusion": None, "name": "CI"}],
    }
    assert evaluate(_payload([pr])) == []


def test_non_agent_pr_is_ignored():
    """A human PR with no CI is out of scope for this detector."""
    pr = {
        "number": 500, "head_ref": "feature/human-branch", "author": "izzywdev",
        "head_committed_at": OLD, "runs": [],
    }
    assert evaluate(_payload([pr])) == []


def test_agent_identified_by_author_when_branch_is_nonstandard():
    """Branch prefix is one signal; a bot author on any branch is another."""
    pr = {
        "number": 501, "head_ref": "tmp-63", "author": "github-actions[bot]",
        "head_committed_at": OLD, "runs": [],
    }
    assert _kinds(evaluate(_payload([pr]))) == {(501, "missing")}


def test_mixed_fleet_reports_each_pr_once():
    prs = [
        {"number": 1, "head_ref": "claude/a", "author": "github-actions[bot]",
         "head_committed_at": OLD,
         "runs": [{"event": "pull_request", "conclusion": "action_required",
                   "status": "completed", "name": "CI",
                   "triggering_actor": "github-actions[bot]"}]},
        {"number": 2, "head_ref": "claude/b", "author": "github-actions[bot]",
         "head_committed_at": OLD, "runs": []},
        {"number": 3, "head_ref": "claude/c", "author": "x",
         "head_committed_at": OLD,
         "runs": [{"event": "pull_request", "conclusion": "success",
                   "status": "completed", "name": "CI"}]},
    ]
    assert _kinds(evaluate(_payload(prs))) == {(1, "gated"), (2, "missing")}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
