"""The Contabo cluster-autoscaler provider image must be pinned to an
immutable tag, never `:latest`.

Why this file exists
---------------------
`:latest` means Kubernetes sees no pod-template change when the image is
rebuilt, so a correct, merged, successfully-built provider fix does not
deploy on its own. This bit prod twice in one chain (#831, #832) before a
human remembered to bump a separate `imageRollout` annotation (#834) to force
the roll. `ca-provider-image.yml` already publishes an immutable
`sha-<short-commit-sha>` tag (docker/metadata-action `type=sha`) on every
push to main alongside `:latest` — the fix is simply to pin to that tag
instead, so bumping the tag in Git IS the deploy mechanism (see #841).

What is enforced here
----------------------
1. `clusterAutoscaler.provider.image` in both `values.yaml` (default) and
   `values-contabo.yaml` (the overlay that actually runs this) is NOT
   `:latest` and matches the `sha-<hex>` shape the workflow publishes.
2. The `imageRollout` field/annotation is gone — with a real immutable tag,
   the image field itself is the rollout trigger; a leftover imageRollout
   annotation referencing a now-removed values field would be a broken
   template, not just dead weight.
3. The provider Deployment's `imagePullPolicy` is no longer hardcoded
   `Always` (that existed specifically to force a re-pull of a mutable
   `:latest` — an immutable tag doesn't need it).

Offline: reads YAML directly + (where helm is available) renders the
template. No cluster, no network, no GHCR lookup — this does not confirm the
pinned tag/digest actually exists in GHCR, which is the issue's own
"Optional" CI check and is not implemented here.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
PROVIDER_TEMPLATE = CHART / "templates" / "autoscaler" / "provider-deployment.yaml"

IMAGE_REPO = "ghcr.io/izzywdev/fuzeinfra-contabo-ca-provider"
# What docker/metadata-action's `type=sha` (default format) actually publishes.
IMMUTABLE_TAG_RE = re.compile(r"^sha-[0-9a-f]{7,40}$")


def _provider_image(values_path: Path) -> str:
    values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    return values["clusterAutoscaler"]["provider"]["image"]


@pytest.mark.parametrize("values_file", ["values.yaml", "values-contabo.yaml"])
def test_provider_image_is_pinned_not_latest(values_file):
    image = _provider_image(CHART / values_file)
    assert image.startswith(f"{IMAGE_REPO}:"), (
        f"{values_file}: clusterAutoscaler.provider.image={image!r} does not "
        f"even reference {IMAGE_REPO} — check for a typo."
    )
    tag = image.split(":", 1)[1]
    assert tag != "latest", (
        f"{values_file}: clusterAutoscaler.provider.image is pinned back to "
        f"':latest' — this is exactly the regression #841 fixed. A rebuild "
        f"would silently stop deploying again."
    )
    assert IMMUTABLE_TAG_RE.match(tag), (
        f"{values_file}: clusterAutoscaler.provider.image tag {tag!r} does not "
        f"look like an immutable ca-provider-image.yml `type=sha` tag "
        f"(expected sha-<commit-sha-prefix>)."
    )


def test_image_rollout_field_and_annotation_are_gone():
    template_text = PROVIDER_TEMPLATE.read_text(encoding="utf-8")
    assert "imageRollout" not in template_text, (
        "templates/autoscaler/provider-deployment.yaml still references "
        "imageRollout — with an immutable provider.image tag (#841) the "
        "image field itself is the rollout trigger; a dangling reference to "
        "a removed values field breaks the template."
    )
    for values_file in ("values.yaml", "values-contabo.yaml"):
        values_text = (CHART / values_file).read_text(encoding="utf-8")
        assert "imageRollout:" not in values_text, (
            f"{values_file} still declares provider.imageRollout — remove it, "
            f"it has no reader left once the checksum/provider-image "
            f"annotation is gone (#841)."
        )


def test_provider_image_pull_policy_not_hardcoded_always():
    template_text = PROVIDER_TEMPLATE.read_text(encoding="utf-8")
    assert "imagePullPolicy: Always" not in template_text, (
        "provider-deployment.yaml still hardcodes imagePullPolicy: Always — "
        "that existed only to force a re-pull of a mutable :latest tag "
        "(#841); an immutable per-build tag should use the chart's normal "
        "pull policy instead."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_rendered_deployment_uses_pinned_image_and_no_rollout_annotation():
    cmd = [
        "helm", "template", "fuzeinfra", str(CHART),
        "-n", "fuzeinfra",
        "-f", str(CHART / "values.yaml"),
        "-f", str(CHART / "values-contabo.yaml"),
        "--set", "clusterAutoscaler.enabled=true",
        "-s", "templates/autoscaler/provider-deployment.yaml",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    deployments = [d for d in docs if d.get("kind") == "Deployment"]
    assert len(deployments) == 1
    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]

    assert container["image"].startswith(f"{IMAGE_REPO}:sha-"), (
        f"rendered provider Deployment image is {container['image']!r}, "
        f"expected an immutable sha- tag."
    )
    assert not container["image"].endswith(":latest")

    annotations = deployments[0]["spec"]["template"]["metadata"].get(
        "annotations", {}
    )
    assert "checksum/provider-image" not in annotations, (
        "rendered Deployment still carries checksum/provider-image — that "
        "annotation's only job was forcing a roll for a mutable :latest tag "
        "and should be gone now that the image tag itself is immutable."
    )
