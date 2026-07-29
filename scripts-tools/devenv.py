#!/usr/bin/env python3
"""
devenv — one command that stands the FuzeInfra local dev environment up and
proves it actually works.

    python3 scripts-tools/devenv.py up

That is the whole contract: at any point in time, one run of this script and a
developer has a working local environment, or a clear reason why not. CI runs the
EXACT same command on a hosted runner (.github/workflows/kind-validate.yml), so
"works on my machine" and "works in CI" cannot drift apart — if the promise
breaks, the per-merge gate goes red rather than a developer discovering it.

Backends
--------
  kind            (default) a disposable 3-node cluster in Docker. Works on
                  Linux, macOS, Windows, and GitHub-hosted runners.
  docker-desktop  Docker Desktop's built-in Kubernetes. For developers who
                  already have it enabled, or where kind cannot run (e.g. Docker
                  Desktop on cgroup v1).
  auto            kind if the binary is present, else docker-desktop.

Both backends run the SAME addon install and chart deploy — devenv delegates to
k8s/kind/setup-kind.{sh,ps1}, passing --no-cluster for docker-desktop, rather
than carrying a second implementation that could drift.

Phases of `up`
--------------
  1. preflight  tools present, docker daemon reachable, ports free
  2. deploy     cluster (if needed) + ingress-nginx + cert-manager + the chart
  3. verify     every ENABLED service has a Ready workload and answers a probe
                (scripts-tools/validate_kind_deployment.py)
  4. smoke      the pytest suite against the live cluster via port-forward
                (scripts-tools/kind_port_forward.py) — skip with --no-smoke

Any phase failing dumps focused diagnostics (non-Ready pods, their events and
logs) before exiting non-zero, because "it timed out" on its own is not
actionable.

Exit codes: 0 ok · 1 a phase failed · 2 preflight failed (missing tool/daemon).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "fuzeinfra"
RELEASE = "fuzeinfra"
KIND_CLUSTER = "fuzeinfra"
KIND_CONTEXT = f"kind-{KIND_CLUSTER}"
DOCKER_DESKTOP_CONTEXT = "docker-desktop"

IS_WINDOWS = platform.system() == "Windows"
PY = "python" if IS_WINDOWS else "python3"

#: Ports kind-cluster.yaml publishes on the host. A conflict here is the single
#: most common local failure and produces a baffling error deep inside `kind
#: create`, so it is worth catching by name up front.
KIND_HOST_PORTS = (80, 443)

INSTALL_HINTS = {
    "docker": "https://docs.docker.com/get-docker/ (Docker Desktop on macOS/Windows)",
    "kind": "https://kind.sigs.k8s.io/docs/user/quick-start/#installation",
    "kubectl": "https://kubernetes.io/docs/tasks/tools/",
    "helm": "https://helm.sh/docs/intro/install/",
}


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def phase(msg: str) -> None:
    print(_c("1;36", f"\n==> {msg}"), flush=True)


def ok(msg: str) -> None:
    print(_c("32", f"    OK  {msg}"), flush=True)


def warn(msg: str) -> None:
    print(_c("33", f"    !   {msg}"), flush=True)


def fail(msg: str) -> None:
    print(_c("1;31", f"    ERR {msg}"), flush=True)


# ---------------------------------------------------------------------------
# shell helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], check: bool = True, capture: bool = False, **kw):
    """Run a command, echoing it so the log doubles as a reproduction script."""
    if not capture:
        print(_c("2", f"    $ {' '.join(cmd)}"), flush=True)
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
        cwd=kw.pop("cwd", REPO_ROOT),
        **kw,
    )


def out(cmd: list[str]) -> str:
    try:
        return run(cmd, check=False, capture=True).stdout.strip()
    except FileNotFoundError:
        return ""


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
# backend selection
# ---------------------------------------------------------------------------


def resolve_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if have("kind"):
        return "kind"
    if DOCKER_DESKTOP_CONTEXT in out(["kubectl", "config", "get-contexts", "-o", "name"]):
        warn("kind not installed — falling back to the docker-desktop backend")
        return "docker-desktop"
    return "kind"  # so preflight reports the missing kind binary with a hint


def context_for(backend: str) -> str:
    return KIND_CONTEXT if backend == "kind" else DOCKER_DESKTOP_CONTEXT


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------


def preflight(backend: str, fresh: bool) -> None:
    phase(f"Preflight ({backend} backend)")

    required = ["docker", "kubectl", "helm"] + (["kind"] if backend == "kind" else [])
    missing = [t for t in required if not have(t)]
    if missing:
        for t in missing:
            fail(f"{t} not found in PATH — install: {INSTALL_HINTS.get(t, '')}")
        sys.exit(2)
    ok(f"tools present: {', '.join(required)}")

    if subprocess.run(
        ["docker", "info"], capture_output=True, text=True
    ).returncode != 0:
        fail("the docker daemon is not reachable — start Docker Desktop / dockerd")
        sys.exit(2)
    ok("docker daemon reachable")

    if backend == "docker-desktop":
        contexts = out(["kubectl", "config", "get-contexts", "-o", "name"])
        if DOCKER_DESKTOP_CONTEXT not in contexts:
            fail(
                "no 'docker-desktop' kube-context — enable Kubernetes in "
                "Docker Desktop → Settings → Kubernetes, then retry"
            )
            sys.exit(2)
        ok("docker-desktop kube-context present")

    # Only meaningful when we are about to CREATE the kind cluster; an existing
    # cluster already holds these ports and that is not a conflict.
    if backend == "kind":
        creating = fresh or KIND_CLUSTER not in out(["kind", "get", "clusters"]).split()
        if creating:
            busy = [p for p in KIND_HOST_PORTS if port_in_use(p)]
            if busy:
                fail(
                    f"host port(s) {busy} are already bound. kind publishes "
                    f"{list(KIND_HOST_PORTS)} for ingress; free them (a local nginx/"
                    "Apache/Traefik is the usual culprit) and retry."
                )
                sys.exit(2)
            ok(f"host ports {list(KIND_HOST_PORTS)} free")


def teardown(backend: str) -> None:
    phase("Tearing down")
    if backend == "kind":
        script = (
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(REPO_ROOT / "k8s/kind/teardown-kind.ps1")]
            if IS_WINDOWS
            else [str(REPO_ROOT / "k8s/kind/teardown-kind.sh")]
        )
        run(script, check=False)
    else:
        # Never delete a developer's Docker Desktop cluster — just remove ours.
        run(
            ["helm", "uninstall", RELEASE, "-n", NAMESPACE,
             "--kube-context", DOCKER_DESKTOP_CONTEXT],
            check=False,
        )
    ok("torn down")


def deploy(backend: str, profile: str | None) -> None:
    phase(f"Deploying the stack ({backend}, profile={profile or 'default/full'})")

    if backend == "docker-desktop":
        run(["kubectl", "config", "use-context", DOCKER_DESKTOP_CONTEXT])

    if IS_WINDOWS:
        cmd = ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(REPO_ROOT / "k8s/kind/setup-kind.ps1")]
        if profile:
            cmd += ["-Profile", profile]
        if backend == "docker-desktop":
            cmd += ["-NoCluster"]
    else:
        cmd = [str(REPO_ROOT / "k8s/kind/setup-kind.sh")]
        if profile:
            cmd += ["--profile", profile]
        if backend == "docker-desktop":
            cmd += ["--no-cluster"]

    run(cmd)
    ok("deploy step finished")


def verify(namespace: str, timeout: int, probes: bool) -> None:
    phase("Verifying every enabled service is Ready + reachable")
    cmd = [
        PY, str(REPO_ROOT / "scripts-tools/validate_kind_deployment.py"),
        "--reuse", "--namespace", namespace, "--timeout", str(timeout),
    ]
    if not probes:
        cmd.append("--no-probes")
    run(cmd)
    ok("all enabled services Ready")


def smoke(namespace: str) -> None:
    phase("Functional smoke tests (pytest via port-forward)")
    run([
        PY, str(REPO_ROOT / "scripts-tools/kind_port_forward.py"),
        "--namespace", namespace,
        "--", "pytest", "tests/", "-v", "-m", "not integration",
    ])
    ok("smoke tests passed")


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


def diagnostics(namespace: str) -> None:
    """Print why it failed. A bare timeout is not an actionable error message."""
    phase("Diagnostics (collected because a phase failed)")

    print(_c("1", "\n--- workloads ---"))
    run(["kubectl", "-n", namespace, "get", "pods,svc,ingress", "-o", "wide"], check=False)

    print(_c("1", "\n--- recent events ---"))
    run(["kubectl", "-n", namespace, "get", "events",
         "--sort-by=.lastTimestamp"], check=False)

    print(_c("1", "\n--- not-ready pods: describe + logs ---"))
    raw = out(["kubectl", "-n", namespace, "get", "pods", "-o", "json"])
    if not raw:
        return
    try:
        pods = json.loads(raw).get("items", [])
    except ValueError:
        return

    for pod in pods:
        name = pod["metadata"]["name"]
        statuses = pod.get("status", {}).get("containerStatuses") or []
        if statuses and all(s.get("ready") for s in statuses):
            continue
        print(_c("1;33", f"\n### {name}"))
        run(["kubectl", "-n", namespace, "describe", "pod", name], check=False)
        run(["kubectl", "-n", namespace, "logs", name,
             "--all-containers", "--tail=60"], check=False)
        run(["kubectl", "-n", namespace, "logs", name,
             "--all-containers", "--previous", "--tail=60"], check=False)


def status(namespace: str) -> int:
    ctx = out(["kubectl", "config", "current-context"]) or "(none)"
    phase(f"Status — context {ctx}, namespace {namespace}")
    if not ctx or ctx == "(none)":
        fail("no current kube-context — nothing is up")
        return 1
    r = run(["kubectl", "-n", namespace, "get", "pods,svc,ingress"], check=False)
    return r.returncode


def summary(backend: str, elapsed: float, smoked: bool) -> None:
    ctx = context_for(backend)
    print(_c("1;32", f"\n{'=' * 68}"))
    print(_c("1;32", f"  Local dev environment is UP and VERIFIED  ({elapsed / 60:.1f} min)"))
    print(_c("1;32", f"{'=' * 68}"))
    print(f"""
  backend        {backend}
  kube-context   {ctx}
  namespace      {NAMESPACE}
  smoke tests    {'passed' if smoked else 'skipped (--no-smoke)'}

  Pods           kubectl -n {NAMESPACE} get pods
  Re-verify      {PY} scripts-tools/devenv.py verify
  Tear down      {PY} scripts-tools/devenv.py down

  UIs are served through ingress on *.dev.local. Point them at 127.0.0.1 —
  either via the in-cluster dnsmasq wildcard or one hosts-file line:

    127.0.0.1 grafana.dev.local prometheus.dev.local airflow.dev.local \\
              flower.dev.local kafka-ui.dev.local mongo-express.dev.local \\
              rabbitmq.dev.local neo4j.dev.local alertmanager.dev.local

  Then open http://grafana.dev.local (admin / admin).
""")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="devenv",
        description="Stand up and verify the FuzeInfra local dev environment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  devenv.py up                              full stack on kind, then verify + smoke
  devenv.py up --profile minimal            postgres + redis only (fast)
  devenv.py up --backend docker-desktop     use Docker Desktop's Kubernetes
  devenv.py up --fresh                      delete any existing cluster first
  devenv.py verify                          re-check whatever is already running
  devenv.py down                            tear it down
""",
    )
    ap.add_argument("command", choices=["up", "verify", "down", "status"])
    ap.add_argument("--backend", choices=["auto", "kind", "docker-desktop"], default="auto")
    ap.add_argument("--profile", default=None,
                    help="helm/fuzeinfra/profiles/<name>.yaml (minimal, data-stores, full)")
    ap.add_argument("--namespace", default=NAMESPACE)
    ap.add_argument("--timeout", type=int, default=900,
                    help="seconds to wait for workloads to become Ready (default 900)")
    ap.add_argument("--no-smoke", action="store_true", help="skip the pytest smoke suite")
    ap.add_argument("--no-probes", action="store_true", help="skip in-cluster reachability probes")
    ap.add_argument("--fresh", action="store_true",
                    help="tear down before bringing up (proves a from-scratch build)")
    args = ap.parse_args()

    backend = resolve_backend(args.backend)

    if args.command == "status":
        return status(args.namespace)

    if args.command == "down":
        teardown(backend)
        return 0

    if args.command == "verify":
        try:
            verify(args.namespace, args.timeout, probes=not args.no_probes)
            if not args.no_smoke:
                smoke(args.namespace)
        except subprocess.CalledProcessError:
            diagnostics(args.namespace)
            return 1
        ok("verified")
        return 0

    # up
    started = time.time()
    try:
        preflight(backend, args.fresh)
        if args.fresh:
            teardown(backend)
        deploy(backend, args.profile)
        verify(args.namespace, args.timeout, probes=not args.no_probes)
        if not args.no_smoke:
            smoke(args.namespace)
    except subprocess.CalledProcessError as exc:
        fail(f"phase failed: {' '.join(exc.cmd) if isinstance(exc.cmd, list) else exc.cmd}")
        diagnostics(args.namespace)
        print(_c("1;31", "\n  Local dev environment did NOT come up cleanly."))
        print("  The environment was left running so you can inspect it; "
              f"tear it down with: {PY} scripts-tools/devenv.py down\n")
        return 1
    except KeyboardInterrupt:
        warn("interrupted")
        return 130

    summary(backend, time.time() - started, smoked=not args.no_smoke)
    return 0


if __name__ == "__main__":
    sys.exit(main())
