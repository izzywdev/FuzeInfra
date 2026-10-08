#!/usr/bin/env python3
"""Enable only the approved FuzeX allocation after its sealed input exists."""
import json
import re
from pathlib import Path

import yaml


def enable(root: Path) -> None:
    secret_path = root / "deploy/sealed-secrets/fuzex-db-credentials.yaml"
    secret = yaml.safe_load(secret_path.read_text())
    expected = {"name": "fuzex-db-credentials", "namespace": "fuzeinfra"}
    if (secret.get("kind") != "SealedSecret"
            or any(secret.get("metadata", {}).get(k) != v for k, v in expected.items())
            or not secret.get("spec", {}).get("encryptedData", {}).get("password")
            or secret.get("spec", {}).get("template", {}).get("data")
            or secret.get("spec", {}).get("template", {}).get("stringData")):
        raise ValueError("FuzeX requires provider-scoped password ciphertext")
    for metadata in (secret.get("metadata", {}), secret.get("spec", {}).get("template", {}).get("metadata", {})):
        if any(metadata.get("annotations", {}).get(key) == "true" for key in (
                "sealedsecrets.bitnami.com/cluster-wide", "sealedsecrets.bitnami.com/namespace-wide")):
            raise ValueError("FuzeX ciphertext must be strictly scoped")

    values_path = root / "helm/fuzeinfra/values-contabo.yaml"
    values_text = values_path.read_text()
    values = yaml.safe_load(values_text)
    allocations = [v for v in values["serviceDatabases"] if v["name"] == "fuzex"]
    expected_allocation = {
        "name": "fuzex", "enabled": False, "role": "fuzex_svc",
        "database": "fuzex_design_frames",
        "passwordSecret": {"name": "fuzex-db-credentials", "key": "password"},
    }
    if allocations != [expected_allocation]:
        raise ValueError("Expected one disabled approved FuzeX allocation; refusing rotation")

    handoff_path = root / "governance/credential-handoff.json"
    handoff_text = handoff_path.read_text()
    registry = json.loads(handoff_text)
    entries = [h for h in registry["handoffs"] if h["id"] == "fuzex-postgres"]
    if len(entries) != 1 or entries[0]["enabled"] is not False:
        raise ValueError("Expected one disabled FuzeX credential handoff")
    entry = entries[0]
    if (entry["consumerRepo"] != "izzywdev/FuzeX"
            or entry["source"] != {"namespace": "fuzeinfra", "secretName": "fuzex-db-credentials", "secretKey": "password"}
            or entry["target"] != {"namespace": "fuzex", "secretName": "fuzex-design-frames-db", "secretKey": "DATABASE_URL", "format": "postgres-url", "manifestPath": "deploy/helm/fuzex/files/secrets/design-frames-db-sealed.yaml", "branch": "master"}
            or entry["verify"] != {"engine": "postgres", "host": "fuzeinfra-postgres.fuzeinfra.svc.cluster.local", "port": 5432, "database": "fuzex_design_frames", "username": "fuzex_svc"}):
        raise ValueError("FuzeX credential destination differs from the approved contract")

    new_values, count = re.subn(r"(?m)^(  - name: fuzex\n    enabled: )false$", r"\g<1>true", values_text)
    new_handoff, handoff_count = re.subn(r'("id": "fuzex-postgres",\n      "enabled": )false', r"\g<1>true", handoff_text)
    if count != 1 or handoff_count != 1:
        raise ValueError("Expected exactly one gate of each kind")
    # Validate every input before changing either tracked file.
    values_path.write_text(new_values)
    handoff_path.write_text(new_handoff)


if __name__ == "__main__":
    enable(Path(__file__).resolve().parents[1])
