"""The Prometheus TSDB backup must survive the NetworkPolicy startup race.

Prometheus is opted into the admin-plane NetworkPolicy (#1253). It admits the
backup pod by namespace, but the CNI adds a NEW pod's IP to the allowed-source
set asynchronously, so a connection in the pod's first milliseconds is refused.
The snapshot init container called curl exactly once, at start, and lost that
race on every run from 2026-09-29 — `curl: (7) Failed to connect to
fuzeinfra-prometheus port 9090 after 3 ms`, three pods a night, no Prometheus
backup for days. It also left fuzeinfra-prod's sync operation Failed.

These tests render the real init script and EXECUTE it with a stubbed curl that
refuses N times, so the retry is tested by behaviour.

Offline: helm template + sh. No cluster, no network.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

SUCCESS = '{"status":"success","data":{"name":"20261004T032000Z-abc"}}'


def _snapshot_script(extra=()):
    out = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART), "--namespace", "fuzeinfra",
         "-f", str(CHART / "values-contabo.yaml"), *extra],
        capture_output=True, text=True, check=True).stdout
    job = next(d for d in yaml.safe_load_all(out)
               if d and d["metadata"]["name"] == "fuzeinfra-backup-prometheus")
    init = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["initContainers"][0]
    assert init["name"] == "snapshot"
    return init["command"][2]


@pytest.fixture
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    snaps, scratch = tmp_path / "snapshots", tmp_path / "scratch"
    snaps.mkdir()
    scratch.mkdir()
    (bin_dir / "curl").write_text(
        "#!/bin/sh\n"
        'n=$(cat "$CURL_COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$CURL_COUNT"\n'
        'if [ "$n" -le "$REFUSE" ]; then\n'
        '  echo "curl: (7) Failed to connect to fuzeinfra-prometheus port 9090 after 3 ms" >&2; exit 7\n'
        "fi\n"
        'mkdir -p "$SNAPS/20261004T032000Z-abc"\n'
        f"echo '{SUCCESS}'\n")
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")  # keep the test fast
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def _run(refuse, script=None):
        body = (script or _snapshot_script()).replace("/snapshots", str(snaps)).replace("/scratch", str(scratch))
        count = tmp_path / "count"
        count.write_text("0")
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", REFUSE=str(refuse),
                   CURL_COUNT=str(count), SNAPS=str(snaps),
                   PROM_URL="http://fuzeinfra-prometheus:9090", PREFIX="fuzeinfra")
        proc = subprocess.run(["sh", "-c", body], env=env, capture_output=True, text=True)
        return proc, int(count.read_text()), scratch
    return _run


def test_first_call_succeeds(run):
    proc, calls, scratch = run(refuse=0)
    assert proc.returncode == 0, proc.stderr
    assert calls == 1
    assert (scratch / "snapshot-name").read_text() == "20261004T032000Z-abc"


def test_survives_the_startup_race(run):
    """The prod failure: refused at first, reachable a few seconds later."""
    proc, calls, scratch = run(refuse=3)
    assert proc.returncode == 0, proc.stderr
    assert calls == 4
    assert (scratch / "snapshot-name").exists()


def test_a_real_block_still_fails_loudly(run):
    proc, calls, _ = run(refuse=10_000)
    assert proc.returncode != 0
    assert "not a startup race" in proc.stderr
    assert calls == 90 // 5 + 1, "must give up after the grace window, not loop forever"


def test_grace_window_is_configurable(run):
    script = _snapshot_script(["--set", "backups.volumes.prometheus.connectGraceSeconds=10"])
    proc, calls, _ = run(refuse=10_000, script=script)
    assert proc.returncode != 0 and calls == 10 // 5 + 1


def test_grace_window_fits_inside_the_job_deadline():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    vols = values["backups"]["volumes"]
    grace = vols["prometheus"].get("connectGraceSeconds", 90)
    assert grace < vols["activeDeadlineSeconds"]
