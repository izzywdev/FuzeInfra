from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/provision-fuzequality.yml"


def test_fuzequality_database_is_declared_but_disabled_by_default():
    values = yaml.safe_load((ROOT / "helm/fuzeinfra/values.yaml").read_text())
    allocation = next(item for item in values["serviceDatabases"] if item["name"] == "fuzequality")
    assert allocation == {
        "name": "fuzequality",
        "enabled": False,
        "role": "fuzequality_user",
        "database": "fuzequality",
        "passwordSecret": {"name": "fuzequality-db-credentials", "key": "password"},
    }


def test_bootstrap_workflow_is_ciphertext_only_and_strict_scoped():
    text = WORKFLOW.read_text()
    assert "kubectl apply" not in text
    assert text.count("kubeseal --scope strict") == 2
    assert "fuzeinfra/$DB_SECRET" in text
    assert "argocd.argoproj.io/sync-wave=-2" in text
    assert "FUZEQUALITY_API_TOKEN" in text
    assert 'REGISTRY_SECRET: ghcr-pull' in text
    assert 'role: fuzequality_user' in text
