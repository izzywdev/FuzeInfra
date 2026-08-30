"""Regression contracts for the Contabo autoscaler's production identity.

The original pool tag was attached to durable/control-plane/CI instances by
ca-salvage-enroll. The provider therefore reported those instances as desired
elastic capacity even though their Kubernetes names/provider IDs could not be
correlated, causing Cluster Autoscaler to repair target size and skip every
scale-up iteration.
"""

from __future__ import annotations

import base64
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROD_VALUES = ROOT / "helm" / "fuzeinfra" / "values-contabo.yaml"
SALVAGE_WORKFLOW = ROOT / ".github" / "workflows" / "ca-salvage-enroll.yml"
CUTOVER_WORKFLOW = ROOT / ".github" / "workflows" / "ca-cutover.yml"
HEALTH_WORKFLOW = ROOT / ".github" / "workflows" / "ca-health-check.yml"


def production_autoscaler() -> dict:
    values = yaml.safe_load(PROD_VALUES.read_text(encoding="utf-8"))
    return values["clusterAutoscaler"]


def test_prod_uses_a_clean_synchronized_elastic_pool_identity() -> None:
    autoscaler = production_autoscaler()
    provider = autoscaler["provider"]

    assert autoscaler["enabled"] is True
    assert autoscaler["scaleDownEnabled"] is False
    assert autoscaler["nodeGroup"] == {"minSize": 0, "maxSize": 1}
    assert {
        "minSize": provider["minSize"],
        "maxSize": provider["maxSize"],
    } == autoscaler["nodeGroup"]
    assert provider["elasticTag"] == provider["elasticNamePrefix"]
    assert provider["elasticTag"] == "fuzeinfra-prod-elastic-v2"
    assert provider["elasticTag"] != "fuzeinfra-elastic"


def test_scale_from_zero_template_preserves_name_provider_id_and_pool_contract() -> None:
    provider = production_autoscaler()["provider"]
    userdata = base64.b64decode(provider["userDataTemplateB64"]).decode("utf-8")

    assert "--node-name '{{.NodeName}}'" in userdata
    assert "provider-id=contabo://{{.NodeName}}" in userdata
    assert "fuzeinfra.io/pool=elastic" in userdata
    assert "fuzeinfra.io/elastic=true:PreferNoSchedule" in userdata


def test_salvage_workflow_cannot_contaminate_the_active_pool() -> None:
    # Parse first so malformed workflow YAML fails the test independently of
    # the textual safety assertions below.
    assert yaml.safe_load(SALVAGE_WORKFLOW.read_text(encoding="utf-8"))
    workflow = SALVAGE_WORKFLOW.read_text(encoding="utf-8")
    tag = production_autoscaler()["provider"]["elasticTag"]

    assert f"ELASTIC_TAG: {tag}" in workflow
    assert f"ELASTIC_NAME_PREFIX: {tag}" in workflow
    assert "elastic-userdata*.template" in workflow
    assert '"$ELASTIC_NAME_PREFIX"-*' in workflow
    assert "refusing to enroll a non-elastic template" in workflow
    assert "refusing to tag instance" in workflow


def test_cutover_workflow_cannot_restore_stale_identity_or_bounds() -> None:
    assert yaml.safe_load(CUTOVER_WORKFLOW.read_text(encoding="utf-8"))
    workflow = CUTOVER_WORKFLOW.read_text(encoding="utf-8")
    tag = production_autoscaler()["provider"]["elasticTag"]

    assert f'.clusterAutoscaler.provider.elasticTag = "{tag}"' in workflow
    assert f'.clusterAutoscaler.provider.elasticNamePrefix = "{tag}"' in workflow
    assert ".clusterAutoscaler.provider.maxSize = 1" in workflow
    assert ".clusterAutoscaler.nodeGroup.maxSize = 1" in workflow
    assert ".clusterAutoscaler.provider.maxSize = 5" not in workflow


def test_operational_check_detects_identity_and_scale_up_failures() -> None:
    assert yaml.safe_load(HEALTH_WORKFLOW.read_text(encoding="utf-8"))
    workflow = HEALTH_WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        "Nodegroup is nil",
        "unregistered nodes present",
        "No node group for node",
        "Some node group target size was fixed",
        "pod didn.t trigger scale-up",
        "No expansion options",
        "NotTriggerScaleUp",
    ):
        assert marker in workflow
