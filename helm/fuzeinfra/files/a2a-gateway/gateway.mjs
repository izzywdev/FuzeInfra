#!/usr/bin/env node
/**
 * A2A delivery gateway (v0) — wakes + delivers into cloud Claude Code sessions.
 *
 * Why this exists: a self-hosted bridge INSIDE a cloud sandbox freezes when the
 * session goes idle, so it cannot receive/​wake an idle peer. Only Anthropic
 * server-side can wake an idle cloud session — via `claude -p "<msg>" --cloud
 * <session-id>`. This gateway runs on infra WE control (holds a Claude.ai account
 * login) and shells out to that CLI, so it can deliver to a peer even when idle.
 *
 *   Session A (active) --outbound HTTPS--> gateway --> `claude -p --cloud <B-id>`
 *                                                        --> Anthropic WAKES B + delivers
 *
 * Endpoints (bearer-gated except /healthz):
 *   GET  /healthz                       -> "ok"
 *   POST /register {name, session_id}   -> registry[name] = session_id
 *   GET  /registry                      -> the name->id map
 *   POST /send {to, text}               -> resolve `to` (name or cse_/session_ id),
 *                                          run `claude -p <text> --cloud <id>`, return result
 *
 * Auth (v0): FUZE_A2A_GATEWAY_TOKEN bearer. The Claude.ai ACCOUNT credential is
 * mounted at $CLAUDE_CONFIG_DIR (e.g. /creds) — a `claude auth login` .credentials.json,
 * provided as a sealed secret. ANTHROPIC_API_KEY MUST be unset so the CLI uses the
 * account login (an API key is "not sufficient" for --cloud).
 *
 * Deps: none beyond Node + the `claude` CLI on PATH. Durable version -> FuzeAgent.
 */
import http from "node:http";
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const PORT = parseInt(process.env.PORT || "8000", 10);
const TOKEN = process.env.FUZE_A2A_GATEWAY_TOKEN || ""; // empty => open (v0 dev only)
const REGISTRY_PATH = process.env.A2A_REGISTRY_PATH || "/tmp/a2a-gateway/registry.json";
const CLAUDE_BIN = process.env.CLAUDE_BIN || "claude";
const SEND_TIMEOUT_MS = parseInt(process.env.A2A_SEND_TIMEOUT_MS || "60000", 10);

function log(...a) { console.error("[a2a-gateway]", ...a); }

function loadRegistry() {
  try { return JSON.parse(readFileSync(REGISTRY_PATH, "utf8")); }
  catch { return {}; }
}
function saveRegistry(reg) {
  mkdirSync(dirname(REGISTRY_PATH), { recursive: true });
  writeFileSync(REGISTRY_PATH, JSON.stringify(reg, null, 2));
}
function resolve(to) {
  if (to.startsWith("cse_") || to.startsWith("session_")) return to;
  return loadRegistry()[to] || null;
}

function readBody(req) {
  return new Promise((res, rej) => {
    let b = ""; req.on("data", (c) => { b += c; if (b.length > 1e6) req.destroy(); });
    req.on("end", () => res(b)); req.on("error", rej);
  });
}

// Deliver via `claude -p "<text>" --cloud <id> --output-format json`. This is the
// documented queue-and-wake primitive: it wakes an idle cloud session and delivers.
function deliver(sessionId, text) {
  return new Promise((res) => {
    const env = { ...process.env };
    delete env.ANTHROPIC_API_KEY;   // account auth only; an API key is rejected by --cloud
    delete env.ANTHROPIC_BASE_URL;
    delete env.ANTHROPIC_AUTH_TOKEN;
    const p = spawn(CLAUDE_BIN, ["-p", text, "--cloud", sessionId, "--output-format", "json"],
      { env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "";
    const t = setTimeout(() => { p.kill("SIGKILL"); }, SEND_TIMEOUT_MS);
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("close", (code) => {
      clearTimeout(t);
      let parsed = null; try { parsed = JSON.parse(out.trim()); } catch { /* not json */ }
      res({ ok: code === 0, code, result: parsed, raw: parsed ? undefined : (out || err).slice(0, 2000) });
    });
    p.on("error", (e) => { clearTimeout(t); res({ ok: false, error: String(e) }); });
  });
}

function authed(req) {
  if (!TOKEN) return true; // open in v0 dev; set FUZE_A2A_GATEWAY_TOKEN in prod
  const h = req.headers["authorization"] || "";
  return h === `Bearer ${TOKEN}`;
}

const server = http.createServer(async (req, res) => {
  const send = (code, obj) => {
    const body = JSON.stringify(obj);
    res.writeHead(code, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
    res.end(body);
  };
  try {
    if (req.method === "GET" && (req.url === "/healthz" || req.url === "/")) return send(200, { ok: true });
    if (!authed(req)) return send(401, { ok: false, error: "unauthorized" });

    if (req.method === "GET" && req.url === "/registry") return send(200, { registry: loadRegistry() });

    if (req.method === "POST" && req.url === "/register") {
      const { name, session_id } = JSON.parse((await readBody(req)) || "{}");
      if (!name || !session_id) return send(400, { ok: false, error: "need name + session_id" });
      const reg = loadRegistry(); reg[name] = session_id; saveRegistry(reg);
      log(`register ${name} -> ${session_id}`);
      return send(200, { ok: true, registry: reg });
    }

    if (req.method === "POST" && req.url === "/send") {
      const { to, text } = JSON.parse((await readBody(req)) || "{}");
      if (!to || !text) return send(400, { ok: false, error: "need to + text" });
      const id = resolve(to);
      if (!id) return send(404, { ok: false, error: `unknown peer ${to}`, known: Object.keys(loadRegistry()) });
      log(`send -> ${to} (${id}) ${text.length} chars`);
      const r = await deliver(id, text);
      return send(r.ok ? 200 : 502, { to, session_id: id, ...r });
    }
    return send(404, { ok: false, error: "not found" });
  } catch (e) {
    return send(500, { ok: false, error: String(e) });
  }
});

server.listen(PORT, "0.0.0.0", () =>
  log(`listening on :${PORT} (auth=${TOKEN ? "bearer" : "OPEN-v0"}) claude=${CLAUDE_BIN}`));
