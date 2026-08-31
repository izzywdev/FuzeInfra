"""Executable invariants for the LiteLLM key-model reconciliation.

CONTEXT — every assertion here is a bug that actually shipped to prod.

The gateway's virtual keys carry a `models` allow-list in LiteLLM's database.
A key provisioned before a model existed cannot reach that model, and the
gateway 403s ("key not allowed to access model") BEFORE the fallback chain
runs. When `claude-opus-4-8` was added, the fleet's `fuze-code-review` check
started failing everywhere. `models: []` means "all models", and a PostSync
hook is supposed to enforce that on every sync.

Three separate defects kept that hook from ever once succeeding, and each cost
a full ~15-minute deploy cycle to find:

  1. `alpine:3.20` + `apk add curl jq` — apk exits 5 on these nodes (no route to
     the Alpine mirrors). `set -eu` then killed the script, and because the apk
     output was redirected to /dev/null the Job failed completely silently.
  2. `ghcr.io/jqlang/jq` — a distroless image carrying ONLY the jq binary. It
     has no shell, so `command: ["/bin/sh","-c"]` could not even start
     ("exec: sh: executable file not found in $PATH").
  3. A git merge-conflict marker (`<<<<<<< HEAD`) was committed into
     job-sync-key-models.yaml. At column 0 it dedents out of the YAML block
     scalar, so `helm template` failed outright — Argo could not render
     helm/litellm AT ALL and the whole Application went Sync=Unknown. Every
     unrelated litellm change silently stopped deploying.

(3) survived local checking because the obvious command, `helm template
helm/litellm -f helm/litellm/values.yaml`, renders NOTHING: base values.yaml
sets `enabled: false`. It reported success while proving nothing. The render
test below therefore uses values-contabo.yaml — the file Argo actually passes.

Offline: renders a chart and runs a stdlib HTTP server on localhost. No
cluster, no network, no credentials.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "litellm"
ADMIN = REPO / "scripts" / "litellm_admin.py"

# The images that broke this hook. Naming them keeps the failure message useful
# and stops a well-meaning "make the hook image smaller" change from regressing.
BANNED_IMAGE_SUBSTRINGS = ("alpine", "jqlang/jq", "busybox")


def _render(values_name="values-contabo.yaml"):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm not installed")
    out = subprocess.run(
        [helm, "template", "litellm", str(CHART),
         "--namespace", "fuzeinfra",
         "-f", str(CHART / values_name)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, (
        f"helm template with {values_name} failed — this is exactly what Argo "
        f"runs, so a failure here means the Application cannot render at all:\n"
        f"{out.stderr}"
    )
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _hook_jobs(docs):
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == "Job"}


# --------------------------------------------------------------------------
# 1. The chart must render with the values Argo passes.
# --------------------------------------------------------------------------

def test_chart_renders_with_contabo_values():
    docs = _render()
    assert docs, "values-contabo.yaml rendered an empty manifest set"


def test_no_conflict_markers_in_chart():
    """A committed '<<<<<<< HEAD' broke prod rendering for hours."""
    offenders = []
    for path in CHART.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.startswith("<<<<<<< ") or line == "=======" or line.startswith(">>>>>>> "):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line[:40]}")
    assert not offenders, "git conflict markers committed:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------
# 2. The PostSync hooks must be runnable and must not be able to fail a sync.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("job_name", ["litellm-sync-key-models", "litellm-seed-models"])
def test_hook_image_can_actually_run_the_script(job_name):
    jobs = _hook_jobs(_render())
    assert job_name in jobs, f"{job_name} did not render"
    container = jobs[job_name]["spec"]["template"]["spec"]["containers"][0]
    image = container["image"]
    for banned in BANNED_IMAGE_SUBSTRINGS:
        assert banned not in image, (
            f"{job_name} uses '{image}'. Images matching '{banned}' have broken "
            f"this hook before: alpine cannot apk-add (no mirror route) and "
            f"jqlang/jq is distroless with no shell. Use the gateway's own "
            f"image — it is already on the node and has python3."
        )
    assert "python3" in (container.get("command") or []), (
        f"{job_name} must invoke python3 directly; shelling out reintroduces "
        f"the 'does this image have curl/jq' failure class."
    )


@pytest.mark.parametrize("job_name", ["litellm-sync-key-models", "litellm-seed-models"])
def test_hook_python_compiles(job_name):
    """A syntax error here is only discoverable in prod otherwise."""
    jobs = _hook_jobs(_render())
    src = jobs[job_name]["spec"]["template"]["spec"]["containers"][0]["args"][0]
    compile(src, f"<{job_name}>", "exec")


@pytest.mark.parametrize("job_name", ["litellm-sync-key-models", "litellm-seed-models"])
def test_hook_cannot_fail_the_argo_sync(job_name):
    """These hooks reconcile a database row; they are not a deploy step.

    An earlier revision exhausted backoffLimit and left the whole litellm
    Application degraded, so a broken reconciler blocked every unrelated prod
    change. Every failure path must exit 0.
    """
    jobs = _hook_jobs(_render())
    job = jobs[job_name]
    assert job["spec"].get("backoffLimit") == 0, (
        f"{job_name} must not retry: the script exits 0 on handled failures, so "
        f"a non-zero exit means the interpreter died and retrying cannot help."
    )
    src = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert "sys.exit(1)" not in src, (
        f"{job_name} exits non-zero somewhere; that fails the Argo sync."
    )


# --------------------------------------------------------------------------
# 3. The admin script's reconciliation logic, against a mock gateway.
# --------------------------------------------------------------------------

class _MockLiteLLM(BaseHTTPRequestHandler):
    """Mimics the subset of the LiteLLM admin API this code touches."""

    state = None   # set by the fixture
    updates = None

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            return self._send({"error": "no auth"}, 401)
        if self.path.startswith("/key/list"):
            return self._send({"keys": self.state["keys"], "total_count": len(self.state["keys"])})
        if self.path.startswith("/model/info"):
            return self._send({"data": [{"model_name": "claude-opus-4-8"}]})
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/key/update"):
            self.updates.append(payload)
            for k in self.state["keys"]:
                if k["token"] == payload.get("key"):
                    k["models"] = payload.get("models")
            return self._send({"token": payload.get("key"), "models": payload.get("models")})
        self._send({"error": "not found"}, 404)


@pytest.fixture
def gateway():
    state = {"keys": [
        # Mirrors the reported prod state: two minted keys, one restricted.
        {"token": "a1b2c3d4deadbeef", "key_alias": "FUZE_KEY",
         "models": ["claude-opus-5", "gpt-4.1"]},
        {"token": "9f8e7d6c5b4a0000", "key_alias": "legacy", "models": []},
    ]}
    updates = []
    handler = type("H", (_MockLiteLLM,), {"state": state, "updates": updates})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], state, updates
    srv.shutdown()


def _load_admin(port):
    os.environ["LITELLM_MASTER_KEY"] = "sk-test-not-a-real-key"
    os.environ["LITELLM_PORT"] = str(port)
    spec = importlib.util.spec_from_file_location("litellm_admin", ADMIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # The module resolves BASE at import time from LITELLM_PORT.
    assert mod.BASE.endswith(str(port)), mod.BASE
    return mod


def test_clear_key_models_clears_only_the_restricted_key(gateway, capsys):
    port, state, updates = gateway
    mod = _load_admin(port)

    rc = mod.cmd_clear_key_models()

    assert rc == 0
    assert len(updates) == 1, f"expected exactly one /key/update, got {updates}"
    assert updates[0] == {"key": "a1b2c3d4deadbeef", "models": []}
    assert all(k["models"] == [] for k in state["keys"]), \
        "every key must end with models=[] (all models)"


def test_clear_key_models_is_idempotent(gateway):
    port, state, updates = gateway
    for k in state["keys"]:
        k["models"] = []
    mod = _load_admin(port)

    assert mod.cmd_clear_key_models() == 0
    assert updates == [], "a second run must not rewrite already-unrestricted keys"


def test_admin_never_prints_a_credential(gateway, capsys):
    """FuzeInfra job logs are public; this output goes straight into one."""
    port, state, _ = gateway
    mod = _load_admin(port)
    mod.cmd_list_keys()
    out = capsys.readouterr().out

    assert "sk-test-not-a-real-key" not in out, "master key leaked into the log"
    for key in state["keys"]:
        assert key["token"] not in out, "full hashed token leaked; print a prefix only"
    # The prefix is what makes the output useful for correlating rows.
    assert "a1b2c3d4" in out
