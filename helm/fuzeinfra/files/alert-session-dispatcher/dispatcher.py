#!/usr/bin/env python3
"""Alert -> agent-session dispatcher (stdlib only, no third-party imports).

Receives Alertmanager/Grafana webhooks, and for a critical alert that matches an
allowlist asks FuzeAgent to create ONE responder session, authenticated by a
short-lived FuzeFront workload token minted per dispatch from this pod's projected
ServiceAccount token. Design: docs/design/alert-to-agent-session.md.

Invariants (each is unit-tested in tests/test_alert_session_dispatcher.py):
  * labels are DATA: only allowlisted keys, strict charset, free-text annotations
    are never forwarded, and no label is ever interpolated into the brief;
  * one open session per (alertname, namespace); cooldown; hard daily spawn cap
    and an estimated-spend cap; every attempt is counted BEFORE the network call;
  * fail closed: bad config, unreadable state, or a missing inbound token stops it;
  * no secret value is ever logged or returned.
"""
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ALERTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
LABEL_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SESSION_URL_RE = re.compile(r"^https://[a-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$")
MAX_BODY = 262144
MAX_LABELS = 12
REQUIRED_LABELS = ("alertname", "namespace")


def utcnow():
    return datetime.now(timezone.utc)


def log(event, **fields):
    """One JSON line to stdout (-> Loki). Callers must never pass secret values."""
    print(json.dumps({"ts": utcnow().isoformat(), "event": event, **fields}, sort_keys=True), flush=True)


# --------------------------------------------------------------------------- config
class ConfigError(Exception):
    pass


def load_config(raw):
    cfg = json.loads(raw) if isinstance(raw, str) else dict(raw)
    try:
        out = {
            "paused": bool(cfg.get("paused", False)),
            "dryRun": bool(cfg.get("dryRun", True)),
            "allowlist": [str(x) for x in cfg["allowlist"]],
            "labelKeys": [str(x) for x in cfg["labelKeys"]],
            "cooldownSeconds": int(cfg["cooldownSeconds"]),
            "openTtlSeconds": int(cfg["openTtlSeconds"]),
            "dailySpawnCap": int(cfg["dailySpawnCap"]),
            "dailySpendCapUsd": float(cfg["dailySpendCapUsd"]),
            "estimatedSessionCostUsd": float(cfg["estimatedSessionCostUsd"]),
            "securityTokenUrl": str(cfg["securityTokenUrl"]),
            "agentSessionUrl": str(cfg["agentSessionUrl"]),
            "projectedTokenFile": str(cfg.get("projectedTokenFile", "/var/run/secrets/tokens/fuzefront-security")),
            "auditRepo": str(cfg.get("auditRepo", "")),
            "stateFile": str(cfg.get("stateFile", "/state/state.json")),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid dispatcher config: {exc!r}") from exc
    if not out["allowlist"]:
        raise ConfigError("allowlist is empty")
    for name in out["allowlist"]:
        if not ALERTNAME_RE.match(name):
            raise ConfigError(f"allowlist entry fails charset check: {name!r}")
    for key in out["labelKeys"]:
        if not LABEL_KEY_RE.match(key):
            raise ConfigError(f"label key fails charset check: {key!r}")
    for req in REQUIRED_LABELS:
        if req not in out["labelKeys"]:
            raise ConfigError(f"labelKeys must include {req}")
    if out["dailySpawnCap"] < 0 or out["cooldownSeconds"] < 0 or out["openTtlSeconds"] < 1:
        raise ConfigError("caps/cooldown/ttl out of range")
    if out["estimatedSessionCostUsd"] <= 0 or out["dailySpendCapUsd"] < 0:
        raise ConfigError("spend figures out of range")
    for url_key in ("securityTokenUrl", "agentSessionUrl"):
        if not out[url_key].startswith(("http://", "https://")):
            raise ConfigError(f"{url_key} must be an http(s) URL")
    return out


# ------------------------------------------------------------------ label handling
def sanitize_labels(labels, allowed_keys):
    """Return (clean_labels, dropped_keys) or raise ValueError if a required label is unusable.

    Unknown keys and values that fail the strict charset are DROPPED (never repaired):
    repairing would let an attacker shape the surviving text.
    """
    if not isinstance(labels, dict):
        raise ValueError("labels is not an object")
    clean, dropped = {}, []
    for key in sorted(labels):
        if key not in allowed_keys or not isinstance(key, str):
            dropped.append(str(key)[:40])
            continue
        value = labels[key]
        pattern = ALERTNAME_RE if key == "alertname" else LABEL_VALUE_RE
        if not isinstance(value, str) or not pattern.match(value):
            dropped.append(key)
            continue
        clean[key] = value
        if len(clean) >= MAX_LABELS:
            break
    for req in REQUIRED_LABELS:
        if req not in clean:
            raise ValueError(f"required label {req} missing or failed validation")
    return clean, dropped


def dedup_key(alertname, namespace):
    return hashlib.sha256(f"{alertname}\x00{namespace}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------------- state
class State:
    """Persistent counters + per-fault records. All access goes through `lock`."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"days": {}, "faults": {}}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict) or "days" not in loaded or "faults" not in loaded:
                raise ValueError("unexpected state shape")
            self.data = loaded
        except FileNotFoundError:
            self._save()  # also proves the path is writable
        # any other error propagates: a corrupt state file must stop the process, not reset the caps

    def _save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def commit(self):
        self._save()


def decide(cfg, state, alertname, namespace, now):
    """Pure policy. Returns (action, reason). action is 'spawn' or 'skip'. Caller holds state.lock."""
    if cfg["paused"]:
        return "skip", "paused"
    if alertname not in cfg["allowlist"]:
        return "skip", "not-allowlisted"
    key = dedup_key(alertname, namespace)
    fault = state.data["faults"].get(key)
    epoch = now.timestamp()
    if fault:
        if fault.get("status") == "open" and epoch - fault["spawnedAt"] < cfg["openTtlSeconds"]:
            return "skip", "open-session-exists"
        if epoch - fault["spawnedAt"] < cfg["cooldownSeconds"]:
            return "skip", "cooldown"
    day = state.data["days"].get(now.strftime("%Y-%m-%d"), {"spawns": 0})
    if day["spawns"] >= cfg["dailySpawnCap"]:
        return "skip", "daily-spawn-cap"
    if (day["spawns"] + 1) * cfg["estimatedSessionCostUsd"] > cfg["dailySpendCapUsd"]:
        return "skip", "daily-spend-cap"
    return "spawn", "ok"


def reserve(state, alertname, namespace, now):
    """Count the attempt BEFORE the network call so a crash or timeout can never double-spawn."""
    key = dedup_key(alertname, namespace)
    day = now.strftime("%Y-%m-%d")
    bucket = state.data["days"].setdefault(day, {"spawns": 0})
    bucket["spawns"] += 1
    state.data["days"] = {d: v for d, v in state.data["days"].items() if d >= day}  # keep today only
    state.data["faults"][key] = {"status": "open", "spawnedAt": now.timestamp(), "alertname": alertname,
                                 "namespace": namespace, "sessionId": None, "sessionUrl": None}
    state.commit()
    return key


def close_fault(state, alertname, namespace):
    key = dedup_key(alertname, namespace)
    fault = state.data["faults"].get(key)
    if fault and fault.get("status") == "open":
        fault["status"] = "closed"
        state.commit()
        return True
    return False


# --------------------------------------------------------------------- outbound HTTP
class Http:
    """Thin urllib wrapper; swapped for a fake in tests."""

    def post_json(self, url, body, headers, timeout=15):
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_BODY).decode()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            return exc.code, {}


class TokenMinter:
    """Mints a FuzeFront workload token from the pod's projected SA token; cached until near expiry."""

    def __init__(self, cfg, http, clock=time.time):
        self.cfg, self.http, self.clock = cfg, http, clock
        self.cached = None

    def get(self):
        if self.cached and self.clock() < self.cached[1] - 30:
            return self.cached[0]
        with open(self.cfg["projectedTokenFile"], encoding="utf-8") as fh:
            sa_token = fh.read().strip()
        status, body = self.http.post_json(self.cfg["securityTokenUrl"], {"serviceAccountToken": sa_token}, {})
        token, ttl = body.get("accessToken"), body.get("expiresIn")
        if status != 200 or not isinstance(token, str) or not isinstance(ttl, int) or ttl <= 0:
            raise RuntimeError(f"workload token mint failed (http {status})")
        self.cached = (token, self.clock() + min(ttl, 900))
        return token


def build_request(brief, labels, starts_at, key):
    """The brief is a fixed constant; labels travel only inside `data`."""
    data = {"alertname": labels["alertname"], "namespace": labels["namespace"], "labels": labels,
            "dedupKey": key}
    if isinstance(starts_at, str) and TIMESTAMP_RE.match(starts_at):
        data["startsAt"] = starts_at
    return {"brief": brief, "data": data, "source": "fuzeinfra/alert-session-dispatcher"}


def create_session(cfg, http, minter, request_body):
    token = minter.get()
    status, body = http.post_json(cfg["agentSessionUrl"], request_body, {"Authorization": f"Bearer {token}"}, timeout=30)
    session_id, session_url = body.get("sessionId"), body.get("sessionUrl")
    if status not in (200, 201, 202) or not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise RuntimeError(f"agent session create failed (http {status})")
    if not isinstance(session_url, str) or not SESSION_URL_RE.match(session_url):
        session_url = None
    return session_id, session_url


def audit_comment(cfg, http, github_token, labels, key, session_id, session_url):
    """Best-effort audit issue. Title/body are built only from validated labels and ids."""
    if not (cfg["auditRepo"] and github_token):
        return "skipped"
    body = {"title": f"[alert-session] {labels['alertname']} / {labels['namespace']}",
            "body": f"Responder session started for dedup key `{key}`.\n\n"
                    f"- session id: `{session_id}`\n- session link: {session_url or 'not returned'}\n"
                    f"- alert: `{labels['alertname']}` in `{labels['namespace']}`\n\n"
                    "Labels were passed to the agent as data. No secrets are recorded here.",
            "labels": ["alert-session"]}
    try:
        status, _ = http.post_json(f"https://api.github.com/repos/{cfg['auditRepo']}/issues", body,
                                   {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"})
    except Exception as exc:  # audit must never block or crash the dispatch path
        return f"error:{type(exc).__name__}"
    return "ok" if status == 201 else f"http-{status}"


# ----------------------------------------------------------------------- dispatcher
class Dispatcher:
    def __init__(self, cfg, state, http, minter, brief, github_token=""):
        self.cfg, self.state, self.http, self.minter = cfg, state, http, minter
        self.brief, self.github_token = brief, github_token

    def handle_alert(self, alert, now=None):
        now = now or utcnow()
        raw_labels = alert.get("labels") if isinstance(alert, dict) else None
        try:
            labels, dropped = sanitize_labels(raw_labels, self.cfg["labelKeys"])
        except ValueError as exc:
            log("rejected", reason=str(exc))
            return {"action": "reject", "reason": "invalid-labels"}
        if labels.get("severity") != "critical":
            return {"action": "skip", "reason": "not-critical"}
        name, ns = labels["alertname"], labels["namespace"]
        status = alert.get("status", "firing")
        with self.state.lock:
            if status == "resolved":
                closed = close_fault(self.state, name, ns)
                log("resolved", alertname=name, namespace=ns, closed=closed)
                return {"action": "skip", "reason": "resolved"}
            action, reason = decide(self.cfg, self.state, name, ns, now)
            if action != "spawn":
                log("skipped", alertname=name, namespace=ns, reason=reason)
                return {"action": "skip", "reason": reason}
            if self.cfg["dryRun"]:
                log("dry-run", alertname=name, namespace=ns, dropped=dropped)
                return {"action": "skip", "reason": "dry-run"}
            key = reserve(self.state, name, ns, now)
            try:
                session_id, session_url = create_session(
                    self.cfg, self.http, self.minter, build_request(self.brief, labels, alert.get("startsAt"), key))
            except Exception as exc:
                # The reservation stays: failed attempts count toward the cap and start the cooldown.
                log("spawn-failed", alertname=name, namespace=ns, key=key, error=type(exc).__name__, detail=str(exc)[:200])
                return {"action": "error", "reason": "spawn-failed"}
            fault = self.state.data["faults"][key]
            fault["sessionId"], fault["sessionUrl"] = session_id, session_url
            self.state.commit()
            audit = audit_comment(self.cfg, self.http, self.github_token, labels, key, session_id, session_url)
            log("spawned", alertname=name, namespace=ns, key=key, sessionId=session_id, sessionUrl=session_url,
                audit=audit, dropped=dropped)
            return {"action": "spawn", "reason": "ok", "sessionId": session_id}

    def handle_payload(self, payload):
        alerts = payload.get("alerts") if isinstance(payload, dict) else None
        if not isinstance(alerts, list) or len(alerts) > 100:
            return None
        group_status = payload.get("status", "firing")
        results = []
        for alert in alerts:
            if isinstance(alert, dict):
                alert.setdefault("status", group_status)
            results.append(self.handle_alert(alert))
        return results


def bearer_ok(header, expected):
    if not expected:
        return False
    return hmac.compare_digest((header or "").encode(), f"Bearer {expected}".encode())


def make_handler(dispatcher, inbound_token):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

        def do_POST(self):
            if self.path != "/alerts":
                return self._send(404, {"error": "not found"})
            if not bearer_ok(self.headers.get("Authorization"), inbound_token):
                return self._send(401, {"error": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._send(413, {"error": "bad length"})
            try:
                payload = json.loads(self.rfile.read(length))
            except ValueError:
                return self._send(400, {"error": "invalid json"})
            results = dispatcher.handle_payload(payload)
            if results is None:
                return self._send(400, {"error": "invalid payload"})
            self._send(200, {"results": results})

        def log_message(self, *args):  # silence default access log (it would print paths only, but stay quiet)
            pass

    return Handler


def main():
    from http.server import ThreadingHTTPServer

    config_dir = os.environ.get("CONFIG_DIR", "/config")
    with open(os.path.join(config_dir, "config.json"), encoding="utf-8") as fh:
        cfg = load_config(fh.read())
    with open(os.path.join(config_dir, "responder-brief.md"), encoding="utf-8") as fh:
        brief = fh.read()
    inbound = os.environ.get("ALERT_DISPATCH_INBOUND_TOKEN", "")
    if not inbound:
        sys.exit("ALERT_DISPATCH_INBOUND_TOKEN is not set; refusing to start (fail closed)")
    state = State(cfg["stateFile"])
    dispatcher = Dispatcher(cfg, state, Http(), TokenMinter(cfg, Http()), brief, os.environ.get("GITHUB_AUDIT_TOKEN", ""))
    log("started", dryRun=cfg["dryRun"], paused=cfg["paused"], allowlist=cfg["allowlist"])
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), make_handler(dispatcher, inbound)).serve_forever()


if __name__ == "__main__":
    main()
