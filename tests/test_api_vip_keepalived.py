"""The API VIP (keepalived) DaemonSet: structure, rollout, and the reconciler.

This subsystem had no tests, and it shows: the history in helm/fuzeinfra/templates
(api-vip-keepalived*.yaml) is a run of prod-only bugs found one at a time on
2026-09-22/23 — wrong node names, a script dir that keepalived refused as unsafe,
a one-line `authentication {` that ran VRRP unauthenticated, a health check whose
401 docked every healthy node. Each was invisible until someone read logs.

The bug this file was written for is the next in that line, found 2026-10-14:

  Contabo said fuze-core-1 held BOTH 13.140.184.164 and 13.140.185.42 — but .42 is
  fuze-core-2's VIP, and keepalived on core-2 had believed itself MASTER for it for
  hours. notify.sh only reassigns the provider IP when keepalived TRANSITIONS into
  MASTER. core-1 briefly won .42 (its notify moved the provider IP), core-2 re-won
  the election without ever leaving MASTER, so nothing re-asserted. Every pod was
  Ready; a third of the round-robin silently failed.

Four properties are pinned:

  1. a LEVEL-triggered reconciler exists, runs as its own unprivileged container,
     and only ever acts on a VIP bound on this node, only on a provider read it
     trusts, and only after consecutive mismatches (reconcile.sh is EXECUTED here,
     against stubbed `ip` and `curl`, not grepped);
  2. editing any script rolls the pods — the old checksum covered only the tables,
     so a script-only fix changed mounted files and restarted nothing, and
     render.sh runs once at start;
  3. the health check has hysteresis, so a few-second stall cannot reorder the
     election (weight -60 exceeds the whole spread of base priorities);
  4. notify.sh's output reaches the container log, which is what hid the drift.

Offline: renders the chart with `helm template` and runs bash. No cluster, network
or Contabo account.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
CONFIG_TPL = CHART / "templates" / "api-vip-keepalived-config.yaml"

pytestmark = [
    pytest.mark.skipif(shutil.which("helm") is None, reason="needs the helm binary to render the chart"),
    pytest.mark.skipif(shutil.which("jq") is None, reason="reconcile.sh needs jq"),
    pytest.mark.skipif(sys.platform == "win32", reason="executes the DaemonSet's real bash"),
]

DS_NAME = "fuzeinfra-api-vip-keepalived"
ME = "203396106"       # fuze-core-2's Contabo instance id (values-contabo.yaml)
OTHER = "203383846"    # fuze-core-1's
VIP_CORE1, VIP_CORE2, VIP_CORE3 = "13.140.184.164", "13.140.185.42", "13.140.185.205"


def _render(chart=CHART, *sets):
    cmd = ["helm", "template", "fuzeinfra", str(chart), "-n", "fuzeinfra",
           "-f", str(chart / "values-contabo.yaml")]
    for s in sets:
        cmd += ["--set", s]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _pick(docs, kind):
    return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == DS_NAME)


@pytest.fixture(scope="module")
def docs():
    return _render()


@pytest.fixture(scope="module")
def scripts(docs):
    return _pick(docs, "ConfigMap")["data"]


# ---------------------------------------------------------------------------
# 1a. structure of the reconciler container
# ---------------------------------------------------------------------------

def test_the_daemonset_runs_keepalived_and_a_separate_reconciler(docs):
    names = [c["name"] for c in _pick(docs, "DaemonSet")["spec"]["template"]["spec"]["containers"]]
    assert names == ["keepalived", "reconciler"]


def test_the_reconciler_is_unprivileged_and_the_keepalived_container_still_is_not(docs):
    """The reconciler only READS this node's addresses, so it gets no capabilities.

    keepalived manages addresses and genuinely needs NET_ADMIN; a second container
    copying that securityContext would be privilege handed out for no reason.
    """
    cs = {c["name"]: c for c in _pick(docs, "DaemonSet")["spec"]["template"]["spec"]["containers"]}
    rec = cs["reconciler"]["securityContext"]
    assert rec.get("privileged") in (None, False)
    assert rec["allowPrivilegeEscalation"] is False
    assert rec["capabilities"] == {"drop": ["ALL"]}
    assert cs["keepalived"]["securityContext"]["privileged"] is True


def test_the_reconciler_gets_the_node_name_and_the_same_provider_credentials(docs):
    cs = {c["name"]: c for c in _pick(docs, "DaemonSet")["spec"]["template"]["spec"]["containers"]}
    env = {e["name"]: e for e in cs["reconciler"]["env"]}
    assert env["NODE_NAME"]["valueFrom"]["fieldRef"]["fieldPath"] == "spec.nodeName"
    for key in ("CONTABO_CLIENT_ID", "CONTABO_CLIENT_SECRET", "CONTABO_API_USER", "CONTABO_API_PASSWORD"):
        ref = env[key]["valueFrom"]["secretKeyRef"]
        same = next(e for e in cs["keepalived"]["env"] if e["name"] == key)["valueFrom"]["secretKeyRef"]
        assert ref == same, f"{key}: the reconciler must read the same Secret key as notify.sh"
    assert "value" not in env["CONTABO_CLIENT_SECRET"], "a credential must never be an inline value"


def test_the_reconciler_runs_the_mounted_script_from_the_shared_configmap(docs):
    spec = _pick(docs, "DaemonSet")["spec"]["template"]["spec"]
    rec = next(c for c in spec["containers"] if c["name"] == "reconciler")
    assert rec["command"] == ["/bin/bash", "/cfg/reconcile.sh"]
    assert {"name": "cfg", "mountPath": "/cfg", "readOnly": True} in rec["volumeMounts"]
    assert rec["image"] == next(c for c in spec["containers"] if c["name"] == "keepalived")["image"], (
        "one image carries bash + curl + jq + iproute2; a second image is a second thing to drift"
    )


def test_the_reconciler_can_be_switched_off():
    docs = _render(CHART, "apiVip.reconcile.enabled=false")
    names = [c["name"] for c in _pick(docs, "DaemonSet")["spec"]["template"]["spec"]["containers"]]
    assert names == ["keepalived"]


# ---------------------------------------------------------------------------
# 2. editing a script must roll the pods
# ---------------------------------------------------------------------------

def _checksum(docs):
    return _pick(docs, "DaemonSet")["spec"]["template"]["metadata"]["annotations"]["checksum/config"]


def test_editing_a_script_changes_the_pod_template_checksum(tmp_path):
    """render.sh runs once at start, so a script change that restarts nothing never lands.

    The checksum used to hash only vips/nodes/authPass. A fix to notify.sh,
    render.sh or check-apiserver.sh updated the mounted ConfigMap files and left
    every pod running the old behaviour — silently, for as long as nobody
    restarted them by hand.
    """
    base = _checksum(_render())
    clone = tmp_path / "fuzeinfra"
    shutil.copytree(CHART, clone, ignore=shutil.ignore_patterns("charts", "*.tgz"))
    cfg = clone / "templates" / "api-vip-keepalived-config.yaml"
    text = cfg.read_text(encoding="utf-8")
    assert "log() { echo \"[api-vip notify]" in text
    cfg.write_text(text.replace('log() { echo "[api-vip notify]', 'log() { echo "[api-vip notify v2]', 1), encoding="utf-8")
    assert _checksum(_render(clone)) != base


def test_the_checksum_is_stable_across_identical_renders():
    assert _checksum(_render()) == _checksum(_render())


# ---------------------------------------------------------------------------
# 3. health-check hysteresis
# ---------------------------------------------------------------------------

def _vrrp_script(render_sh):
    block = re.search(r"vrrp_script chk_apiserver \{(.*?)\}", render_sh, re.S).group(1)
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s*(interval|timeout|rise|fall|weight)\s+(-?\d+)", block, re.M)}


def test_the_health_check_needs_about_fifteen_seconds_of_failures(scripts):
    s = _vrrp_script(scripts["render.sh"])
    assert s["interval"] * s["fall"] >= 15, (
        f"interval={s['interval']} x fall={s['fall']} docks priority after "
        f"{s['interval'] * s['fall']}s. weight -60 exceeds the whole spread of base priorities "
        "(150/140/100), so a few-second stall reorders the election — observed 2026-10-13 22:17."
    )
    assert s["timeout"] < s["interval"] * s["fall"]


def test_the_health_check_tunables_come_from_values():
    docs = _render(CHART, "apiVip.healthCheck.fall=7", "apiVip.healthCheck.intervalSeconds=4")
    s = _vrrp_script(_pick(docs, "ConfigMap")["data"]["render.sh"])
    assert (s["fall"], s["interval"]) == (7, 4)


def test_the_check_script_still_treats_401_as_alive(scripts):
    """The 2026-09-23 regression: requiring 200 marked every healthy node unhealthy."""
    body = scripts["check-apiserver.sh"]
    assert re.search(r"200\|401\|403\)\s*exit 0", body)


# ---------------------------------------------------------------------------
# 4. notify.sh output must reach the container log
# ---------------------------------------------------------------------------

def test_notify_output_is_sent_to_the_container_log_best_effort(scripts):
    body = scripts["notify.sh"]
    redirect = "{ exec >>/proc/1/fd/1 2>&1; } 2>/dev/null || true"
    assert redirect in body
    assert body.index(redirect) < body.index('STATE="${1:-}"'), "redirect must precede the first log line"


# ---------------------------------------------------------------------------
# 1b. reconcile.sh, EXECUTED against stubbed `ip` and `curl`
# ---------------------------------------------------------------------------

IP_STUB = r"""#!/usr/bin/env bash
# `ip -4 -o addr show dev eth0`: the primary address plus whatever VIPs are "bound".
echo "2: eth0    inet 161.97.118.134/18 brd 161.97.127.255 scope global eth0"
IFS=, read -ra A <<< "${BOUND:-}"
for a in "${A[@]}"; do [ -n "$a" ] && echo "2: eth0    inet $a/32 scope global eth0"; done
"""

CURL_STUB = r"""#!/usr/bin/env bash
echo "curl $*" >> "$CALLS"
# The URL is not always the last argument (token() puts --data-urlencode after it).
url=""
for a in "$@"; do case "$a" in https://*) url="$a" ;; esac; done
case "$url" in
  *auth.contabo.com*)
    if [ -n "${TOKEN_FAIL:-}" ]; then echo '{"error":"invalid_grant"}'; else echo '{"access_token":"TKN"}'; fi ;;
  */v1/vips/*)
    ip="${url##*/}"
    if [ -n "${HOLDER_ERROR:-}" ]; then echo '{"message":"internal error"}'; exit 0; fi
    n=$(cat "$COUNTS/$ip" 2>/dev/null || echo 0); echo $((n + 1)) > "$COUNTS/$ip"
    IFS=, read -ra SEQ <<< "${HOLDER_SEQ:-$OTHER_ID}"
    i=$n; [ "$i" -ge "${#SEQ[@]}" ] && i=$((${#SEQ[@]} - 1))
    printf '{"data":[{"resourceId":"%s"}]}\n' "${SEQ[$i]}" ;;
esac
"""

NOTIFY_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$NOTIFIED"
"""


def _write(path, text, mode=0o755):
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _run_reconcile(tmp_path, scripts, *, bound="", holder_seq=None, cycles=1, node="fuze-core-2",
                   token_fail=False, holder_error=False, strip_tools=(), interval="0"):
    cfg, bin_dir, counts = tmp_path / "cfg", tmp_path / "bin", tmp_path / "counts"
    for d in (cfg, bin_dir, counts):
        d.mkdir(exist_ok=True)
    vips = json.loads(scripts["vips.json"])
    nodes = json.loads(scripts["nodes.json"])
    (cfg / "vips.json").write_text(json.dumps(vips))
    (cfg / "nodes.json").write_text(json.dumps(nodes))
    _write(cfg / "reconcile.sh", scripts["reconcile.sh"])
    _write(cfg / "notify.sh", NOTIFY_STUB)
    for name, text in (("ip", IP_STUB), ("curl", CURL_STUB)):
        if name not in strip_tools:
            _write(bin_dir / name, text)
    # `sleep infinity` (the unknown-node path) must return so the test can finish.
    _write(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    calls, notified = tmp_path / "calls", tmp_path / "notified"
    calls.write_text("")
    notified.write_text("")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CFG_DIR": str(cfg), "NODE_NAME": node, "BOUND": bound,
        "RECONCILE_INTERVAL": interval, "RECONCILE_MAX_CYCLES": str(cycles),
        "NOTIFY_SCRIPT": str(cfg / "notify.sh"),
        "CALLS": str(calls), "COUNTS": str(counts), "NOTIFIED": str(notified),
        "OTHER_ID": OTHER,
        "CONTABO_CLIENT_ID": "id", "CONTABO_CLIENT_SECRET": "secret",
        "CONTABO_API_USER": "user", "CONTABO_API_PASSWORD": "pass",
    }
    if holder_seq is not None:
        env["HOLDER_SEQ"] = holder_seq
    if token_fail:
        env["TOKEN_FAIL"] = "1"
    if holder_error:
        env["HOLDER_ERROR"] = "1"
    proc = subprocess.run(["bash", str(cfg / "reconcile.sh")], capture_output=True, text=True, env=env, timeout=60)
    return proc, notified.read_text().splitlines(), calls.read_text().splitlines()


def test_nothing_bound_locally_means_no_provider_call_at_all(tmp_path, scripts):
    """A BACKUP node must never reach for an address it does not hold."""
    proc, notified, calls = _run_reconcile(tmp_path, scripts, bound="", cycles=3)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert notified == [] and calls == []


def test_a_correctly_assigned_vip_is_left_alone(tmp_path, scripts):
    proc, notified, _ = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, holder_seq=ME, cycles=3)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert notified == []


def test_one_mismatch_is_not_enough_to_act(tmp_path, scripts):
    """A reassignment already in flight looks like drift for a moment; do not fight it."""
    proc, notified, _ = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, holder_seq=OTHER, cycles=1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert notified == []
    assert "DRIFT" in proc.stdout and "(1/2)" in proc.stdout


def test_two_consecutive_mismatches_reassign_exactly_once(tmp_path, scripts):
    """The 2026-10-14 incident: bound on core-2, routed by the provider to core-1."""
    proc, notified, _ = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, holder_seq=OTHER, cycles=2)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert notified == [f"MASTER {VIP_CORE2}"]
    assert f"provider routes it to instance {OTHER}, not {ME}" in proc.stdout
    assert "reassigning" in proc.stdout


def test_the_strike_counter_resets_after_a_reassignment(tmp_path, scripts):
    """Cycle 3 is strike 1 again. Without the reset it would reassign every cycle."""
    _, notified, _ = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, holder_seq=OTHER, cycles=3)
    assert notified == [f"MASTER {VIP_CORE2}"]


def test_a_mismatch_that_corrects_itself_never_reassigns(tmp_path, scripts):
    proc, notified, _ = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, holder_seq=f"{OTHER},{ME}", cycles=3)
    assert notified == []
    assert "drift cleared" in proc.stdout


def test_an_unreadable_provider_state_is_cannot_tell_not_mismatch(tmp_path, scripts):
    """An API error must never be treated as 'someone else holds it'."""
    proc, notified, _ = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, holder_error=True, cycles=5)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert notified == []
    assert "unreadable; not acting" in proc.stdout


def test_a_token_failure_means_no_action_and_no_holder_lookup(tmp_path, scripts):
    proc, notified, calls = _run_reconcile(tmp_path, scripts, bound=VIP_CORE2, token_fail=True, cycles=3)
    assert notified == []
    assert not any("/v1/vips/" in c for c in calls)
    assert "no Contabo token" in proc.stdout


def test_only_locally_bound_vips_are_checked_and_each_is_reassigned_independently(tmp_path, scripts):
    proc, notified, calls = _run_reconcile(
        tmp_path, scripts, bound=f"{VIP_CORE1},{VIP_CORE2}", holder_seq=OTHER, cycles=2)
    assert sorted(notified) == sorted([f"MASTER {VIP_CORE1}", f"MASTER {VIP_CORE2}"])
    assert not any(VIP_CORE3 in c for c in calls), "a VIP this node does not hold must never be queried"


@pytest.mark.parametrize("lookalike", ["113.140.185.42", "13.140.185.420", "13.140.185.4"])
def test_an_address_that_merely_contains_a_vip_does_not_count_as_bound(tmp_path, scripts, lookalike):
    """A substring match would call a VIP 'bound' when only a lookalike is.

    113.140.185.42 and 13.140.185.420 both CONTAIN 13.140.185.42; the check must
    anchor on the surrounding space and the slash of the CIDR.
    """
    proc, notified, calls = _run_reconcile(tmp_path, scripts, bound=lookalike, holder_seq=OTHER, cycles=3)
    assert notified == [] and calls == []


def test_a_node_missing_from_the_table_idles_instead_of_acting(tmp_path, scripts):
    proc, notified, calls = _run_reconcile(tmp_path, scripts, node="not-a-node", bound=VIP_CORE2)
    assert "not in apiVip.nodes; idling" in proc.stdout
    assert notified == [] and calls == []


def test_a_missing_tool_is_a_loud_failure_not_a_silently_healthy_loop(tmp_path, scripts):
    """With no `ip`, nothing ever looks bound — a loop that reports healthy and does nothing."""
    # PATH is narrowed to the stub dir so a developer machine's real `ip` cannot mask this.
    cfg, bin_dir = tmp_path / "cfg", tmp_path / "bin"
    cfg.mkdir()
    bin_dir.mkdir()
    (cfg / "vips.json").write_text(scripts["vips.json"])
    (cfg / "nodes.json").write_text(scripts["nodes.json"])
    _write(cfg / "reconcile.sh", scripts["reconcile.sh"])
    for tool in ("jq", "curl", "date"):
        real = shutil.which(tool)
        if real:
            (bin_dir / tool).symlink_to(real)
    proc = subprocess.run([shutil.which("bash"), str(cfg / "reconcile.sh")], capture_output=True, text=True,
                          env={"PATH": str(bin_dir), "CFG_DIR": str(cfg), "NODE_NAME": "fuze-core-2"})
    assert proc.returncode == 1
    assert "required tool 'ip' missing" in proc.stdout


# ---------------------------------------------------------------------------
# the table the reconciler trusts must agree with the Contabo facts
# ---------------------------------------------------------------------------

def test_the_instance_ids_match_the_contabo_instances_listing(scripts):
    """Verified against GET /v1/compute/instances on 2026-10-14."""
    by_name = {n["name"]: n["instanceId"] for n in json.loads(scripts["nodes.json"])}
    assert by_name == {"fuze-core-1": "203383846", "fuze-core-2": "203396106", "fuze-core-3": "203410214"}


def test_every_vip_has_a_distinct_router_id_and_a_known_owner(scripts):
    vips = json.loads(scripts["vips.json"])
    nodes = {n["name"] for n in json.loads(scripts["nodes.json"])}
    assert len({v["routerId"] for v in vips}) == len(vips)
    assert {v["primaryNode"] for v in vips} <= nodes
    assert {v["address"]: v["primaryNode"] for v in vips} == {
        VIP_CORE1: "fuze-core-1", VIP_CORE2: "fuze-core-2", VIP_CORE3: "fuze-core-3"}
