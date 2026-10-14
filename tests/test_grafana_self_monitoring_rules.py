"""Offline guards for the Grafana-managed self-monitoring alert rules.

Why this exists: every Prometheus rule is evaluated BY Prometheus, so none fires when
Prometheus itself is down (2026-10-14: fuzeinfra-prometheus stuck in ContainerCreating
for 13h on a Longhorn fsck failure; PrometheusDown, being absent(up{job="prometheus"})
on the same Prometheus, was silent too). The watchdog rule lives in Grafana, a separate
Deployment. A malformed provisioning file can stop Grafana from starting, so the rendered
rules.yaml is parsed and compared structurally against the long-working critlogdetect rule.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
WORKFLOW = REPO / ".github" / "workflows" / "grafana-crit-fix.yml"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

OVERLAYS = [None, "values-local.yaml", "values-aws.yaml", "values-contabo.yaml"]


def _rules_doc(overlay):
    cmd = ["helm", "template", "t", str(CHART), "-n", "fuzeinfra"]
    if overlay:
        cmd += ["-f", str(CHART / overlay)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    cm = next(
        d for d in yaml.safe_load_all(out)
        if d and d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "fuzeinfra-grafana-alerting"
    )
    return yaml.safe_load(cm["data"]["rules.yaml"])


def _rules(doc):
    return {r["uid"]: r for g in doc["groups"] for r in g["rules"]}


@pytest.fixture(scope="module", params=OVERLAYS, ids=lambda o: o or "default")
def doc(request):
    return _rules_doc(request.param)


def test_unique_uids_and_titles(doc):
    rules = [r for g in doc["groups"] for r in g["rules"]]
    assert len({r["uid"] for r in rules}) == len(rules)
    # alertname == title is what grafana-crit-fix.yml dedups on.
    assert len({r["title"] for r in rules}) == len(rules)
    assert {"critlogdetect", "prometheusunreachable", "storagemountfailure"} <= {r["uid"] for r in rules}


def test_group_names_unique_and_intervals_valid(doc):
    names = [g["name"] for g in doc["groups"]]
    assert len(names) == len(set(names))
    for g in doc["groups"]:
        assert g["orgId"] == 1 and g["folder"] and g["interval"]


def test_structure_matches_working_rule(doc):
    """Every rule has exactly the key set of the proven critlogdetect rule."""
    rules = _rules(doc)
    ref = rules["critlogdetect"]
    for uid, r in rules.items():
        assert set(r) == set(ref), uid
        assert [d["refId"] for d in r["data"]] == [d["refId"] for d in ref["data"]], uid
        assert r["condition"] == "C"
        cond = r["data"][-1]
        assert cond["datasourceUid"] == "-100"
        assert cond["model"]["type"] == "classic_conditions"
        for d in r["data"]:
            assert set(d) == set(ref["data"][0]) , (uid, d["refId"])
            assert set(d["relativeTimeRange"]) == {"from", "to"}


def test_watchdog_alerts_on_no_data_and_error(doc):
    r = _rules(doc)["prometheusunreachable"]
    assert r["noDataState"] == "Alerting"
    assert r["execErrState"] == "Alerting"
    assert r["for"] == "10m"
    assert r["labels"]["severity"] == "critical"
    assert r["labels"]["namespace"] == "fuzeinfra"
    q = r["data"][0]
    assert q["datasourceUid"] == "fuzeinfra-prometheus"
    assert q["model"]["expr"] == 'up{job="prometheus"}'
    assert r["annotations"]["summary"] and r["annotations"]["description"]


def test_loki_rule_uses_loki_datasource_and_routes_critical(doc):
    r = _rules(doc)["storagemountfailure"]
    assert r["data"][0]["datasourceUid"] == "fuzeinfra-loki"
    expr = r["data"][0]["model"]["expr"]
    assert '{namespace="longhorn-system"}' in expr
    for needle in ("fsck", "MountVolume", "FailedAttachVolume"):
        assert needle in expr
    assert r["labels"]["severity"] == "critical"
    assert r["noDataState"] == "OK"  # no matching lines is the healthy state


def test_every_datasource_uid_exists_in_provisioning():
    out = subprocess.run(
        ["helm", "template", "t", str(CHART), "-n", "fuzeinfra", "-f", str(CHART / "values-contabo.yaml")],
        capture_output=True, text=True, check=True,
    ).stdout
    cms = {d["metadata"]["name"]: d for d in yaml.safe_load_all(out) if d and d.get("kind") == "ConfigMap"}
    ds = yaml.safe_load(cms["fuzeinfra-grafana-datasources"]["data"]["datasources.yaml"])
    uids = {d["uid"] for d in ds["datasources"]} | {"-100"}
    for r in _rules(_rules_doc("values-contabo.yaml")).values():
        for d in r["data"]:
            assert d["datasourceUid"] in uids


def test_critical_alerts_reach_the_webhook_policy():
    out = subprocess.run(
        ["helm", "template", "t", str(CHART), "-n", "fuzeinfra", "-f", str(CHART / "values-contabo.yaml")],
        capture_output=True, text=True, check=True,
    ).stdout
    cm = next(d for d in yaml.safe_load_all(out) if d and d.get("metadata", {}).get("name") == "fuzeinfra-grafana-alerting")
    policy = yaml.safe_load(cm["data"]["policy.yaml"])["policies"][0]
    assert policy["routes"][0]["matchers"] == ["severity = critical"]
    assert policy["routes"][0]["receiver"] == "fuze-autofix"


# --- dedup: one open generic issue must not suppress an unrelated critical alert ---

def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_concurrency_is_keyed_per_alertname():
    group = _workflow()["concurrency"]["group"]
    assert "alertname" in group, "a global group lets one pending run evict another alert's run"


def test_named_alerts_bypass_generic_gate_and_suppress_list():
    wf = WORKFLOW.read_text()
    pre = _workflow()["jobs"]["preflight"]["steps"][0]["run"]
    assert '!= "CRIT Log Detected"' in pre and "skip=false" in pre.split("Ensure both labels")[0]
    steps = {s.get("id"): s for s in _workflow()["jobs"]["analyze-and-fix"]["steps"] if s.get("id")}
    assert steps["logs"]["if"] == "steps.alertkey.outputs.generic == 'true'"
    assert steps["suppress"]["if"] == "steps.alertkey.outputs.generic == 'true'"
    assert "grafana-alert-key" in wf and "label grafana-autofix" in wf
