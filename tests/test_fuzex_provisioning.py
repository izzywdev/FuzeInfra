"""Exercise bootstrap gates without ever generating a real credential."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("enable_fuzex", ROOT / "scripts/enable-fuzex-provisioning.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def checkout(tmp_path):
    for name in ("helm/fuzeinfra/values-contabo.yaml", "governance/credential-handoff.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, target)
    sealed = tmp_path / "deploy/sealed-secrets/fuzex-db-credentials.yaml"
    sealed.parent.mkdir(parents=True)
    sealed.write_text(yaml.safe_dump({
        "kind": "SealedSecret",
        "metadata": {"namespace": "fuzeinfra", "name": "fuzex-db-credentials"},
        "spec": {"encryptedData": {"password": "fixture-ciphertext"}},
    }))
    return tmp_path


def test_enables_both_gates_without_changing_other_allocations(checkout):
    values_path = checkout / "helm/fuzeinfra/values-contabo.yaml"
    registry_path = checkout / "governance/credential-handoff.json"
    old_values = yaml.safe_load(values_path.read_text())
    old_registry = json.loads(registry_path.read_text())
    module.enable(checkout)
    expected_values = old_values
    next(v for v in expected_values["serviceDatabases"] if v["name"] == "fuzex")["enabled"] = True
    next(v for v in old_registry["handoffs"] if v["id"] == "fuzex-postgres")["enabled"] = True
    assert yaml.safe_load(values_path.read_text()) == expected_values
    assert json.loads(registry_path.read_text()) == old_registry
    with pytest.raises(ValueError, match="refusing rotation"):
        module.enable(checkout)


@pytest.mark.parametrize("mutation", ["wrong-namespace", "missing-ciphertext", "broad-scope", "wrong-target", "missing-secret"])
def test_rejects_invalid_bootstrap_without_touching_gates(checkout, mutation):
    secret_path = checkout / "deploy/sealed-secrets/fuzex-db-credentials.yaml"
    secret = yaml.safe_load(secret_path.read_text())
    registry_path = checkout / "governance/credential-handoff.json"
    if mutation == "wrong-namespace":
        secret["metadata"]["namespace"] = "fuzex"
    elif mutation == "missing-ciphertext":
        secret["spec"]["encryptedData"] = {}
    elif mutation == "broad-scope":
        secret["metadata"]["annotations"] = {"sealedsecrets.bitnami.com/cluster-wide": "true"}
    elif mutation == "wrong-target":
        registry = json.loads(registry_path.read_text())
        next(v for v in registry["handoffs"] if v["id"] == "fuzex-postgres")["consumerRepo"] = "izzywdev/another-app"
        registry_path.write_text(json.dumps(registry))
    secret_path.write_text(yaml.safe_dump(secret))
    if mutation == "missing-secret":
        secret_path.unlink()
    values_path = checkout / "helm/fuzeinfra/values-contabo.yaml"
    before = (values_path.read_text(), registry_path.read_text())
    with pytest.raises((ValueError, FileNotFoundError)):
        module.enable(checkout)
    assert (values_path.read_text(), registry_path.read_text()) == before


def test_workflow_has_no_arbitrary_inputs_cluster_credentials_or_shell_syntax_errors():
    workflow = yaml.safe_load((ROOT / ".github/workflows/provision-fuzex.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"workflow_dispatch": None}
    for step in workflow["jobs"]["provision"]["steps"]:
        script = step.get("run", "")
        assert "kubectl" not in script
        assert "KUBE_CONFIG" not in str(step)
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_no_mongo_allocation_is_added():
    for name in ("values.yaml", "values-contabo.yaml"):
        values = yaml.safe_load((ROOT / "helm/fuzeinfra" / name).read_text())
        assert all(v["name"] != "fuzex" for v in values.get("serviceMongoDatabases", []))
