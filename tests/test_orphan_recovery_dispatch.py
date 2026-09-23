"""Executable invariants for the ARC orphan-recovery CronJob's detection logic.

runners/arc/gitops/orphan-recovery-dispatch.yaml embeds a shell script that reads
each ARC listener pod's "Calculated target runner count" scaler log line and
decides whether that repo's queue is genuinely orphaned. Getting this wrong is not
merely useless -- it is actively harmful in both directions:

  - treating a CAPACITY-BOUND scale set ("assigned job"==max, draining normally)
    as orphaned and re-running its queue would dump duplicate jobs onto an
    already-saturated pool (observed live 2026-09-23: FuzeSDLC 260 queued /
    FuzePlan 45 queued against a 2-node pool at 96% CPU);
  - treating a merely-IDLE listener ("assigned job"=0, nothing actually queued)
    as orphaned would fire a repository_dispatch, and hence a recovery sweep, on
    every single tick, since assigned=0 is also the normal steady state.

These tests EXECUTE the real embedded script (extracted from the CronJob manifest,
truncated before the "MAIN LOOP" section that talks to kubectl/curl) rather than
grepping it for keywords -- a substring check passes just as happily when the
shell logic around it is broken.

Offline: parses one YAML file and runs bash against pure functions. No cluster,
no network, no GitHub token.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# Only the tests that actually SHELL OUT to bash (classify()/parse_scaler_line())
# are skipped on Windows -- same reason as tests/test_cluster_query_guard.py: Git
# Bash's subprocess handling of multi-line -c scripts truncates at the first
# newline and would give false passes. Applied per-test below (not module-wide)
# so the static YAML/manifest-shape assertions still run everywhere, including a
# local Windows dev loop -- a module-wide skip here would silently stop checking
# the RBAC/secret-reference/workflow-shape invariants too, which need no bash at
# all and have no excuse to be skipped anywhere.
skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="tests the real Linux CronJob shell script; Git Bash truncates multi-line -c scripts",
)

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "runners/arc/gitops/orphan-recovery-dispatch.yaml"
WORKFLOW = ROOT / ".github/workflows/runner-watch.yml"

MAIN_LOOP_MARKER = "# ---- MAIN LOOP"

# A real scaler log line captured live from the fuzehub listener pod via
# cluster-query.yml on 2026-09-23 (run 35846124882) while it was genuinely idle
# (not the orphaned incident state -- see the manifest header for that timeline).
REAL_LOG_LINE = (
    'time=2026-09-23T09:56:36.098Z level=INFO '
    'source=github.com/actions/actions-runner-controller/cmd/ghalistener/scaler/scaler.go:250 '
    'msg="Calculated target runner count" component=worker '
    '"assigned job"=0 decision=0 min=0 max=5 currentRunnerCount=0'
)


def _cronjob_script() -> str:
    """The CronJob container's embedded args[0] script, verbatim."""
    docs = list(yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")))
    cronjobs = [d for d in docs if d and d.get("kind") == "CronJob"]
    assert len(cronjobs) == 1, f"expected exactly one CronJob doc in {MANIFEST}"
    containers = cronjobs[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    args = containers[0]["args"]
    assert len(args) == 1
    return args[0]


def _pure_helpers() -> str:
    """Just parse_scaler_line()/classify() -- truncated before any kubectl/curl call.

    Keeps this suite a pure-function test: no cluster, no network, no GitHub token.
    """
    script = _cronjob_script()
    assert MAIN_LOOP_MARKER in script, (
        f"{MANIFEST} no longer contains {MAIN_LOOP_MARKER!r}; this test truncates the "
        "script there to keep it offline and must be updated with the script"
    )
    return script.split(MAIN_LOOP_MARKER)[0]


def run_bash(script_body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script_body],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def classify(assigned: str, maxv: str, queued: str) -> str:
    body = _pure_helpers() + f'\nclassify "{assigned}" "{maxv}" "{queued}"\n'
    proc = run_bash(body)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def parse_scaler_line(line: str) -> str:
    # Feed the line via a variable + echo, exactly as the real MAIN LOOP does
    # (`echo "$last" | parse_scaler_line`), rather than embedding it directly in
    # the script body where its own quotes/backslashes would need re-escaping.
    body = _pure_helpers() + '\necho "$TEST_LINE" | parse_scaler_line\n'
    proc = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "TEST_LINE": line},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# --- the four decisions that matter -----------------------------------------------


@skip_on_windows
def test_orphaned_when_assigned_zero_and_queued_confirmed_positive():
    """The genuine incident shape: assigned=0, GitHub confirms runs are queued.

    Mirrors FuzeHub live 2026-09-23: assigned=0, 13 queued -> must be flagged."""
    assert classify("0", "5", "13") == "orphaned"


@skip_on_windows
def test_capacity_bound_when_assigned_equals_max_never_touched():
    """assigned==max must short-circuit to capacity-bound BEFORE any queued-count
    check -- re-running a saturated pool's queue is actively harmful, not a no-op.

    Mirrors FuzeSDLC/FuzePlan live 2026-09-23: capacity-bound, must be left alone
    regardless of how large the queued count is."""
    assert classify("5", "5", "260") == "capacity-bound"
    # Even with a queued count supplied, capacity-bound wins -- the check must
    # never even reach the assigned==0 branch for this case.
    assert classify("5", "5", "0") == "capacity-bound"


@skip_on_windows
def test_idle_when_assigned_zero_and_nothing_queued():
    """The ordinary steady state -- must NOT dispatch."""
    assert classify("0", "5", "0") == "idle"


@skip_on_windows
def test_unknown_when_assigned_zero_and_queued_count_unconfirmed():
    """Fail-safe by construction: if the GitHub API read failed/was skipped
    (queued=""), an assigned=0 listener is "unknown", never "orphaned". A
    dispatch must never fire on an unconfirmed signal."""
    assert classify("0", "5", "") == "unknown"


@skip_on_windows
def test_has_work_when_assigned_positive_and_below_max():
    assert classify("1", "5", "") == "has-work"


@skip_on_windows
def test_unknown_when_the_scaler_line_could_not_be_parsed():
    assert classify("", "", "") == "unknown"


# --- the parser must match the real controller log line verbatim -----------------


@skip_on_windows
def test_parses_the_real_captured_scaler_line():
    """Regression pin against the actual line format, captured live via
    cluster-query.yml (run 35846124882) rather than assumed from memory."""
    assert parse_scaler_line(REAL_LOG_LINE) == "0 5 0"


@skip_on_windows
def test_parses_a_nonzero_assigned_line():
    line = REAL_LOG_LINE.replace('"assigned job"=0', '"assigned job"=1').replace(
        "currentRunnerCount=0", "currentRunnerCount=1"
    )
    assert parse_scaler_line(line) == "1 5 1"


@skip_on_windows
def test_unrelated_log_line_parses_to_nothing():
    assert parse_scaler_line("some unrelated listener log line with no scaler fields") == ""


# --- the manifest ships no write RBAC and no inline secret ------------------------


class TestManifestShape:
    @classmethod
    def setup_class(cls):
        cls.docs = list(yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")))

    def test_role_grants_only_read_verbs(self):
        roles = [d for d in self.docs if d and d.get("kind") == "Role"]
        assert roles, "no Role found in the manifest"
        allowed = {"get", "list"}
        for role in roles:
            for rule in role["rules"]:
                verbs = set(rule["verbs"])
                assert verbs <= allowed, (
                    f"Role {role['metadata']['name']} grants {verbs - allowed} beyond "
                    f"{allowed} -- this CronJob must never hold a write verb"
                )

    def test_secret_is_referenced_not_inlined(self):
        cronjob = next(d for d in self.docs if d and d.get("kind") == "CronJob")
        container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        env_names = {e["name"] for e in container["env"]}
        assert {"GH_DISPATCH_TOKEN", "GH_READ_TOKEN"} <= env_names
        for e in container["env"]:
            if e["name"] in ("GH_DISPATCH_TOKEN", "GH_READ_TOKEN"):
                assert "valueFrom" in e and "secretKeyRef" in e["valueFrom"], (
                    f"{e['name']} must come from secretKeyRef, never an inline value"
                )
                assert "value" not in e


# --- runner-watch.yml's recover-only mode cannot publish CI_RUNNER_LABELS --------


class TestRecoverOnlyCannotEscalate:
    """The other half of the safety story: this CronJob only ever fires
    repository_dispatch(runner-recovery), and runner-watch.yml's handling of that
    event type must be structurally incapable of reaching CI_RUNNER_LABELS
    publication. See scripts/__tests__/test_runner_watch.py::RecoverOnlyMode for
    the corresponding assertions against runner_watch.py itself and the full
    mode-resolution shape -- this class only pins the two workflow-level facts
    this CronJob's own correctness depends on.
    """

    @classmethod
    def setup_class(cls):
        cls.wf_text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_declares_the_runner_recovery_dispatch_type(self):
        wf = yaml.safe_load(self.wf_text)
        triggers = wf.get(True, wf.get("on"))
        assert "repository_dispatch" in triggers
        assert "runner-recovery" in triggers["repository_dispatch"]["types"]

    def test_mode_resolution_never_reads_client_payload(self):
        """A crafted client_payload must never be able to select `apply` -- only an
        explicit human workflow_dispatch can. If this ever starts BINDING
        client_payload into an env var this step reads for MODE purposes, this
        CronJob's dispatch becomes a path to unattended CI_RUNNER_LABELS
        publication. Checked against the step's actual `env:` bindings (an
        explanatory comment is fine and expected -- see the header above this
        step -- so this must not just ban the substring "client_payload" anywhere
        in the step, only its use as a github.event.client_payload expression)."""
        wf = yaml.safe_load(self.wf_text)
        step = next(
            s for s in wf["jobs"]["decide"]["steps"]
            if s.get("name") == "Resolve mode (the schedule is READ-ONLY)"
        )
        env_values = " ".join(str(v) for v in step.get("env", {}).values())
        assert "client_payload" not in env_values, (
            f"Resolve mode step now binds client_payload into an env var: {env_values!r}"
        )
