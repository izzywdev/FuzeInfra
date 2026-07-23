"""Static regression tests for authenticated Chroma Helm provisioning."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_chroma_image_is_version_pinned():
    values = (ROOT / "helm/fuzeinfra/values.yaml").read_text()
    assert "image: chromadb/chroma:0.5.23@sha256:18e67eecc172abbcd9413d751bde64983b3d167fe497c98f979083eb24c0c942" in values
    assert "image: chromadb/chroma:latest" not in values


def test_chroma_provisioning_keeps_security_checks():
    template = (ROOT / "helm/fuzeinfra/templates/service-chroma-provisioning.yaml").read_text()
    for required in (
        "credentialSecret",
        "tenant",
        "database",
        "NetworkPolicy",
        "probe.upsert",
        "client.list_collections",
        "foreign.list_collections",
        "cross_tenant_must_be_denied",
        "SECURITY FAILURE",
        "must be 3-63 characters",
    ):
        assert required in template

    values = (ROOT / "helm/fuzeinfra/values-contabo.yaml").read_text()
    assert "_repo_digester_ready" not in values
    assert "_fuzequality_ready" not in values
    assert "repo_digester_ready" in values
    assert "fuzequality_ready" in values


def test_chroma_server_requires_auth_and_disables_reset():
    template = (ROOT / "helm/fuzeinfra/templates/databases.yaml").read_text()
    assert "TokenAuthenticationServerProvider" in template
    assert "TenantDatabaseAuthorizationProvider" in template
    assert 'name: ALLOW_RESET\n              value: "false"' in template

    provider = (ROOT / "helm/fuzeinfra/templates/chroma-authz.yaml").read_text()
    assert "self._sysdb.get_collections" in provider
    assert "resource_tenant == user.tenant" in provider
    assert "resource_database == user.databases[0]" in provider


def test_chroma_tokens_are_normalized_at_secret_boundaries():
    server = (ROOT / "helm/fuzeinfra/templates/databases.yaml").read_text()
    provisioner = (ROOT / "helm/fuzeinfra/templates/service-chroma-provisioning.yaml").read_text()

    assert "value = os.environ[name].strip()" in server
    assert "printable, non-whitespace ASCII characters only" in server
    assert 'os.environ["CHROMA_ADMIN_TOKEN"].strip()' in provisioner
    assert '].strip()' in provisioner
