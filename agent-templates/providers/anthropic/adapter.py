"""Anthropic Managed Agents provider — the reference `AgentProvider`.

Additive: delegates to the existing `sync/` modules (`common`, `driver`,
`role_loader`) rather than moving them, so the deployed handoff image and the
standalone sync scripts keep working. The idempotent create/update logic mirrors
`sync/sync_{environments,agents,vaults,memory}.py`.
"""
import glob
import json
import os
import sys

from providers.base import AgentProvider

_TEMPLATES_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SYNC = os.environ.get("HANDOFF_SYNC_DIR") or os.path.join(_TEMPLATES_ROOT, "sync")
if _SYNC not in sys.path:
    sys.path.insert(0, _SYNC)

import common          # noqa: E402
import driver          # noqa: E402
import role_loader as rl  # noqa: E402

# Agent fields we compare to decide whether an update is needed.
_COMPARE = ("model", "system", "description", "tools", "mcp_servers", "skills", "multiagent")


def _norm_model(m):
    return m["id"] if isinstance(m, dict) else m


def _agent_changed(current, desired):
    for k in _COMPARE:
        d = desired.get(k)
        if d is None:
            continue
        c = current.get(k)
        if k == "model":
            if _norm_model(c) != _norm_model(d):
                return True
        elif c != d:
            return True
    return False


def _cred_key(auth):
    return (auth or {}).get("mcp_server_url") or (auth or {}).get("secret_name")


def _subset(desired, live):
    """True if `desired` config is contained in `live` — extra live fields (API-added
    defaults) are ignored; lists compared order-independently. Used for environment
    drift detection so only a REAL config change triggers a recreate (envs aren't
    versioned and have no update endpoint)."""
    if isinstance(desired, dict):
        return isinstance(live, dict) and all(k in live and _subset(v, live[k]) for k, v in desired.items())
    if isinstance(desired, list):
        if not isinstance(live, list):
            return False
        canon = lambda xs: sorted(json.dumps(x, sort_keys=True) for x in xs)
        return canon(desired) == canon(live)
    return desired == live


class AnthropicProvider(AgentProvider):
    name = "anthropic"
    capabilities = {"self_hosted": True, "vaults": True, "memory": True, "multiagent": True}

    # ---- provisioning -------------------------------------------------------
    def ensure_environment(self, manifest):
        name = manifest["name"]
        desired = manifest["config"]
        live = next((e for e in common.list_all("/v1/environments") if e.get("name") == name), None)
        if live:
            cfg = live.get("config") or common.request("GET", f"/v1/environments/{live['id']}").get("config", {})
            if _subset(desired, cfg):
                return {"name": name, "id": live["id"], "created": False}
            # Config drifted. Environments are NOT versioned and have no update endpoint,
            # so archive the old (session-safe: running sessions continue) and recreate.
            try:
                common.request("POST", f"/v1/environments/{live['id']}/archive")
            except SystemExit:
                pass
            created = common.request("POST", "/v1/environments", body={"name": name, "config": desired})
            return {"name": name, "id": created["id"], "created": True, "recreated": True}
        created = common.request("POST", "/v1/environments", body={"name": name, "config": desired})
        return {"name": name, "id": created["id"], "created": True}

    def ensure_agent(self, manifest, multiagent=None):
        payload = rl.agent_payload(manifest)
        if multiagent:
            payload["multiagent"] = {"type": "coordinator", "agents": multiagent}
        existing = {a["name"]: a for a in common.list_all("/v1/agents")}
        name = payload["name"]
        if name in existing:
            cur = existing[name]
            if _agent_changed(cur, payload):
                updated = common.request("POST", f"/v1/agents/{cur['id']}",
                                         body={**payload, "version": cur["version"]})
                return {"name": name, "id": updated["id"], "version": updated["version"], "created": False}
            return {"name": name, "id": cur["id"], "version": cur["version"], "created": False}
        created = common.request("POST", "/v1/agents", body=payload)
        return {"name": name, "id": created["id"], "version": created["version"], "created": True}

    def ensure_vault(self, manifest):
        tmpl = common.expand_env(manifest)
        existing = {v["display_name"]: v["id"] for v in common.list_all("/v1/vaults")}
        name = tmpl["display_name"]
        if name in existing:
            vid = existing[name]
        else:
            body = {"display_name": name}
            if tmpl.get("metadata"):
                body["metadata"] = tmpl["metadata"]
            vid = common.request("POST", "/v1/vaults", body=body)["id"]
        have = {_cred_key(c.get("auth")): c for c in common.list_all(f"/v1/vaults/{vid}/credentials")
                if _cred_key(c.get("auth"))}
        for cred in tmpl.get("credentials", []):
            auth = cred["auth"]
            key = _cred_key(auth)
            # Skip a credential whose secret isn't actually provided: an unresolved ${VAR}
            # (env unset -> literal "${...}") OR an empty/whitespace value (env set to "").
            if not key or "${" in json.dumps(auth):
                continue
            if "token" in auth and not str(auth.get("token") or "").strip():
                continue
            if key in have:
                # Rotate: push the current secret (values are write-only, so we can't
                # compare — always re-set). Structural fields (mcp_server_url/secret_name)
                # are immutable, so send only the mutable auth fields.
                upd = {k: v for k, v in auth.items() if k not in ("mcp_server_url", "secret_name")}
                common.request("POST", f"/v1/vaults/{vid}/credentials/{have[key]['id']}", body={"auth": upd})
            else:
                common.request("POST", f"/v1/vaults/{vid}/credentials",
                               body={"display_name": cred["display_name"], "auth": auth})
        return {"name": name, "id": vid}

    def ensure_memory(self, manifest):
        existing = {s["name"]: s["id"] for s in common.list_all("/v1/memory_stores", beta=common.MEMORY_BETA)}
        name = manifest["name"]
        if name in existing:
            sid = existing[name]
        else:
            sid = common.request("POST", "/v1/memory_stores",
                                 body={"name": name, "description": manifest.get("description", "")},
                                 beta=common.MEMORY_BETA)["id"]
        have = {m["path"]: m for m in common.list_all(f"/v1/memory_stores/{sid}/memories", beta=common.MEMORY_BETA)
                if m.get("path")}
        for mem in manifest.get("seed", []):
            cur = have.get(mem["path"])
            if cur:
                # Update the seed content if it changed (memory content IS readable).
                full = common.request("GET", f"/v1/memory_stores/{sid}/memories/{cur['id']}", beta=common.MEMORY_BETA)
                if full.get("content") != mem["content"]:
                    common.request("POST", f"/v1/memory_stores/{sid}/memories/{cur['id']}",
                                   body={"content": mem["content"]}, beta=common.MEMORY_BETA)
            else:
                common.request("POST", f"/v1/memory_stores/{sid}/memories",
                               body={"path": mem["path"], "content": mem["content"]}, beta=common.MEMORY_BETA)
        return {"name": name, "id": sid}

    # ---- runtime (thin delegations to the driver) ---------------------------
    def create_session(self, agent_id, version, environment_id, vault_ids=None, memory_resources=None, title=None):
        return driver.create_session(agent_id, version, environment_id,
                                     vault_ids=vault_ids, resources=memory_resources, title=title)["id"]

    def send_message(self, session_id, text):
        driver.send_message(session_id, text)

    def run_turn(self, session_id, prompt, approver, echo=True):
        return driver.run_turn(session_id, prompt, approver, echo=echo)

    def run_until_block(self, session_id, prompt=None):
        return driver.run_until_block(session_id, prompt)

    def resume_session(self, session_id, summary, context_ref=""):
        msg = f"[handoff:resume] {summary}"
        if context_ref:
            msg += f"\n\nPersisted state: {context_ref} (read it for details)."
        driver.send_message(session_id, msg)

    def confirm_tool(self, session_id, tool_use_id, allow=True, deny_message=None):
        driver.confirm_tool(session_id, tool_use_id, allow=allow, deny_message=deny_message)

    def archive_session(self, session_id):
        driver.archive_session(session_id)

    # ---- memory -------------------------------------------------------------
    def memory_resource(self, store_id, access="read_write", instructions=None):
        return driver.memory_resource(store_id, access=access, instructions=instructions)

    def memory_write(self, store_id, path, content):
        common.request("POST", f"/v1/memory_stores/{store_id}/memories",
                       body={"path": path, "content": content}, beta=common.MEMORY_BETA)
        return {"store_id": store_id, "path": path}

    def memory_read(self, store_id, path):
        res = common.request("GET", f"/v1/memory_stores/{store_id}/memories",
                             query={"path": path}, beta=common.MEMORY_BETA)
        data = res.get("data", res)
        return data[0]["content"] if isinstance(data, list) and data else (data.get("content") if isinstance(data, dict) else None)
