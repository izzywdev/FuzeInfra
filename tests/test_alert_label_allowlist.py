"""Every node label an alert rule selects on must be emitted by kube-state-metrics.

kube-state-metrics v2 drops EVERY node label from kube_node_labels unless it is named
in --metric-labels-allowlist. A rule that selects on a dropped label is not rejected by
anything: promtool parses it, helm renders it, Prometheus loads it. It simply operates
on a dimension that does not exist.

On 2026-09-07 `fuzeinfra.io/vlan` was referenced by two shipped rules and was NOT on the
allowlist (live prod args were `nodes=[fuzeinfra.io/pool]`). The two rules broke in
OPPOSITE directions, which is why this is worth a guard rather than a comment:

  * NodeQuarantinedOffVLAN selects kube_node_labels{label_fuzeinfra_io_vlan="absent"}
    directly. With no such dimension it matched nothing and could never fire.
  * NodeMissingVLANLabel EXCLUDES via `unless on(node) kube_node_labels{...vlan=~
    "present|absent"}`. An always-empty right-hand side excludes nothing, so it fired
    for every non-control-plane node continuously.

Silent-and-dead and loud-and-wrong from the same missing config line.

The existing promtool unit tests do not catch this and cannot: their input_series
hand-feed a label_fuzeinfra_io_vlan dimension, so they assert the rule's LOGIC against a
metric shape that prod never produces. This test checks the other half — that the metric
really carries the dimension the logic depends on.

Offline: reads two YAML files. No cluster, no network.
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
VALUES = CHART / "values.yaml"
RULES_DIR = CHART / "rules"

# kube-state-metrics replaces every character that is invalid in a Prometheus label
# name with "_", so fuzeinfra.io/pool is exposed as label_fuzeinfra_io_pool.
_INVALID = re.compile(r"[^a-zA-Z0-9_]")

# Matches a kube_node_labels selector and captures its brace body, e.g.
# kube_node_labels{label_fuzeinfra_io_pool="ci"} -> label_fuzeinfra_io_pool="ci"
_SELECTOR = re.compile(r"kube_node_labels\s*\{([^}]*)\}")
_LABEL_REF = re.compile(r"\b(label_[a-zA-Z0-9_]+)\b")


def _sanitize(node_label: str) -> str:
    """fuzeinfra.io/vlan -> label_fuzeinfra_io_vlan (the kube-state-metrics form)."""
    return "label_" + _INVALID.sub("_", node_label)


def _allowlisted() -> set:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    entries = values["kubeStateMetrics"]["nodeLabelsAllowlist"]
    return {_sanitize(e) for e in entries}


def _referenced() -> dict:
    """Every label_* selected on kube_node_labels, mapped to the files using it."""
    found = {}
    for path in sorted(RULES_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                expr = str(rule.get("expr", ""))
                for body in _SELECTOR.findall(expr):
                    for ref in _LABEL_REF.findall(body):
                        found.setdefault(ref, set()).add(
                            f"{path.name}:{rule.get('alert') or rule.get('record')}"
                        )
    return found


def test_every_selected_node_label_is_allowlisted():
    allowed = _allowlisted()
    missing = {
        ref: sorted(users)
        for ref, users in _referenced().items()
        if ref not in allowed
    }
    assert not missing, (
        "These rules select on node labels that kube-state-metrics is not configured "
        "to emit, so the dimension does not exist at evaluation time. A direct "
        "selector can never fire; an `unless` exclusion never excludes and fires "
        "constantly. Add the label to kubeStateMetrics.nodeLabelsAllowlist in "
        f"helm/fuzeinfra/values.yaml.\n{missing}"
    )


def test_vlan_label_is_allowlisted():
    """Regression pin for the 2026-09-07 finding specifically."""
    assert "label_fuzeinfra_io_vlan" in _allowlisted(), (
        "fuzeinfra.io/vlan drives NodeQuarantinedOffVLAN and NodeMissingVLANLabel; "
        "dropping it from the allowlist silently disables one and inverts the other."
    )


def test_rules_actually_reference_node_labels():
    """Guard the guard: if the selector regex stops matching, the test above passes
    vacuously and this whole file becomes decorative."""
    referenced = _referenced()
    assert "label_fuzeinfra_io_pool" in referenced, (
        "Expected the ci-pool rules to select on label_fuzeinfra_io_pool. If the rules "
        "were restructured, update _SELECTOR — do not delete this assertion."
    )
