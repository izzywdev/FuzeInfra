"""Offline guard for the nightly-security-triage managed-path filter (issue #1001).

`nightly-security-triage` used to open autofix PRs for files that FuzeSDLC's
`governance_sync.py` reconciles back to canonical on EVERY PR — FRAMEWORK_DIRS under
`agent-templates/` (`schema`, `roles/_base`, `sync`, `providers`), plus the vendored
`.github/actions/**`, `.claude/skills/**`, `.claude/agents/**` directories, plus two
pinned single files (`.fuze/repo-manifest.schema.json`,
`governance/federation-contract-policy.json`). A fix committed to one of those paths is
always clobbered in the same PR (PR #1000: autofix + governance-sync revert net to a
zero-line diff, yet the run reported `fix-PR-opened`).

This test locks two things, executing the workflow's REAL bash+jq filter rather than
grepping it for keywords (per the `test_cluster_query_guard.py` convention — a guard
asserted by substring passes just as happily when the logic around it is broken):

  1. the filter's declared path set (`MANAGED_PREFIXES` + `MANAGED_EXACT`, extracted
     from the workflow file) matches the FRAMEWORK_DIRS + `.github/actions/` set above,
     exactly — no drift, no silent narrowing/widening; and
  2. the filter, run against a synthetic batch of alerts, actually drops managed-path
     alerts into `managed.json` and leaves everything else in `norm.json` untouched.

Offline: parses one YAML file and runs bash+jq against a temp dir. No network, no gh,
no git remote (the filter itself never shells out to either).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("jq") is None,
    reason="executes the real Linux CI bash+jq filter; needs bash and jq on PATH",
)

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/nightly-security-triage.yml"

STEP_NAME = "Enumerate and plan"
START_MARKER = "# --- Managed-path filter"
END_MARKER = "# --- End managed-path filter"

# The set this test locks the workflow to: FRAMEWORK_DIRS under agent-templates/, plus
# the vendored actions/skills/agents directories, plus the two pinned single files.
# Mirrors issue #1001's proposed fix exactly — do not widen/narrow without updating both.
EXPECTED_MANAGED_PREFIXES = {
    "agent-templates/schema/",
    "agent-templates/roles/_base/",
    "agent-templates/sync/",
    "agent-templates/providers/",
    ".github/actions/",
    ".claude/skills/",
    ".claude/agents/",
}
EXPECTED_MANAGED_EXACT = {
    ".fuze/repo-manifest.schema.json",
    "governance/federation-contract-policy.json",
}


def _enumerate_script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["enumerate"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == STEP_NAME)
    assert START_MARKER in script and END_MARKER in script, (
        f"the {STEP_NAME!r} step no longer contains the managed-path filter markers "
        f"({START_MARKER!r} / {END_MARKER!r}); this test slices the script there and "
        "must be updated with the step"
    )
    return script


def _filter_block() -> str:
    """Just the managed-path filter sub-script (jq calls + MANAGED_* declarations)."""
    script = _enumerate_script()
    start = script.index(START_MARKER)
    end = script.index(END_MARKER) + len(END_MARKER)
    return script[start:end]


def _extract_bash_json_array(script: str, var_name: str) -> list[str]:
    """Pull `VAR_NAME='[ ... ]'` out of the script and parse the JSON array.

    Matches the real declaration used by both `managed.json` and the (unmanaged)
    `norm.json` jq invocations, so a change to the filter's path set is caught here
    without re-implementing the jq logic in Python.
    """
    m = re.search(rf"{var_name}='(\[[^']*\])'", script, re.DOTALL)
    assert m, f"could not find `{var_name}='[...]'` in the managed-path filter block"
    return json.loads(m.group(1))


# --- (1) declared path set matches FRAMEWORK_DIRS + .github/actions/ exactly --------

def test_managed_prefixes_match_framework_dirs():
    prefixes = _extract_bash_json_array(_filter_block(), "MANAGED_PREFIXES")
    assert set(prefixes) == EXPECTED_MANAGED_PREFIXES


def test_managed_exact_paths_match_pinned_files():
    exact = _extract_bash_json_array(_filter_block(), "MANAGED_EXACT")
    assert set(exact) == EXPECTED_MANAGED_EXACT


def test_no_overlap_between_prefixes_and_exact():
    prefixes = _extract_bash_json_array(_filter_block(), "MANAGED_PREFIXES")
    exact = _extract_bash_json_array(_filter_block(), "MANAGED_EXACT")
    for e in exact:
        assert not any(e.startswith(p) for p in prefixes), (
            f"{e!r} is already covered by a prefix — redundant/confusing entry"
        )


# --- (2) the real filter actually partitions alerts correctly -----------------------

SYNTHETIC_ALERTS = [
    # managed by a FRAMEWORK_DIRS prefix
    {"number": 707, "rule_id": "py/empty-except", "severity": "medium", "tags": "",
     "path": "agent-templates/sync/driver.py", "start_line": 40, "end_line": 40,
     "title": "Empty except", "description": "", "html_url": "https://x/707"},
    {"number": 688, "rule_id": "py/foo", "severity": "medium", "tags": "",
     "path": "agent-templates/schema/role.schema.json", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/688"},
    {"number": 689, "rule_id": "py/foo", "severity": "medium", "tags": "",
     "path": "agent-templates/roles/_base/role.json", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/689"},
    {"number": 696, "rule_id": "py/foo", "severity": "medium", "tags": "",
     "path": "agent-templates/providers/anthropic.py", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/696"},
    {"number": 800, "rule_id": "js/foo", "severity": "medium", "tags": "",
     "path": ".github/actions/fuze-code-action/action.yml", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/800"},
    {"number": 801, "rule_id": "md/foo", "severity": "low", "tags": "",
     "path": ".claude/skills/repo-hardening/SKILL.md", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/801"},
    {"number": 802, "rule_id": "md/foo", "severity": "low", "tags": "",
     "path": ".claude/agents/devops.md", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/802"},
    # managed by an exact-file match
    {"number": 803, "rule_id": "json/foo", "severity": "low", "tags": "",
     "path": ".fuze/repo-manifest.schema.json", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/803"},
    {"number": 804, "rule_id": "json/foo", "severity": "low", "tags": "",
     "path": "governance/federation-contract-policy.json", "start_line": 1, "end_line": 1,
     "title": "Foo", "description": "", "html_url": "https://x/804"},
    # NOT managed — must remain actionable
    {"number": 900, "rule_id": "py/bar", "severity": "high", "tags": "",
     "path": "scripts/gate_manifest.py", "start_line": 5, "end_line": 5,
     "title": "Bar", "description": "", "html_url": "https://x/900"},
    # a decoy that merely CONTAINS a managed segment without matching a prefix/exact
    # path — must NOT be dropped (guards against a naive substring match).
    {"number": 901, "rule_id": "py/baz", "severity": "high", "tags": "",
     "path": "scripts/agent-templates-sync-helper.py", "start_line": 1, "end_line": 1,
     "title": "Baz", "description": "", "html_url": "https://x/901"},
    {"number": 902, "rule_id": "py/qux", "severity": "high", "tags": "",
     "path": "docs/notes/.claude/skills/not-really.md", "start_line": 1, "end_line": 1,
     "title": "Qux", "description": "", "html_url": "https://x/902"},
]

EXPECTED_MANAGED_NUMBERS = {707, 688, 689, 696, 800, 801, 802, 803, 804}
EXPECTED_UNMANAGED_NUMBERS = {900, 901, 902}


def run_filter(tmp_path: Path, norm: list) -> subprocess.CompletedProcess:
    """Run the real filter block against a synthetic norm.json in tmp_path."""
    (tmp_path / "norm.json").write_text(json.dumps(norm))
    return subprocess.run(
        ["bash", "-e", "-c", _filter_block()],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_filter_partitions_synthetic_alerts_correctly(tmp_path):
    result = run_filter(tmp_path, SYNTHETIC_ALERTS)
    assert result.returncode == 0, f"filter block failed:\n{result.stdout}{result.stderr}"

    managed = json.loads((tmp_path / "managed.json").read_text())
    remaining = json.loads((tmp_path / "norm.json").read_text())

    managed_numbers = {a["number"] for a in managed}
    remaining_numbers = {a["number"] for a in remaining}

    assert managed_numbers == EXPECTED_MANAGED_NUMBERS, (
        f"managed.json contained {managed_numbers}, expected {EXPECTED_MANAGED_NUMBERS}"
    )
    assert remaining_numbers == EXPECTED_UNMANAGED_NUMBERS, (
        f"norm.json (post-filter) contained {remaining_numbers}, expected "
        f"{EXPECTED_UNMANAGED_NUMBERS} — a managed alert leaked through, or a real "
        "alert was wrongly dropped"
    )
    # Every original alert accounted for exactly once, no duplication/loss.
    assert managed_numbers | remaining_numbers == {a["number"] for a in SYNTHETIC_ALERTS}
    assert not (managed_numbers & remaining_numbers)


def test_filter_drops_nothing_when_no_alerts_are_managed(tmp_path):
    only_unmanaged = [a for a in SYNTHETIC_ALERTS if a["number"] in EXPECTED_UNMANAGED_NUMBERS]
    result = run_filter(tmp_path, only_unmanaged)
    assert result.returncode == 0, f"filter block failed:\n{result.stdout}{result.stderr}"

    managed = json.loads((tmp_path / "managed.json").read_text())
    remaining = json.loads((tmp_path / "norm.json").read_text())
    assert managed == []
    assert {a["number"] for a in remaining} == EXPECTED_UNMANAGED_NUMBERS


# --- (3) the enumerate step actually reports the drop, never silently -------------

def test_enumerate_script_emits_upstream_summary_heading_for_managed_alerts():
    script = _enumerate_script()
    assert "Upstream (FuzeSDLC-managed" in script
    assert "managed.json" in script
    # Reported, never actioned: dropped alerts must never reach the actionable set
    # that ends up in the `alerts` matrix output.
    assert script.index(END_MARKER) < script.index("actionable='[]'")
