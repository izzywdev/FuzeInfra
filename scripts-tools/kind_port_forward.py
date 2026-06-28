#!/usr/bin/env python3
"""
Port-forward the deployed FuzeInfra services to the localhost ports the pytest
suite expects (tests/conftest.py), then run a command (default: pytest tests/).

This is the *functional* smoke layer of the local-k8s gate — it reuses the
existing tests/ suite (written for docker-compose's host-published ports) against
the kind cluster, with no test rewrite. The deployability gate
(validate_kind_deployment.py) verifies readiness; this verifies real reachability.

Each entry is (local_port, remote_port, service-name keyword). The k8s Service is
discovered by keyword (so it adapts to the release name) and skipped if absent
(disabled service / profile). Grafana (3000→3001) and Airflow (8080→8082) are the
only port remaps; everything else is 1:1, matching conftest.

Usage:
  python scripts-tools/kind_port_forward.py [--namespace fuzeinfra] -- pytest tests/ -v
  python scripts-tools/kind_port_forward.py            # defaults to: pytest tests/
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time

# (local_port, remote_port, service-name keyword) — keyword matches the chart's
# `fuzeinfra-<svc>` Service names. Longest-keyword match wins (kafka-ui vs kafka).
FORWARDS = [
    (5432, 5432, "postgres"),
    (27017, 27017, "mongodb"),
    (6379, 6379, "redis"),
    (7474, 7474, "neo4j"),
    (7687, 7687, "neo4j"),
    (9200, 9200, "elasticsearch"),
    (29092, 29092, "kafka"),
    (9090, 9090, "prometheus"),
    (3001, 3000, "grafana"),          # conftest expects grafana on 3001
    (9093, 9093, "alertmanager"),
    (3100, 3100, "loki"),
    (8081, 8081, "mongo-express"),
    (8080, 8080, "kafka-ui"),
    (15672, 15672, "rabbitmq"),
    (8082, 8080, "airflow-webserver"),  # conftest expects airflow on 8082
    (5555, 5555, "airflow-flower"),
]


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout


def services(namespace: str) -> list[str]:
    rc, out = run(["kubectl", "-n", namespace, "get", "svc", "-o",
                   "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"])
    return [s for s in out.splitlines() if s] if rc == 0 else []


def match_service(svcs: list[str], keyword: str) -> str | None:
    # Prefer the longest service name containing the keyword but excluding more
    # specific siblings (so "kafka" doesn't grab "kafka-ui").
    cands = [s for s in svcs if keyword in s]
    if not cands:
        return None
    # exact-ish: shortest candidate that still contains the keyword as a token
    cands.sort(key=len)
    return cands[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--namespace", default="fuzeinfra")
    ap.add_argument("--settle", type=int, default=6, help="seconds to let forwards establish")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="command to run after '--' (default: pytest tests/)")
    args = ap.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        cmd = ["pytest", "tests/"]

    svcs = services(args.namespace)
    if not svcs:
        print(f"!! no Services found in namespace '{args.namespace}'. Is the cluster up?",
              file=sys.stderr)
        return 2

    procs: list[subprocess.Popen] = []

    def cleanup():
        for p in procs:
            try:
                if os.name == "nt":
                    p.terminate()
                else:
                    p.send_signal(signal.SIGTERM)
            except Exception:
                pass
    atexit.register(cleanup)

    print("==> Establishing port-forwards")
    forwarded = []
    for local, remote, kw in FORWARDS:
        svc = match_service(svcs, kw)
        if not svc:
            continue  # service not deployed (disabled/profile) — skip silently
        p = subprocess.Popen(
            ["kubectl", "-n", args.namespace, "port-forward",
             f"svc/{svc}", f"{local}:{remote}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        forwarded.append(f"{svc} {local}->{remote}")
    for f in forwarded:
        print(f"    {f}")
    if not forwarded:
        print("!! nothing to forward", file=sys.stderr)
        return 2

    time.sleep(args.settle)

    # conftest reads POSTGRES_* from env; align to the chart's dev defaults.
    env = dict(os.environ)
    env.setdefault("POSTGRES_HOST", "localhost")
    env.setdefault("POSTGRES_PORT", "5432")

    print(f"\n==> Running: {' '.join(cmd)}\n")
    rc = subprocess.run(cmd, env=env).returncode
    cleanup()
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
