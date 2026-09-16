"""Every longhorn_crd_* metric an alert rule selects on must be produced by the
kube-state-metrics CustomResourceState config.

This is the same guard as tests/test_alert_label_allowlist.py, for the same
failure mode. A Prometheus rule that queries a metric or label dimension nothing
emits is rejected by NOTHING in the chain: promtool parses it, its own unit tests
pass (they hand-feed the series), helm renders it, kubeconform validates it and
Prometheus loads it without complaint. It is simply dead, and looks green.

That is not hypothetical here. Two things were true when rules/storage.yml was
written, both verified against the live cluster on 2026-09-07:

  * Nothing scrapes longhorn-backend:9500. Querying the live Prometheus for
    `longhorn_volume_robustness` returns an empty vector, so a rule written
    against Longhorn's own metric names would have been permanently unable to
    fire while passing every check in CI.
  * Longhorn exposes no replica-count metric at all, so spec.numberOfReplicas is
    reachable ONLY through the CustomResourceState collector configured in
    helm/fuzeinfra/files/ksm-customresource-state.yaml.

Both alerts therefore depend on that one config file staying in sync with the
rules. This test enforces the link in both directions-ish: every metric selected
by the rules must be declared in the config, and the StateSet label values the
rules match on (robustness="degraded", state="attached") must be in the declared
state lists.

Offline: reads two YAML files. No cluster, no network.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
CRS_CONFIG = CHART / "files" / "ksm-customresource-state.yaml"
STORAGE_RULES = CHART / "rules" / "storage.yml"

# longhorn_crd_volume_robustness{robustness="degraded"} -> name, brace body
_SELECTOR = re.compile(r"\b(longhorn_crd_[a-zA-Z0-9_]+)\s*(?:\{([^}]*)\})?")
_MATCHER = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _crs_spec() -> dict:
    return yaml.safe_load(CRS_CONFIG.read_text(encoding="utf-8"))


def _declared() -> dict:
    """metric name -> {"labels": set, "state_values": {label: set}}."""
    out = {}
    for resource in _crs_spec()["spec"]["resources"]:
        prefix = resource["metricNamePrefix"]
        common = set(resource.get("labelsFromPath", {}))
        for metric in resource["metrics"]:
            name = f"{prefix}_{metric['name']}"
            entry = {"labels": set(common), "state_values": {}}
            each = metric["each"]
            if each["type"] == "StateSet":
                label = each["stateSet"]["labelName"]
                entry["labels"].add(label)
                entry["state_values"][label] = set(each["stateSet"]["list"])
            out[name] = entry
    return out


def _rule_selectors():
    """[(alertname, metric, {label: value}), ...] for every longhorn_crd_* use."""
    doc = yaml.safe_load(STORAGE_RULES.read_text(encoding="utf-8"))
    found = []
    for group in doc.get("groups", []):
        for rule in group.get("rules", []):
            expr = str(rule.get("expr", ""))
            name = rule.get("alert") or rule.get("record")
            for metric, body in _SELECTOR.findall(expr):
                found.append((name, metric, dict(_MATCHER.findall(body or ""))))
    return found


def test_rules_actually_reference_the_crs_metrics():
    """Guards against the rules being silently rewritten onto another source."""
    assert _rule_selectors(), (
        f"{STORAGE_RULES.name} selects no longhorn_crd_* metric. If the rules were "
        "moved onto Longhorn's own longhorn_volume_* metrics, note that NOTHING "
        "scrapes longhorn-backend:9500 in this cluster — verified 2026-09-07, the "
        "query returns an empty vector — so those rules would be dead on arrival."
    )


def test_every_selected_metric_is_declared_in_the_crs_config():
    declared = _declared()
    missing = {
        f"{alert} -> {metric}"
        for alert, metric, _ in _rule_selectors()
        if metric not in declared
    }
    assert not missing, (
        f"Alert rules select metrics that {CRS_CONFIG.name} does not generate: "
        f"{sorted(missing)}. kube-state-metrics will emit nothing for them and the "
        f"alerts can never fire. Declared: {sorted(declared)}"
    )


def test_every_selected_label_is_emitted():
    declared = _declared()
    problems = []
    for alert, metric, matchers in _rule_selectors():
        entry = declared.get(metric)
        if entry is None:
            continue  # covered by the test above
        for label in matchers:
            if label not in entry["labels"]:
                problems.append(
                    f"{alert}: {metric}{{{label}=...}} — not emitted "
                    f"(emits {sorted(entry['labels'])})"
                )
    assert not problems, (
        "Alert rules match on label dimensions kube-state-metrics never emits, so "
        f"the selector matches nothing: {problems}"
    )


def test_every_selected_state_value_is_in_the_state_list():
    """A StateSet only emits the values it lists; matching any other is dead."""
    declared = _declared()
    problems = []
    for alert, metric, matchers in _rule_selectors():
        entry = declared.get(metric)
        if entry is None:
            continue
        for label, value in matchers.items():
            allowed = entry["state_values"].get(label)
            if allowed is not None and value not in allowed:
                problems.append(
                    f"{alert}: {metric}{{{label}=\"{value}\"}} — not in the "
                    f"stateSet list {sorted(allowed)}"
                )
    assert not problems, (
        "Alert rules match StateSet values that are never generated: "
        f"{problems}"
    )


def test_underreplication_rule_reads_spec_not_status():
    """The whole point of the alert: it must fire while robustness is healthy.

    fuzeinfra-prometheus-data reported robustness=healthy for the entire time it
    sat at one replica — one replica out of one desired IS healthy. An alert
    gated on robustness would not have fired, which is precisely why the volume
    was lost. Keep the under-replication rule off status.robustness.
    """
    doc = yaml.safe_load(STORAGE_RULES.read_text(encoding="utf-8"))
    rules = [
        r
        for g in doc["groups"]
        for r in g["rules"]
        if r.get("alert") == "LonghornVolumeUnderReplicated"
    ]
    assert len(rules) == 1, "LonghornVolumeUnderReplicated must exist exactly once"
    expr = str(rules[0]["expr"])
    assert "replicas_desired" in expr, (
        "LonghornVolumeUnderReplicated must read the DESIRED replica count "
        "(spec.numberOfReplicas)."
    )
    assert "robustness" not in expr, (
        "LonghornVolumeUnderReplicated must NOT be gated on robustness. A "
        "single-replica volume reports robustness=healthy, so gating on it "
        "recreates the exact blind spot that lost fuzeinfra-prometheus-data."
    )
