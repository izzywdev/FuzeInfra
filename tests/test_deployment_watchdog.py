"""Executable invariants for the deployment-freeze watchdog.

The watchdog (scripts-tools/deployment_watchdog.py, run by
.github/workflows/deployment-watchdog.yml) exists because of the
2026-08-30..09-01 fleet-deployment freeze: a full Loki PVC crash-looped Loki 694
times over 2d11h, the permanently-unhealthy StatefulSet wedged one Argo sync
operation in phase=Running for 37h, and every prod change queued silently behind
it. Every component reported "running" and nothing alerted.

The properties that actually matter are the ones a passing-but-vacuous watchdog
would also satisfy, so they are pinned here explicitly:

  1. A Running op BELOW the threshold is NOT flagged (or the alert is noise
     within a week and gets muted, which is the same as not existing) and one
     ABOVE it IS.
  2. A healthy pod is never flagged.
  3. An existing open issue is not re-filed.
  4. "Cannot reach the cluster" FAILS. It must never render as "all clear" —
     that is the vacuous gate in its most dangerous form, and it is precisely
     what would have made this watchdog useless during the incident it is
     named after.
  5. It never reads a Secret. FuzeInfra's job logs are PUBLIC, so a read whose
     OUTPUT is a credential leaks it (tests/test_cluster_query_guard.py encodes
     the same rule for cluster-query.yml).

Offline: no cluster, no network, no gh. Every cluster interaction is a fixture.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts-tools" / "deployment_watchdog.py"
CONFIG = ROOT / "governance" / "watchdog-thresholds.json"
WORKFLOW = ROOT / ".github" / "workflows" / "deployment-watchdog.yml"


def _load_module():
    # scripts-tools is not an importable package name (hyphen), so load by path.
    spec = importlib.util.spec_from_file_location("deployment_watchdog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec: @dataclass resolves its own module out of sys.modules
    # and raises AttributeError on None if the module is not there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wd = _load_module()
CFG = json.loads(CONFIG.read_text(encoding="utf-8"))
NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# fixtures shaped like the real objects
# ---------------------------------------------------------------------------

def argo_app(name: str, phase: str, started_minutes_ago: float | None,
             message: str = "") -> dict:
    state: dict = {"phase": phase, "message": message}
    if started_minutes_ago is not None:
        state["startedAt"] = _ts(started_minutes_ago)
    return {
        "metadata": {"name": name, "namespace": "argocd"},
        "status": {
            "operationState": state,
            "sync": {"status": "OutOfSync"},
            "health": {"status": "Degraded"},
        },
    }


def pod(name, namespace="fuzeinfra", *, container="app", restarts=0, reason=None,
        ready=True, start_minutes_ago=5.0, phase="Running", message=None) -> dict:
    state = {"running": {"startedAt": _ts(start_minutes_ago)}} if reason is None else {
        "waiting": {"reason": reason, "message": message}
    }
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"nodeName": "vmi3396106", "containers": [{"name": container}]},
        "status": {
            "phase": phase,
            "startTime": _ts(start_minutes_ago),
            "containerStatuses": [
                {
                    "name": container,
                    "ready": ready,
                    "restartCount": restarts,
                    "state": state,
                    "lastState": {
                        "terminated": {
                            "exitCode": 1,
                            "reason": "Error",
                            "finishedAt": _ts(1),
                        }
                    },
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# 1. stuck Argo operation — the highest-value signal
# ---------------------------------------------------------------------------

def test_running_op_below_threshold_is_not_flagged():
    """A real fuzeinfra-prod sync takes ~5 minutes; hooks and rollouts run.

    Flagging those is how an alert gets muted, which ends in exactly the silence
    this watchdog was written to break.
    """
    apps = {"items": [argo_app("fuzeinfra-prod", "Running", 44)]}
    assert wd.detect_stuck_argo_ops(apps, CFG, NOW) == []


def test_running_op_above_threshold_is_flagged_with_the_blocking_resource():
    """The incident itself: 37h in phase=Running, blocked on the Loki StatefulSet."""
    message = "waiting for healthy state of apps/StatefulSet/fuzeinfra-loki and 1 more resources"
    apps = {"items": [argo_app("fuzeinfra-prod", "Running", 37 * 60, message)]}

    findings = wd.detect_stuck_argo_ops(apps, CFG, NOW)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == wd.KIND_ARGO
    assert finding.subject == "fuzeinfra-prod"
    # The diagnostics a human needs must be IN the finding, not left in a log:
    # app name, phase, startedAt and the blocking resource.
    assert finding.facts["phase"] == "Running"
    assert finding.facts["startedAt"] == _ts(37 * 60)
    assert finding.facts["message"] == message
    assert finding.facts["running_minutes"] == pytest.approx(2220.0)


def test_threshold_is_read_from_config_not_hardcoded():
    apps = {"items": [argo_app("fuzeinfra-prod", "Running", 50)]}
    assert len(wd.detect_stuck_argo_ops(apps, CFG, NOW)) == 1
    relaxed = {**CFG, "argo_stuck_op": {**CFG["argo_stuck_op"], "running_minutes": 120}}
    assert wd.detect_stuck_argo_ops(apps, relaxed, NOW) == []


@pytest.mark.parametrize("phase", ["Succeeded", "Failed", "Terminating", "Error"])
def test_non_running_phases_are_never_flagged(phase):
    apps = {"items": [argo_app("fuzeinfra-prod", phase, 5 * 24 * 60)]}
    assert wd.detect_stuck_argo_ops(apps, CFG, NOW) == []


def test_app_with_no_operation_state_is_not_flagged():
    apps = {"items": [{"metadata": {"name": "litellm"}, "status": {}}]}
    assert wd.detect_stuck_argo_ops(apps, CFG, NOW) == []


def test_default_threshold_matches_the_governance_file():
    assert CFG["argo_stuck_op"]["running_minutes"] == 45


# ---------------------------------------------------------------------------
# 2. chronic CrashLoopBackOff
# ---------------------------------------------------------------------------

def test_healthy_pod_is_not_flagged_by_any_pod_detection():
    pods = {"items": [pod("litellm-69d54765ff-sc9gz")]}
    assert wd.detect_chronic_crashloop(pods, CFG, NOW) == []
    assert wd.detect_stuck_container_creating(pods, CFG, NOW) == []


def test_a_few_restarts_are_not_chronic():
    """A rollout, an OOM burst or a dependency flap is not a self-sealing loop."""
    pods = {"items": [pod("api-1", restarts=7, reason="CrashLoopBackOff", ready=False,
                          start_minutes_ago=10)]}
    assert wd.detect_chronic_crashloop(pods, CFG, NOW) == []


def test_loki_restart_storm_is_flagged():
    """694 restarts over 2d11h — the real number from the incident."""
    pods = {"items": [pod("fuzeinfra-loki-0", container="loki", restarts=694,
                          reason="CrashLoopBackOff", ready=False,
                          start_minutes_ago=59 * 60)]}
    findings = wd.detect_chronic_crashloop(pods, CFG, NOW)
    assert len(findings) == 1
    assert findings[0].facts["restartCount"] == 694
    assert findings[0].subject == "fuzeinfra/fuzeinfra-loki-0:loki"


def test_long_crashloop_is_flagged_even_below_the_restart_count():
    pods = {"items": [pod("api-1", restarts=6, reason="CrashLoopBackOff", ready=False,
                          start_minutes_ago=180)]}
    assert len(wd.detect_chronic_crashloop(pods, CFG, NOW)) == 1


def test_short_crashloop_below_both_thresholds_is_not_flagged():
    pods = {"items": [pod("api-1", restarts=3, reason="CrashLoopBackOff", ready=False,
                          start_minutes_ago=30)]}
    assert wd.detect_chronic_crashloop(pods, CFG, NOW) == []


# ---------------------------------------------------------------------------
# 3. stuck ContainerCreating (the stale-mount class)
# ---------------------------------------------------------------------------

def test_stuck_container_creating_is_flagged_with_the_mount_error():
    """'already mounted or mount point busy' kept Loki down 14h AFTER the disk was fixed."""
    message = ("MountVolume.SetUp failed for volume \"pvc-loki\": already mounted or "
               "mount point busy")
    pods = {"items": [pod("fuzeinfra-loki-0", container="loki", reason="ContainerCreating",
                          ready=False, start_minutes_ago=14 * 60, phase="Pending",
                          message=message)]}
    findings = wd.detect_stuck_container_creating(pods, CFG, NOW)
    assert len(findings) == 1
    assert findings[0].kind == wd.KIND_CREATING
    assert findings[0].facts["waiting_message"] == message


def test_recent_container_creating_is_not_flagged():
    """Multi-GB image pulls on a cold node legitimately take minutes."""
    pods = {"items": [pod("api-1", reason="ContainerCreating", ready=False,
                          start_minutes_ago=4, phase="Pending")]}
    assert wd.detect_stuck_container_creating(pods, CFG, NOW) == []


# ---------------------------------------------------------------------------
# 4. PVC nearing full — the rotate-by-volume alarm
# ---------------------------------------------------------------------------

def test_pvc_below_threshold_is_not_flagged():
    usage = [{"namespace": "fuzeinfra", "claim": "storage-fuzeinfra-loki-0",
              "ratio": 0.62, "source": "prometheus"}]
    assert wd.detect_pvc_pressure(usage, CFG) == []


def test_pvc_above_threshold_is_flagged_before_it_fills():
    """Loki has no size-based retention: an alarm before full is the only mechanism."""
    usage = [{"namespace": "fuzeinfra", "claim": "storage-fuzeinfra-loki-0",
              "ratio": 0.87, "used_bytes": 4_670_000_000,
              "capacity_bytes": 5_368_709_120, "source": "prometheus"}]
    findings = wd.detect_pvc_pressure(usage, CFG)
    assert len(findings) == 1
    assert findings[0].subject == "fuzeinfra/storage-fuzeinfra-loki-0"
    assert findings[0].facts["used_ratio"] == 0.87
    assert findings[0].facts["source"] == "prometheus"


def test_prometheus_vector_is_parsed_into_usage_records():
    payload = {
        "status": "success",
        "data": {"result": [
            {"metric": {"namespace": "fuzeinfra",
                        "persistentvolumeclaim": "storage-fuzeinfra-loki-0"},
             "value": [1756732800, "0.94"]},
        ]},
    }
    records = wd.parse_prometheus_vector(payload)
    assert records == [{"namespace": "fuzeinfra",
                        "claim": "storage-fuzeinfra-loki-0", "ratio": 0.94}]


def test_prometheus_with_zero_samples_raises_instead_of_reporting_clear():
    """'Prometheus knows about no volume at all' is blindness, not an all-clear."""
    with pytest.raises(wd.ClusterUnreachable):
        wd.parse_prometheus_vector({"status": "success", "data": {"result": []}})


def test_prometheus_error_status_raises():
    with pytest.raises(wd.ClusterUnreachable):
        wd.parse_prometheus_vector({"status": "error", "error": "query timeout"})


def test_df_fallback_output_is_parsed():
    out = (
        "Filesystem                1B-blocks       Used  Available Capacity Mounted on\n"
        "/dev/longhorn/pvc-loki   5368709120 5368705024          0     100% /loki\n"
    )
    assert wd.parse_df_output(out) == (5368705024, 5368709120)


def test_pvc_source_falls_back_to_df_when_prometheus_is_unreachable(monkeypatch):
    """The fallback exists so a Prometheus outage cannot silently disable this check."""
    monkeypatch.setattr(wd, "pvc_usage_via_prometheus",
                        lambda cfg: (_ for _ in ()).throw(wd.ClusterUnreachable("prom down")))
    monkeypatch.setattr(
        wd, "kubectl_exec_df",
        lambda ns, p, c, path, timeout=60: (
            "Filesystem 1B-blocks Used Available Capacity Mounted on\n"
            "/dev/x 100 90 10 90% /loki\n"
        ),
    )
    pods = {"items": [{
        "metadata": {"name": "fuzeinfra-loki-0", "namespace": "fuzeinfra"},
        "status": {"phase": "Running"},
        "spec": {
            "volumes": [{"name": "storage",
                         "persistentVolumeClaim": {"claimName": "storage-fuzeinfra-loki-0"}}],
            "containers": [{"name": "loki",
                            "volumeMounts": [{"name": "storage", "mountPath": "/loki"}]}],
        },
    }]}
    usage, source = wd.collect_pvc_usage(pods, CFG)
    assert source == "kubectl-exec-df"
    assert usage[0]["ratio"] == pytest.approx(0.9)
    assert wd.detect_pvc_pressure(usage, CFG)[0].facts["source"] == "kubectl-exec-df"


def test_both_pvc_sources_failing_raises_rather_than_reporting_clear(monkeypatch):
    monkeypatch.setattr(wd, "pvc_usage_via_prometheus",
                        lambda cfg: (_ for _ in ()).throw(wd.ClusterUnreachable("prom down")))
    with pytest.raises(wd.ClusterUnreachable):
        wd.collect_pvc_usage({"items": []}, CFG)


# ---------------------------------------------------------------------------
# 5. dedup — a watchdog that files 40 duplicates gets muted
# ---------------------------------------------------------------------------

def _stuck_finding():
    apps = {"items": [argo_app("fuzeinfra-prod", "Running", 37 * 60, "waiting for healthy state")]}
    return wd.detect_stuck_argo_ops(apps, CFG, NOW)[0]


def test_existing_open_issue_is_not_refiled():
    finding = _stuck_finding()
    existing = [{"number": 744, "url": "https://github.com/izzywdev/FuzeInfra/issues/744",
                 "title": "anything a human renamed it to",
                 "body": f"{finding.marker}\n\n@fuze ..."}]
    new, duplicates = wd.filter_new_findings([finding], existing)
    assert new == []
    assert duplicates and duplicates[0][1]["number"] == 744


def test_matching_title_also_dedupes_when_the_body_marker_was_edited_away():
    finding = _stuck_finding()
    existing = [{"number": 745, "url": "u", "title": finding.title(), "body": "human rewrote this"}]
    new, _ = wd.filter_new_findings([finding], existing)
    assert new == []


def test_a_different_condition_still_gets_its_own_issue():
    """Dedup is per condition, not per run — one issue per DISTINCT condition."""
    stuck = _stuck_finding()
    other = wd.detect_chronic_crashloop(
        {"items": [pod("fuzeinfra-loki-0", container="loki", restarts=694,
                       reason="CrashLoopBackOff", ready=False, start_minutes_ago=600)]},
        CFG, NOW,
    )[0]
    existing = [{"number": 744, "url": "u", "title": stuck.title(),
                 "body": stuck.marker}]
    new, duplicates = wd.filter_new_findings([stuck, other], existing)
    assert [f.kind for f in new] == [wd.KIND_CRASHLOOP]
    assert len(duplicates) == 1


def test_issue_title_is_stable_across_runs():
    """Ages/counts in the title would defeat the title half of the dedup."""
    early = wd.detect_stuck_argo_ops(
        {"items": [argo_app("fuzeinfra-prod", "Running", 46)]}, CFG, NOW)[0]
    late = wd.detect_stuck_argo_ops(
        {"items": [argo_app("fuzeinfra-prod", "Running", 3000)]}, CFG, NOW)[0]
    assert early.title() == late.title() == "[watchdog] stuck-argo-op: fuzeinfra-prod"


def test_issue_body_mentions_fuze_and_embeds_the_real_diagnostics():
    finding = _stuck_finding()
    body = wd.build_issue_body(finding, CFG, "https://example/run/1",
                               ["Dispatched `argo-terminate-op.yml` with `app=fuzeinfra-prod`"])
    assert "@fuze" in body
    assert finding.marker in body
    assert "fuzeinfra-prod" in body
    assert "waiting for healthy state" in body
    # The auto-terminate MUST be disclosed in the issue that reports the condition.
    assert "argo-terminate-op.yml" in body


# ---------------------------------------------------------------------------
# 6. blindness FAILS, and the cluster access stays read-only + secret-free
# ---------------------------------------------------------------------------

def test_unreachable_cluster_exits_nonzero_and_never_reports_clear(monkeypatch, capsys):
    """The property that makes this watchdog worth having.

    If `kubectl` cannot reach the API server, main() must exit non-zero. Silently
    treating an unreachable cluster as "nothing wrong" is exactly the failure mode
    the 2d11h freeze was: a report of health from something that never looked.
    """
    def refuse(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, "", "Unable to connect to the server: dial tcp i/o timeout"
        )

    monkeypatch.setattr(wd.subprocess, "run", refuse)

    # main() propagates; run() is the process entrypoint and maps it to exit 2.
    with pytest.raises(wd.ClusterUnreachable):
        wd.main(["--repo", "izzywdev/FuzeInfra", "--dry-run"])
    assert wd.run(["--repo", "izzywdev/FuzeInfra", "--dry-run"]) == 2

    out = capsys.readouterr().out
    assert "::error::" in out
    assert "no stuck argo operation" not in out.lower()


def test_kubectl_nonzero_exit_raises_cluster_unreachable(monkeypatch):
    monkeypatch.setattr(
        wd.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "connection refused"),
    )
    with pytest.raises(wd.ClusterUnreachable):
        wd.kubectl(["get", "pods", "-A", "-o", "json"])


@pytest.mark.parametrize("args", [
    ["-n", "fuzeinfra", "get", "secret", "litellm-secret", "-o", "yaml"],
    ["get", "secrets", "-A", "-o", "yaml"],
    ["-n", "fuzeinfra", "get", "secret/litellm-secret", "-o", "yaml"],
    ["-n", "fuzeinfra", "get", "pods,secrets"],
    ["-n", "fuzeinfra", "get", "secrets.v1.", "-o", "yaml"],
    ["-n", "fuzeinfra", "get", "Secret", "litellm-secret"],
])
def test_secret_reads_are_refused(args):
    """This repo's job logs are PUBLIC: a read whose output is a credential leaks it."""
    with pytest.raises(wd.UnsafeCommand):
        wd.assert_safe_kubectl(args)


@pytest.mark.parametrize("args", [
    ["-n", "fuzeinfra", "delete", "pod", "fuzeinfra-loki-0"],
    ["-n", "fuzeinfra", "patch", "statefulset", "fuzeinfra-loki"],
    ["-n", "argocd", "edit", "application", "fuzeinfra-prod"],
    ["-n", "fuzeinfra", "exec", "fuzeinfra-loki-0", "--", "rm", "-rf", "/loki"],
    ["-n", "fuzeinfra", "scale", "sts", "fuzeinfra-loki", "--replicas=0"],
])
def test_mutating_kubectl_is_refused(args):
    """Prod is GitOps under Argo selfHeal; an out-of-band write is reverted anyway."""
    with pytest.raises(wd.UnsafeCommand):
        wd.assert_safe_kubectl(args)


def test_config_view_raw_is_refused():
    """`kubectl config view --raw` prints this runner's cluster-admin kubeconfig."""
    with pytest.raises(wd.UnsafeCommand):
        wd.assert_safe_kubectl(["config", "view", "--raw"])


def test_raw_is_only_allowed_for_the_service_proxy():
    wd.assert_safe_kubectl([
        "get", "--raw",
        "/api/v1/namespaces/fuzeinfra/services/fuzeinfra-prometheus:9090/proxy/api/v1/query?query=up",
    ])
    with pytest.raises(wd.UnsafeCommand):
        wd.assert_safe_kubectl(["get", "--raw", "/metrics"])


@pytest.mark.parametrize("args", [
    ["get", "pods", "-A", "-o", "json"],
    ["-n", "argocd", "get", "applications", "-o", "json"],
    ["-n", "fuzeinfra", "get", "sealedsecret", "litellm-secret", "-o", "yaml"],
    ["-n", "fuzeinfra", "describe", "deployment", "litellm-secret-reader"],
])
def test_legitimate_reads_still_work(args):
    wd.assert_safe_kubectl(args)


def test_exec_df_fallback_refuses_injected_names():
    with pytest.raises(wd.UnsafeCommand):
        wd.kubectl_exec_df("fuzeinfra", "loki-0; rm -rf /", "loki", "/loki")
    with pytest.raises(wd.UnsafeCommand):
        wd.kubectl_exec_df("fuzeinfra", "loki-0", "loki", "/loki; cat /etc/shadow")


def test_terminate_op_dispatch_validates_the_app_name():
    with pytest.raises(wd.UnsafeCommand):
        wd.dispatch_terminate_op("izzywdev/FuzeInfra", "argo-terminate-op.yml",
                                 "fuzeinfra-prod; curl evil")


def test_auto_terminate_targets_the_existing_purpose_built_workflow():
    workflow = CFG["argo_stuck_op"]["auto_terminate_workflow"]
    assert (ROOT / ".github" / "workflows" / workflow).is_file()


# ---------------------------------------------------------------------------
# 7. the workflow itself
# ---------------------------------------------------------------------------

def test_workflow_runs_on_a_hosted_runner_not_the_cluster_it_watches():
    """A watchdog hosted by its own subject cannot start when the subject is broken.

    Every other cluster-touching workflow here uses `runs-on: staging`, an ARC
    runner INSIDE the prod cluster. This one must not.
    """
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert spec["jobs"]["watch"]["runs-on"] == "ubuntu-latest"


def test_workflow_is_scheduled():
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on` as the boolean True.
    triggers = spec.get("on", spec.get(True))
    assert "schedule" in triggers and triggers["schedule"]


def test_workflow_has_no_failure_swallowing():
    """No continue-on-error / || true / exit 0. Blind must never look green."""
    text = WORKFLOW.read_text(encoding="utf-8")
    body = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
    assert "continue-on-error" not in body
    assert "|| true" not in body
    assert "exit 0" not in body


# ---------------------------------------------------------------------------
# 8. per-run issue cap — measured against the real cluster, not invented
# ---------------------------------------------------------------------------

def test_stuck_argo_op_files_first_under_the_cap():
    """The op that blocks every other deploy must never lose a slot to a crash loop."""
    crashloops = wd.detect_chronic_crashloop(
        {"items": [pod(f"api-{i}", restarts=600, reason="CrashLoopBackOff", ready=False,
                       start_minutes_ago=900) for i in range(20)]},
        CFG, NOW,
    )
    stuck = _stuck_finding()
    ordered = wd.prioritize(crashloops + [stuck], CFG["issues"]["priority"])
    assert ordered[0] is stuck


def test_end_to_end_caps_issues_filed_reports_everything_and_dispatches_terminate(monkeypatch):
    """Detection is never capped; only issue CREATION is.

    The first live dry-run against prod returned 55 distinct conditions. Filing
    55 issues at once is the muting failure in a different shape, so the run
    files the worst `max_per_run` and lists the rest in the summary.
    """
    apps = {"items": [argo_app("fuzeinfra-prod", "Running", 37 * 60,
                               "waiting for healthy state of apps/StatefulSet/fuzeinfra-loki")]}
    pods = {"items": [pod(f"api-{i}", restarts=600, reason="CrashLoopBackOff", ready=False,
                          start_minutes_ago=900) for i in range(20)]}
    filed: list[tuple[str, str]] = []
    dispatched: list[str] = []

    monkeypatch.setattr(wd, "collect_cluster_state", lambda cfg: (apps, pods))
    monkeypatch.setattr(wd, "collect_pvc_usage", lambda p, cfg: ([], "prometheus"))
    monkeypatch.setattr(wd, "list_open_watchdog_issues", lambda repo, label: [])
    monkeypatch.setattr(wd, "ensure_label", lambda *a, **k: None)
    monkeypatch.setattr(wd, "create_issue",
                        lambda repo, label, title, body: filed.append((title, body)) or "url")
    monkeypatch.setattr(wd, "dispatch_terminate_op",
                        lambda repo, wf, app, ref="main": dispatched.append(app))

    exit_code = wd.main(["--repo", "izzywdev/FuzeInfra"])

    assert exit_code == 1  # findings -> red run, so the freeze is visible in Actions
    assert len(filed) == CFG["issues"]["max_per_run"] < 21
    # The stuck op took the first slot and triggered the sanctioned terminate.
    assert filed[0][0] == "[watchdog] stuck-argo-op: fuzeinfra-prod"
    assert dispatched == ["fuzeinfra-prod"]
    assert "argo-terminate-op.yml" in filed[0][1]


def test_no_findings_files_nothing_and_exits_zero(monkeypatch):
    monkeypatch.setattr(wd, "collect_cluster_state", lambda cfg: ({"items": []}, {"items": []}))
    monkeypatch.setattr(wd, "collect_pvc_usage", lambda p, cfg: ([], "prometheus"))
    monkeypatch.setattr(wd, "create_issue", lambda *a, **k: pytest.fail("filed an issue with no findings"))
    assert wd.main(["--repo", "izzywdev/FuzeInfra"]) == 0
