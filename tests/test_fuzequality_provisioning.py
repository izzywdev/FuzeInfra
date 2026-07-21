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
    assert 'POSTGRES_ROLE: fuzequality_user' in text


def test_bootstrap_uses_approved_registry_source_secret():
    text = WORKFLOW.read_text()
    assert 'REGISTRY_SOURCE_NAMESPACE: fuzefront' in text
    assert 'REGISTRY_SOURCE_SECRET: ghcr-pull\n' in text
    assert 'REGISTRY_SOURCE_SECRET: ghcr-pull-secret' not in text


def test_verification_requires_immutable_commit_tag():
    text = WORKFLOW.read_text()
    assert '"$image" == *:latest' in text
    assert '"$image" =~ :[0-9a-f]{12}$' in text
    assert 'image must use a non-latest 12-hex commit tag' in text
    assert 'case "$image" in *@sha256:*' not in text


def test_bootstrap_installs_verified_github_cli_on_runner():
    text = WORKFLOW.read_text()
    assert 'name: Install GitHub CLI' in text
    assert 'gh_${version}_checksums.txt' in text
    assert 'sha256sum -c -' in text
    assert 'gh --version' in text


def test_bootstrap_has_no_ruby_runner_dependency():
    text = WORKFLOW.read_text()
    assert "ruby -e" not in text
    assert "sed -i '/- name: fuzequality/{n;s/enabled: false/enabled: true/;}'" in text
    assert "fuzequality PostgreSQL gate remains disabled" in text`n    assert 'grep -c ''enabled: false''' in text
