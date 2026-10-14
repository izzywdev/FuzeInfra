"""The prod-write exception register, CLAUDE.md and the workflows must agree.

CLAUDE.md says no session writes to the prod cluster, except under an entry in its
"Guarded exceptions" section. That rule only means something if the exceptions are
(a) a closed, reviewable list, and (b) actually enforced by the workflow they name —
not prose that a later edit to the workflow can quietly outgrow.

This file is the executable half of that. For every entry in
governance/prod-write-exceptions.json it checks that:

  * the workflow exists, is dispatch-only and runs on a hosted runner (so it cannot
    be triggered by a push and does not depend on the cluster it acts on);
  * every gate the entry claims is a real step, conditioned on the destructive mode;
  * every input the entry says a session must never set on its own exists and
    defaults to OFF — a dangerous override that defaults on is not an override;
  * the test file that exercises the guards exists;
  * CLAUDE.md carries the entry under its id, so the human-readable policy and the
    machine-readable register cannot diverge.

It also pins the policy sentence that motivated the register: a session resolves a
blocked operation at the policy layer (a verification hand-off or a guarded
exception) and never closes a turn by handing the owner a command to run.

Offline: parses YAML, JSON and markdown. No network, no cluster.
"""

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
REGISTER = REPO / "governance" / "prod-write-exceptions.json"
CLAUDE_MD = REPO / "CLAUDE.md"

REQUIRED_KEYS = {
    "id", "workflow", "mode", "operation", "why_not_git", "granted", "granted_by",
    "evidence_required", "outside_grant", "enforced_by", "verify", "rollback",
}
HOSTED_PREFIXES = ("ubuntu-", "windows-", "macos-")


def _register():
    return json.loads(REGISTER.read_text(encoding="utf-8"))["exceptions"]


def _workflow(entry):
    path = REPO / entry["workflow"]
    assert path.is_file(), f"{entry['id']}: workflow {entry['workflow']} does not exist"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(spec):
    # PyYAML parses the bare key `on` as the boolean True.
    return spec.get("on", spec.get(True))


ENTRIES = _register()
IDS = [e["id"] for e in ENTRIES]


def test_the_register_is_not_empty_and_ids_are_unique():
    assert ENTRIES, "an empty register means CLAUDE.md's exception section points at nothing"
    assert len(IDS) == len(set(IDS))


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_entry_carries_every_required_field(entry):
    missing = REQUIRED_KEYS - set(entry)
    assert not missing, f"{entry['id']} is missing {sorted(missing)}"
    assert entry["outside_grant"], (
        f"{entry['id']} must state what is OUTSIDE the grant. A grant with no stated "
        "limit reads as a grant for the whole class."
    )


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_workflow_is_dispatch_only_and_hosted(entry):
    spec = _workflow(entry)
    assert set(_triggers(spec)) == {"workflow_dispatch"}, (
        f"{entry['id']}: a prod-writing workflow must be reachable only by an explicit "
        "dispatch — a push or schedule trigger would exercise the exception unattended."
    )
    for name, job in spec["jobs"].items():
        assert str(job["runs-on"]).startswith(HOSTED_PREFIXES), (
            f"{entry['id']}: job {name} runs on {job['runs-on']!r}; a recovery lever "
            "must not run inside the cluster it acts on."
        )


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_claimed_gate_is_a_real_step_conditioned_on_the_mode(entry):
    spec = _workflow(entry)
    steps = {s.get("name"): s for j in spec["jobs"].values() for s in j["steps"]}
    for gate in entry["enforced_by"]["gates"]:
        assert gate in steps, f"{entry['id']} claims gate {gate!r} but the workflow has no such step"
        condition = str(steps[gate].get("if", ""))
        assert entry["mode"] in condition, (
            f"{entry['id']}: gate {gate!r} is not conditioned on mode={entry['mode']!r}, "
            "so it is not actually guarding the destructive path."
        )


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_inputs_a_session_must_not_set_exist_and_default_off(entry):
    inputs = _triggers(_workflow(entry))["workflow_dispatch"]["inputs"]
    for name in entry["enforced_by"]["never_set_by_session"]:
        assert name in inputs, f"{entry['id']}: input {name!r} is not declared by the workflow"
        assert inputs[name].get("default") in (False, "false", ""), (
            f"{entry['id']}: {name!r} must default OFF — an override that defaults on is "
            "not an override."
        )


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_named_test_file_exists(entry):
    assert (REPO / entry["enforced_by"]["test_file"]).is_file()


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_claude_md_records_the_entry_under_its_id(entry):
    text = CLAUDE_MD.read_text(encoding="utf-8")
    workflow_name = Path(entry["workflow"]).name
    anchor = f"**{entry['id']} — `{workflow_name}`"
    assert anchor in text, (
        f"CLAUDE.md has no entry starting {anchor!r}. The register and the policy text "
        "must agree, or the policy is whatever the workflow happens to do."
    )
    assert "governance/prod-write-exceptions.json" in text


def test_claude_md_still_forbids_handing_the_owner_a_command():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "## Who runs it — the session does" in text
    assert "run this yourself" in text, "the rule must quote the phrase it forbids"
    assert "Guarded exception" in text
    assert "Verification hand-off" in text


def test_the_no_write_rule_names_the_exception_mechanism():
    """The absolute 'no exceptions at all' wording contradicted the register.

    If it returns, CLAUDE.md contradicts itself: one section says no exception can
    exist, another registers one. The rule must point at the register.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "zero exceptions" not in text
    assert "except under a registered exception" in text
