"""Cluster-recovery workflows must not run inside the cluster they recover.

A recovery lever wired to the thing it recovers is not a lever. Four workflows in
this repo are the only automated ways to READ prod, DEPLOY to prod, or APPLY prod's
bootstrap manifests:

  * ``cluster-query.yml``        — read-only kubectl, the incident-response tool
  * ``cluster-node-status.yml``  — node/ARC status, the escape hatch
  * ``deploy-prod.yml``          — validate + ArgoCD sync
  * ``apply-cluster-config.yml`` — the ONLY Git-triggered path that applies
    ``argocd/cluster-config``, ``cluster-bootstrap``, ``cluster-settings`` and
    ``notifications``

Each of these ran, at some point, on ``runs-on: staging`` — a self-hosted ARC pool
whose runner pods live in the prod cluster. That coupling has already cost real
outage time: on 2026-09-27 the ``staging`` pool sat at zero pods for ~18h with 30+
jobs queued, so for that entire window prod could be neither read nor deployed to
nor reconfigured — precisely when all three were wanted.

The first three were moved to hosted runners one at a time as each outage made the
case. ``apply-cluster-config`` was the last one left behind, and this guard exists
so the set is enforced as a class instead of rediscovered one incident at a time.

WHY A TEST AND NOT A COMMENT. The pull toward ``staging`` is real and recurring:
it is the default for cluster-touching work in this repo, it is what every
non-recovery workflow uses, and it is free of hosted-minute cost. Someone
"harmonising" these four back onto the in-cluster pool would reintroduce the
outage with a diff that reads like cleanup.

Offline: parses YAML. No network, no cluster, no GitHub.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

# Every one of these is reachable ONLY from outside the cluster, by design.
# `runs-on` values that are self-hosted ARC scale sets are bare strings naming
# the scale set (gha-runner-scale-set runners register with no labels), so the
# check is "is it a GitHub-hosted image", not "is it not the string staging" —
# the latter would pass for `runs-on: fuze-runner` and every other pool.
RECOVERY_WORKFLOWS = {
    "cluster-query.yml": "read-only kubectl against prod; the incident-response tool",
    "cluster-node-status.yml": "node + ARC status; the escape hatch when ARC is down",
    "deploy-prod.yml": "validate + ArgoCD sync; the only path that deploys prod",
    "apply-cluster-config.yml": "the only Git-triggered path applying prod bootstrap manifests",
}

HOSTED_PREFIXES = ("ubuntu-", "windows-", "macos-")


def _jobs(name):
    path = WORKFLOWS / name
    assert path.is_file(), f"{name} is missing — did it get renamed?"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    return spec["jobs"]


@pytest.mark.parametrize("workflow", sorted(RECOVERY_WORKFLOWS))
def test_recovery_workflow_runs_on_a_hosted_runner(workflow):
    why = RECOVERY_WORKFLOWS[workflow]
    for job_name, job in _jobs(workflow).items():
        runs_on = job.get("runs-on")
        assert isinstance(runs_on, str), (
            f"{workflow}:{job_name} has a non-scalar runs-on ({runs_on!r}). "
            "Matrix/array runners are not expected here; if one is added, extend "
            "this guard rather than dropping it."
        )
        assert runs_on.startswith(HOSTED_PREFIXES), (
            f"{workflow}:{job_name} runs on '{runs_on}', which is not a GitHub-hosted "
            f"runner. This workflow is {why}, so hosting it inside the prod cluster "
            "makes it unavailable exactly when it is needed — the 2026-09-27 ARC "
            "outage left prod unreadable and undeployable for ~18h for this reason."
        )


def test_apply_cluster_config_still_owns_the_bootstrap_paths():
    """The guard above is only meaningful while this workflow is still the path.

    If the bootstrap apply moves elsewhere, the runner pin protects nothing and
    this file needs updating rather than silently passing.
    """
    spec = yaml.safe_load((WORKFLOWS / "apply-cluster-config.yml").read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on` as the boolean True.
    triggers = spec.get("on", spec.get(True))
    paths = triggers["push"]["paths"]
    assert "argocd/cluster-bootstrap/**" in paths
    assert "argocd/cluster-config/**" in paths


def test_apply_cluster_config_does_not_hand_roll_a_kubectl_install():
    """It used to curl kubectl itself, because the `staging` image lacked it.

    That bespoke step existed only to work around the self-hosted image drifting
    (this workflow silently failed with "kubectl: command not found" from
    2026-07-29). On a hosted runner the pinned upstream action does the job, and
    keeping both would leave a second, unpinned way for the client version to
    drift away from the cluster's.
    """
    text = (WORKFLOWS / "apply-cluster-config.yml").read_text(encoding="utf-8")
    assert "dl.k8s.io/release" not in text, (
        "apply-cluster-config is hand-installing kubectl again. On a hosted runner "
        "use azure/setup-kubectl with a pinned version instead."
    )
    assert "azure/setup-kubectl@" in text
