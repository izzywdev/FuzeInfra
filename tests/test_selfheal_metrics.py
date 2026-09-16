"""Guards for the self-heal loop metrics exporter's chart wiring.

Why this file exists
--------------------
The exporter is a new gated service, and the two ways a gated service silently
breaks in this chart are both mechanical:

1. An overlay is missed. CLAUDE.md requires every service to carry an `enabled`
   gate present in ALL FOUR values files. An overlay that never mentions the
   flag inherits whatever the base default happens to be — which works right
   up until someone flips the base default and turns a prod-only feature on
   across kind and EKS.

2. A dashboard JSON is added under helm/fuzeinfra/dashboards/ and never wired
   into the `range $name := list ...` in templates/grafana-dashboards.yaml. The
   file then sits in git looking deployed while Grafana never sees it, because
   the chart only renders a ConfigMap per NAME in that list. Nothing else in CI
   notices: helm lint passes, kubeconform passes, the chart installs.

Everything here is offline (helm template only, no cluster, no network).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
DASHBOARD_TEMPLATE = CHART / "templates" / "grafana-dashboards.yaml"
DASHBOARD_JSON = CHART / "dashboards" / "selfheal-loop.json"

#: overlay -> expected selfHealMetrics.enabled. Prod is the only cluster whose
#: alerts open the autofix issues, and the counts come from one shared GitHub
#: repo, so a second exporter anywhere else double-publishes the same numbers.
EXPECTED_ENABLED = {
    "values.yaml": False,
    "values-local.yaml": False,
    "values-aws.yaml": False,
    "values-contabo.yaml": True,
}

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(values_file: str, *set_args: str) -> list[dict]:
    cmd = ["helm", "template", "fuzeinfra", str(CHART), "--namespace", "fuzeinfra",
           "-f", str(CHART / values_file)]
    for arg in set_args:
        cmd += ["--set", arg]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _names(docs: list[dict], kind: str) -> set[str]:
    return {
        (d.get("metadata") or {}).get("name", "")
        for d in docs
        if d.get("kind") == kind
    }


@pytest.mark.parametrize("values_file,expected", sorted(EXPECTED_ENABLED.items()))
def test_flag_declared_in_every_overlay(values_file: str, expected: bool):
    values = yaml.safe_load((CHART / values_file).read_text(encoding="utf-8")) or {}
    block = values.get("selfHealMetrics")
    assert isinstance(block, dict), (
        f"{values_file} does not declare selfHealMetrics at all. Every gated "
        "service must appear in all four values files, so the overlay's intent "
        "is explicit and cannot be flipped by a change to the base default."
    )
    assert block.get("enabled") is expected, (
        f"{values_file}: selfHealMetrics.enabled is {block.get('enabled')!r}, "
        f"expected {expected!r}."
    )


def test_no_token_value_is_committed():
    """The chart may reference the GitHub token Secret; it may never carry one."""
    for values_file in EXPECTED_ENABLED:
        block = (yaml.safe_load((CHART / values_file).read_text(encoding="utf-8"))
                 or {}).get("selfHealMetrics") or {}
        for key in block:
            assert key not in ("githubToken", "token", "githubTokenValue"), (
                f"{values_file} carries an inline token key {key!r} under "
                "selfHealMetrics. Tokens are referenced from a SealedSecret only "
                "(githubTokenSecretName / githubTokenSecretKey)."
            )


def test_dashboard_json_is_valid_and_migrated():
    dashboard = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    assert dashboard["schemaVersion"] >= 39, (
        "Grafana v13 fails to load pre-v39 table panels with 'Error loading: "
        "table'. Bump schemaVersion to 39."
    )
    for panel in dashboard["panels"]:
        if panel.get("type") != "table":
            continue
        for override in panel.get("fieldConfig", {}).get("overrides", []):
            ids = {p.get("id") for p in override.get("properties", [])}
            assert "custom.displayMode" not in ids, (
                f"Table panel {panel.get('title')!r} uses the pre-v39 "
                "custom.displayMode; Grafana v13 needs custom.cellOptions."
            )


def test_dashboard_is_wired_into_the_configmap_range():
    """A dashboard JSON not named in the range list is never rendered at all."""
    template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"range \$name := list ([^}]+)\}\}", template)
    assert match, "could not find the dashboard `range $name := list ...` line"
    listed = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert "selfheal-loop" in listed, (
        "helm/fuzeinfra/dashboards/selfheal-loop.json exists but is not in the "
        f"range list {sorted(listed)}, so no ConfigMap is rendered for it and "
        "Grafana never sees the dashboard."
    )


@needs_helm
def test_disabled_overlay_renders_nothing():
    docs = _render("values-local.yaml")
    assert "fuzeinfra-selfheal-metrics" not in _names(docs, "Deployment")
    assert "fuzeinfra-selfheal-metrics" not in _names(docs, "Service")

    prom = [d for d in docs
            if d.get("kind") == "ConfigMap"
            and (d.get("metadata") or {}).get("name") == "fuzeinfra-prometheus-config"]
    if prom:
        assert "selfheal-metrics" not in prom[0]["data"]["prometheus.yml"]


@needs_helm
def test_enabled_overlay_renders_the_full_wiring():
    docs = _render("values-contabo.yaml")

    assert "fuzeinfra-selfheal-metrics" in _names(docs, "Deployment")
    assert "fuzeinfra-selfheal-metrics" in _names(docs, "Service")
    assert "fuzeinfra-grafana-dashboard-selfheal-loop" in _names(docs, "ConfigMap")

    deploy = next(d for d in docs
                  if d.get("kind") == "Deployment"
                  and d["metadata"]["name"] == "fuzeinfra-selfheal-metrics")
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in container["env"]}

    # The map must reach the pod as parseable JSON, or the exporter silently
    # falls back to its built-in defaults and an added 4th label is ignored.
    assert set(json.loads(env["LABEL_SYSTEM_MAP"]["value"])) >= {
        "argo-autofix", "crit-autofix", "alertmanager-autofix"
    }

    token = env["GITHUB_TOKEN"]
    assert "value" not in token, "GITHUB_TOKEN must be a secretKeyRef, never a literal"
    assert token["valueFrom"]["secretKeyRef"]["optional"] is True, (
        "the token ref must stay optional: a cluster without the SealedSecret "
        "should poll GitHub unauthenticated, not crash-loop on "
        "CreateContainerConfigError."
    )

    prom = next(d for d in docs
                if d.get("kind") == "ConfigMap"
                and d["metadata"]["name"] == "fuzeinfra-prometheus-config")
    scrape = yaml.safe_load(prom["data"]["prometheus.yml"])["scrape_configs"]
    job = next(j for j in scrape if j["job_name"] == "selfheal-metrics")
    assert job["static_configs"][0]["targets"] == ["fuzeinfra-selfheal-metrics:9109"]


@needs_helm
@pytest.mark.parametrize("enabled", ["true", "false"])
def test_renders_cleanly_with_the_flag_forced_either_way(enabled: str):
    docs = _render("values.yaml", f"selfHealMetrics.enabled={enabled}")
    assert docs
    present = "fuzeinfra-selfheal-metrics" in _names(docs, "Deployment")
    assert present is (enabled == "true")
