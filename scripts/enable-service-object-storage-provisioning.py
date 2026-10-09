#!/usr/bin/env python3
"""Enable a reviewed S3-compatible allocation without handling its plaintext."""
import json
import os
import re
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
REPO = re.compile(r"^izzywdev/[A-Za-z0-9_.-]+$")

def request_from_env():
    try:
        request = json.loads(os.environ["OBJECT_STORAGE_PROVISION_REQUEST"])
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("OBJECT_STORAGE_PROVISION_REQUEST must be JSON") from error
    if set(request) != {"allocationId", "consumerRepo"} or not all(isinstance(v, str) for v in request.values()):
        raise ValueError("request has an invalid shape")
    if not NAME.fullmatch(request["allocationId"]) or not REPO.fullmatch(request["consumerRepo"]):
        raise ValueError("request contains an invalid identifier")
    return request

def enable(root: Path, request: dict):
    allocation_path = root / "governance/object-storage-allocations.json"
    allocation_text = allocation_path.read_text()
    allocations = json.loads(allocation_text)
    matches = [a for a in allocations["allocations"] if a["id"] == request["allocationId"]]
    if len(matches) != 1 or matches[0]["enabled"] is not False:
        raise ValueError("expected one matching disabled object-storage allocation")
    allocation = matches[0]
    # The workflow never receives a provider credential. A reviewed, strictly
    # scoped SealedSecret source must already be present before a handoff can
    # become deliverable; otherwise enabling would create a broken allocation.
    if not (root / allocation["sourceManifest"]).is_file():
        raise ValueError("object-storage source credential has not been sealed by FuzeInfra")
    handoff_path = root / "governance/credential-handoff.json"
    handoff_text = handoff_path.read_text()
    registry = json.loads(handoff_text)
    entries = [h for h in registry["handoffs"] if h["id"] == allocation["handoffId"]]
    if len(entries) != 1 or entries[0].get("enabled") is not False:
        raise ValueError("expected one matching disabled credential handoff")
    handoff = entries[0]
    if handoff.get("consumerRepo") != request["consumerRepo"] or handoff.get("source") != allocation["source"]:
        raise ValueError("allocation and credential handoff do not match")
    needle = '"id": "' + allocation["id"] + '",\n      "enabled": false'
    replacement = '"id": "' + allocation["id"] + '",\n      "enabled": true'
    allocation_text, count_a = allocation_text.replace(needle, replacement), allocation_text.count(needle)
    handoff_needle = '"id": "' + allocation["handoffId"] + '",\n      "enabled": false'
    handoff_replacement = '"id": "' + allocation["handoffId"] + '",\n      "enabled": true'
    handoff_text, count_h = handoff_text.replace(handoff_needle, handoff_replacement), handoff_text.count(handoff_needle)
    if count_a != 1 or count_h != 1:
        raise ValueError("expected exactly one disabled allocation and handoff gate")
    allocation_path.write_text(allocation_text)
    handoff_path.write_text(handoff_text)

if __name__ == "__main__":
    enable(Path(__file__).resolve().parents[1], request_from_env())
