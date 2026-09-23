"""Executable invariants for the secret-provision request filter.

secret-provision is the only path by which an agent session can cause a write to
this repo's Actions secrets, so its guard carries two properties that are easy to
conflate and must both hold:

  1. no caller may transport a secret VALUE into the workflow, and
  2. no caller may target a credential the platform depends on.

(1) is the one that bites. This repo's job logs are PUBLIC — the same fact that
forces cluster-query to refuse `kubectl get secret` — so a value accepted as a
`workflow_dispatch` input or a `client_payload` field is a published credential
the moment anyone dispatches it. The defence is structural rather than a filter:
the workflow declares no such input at all, and `test_no_input_transports_a_value`
fails the build if one is ever added. A reviewer adding `value:` to make the
workflow "more useful" is exactly the regression this file exists to stop.

(2) is why PROTECTED exists. `SECRETS_ADMIN_PAT` is the workflow's OWN credential:
letting a run rewrite it either locks the workflow out of the repo or silently
swaps the identity that every subsequent run acts as. `copy_from` is guarded for
the same reason even though a copy never prints anything — re-keying a privileged
credential under a caller-chosen name is a lateral move, not a read.

These tests EXECUTE the workflow's real bash guard rather than grepping it for
keywords. A guard asserted by substring passes just as happily when the shell
logic around it is broken, and the subtle cases here — a lowercase name, a
leading digit, `copy_from` equal to the target — are precisely the ones a
keyword check cannot distinguish.

Offline: parses one YAML file and runs bash. No network, no GitHub, no cluster.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# The subject IS the Linux bash guard that runs on GitHub Actions runners; there
# is no PowerShell equivalent, and porting it would test something other than the
# real gate. On Windows, subprocess passes a multi-line `-c` script through
# CreateProcess/list2cmdline and truncates it at the first newline, so every
# BLOCKED case would exit 0 and pass vacuously.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="tests the real Linux CI bash guard — Git Bash truncates multi-line -c scripts",
)

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/secret-provision.yml"

VALIDATE_STEP = "Validate request"
PROVISION_STEP = "Provision"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on() -> dict:
    """The workflow's trigger block.

    YAML 1.1 resolves a bare `on:` key to the boolean True, so `wf["on"]` is a
    KeyError against a perfectly valid workflow. Look up both spellings rather
    than depending on which resolver the installed PyYAML uses.
    """
    wf = _workflow()
    for key in ("on", True):
        if key in wf:
            return wf[key]
    raise AssertionError(f"no trigger block in {WORKFLOW}")


def _step(name: str) -> dict:
    for step in _workflow()["jobs"]["provision"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in {WORKFLOW}")


def run_guard(tmp_path, **env) -> subprocess.CompletedProcess:
    """Execute the real validation step with the given inputs."""
    github_env = tmp_path / "github_env"
    github_env.touch()
    full = {
        "SECRET_NAME": "",
        "SOURCE": "",
        "COPY_FROM": "",
        "LENGTH": "48",
        "GITHUB_ENV": str(github_env),
        "PATH": "/usr/bin:/bin",
    }
    full.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        ["bash", "-c", _step(VALIDATE_STEP)["run"]],
        env=full,
        capture_output=True,
        text=True,
    )


def ok(tmp_path, **env) -> None:
    r = run_guard(tmp_path, **env)
    assert r.returncode == 0, f"expected ALLOWED, got rc={r.returncode}: {r.stdout}{r.stderr}"


def blocked(tmp_path, **env) -> None:
    r = run_guard(tmp_path, **env)
    assert r.returncode != 0, f"expected BLOCKED, but it was allowed: {r.stdout}{r.stderr}"


# --------------------------------------------------------------------------
# (1) No caller may transport a value. Structural, not filtered.
# --------------------------------------------------------------------------

# Substrings that would indicate an input carrying plaintext. `secret_name` is
# legitimate, so match on the value-bearing words only.
VALUE_BEARING = ("value", "plaintext", "password", "passwd", "token", "credential")


def test_no_input_transports_a_value():
    """The workflow must declare no input that could carry a secret value.

    Job logs here are public; an input holding plaintext publishes it. Keep the
    three value-free sources (verify/generate/copy) instead.
    """
    inputs = _on()["workflow_dispatch"]["inputs"]
    for name in inputs:
        low = name.lower()
        assert not any(w in low for w in VALUE_BEARING), (
            f"input {name!r} looks like it transports a secret value. This repo's "
            f"job logs are public — use source=generate (mint in-runner), "
            f"source=copy (re-key an existing secret), or seal it offline with "
            f"scripts/seal-secret.sh instead."
        )


def test_declared_sources_are_exactly_the_value_free_three():
    opts = _on()["workflow_dispatch"]["inputs"]["source"]["options"]
    assert sorted(opts) == ["copy", "generate", "verify"], (
        "the source enum is the list of ways a value can originate WITHOUT a "
        f"caller transporting it; got {opts}"
    )


def test_provision_step_never_prints_a_value():
    """No line sends the value file to stdout except the ::add-mask:: call.

    Writing INTO the file (`> "$d/val"`) and feeding it to a command's stdin
    (`< "$d/val"`) are both safe and expected; only a read that lands in the log
    is a disclosure. ::add-mask:: is the one permitted read — it registers the
    value with the runner so any later accidental echo is redacted.
    """
    for raw in _step(PROVISION_STEP)["run"].splitlines():
        line = raw.strip()
        if "$d/val" not in line:
            continue
        if '> "$d/val"' in line or '< "$d/val"' in line:
            continue  # write / stdin redirect — nothing reaches the log
        assert "::add-mask::" in line, f"line may print a secret value: {line!r}"


# --------------------------------------------------------------------------
# (2) Protected credentials are unwritable, as target and as copy source.
# --------------------------------------------------------------------------

PROTECTED_SAMPLES = [
    "SECRETS_ADMIN_PAT",  # the workflow's own credential — lockout / identity swap
    "KUBE_CONFIG",  # rewriting it breaks cluster-query
    "FUZEINFRA_DISPATCH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "FUZE_AGENT_APP_PRIVATE_KEY",
]


@pytest.mark.parametrize("name", PROTECTED_SAMPLES)
def test_protected_names_cannot_be_written(tmp_path, name):
    blocked(tmp_path, SECRET_NAME=name, SOURCE="generate")


@pytest.mark.parametrize("name", PROTECTED_SAMPLES)
def test_protected_names_cannot_be_the_copy_source(tmp_path, name):
    """A copy prints nothing, but it still re-keys a credential to a new name."""
    blocked(tmp_path, SECRET_NAME="HARMLESS_TARGET", SOURCE="copy", COPY_FROM=name)


def test_admin_pat_is_protected_in_the_workflow_text():
    """The credential named in the Provision step must appear in PROTECTED.

    If the PAT secret is ever renamed, renaming it in `Provision` alone would
    quietly make it writable by its own workflow.
    """
    provision = _step(PROVISION_STEP)
    token_expr = provision["env"]["GH_TOKEN"]
    pat_name = token_expr.split("secrets.")[1].split("}")[0].strip()
    assert pat_name in _step(VALIDATE_STEP)["run"], (
        f"{pat_name} is the workflow's own credential but is not in PROTECTED"
    )


# --------------------------------------------------------------------------
# Name validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "",  # required
        "   ",  # whitespace-only
        "lowercase_name",
        "Mixed_Case",
        "1LEADING_DIGIT",
        "_LEADING_UNDERSCORE",
        "HAS-DASH",
        "HAS SPACE",
        "HAS$METACHAR",
        "AB",  # shorter than 3
        "A" * 65,  # longer than 64
        "GITHUB_TOKEN",  # reserved prefix
        "GITHUB_ANYTHING",
    ],
)
def test_invalid_names_blocked(tmp_path, name):
    blocked(tmp_path, SECRET_NAME=name, SOURCE="generate")


@pytest.mark.parametrize("name", ["TWILIO_AUTH_TOKEN_REF", "ABC", "A_1", "WEBHOOK_SHARED_SECRET"])
def test_valid_names_allowed(tmp_path, name):
    ok(tmp_path, SECRET_NAME=name, SOURCE="verify")


# --------------------------------------------------------------------------
# source / copy_from / length
# --------------------------------------------------------------------------


@pytest.mark.parametrize("src", ["", "delete", "read", "GENERATE", "generate; rm -rf /"])
def test_invalid_source_blocked(tmp_path, src):
    blocked(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE=src)


def test_copy_requires_a_source(tmp_path):
    blocked(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE="copy", COPY_FROM="")


def test_copy_from_must_be_a_valid_name(tmp_path):
    blocked(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE="copy", COPY_FROM="not-a-name")


def test_copy_to_itself_blocked(tmp_path):
    """A self-copy is always a mistake, and would rewrite a secret with itself."""
    blocked(tmp_path, SECRET_NAME="SAME_NAME", SOURCE="copy", COPY_FROM="SAME_NAME")


def test_copy_happy_path(tmp_path):
    ok(tmp_path, SECRET_NAME="NEW_NAME", SOURCE="copy", COPY_FROM="OLD_NAME")


@pytest.mark.parametrize("length", ["0", "15", "129", "abc", "48x", "-20"])
def test_invalid_length_blocked(tmp_path, length):
    blocked(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE="generate", LENGTH=length)


def test_omitted_length_falls_back_to_the_default(tmp_path):
    """A repository_dispatch caller may omit length; empty means "use 48"."""
    ok(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE="generate", LENGTH="")


@pytest.mark.parametrize("length", ["16", "48", "128"])
def test_valid_length_allowed(tmp_path, length):
    ok(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE="generate", LENGTH=length)


def test_length_is_not_checked_for_non_generate(tmp_path):
    """verify/copy ignore length — a stale default must not block them."""
    ok(tmp_path, SECRET_NAME="SOME_SECRET", SOURCE="verify", LENGTH="not-a-number")


# --------------------------------------------------------------------------
# Both event shapes reach the same filter
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "var,payload_key",
    [
        ("SECRET_NAME", "secret_name"),
        ("SOURCE", "source"),
        ("COPY_FROM", "copy_from"),
        ("LENGTH", "length"),
    ],
)
def test_both_event_shapes_feed_the_filter(var, payload_key):
    """Drop either half of a fallback and that event runs with an empty arg.

    For repository_dispatch that is a silent no-op shaped like success; the same
    pin exists in tests/test_cluster_query_guard.py for the same reason.
    """
    expr = _step(VALIDATE_STEP)["env"][var]
    assert f"inputs.{payload_key}" in expr, f"{var} ignores workflow_dispatch"
    assert f"client_payload.{payload_key}" in expr, f"{var} ignores repository_dispatch"


def test_repository_dispatch_type_is_registered():
    types = _on()["repository_dispatch"]["types"]
    assert "secret-provision" in types


def test_workflow_token_permissions_are_minimal():
    """The job needs no write scope of its own — the PAT carries the privilege."""
    assert _workflow()["permissions"] == {"contents": "read"}
