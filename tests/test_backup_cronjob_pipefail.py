"""Regression guard: every backup CronJob shell pipeline must be pipefail-safe
and must refuse to upload an undersized/corrupt dump.

Why this file exists
---------------------
`helm/fuzeinfra/templates/backup-cronjobs.yaml` pipes each database's native
dump tool into `gzip -c`. Without `set -o pipefail`, a shell pipeline's exit
status is the LAST command's — and `gzip` exits 0 on empty stdin, emitting a
valid ~20-byte gzip header. So a failed `pg_dump`/`mongodump`/`mariadb-dump`/
APOC export (bad credentials, unreachable database, mid-dump disconnect) let
the Job report Complete while uploading nothing restorable
(fuzeinfra-backup-mariadb shipped exactly this: a 20-byte `.sql.gz`, green).

What is enforced here
----------------------
1. Every `/bin/sh -c` script embedded in a backup CronJob (dump init
   containers AND upload containers, both sinks) opens with
   `set -euo pipefail` (or at least has `pipefail` set) — a bare `set -eu`
   is a regression.
2. Each dump init container asserts a minimum byte size on the produced
   artifact and refuses to write out its `remote-key` marker /
   proceed to upload below that floor, and each also verifies the artifact
   with `gzip -t` before declaring the dump written.
3. Each embedded script is syntactically valid `sh` (`bash -n`), decoupled
   from `#1` above and cheap enough to run every time.

Scope: this repo's own chart only (helm/fuzeinfra). See FuzeInfra#1051.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"

# Backups are gated `enabled=false` by default and require an S3 secret name
# + endpoint when sink=s3 (the default), so every render below turns backups
# on explicitly and supplies dummy S3 coordinates purely for template
# resolution — no real secret is read or required for `helm template`.
COMMON_SET = [
    "--set", "backups.enabled=true",
    "--set", "backups.s3.existingSecret=dummy",
    "--set", "backups.s3.endpoint=https://example.com",
]


def _render(extra_set: list[str]) -> list[dict]:
    cmd = [
        "helm", "template", "fuzeinfra", str(CHART),
        "-n", "fuzeinfra",
        "-s", "templates/backup-cronjobs.yaml",
        *COMMON_SET,
        *extra_set,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def _iter_shell_scripts(doc: dict):
    """Yield (container_name, script) for every /bin/sh -c command in a CronJob."""
    spec = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    for container in spec.get("initContainers", []) + spec.get("containers", []):
        command = container.get("command")
        if command and len(command) >= 3 and command[0] == "/bin/sh" and command[1] == "-c":
            yield container["name"], command[2]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("sink", ["s3", "pvc"])
def test_backup_scripts_are_pipefail_safe_and_size_checked(sink):
    extra = ["--set", f"backups.sink={sink}"]
    if sink == "pvc":
        # values-.*.yaml overlays are not applied here (default values.yaml
        # only), so the prometheus-volume-backup cross-check in the sibling
        # template does not apply; nothing extra needed.
        pass

    docs = _render(extra)
    cronjobs = [d for d in docs if d.get("kind") == "CronJob"]
    assert cronjobs, "expected at least one backup CronJob to render"

    for doc in cronjobs:
        name = doc["metadata"]["name"]
        scripts = dict(_iter_shell_scripts(doc))
        assert scripts, f"{name}: no /bin/sh -c scripts found"

        for container_name, script in scripts.items():
            # (1) pipefail-safe.
            assert "set -euo pipefail" in script or (
                "set -e" in script and "pipefail" in script
            ), (
                f"{name}/{container_name}: script does not set pipefail — a "
                f"failed dump piped into `gzip -c` would exit 0 (#1051). "
                f"Script:\n{script}"
            )
            assert "set -eu\n" not in script and not script.strip().startswith(
                "set -eu\n"
            ), (
                f"{name}/{container_name}: found bare `set -eu` without "
                f"pipefail (#1051)."
            )

            # (3) syntactically valid POSIX shell.
            proc = subprocess.run(
                ["bash", "-n"], input=script, text=True, capture_output=True
            )
            assert proc.returncode == 0, (
                f"{name}/{container_name}: script fails `bash -n`:\n"
                f"{proc.stderr}\nScript:\n{script}"
            )

            # (2) dump containers assert a size floor + integrity check.
            if container_name == "dump":
                assert "wc -c < /backup/dump.bin" in script, (
                    f"{name}/{container_name}: dump script does not measure "
                    f"the artifact's size before declaring success (#1051)."
                )
                assert "min_bytes" in script and "-lt \"$min_bytes\"" in script, (
                    f"{name}/{container_name}: dump script has no minimum-size "
                    f"assertion before upload (#1051)."
                )
                assert "exit 1" in script, (
                    f"{name}/{container_name}: dump script never fails the "
                    f"pipeline on an undersized dump."
                )
                assert "gzip -t /backup/dump.bin" in script, (
                    f"{name}/{container_name}: dump script does not verify "
                    f"gzip integrity (truncation) before upload (#1051)."
                )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_backup_min_bytes_is_configurable_per_database():
    """`backups.mariadb.minBytes` (etc.) must override `backups.minDumpBytes`."""
    docs = _render(
        [
            "--set", "backups.sink=s3",
            "--set", "backups.minDumpBytes=1024",
            "--set", "backups.mariadb.enabled=true",
            "--set", "backups.mariadb.minBytes=64",
        ]
    )
    mariadb_job = next(
        d for d in docs if d.get("kind") == "CronJob"
        and d["metadata"]["name"] == "fuzeinfra-backup-mariadb"
    )
    scripts = dict(_iter_shell_scripts(mariadb_job))
    assert "min_bytes=64" in scripts["dump"], (
        "backups.mariadb.minBytes did not override backups.minDumpBytes for "
        f"the mariadb dump container:\n{scripts['dump']}"
    )

    postgres_job = next(
        d for d in docs if d.get("kind") == "CronJob"
        and d["metadata"]["name"] == "fuzeinfra-backup-postgres"
    )
    scripts = dict(_iter_shell_scripts(postgres_job))
    assert "min_bytes=1024" in scripts["dump"], (
        "backups.minDumpBytes default did not apply to the postgres dump "
        f"container:\n{scripts['dump']}"
    )
