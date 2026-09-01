"""Executable invariants for scripts/runner_watch.py and .github/workflows/runner-watch.yml.

WHAT THIS PROTECTS

`runs-on` is fixed at job dispatch. A job pointed at a scale set that is down does not
fail, it QUEUES FOREVER, and a required context that never reports blocks merges with no
red to look at. The watcher exists to make that decision BEFORE dispatch and to rescue the
runs a previous wrong decision already stranded, so the properties that matter are not
"the file parses" but:

  * given these facts, which label comes out — and, critically, WHEN the decision is
    "none, keep what you have" rather than a value;
  * that a newly-computed value cannot be written on a single tick (hysteresis), because
    unlike the closed PR #249 this design PERSISTS its decision and therefore CAN
    oscillate unless something stops it;
  * that the recovery selector can never cancel real work and can never cancel the same
    run twice.

The third is the one with teeth. `stranded_runs` drives `gh run cancel`, so a defect there
does not produce a wrong label, it DESTROYS a running build. Every safety property in its
docstring has a test here, named for the property rather than for the function.

Offline: pure functions over plain data. No network, no token, no cluster.
"""

import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import runner_watch as rw  # noqa: E402

WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "runner-watch.yml")

NOW = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def facts(**kw):
    base = dict(slug="izzywdev/Example", private=True, declared_pool="fuze-runner",
                capability="", online_runners=None, pinned=False)
    base.update(kw)
    return rw.RepoFacts(**base)


def job(run_id, status="queued", minutes_queued=60, runner_name=None, runner_id=None,
        job_id=1, name="gate-test"):
    return rw.JobFacts(
        run_id=run_id, job_id=job_id, name=name, status=status,
        queued_at=NOW - dt.timedelta(minutes=minutes_queued),
        runner_name=runner_name, runner_id=runner_id,
        html_url=f"https://github.com/izzywdev/Example/actions/runs/{run_id}",
    )


# ======================================================================================
# Job A — the decision. #249's precedence order, minus budget.
# ======================================================================================

class DecideRunner(unittest.TestCase):

    def test_public_repo_never_consumes_self_hosted_capacity(self):
        """Actions is free and unmetered on public repos; the pool is scarce.

        Note it does not even reach the liveness probe: a live pool is not a reason to
        spend a shared 4-vCPU node on work GitHub will do for nothing."""
        d = rw.decide_runner(facts(private=False, online_runners=5))
        self.assertEqual(d.labels, "ubuntu-latest")
        self.assertIn("public repo", d.reason)

    def test_no_declared_pool_falls_to_the_documented_hosted_default(self):
        """ci-runners.md: absence of `ci.runner` is a legitimate answer, not an oversight.
        Inventing a pool name here would be the queue-forever hazard, self-inflicted."""
        d = rw.decide_runner(facts(declared_pool="", online_runners=3))
        self.assertEqual(d.labels, "ubuntu-latest")
        self.assertIn("no self-hosted runner declared", d.reason)

    def test_zero_online_refuses_to_queue_onto_a_down_pool(self):
        """THE point of the whole watcher.

        A budget-exhausted hosted runner is recoverable by a human with a card in minutes;
        a down cluster is repaired BY workflows, so routing CI onto it while it is down
        removes the means of fixing it."""
        d = rw.decide_runner(facts(online_runners=0))
        self.assertEqual(d.labels, "ubuntu-latest")
        self.assertIn("0 runners online", d.reason)
        self.assertTrue(d.liveness_verified)

    def test_live_pool_on_a_private_repo_is_used(self):
        """Conserves metered hosted minutes, per ci-runners.md — but only once a runner has
        actually been OBSERVED online. Pods Running is not proof a runner registered."""
        d = rw.decide_runner(facts(online_runners=3))
        self.assertEqual(d.labels, "fuze-runner")
        self.assertTrue(d.liveness_verified)

    def test_probe_failure_does_not_invent_an_answer(self):
        """`online_runners is None` means UNVERIFIED, which is not the same as zero.

        This is where the precomputed form deliberately diverges from #249: #249 chose
        hosted on a failed probe because its answer was consumed by exactly one run. Here
        the answer PERSISTS, so choosing hosted from one failed probe would migrate the
        whole fleet off its runners on the strength of an API blip. `labels is None` means
        keep the current value — the only answer that invents nothing."""
        d = rw.decide_runner(facts(online_runners=None))
        self.assertIsNone(d.labels)
        self.assertFalse(d.liveness_verified)
        self.assertTrue(d.warning, "an unverifiable probe must warn LOUDLY, not silently")
        self.assertIn("UNVERIFIED", d.reason)

    def test_probe_failure_is_distinguishable_from_zero_online(self):
        """The two failure shapes must not collapse into each other. If they ever did, an
        unreachable API would read as 'the pool is down' and migrate everybody."""
        unverified = rw.decide_runner(facts(online_runners=None))
        down = rw.decide_runner(facts(online_runners=0))
        self.assertIsNone(unverified.labels)
        self.assertEqual(down.labels, "ubuntu-latest")

    def test_cluster_capability_pins_self_hosted_even_on_a_public_repo(self):
        """A hosted runner cannot reach the cluster at all, so cost cannot enter into it
        (ci-runners.md §1). Capability beats visibility and beats liveness."""
        d = rw.decide_runner(facts(private=False, capability="cluster", online_runners=0))
        self.assertEqual(d.labels, "fuze-runner")
        self.assertIn("capability", d.reason)

    def test_cluster_capability_without_a_pool_refuses_rather_than_guessing(self):
        """Naming a pool that does not exist queues forever. So does guessing one."""
        d = rw.decide_runner(facts(capability="cluster", declared_pool=""))
        self.assertIsNone(d.labels)
        self.assertTrue(d.warning)

    def test_operator_pin_stops_the_watcher_writing(self):
        """The override lever from #249 (`vars.CI_RUNNER_LABELS`) could not survive the
        shape change — that variable is now the watcher's OWN output, so honouring it as an
        override would make the watcher defer to its own last write forever. The lever
        moved to governance/runner-watch.json, which is a separate sink."""
        d = rw.decide_runner(facts(pinned=True, online_runners=0))
        self.assertIsNone(d.labels)
        self.assertIn("pinned", d.reason)


# ======================================================================================
# Hysteresis — the anti-flap guarantee the persisted form has to buy back
# ======================================================================================

class Hysteresis(unittest.TestCase):

    def test_first_divergent_observation_does_not_write(self):
        """A one-tick blip must never move the fleet."""
        r = rw.hysteresis(current="fuze-runner", computed="ubuntu-latest", prior=None)
        self.assertFalse(r.write)
        self.assertEqual(r.observation, rw.Observation("ubuntu-latest", 1))

    def test_second_consecutive_identical_observation_writes(self):
        """N=2: the same new value, twice running, is a signal rather than a blip."""
        prior = rw.Observation("ubuntu-latest", 1)
        r = rw.hysteresis(current="fuze-runner", computed="ubuntu-latest", prior=prior)
        self.assertTrue(r.write)
        self.assertEqual(r.observation.count, 2)

    def test_a_flapping_pool_never_reaches_the_threshold(self):
        """Simulate the exact oscillation #249 avoided by construction: the probe reports
        down, then up, then down. Every tick diverges from the live value, but never the
        SAME candidate twice in a row, so the counter restarts and nothing is written."""
        prior = None
        writes = 0
        for computed in ("ubuntu-latest", "fuze-runner", "ubuntu-latest", "fuze-runner"):
            # `current` stays "fuze-runner": nothing was written, so nothing changed.
            r = rw.hysteresis(current="fuze-runner", computed=computed, prior=prior)
            writes += int(r.write)
            prior = r.observation
        self.assertEqual(writes, 0, "a flapping pool moved the fleet — the counter reset "
                                    "must be keyed on the candidate VALUE, not just on "
                                    "divergence from current")

    def test_agreement_clears_the_counter(self):
        """Once the live value matches, the divergence being counted is over. Leaving a
        stale counter behind would let an unrelated future divergence write on its FIRST
        tick, silently defeating the threshold."""
        prior = rw.Observation("ubuntu-latest", 1)
        r = rw.hysteresis(current="ubuntu-latest", computed="ubuntu-latest", prior=prior)
        self.assertFalse(r.write)
        self.assertIsNone(r.observation)

    def test_a_declined_decision_preserves_the_counter_rather_than_resetting_it(self):
        """`computed is None` is 'could not verify', not 'no divergence'. Resetting on it
        would let one unreachable tick in the middle of a legitimate migration throw away
        the progress toward it, so a flaky API could stall a real migration forever."""
        prior = rw.Observation("ubuntu-latest", 1)
        r = rw.hysteresis(current="fuze-runner", computed=None, prior=prior)
        self.assertFalse(r.write)
        self.assertEqual(r.observation, prior)

    def test_a_repo_with_no_variable_yet_still_serves_the_threshold(self):
        """First-ever write is still a write, and still costs two consecutive observations.
        A brand-new repo is exactly when the probe is least trustworthy."""
        r1 = rw.hysteresis(current=None, computed="fuze-runner", prior=None)
        self.assertFalse(r1.write)
        r2 = rw.hysteresis(current=None, computed="fuze-runner", prior=r1.observation)
        self.assertTrue(r2.write)

    def test_threshold_is_configurable_and_must_be_at_least_one(self):
        r = rw.hysteresis(current="a", computed="b", prior=None, threshold=1)
        self.assertTrue(r.write)
        with self.assertRaises(ValueError):
            rw.hysteresis(current="a", computed="b", prior=None, threshold=0)


# ======================================================================================
# Job B — the stranded-run selector. This one can destroy work, so it gets the most tests.
# ======================================================================================

class StrandedRuns(unittest.TestCase):

    def test_selects_a_job_queued_past_the_threshold_with_no_runner(self):
        self.assertEqual(rw.stranded_runs([job(1)], now=NOW), [1])

    def test_NEVER_selects_a_run_with_an_in_progress_job(self):
        """The destructive-defect test. Cancellation is per-RUN, so cancelling to rescue a
        queued job would discard the real work its sibling is doing. The whole run is
        excluded — strictly stronger than 'do not select an in_progress job', and the
        honest reading of the API's granularity."""
        jobs = [job(7, status="queued", job_id=1),
                job(7, status="in_progress", runner_name="fuze-runner-abc", job_id=2)]
        self.assertEqual(rw.stranded_runs(jobs, now=NOW), [])

    def test_never_selects_an_in_progress_job_in_any_ordering(self):
        """Order-independence matters: the in_progress job may be listed AFTER the queued
        one, and an implementation that decided as it iterated would miss it."""
        for jobs in ([job(7, status="in_progress", job_id=2), job(7, job_id=1)],
                     [job(7, job_id=1), job(7, status="in_progress", job_id=2)]):
            self.assertEqual(rw.stranded_runs(jobs, now=NOW), [], f"ordering {jobs}")

    def test_a_job_with_a_runner_assigned_is_being_served_not_stranded(self):
        jobs = [job(3, runner_name="fuze-runner-xyz"), job(4, runner_id=42)]
        self.assertEqual(rw.stranded_runs(jobs, now=NOW), [])

    def test_runner_id_zero_means_UNASSIGNED_not_assigned(self):
        """GitHub reports an unassigned job as `runner_id: 0` with an empty runner_name.
        Treating 0 as truthy assignment would make every stranded job look served and the
        entire sweep a silent no-op."""
        self.assertEqual(rw.stranded_runs([job(5, runner_id=0, runner_name="")],
                                          now=NOW), [5])

    def test_below_the_threshold_is_a_busy_pool_not_a_stranding(self):
        self.assertEqual(rw.stranded_runs([job(9, minutes_queued=5)], now=NOW), [])
        self.assertEqual(rw.stranded_runs([job(9, minutes_queued=25)], now=NOW), [9])

    def test_threshold_is_configurable(self):
        self.assertEqual(rw.stranded_runs([job(9, minutes_queued=25)], now=NOW,
                                          threshold_minutes=60), [])

    def test_unknown_queue_age_is_never_acted_on(self):
        """Same 'never guess on unknown state' discipline prune_merged_branches.py holds
        for `ahead_by is None`. An unparseable timestamp must not become 'old enough'."""
        j = rw.JobFacts(run_id=11, job_id=1, name="x", status="queued", queued_at=None)
        self.assertEqual(rw.stranded_runs([j], now=NOW), [])

    def test_a_completed_job_is_not_a_candidate(self):
        self.assertEqual(rw.stranded_runs([job(12, status="completed")], now=NOW), [])

    def test_NEVER_returns_a_run_id_twice_in_one_sweep(self):
        """The hard rerun cap, first half: three stranded jobs in one run is ONE cancel."""
        jobs = [job(21, job_id=1), job(21, job_id=2), job(21, job_id=3)]
        self.assertEqual(rw.stranded_runs(jobs, now=NOW), [21])

    def test_NEVER_returns_a_run_id_already_recovered(self):
        """The hard rerun cap, second half. Without it, a run that is broken for a reason
        recovery cannot fix — a bad workflow file, a missing secret — is cancelled,
        re-dispatched, queues again, and is cancelled again, forever."""
        self.assertEqual(rw.stranded_runs([job(21)], now=NOW,
                                          already_recovered={21}), [])

    def test_recovering_one_run_does_not_shield_its_neighbours(self):
        jobs = [job(21), job(22)]
        self.assertEqual(rw.stranded_runs(jobs, now=NOW, already_recovered={21}), [22])

    def test_result_is_sorted_so_the_audit_log_is_deterministic(self):
        self.assertEqual(rw.stranded_runs([job(30), job(10), job(20)], now=NOW),
                         [10, 20, 30])


# ======================================================================================
# Durable state — the thing #249 deliberately did not have
# ======================================================================================

class State(unittest.TestCase):

    def test_hysteresis_state_round_trips(self):
        obs = {"izzywdev/FuzeHub": rw.Observation("ubuntu-latest", 1)}
        self.assertEqual(rw.load_state(rw.dump_state(obs)), obs)

    def test_corrupt_or_absent_state_degrades_to_empty_rather_than_raising(self):
        """A corrupt variable must cost one extra tick before any write, not take the
        watcher down — a crashed watcher writes nothing AND recovers nothing."""
        for raw in (None, "", "not json", "[]", '{"observations": "nope"}'):
            self.assertEqual(rw.load_state(raw), {}, repr(raw))

    def test_recovered_ledger_round_trips_and_keys_stay_ints(self):
        led = {12345: "2026-09-01T12:00:00Z"}
        back = rw.load_recovered(rw.dump_recovered(led), now=NOW)
        self.assertEqual(back, led)

    def test_recovered_ledger_expires_by_AGE_only(self):
        """Pruned by age, never trimmed to size: a ledger trimmed for space would silently
        un-cap the rerun for whichever run it dropped, re-arming the cancel loop."""
        fresh = NOW - dt.timedelta(days=1)
        stale = NOW - dt.timedelta(days=rw.RECOVERED_TTL_DAYS + 1)
        raw = rw.dump_recovered({
            1: fresh.isoformat().replace("+00:00", "Z"),
            2: stale.isoformat().replace("+00:00", "Z"),
        })
        self.assertEqual(sorted(rw.load_recovered(raw, now=NOW)), [1])

    def test_parse_ts_returns_None_rather_than_guessing(self):
        self.assertIsNone(rw.parse_ts("not a timestamp"))
        self.assertIsNone(rw.parse_ts(None))
        self.assertEqual(rw.parse_ts("2026-09-01T12:00:00Z"), NOW)


class FleetAndPolicy(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = d.name

    def test_targets_come_from_the_one_fleet_list_everything_else_uses(self):
        """A second target list is a second thing to forget. ruleset_sync,
        repo_settings_sync and prune_merged_branches all read governance/ruleset-fleet.json
        in FuzeSDLC; so does this — which is why the file is NOT vendored into this repo
        (runner_watch.py, "WHERE THIS RUNS", point 2) and is fetched at run time instead.

        The reduction is therefore asserted against a fixture rather than against a
        committed file: what has to hold is that `owner` is joined on and that `excluded`
        is honoured. Whether the real list contains any particular repo is FuzeSDLC's
        assertion to make, and this test must not pretend to make it from here."""
        fixture = os.path.join(self.tmp, "ruleset-fleet.json")
        with open(fixture, "w", encoding="utf-8") as fh:
            json.dump({
                "owner": "izzywdev",
                "repos": ["FuzeHub", "FuzeFront", "FuzeInfra", "FuzeSDLC"],
                "excluded": {"FuzeInfra": "infra repo", "FuzeSDLC": "the hub itself"},
            }, fh)
        repos = rw.fleet_repos(fixture)
        self.assertIn("izzywdev/FuzeHub", repos)
        self.assertNotIn("izzywdev/FuzeInfra", repos, "`excluded` must be honoured")
        self.assertNotIn("izzywdev/FuzeSDLC", repos)

    def test_an_absent_fleet_file_raises_rather_than_sweeping_nothing(self):
        """The default FLEET_FILE path does NOT exist in a FuzeInfra checkout, by design.
        It must blow up, not resolve to an empty fleet: a watcher that sweeps zero repos
        and exits 0 is indistinguishable from a healthy fleet."""
        with self.assertRaises(FileNotFoundError):
            rw.fleet_repos(os.path.join(self.tmp, "definitely-not-here.json"))

    def test_policy_defaults_are_the_safe_end_of_every_lever(self):
        p = rw.load_policy(os.path.join(REPO_ROOT, "governance", "does-not-exist.json"))
        self.assertEqual(p.hysteresis, 2)
        self.assertEqual(p.stranded_minutes, 20)
        self.assertEqual(p.pinned, {})

    def test_the_shipped_policy_file_loads(self):
        p = rw.load_policy()
        self.assertGreaterEqual(p.hysteresis, 2,
                                "a threshold below 2 disables the anti-flap guarantee")
        self.assertGreaterEqual(p.stranded_minutes, 1)


# ======================================================================================
# Shape — properties of the workflow itself, however the script is edited
# ======================================================================================

class WorkflowShape(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("pyyaml not available")
        with open(WORKFLOW, encoding="utf-8") as fh:
            cls.wf = yaml.safe_load(fh)

    def test_the_chooser_itself_is_always_github_hosted(self):
        """A chooser running on the pool it evaluates cannot report that pool down.

        Same reasoning that keeps arc-register.yml hosted (render.RUNNER_EXEMPT): the
        bootstrap step must not depend on the thing it bootstraps. Here it is sharper than
        in #249 — this watcher is ALSO the recovery path, so a watcher stranded on a dead
        pool takes the only mechanism that could unstrand the fleet down with it."""
        for name, jobdef in self.wf["jobs"].items():
            runs_on = jobdef["runs-on"]
            self.assertEqual(runs_on, "ubuntu-latest", f"job {name}")
            self.assertNotIn("${{", str(runs_on),
                             f"job {name}: the watcher's own runs-on must be a literal")

    def test_every_job_has_a_timeout(self):
        for name, jobdef in self.wf["jobs"].items():
            self.assertIn("timeout-minutes", jobdef, f"job {name}")

    def test_it_is_cron_driven_and_hand_dispatchable(self):
        triggers = self.wf.get("on", self.wf.get(True))
        self.assertIsNotNone(triggers, "runner-watch.yml has no trigger block")
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)

    def test_the_schedule_is_read_only_and_apply_is_an_explicit_choice(self):
        """Same safety shape as prune-merged-branches.yml: the destructive mode is never
        reached by a timer alone."""
        with open(WORKFLOW, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("workflow_dispatch", body)
        self.assertIn("--check", body)
        self.assertIn("--apply", body)

    def test_it_names_the_app_token_secrets_not_the_default_GITHUB_TOKEN(self):
        """The runners listing needs `administration: read`, which GITHUB_TOKEN does NOT
        carry. A watcher running on GITHUB_TOKEN would fail every probe, take the
        keep-current branch on every repo, and report a clean-looking no-op forever."""
        with open(WORKFLOW, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("FUZE_AGENT_APP_ID", body)
        self.assertIn("FUZE_AGENT_APP_PRIVATE_KEY", body)

    def test_recovery_runs_after_the_decision_in_the_same_workflow(self):
        """Job B re-dispatches so a run picks up the CURRENT variable. Re-dispatching
        before Job A has published it would re-strand the run on the old value."""
        self.assertIn("decide", self.wf["jobs"]["recover"].get("needs", []))


# ======================================================================================
# C3 conformance — the consumption pattern must satisfy the gate it is meant to unblock
# ======================================================================================

class ConsumptionPatternSatisfiesC3(unittest.TestCase):
    """`runs-on: ${{ vars.CI_RUNNER_LABELS || 'ubuntu-latest' }}` is the whole point of
    precomputing. If gate_required_checks.py's C3 rejected it, every required check in
    every private repo would still be unsatisfiable and this watcher would buy nothing.

    Asserted against the REAL gate function rather than by reading §2.2, because the
    document and the code are two artefacts and only one of them blocks a merge."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(SCRIPTS, "gate_required_checks.py")
        spec = importlib.util.spec_from_file_location("gate_required_checks", path)
        cls.grc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.grc)

    def test_the_precomputed_variable_form_is_fit(self):
        finding = self.grc.runner_finding(
            "gate-test", "${{ vars.CI_RUNNER_LABELS || 'ubuntu-latest' }}",
            contexts={}, allowed=set(), code="C3 no_self_hosted_required")
        self.assertIsNone(finding, f"C3 rejected the consumption pattern: {finding}")

    def test_a_hardcoded_self_hosted_label_is_still_rejected(self):
        """C3 is NOT weakened by sanctioning the expression form. The thing it forbids —
        a bare self-hosted label with no hosted default — must still fail."""
        finding = self.grc.runner_finding(
            "gate-test", "fuze-runner",
            contexts={}, allowed={"fuze-runner"}, code="C3 no_self_hosted_required")
        self.assertIsNotNone(finding)
        self.assertIn("QUEUES FOREVER", finding)

    def test_an_expression_with_no_hosted_default_is_the_authors_responsibility(self):
        """Documented limit, asserted so nobody mistakes it for a guarantee: the gate does
        not EVALUATE the expression, so `${{ vars.X }}` with no `|| 'ubuntu-latest'` passes
        C3 while still being able to queue forever. That is why the fleet migration (a
        separate PR) must use the literal-defaulted form, and why this watcher never writes
        an empty value."""
        self.assertIsNone(self.grc.runner_finding(
            "gate-test", "${{ vars.CI_RUNNER_LABELS }}",
            contexts={}, allowed=set(), code="C3"))


# ======================================================================================
# The move - properties that only hold because the INSTANCE runs in FuzeInfra
# ======================================================================================

def _hub_default():
    """The `--hub` default lives in main()'s parser, which is only built at call time.

    Read it by intercepting parse_args rather than by grepping the source: a grep would
    still pass if the default were moved somewhere that no longer reaches the parser."""
    import argparse
    holder = {}
    real = argparse.ArgumentParser.parse_args

    def capture(self, *a, **kw):
        holder["actions"] = list(self._actions)
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = capture
    try:
        try:
            rw.main(["decide"])
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real
    for action in holder.get("actions", []):
        if "--hub" in action.option_strings:
            return action.default
    raise AssertionError("main() has no --hub option")


class RunsInThePublicRepo(unittest.TestCase):
    """This watcher is hosted-only by design (a chooser must not run on the pool it
    evaluates), and ONE OF THE TWO CONDITIONS IT DETECTS IS ACTIONS-BUDGET EXHAUSTION.

    In a PRIVATE repo a hosted job whose account budget is gone does not fail, it never
    starts - zero steps, no log. So a private hub guarantees this watcher is dead in
    exactly the scenario it exists for. These tests pin the properties that make the
    public-repo instance work, so a later "tidy-up" back toward the hub fails here first."""

    @classmethod
    def setUpClass(cls):
        with open(WORKFLOW, encoding="utf-8") as fh:
            cls.body = fh.read()

    def test_the_hub_default_is_this_repo_not_the_authoring_repo(self):
        """--hub names the repo whose variables hold the durable state (the hysteresis
        counter and the recovered-run ledger). Left pointing at the authoring repo, every
        run would read and write its state in a repo this workflow does not run in - and
        the hysteresis counter would be shared with a watcher that no longer exists."""
        self.assertEqual(_hub_default(), "izzywdev/FuzeInfra")

    def test_the_fleet_list_is_not_vendored_into_this_repo(self):
        """The hub owns the list. A committed copy here is a second source of truth that
        nothing reconciles - which is how a repo joins the fleet and never joins the
        watcher."""
        self.assertFalse(
            os.path.isfile(os.path.join(REPO_ROOT, "governance", "ruleset-fleet.json")),
            "governance/ruleset-fleet.json must NOT be committed here; runner-watch.yml "
            "fetches it from the hub at run time",
        )

    def test_every_job_materialises_the_fleet_list_before_running_the_script(self):
        """Both drivers need the list, and it is fetched per job. If a job could reach the
        script without it, fleet_repos() would raise mid-run instead of failing in a step
        whose error message names the missing grant."""
        import yaml
        with open(WORKFLOW, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        for name, jobdef in wf["jobs"].items():
            steps = jobdef["steps"]
            fetch = [i for i, st in enumerate(steps)
                     if "fleet target list" in str(st.get("name", ""))]
            runs = [i for i, st in enumerate(steps)
                    if "runner_watch.py" in str(st.get("run", ""))]
            self.assertTrue(fetch, "job %s: no fleet-materialisation step" % name)
            self.assertTrue(runs, "job %s: never invokes runner_watch.py" % name)
            self.assertLess(fetch[0], runs[0],
                            "job %s: fetches the fleet list AFTER using it" % name)

    def test_the_fleet_fetch_never_falls_back(self):
        """A fallback - a vendored copy, || true, an ignored non-zero - turns "the fleet is
        unknown" into "the fleet is empty", and an empty sweep exits 0. The watcher must be
        RED when it is blind, which is the one thing it can never be silent about."""
        import yaml
        with open(WORKFLOW, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        seen = 0
        for jobdef in wf["jobs"].values():
            for st in jobdef["steps"]:
                if "fleet target list" not in str(st.get("name", "")):
                    continue
                seen += 1
                run = str(st.get("run", ""))
                # Strip comments first: this step's own prose says "NO FALLBACK, NO
                # `|| true`", and a substring check that cannot tell the rule from the
                # code enforcing it is not an assertion, it is a coincidence.
                code = "\n".join(ln for ln in run.splitlines()
                                 if not ln.lstrip().startswith("#"))
                self.assertNotIn("|| true", code)
                self.assertNotIn("continue-on-error", str(st))
                self.assertIn("exit 1", code)
        self.assertEqual(seen, 2, "expected a fleet-fetch step in both jobs")

    def test_it_is_marked_as_a_fork_so_a_stamping_sweep_leaves_it_alone(self):
        """FuzeInfra reconciles `fuze:managed` workflow files against canonical templates.
        This one is FuzeInfra-owned and has no canonical template; an unmarked file that
        later acquired one would be silently overwritten by the sweep."""
        first_line = self.body.splitlines()[0]
        # The marker is what the sweep reads: the FIRST token after "# ". The rest of the
        # line explains itself and legitimately contains the word it is opting out of.
        marker = first_line.split()[1] if len(first_line.split()) > 1 else ""
        self.assertEqual(marker, "fuze:fork", first_line)
        self.assertFalse(first_line.startswith("# fuze:managed"))

    def test_the_schedule_caveats_are_written_down(self):
        """Scheduled workflows fire only from the DEFAULT branch, are best-effort (hence
        the off-peak 13,43 rather than :00), and are disabled after 60 days of repository
        inactivity. All three have burned someone; none is visible from the cron line."""
        # Anchored on the INDENTED key inside `on:`, because the header prose also says
        # "`schedule:`" and splitting on the bare word lands in the comment instead.
        start = self.body.index("\n  schedule:")
        schedule_block = self.body[start:self.body.index("\npermissions:", start)]
        self.assertIn("13,43 * * * *", schedule_block)
        for phrase in ("DEFAULT BRANCH", "BEST-EFFORT", "60 days"):
            self.assertIn(phrase, schedule_block,
                          "schedule caveat missing: %s" % phrase)


# ======================================================================================
# The fleet preflight - the failure mode a local file read did not have
# ======================================================================================

class FleetPreflight(unittest.TestCase):
    """A gh api call can succeed and still write something that is not the fleet list.
    That output is valid JSON, so fleet_repos() parses it, finds no `repos`, and returns an
    EMPTY list - and an empty fleet is a clean sweep of nothing that exits 0. These assert
    each such document is fatal instead."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(SCRIPTS, "runner_watch_fleet_preflight.py")
        spec = importlib.util.spec_from_file_location("rw_fleet_preflight", path)
        cls.pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.pf)

    def good(self):
        return {"owner": "izzywdev", "repos": ["FuzeHub", "FuzeInfra"],
                "excluded": {"FuzeInfra": "infra repo"}}

    def test_a_real_fleet_list_reduces_the_same_way_fleet_repos_does(self):
        self.assertEqual(self.pf.preflight(self.good()), ["izzywdev/FuzeHub"])

    def test_a_github_api_error_body_is_fatal_and_names_the_missing_grant(self):
        with self.assertRaises(self.pf.FleetPreflightError) as ctx:
            self.pf.preflight({"message": "Not Found",
                               "documentation_url": "https://docs.github.com"})
        self.assertIn("ERROR body", str(ctx.exception))
        self.assertIn("contents:read", str(ctx.exception))

    def test_an_empty_fleet_is_fatal_not_a_clean_run(self):
        with self.assertRaises(self.pf.FleetPreflightError) as ctx:
            self.pf.preflight({"owner": "izzywdev", "repos": ["FuzeInfra"],
                               "excluded": {"FuzeInfra": "x"}})
        self.assertIn("EMPTY", str(ctx.exception))

    def test_a_missing_owner_is_fatal_rather_than_guessed(self):
        doc = self.good()
        del doc["owner"]
        with self.assertRaises(self.pf.FleetPreflightError):
            self.pf.preflight(doc)

    def test_a_non_object_document_is_fatal(self):
        with self.assertRaises(self.pf.FleetPreflightError):
            self.pf.preflight(["FuzeHub"])


# ======================================================================================
# Systematic blindness - the run must be RED when it could not see ANYTHING
# ======================================================================================

class NeedsLiveness(unittest.TestCase):
    """`needs_liveness` decides which repos a failed probe actually MATTERS for. It must
    mirror decide_runner's precedence order exactly: every arm above the liveness check
    answers without a probe, so counting those repos as "blind" would let a fleet of public
    repos trip a systematic-blindness failure that has nothing to do with any grant."""

    def test_only_a_private_repo_with_a_declared_pool_needs_a_probe(self):
        self.assertTrue(rw.needs_liveness(facts(private=True, declared_pool="fuze-runner")))

    def test_a_public_repo_is_answered_by_visibility_alone(self):
        self.assertFalse(rw.needs_liveness(facts(private=False, declared_pool="fuze-runner")))

    def test_no_declared_pool_is_answered_by_the_documented_default(self):
        self.assertFalse(rw.needs_liveness(facts(private=True, declared_pool="")))

    def test_an_operator_pin_is_answered_by_the_operator(self):
        self.assertFalse(rw.needs_liveness(
            facts(private=True, declared_pool="fuze-runner", pinned=True)))

    def test_a_capability_pin_beats_liveness_so_needs_no_probe(self):
        self.assertFalse(rw.needs_liveness(
            facts(private=True, declared_pool="fuze-runner", capability="cluster")))

    def test_it_agrees_with_decide_runner_about_who_reaches_the_probe(self):
        """The two must not drift. For any repo where needs_liveness() is False,
        decide_runner must reach an answer even with online_runners=None - i.e. it must NOT
        return the "liveness UNVERIFIED" decision."""
        for kw in (dict(private=False, declared_pool="fuze-runner"),
                   dict(private=True, declared_pool=""),
                   dict(private=True, declared_pool="fuze-runner", pinned=True),
                   dict(private=True, declared_pool="fuze-runner", capability="cluster")):
            f = facts(online_runners=None, **kw)
            self.assertFalse(rw.needs_liveness(f), kw)
            self.assertNotIn("UNVERIFIED", rw.decide_runner(f).reason, kw)
        f = facts(private=True, declared_pool="fuze-runner", online_runners=None)
        self.assertTrue(rw.needs_liveness(f))
        self.assertIn("UNVERIFIED", rw.decide_runner(f).reason)


class SystematicBlindnessFailsTheRun(unittest.TestCase):
    """Per repo, an unverified probe is keep-current + warn, and that is correct. But when
    EVERY repo that needed a probe failed it, nothing was written and nothing COULD have
    been - and a green run there is the exact "looked at the whole fleet and saw none of
    it" outcome the design refuses.

    Observed live on the first dispatch after the port (run 33517478654): the fuze-agent App
    had no `administration: read`, all 14 private repos with a declared pool came back
    online=None, and the job reported SUCCESS.

    `run_decide` is driven here with its network layer stubbed, so these stay offline."""

    def setUp(self):
        self.calls = []
        self._probe, self._read, self._write = rw.probe_repo, rw.read_variable, rw.write_variable
        rw.read_variable = lambda slug, name: None
        rw.write_variable = lambda slug, name, value: self.calls.append((slug, name, value))
        self.addCleanup(self._restore)

    def _restore(self):
        rw.probe_repo, rw.read_variable, rw.write_variable = self._probe, self._read, self._write

    def stub(self, mapping):
        rw.probe_repo = lambda slug: mapping[slug]

    def test_all_probes_failed_on_repos_that_needed_them_is_a_FAILURE(self):
        self.stub({
            "izzywdev/A": rw.RepoFacts("izzywdev/A", private=True,
                                       declared_pool="a", online_runners=None),
            "izzywdev/B": rw.RepoFacts("izzywdev/B", private=True,
                                       declared_pool="b", online_runners=None),
        })
        rc = rw.run_decide(["izzywdev/A", "izzywdev/B"], rw.Policy(),
                           apply=False, hub="izzywdev/FuzeInfra")
        self.assertEqual(rc, 1, "a fleet-wide blind run must not exit 0")

    def test_a_partial_probe_failure_is_still_a_success(self):
        """One repo down is a blip, not a missing grant. Reddening the run there would
        train everyone to ignore the result - the same reason deploy-prod deliberately does
        not gate on health."""
        self.stub({
            "izzywdev/A": rw.RepoFacts("izzywdev/A", private=True,
                                       declared_pool="a", online_runners=None),
            "izzywdev/B": rw.RepoFacts("izzywdev/B", private=True,
                                       declared_pool="b", online_runners=3),
        })
        rc = rw.run_decide(["izzywdev/A", "izzywdev/B"], rw.Policy(),
                           apply=False, hub="izzywdev/FuzeInfra")
        self.assertEqual(rc, 0)

    def test_an_all_public_fleet_does_not_trip_it(self):
        """No repo needed a probe, so `blind == needed == 0`. Guarding on `needed_liveness`
        being non-zero is what stops "nobody needed liveness" reading as "nobody could see"."""
        self.stub({
            "izzywdev/A": rw.RepoFacts("izzywdev/A", private=False, online_runners=None),
            "izzywdev/B": rw.RepoFacts("izzywdev/B", private=False, online_runners=None),
        })
        rc = rw.run_decide(["izzywdev/A", "izzywdev/B"], rw.Policy(),
                           apply=False, hub="izzywdev/FuzeInfra")
        self.assertEqual(rc, 0)

    def test_check_mode_writes_nothing_even_while_failing(self):
        """The failure path must not become a write path. In check mode a blind run reports
        and exits non-zero; it must still not touch a single variable."""
        self.stub({"izzywdev/A": rw.RepoFacts("izzywdev/A", private=True,
                                              declared_pool="a", online_runners=None)})
        rw.run_decide(["izzywdev/A"], rw.Policy(), apply=False, hub="izzywdev/FuzeInfra")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
