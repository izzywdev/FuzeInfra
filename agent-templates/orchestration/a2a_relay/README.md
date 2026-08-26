# A2A WSS relay (v0)

Brokers cloud↔cloud Claude Code session messages over **outbound WebSocket/443**,
because cloudflared quick tunnels are blocked in the Anthropic sandbox (port 7844
denied; only HTTPS/443 through the security proxy to allowlisted hosts works). Each
session's bridge (`agent-templates/environments/desktop/a2a-bridge/wss_bridge.py`)
dials this relay and registers by session-id; the relay routes messages by id.

```
Session A ──outbound WSS/443──► relay.prod.fuzefront.com ◄──outbound WSS/443── Session B
   register session=A                 route by 'to'                  register session=B
   inbound frame ──► write to $CLAUDE_CODE_MESSAGING_SOCKET ──► new turn in A
```

## Protocol
- Connect: `wss://relay.prod.fuzefront.com/ws?session=<session-id>[&token=<bearer>]`
- Send:   `{"to":"<peer-session-id>","text":"...","reply_to":"<optional>"}` (relay stamps `from`)
- Routed: the frame is delivered verbatim to `<to>`'s connection
- Error:  `{"type":"error","error":"...","to":"..."}` back to the sender
- Health: `GET /healthz` → 200 `ok` (same port)

## Auth
- **v0 = OPEN**: `FUZE_A2A_RELAY_TOKEN` unset on the server → any client may connect;
  the capability is the unguessable `cse_…` session-id. Reachable because the host has
  a Cloudflare Access **bypass** (machine callers must not hit OTP), same as handoff-mcp.
- **Hardening (FuzeAgent / FuzeSDLC#219)**: set `FUZE_A2A_RELAY_TOKEN` (SealedSecret
  `a2a-relay-secret`) and hand each session a **per-session minted** token instead of a
  shared bearer; tie identity to the agent GitHub-App/bot machine identity.

## Deploy
- Helm: `helm/fuzeinfra/templates/a2a-relay.yaml` (ConfigMap-embedded `relay.py` run by a
  stock `python:3.12-slim` — no image build for v0), gated by `a2aRelay.enabled`
  (off in `values.yaml`, on in `values-contabo.yaml`). Ingress host `relay.prod.fuzefront.com`.
- Cloudflare Access bypass: `terraform/contabo/cloudflare.tf` (`a2a_relay_access_enabled`).
- Token (when hardening): `deploy/sealed-secrets/a2a-relay-secret.yaml.template`.

## Local smoke test
```bash
pip install websockets==12.0
PORT=8000 python relay.py &
# terminal A:
python - <<'PY'
import asyncio, websockets, json
async def go():
    async with websockets.connect("ws://localhost:8000/ws?session=A") as ws:
        print(await ws.recv())  # waits for a routed msg
asyncio.run(go())
PY
# terminal B: connect as B and send {"to":"A","text":"hi"} — A should receive it.
```

Graduates to **FuzeAgent** (cross-provider agent orchestration + rendezvous); it lives
here only to unblock the spike (see `agent-templates/README.md`).
