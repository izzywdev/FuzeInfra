#!/usr/bin/env python3
"""Enable a declared service Postgres allocation from a bounded runtime request.

Consumer names belong in reviewed values/registry data, never in this workflow
or helper. This script only enables an already-declared disabled allocation and
its matching disabled credential handoff after validating the generated provider
SealedSecret. It cannot create shell commands or mutate the cluster.
"""
import json
import os
import re
from pathlib import Path

import yaml

NAME = re.compile(r"^[a-z][a-z0-9-]{0,38}$")
IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
REPO = re.compile(r"^izzywdev/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
PATH = re.compile(r"^deploy/[A-Za-z0-9._/-]+\.ya?ml$")


def request_from_env() -> dict:
    try:
        request = json.loads(os.environ["POSTGRES_PROVISION_REQUEST"])
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("POSTGRES_PROVISION_REQUEST must be JSON") from error
    required = {"service", "role", "database", "consumerRepo", "handoffId", "target"}
    if set(request) != required or not all(isinstance(request[key], str) for key in required - {"target"}):
        raise ValueError("request has an invalid shape")
    target = request["target"]
    if not isinstance(target, dict) or set(target) != {"namespace", "secretName", "manifestPath", "branch"}:
        raise ValueError("request target has an invalid shape")
    if not (NAME.fullmatch(request["service"]) and IDENT.fullmatch(request["role"])
            and IDENT.fullmatch(request["database"]) and NAME.fullmatch(request["handoffId"])
            and REPO.fullmatch(request["consumerRepo"]) and NAME.fullmatch(target["namespace"])
            and NAME.fullmatch(target["secretName"]) and PATH.fullmatch(target["manifestPath"])
            and BRANCH.fullmatch(target["branch"]) and ".." not in target["manifestPath"]):
        raise ValueError("request contains an invalid identifier")
    return request


def enable(root: Path, request: dict) -> None:
    service = request["service"]
    provider_secret = f"{service}-db-credentials"
    secret_path = root / "deploy/sealed-secrets" / f"{provider_secret}.yaml"
    secret = yaml.safe_load(secret_path.read_text())
    expected = {"name": provider_secret, "namespace": "fuzeinfra"}
    if (secret.get("kind") != "SealedSecret"
            or any(secret.get("metadata", {}).get(k) != v for k, v in expected.items())
            or not secret.get("spec", {}).get("encryptedData", {}).get("password")
            or secret.get("spec", {}).get("template", {}).get("data")
            or secret.get("spec", {}).get("template", {}).get("stringData")):
        raise ValueError("provider password must be strictly-scoped ciphertext")
    for metadata in (secret.get("metadata", {}), secret.get("spec", {}).get("template", {}).get("metadata", {})):
        if any(metadata.get("annotations", {}).get(key) == "true" for key in (
                "sealedsecrets.bitnami.com/cluster-wide", "sealedsecrets.bitnami.com/namespace-wide")):
            raise ValueError("provider ciphertext must be strictly scoped")

    values_path = root / "helm/fuzeinfra/values-contabo.yaml"
    values_text = values_path.read_text()
    values = yaml.safe_load(values_text)
    expected_allocation = {
        "name": service, "enabled": False, "role": request["role"], "database": request["database"],
        "passwordSecret": {"name": provider_secret, "key": "password"},
    }
    allocations = [value for value in values["serviceDatabases"] if value["name"] == service]
    if allocations != [expected_allocation]:
        raise ValueError("expected one matching disabled Postgres allocation")

    handoff_path = root / "governance/credential-handoff.json"
    handoff_text = handoff_path.read_text()
    registry = json.loads(handoff_text)
    entries = [entry for entry in registry["handoffs"] if entry["id"] == request["handoffId"]]
    target = request["target"]
    expected_handoff = {
        "consumerRepo": request["consumerRepo"],
        "source": {"namespace": "fuzeinfra", "secretName": provider_secret, "secretKey": "password"},
        "target": {"namespace": target["namespace"], "secretName": target["secretName"], "secretKey": "DATABASE_URL",
                   "format": "postgres-url", "manifestPath": target["manifestPath"], "branch": target["branch"]},
        "verify": {"engine": "postgres", "host": "fuzeinfra-postgres.fuzeinfra.svc.cluster.local", "port": 5432,
                   "database": request["database"], "username": request["role"]},
    }
    if len(entries) != 1 or entries[0].get("enabled") is not False or any(entries[0].get(key) != value for key, value in expected_handoff.items()):
        raise ValueError("expected one matching disabled credential handoff")

    allocation_gate = rf"(?m)^(  - name: {re.escape(service)}\n    enabled: )false$"
    handoff_gate = rf'("id": "{re.escape(request["handoffId"])}",\n      "enabled": )false'
    new_values, allocation_count = re.subn(allocation_gate, r"\g<1>true", values_text)
    new_handoff, handoff_count = re.subn(handoff_gate, r"\g<1>true", handoff_text)
    if allocation_count != 1 or handoff_count != 1:
        raise ValueError("expected exactly one disabled gate of each kind")
    values_path.write_text(new_values)
    handoff_path.write_text(new_handoff)


if __name__ == "__main__":
    enable(Path(__file__).resolve().parents[1], request_from_env())
