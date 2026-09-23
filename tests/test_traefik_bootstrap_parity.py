"""The two Traefik HelmChartConfigs must not drift apart.

k3s's bundled Traefik is configured from TWO places, applied by different paths:

  * ``terraform/contabo/provisioning.tf`` — a heredoc literal inside the cloud-init
    script, applied ONCE at first boot of a newly provisioned node.
  * ``argocd/cluster-bootstrap/traefik-clusterip.yaml`` — re-applied by
    ``apply-cluster-config.yml`` on every push to ``main``.

Whichever ran last wins, so divergence is not a style problem, it is a live
configuration difference with an unbounded window. A fresh node runs whatever the
cloud-init literal says until the next push to main happens to reconcile it.

This guard exists because that divergence was real: the manifest was given
``deployment.replicas: 2`` + a PDB + soft anti-affinity to remove a single-replica
ingress SPOF (confirmed live as ``kube-system traefik 1/1``), while the cloud-init
literal still said only ``service.type: ClusterIP``. Every newly provisioned node
would therefore have come up as exactly the SPOF the manifest exists to remove.

The repo's own precedent is to duplicate HA settings into the bootstrap literal —
the CoreDNS block a few lines below Traefik's in ``provisioning.tf`` carries
``replicaCount: 2`` and its anti-affinity rule for the same reason.

Compares PARSED values, not text, so formatting/indentation differences between a
Terraform string list and a YAML file are not false positives.

Offline: reads two files. No cluster, no network, no Terraform binary.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
PROVISIONING = REPO / "terraform" / "contabo" / "provisioning.tf"
MANIFEST = REPO / "argocd" / "cluster-bootstrap" / "traefik-clusterip.yaml"

# The cloud-init heredoc: "kubectl apply -f - <<'HELMCFG'", "<line>", ..., "HELMCFG",
_HEREDOC = re.compile(
    r'"kubectl apply -f - <<\'HELMCFG\'",\n(.*?)\s*"HELMCFG",', re.S
)
_QUOTED_LINE = re.compile(r'^\s*"(.*)",\s*$', re.M)


def _bootstrap_doc() -> dict:
    """The HelmChartConfig as the cloud-init script would apply it."""
    body = _HEREDOC.search(PROVISIONING.read_text(encoding="utf-8"))
    assert body, (
        "Traefik HELMCFG heredoc not found in provisioning.tf — if the bootstrap "
        "block was renamed or restructured, update this guard rather than deleting it."
    )
    lines = [m.replace('\\"', '"') for m in _QUOTED_LINE.findall(body.group(1))]
    return yaml.safe_load("\n".join(lines))


def _manifest_doc() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_both_sources_target_the_same_object():
    boot, man = _bootstrap_doc(), _manifest_doc()
    for doc, where in ((boot, "provisioning.tf"), (man, MANIFEST.name)):
        assert doc["apiVersion"] == "helm.cattle.io/v1", where
        assert doc["kind"] == "HelmChartConfig", where
        assert doc["metadata"]["name"] == "traefik", where
        assert doc["metadata"]["namespace"] == "kube-system", where


def test_values_are_identical():
    boot = yaml.safe_load(_bootstrap_doc()["spec"]["valuesContent"])
    man = yaml.safe_load(_manifest_doc()["spec"]["valuesContent"])
    assert boot == man, (
        "The cloud-init Traefik literal and argocd/cluster-bootstrap/"
        "traefik-clusterip.yaml have diverged. A freshly provisioned node will run "
        f"the cloud-init version until the next push to main.\n"
        f"cloud-init: {boot}\nmanifest:   {man}"
    )


@pytest.mark.parametrize("source", ["bootstrap", "manifest"])
def test_ha_and_clusterip_invariants_hold(source):
    """The properties that made this change worth making, asserted on BOTH sources.

    Parity alone would be satisfied by both sides being wrong together.
    """
    doc = _bootstrap_doc() if source == "bootstrap" else _manifest_doc()
    values = yaml.safe_load(doc["spec"]["valuesContent"])

    # The tunnel-only invariant: k3s servicelb must never bind hostPorts 80/443.
    assert values["service"]["type"] == "ClusterIP", source

    # The HA invariant this change introduced.
    assert values["deployment"]["replicas"] >= 2, source
    assert values["podDisruptionBudget"]["enabled"] is True, source
    assert values["podDisruptionBudget"]["minAvailable"] >= 1, source

    # Anti-affinity must stay SOFT: a hard rule could leave the second replica
    # permanently Pending on this small durable node pool, turning HA into a
    # capacity outage.
    anti = values["affinity"]["podAntiAffinity"]
    assert "preferredDuringSchedulingIgnoredDuringExecution" in anti, source
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in anti, (
        f"{source}: anti-affinity must be soft, not required"
    )
