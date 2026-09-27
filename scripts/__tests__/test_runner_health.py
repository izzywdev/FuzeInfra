"""Executable invariants for scripts/runner_health.py.

The monitor's whole value is catching the case where local state and GitHub's view
disagree -- pods `2/2 Running`, GitHub `offline`. So the tests that matter here are the
ones that pin the disagreement, plus the two ways a monitor like this fails in practice:

  * crying wolf during normal scale-up (a pod that is 3 seconds old has not failed to
    register, it simply has not registered yet), and
  * reporting green for a scale set it silently does not cover.

The last test replays the real numbers from FuzeInfra#1187 to show the probe would have
fired on the outage it was written for.

Offline: pure functions over literals. No cluster, no network.
"""

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import runner_health as rh  # noqa: E402

NOW = dt.datetime(2026, 9, 23, 8, 0, 0, tzinfo=dt.timezone.utc)


def _pod(name, node, minutes_old=60, phase="Running"):
    created = NOW - dt.timedelta(minutes=minutes_old)
    return {
        "metadata": {"name": name, "creationTimestamp": created.isoformat().replace("+00:00", "Z")},
        "spec": {"nodeName": node},
        "status": {"phase": phase},
    }


def _runner(name, status):
    return {"name": name, "status": status}


def _workflow():
    import yaml

    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / ".github" / "workflows" / "runner-health-check.yml"
    )
    assert path.is_file(), f"{path} is missing"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- mapping


class TestMapping(unittest.TestCase):

    def test_repo_from_config_url(self):
        cases = [
            ("https://github.com/izzywdev/FuzePlan", "izzywdev/FuzePlan"),
            ("https://github.com/izzywdev/FuzePlan/", "izzywdev/FuzePlan"),
            ("https://github.com/izzywdev", None),  # org-level: no repo to query
            ("", None),
            (None, None),
            ("not a url", None),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(rh.repo_from_config_url(url), expected)

    def test_scale_set_mapping_comes_from_the_cluster_not_a_hardcoded_list(self):
        payload = {
            "items": [
                {"metadata": {"name": "fuzeplan"}, "spec": {"githubConfigUrl": "https://github.com/izzywdev/FuzePlan"}},
                {"metadata": {"name": "staging"}, "spec": {"githubConfigUrl": "https://github.com/izzywdev/FuzeInfra"}},
                # Unparseable config URL must be DROPPED, not guessed at.
                {"metadata": {"name": "broken"}, "spec": {"githubConfigUrl": "https://example.invalid"}},
            ]
        }
        self.assertEqual(rh.parse_scale_sets(payload), {
            "fuzeplan": "izzywdev/FuzePlan",
            "staging": "izzywdev/FuzeInfra",
        })

    def test_scale_set_prefix_does_not_swallow_a_longer_sibling(self):
        """'fuze-runner' must not claim 'fuze-runner-extra' pods, or findings get misattributed."""
        pods = rh.parse_runner_pods(
            {"items": [
                _pod("fuze-runner-hb448-runner-25pl2", "n1"),
                _pod("fuze-runner-extra-hb448-runner-99999", "n1"),
            ]},
            NOW,
        )
        mine = [p["name"] for p in rh.pods_for_scale_set(pods, "fuze-runner")]
        self.assertEqual(mine, ["fuze-runner-hb448-runner-25pl2"])

        theirs = [p["name"] for p in rh.pods_for_scale_set(pods, "fuze-runner-extra")]
        self.assertEqual(theirs, ["fuze-runner-extra-hb448-runner-99999"])


# --------------------------------------------------------------------------- evaluate


class TestEvaluate(unittest.TestCase):

    def test_running_pods_with_zero_online_runners_is_critical(self):
        """The #1187 signature: capacity that exists, looks healthy, and serves nothing."""
        scale_sets = {"fuzeplan": "izzywdev/FuzePlan"}
        pods = rh.parse_runner_pods(
            {"items": [
                _pod("fuzeplan-bhn6d-runner-976qb", "fuzeinfra-ci-runner-1"),
                _pod("fuzeplan-bhn6d-runner-jqcjk", "fuzeinfra-ci-runner-2"),
            ]},
            NOW,
        )
        runners = {"izzywdev/FuzePlan": {"runners": [
            _runner("fuzeplan-bhn6d-runner-976qb", "offline"),
            _runner("fuzeplan-bhn6d-runner-jqcjk", "offline"),
        ]}}

        findings = rh.evaluate(scale_sets, pods, runners)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].level, rh.CRITICAL)
        # The node correlation is what made #1187 diagnosable; it must be in the output.
        self.assertEqual(findings[0].nodes, ("fuzeinfra-ci-runner-1", "fuzeinfra-ci-runner-2"))

    def test_freshly_created_pods_do_not_cry_wolf(self):
        """A pod seconds old has not failed to register; it has not tried yet."""
        scale_sets = {"fuzeplan": "izzywdev/FuzePlan"}
        pods = rh.parse_runner_pods(
            {"items": [_pod("fuzeplan-bhn6d-runner-976qb", "n1", minutes_old=0)]}, NOW
        )
        runners = {"izzywdev/FuzePlan": {"runners": []}}
        self.assertEqual(rh.evaluate(scale_sets, pods, runners), [])

    def test_idle_scale_set_with_no_pods_is_not_a_finding(self):
        """Zero runners is the normal resting state of an ephemeral scale set."""
        self.assertEqual(rh.evaluate({"fuzebi": "izzywdev/FuzeBI"}, [], {"izzywdev/FuzeBI": {"runners": []}}), [])

    def test_healthy_set_is_silent(self):
        scale_sets = {"fuzefinance": "izzywdev/FuzeFinance"}
        pods = rh.parse_runner_pods(
            {"items": [_pod("fuzefinance-rmqs9-runner-f5dwf", "elastic-1")]}, NOW
        )
        runners = {"izzywdev/FuzeFinance": {"runners": [
            _runner("fuzefinance-rmqs9-runner-f5dwf", "online")
        ]}}
        self.assertEqual(rh.evaluate(scale_sets, pods, runners), [])

    def test_partial_registration_warns_and_names_only_the_stranded_nodes(self):
        """Replays FuzeInfra#1187: 1 of 5 online, and the split falls exactly on node lines."""
        scale_sets = {"mendysrobotics": "izzywdev/MendysRobotics"}
        pods = rh.parse_runner_pods(
            {"items": [
                _pod("mendysrobotics-btqtv-runner-2nzwf", "fuzeinfra-prod-elastic-v2-89a375a8"),
                _pod("mendysrobotics-btqtv-runner-kx8kx", "fuzeinfra-ci-runner-1"),
                _pod("mendysrobotics-btqtv-runner-mm8r6", "fuzeinfra-ci-runner-1"),
                _pod("mendysrobotics-btqtv-runner-w4fsh", "fuzeinfra-ci-runner-1"),
                _pod("mendysrobotics-btqtv-runner-vg2l6", "fuzeinfra-ci-runner-2"),
            ]},
            NOW,
        )
        runners = {"izzywdev/MendysRobotics": {"runners": [
            _runner("mendysrobotics-btqtv-runner-2nzwf", "online"),
            _runner("mendysrobotics-btqtv-runner-kx8kx", "offline"),
            _runner("mendysrobotics-btqtv-runner-mm8r6", "offline"),
            _runner("mendysrobotics-btqtv-runner-w4fsh", "offline"),
            _runner("mendysrobotics-btqtv-runner-vg2l6", "offline"),
        ]}}

        findings = rh.evaluate(scale_sets, pods, runners)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].level, rh.WARNING)
        self.assertIn("4 of 5", findings[0].message)
        # Only the broken nodes -- the healthy elastic node must not be implicated.
        self.assertEqual(findings[0].nodes, ("fuzeinfra-ci-runner-1", "fuzeinfra-ci-runner-2"))

    def test_stranded_count_matches_the_nodes_printed_beside_it(self):
        """Regression: the count must be of stranded PODS, not len(settled) - len(online).

        GitHub's runner list is not a subset of the settled pods -- it can include a runner
        whose pod is too young to be settled, or a stale registration whose pod is gone.
        Subtracting lengths then prints a number that contradicts the node list next to it.
        Seen live against the cluster as "1 of 3 ... not online" followed by THREE nodes,
        which is exactly the kind of internal contradiction that makes an operator stop
        trusting a monitor.
        """
        scale_sets = {"fuzefront": "izzywdev/FuzeFront"}
        pods = rh.parse_runner_pods(
            {"items": [
                _pod("fuzefront-abc-runner-1", "fuzeinfra-ci-runner-1"),
                _pod("fuzefront-abc-runner-2", "fuzeinfra-ci-runner-2"),
                # Too young to be settled: present in GitHub's list, absent from `settled`.
                _pod("fuzefront-abc-runner-3", "elastic-1", minutes_old=0),
            ]},
            NOW,
        )
        runners = {"izzywdev/FuzeFront": {"runners": [
            _runner("fuzefront-abc-runner-3", "online"),
            _runner("fuzefront-abc-runner-1", "offline"),
            _runner("fuzefront-abc-runner-2", "offline"),
        ]}}

        finding = rh.evaluate(scale_sets, pods, runners)[0]
        self.assertEqual(finding.level, rh.WARNING)
        # 2 settled pods are stranded, and exactly 2 nodes should be named.
        self.assertIn("2 of 2", finding.message)
        self.assertEqual(finding.nodes, ("fuzeinfra-ci-runner-1", "fuzeinfra-ci-runner-2"))
        self.assertEqual(len(finding.nodes), int(finding.message.split()[0]))

    def test_critical_findings_sort_before_warnings(self):
        scale_sets = {"a": "o/A", "b": "o/B"}
        pods = rh.parse_runner_pods(
            {"items": [
                _pod("a-h-runner-1", "n1"),
                _pod("b-h-runner-1", "n2"),
                _pod("b-h-runner-2", "n2"),
            ]},
            NOW,
        )
        runners = {
            "o/A": {"runners": [_runner("a-h-runner-1", "offline")]},          # critical
            "o/B": {"runners": [_runner("b-h-runner-1", "online"),
                                _runner("b-h-runner-2", "offline")]},          # warning
        }
        levels = [f.level for f in rh.evaluate(scale_sets, pods, runners)]
        self.assertEqual(levels, [rh.CRITICAL, rh.WARNING])


# --------------------------------------------------------------------------- dns


class TestDns(unittest.TestCase):

    def test_single_homed_dns_is_flagged(self):
        """The condition that turned one node fault into a fleet outage."""
        endpoints = {"subsets": [{"addresses": [{"ip": "10.42.2.10", "nodeName": "fuze-core-2"}]}]}
        findings = rh.dns_endpoint_findings(endpoints)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].level, rh.WARNING)
        self.assertEqual(findings[0].nodes, ("fuze-core-2",))

    def test_dns_spread_across_two_nodes_is_silent(self):
        endpoints = {"subsets": [{"addresses": [
            {"ip": "10.42.2.10", "nodeName": "fuze-core-2"},
            {"ip": "10.42.19.5", "nodeName": "fuzeinfra-ci-runner-1"},
        ]}]}
        self.assertEqual(rh.dns_endpoint_findings(endpoints), [])

    def test_no_dns_endpoints_at_all_is_critical(self):
        self.assertEqual(rh.dns_endpoint_findings({"subsets": []})[0].level, rh.CRITICAL)


# --------------------------------------------------------------------- workflow


class TestWorkflow(unittest.TestCase):

    def test_probe_does_not_run_inside_the_cluster_it_monitors(self):
        """The blind spot this workflow exists to close.

        ca-health-check.yml runs `runs-on: staging` -- a self-hosted runner in the cluster
        it inspects. During FuzeInfra#1187 both staging runners were on the broken nodes,
        so it did not go red, it went SILENT: last run 04:56Z on a */15 schedule. A monitor
        hosted inside the thing it monitors cannot report that thing's outage.
        """
        runs_on = _workflow()["jobs"]["probe"]["runs-on"]
        self.assertEqual(
            runs_on,
            "ubuntu-latest",
            f"runner-health must run on a GitHub-hosted runner, got {runs_on!r}. Moving it "
            "onto a self-hosted pool means the outage it detects also prevents it running.",
        )

    def test_probe_runs_on_a_schedule(self):
        """A check that only runs on dispatch detects nothing on its own."""
        triggers = _workflow()[True]  # YAML parses the `on:` key as the boolean True
        self.assertIn("schedule", triggers, "runner-health must be scheduled, not dispatch-only")
