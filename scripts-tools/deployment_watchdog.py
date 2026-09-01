#!/usr/bin/env python3
"""Deployment-freeze watchdog for the prod cluster.

WHY THIS EXISTS
---------------
Between 2026-08-30 and 2026-09-01 fleet-wide deployment was frozen for 2d11h and
NOTHING alerted. Every individual component reported "running". The chain was:

  1. Loki's 5Gi PVC filled. A full Loki volume is SELF-SEALING: retention is
     enforced by the compactor, the compactor runs inside Loki, and Loki cannot
     start on a full disk ("mkdir /loki/tsdb-shipper-active/scratch/...: no space
     left on device"). It crash-looped 694 times over 2d11h and could never free
     its own space.
  2. Argo's sync of `fuzeinfra-prod` blocks on resource health, so the
     permanently-unhealthy StatefulSet wedged ONE sync operation in
     phase=Running for 37 hours ("waiting for healthy state of
     apps/StatefulSet/fuzeinfra-loki and 1 more resources").
  3. Argo will not start a new sync while an operation is in flight, so every
     prod change queued silently behind it — including a cluster-autoscaler
     maxSize raise that fleet-wide CI was blocked on.
  4. The freeze was found by hand, days later.

Each detection below is one link of that chain. Thresholds live in
governance/watchdog-thresholds.json.

DESIGN RULES (do not weaken)
----------------------------
* READ-ONLY against the cluster, with exactly two sanctioned exceptions, both
  named and both bounded: dispatching the existing `argo-terminate-op` workflow
  for an op that is already past threshold, and `kubectl exec -- df` as the PVC
  fallback when Prometheus is unreachable. Nothing is ever patched, edited or
  deleted: prod is GitOps under Argo selfHeal.
* NEVER read or print a Secret. FuzeInfra's job logs are PUBLIC — a read whose
  OUTPUT is a credential leaks it (this happened on 2026-07-29). The same rule
  tests/test_cluster_query_guard.py encodes for cluster-query is enforced here
  in `assert_safe_kubectl`.
* FAIL LOUDLY WHEN BLIND. If the cluster cannot be reached, or Prometheus and
  its fallback both fail, this exits non-zero. It never reports "all clear"
  because it could not look. A watchdog that goes green when blind is the
  vacuous gate in its most dangerous form.

Everything that decides *whether something is wrong* is a pure function over
parsed JSON (`detect_*`, `filter_new_findings`), so the predicates are unit
tested offline with no cluster: tests/test_deployment_watchdog.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "governance" / "watchdog-thresholds.json"

# Detection kinds. These strings are part of the dedup key, so renaming one
# orphans every open issue filed under the old name.
KIND_ARGO = "stuck-argo-op"
KIND_CRASHLOOP = "chronic-crashloop"
KIND_CREATING = "stuck-containercreating"
KIND_PVC = "pvc-nearing-full"


class ClusterUnreachable(RuntimeError):
    """The cluster (or the metric source) could not be read.

    Raised instead of returning an empty result set, because "no findings" and
    "could not look" must never be the same value.
    """


class UnsafeCommand(RuntimeError):
    """A kubectl invocation that would mutate state or print a credential."""


class GitHubError(RuntimeError):
    """The GitHub CLI failed. Also fatal — an issue that was not filed is not an alert."""


# --------------------------------------------------------------------------
# config + time helpers
# --------------------------------------------------------------------------

def load_config(path: str | os.PathLike[str] | None = None) -> dict:
    return json.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_k8s_time(value: Any) -> datetime | None:
    """Parse an RFC3339 kubernetes timestamp ('2026-08-30T04:11:07Z')."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(started: datetime, now: datetime) -> float:
    return (now - started).total_seconds() / 60.0


def _fmt_age(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f}m"
    hours = minutes / 60.0
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str          # app name / namespace-qualified pod / namespace-qualified pvc
    summary: str          # one-line human summary
    facts: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.subject}"

    @property
    def marker(self) -> str:
        """Machine-readable dedup marker embedded in the issue body."""
        return f"<!-- watchdog-key: {self.key} -->"

    def title(self, prefix: str = "[watchdog]") -> str:
        # Deliberately STABLE: no ages, counts or ratios, which change every run
        # and would defeat the title half of the dedup.
        return f"{prefix} {self.kind}: {self.subject}"


# --------------------------------------------------------------------------
# detections — pure functions over parsed JSON
# --------------------------------------------------------------------------

def detect_stuck_argo_ops(applications: dict, config: dict, now: datetime) -> list[Finding]:
    """Argo Applications with an operation stuck in phase=Running past threshold.

    This is incident link 2: the thing that silently froze deployment. Note that
    a Running op BELOW the threshold is explicitly not a finding — real syncs
    take ~5 minutes and hooks legitimately run.
    """
    cfg = config.get("argo_stuck_op", {})
    if not cfg.get("enabled", True):
        return []
    threshold = float(cfg.get("running_minutes", 45))
    findings: list[Finding] = []

    for app in applications.get("items", []) or []:
        meta = app.get("metadata") or {}
        name = meta.get("name") or "<unnamed>"
        state = (app.get("status") or {}).get("operationState") or {}
        if state.get("phase") != "Running":
            continue
        started = parse_k8s_time(state.get("startedAt"))
        if started is None:
            # Running with no parseable startedAt: cannot age it, so it is not
            # flagged here — but it is never silently swallowed either.
            print(
                f"::warning::{name}: operation phase=Running with unparseable "
                f"startedAt={state.get('startedAt')!r} — cannot age it",
                file=sys.stderr,
            )
            continue
        minutes = age_minutes(started, now)
        if minutes <= threshold:
            continue

        operation = state.get("operation") or {}
        revision = (operation.get("sync") or {}).get("revision") or (
            (state.get("syncResult") or {}).get("revision")
        )
        findings.append(
            Finding(
                kind=KIND_ARGO,
                subject=name,
                summary=(
                    f"Argo operation on `{name}` has been phase=Running for "
                    f"{_fmt_age(minutes)} (threshold {threshold:.0f}m) — every "
                    f"other sync of this app is queued behind it."
                ),
                facts={
                    "application": name,
                    "namespace": meta.get("namespace"),
                    "phase": state.get("phase"),
                    "startedAt": state.get("startedAt"),
                    "running_minutes": round(minutes, 1),
                    "threshold_minutes": threshold,
                    # The blocking-resource line, verbatim. In the incident this
                    # read "waiting for healthy state of
                    # apps/StatefulSet/fuzeinfra-loki and 1 more resources".
                    "message": state.get("message"),
                    "revision": revision,
                    "sync_status": ((app.get("status") or {}).get("sync") or {}).get("status"),
                    "health_status": ((app.get("status") or {}).get("health") or {}).get("status"),
                },
            )
        )
    return findings


def _container_statuses(pod: dict) -> list[dict]:
    status = pod.get("status") or {}
    return list(status.get("containerStatuses") or []) + list(
        status.get("initContainerStatuses") or []
    )


def _pod_subject(pod: dict) -> str:
    meta = pod.get("metadata") or {}
    return f"{meta.get('namespace', 'default')}/{meta.get('name', '<unnamed>')}"


def _pod_start(pod: dict) -> datetime | None:
    status = pod.get("status") or {}
    return parse_k8s_time(status.get("startTime")) or parse_k8s_time(
        (pod.get("metadata") or {}).get("creationTimestamp")
    )


def detect_chronic_crashloop(pods: dict, config: dict, now: datetime) -> list[Finding]:
    """Pods stuck in a crash loop they cannot get out of (incident link 1).

    Two independent triggers, either is enough:
      * restartCount above `restart_count` (Loki reached 694), or
      * currently in CrashLoopBackOff, never Ready, for longer than
        `crashloop_minutes`.
    """
    cfg = config.get("crashloop", {})
    if not cfg.get("enabled", True):
        return []
    max_restarts = int(cfg.get("restart_count", 50))
    max_minutes = float(cfg.get("crashloop_minutes", 60))
    ignore = set(cfg.get("ignore_namespaces") or [])
    findings: list[Finding] = []

    for pod in pods.get("items", []) or []:
        meta = pod.get("metadata") or {}
        if meta.get("namespace") in ignore:
            continue
        for cs in _container_statuses(pod):
            restarts = int(cs.get("restartCount") or 0)
            waiting = (cs.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason")
            in_backoff = reason == "CrashLoopBackOff"
            ready = bool(cs.get("ready"))

            minutes: float | None = None
            if in_backoff and not ready:
                started = _pod_start(pod)
                if started is not None:
                    minutes = age_minutes(started, now)

            by_restarts = restarts > max_restarts
            by_duration = minutes is not None and minutes > max_minutes
            if not (by_restarts or by_duration):
                continue

            terminated = (cs.get("lastState") or {}).get("terminated") or {}
            triggers = []
            if by_restarts:
                triggers.append(f"restartCount {restarts} > {max_restarts}")
            if by_duration:
                triggers.append(
                    f"CrashLoopBackOff for {_fmt_age(minutes or 0)} > {max_minutes:.0f}m"
                )
            findings.append(
                Finding(
                    kind=KIND_CRASHLOOP,
                    subject=f"{_pod_subject(pod)}:{cs.get('name', '<container>')}",
                    summary=(
                        f"`{_pod_subject(pod)}` container `{cs.get('name')}` is in a "
                        f"chronic crash loop ({'; '.join(triggers)})."
                    ),
                    facts={
                        "namespace": meta.get("namespace"),
                        "pod": meta.get("name"),
                        "container": cs.get("name"),
                        "restartCount": restarts,
                        "waiting_reason": reason,
                        "ready": ready,
                        "crashloop_minutes": None if minutes is None else round(minutes, 1),
                        "threshold_restarts": max_restarts,
                        "threshold_minutes": max_minutes,
                        "last_exit_code": terminated.get("exitCode"),
                        "last_terminated_reason": terminated.get("reason"),
                        "last_terminated_at": terminated.get("finishedAt"),
                        "waiting_message": waiting.get("message"),
                    },
                )
            )
    return findings


def detect_stuck_container_creating(pods: dict, config: dict, now: datetime) -> list[Finding]:
    """Pods wedged in ContainerCreating — the stale-mount class.

    'already mounted or mount point busy' kept Loki down for 14h AFTER the disk
    was fixed. The controller reports the workload as present the whole time.
    """
    cfg = config.get("container_creating", {})
    if not cfg.get("enabled", True):
        return []
    threshold = float(cfg.get("minutes", 30))
    reasons = set(cfg.get("reasons") or ["ContainerCreating", "PodInitializing"])
    ignore = set(cfg.get("ignore_namespaces") or [])
    findings: list[Finding] = []

    for pod in pods.get("items", []) or []:
        meta = pod.get("metadata") or {}
        if meta.get("namespace") in ignore:
            continue
        statuses = _container_statuses(pod)
        for cs in statuses:
            if cs.get("ready"):
                continue
            waiting = (cs.get("state") or {}).get("waiting") or {}
            if waiting.get("reason") not in reasons:
                continue
            started = _pod_start(pod)
            if started is None:
                continue
            minutes = age_minutes(started, now)
            if minutes <= threshold:
                continue
            findings.append(
                Finding(
                    kind=KIND_CREATING,
                    subject=f"{_pod_subject(pod)}:{cs.get('name', '<container>')}",
                    summary=(
                        f"`{_pod_subject(pod)}` container `{cs.get('name')}` has been "
                        f"{waiting.get('reason')} for {_fmt_age(minutes)} "
                        f"(threshold {threshold:.0f}m) — likely a stuck volume attach."
                    ),
                    facts={
                        "namespace": meta.get("namespace"),
                        "pod": meta.get("name"),
                        "container": cs.get("name"),
                        "waiting_reason": waiting.get("reason"),
                        "waiting_message": waiting.get("message"),
                        "pending_minutes": round(minutes, 1),
                        "threshold_minutes": threshold,
                        "node": (pod.get("spec") or {}).get("nodeName"),
                    },
                )
            )
    return findings


def detect_pvc_pressure(usage: Sequence[dict], config: dict) -> list[Finding]:
    """PVCs above `used_ratio`. `usage` records come from Prometheus or the df fallback.

    Loki has no size-based retention, so this alarm IS the rotate-by-volume
    mechanism: once the volume is full nothing inside the pod can free it.
    """
    cfg = config.get("pvc_pressure", {})
    if not cfg.get("enabled", True):
        return []
    limit = float(cfg.get("used_ratio", 0.80))
    findings: list[Finding] = []

    for record in usage:
        ratio = record.get("ratio")
        if ratio is None:
            continue
        if float(ratio) <= limit:
            continue
        namespace = record.get("namespace") or "?"
        claim = record.get("claim") or "?"
        used = record.get("used_bytes")
        capacity = record.get("capacity_bytes")
        detail = ""
        if used and capacity:
            detail = f" ({used / 2**30:.2f} GiB of {capacity / 2**30:.2f} GiB)"
        findings.append(
            Finding(
                kind=KIND_PVC,
                subject=f"{namespace}/{claim}",
                summary=(
                    f"PVC `{namespace}/{claim}` is {float(ratio) * 100:.1f}% full"
                    f"{detail} — above the {limit * 100:.0f}% alarm."
                ),
                facts={
                    "namespace": namespace,
                    "persistentvolumeclaim": claim,
                    "used_ratio": round(float(ratio), 4),
                    "threshold_ratio": limit,
                    "used_bytes": used,
                    "capacity_bytes": capacity,
                    "source": record.get("source"),
                },
            )
        )
    return findings


# --------------------------------------------------------------------------
# dedup
# --------------------------------------------------------------------------

def prioritize(findings, priority=None):
    """Worst-first order for filing under the per-run cap.

    The stuck Argo op comes first because it is the one condition that blocks
    EVERY other deploy; PVC pressure second because it is the only warning that
    arrives before a volume fills (after that nothing inside the pod can free
    it). Unknown kinds sort last but are never dropped.
    """
    order = list(priority or [KIND_ARGO, KIND_PVC, KIND_CREATING, KIND_CRASHLOOP])

    def rank(finding):
        return (order.index(finding.kind) if finding.kind in order else len(order), finding.key)

    return sorted(findings, key=rank)


def filter_new_findings(
    findings: Iterable[Finding], open_issues: Iterable[dict], title_prefix: str = "[watchdog]"
) -> tuple[list[Finding], list[tuple[Finding, dict]]]:
    """Split findings into (new, already-filed).

    Matches on the `<!-- watchdog-key: ... -->` marker in the body, with the exact
    title as a second layer for issues whose body was edited by a human. A
    watchdog that files 40 duplicate issues gets muted, which is the same
    outcome as not existing.
    """
    issues = list(open_issues)
    new: list[Finding] = []
    duplicates: list[tuple[Finding, dict]] = []
    for finding in findings:
        title = finding.title(title_prefix)
        match = None
        for issue in issues:
            body = issue.get("body") or ""
            if finding.marker in body or (issue.get("title") or "") == title:
                match = issue
                break
        if match is None:
            new.append(finding)
        else:
            duplicates.append((finding, match))
    return new, duplicates


# --------------------------------------------------------------------------
# issue body
# --------------------------------------------------------------------------

INCIDENT_NOTE = (
    "This watchdog exists because of the 2026-08-30..09-01 fleet-deployment freeze: "
    "a full Loki PVC crash-looped Loki 694 times, the permanently-unhealthy StatefulSet "
    "wedged one Argo sync operation in `phase=Running` for 37h, and every prod change "
    "queued silently behind it for 2d11h. Every component reported \"running\"; nothing alerted."
)


def build_issue_body(
    finding: Finding, config: dict, run_url: str = "", actions: Sequence[str] = ()
) -> str:
    mention = (config.get("issues") or {}).get("mention", "@fuze")
    lines = [
        finding.marker,
        "",
        f"{mention} the deployment-freeze watchdog detected **{finding.kind}**.",
        "",
        finding.summary,
        "",
        "### Diagnostics (verbatim from the cluster)",
        "",
        "```json",
        json.dumps(finding.facts, indent=2, sort_keys=True, default=str),
        "```",
        "",
    ]
    if actions:
        lines += ["### Automated action taken", ""]
        lines += [f"- {a}" for a in actions]
        lines += [""]
    else:
        lines += [
            "### Automated action taken",
            "",
            "- None. This watchdog is read-only against the cluster; prod is GitOps "
            "under Argo `selfHeal`, so any fix must land in git.",
            "",
        ]
    if run_url:
        lines += [f"Watchdog run: {run_url}", ""]
    lines += [
        "---",
        "",
        INCIDENT_NOTE,
        "",
        "Thresholds: `governance/watchdog-thresholds.json`. Detection: "
        "`scripts-tools/deployment_watchdog.py`. This issue is deduplicated on the "
        "`watchdog-key` marker above — it will not be re-filed while it stays open, "
        "so close it once the condition is actually resolved.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# cluster access — read-only, guarded
# --------------------------------------------------------------------------

# Same taxonomy tests/test_cluster_query_guard.py pins for cluster-query.yml.
MUTATING_TOKENS = {
    "exec", "attach", "cp", "port-forward", "proxy", "run", "delete", "apply",
    "edit", "patch", "replace", "scale", "rollout", "cordon", "drain",
    "annotate", "label", "set", "create", "taint", "uncordon", "debug",
}


def assert_safe_kubectl(args: Sequence[str]) -> None:
    """Reject anything that mutates state or whose OUTPUT would be a credential.

    FuzeInfra's job logs are PUBLIC. `kubectl get secret -o jsonpath=...` is a
    READ that passes every mutation check and prints the credential verbatim
    into a retained public log — that happened on 2026-07-29. Read-only is not
    the same as safe-to-log, so the Secret check is separate from the verb
    checks. `--raw` is allowed ONLY for the Prometheus service proxy path,
    because `kubectl config view --raw` would print this runner's cluster-admin
    kubeconfig.
    """
    for token in args:
        low = token.lower()
        if low in MUTATING_TOKENS:
            raise UnsafeCommand(f"refusing mutating/exec token: {token!r}")
        # `secret/foo`, `pods,secrets` and `secrets.v1.` each name the Secret
        # resource while being ONE token, so normalise separators first. Do not
        # split on '-': `litellm-secret-reader` is a NAME, not the resource.
        for piece in re.split(r"[/,.]", low):
            if piece in {"secret", "secrets"}:
                raise UnsafeCommand(
                    "refusing to read Secret objects: this job's log is public and retained"
                )
    raw = [a for a in args if a == "--raw" or a.startswith("--raw=")]
    if raw:
        if "config" in args:
            raise UnsafeCommand("refusing `config ... --raw`: it prints the runner's kubeconfig")
        idx = list(args).index(raw[0])
        path = raw[0].split("=", 1)[1] if "=" in raw[0] else (
            args[idx + 1] if idx + 1 < len(args) else ""
        )
        if not path.startswith("/api/v1/namespaces/"):
            raise UnsafeCommand(f"refusing --raw path outside the service proxy: {path!r}")


def kubectl_json(args: Sequence[str], timeout: int = 120) -> dict:
    return json.loads(kubectl(args, timeout=timeout) or "{}")


def kubectl(args: Sequence[str], timeout: int = 120) -> str:
    assert_safe_kubectl(args)
    try:
        proc = subprocess.run(
            ["kubectl", *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClusterUnreachable(f"kubectl {' '.join(args)} failed to run: {exc}") from exc
    if proc.returncode != 0:
        raise ClusterUnreachable(
            f"kubectl {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")


def kubectl_exec_df(namespace: str, pod: str, container: str, mount_path: str,
                    timeout: int = 60) -> str:
    """The ONLY exec this tool performs: a fixed `df` argv, validated, on a fallback path.

    Not routed through assert_safe_kubectl (which refuses `exec` outright, and
    should keep refusing it for every other caller). The safety here comes from
    the argv being fixed and every interpolated component being validated.
    """
    for value in (namespace, pod, container):
        if not _NAME_RE.match(value or ""):
            raise UnsafeCommand(f"refusing exec with suspicious name: {value!r}")
    if not _PATH_RE.match(mount_path or ""):
        raise UnsafeCommand(f"refusing exec with suspicious mount path: {mount_path!r}")
    argv = [
        "kubectl", "-n", namespace, "exec", pod, "-c", container, "--",
        "df", "-P", "-B1", mount_path,
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClusterUnreachable(f"exec df on {namespace}/{pod} failed: {exc}") from exc
    if proc.returncode != 0:
        raise ClusterUnreachable(
            f"exec df on {namespace}/{pod} exited {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def parse_df_output(text: str) -> tuple[int, int] | None:
    """Parse `df -P -B1` into (used_bytes, capacity_bytes)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    if len(parts) < 4:
        return None
    try:
        capacity = int(parts[1])
        used = int(parts[2])
    except ValueError:
        return None
    return used, capacity


# --------------------------------------------------------------------------
# PVC usage sources
# --------------------------------------------------------------------------

def parse_prometheus_vector(payload: dict, value_key: str = "ratio") -> list[dict]:
    """Turn a Prometheus instant-vector response into usage records.

    A non-success status, or a success with ZERO samples, is treated as blindness
    and raises: "Prometheus answered but knows nothing about any volume" must not
    render as "no PVC is full".
    """
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ClusterUnreachable(
            f"prometheus query failed: status={payload.get('status') if isinstance(payload, dict) else payload!r}"
        )
    result = ((payload.get("data") or {}).get("result")) or []
    if not result:
        raise ClusterUnreachable(
            "prometheus returned zero kubelet_volume_stats samples — cannot conclude "
            "that no volume is filling up"
        )
    records = []
    for sample in result:
        metric = sample.get("metric") or {}
        value = (sample.get("value") or [None, None])[1]
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        records.append(
            {
                "namespace": metric.get("namespace"),
                "claim": metric.get("persistentvolumeclaim"),
                value_key: parsed,
            }
        )
    return records


def merge_usage(ratios: list[dict], used: list[dict], capacity: list[dict]) -> list[dict]:
    index: dict[tuple[Any, Any], dict] = {}
    for rec in ratios:
        index[(rec.get("namespace"), rec.get("claim"))] = {
            "namespace": rec.get("namespace"),
            "claim": rec.get("claim"),
            "ratio": rec.get("ratio"),
            "source": "prometheus",
        }
    for rec in used:
        entry = index.get((rec.get("namespace"), rec.get("claim")))
        if entry is not None:
            entry["used_bytes"] = rec.get("used")
    for rec in capacity:
        entry = index.get((rec.get("namespace"), rec.get("claim")))
        if entry is not None:
            entry["capacity_bytes"] = rec.get("capacity")
    return list(index.values())


def prometheus_query(query: str, config: dict) -> dict:
    cfg = config.get("pvc_pressure", {})
    namespace = cfg.get("prometheus_namespace", "fuzeinfra")
    service = cfg.get("prometheus_service", "fuzeinfra-prometheus")
    port = cfg.get("prometheus_port", 9090)
    path = (
        f"/api/v1/namespaces/{namespace}/services/{service}:{port}/proxy"
        f"/api/v1/query?query={quote(query)}"
    )
    raw = kubectl(["get", "--raw", path])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClusterUnreachable(f"prometheus returned non-JSON: {exc}") from exc


def pvc_usage_via_prometheus(config: dict) -> list[dict]:
    cfg = config.get("pvc_pressure", {})
    query = cfg.get(
        "prometheus_query",
        "kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes",
    )
    ratios = parse_prometheus_vector(prometheus_query(query, config), "ratio")
    try:
        used = parse_prometheus_vector(
            prometheus_query("kubelet_volume_stats_used_bytes", config), "used"
        )
        capacity = parse_prometheus_vector(
            prometheus_query("kubelet_volume_stats_capacity_bytes", config), "capacity"
        )
    except ClusterUnreachable:
        # The ratio (the thing the threshold is applied to) already succeeded;
        # the byte columns are display-only, so their absence must not turn a
        # real detection into a failure.
        used, capacity = [], []
    return merge_usage(ratios, used, capacity)


def pvc_mount_targets(pods: dict) -> list[dict]:
    """Map each PVC to a Running pod/container/mountPath that mounts it."""
    targets: dict[tuple[str, str], dict] = {}
    for pod in pods.get("items", []) or []:
        meta = pod.get("metadata") or {}
        status = pod.get("status") or {}
        if status.get("phase") != "Running":
            continue
        spec = pod.get("spec") or {}
        volume_to_claim = {}
        for volume in spec.get("volumes") or []:
            claim = (volume.get("persistentVolumeClaim") or {}).get("claimName")
            if claim:
                volume_to_claim[volume.get("name")] = claim
        if not volume_to_claim:
            continue
        for container in spec.get("containers") or []:
            for mount in container.get("volumeMounts") or []:
                claim = volume_to_claim.get(mount.get("name"))
                if not claim:
                    continue
                key = (meta.get("namespace"), claim)
                targets.setdefault(
                    key,
                    {
                        "namespace": meta.get("namespace"),
                        "claim": claim,
                        "pod": meta.get("name"),
                        "container": container.get("name"),
                        "mount_path": mount.get("mountPath"),
                    },
                )
    return list(targets.values())


def pvc_usage_via_exec_df(pods: dict, config: dict) -> list[dict]:
    """Fallback used ONLY when Prometheus is unreachable."""
    targets = pvc_mount_targets(pods)
    if not targets:
        raise ClusterUnreachable(
            "prometheus unreachable and no running pod mounts any PVC — no way to "
            "measure volume usage"
        )
    records: list[dict] = []
    errors: list[str] = []
    for target in targets:
        try:
            out = kubectl_exec_df(
                target["namespace"], target["pod"], target["container"], target["mount_path"]
            )
        except (ClusterUnreachable, UnsafeCommand) as exc:
            errors.append(f"{target['namespace']}/{target['pod']}: {exc}")
            continue
        parsed = parse_df_output(out)
        if parsed is None:
            errors.append(f"{target['namespace']}/{target['pod']}: unparseable df output")
            continue
        used, capacity = parsed
        if capacity <= 0:
            continue
        records.append(
            {
                "namespace": target["namespace"],
                "claim": target["claim"],
                "ratio": used / capacity,
                "used_bytes": used,
                "capacity_bytes": capacity,
                "source": "kubectl-exec-df",
            }
        )
    if not records:
        raise ClusterUnreachable(
            "prometheus unreachable and every df fallback failed: " + "; ".join(errors[:5])
        )
    for err in errors:
        print(f"::warning::df fallback: {err}", file=sys.stderr)
    return records


def collect_pvc_usage(pods: dict, config: dict) -> tuple[list[dict], str]:
    """Returns (usage records, source name). Raises if BOTH sources fail."""
    try:
        return pvc_usage_via_prometheus(config), "prometheus"
    except ClusterUnreachable as exc:
        prom_error = str(exc)
        print(f"::warning::prometheus unusable ({prom_error}) — trying df fallback", file=sys.stderr)
    if not (config.get("pvc_pressure") or {}).get("allow_exec_df_fallback", True):
        raise ClusterUnreachable(
            f"prometheus unusable ({prom_error}) and the df fallback is disabled in config"
        )
    return pvc_usage_via_exec_df(pods, config), "kubectl-exec-df"


# --------------------------------------------------------------------------
# GitHub side
# --------------------------------------------------------------------------

def gh(args: Sequence[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubError(f"gh {' '.join(args)} failed to run: {exc}") from exc
    if proc.returncode != 0:
        raise GitHubError(
            f"gh {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )
    return proc.stdout


def list_open_watchdog_issues(repo: str, label: str) -> list[dict]:
    out = gh(
        [
            "issue", "list", "--repo", repo, "--state", "open", "--label", label,
            "--limit", "100", "--json", "number,title,body,url",
        ]
    )
    return json.loads(out or "[]")


def ensure_label(repo: str, label: str, color: str) -> None:
    try:
        gh(["label", "create", label, "--repo", repo, "--color", color,
            "--description", "deployment-freeze watchdog"])
    except GitHubError:
        # Already exists is the overwhelmingly common case and is not an error;
        # a genuinely broken gh surfaces on the next call, which is not tolerated.
        pass


def create_issue(repo: str, label: str, title: str, body: str) -> str:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(body)
        path = handle.name
    try:
        return gh(
            ["issue", "create", "--repo", repo, "--label", label, "--title", title,
             "--body-file", path]
        ).strip()
    finally:
        os.unlink(path)


def dispatch_terminate_op(repo: str, workflow: str, app: str, ref: str = "main") -> None:
    if not _NAME_RE.match(app or ""):
        raise UnsafeCommand(f"refusing to dispatch terminate-op for app name {app!r}")
    gh(["workflow", "run", workflow, "--repo", repo, "--ref", ref, "-f", f"app={app}"])


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def collect_cluster_state(config: dict) -> tuple[dict, dict]:
    argo_ns = (config.get("argo_stuck_op") or {}).get("namespace", "argocd")
    applications = kubectl_json(["-n", argo_ns, "get", "applications", "-o", "json"])
    pods = kubectl_json(["get", "pods", "-A", "-o", "json"])
    return applications, pods


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-url", default="")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="detect and report, but file no issue and dispatch nothing",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    now = utcnow()
    issues_cfg = config.get("issues") or {}
    label = issues_cfg.get("label", "deploy-watchdog")
    prefix = issues_cfg.get("title_prefix", "[watchdog]")

    # --- look. Any failure here is fatal: blind must never render as clear. ---
    applications, pods = collect_cluster_state(config)
    findings: list[Finding] = []
    findings += detect_stuck_argo_ops(applications, config, now)
    findings += detect_chronic_crashloop(pods, config, now)
    findings += detect_stuck_container_creating(pods, config, now)

    usage, usage_source = collect_pvc_usage(pods, config)
    findings += detect_pvc_pressure(usage, config)

    lines = [
        "## Deployment-freeze watchdog",
        "",
        f"- checked at: `{now.isoformat()}`",
        f"- Argo Applications scanned: {len(applications.get('items') or [])}",
        f"- pods scanned: {len(pods.get('items') or [])}",
        f"- PVC usage source: **{usage_source}** ({len(usage)} volumes)",
        f"- findings: **{len(findings)}**",
        "",
    ]
    for finding in findings:
        print(f"DETECTED {finding.key}: {finding.summary}")

    if not findings:
        lines.append("No stuck Argo operation, chronic crash loop, stuck ContainerCreating "
                     "or PVC above threshold.")
        print("\n".join(lines))
        write_summary("\n".join(lines))
        return 0

    if args.dry_run:
        lines.append("### Dry run — no issue filed, nothing dispatched")
        lines.append("")
        for finding in findings:
            lines.append(f"- `{finding.key}` — {finding.summary}")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(finding.facts, indent=2, sort_keys=True, default=str))
            lines.append("```")
        print("\n".join(lines))
        write_summary("\n".join(lines))
        return 1 if config.get("fail_on_findings", True) else 0

    if not args.repo:
        raise GitHubError("--repo (or GITHUB_REPOSITORY) is required to file issues")

    open_issues = list_open_watchdog_issues(args.repo, label)
    new, duplicates = filter_new_findings(findings, open_issues, prefix)
    for finding, issue in duplicates:
        lines.append(f"- `{finding.key}` — already tracked by {issue.get('url')} (not re-filed)")
    if new:
        ensure_label(args.repo, label, issues_cfg.get("label_color", "B60205"))

    # Cap issue CREATION (never detection): the first live run against prod
    # returned 55 distinct conditions. Filing 55 issues at once is the muting
    # failure in a different shape. Everything is still reported in the summary
    # and the run still goes red; the overflow files on later runs.
    max_per_run = int(issues_cfg.get("max_per_run", 10))
    ordered = prioritize(new, issues_cfg.get("priority"))
    to_file, overflow = ordered[:max_per_run], ordered[max_per_run:]
    if overflow:
        lines.append(
            f"- {len(overflow)} further condition(s) detected but NOT filed this run "
            f"(max_per_run={max_per_run}); they file on a later run as earlier ones close:"
        )
        lines += [f"  - `{f.key}` — {f.summary}" for f in overflow]

    argo_cfg = config.get("argo_stuck_op") or {}
    for finding in to_file:
        actions: list[str] = []
        if finding.kind == KIND_ARGO and argo_cfg.get("auto_terminate", True):
            workflow = argo_cfg.get("auto_terminate_workflow", "argo-terminate-op.yml")
            dispatch_terminate_op(
                args.repo, workflow, finding.subject, argo_cfg.get("auto_terminate_ref", "main")
            )
            actions.append(
                f"Dispatched `{workflow}` with `app={finding.subject}` — the operation had "
                f"already been Running past the "
                f"{argo_cfg.get('running_minutes', 45)}m threshold. Terminating a stale op is "
                "safe and reversible: Argo re-syncs from git, which is the desired state. "
                "Nothing else was touched."
            )
        url = create_issue(
            args.repo, label, finding.title(prefix),
            build_issue_body(finding, config, args.run_url, actions),
        )
        lines.append(f"- `{finding.key}` — filed {url}")
        for action in actions:
            lines.append(f"  - {action}")

    print("\n".join(lines))
    write_summary("\n".join(lines))
    return 1 if config.get("fail_on_findings", True) else 0


def run(argv: Sequence[str] | None = None) -> int:
    """Process entrypoint. Returns the exit code instead of exiting, so the
    blind-fails-loudly path is itself unit-testable (it is the property that
    makes this tool worth having)."""
    try:
        return main(argv)
    except (ClusterUnreachable, UnsafeCommand, GitHubError) as error:
        # FAIL LOUDLY. Never exit 0 on a path where the watchdog could not look:
        # "all clear" and "could not check" must not look the same.
        print(f"::error::deployment watchdog could not complete: {error}")
        write_summary(f"## Deployment-freeze watchdog FAILED\n\n`{error}`\n")
        return 2


if __name__ == "__main__":
    sys.exit(run())
