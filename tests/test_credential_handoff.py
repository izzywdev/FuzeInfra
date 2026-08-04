"""Offline guards for the FuzeInfra → consumer credential hand-off (#510).

Everything here runs with no cluster, no network and no credentials. The point is
to pin the three properties the hand-off depends on:

1. The registry is the ONLY source of a source→target mapping, and it is
   well-formed. A malformed or ambiguous entry must fail here, not in a run that
   seals a credential for the wrong namespace.
2. No plaintext can escape. The rendering helpers are exercised for real, and the
   script is checked for the specific footguns (a password in ``argv``, a Secret
   value echoed) that this whole design exists to avoid.
3. The ``cluster-query`` Secret-read guard is still intact. This change replaces
   the channel that guard removed; it must never be the change that quietly
   re-opens it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "governance" / "credential-handoff.json"
SCHEMA = REPO / "governance" / "credential-handoff.schema.json"
SCRIPT = REPO / "scripts" / "credential-handoff.sh"
SEAL = REPO / "scripts" / "seal-secret.sh"
CLUSTER_QUERY = REPO / ".github" / "workflows" / "cluster-query.yml"
PUBLISH_WF = REPO / ".github" / "workflows" / "publish-sealed-handoff.yml"
VERIFY_WF = REPO / ".github" / "workflows" / "verify-consumer-credentials.yml"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- registry ---

def test_registry_matches_schema(registry):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    # $schema is a self-reference for editors; the schema itself allows it.
    jsonschema.validate(instance=registry, schema=schema)


def test_ids_are_unique(registry):
    ids = [h["id"] for h in registry["handoffs"]]
    assert len(ids) == len(set(ids)), f"duplicate hand-off id in {REGISTRY.name}: {ids}"


def test_target_keys_are_unique(registry):
    """Two hand-offs writing the same Secret key would silently overwrite each
    other on alternate runs, producing a credential that flaps."""
    targets = [
        (h["target"]["namespace"], h["target"]["secretName"], h["target"]["secretKey"])
        for h in registry["handoffs"]
    ]
    assert len(targets) == len(set(targets)), f"two hand-offs share a target key: {targets}"


def test_postgres_url_entries_carry_a_complete_verify_block(registry):
    """format 'postgres-url' composes the DSN out of the verify block, so an
    incomplete block would render a broken URL instead of failing."""
    for h in registry["handoffs"]:
        if h["target"]["format"] != "postgres-url":
            continue
        v = h.get("verify")
        assert v, f"{h['id']}: postgres-url requires a verify block"
        for field in ("engine", "host", "port", "database", "username"):
            assert v.get(field), f"{h['id']}: verify.{field} is required for postgres-url"


def test_manifest_paths_are_repo_relative(registry):
    for h in registry["handoffs"]:
        path = h["target"]["manifestPath"]
        assert not path.startswith("/"), f"{h['id']}: manifestPath must be repo-relative"
        assert ".." not in path, f"{h['id']}: manifestPath must not traverse upward"


# ------------------------------------------------------------ no plaintext ---

def test_script_never_puts_a_password_in_argv():
    """argv is visible in `ps` and in shell history. The password must reach psql
    through PGPASSWORD and reach kubeseal through a file reference."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "PGPASSWORD=" in body, "psql must receive the password through the environment"
    assert "--password" not in body
    # seal-secret.sh's KEY=@file form keeps the value out of argv; a bare KEY=$var
    # would not.
    assert '"${t_key}=@${d}/want"' in body, "kubeseal input must be passed by file reference"


def test_sealing_is_strict_scoped():
    """A strict-scoped value only decrypts under one namespace + name. That
    binding is the security property the whole hand-off rests on."""
    assert "--scope strict" in SEAL.read_text(encoding="utf-8")


def test_script_does_not_echo_secret_files():
    body = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ('echo "$(cat', "cat \"$d/have\"", "cat \"$d/src\"", "cat \"$d/want\""):
        assert forbidden not in body, f"{forbidden!r} would print a credential"


@needs_bash
def test_script_syntax_is_valid():
    proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@needs_bash
def test_postgres_url_round_trip(tmp_path):
    """Compose a DSN and recover the password from it. If these two disagree the
    verifier would authenticate with the wrong string and alert on a healthy
    credential (or, worse, stay quiet on a broken one)."""
    pw = tmp_path / "pw"
    pw.write_text("AbC123xyzPassword", encoding="utf-8")
    script = f"""
set -euo pipefail
source '{SCRIPT.as_posix()}'
render_target postgres-url '{pw.as_posix()}' mendys_svc fuzeinfra-postgres.fuzeinfra 5432 mendys '{(tmp_path / "url").as_posix()}'
password_from_target postgres-url '{(tmp_path / "url").as_posix()}' '{(tmp_path / "back").as_posix()}'
"""
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "back").read_text(encoding="utf-8") == "AbC123xyzPassword"
    url = (tmp_path / "url").read_text(encoding="utf-8")
    assert url == "postgresql://mendys_svc:AbC123xyzPassword@fuzeinfra-postgres.fuzeinfra:5432/mendys"
    # ...and nothing leaked to the console on the way.
    assert "AbC123xyzPassword" not in proc.stdout
    assert "AbC123xyzPassword" not in proc.stderr


@needs_bash
def test_postgres_url_refuses_a_password_needing_url_encoding(tmp_path):
    """An unencoded '@' or '/' silently truncates the DSN — an auth failure that
    would only surface weeks later. Fail at compose time instead."""
    pw = tmp_path / "pw"
    pw.write_text("bad@pass/word", encoding="utf-8")
    script = f"""
set -uo pipefail
source '{SCRIPT.as_posix()}'
render_target postgres-url '{pw.as_posix()}' u svc.ns 5432 db '{(tmp_path / "url").as_posix()}'
"""
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "URL-encoding" in proc.stderr
    assert "bad@pass/word" not in proc.stderr


@needs_bash
def test_unknown_id_is_an_error_not_a_no_op():
    """A typo'd id must not look exactly like 'nothing to do'."""
    proc = subprocess.run(
        [BASH, str(SCRIPT), "verify", "--id", "definitely-not-a-handoff"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode != 0
    assert "no hand-off with id" in proc.stderr


@needs_bash
def test_list_reports_every_registry_entry(registry):
    proc = subprocess.run(
        [BASH, str(SCRIPT), "list"], capture_output=True, text=True, cwd=str(REPO)
    )
    assert proc.returncode == 0, proc.stderr
    rows = [r for r in proc.stdout.splitlines() if r.strip()]
    assert len(rows) == len(registry["handoffs"])
    for row, h in zip(rows, registry["handoffs"]):
        assert row.split("\t")[0] == h["id"]


# ------------------------------------------- the guard this must not weaken ---

def test_cluster_query_still_rejects_reading_secrets():
    """#510 exists BECAUSE this guard is correct. Replacing the channel it
    removed must never be the change that quietly re-opens it."""
    body = CLUSTER_QUERY.read_text(encoding="utf-8")
    assert "secret|secrets)" in body, "the Secret-resource rejection case is gone"
    assert "Rejected: reading Secret objects" in body
    assert "--raw|--raw=*)" in body, "the --raw rejection is gone"


def test_handoff_workflows_do_not_reintroduce_a_logged_secret_read():
    """The publisher and verifier read Secrets — that is the point — but they must
    never render one into the job log."""
    for wf in (PUBLISH_WF, VERIFY_WF):
        body = wf.read_text(encoding="utf-8")
        assert "credential-handoff.sh" in body
        # Comments describe the old broken channel on purpose; only executable
        # lines are checked.
        code = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        for line in code:
            assert "get secret" not in line, f"{wf.name} must delegate Secret reads to the script: {line!r}"
            # -o jsonpath on a Namespace is fine; -o json on a Secret is what
            # dumped a credential into a public log in the first place.
            assert not re.search(r"-o json(?!path)", line), f"{wf.name}: {line!r}"


def test_workflows_are_valid_yaml():
    yaml = pytest.importorskip("yaml")
    for wf in (PUBLISH_WF, VERIFY_WF):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        assert "jobs" in doc, f"{wf.name} has no jobs"
        # `on:` parses as the boolean True in YAML 1.1.
        assert True in doc or "on" in doc, f"{wf.name} has no triggers"
