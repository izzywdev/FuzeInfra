"""Executable invariants for the traefik-helm-recover gate.

This workflow is the only path by which anything in this repo deletes a live
object in the prod cluster, and the object it deletes is load-bearing in a
non-obvious way. Clearing `helm-install-traefik` makes the k3s helm-controller
re-run the chart, and klipper-helm's `entry` script runs

    helm uninstall ${NAME} --namespace ${TARGET_NAMESPACE} --wait

before reinstalling whenever the RELEASE status matches
``^(deleted|failed|null|unknown)$`` — which for Traefik is a window with no
ingress controller in the cluster at all. A release in ``deployed`` status
upgrades in place with no such window.

So the whole safety argument rests on two things holding at once:

  1. ``recover`` cannot run by accident (an explicit token, fail-closed), and
  2. ``recover`` cannot delete a Job that is not already terminally Failed —
     aborting a RUNNING install Job is itself a way to produce the ``failed``
     release status that turns the next attempt into an uninstall.

Both are shell, so both are tested by EXECUTING the workflow's real bash with a
stubbed ``kubectl`` rather than grepping the YAML for keywords. A guard asserted
by substring passes just as happily when the logic around it is inverted, and
the cases that matter here — an empty ``confirm``, a lowercase ``recover``,
``jsonpath`` returning empty because the condition is absent — are exactly the
ones a keyword check cannot tell apart.

A third property is structural rather than behavioural: ``diagnose`` must stay
the default. A workflow whose dangerous mode is preselected is one stray click
from an outage, and defaults are the kind of thing a later edit changes without
anyone reading this file.

Offline: parses one YAML file and runs bash. No network, no cluster, no helm.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "traefik-helm-recover.yml"

# The subject IS the Linux bash guard that runs on GitHub Actions runners; there
# is no PowerShell equivalent and porting it would test something other than the
# real gate. On Windows, subprocess passes a multi-line `-c` script through
# CreateProcess/list2cmdline and truncates it at the first newline.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="executes the workflow's real bash; no PowerShell equivalent exists",
)


def _spec():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps():
    return _spec()["jobs"]["recover"]["steps"]


def _step(name):
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in {WORKFLOW}")


def _run(script, env=None, path_prepend=None):
    """Run a workflow step's `run:` body under bash, as Actions would."""
    environ = dict(os.environ)
    environ.pop("CONFIRM", None)
    if path_prepend:
        environ["PATH"] = f"{path_prepend}{os.pathsep}{environ['PATH']}"
    environ.update(env or {})
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=environ,
        cwd=str(REPO),
    )


def _kubectl_stub(tmp_path, stdout="", exit_code=0):
    """A fake `kubectl` first on PATH.

    The guard only ever reads kubectl's stdout, so a stub that ignores its
    arguments exercises the real branching without a cluster.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "kubectl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s' {stdout!r}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    # Record that a delete was attempted, so a test can prove it was NOT.
    marker = tmp_path / "delete-attempted"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do\n'
        f'  if [ "$a" = "delete" ]; then touch {str(marker)!r}; fi\n'
        "done\n"
        f"printf '%s' {stdout!r}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir, marker


# ---------------------------------------------------------------------------
# 1. the confirmation gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "confirm",
    [
        "",             # the default — a dispatch that just picked the mode
        "recover",      # lowercase
        "Recover",
        "RECOVER ",     # trailing space, e.g. pasted
        " RECOVER",
        "YES",
        "y",
        "RECOVERY",
        "RECOVER RECOVER",
    ],
)
def test_gate_refuses_anything_but_the_exact_token(confirm):
    result = _run(_step("Gate recover")["run"], env={"CONFIRM": confirm})
    assert result.returncode != 0, (
        f"confirm={confirm!r} was ACCEPTED. The gate is the only thing standing "
        "between a dispatch and a possible cluster-wide ingress outage."
    )
    assert "requires confirm=RECOVER" in result.stdout


def test_gate_accepts_the_exact_token():
    result = _run(_step("Gate recover")["run"], env={"CONFIRM": "RECOVER"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "confirmed" in result.stdout


def test_gate_fails_closed_when_confirm_is_unset_entirely():
    """`set -u` must not turn a missing input into a traceback that passes.

    `inputs.confirm` has a default, but a step env that stopped being wired
    would leave CONFIRM unset — and the one outcome that must never follow is
    "proceed".
    """
    result = _run(_step("Gate recover")["run"])
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# 2. the terminal-Failed precondition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "job_condition, should_delete",
    [
        ("True", True),      # terminally Failed — the one case we act on
        ("", False),         # no Failed condition: jsonpath yields empty
        ("False", False),    # Failed condition present but not set
        ("true", False),     # kubectl prints True; lowercase is not it
        ("Unknown", False),
    ],
)
def test_recover_only_deletes_a_terminally_failed_job(tmp_path, job_condition, should_delete):
    bin_dir, marker = _kubectl_stub(tmp_path, stdout=job_condition)
    result = _run(_step("Recover — delete the failed Job")["run"], path_prepend=str(bin_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists() is should_delete, (
        f"job Failed condition {job_condition!r}: expected delete={should_delete}, "
        f"got {marker.exists()}. Deleting a RUNNING install Job aborts an upgrade "
        "mid-flight, which is itself a way to leave the release in the `failed` "
        "status that makes the NEXT attempt an uninstall."
    )


def test_recover_is_a_clean_no_op_when_there_is_nothing_to_clear(tmp_path):
    """Not-Failed must exit 0, not error.

    This is dispatched by a human during an incident, and a red run that merely
    means "already converged" trains people to ignore the result.
    """
    bin_dir, marker = _kubectl_stub(tmp_path, stdout="")
    result = _run(_step("Recover — delete the failed Job")["run"], path_prepend=str(bin_dir))
    assert result.returncode == 0
    assert not marker.exists()
    assert "No action taken" in result.stdout


def test_recover_does_not_delete_when_kubectl_read_fails(tmp_path):
    """A failed read must not be mistaken for a Failed Job.

    `|| true` keeps `set -u` from aborting, so the empty string has to be the
    thing that stops the delete.
    """
    bin_dir, marker = _kubectl_stub(tmp_path, stdout="", exit_code=1)
    result = _run(_step("Recover — delete the failed Job")["run"], path_prepend=str(bin_dir))
    assert result.returncode == 0
    assert not marker.exists()


# ---------------------------------------------------------------------------
# 2b. the release-status gate (CLAUDE.md exception E1 — `deployed` only)
# ---------------------------------------------------------------------------

def _helm_stub(tmp_path, stdout="", exit_code=0):
    """A fake `helm` first on PATH; `helm status -o json` prints `stdout`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "helm"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s' {stdout!r}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _status_json(status):
    return '{"info": {"status": "%s"}}' % status


def _gate_status(tmp_path, helm_stdout, accept=None, helm_exit=0):
    env = {} if accept is None else {"ACCEPT_OUTAGE": accept}
    return _run(
        _step("Gate release status")["run"],
        env=env,
        path_prepend=str(_helm_stub(tmp_path, helm_stdout, helm_exit)),
    )


def test_release_gate_passes_a_deployed_release(tmp_path):
    result = _gate_status(tmp_path, _status_json("deployed"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "in-place upgrade" in result.stdout


@pytest.mark.parametrize(
    "status",
    [
        "failed",           # klipper-helm uninstalls first
        "unknown",
        "deleted",
        "pending-upgrade",  # an upgrade in flight: deleting the Job now aborts it
        "pending-install",
        "uninstalling",
        "superseded",
        "Deployed",         # case matters: helm prints lowercase, so this is not it
        "deployed ",
    ],
)
def test_release_gate_refuses_anything_but_deployed(tmp_path, status):
    result = _gate_status(tmp_path, _status_json(status), accept="false")
    assert result.returncode != 0, (
        f"release status {status!r} was ACCEPTED. For failed|unknown|deleted "
        "klipper-helm runs `helm uninstall --wait` on Traefik first: no ingress "
        "controller until the reinstall completes."
    )
    assert "outside CLAUDE.md exception E1" in result.stdout


@pytest.mark.parametrize(
    "helm_stdout, helm_exit",
    [
        ("", 1),                      # helm errored (release absent, no API, no perms)
        ("", 0),                      # helm printed nothing
        ("not json at all", 0),
        ('{"info": {}}', 0),          # JSON without a status
        ('{"nope": 1}', 0),
        ("[]", 0),
    ],
)
def test_release_gate_fails_closed_when_the_status_cannot_be_read(tmp_path, helm_stdout, helm_exit):
    """"Could not tell" must never read as "safe".

    An unreadable status resolves to `unknown`, which is itself one of the four
    statuses that make klipper-helm uninstall. Every way the read can fail must
    therefore refuse — a gate that passes on a broken `helm status` would be most
    permissive exactly when the cluster is least understood.
    """
    result = _gate_status(tmp_path, helm_stdout, accept="false", helm_exit=helm_exit)
    assert result.returncode != 0
    assert "'unknown'" in result.stdout


def test_release_gate_fails_closed_when_the_accept_input_is_unset(tmp_path):
    result = _gate_status(tmp_path, _status_json("failed"))
    assert result.returncode != 0


@pytest.mark.parametrize("accept", ["", "false", "False", "TRUE", "True", "1", "yes", "true "])
def test_only_the_exact_string_true_overrides_the_gate(tmp_path, accept):
    result = _gate_status(tmp_path, _status_json("failed"), accept=accept)
    assert result.returncode != 0, f"accept_ingress_outage={accept!r} overrode the gate"


def test_exact_true_overrides_and_says_so_loudly(tmp_path):
    result = _gate_status(tmp_path, _status_json("failed"), accept="true")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNINSTALL Traefik first" in result.stdout


# ---------------------------------------------------------------------------
# 3. structural properties
# ---------------------------------------------------------------------------

def test_diagnose_is_the_default_mode():
    spec = _spec()
    # PyYAML parses the bare key `on` as the boolean True.
    triggers = spec.get("on", spec.get(True))
    mode = triggers["workflow_dispatch"]["inputs"]["mode"]
    assert mode["default"] == "diagnose", (
        "the destructive mode must never be preselected — a dispatch form that "
        "opens on `recover` is one stray click from an ingress outage"
    )
    assert set(mode["options"]) == {"diagnose", "recover"}


def test_diagnose_runs_in_both_modes():
    """It is the before-picture, so it must not be conditioned on the mode.

    The failed Job's pod is GC'd within days — which is exactly why the original
    2026-09-24 failure reason is unrecoverable. A recovery performed without its
    own diagnostics in the same log would repeat that.
    """
    assert "if" not in _step("Diagnose")


def test_diagnose_covers_the_crd_chart_release():
    """Clearing the traefik Job cannot converge Traefik on its own.

    Measured 2026-10-14: the recreated Job failed in seconds with 'Required CRDs are
    missing' (traefik-40.x validate-install-crd.yaml). The CRDs belong to the separate
    traefik-crd release, whose Job had completed 83 days earlier. Its failurePolicy is
    `reinstall` as well, so a failed CRD install runs `helm uninstall` and deletes the
    CRDs with every IngressRoute/Middleware. Diagnose must therefore show that
    release's status, or a person would clear the wrong Job on the strength of a
    diagnosis that never looked at the real cause.
    """
    body = _step("Diagnose")["run"]
    assert "helm history traefik-crd -n kube-system" in body
    assert "helm-install-traefik-crd" in body


@pytest.mark.parametrize(
    "step_name",
    ["Gate recover", "Gate release status", "Recover — delete the failed Job"],
)
def test_acting_steps_are_gated_on_recover_mode(step_name):
    assert _step(step_name)["if"].strip() == "${{ inputs.mode == 'recover' }}"


def test_both_gates_precede_the_delete():
    names = [s.get("name") for s in _steps()]
    delete = names.index("Recover — delete the failed Job")
    assert names.index("Gate recover") < delete
    assert names.index("Gate release status") < delete


def test_the_outage_override_defaults_to_false_and_is_a_boolean():
    spec = _spec()
    triggers = spec.get("on", spec.get(True))
    accept = triggers["workflow_dispatch"]["inputs"]["accept_ingress_outage"]
    assert accept["default"] is False, "the outage override must never be preselected"
    assert accept["type"] == "boolean"
    assert accept["required"] is False


def test_workflow_is_dispatch_only():
    """No push/schedule trigger may ever reach the delete.

    `repository_dispatch` is deliberately absent too: cluster-query takes it so
    consuming repos can READ, and the same reasoning does not carry over to a
    lever that can drop every consumer's ingress.
    """
    spec = _spec()
    triggers = spec.get("on", spec.get(True))
    assert set(triggers) == {"workflow_dispatch"}


def test_runs_on_a_hosted_runner():
    """Recovery tooling for the ingress path must not run inside that cluster."""
    assert _spec()["jobs"]["recover"]["runs-on"].startswith("ubuntu-")


def test_does_not_print_secret_material():
    """Job logs in this repo are PUBLIC.

    `helm status`/`helm history` print release METADATA. `helm get values`,
    `get manifest`, `get all` and a `get secret` would each put the release's
    packed contents — or a credential — into a world-readable log, which is the
    same rule cluster-query enforces by refusing Secret reads.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    for banned in ("helm get ", "get secret", "--raw", "kubectl get secrets"):
        assert banned not in text, f"{banned!r} would publish release contents to a public job log"
