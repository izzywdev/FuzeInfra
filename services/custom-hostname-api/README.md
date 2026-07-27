# Custom Hostname API

Cluster-internal service that attaches arbitrary **customer-owned domains**
(`app.corpabc.com`) to a consumer workload at runtime — with no Helm release per
domain.

The design rationale, security model, cost table, and the consuming-repo
integration guide live in
[`docs/consuming-repos/CUSTOM_DOMAINS.md`](../../docs/consuming-repos/CUSTOM_DOMAINS.md).
This README is the operator/developer view.

## What it does

Each `POST /custom-hostnames` does two things, because either one alone leaves a
broken customer domain:

1. **Registers the hostname with Cloudflare for SaaS** — ownership validation
   plus DV certificate issuance and deployment at the Cloudflare edge, where TLS
   is already terminated. The origin stays HTTP-only, so the tunnel-only
   invariant (Traefik pinned to `ClusterIP`) is untouched.
2. **Materializes a per-domain `Ingress`** in the consumer's namespace, so
   Traefik host-routes the domain. Cloudflare does not do this, and without it a
   customer gets a Traefik 404 behind a perfectly valid certificate.

The Ingress is written **without Argo CD tracking metadata**, so `selfHeal` never
reverts it and `prune` never deletes it. A test enforces that.

## Contract

`openapi.yaml` is the frozen contract consumers generate clients from.
`tests/test_custom_hostname_api.py::TestFrozenContract` diffs the running app's
generated schema against it, so drift fails CI.

```
POST   /custom-hostnames            { domain }   201 | 200 if already known
GET    /custom-hostnames/{domain}                200
GET    /custom-hostnames                         200  (caller's domains only)
DELETE /custom-hostnames/{domain}                204  (idempotent)
GET    /healthz  /readyz                              (unauthenticated)
```

## Configuration

All environment-driven (`app/config.py`); the Helm chart is the source of truth.

| Variable | Default | Notes |
|---|---|---|
| `PROVIDER` | `stub` | `cloudflare` or `stub` |
| `CLOUDFLARE_API_TOKEN` | — | Required for `cloudflare`. Scope: SSL and Certificates:Edit + Zone:Read, one zone |
| `CLOUDFLARE_ZONE_ID` | — | Required for `cloudflare` |
| `MANAGED_ZONE` | `fuzefront.com` | Domains inside it are rejected (422) — the wildcard already serves them |
| `RESERVED_ZONES` | — | Comma-separated additional rejected zones |
| `CNAME_TARGET` | `connect.fuzefront.com` | Published to customers |
| `MAX_CUSTOM_HOSTNAMES` | `100` | Soft cap checked before calling Cloudflare; `0` disables |
| `ROUTING_ENABLED` | `true` | Materialize the Ingress. `false` = Cloudflare-only |
| `ROUTE_PROFILES` / `ROUTE_PROFILES_FILE` | — | YAML/JSON profile list |
| `CONSUMER_TOKEN_<PROFILE>` | — | Bearer token per profile |
| `STUB_ACTIVATE_AFTER_SECONDS` | `20` | Stub only: how long the lifecycle takes |

### Route profiles

A bearer token maps to exactly one profile, and the profile is the authorization
boundary — it pins which namespace/Service a caller may route domains at.

```yaml
- name: fuzefront
  namespace: fuzefront
  service: fuzefront-frontend
  port: 80
  paths: ["/", "/api", "/socket.io"]
  ingressClass: traefik
  tokenEnv: CONSUMER_TOKEN_FUZEFRONT
```

A profile whose token env var is missing is loaded but **unusable** — it can
never match a request, so a half-applied SealedSecret fails closed (and shows up
as `NotReady` via `/readyz`, not as a silent 401 storm).

## Run it locally

```bash
pip install -r requirements.txt
PROVIDER=stub ROUTING_ENABLED=false \
  ROUTE_PROFILES='- {name: fuzefront, namespace: fuzefront, service: fuzefront-frontend, port: 80, tokenEnv: T}' \
  T=dev uvicorn app.main:app --port 8080

curl -sS -X POST localhost:8080/custom-hostnames \
  -H 'Authorization: Bearer dev' -H 'Content-Type: application/json' \
  -d '{"domain":"app.corpabc.test"}' | jq
```

On kind the chart enables this automatically in stub mode — see
`helm/fuzeinfra/values-local.yaml`.

## Tests

```bash
pip install -r tests/requirements.txt        # from the repo root
pytest tests/test_custom_hostname_api.py
```

Fully offline: Cloudflare runs against `httpx.MockTransport` and the routing
layer against a fake Kubernetes API. No cluster, no Cloudflare account.

## Build

```bash
docker build -t ghcr.io/izzywdev/fuzeinfra/custom-hostname-api:dev services/custom-hostname-api
```

CI publishes on merge to `main` via `.github/workflows/custom-hostname-api-image.yml`.

## Layout

```
app/
  main.py            FastAPI routes
  service.py         composes provider state + routing into the API resource
  providers/
    cloudflare.py    Cloudflare for SaaS; status normalization lives here
    stub.py          local state-machine emulation
  routing.py         Kubernetes Ingress materialization
  auth.py            bearer token -> route profile
  config.py          environment-driven settings
  models.py          pydantic mirror of openapi.yaml
openapi.yaml         FROZEN CONTRACT
```
