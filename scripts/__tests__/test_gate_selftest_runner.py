"""The gate-identifier self-test step must be able to RUN what lives beside it.

`scripts/__tests__/` is the self-test directory for every distributed script, and
harden-gate.yml's "the gate must be known to FIRE" step runs it with a deliberately
broad `test_*.py` glob so no suite is silently skipped. The glob is only half the
guarantee: the RUNNER has to be able to execute what the glob finds.

`python -m unittest discover` cannot execute a pytest-style test. Two distinct
failures follow, and this repo hit both at once when
`scripts/__tests__/test_runner_health.py` arrived using `@pytest.mark.parametrize`:

  * a module that does `import pytest` raises ModuleNotFoundError under a runner
    that never installed it, which takes the ENTIRE discovery down -- the gate went
    red fleet-wide with the cause buried under 400 lines of passing output; and
  * worse, had pytest merely been installed without switching the runner, the import
    would have resolved and unittest would have reported OK while never executing the
    parametrized functions. Measured on this tree: unittest 416 tests, pytest 435 + 2
    subtests. A green gate running 19 fewer tests than it appears to is the exact
    "vacuous gate" harden-gate.yml's own comment says the broad glob exists to prevent.

So: if any suite here uses pytest, the gate must install pytest AND run pytest.
pytest runs `unittest.TestCase` classes natively, so the swap loses nothing.

Written as a unittest.TestCase ON PURPOSE, using no pytest-only construct. A change
that reverts the runner to `unittest discover` must not be able to disable the test
that objects to it -- this one keeps running, and failing, under either runner.

Offline: reads two files. No cluster, no network, no subprocess.
"""

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/harden-gate.yml"
SELFTEST_DIR = ROOT / "scripts/__tests__"
STEP_MARKER = "must be known to FIRE"


def _selftest_step_script():
    """The run: body of gate-identifier's self-test step."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in doc["jobs"]["gate-identifier"]["steps"]:
        if STEP_MARKER in (step.get("name") or ""):
            return step["run"]
    raise AssertionError(
        f"no gate-identifier step whose name contains {STEP_MARKER!r} in {WORKFLOW}"
    )


def _selftest_step_commands():
    """The step's executable lines, with comments and blanks stripped.

    The assertions below are about what the step RUNS. Matching the raw body would
    also match the prose: this file's own first draft failed because the comment
    explaining why `unittest discover` is wrong contains that exact string.
    """
    return "\n".join(
        line
        for line in _selftest_step_script().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _modules_importing_pytest():
    return sorted(
        p.name
        for p in SELFTEST_DIR.glob("test_*.py")
        if re.search(r"^\s*import pytest\b", p.read_text(encoding="utf-8"), re.M)
    )


class GateSelfTestRunnerTest(unittest.TestCase):
    def test_runner_can_execute_every_suite_present(self):
        """If any suite here needs pytest, the gate must install AND use pytest."""
        needs_pytest = _modules_importing_pytest()
        if not needs_pytest:
            self.skipTest("no suite here imports pytest; unittest discovery suffices")

        script = _selftest_step_commands()

        self.assertRegex(
            script,
            r"pip install[^\n]*\bpytest\b",
            f"{needs_pytest} import pytest, but the self-test step does not install it.",
        )
        self.assertRegex(
            script,
            r"python -m pytest\b",
            f"{needs_pytest} use pytest-only constructs, so the step must RUN pytest. "
            f"Installing it while still invoking `unittest discover` is worse than the "
            f"red it replaces: the import resolves, the run reports OK, and the "
            f"parametrized cases never execute.",
        )

    def test_unittest_discovery_is_not_the_runner_while_pytest_suites_exist(self):
        """The swap must be a swap, not an addition that leaves the weaker runner in."""
        if not _modules_importing_pytest():
            self.skipTest("no pytest suites here")
        self.assertNotRegex(
            _selftest_step_commands(),
            r"python -m unittest discover",
            "the self-test step still invokes `unittest discover`, which cannot run the "
            "pytest suites in this directory",
        )


if __name__ == "__main__":
    unittest.main()
