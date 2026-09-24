"""Regression guard for M3-S3: elasticsearch/chromadb must never get a public host again.

Why this file exists
---------------------
`elasticsearch.prod.fuzefront.com` and `chromadb.prod.fuzefront.com` used to publish
raw datastore HTTP APIs to the internet, guarded only by the `*.prod` Cloudflare
Access wildcard (email-OTP) — no other authorization layer in front of a datastore
API. M3-S3 removed both routes from `helm/fuzeinfra/templates/ingress.yaml` and both
tiles from `terraform/contabo/cloudflare.tf`'s `launcher_services`. Neo4j Browser is
the deliberate, owner-approved exception and keeps its own dedicated Ingress
(`neo4j-ingress.yaml`).

This test asserts BOTH directions on every overlay:
  1. elasticsearch/chromadb never appear as a rendered Ingress host (the hardening).
  2. neo4j DOES still appear as a rendered Ingress host (so a test that only checked
     removal couldn't pass by accident if someone deleted every route, including
     the one that must stay).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
OVERLAYS = ["values.yaml", "values-local.yaml", "values-aws.yaml", "values-contabo.yaml"]

FORBIDDEN_HOST_SUBSTRINGS = ("elasticsearch.", "chromadb.")
REQUIRED_HOST_SUBSTRING = "neo4j."


def _render(overlay: str) -> list[dict]:
    cmd = ["helm", "template", "fuzeinfra", str(CHART), "-n", "fuzeinfra"]
    if overlay != "values.yaml":
        cmd += ["-f", str(CHART / overlay)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def _ingress_hosts(docs: list[dict]) -> list[str]:
    hosts: list[str] = []
    for doc in docs:
        if doc.get("kind") != "Ingress":
            continue
        for rule in doc.get("spec", {}).get("rules") or []:
            host = rule.get("host")
            if isinstance(host, str):
                hosts.append(host)
    return hosts


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_elasticsearch_and_chromadb_publish_no_ingress_host(overlay):
    """The hardening: neither datastore may appear as a public Ingress host."""
    hosts = _ingress_hosts(_render(overlay))

    for forbidden in FORBIDDEN_HOST_SUBSTRINGS:
        offenders = [h for h in hosts if forbidden in h]
        assert not offenders, (
            f"{overlay}: found Ingress host(s) {offenders} containing {forbidden!r}. "
            f"elasticsearch and chromadb are raw datastore HTTP APIs and must stay "
            f"ClusterIP-only (M3-S3) — see helm/fuzeinfra/templates/ingress.yaml."
        )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_neo4j_still_publishes_an_ingress_host(overlay):
    """The negative-control: neo4j Browser must NOT have been swept up by the removal."""
    hosts = _ingress_hosts(_render(overlay))

    matches = [h for h in hosts if REQUIRED_HOST_SUBSTRING in h]
    assert matches, (
        f"{overlay}: expected at least one Ingress host containing "
        f"{REQUIRED_HOST_SUBSTRING!r} (Neo4j Browser keeps its Ingress per the M3-S3 "
        f"owner decision — see neo4j-ingress.yaml), found none among {hosts}. "
        f"A test that only checks removal would pass even if every route were "
        f"accidentally deleted; this direction catches that."
    )
