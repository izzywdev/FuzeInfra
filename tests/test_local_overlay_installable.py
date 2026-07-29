"""Regression guard: the local overlay must be installable on a bare kind cluster.

Why this file exists
--------------------
`helm/fuzeinfra/templates/autoscaler/sealed-secret-ca-provider.yaml` rendered a
`SealedSecret` with no `enabled` gate, so it appeared in EVERY overlay including
values-local. A fresh kind cluster has no Sealed Secrets CRD, so the very first
real `kind-validate` run died at install:

    Error: unable to build kubernetes objects from release manifest:
    resource mapping not found for name: "fuzeinfra-ca-provider" ...
    no matches for kind "SealedSecret" in version "bitnami.com/v1alpha1"
    ensure CRDs are installed first

The dangerous part is that nothing caught it for months. `kubeconform
-ignore-missing-schemas` — which is what helm-validate.yml runs — SKIPS resources
whose CRD schema it cannot find, so an uninstallable chart validated clean. The
skip was even visible in CI output as "Skipped: 1" and read as noise.

What is enforced
----------------
Every apiVersion rendered by values-local.yaml must belong to a group that a
`k8s/kind/setup-kind.sh` cluster actually has: core Kubernetes, or one of the
CRDs that script installs (cert-manager). Anything else means `helm install`
would fail on a developer's fresh cluster, and must be gated off for local.

Add to CRD_GROUPS_INSTALLED_LOCALLY only when setup-kind.sh genuinely installs
that CRD — the point of the list is to mirror reality, not to silence the test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
LOCAL_VALUES = CHART / "values-local.yaml"
SETUP_KIND = REPO_ROOT / "k8s" / "kind" / "setup-kind.sh"

#: Built-in Kubernetes API groups — always present, no CRD needed. "" is core/v1.
CORE_GROUPS = {
    "", "apps", "batch", "networking.k8s.io", "rbac.authorization.k8s.io",
    "policy", "autoscaling", "storage.k8s.io", "scheduling.k8s.io",
    "apiextensions.k8s.io", "coordination.k8s.io", "discovery.k8s.io",
    "admissionregistration.k8s.io", "apiregistration.k8s.io", "node.k8s.io",
    "flowcontrol.apiserver.k8s.io", "certificates.k8s.io", "events.k8s.io",
    "authentication.k8s.io", "authorization.k8s.io",
}

#: CRD groups setup-kind.sh installs before deploying the chart.
CRD_GROUPS_INSTALLED_LOCALLY = {
    "cert-manager.io",        # cert-manager + the fuzeinfra-local-ca ClusterIssuer
    "acme.cert-manager.io",
}


def _group(api_version: str) -> str:
    """`apps/v1` -> `apps`; `v1` -> `` (core)."""
    return api_version.split("/")[0] if "/" in api_version else ""


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_local_overlay_needs_no_uninstalled_crd():
    rendered = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(LOCAL_VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout

    offenders: dict[str, set[str]] = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc or "apiVersion" not in doc:
            continue
        group = _group(str(doc["apiVersion"]))
        if group in CORE_GROUPS or group in CRD_GROUPS_INSTALLED_LOCALLY:
            continue
        offenders.setdefault(
            f"{doc['apiVersion']} {doc.get('kind', '?')}", set()
        ).add((doc.get("metadata") or {}).get("name", "?"))

    assert not offenders, (
        "values-local.yaml renders resources whose CRDs a fresh kind cluster does "
        "not have, so `helm install` fails for any developer running `make dev`:\n"
        + "\n".join(f"  {k}: {sorted(v)}" for k, v in sorted(offenders.items()))
        + "\n\nGate these off for local (an `enabled` flag that values-local sets "
          "false), or install the CRD in k8s/kind/setup-kind.sh and add its group "
          "to CRD_GROUPS_INSTALLED_LOCALLY.\n"
          "NOTE: `kubeconform -ignore-missing-schemas` will NOT catch this — it "
          "silently skips resources with unknown schemas."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_sealed_secrets_are_prod_only():
    """Specific guard for the resource that actually broke it.

    Sealed Secrets are a prod/GitOps mechanism — the controller lives in the real
    cluster and holds the decryption key. A SealedSecret in the local overlay can
    never decrypt into anything useful even if the CRD were installed, so its
    presence there is always a mistake.
    """
    rendered = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(LOCAL_VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout
    names = [
        (doc.get("metadata") or {}).get("name")
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "SealedSecret"
    ]
    assert not names, (
        f"values-local.yaml renders SealedSecret(s) {names}. kind has no Sealed "
        "Secrets controller or CRD, so the install fails outright — and even with "
        "the CRD there is no key to decrypt them. Gate them to prod."
    )


def test_setup_kind_installs_the_crds_this_test_assumes():
    """Keep the allowlist honest: it must describe what setup-kind.sh really does.

    If someone drops the cert-manager install from setup-kind.sh, this list would
    silently keep permitting cert-manager resources that no longer have a CRD.
    """
    script = SETUP_KIND.read_text(encoding="utf-8")
    assert "cert-manager" in script, (
        "CRD_GROUPS_INSTALLED_LOCALLY allows cert-manager.io, but setup-kind.sh no "
        "longer installs cert-manager — the allowlist is now lying. Update both."
    )
