"""Executable invariants for the cluster-query arg filter.

cluster-query hands any agent session read-only kubectl against PROD and echoes the
result into a GitHub Actions job log. Two properties have to hold, and only one of
them is about mutation:

  1. nothing that changes cluster state may run, and
  2. nothing whose OUTPUT is a credential may run.

(2) is the one that is easy to miss, because `kubectl get secret` is a read. It
passes every mutation check, and it prints the credential verbatim into a log that
is retained and readable by anyone with repo access. That happened on 2026-07-29 —
LITELLM_MASTER_KEY was fetched this way and the run's logs had to be deleted after
the fact. Read-only is not the same as safe-to-log.

These tests EXECUTE the workflow's real filter rather than grepping it for keywords.
A guard asserted by substring passes just as happily when the shell logic around it
is broken, and the interesting cases here are exactly the ones where the logic is
subtle: `secret/foo`, `pods,secrets` and `secrets.v1.` all name the Secret resource
while being a single whitespace-delimited token, so the naive check misses all three.

Offline: parses one YAML file and runs bash. No cluster, no network.
"""

import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/cluster-query.yml"

STEP_NAME = "Run read-only kubectl"


def _filter_script() -> str:
    """The workflow's arg-filter step, with the kubectl call itself removed.

    Everything up to `echo "### kubectl ..."` is validation; the line after it
    actually talks to the cluster. Truncating there lets the real filter run
    unmodified while making the script inert.
    """
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["query"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == STEP_NAME)

    marker = 'echo "### kubectl'
    assert marker in script, (
        f"the {STEP_NAME!r} step no longer contains {marker!r}; this test truncates "
        "the script there to keep it inert and must be updated with the step"
    )
    return script.split(marker)[0]


def run_filter(args: str) -> subprocess.CompletedProcess:
    """Run the real filter with ARGS=args. Returncode 0 means the args were allowed."""
    return subprocess.run(
        ["bash", "-e", "-c", _filter_script()],
        env={"ARGS": args, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


# --- reads that must keep working ------------------------------------------------
# Regression cover for the guard being too broad. Every one of these is a read a
# human legitimately needs, and several MENTION a secret without reading one.
ALLOWED = [
    "get nodes",
    "-n fuzeinfra get pods -o wide",
    "-n fuzeinfra get pods,svc,endpoints,ingress,networkpolicy -o wide",
    "-n fuzeinfra logs litellm-69d54765ff-sc9gz --tail=120",
    "-n fuzeinfra logs deploy/fuzeinfra-cloudflare-tunnel --tail=200",
    "-n kube-system describe pod traefik",
    "-n fuzeinfra get events --sort-by=.lastTimestamp",
    # SealedSecrets are encrypted at rest — readable on purpose.
    "-n fuzeinfra get sealedsecret litellm-secret -o yaml",
    "-n fuzeinfra get sealedsecrets",
    # Names that merely CONTAIN "secret" must not trip the guard.
    "-n fuzeinfra describe deployment litellm-secret-reader",
    "-n fuzeinfra logs job/fuzeinfra-sealed-secrets-sync",
]

# --- credential reads that must be rejected --------------------------------------
# The bare forms plus every separator/casing variant that names the same resource
# while surviving a naive token match.
BLOCKED_SECRET_READS = [
    "-n fuzeinfra get secret litellm-secret -o jsonpath={.data.LITELLM_MASTER_KEY}",
    "-n fuzeinfra get secrets",
    "get secrets -A -o yaml",
    "-n fuzeinfra describe secret litellm-secret",
    # separator variants — one $ARGS token each
    "-n fuzeinfra get secret/litellm-secret -o yaml",
    "-n fuzeinfra get pods,secrets",
    "-n fuzeinfra get secrets.v1. -o yaml",
    "-n fuzeinfra get secrets.v1.core/litellm-secret",
    # kubectl accepts the capitalised kind
    "-n fuzeinfra get Secret litellm-secret -o yaml",
    "-n fuzeinfra get SECRETS",
]

# --- mutation/exec, i.e. the pre-existing guard ------------------------------------
BLOCKED_MUTATIONS = [
    "-n fuzeinfra delete pod litellm-69d54765ff-sc9gz",
    "-n fuzeinfra exec litellm-0 -- env",
    "-n fuzeinfra port-forward svc/litellm 4000:4000",
    "apply -f helm/litellm",
    "-n fuzeinfra patch deployment litellm --type=merge -p {}",
]


@pytest.mark.parametrize("args", ALLOWED)
def test_legitimate_reads_are_allowed(args):
    result = run_filter(args)
    assert result.returncode == 0, (
        f"cluster-query rejected a legitimate read: {args!r}\n{result.stdout}{result.stderr}"
    )


@pytest.mark.parametrize("args", BLOCKED_SECRET_READS)
def test_secret_reads_are_rejected(args):
    result = run_filter(args)
    assert result.returncode != 0, (
        f"cluster-query ALLOWED a Secret read: {args!r}\n"
        "Its output would land in the job log, which is retained and readable by "
        f"anyone with repo access.\n{result.stdout}{result.stderr}"
    )
    assert "job log" in result.stdout, (
        "a Secret read was rejected, but not by the secret guard — the error should "
        f"explain the logging risk and point at the SSH alternative.\n{result.stdout}"
    )


@pytest.mark.parametrize("args", BLOCKED_MUTATIONS)
def test_mutating_args_are_rejected(args):
    result = run_filter(args)
    assert result.returncode != 0, (
        f"cluster-query ALLOWED a mutating/exec command: {args!r}\n"
        f"{result.stdout}{result.stderr}"
    )


def test_read_verb_is_still_required():
    """A command with no read verb at all is refused (pre-existing behaviour)."""
    result = run_filter("-n fuzeinfra")
    assert result.returncode != 0
    assert "read verb" in result.stdout


def test_empty_args_are_a_no_op():
    """The default dispatch path: no args is not an error."""
    result = run_filter("")
    assert result.returncode == 0
