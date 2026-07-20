"""Runtime proof of Chroma 0.5.23 tenant isolation with the rendered provider."""

import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[2]
IMAGE = (
    "chromadb/chroma:0.5.23@sha256:"
    "18e67eecc172abbcd9413d751bde64983b3d167fe497c98f979083eb24c0c942"
)


def run(
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True, env=env)


def rendered_provider() -> str:
    rendered = run("helm", "template", "runtime-test", str(ROOT / "helm/fuzeinfra")).stdout
    for document in yaml.safe_load_all(rendered):
        if (
            isinstance(document, dict)
            and document.get("kind") == "ConfigMap"
            and document.get("metadata", {}).get("name") == "fuzeinfra-chromadb-authz"
        ):
            return document["data"]["fuzeinfra_chroma_authz.py"]
    raise AssertionError("rendered Chroma authorization provider not found")


@pytest.mark.integration
def test_scoped_chroma_clients_can_crud_but_cannot_cross_tenants(tmp_path: Path):
    """Exercise real HTTP clients against the exact production image digest."""
    if not shutil.which("docker") or not shutil.which("helm"):
        pytest.skip("docker and helm are required")

    admin_token, token_a, token_b = (secrets.token_hex(24) for _ in range(3))
    provider_file = tmp_path / "fuzeinfra_chroma_authz.py"
    provider_file.write_text(rendered_provider())
    credentials_file = tmp_path / "credentials.yaml"
    credentials_file.write_text(
        "users:\n"
        f"  - id: fuzeinfra-chroma-admin\n    role: admin\n    tenant: '*'\n"
        f"    databases: ['*']\n    tokens: ['{admin_token}']\n"
        f"  - id: app-a\n    role: write\n    tenant: tenant-a\n"
        f"    databases: [database-a]\n    tokens: ['{token_a}']\n"
        f"  - id: app-b\n    role: write\n    tenant: tenant-b\n"
        f"    databases: [database-b]\n    tokens: ['{token_b}']\n"
    )

    name = f"fuzeinfra-chroma-auth-{secrets.token_hex(6)}"
    container = run(
        "docker", "run", "--detach", "--rm", "--name", name,
        "--publish", "127.0.0.1::8000",
        "--volume", f"{provider_file}:/auth-provider/fuzeinfra_chroma_authz.py:ro",
        "--volume", f"{credentials_file}:/auth/credentials.yaml:ro",
        "--env", "IS_PERSISTENT=false",
        "--env", "ALLOW_RESET=false",
        "--env", "CHROMA_SERVER_AUTHN_PROVIDER=chromadb.auth.token_authn.TokenAuthenticationServerProvider",
        "--env", "CHROMA_SERVER_AUTHN_CREDENTIALS_FILE=/auth/credentials.yaml",
        "--env", "CHROMA_SERVER_AUTHZ_PROVIDER=fuzeinfra_chroma_authz.TenantDatabaseAuthorizationProvider",
        "--env", "PYTHONPATH=/auth-provider",
        "--env", "CHROMA_AUTH_TOKEN_TRANSPORT_HEADER=Authorization",
        "--env", "CHROMA_OVERWRITE_SINGLETON_TENANT_DATABASE_ACCESS_FROM_AUTH=true",
        IMAGE,
    ).stdout.strip()
    try:
        ports = json.loads(run("docker", "inspect", name, "--format", "{{json .NetworkSettings.Ports}}").stdout)
        port = int(ports["8000/tcp"][0]["HostPort"])
        heartbeat = f"http://127.0.0.1:{port}/api/v2/heartbeat"
        for _ in range(60):
            try:
                with urllib.request.urlopen(heartbeat, timeout=1):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise AssertionError(f"Chroma did not become ready: {run('docker', 'logs', name).stderr}")

        client_script = r'''
import os
import uuid
import httpx
import chromadb
from chromadb.config import Settings

host, port = "127.0.0.1", int(os.environ["PORT"])
def settings(token):
    return Settings(chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider", chroma_client_auth_credentials=token)

admin_settings = settings(os.environ["ADMIN_TOKEN"])
admin_settings.chroma_api_impl = "chromadb.api.fastapi.FastAPI"
admin_settings.chroma_server_host = host
admin_settings.chroma_server_http_port = port
admin = chromadb.AdminClient(admin_settings)
for tenant, database in (("tenant-a", "database-a"), ("tenant-b", "database-b")):
    admin.create_tenant(tenant)
    admin.create_database(database, tenant=tenant)

a = chromadb.HttpClient(host=host, port=port, tenant="tenant-a", database="database-a", settings=settings(os.environ["TOKEN_A"]))
b = chromadb.HttpClient(host=host, port=port, tenant="tenant-b", database="database-b", settings=settings(os.environ["TOKEN_B"]))
name_a, name_b = "runtime_a_" + uuid.uuid4().hex[:8], "runtime_b_" + uuid.uuid4().hex[:8]
collection_a = a.create_collection(name_a, embedding_function=None)
collection_b = b.create_collection(name_b, embedding_function=None)

# Chroma 0.5.23 emits default_tenant/default_database for these Collection
# operations. The rendered provider must resolve and authorize their UUIDs.
collection_a.upsert(ids=["one"], embeddings=[[0.1, 0.2]])
assert collection_a.get(ids=["one"])["ids"] == ["one"]
assert name_a in {collection.name for collection in a.list_collections()}

try:
    foreign = chromadb.HttpClient(host=host, port=port, tenant="tenant-b", database="database-b", settings=settings(os.environ["TOKEN_A"]))
    foreign.list_collections()
except Exception:
    pass
else:
    raise AssertionError("cross-tenant list unexpectedly succeeded")

# Prove that even a known foreign UUID cannot exploit the broken default path.
response = httpx.post(
    f"http://{host}:{port}/api/v2/tenants/default_tenant/databases/default_database/collections/{collection_b.id}/upsert",
    headers={"Authorization": "Bearer " + os.environ["TOKEN_A"]},
    json={"ids": ["stolen"], "embeddings": [[0.3, 0.4]]},
)
assert response.status_code == 403, response.text

collection_a.delete(ids=["one"])
assert collection_a.get(ids=["one"])["ids"] == []
a.delete_collection(name_a)
b.delete_collection(name_b)
assert name_a not in {collection.name for collection in a.list_collections()}
'''
        client_env = os.environ.copy()
        client_env.update(
            PORT=str(port),
            ADMIN_TOKEN=admin_token,
            TOKEN_A=token_a,
            TOKEN_B=token_b,
        )
        run(
            "docker", "run", "--rm", "--network", "host",
            "--entrypoint", "python",
            "--env", "PORT",
            "--env", "ADMIN_TOKEN",
            "--env", "TOKEN_A",
            "--env", "TOKEN_B",
            IMAGE, "-c", client_script,
            env=client_env,
        )
    finally:
        run("docker", "rm", "--force", container, check=False)
