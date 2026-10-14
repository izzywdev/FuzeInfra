"""The provisioner's inline image gate must agree with the script it guards.

THE DEFECT THIS PINS. `provision-registry-bot-ghcr-pull.yml` validates its `verify_image`
input twice: once in bash, in the workflow's own "Validate request and credential shape"
step, and once in `scripts/verify_ghcr_pull.py`'s `parse_image`. The two grammars drifted.

`parse_image` has always accepted NESTED GHCR package paths:

    r"ghcr\\.io/(izzywdev/[a-z0-9._-]+(?:/[a-z0-9._-]+)*):(...)"

The workflow's gate allowed only a single path segment, so every nested image was rejected
before the probe could run. That is not hypothetical: MendysRobotics publishes
`ghcr.io/izzywdev/mendysrobotics/<service>`, not a flat `ghcr.io/izzywdev/<repo>`, so
`mendys-prod` could not be provisioned through this workflow AT ALL -- while the namespace
sat with no GHCR credential and ~20 workloads in ImagePullBackOff for 17 days
(FuzeInfra#1415, MendysRobotics#903). Both validators were individually defensible; the
stricter one silently won, and the error message blamed the image rather than the gate.

So the interesting property is not "the regex is correct" -- it is that the two layers
accept the SAME set. A test on either one alone would have passed throughout. The
agreement test below is the one that matters; the accept/reject cases exist so that a
future widening of BOTH layers in the same wrong direction still fails.

Offline: reads one YAML file and runs bash. No network, no cluster, no credentials.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="executes the real Linux CI bash gate",
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/provision-registry-bot-ghcr-pull.yml"
SCRIPT = ROOT / "scripts/verify_ghcr_pull.py"
STEP_NAME = "Validate request and credential shape"


def _gate_regex() -> str:
    """The bash ERE literal out of the workflow, so the test cannot drift from it."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["provision"]["steps"]
    run = next(s["run"] for s in steps if s.get("name") == STEP_NAME)
    m = re.search(r'\[\[ "\$IMAGE" =~ (\S+) \]\]', run)
    # GUARD THE GUARD: a missed extraction would make every case below vacuously pass,
    # which is the same failure mode that let the original drift ship.
    assert m, f"could not find the IMAGE gate in step {STEP_NAME!r}"
    return m.group(1)


def _gate_accepts(image: str) -> bool:
    proc = subprocess.run(
        ["bash", "-c", f'[[ "$1" =~ {_gate_regex()} ]]', "_", image],
        capture_output=True,
        check=False,
    )
    assert proc.returncode in (0, 1), f"gate errored on {image!r}: {proc.stderr!r}"
    return proc.returncode == 0


def _parse_image():
    spec = importlib.util.spec_from_file_location("verify_ghcr_pull", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_image, mod.ProbeError


def _script_accepts(image: str) -> bool:
    parse_image, ProbeError = _parse_image()
    try:
        parse_image(image)
        return True
    except ProbeError:
        return False


ACCEPT = [
    "ghcr.io/izzywdev/fuzeplan:1.2.3",
    # The case the drift blocked. Keep it first among the nested ones.
    "ghcr.io/izzywdev/mendysrobotics/backend:1.11.28",
    "ghcr.io/izzywdev/mendysrobotics/consumer-crawler:2.0.0",
    "ghcr.io/izzywdev/a/b/c:v1",
    "ghcr.io/izzywdev/fuze-runner:sha-abc123",
    # OCI allows a leading underscore and underscores inside a tag; the gate
    # used to forbid both while parse_image allowed them.
    "ghcr.io/izzywdev/x:_leading_underscore",
    "ghcr.io/izzywdev/x:1_2_3",
]

REJECT = [
    # Another org: the whole point of anchoring the gate.
    "ghcr.io/someoneelse/x:1",
    "ghcr.io/izzywdevil/x:1",
    # Not an exact tag.
    "ghcr.io/izzywdev/x",
    "ghcr.io/izzywdev/x:",
    # Digest-pinned and floating forms this credential must not be aimed at.
    "ghcr.io/izzywdev/x:1@sha256:abc",
    # Malformed paths.
    "ghcr.io/izzywdev//x:1",
    "ghcr.io/izzywdev/x/:1",
    "ghcr.io/izzywdev/-x:1",
    # A different registry wearing the right suffix.
    "evil.example.com/ghcr.io/izzywdev/x:1",
    # Trailing whitespace, which a copy-paste from a log carries.
    "ghcr.io/izzywdev/x:latest ",
]


@pytest.mark.parametrize("image", ACCEPT)
def test_gate_accepts_real_images(image):
    assert _gate_accepts(image), f"gate rejected a legitimate image: {image}"


@pytest.mark.parametrize("image", REJECT)
def test_gate_rejects_bad_images(image):
    assert not _gate_accepts(image), f"gate accepted something it must not: {image}"


@pytest.mark.parametrize("image", ACCEPT + REJECT)
def test_the_two_validators_agree(image):
    """The actual invariant. Either layer alone passing is what shipped the bug.

    If this fails, do not relax one side to match -- work out which grammar is right and
    change both, because whichever is stricter silently decides what can be provisioned.
    """
    assert _gate_accepts(image) == _script_accepts(image), (
        f"workflow gate and verify_ghcr_pull.parse_image disagree on {image!r}: "
        f"gate={_gate_accepts(image)} script={_script_accepts(image)}"
    )
