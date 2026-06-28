#!/usr/bin/env python3
"""
Validate that every *enabled* FuzeInfra service stands up Ready (and, where a
cheap probe exists, is reachable) on a local kind cluster.

This is the "the entire env is deployable" gate. Different consuming repos need
different service subsets (one needs mongo, another neo4j, another kafka), so the
platform must prove each enabled service actually comes up — not just that the
chart renders.

What it does
  1. Reads the *computed* Helm values (`helm get values <release> -a`) to learn
     which services are enabled (honours whatever profile/overlay was deployed).
  2. Lists Deployments / StatefulSets / DaemonSets in the namespace and matches
     each enabled service to its workload(s), asserting all replicas are Ready.
  3. Runs a light in-cluster connectivity probe for the core data/monitoring
     services (best-effort; deep functional checks live in the pytest suite via
     scripts-tools/kind_port_forward.py).
  4. Prints a service x {ready, reachable} matrix and exits non-zero if any
     enabled service has no Ready workload.

Cross-platform: shells out to `kubectl` and `helm` (both prereqs of the kind
setup), so it needs no Python packages.

Usage:
  python scripts-tools/validate_kind_deployment.py [--reuse] [--namespace fuzeinfra]
                                                    [--release fuzeinfra]
                                                    [--timeout 600] [--no-probes]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# service key in values.yaml -> workload-name keyword used to match the running
# Deployment/StatefulSet/DaemonSet. Each workload is assigned to the service with
# the LONGEST matching keyword, so "kafka-ui" wins over "kafka" and
# "mongo-express" never matches the "mongodb" service.
SERVICE_KEYWORDS = {
    "postgres": "postgres",
    "mongodb": "mongodb",
    "mongoExpress": "mongo-express",
    "redis": "redis",
    "neo4j": "neo4j",
    "elasticsearch": "elasticsearch",
    "chromadb": "chromadb",
    "kafka": "kafka",
    "kafkaUi": "kafka-ui",
    "rabbitmq": "rabbitmq",
    "prometheus": "prometheus",
    "grafana": "grafana",
    "alertmanager": "alertmanager",
    "loki": "loki",
    "promtail": "promtail",
    "nodeExporter": "node-exporter",
    "kubeStateMetrics": "kube-state-metrics",
    "airflow": "airflow",
    "dnsmasq": "dnsmasq",
}

# Optional in-cluster reachability probe per service: a shell command run inside
# the service's own pod. None => readiness-only (k8s readiness already implies the
# port is accepting). Probes are best-effort confidence checks; the pytest suite
# does the deep functional verification.
SERVICE_PROBES = {
    "postgres": "pg_isready -U postgres 2>/dev/null || pg_isready",
    "redis": "redis-cli ping",
    "elasticsearch": "curl -sf http://localhost:9200/_cluster/health >/dev/null && echo ok",
    "prometheus": "wget -qO- http://localhost:9090/-/healthy >/dev/null && echo ok",
    "grafana": "wget -qO- http://localhost:3000/api/health >/dev/null && echo ok",
    "loki": "wget -qO- http://localhost:3100/ready >/dev/null && echo ok",
    "alertmanager": "wget -qO- http://localhost:9093/-/healthy >/dev/null && echo ok",
    "rabbitmq": "rabbitmq-diagnostics -q ping",
}

OK = "ok"
FAIL = "FAIL"
SKIP = "-"


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def enabled_services(release: str, namespace: str) -> list[str]:
    """Read computed Helm values; return the enabled service keys we know about."""
    rc, out, err = run(["helm", "get", "values", release, "-n", namespace, "-a", "-o", "json"])
    if rc != 0:
        print(f"!! could not read helm values ({err.strip()}); falling back to "
              f"deployed-workload discovery", file=sys.stderr)
        return []  # caller falls back to "validate whatever is deployed"
    values = json.loads(out or "{}")
    enabled = []
    for svc in SERVICE_KEYWORDS:
        node = values.get(svc)
        # default-enabled unless explicitly false; matches the chart's `enabled` gates
        if isinstance(node, dict) and node.get("enabled", True) is not False:
            enabled.append(svc)
    return enabled


def list_workloads(namespace: str) -> list[dict]:
    """Return [{name, kind, desired, ready}] for all workloads in the namespace."""
    rc, out, err = run(["kubectl", "-n", namespace, "get",
                        "deployments,statefulsets,daemonsets", "-o", "json"])
    if rc != 0:
        print(f"!! kubectl get workloads failed: {err.strip()}", file=sys.stderr)
        return []
    items = json.loads(out or "{}").get("items", [])
    workloads = []
    for it in items:
        kind = it["kind"]
        name = it["metadata"]["name"]
        status = it.get("status", {})
        if kind == "DaemonSet":
            desired = status.get("desiredNumberScheduled", 0)
            ready = status.get("numberReady", 0)
        else:  # Deployment / StatefulSet
            desired = it.get("spec", {}).get("replicas", 0)
            ready = status.get("readyReplicas", 0)
        workloads.append({"name": name, "kind": kind, "desired": desired, "ready": ready})
    return workloads


def assign(workloads: list[dict]) -> dict[str, list[dict]]:
    """Map each workload to the service with the longest matching keyword."""
    by_service: dict[str, list[dict]] = {}
    for wl in workloads:
        best_svc, best_len = None, -1
        for svc, kw in SERVICE_KEYWORDS.items():
            if kw in wl["name"] and len(kw) > best_len:
                best_svc, best_len = svc, len(kw)
        if best_svc:
            by_service.setdefault(best_svc, []).append(wl)
    return by_service


def first_ready_pod(namespace: str, workload_name: str) -> str | None:
    # Pods are conventionally named "<workload>-..."; grab a Running one.
    rc, out, _ = run(["kubectl", "-n", namespace, "get", "pods",
                      "-o", "jsonpath={range .items[*]}{.metadata.name}{\" \"}{.status.phase}{\"\\n\"}{end}"])
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith(workload_name) and parts[1] == "Running":
            return parts[0]
    return None


def probe(namespace: str, pod: str, command: str, timeout: int = 30) -> bool:
    rc, out, _ = run(["kubectl", "-n", namespace, "exec", pod, "--",
                      "sh", "-c", command], timeout=timeout)
    return rc == 0


def wait_until_ready(namespace: str, services: list[str], timeout: int) -> None:
    """Block (best-effort) until workloads settle, so the matrix isn't a snapshot
    of a mid-rollout state. Uses kubectl rollout status per workload."""
    deadline = time.time() + timeout
    workloads = assign(list_workloads(namespace))
    targets = [wl for svc in services for wl in workloads.get(svc, [])]
    for wl in targets:
        remaining = max(5, int(deadline - time.time()))
        kind = wl["kind"].lower()
        if kind == "daemonset":
            continue  # rollout status on DS can hang on single-node; readiness check covers it
        run(["kubectl", "-n", namespace, "rollout", "status",
             f"{kind}/{wl['name']}", f"--timeout={remaining}s"], timeout=remaining + 10)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--namespace", default="fuzeinfra")
    ap.add_argument("--release", default="fuzeinfra")
    ap.add_argument("--timeout", type=int, default=600,
                    help="overall budget (s) for workloads to become Ready")
    ap.add_argument("--reuse", action="store_true",
                    help="use the running cluster as-is (no-op flag; the script never creates one)")
    ap.add_argument("--no-probes", action="store_true", help="skip in-cluster reachability probes")
    args = ap.parse_args()

    for tool in ("kubectl", "helm"):
        if run([tool, "version", "--client"] if tool == "kubectl" else [tool, "version"])[0] not in (0,):
            pass  # version flags differ across versions; tolerate, real calls will error clearly

    print(f"==> Validating release '{args.release}' in namespace '{args.namespace}'")
    services = enabled_services(args.release, args.namespace)
    workloads = assign(list_workloads(args.namespace))
    if not services:
        # Fallback: validate whatever workloads are deployed.
        services = sorted(workloads.keys())
        print("    (using deployed-workload discovery)")
    print(f"    enabled services: {', '.join(services)}\n")

    print("==> Waiting for workloads to become Ready (budget %ds)..." % args.timeout)
    try:
        wait_until_ready(args.namespace, services, args.timeout)
    except subprocess.TimeoutExpired:
        print("    (rollout wait hit the budget; reporting current state)")

    # Re-read after the wait for an accurate matrix.
    workloads = assign(list_workloads(args.namespace))

    rows, failures = [], []
    for svc in services:
        wls = workloads.get(svc, [])
        if not wls:
            rows.append((svc, "MISSING", FAIL, "no Deployment/StatefulSet/DaemonSet found"))
            failures.append(svc)
            continue
        not_ready = [w for w in wls if w["ready"] < (w["desired"] or 1)]
        ready_str = "/".join(f"{w['ready']}/{w['desired']}" for w in wls)
        if not_ready:
            rows.append((svc, f"ready {ready_str}", FAIL, "workload not fully Ready"))
            failures.append(svc)
            continue

        reach = SKIP
        if not args.no_probes and svc in SERVICE_PROBES:
            pod = first_ready_pod(args.namespace, wls[0]["name"])
            if pod:
                reach = OK if probe(args.namespace, pod, SERVICE_PROBES[svc]) else FAIL
                # A probe failure is a warning, not a hard fail — readiness already
                # gates acceptance, and deep checks run in the pytest suite.
        rows.append((svc, f"ready {ready_str}", reach, ""))

    print("\n==> Result matrix")
    print(f"    {'SERVICE':<18}{'WORKLOAD':<16}{'REACHABLE':<11}NOTE")
    for svc, wl, reach, note in rows:
        print(f"    {svc:<18}{wl:<16}{reach:<11}{note}")

    if failures:
        print(f"\n[FAIL] {len(failures)} enabled service(s) not deployable: {', '.join(failures)}")
        return 1
    print(f"\n[OK] all {len(services)} enabled service(s) are Ready.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
