# Multi-Tenant Portal DNS/TLS + Custom-Domain Provisioning

FuzeInfra's answer to the FuzeFront capability request for multi-tenant portal
addressing (EPIC-16 / FFRNT-91, unblocking S2 / FFRNT-99 and FFRNT-108).

**TL;DR** — Cloudflare-terminated TLS everywhere. Tenant subdomains are a static
wildcard with zero per-tenant work. Customer domains use **Cloudflare for SaaS
(Custom Hostnames)**, driven at runtime by a new cluster-internal API. There is
one thing the original request did not account for, and it is the reason this is
a service rather than pure Terraform: **Cloudflare for SaaS does not teach
Traefik about the new host**, so the same call also materializes a per-domain
`Ingress`. Details in [§4.2](#42-why-a-service-and-not-just-cloudflare).

| # | Ask | Answer | Where |
|---|-----|--------|-------|
| 1 | Wildcard DNS `*.fuzefront.com` | Proxied CNAME to the existing tunnel. Reserved hosts need no exclusion list. | `terraform/contabo/cloudflare.tf` |
| 2 | Wildcard TLS | **Cloudflare-terminated** (Universal SSL). No cert-manager, no DNS-01, no new secret. | — (already covered) |
| 3 | Ingress for the wildcard host | Yours to add; exact YAML in [§3](#3-the-ingress-change-fuzefront-makes). | FuzeFront chart |
| 4 | Custom-domain provisioning | **Cloudflare for SaaS** + per-domain Ingress materialization, behind an authenticated internal API. | `services/custom-hostname-api/` |
| 5 | Authentik on custom domains | Reachable, no constraint. One caveat about redirect-URI registration. | [§7](#7-authentik) |
| 6 | Cookies | No infra involvement, confirmed. | [§8](#8-cookies-fyi) |

---

## 1. Tenant subdomains — `corpabc.fuzefront.com`

```hcl
# terraform/contabo/cloudflare.tf   (var tenant_wildcard_enabled)
resource "cloudflare_record" "tenant_wildcard" {
  name    = "*"
  value   = <the fuzeinfra tunnel CNAME>
  type    = "CNAME"
  proxied = true
}
```

Traffic path: `corpabc.fuzefront.com` → Cloudflare edge → cloudflared → the
existing catch-all tunnel rule → `traefik.kube-system:80` → your Ingress.

> ### ⚠️ What actually gates `*.fuzefront.com` — it is NOT this Terraform variable
>
> `tenant_wildcard_enabled` controls whether **Terraform manages** the wildcard
> record. It does **not** control whether `*.fuzefront.com` resolves.
>
> A proxied wildcard CNAME for `*.fuzefront.com` **already exists in the zone**,
> created outside this Terraform. So arbitrary tenant hosts have been resolving
> all along: `tenant-probe.fuzefront.com` and `corpabc.fuzefront.com` both answer
> the moment something in the cluster claims the host.
>
> **Therefore the Ingress rule is the only gate on serving.** Merging the §3
> Ingress change is a live production change that takes effect on the next Argo
> sync — there is no DNS staging behind it. An earlier revision of this section
> implied otherwise, and "enabling the Ingress rule is safe until Terraform is
> applied" was a reasonable and wrong reading of it.
>
> **Terraform collision hazard:** because the record pre-exists outside state,
> applying `tenant_wildcard_enabled = true` on a zone that already has one will
> fail on a duplicate record rather than adopt it. Before that apply, check:
>
> ```bash
> # does a wildcard already exist, and is Terraform tracking it?
> dig +short '*.fuzefront.com'
> terraform -chdir=terraform/contabo state list | grep tenant_wildcard
> ```
>
> If it resolves but is absent from state, `terraform import` it before applying:
>
> ```bash
> terraform -chdir=terraform/contabo import \
>   'cloudflare_record.tenant_wildcard[0]' '<zone_id>/<record_id>'
> ```
>
> Read the PR's plan output before merging — a `destroy`/`create` pair on the
> wildcard means Terraform is about to replace a record that is already serving
> production traffic.

**Reserved hosts need no exclusion list — at the DNS layer.** DNS resolves the
most specific match first, so the explicit `app`, `auth`, `plan` and `prod`
records and the `*.prod.fuzefront.com` wildcard all take precedence over `*`
automatically. Reserving a new host later means *adding a record*, not editing an
exclusion.

**This is a DNS-layer statement only, and it does not carry over to Ingress
routing.** Cloudflare resolves `plan.fuzefront.com` to its own record, but the
request still arrives at Traefik, where a wildcard Ingress rule can and did
capture it. Specificity wins in DNS; **rule length** wins in Traefik. See §3.

Tenant subdomains are **public by default** — they sit outside the `*.prod`
Access application, so Authentik owns their auth, exactly like
`app.fuzefront.com` does today.

### 2. Wildcard TLS — Cloudflare-terminated, and here is why

You offered a choice; take Cloudflare-terminated.

Cloudflare **Universal SSL** already covers `fuzefront.com` and
`*.fuzefront.com` on every plan. So tenant subdomains get a valid certificate
with **no cert-manager, no DNS-01 solver, no Cloudflare API token in the
cluster, and no new Secret**. Enabling the wildcard record is the entire job.

The cert-manager DNS-01 alternative is worse here for a structural reason, not
just a convenience one. Prod ingress is tunnel-only: Traefik is pinned to
`ClusterIP` (`argocd/cluster-bootstrap/traefik-clusterip.yaml`) precisely so k3s
servicelb never binds host ports 80/443. Terminating TLS in-cluster means giving
Traefik a real TLS entrypoint and a certificate to serve, which is the thing the
tunnel architecture exists to avoid. Cloudflare already terminates TLS one hop
earlier; adding a second termination point buys nothing and costs the invariant.

⚠️ **One-level limit.** Universal SSL covers exactly one wildcard label.
`corpabc.fuzefront.com` ✅. `eu.corpabc.fuzefront.com` ❌ — that would need
Cloudflare Advanced Certificate Manager (~$10/month). If nested tenant
subdomains are on your roadmap, say so now and we will price ACM rather than
discover it in production.

---

## 3. The Ingress change FuzeFront makes

This lives in your chart, not ours. Kubernetes Ingress supports wildcard hosts,
matching **exactly one label** — the same granularity as Universal SSL, which is
a convenient coincidence.

> ### ⚠️ Read this before adding a wildcard host on Traefik
>
> **On Traefik, a wildcard host rule outranks an exact host rule by default, and
> it will capture other products' hosts on the shared cluster.**
>
> An earlier revision of this section claimed "exact hosts beat wildcards, order
> does not matter." That is true of the Kubernetes Ingress spec and of
> ingress-nginx. It is **false for Traefik**, which is what prod uses. Acting on
> it took `plan.fuzefront.com` — a different product, in its own namespace, with
> its own exact-host Ingress — down onto the FuzeFront shell within minutes
> (FuzeFront#431, reverted in #437). The rest of this section is the corrected
> guidance; §3.1 is the pattern to actually use.

### Why Traefik behaves this way

Traefik sorts routers by **rule length**, not by host specificity. From its own
[rules and priority docs](https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/rules-and-priority/):

> To avoid path overlap, routes are sorted, by default, in descending order using
> rules length. The priority is directly equal to the length of the rule, and so
> the longest length has the highest priority.

Traefik's documentation makes the consequence explicit with the exact case we hit:

| Router | Rule | Priority |
|---|---|---|
| Router-1 | ``HostRegexp(`[a-z]+\.traefik\.com`)`` | **34** |
| Router-2 | ``Host(`foobar.traefik.com`)`` | **26** |

> Router-1 handles requests to `foobar.traefik.com` instead of Router-2, **despite
> Router-2 being more specific**.

A Kubernetes wildcard host is compiled into a longer rule string than any exact
host in the same zone, so the wildcard wins. ``Host(`plan.fuzefront.com`)``
computes to 26 by the same arithmetic — the shorter, losing side.

**Do not reason about which rule happens to be longer.** The generated rule
string differs between Traefik versions (v2 emits `HostRegexp`, v3 can emit a
wildcard `Host`), so the default ordering can silently flip under you on a k3s
upgrade. Set the priority explicitly and the version stops mattering.

To read the priorities your cluster actually computed:

```bash
kubectl -n kube-system port-forward deploy/traefik 9000:9000
curl -s localhost:9000/api/http/routers | jq '.[] | {rule, priority, service}'
```

(The API is on the `traefik` container port 9000. If it 404s, the dashboard/API
is disabled in the k3s HelmChartConfig — the priority annotation below is still
the correct fix, you just cannot observe the numbers.)

### 3.1 The pattern to use — wildcard alone, priority low

Put the wildcard rule in its **own Ingress object** with a low
`traefik.ingress.kubernetes.io/router.priority`, so every exact-host router in
the cluster outranks it:

```yaml
# fuzefront chart — Ingress #1: canonical host. UNCHANGED, no annotation.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: fuzefront
spec:
  ingressClassName: traefik
  rules:
    - host: app.fuzefront.com
      http: &fanout
        paths:
          - { path: /,          pathType: Prefix, backend: { service: { name: fuzefront-frontend, port: { number: 80 } } } }
          - { path: /api,       pathType: Prefix, backend: { service: { name: fuzefront-backend,  port: { number: 3001 } } } }
          - { path: /socket.io, pathType: Prefix, backend: { service: { name: fuzefront-backend,  port: { number: 3001 } } } }
---
# fuzefront chart — Ingress #2: tenant subdomains. SEPARATE OBJECT, low priority.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: fuzefront-tenant-wildcard
  annotations:
    # Lowest-wins: any exact-host router (priority = its rule length, ~26+)
    # outranks this, so the wildcard only ever serves hosts nothing else claims.
    traefik.ingress.kubernetes.io/router.priority: "1"
spec:
  ingressClassName: traefik
  rules:
    - host: "*.fuzefront.com"
      http: *fanout
```

**The annotation is per-Ingress-object, not per-rule.** That is why the wildcard
must live in its own object: a wildcard rule sharing an object with the canonical
host cannot be de-prioritised separately, and annotating the shared object would
drag `app.fuzefront.com` down with it.

Note also that `priority: 0` is **not** "lowest" — Traefik treats 0 as unset and
falls back to length sorting. Use `1`.

### 3.2 Shared-cluster warning — this affects other products

A single-label wildcard on `fuzefront.com` matches **every** single-label host in
that zone, including hosts owned by products that have nothing to do with you.
`plan.fuzefront.com` belongs to FuzePlan, in the `fuzeplan` namespace, declared
in a different repository — and the wildcard captured it anyway, because Traefik
routes by rule, not by namespace or ownership.

Before adding any wildcard host to this cluster, enumerate what you are about to
shadow:

```bash
# every single-label host in the zone — these are the hosts a *.fuzefront.com
# wildcard can capture
kubectl get ingress -A -o json \
  | jq -r '.items[] | .metadata.namespace as $ns | .spec.rules[]?.host
           | select(test("^[^.]+\\.fuzefront\\.com$"))
           | "\($ns)\t\(.)"'
```

`*.prod.fuzefront.com` hosts (argocd, grafana, prometheus, kafka-ui, neo4j,
authentik-admin) are **not** at risk: they carry two labels and a k8s wildcard
host matches exactly one. The Cloudflare Access OTP wall is likewise unaffected —
it gates at the edge, before Traefik.

### 3.3 Why the usual checks do not catch this

- **`helm template | kubeconform -strict` passes.** The manifest is valid; the
  defect is in runtime router ordering, which no schema validator models.
- **Local kind uses `ingress-nginx`, prod uses Traefik.** ingress-nginx *does*
  implement exact-beats-wildcard, so the rule behaves correctly locally and only
  misbehaves in prod. Local validation is structurally incapable of catching it.
- **A parsed check that both rules carry an identical path fan-out passes** — the
  fan-out was never the problem.
- **The canonical host masks it.** `app.fuzefront.com` fans out to the same
  backends whether it matches its own rule or the wildcard, so it looks healthy
  either way. The blast radius is only visible on *other* single-label hosts.

The only checks that catch it are the ownership enumeration in §3.2 and a
regression test asserting an exact-host router outranks the wildcard. FuzeInfra
ships the latter for its own chart
(`tests/test_ingress_wildcard_priority.py`); mirror it in your repo, since your
Ingress lives in your chart and FuzeInfra's tests cannot see it.

### 3.4 Remaining review notes

- **No `tls:` block.** Cloudflare terminates edge TLS and the tunnel delivers
  plain HTTP to Traefik. Every FuzeInfra Ingress works this way; adding a `tls:`
  block here would either do nothing or break the path.
- Keep the `/api` and `/socket.io` fan-out identical between the two rules, or a
  tenant subdomain will silently behave differently from the canonical host. A
  YAML anchor as above makes that structural rather than a review item.
- `ingressClassName: traefik` in prod. On kind the chart uses `nginx` — see §3.3
  for why that divergence matters.

---

## 4. Custom customer domains — `app.corpabc.com`

### 4.1 Mechanism: Cloudflare for SaaS (Custom Hostnames)

Your preference is the right call, for the same reason as §2: certificate
issuance happens at the edge, where TLS is already terminated, so the origin
stays HTTP-only and the tunnel model is untouched. Cloudflare also handles
renewal, OCSP, and cipher policy — none of which we want to own per customer.

Two static records plus one zone setting, all in Terraform
(`saas_custom_hostnames_enabled`, default off):

| Record | Role |
|---|---|
| `connect.fuzefront.com` | The CNAME target published **to customers**. This is a public contract — deliberately separate from the origin so the origin can be repointed during a migration without asking every customer to change DNS. |
| `saas-origin.fuzefront.com` | The Cloudflare for SaaS **fallback origin** — where the edge sends custom-hostname traffic. Proxied → resolves through Cloudflare to the tunnel. |

Traffic path once active:

```
browser --TLS(app.corpabc.com)--> Cloudflare edge     [custom hostname cert; Host preserved]
    --> saas-origin.fuzefront.com --> cloudflared --> traefik.kube-system:80
    --> the Ingress the API materialized --> fuzefront-frontend
```

### 4.2 Why a service, and not just Cloudflare

**This is the part worth reading carefully.** Cloudflare for SaaS solves DNS,
ownership validation, and TLS. It does not solve in-cluster routing. After the
edge is happy, the request still arrives at Traefik carrying
`Host: app.corpabc.com`, and Traefik host-routes strictly by Ingress rule. With
no matching rule the customer gets a **Traefik 404, not your portal** — with a
perfectly valid certificate in front of it, which is the worst possible failure
mode to debug.

So each custom hostname needs exactly one small `Ingress`. The API creates it in
the same call that registers the hostname, which is what keeps the "no Helm
release per domain" requirement intact.

Two alternatives were considered and rejected:

- **A host-less catch-all Ingress** (FuzeFront as Traefik's default backend).
  One static object, no runtime writes — but it makes FuzeFront the default for
  *every* unrouted host in the shared cluster, including hosts belonging to
  other products. It converts Traefik's "404 for unconfigured hosts" safety
  property into a silent mis-route platform-wide. Not acceptable in a shared
  cluster.
- **A Cloudflare Worker rewriting `Host` to `app.fuzefront.com`.** Zero
  Kubernetes objects — but it forces you to resolve the portal from
  `X-Forwarded-Host` instead of `Host`, which is a contract change on your side
  and one more place for the real host to get lost.

**Does a runtime write violate GitOps?** No, and this was checked rather than
assumed. Argo CD prunes only resources it *tracks* — objects carrying its
tracking metadata that have vanished from the desired state. These Ingresses are
written **without any Argo tracking metadata**, into your namespace, so they are
invisible to the FuzeInfra Application's reconcile loop. `selfHeal` will not
revert them and `prune` will not delete them. There is a test asserting the
manifest carries no Argo metadata, so a future refactor cannot quietly break
this (`tests/test_custom_hostname_api.py::test_manifest_carries_no_argo_tracking_metadata`).

The GitOps rule FuzeInfra actually holds — "never hand-deploy, never
`kubectl patch` a chart-managed resource" — is untouched. Every *chart-managed*
object still flows through Git → Argo. These per-domain Ingresses are runtime
data, not desired state, in the same way a row in a database is.

### 4.3 The API

Frozen contract: **`services/custom-hostname-api/openapi.yaml`** (OpenAPI 3.1) —
generate your client from that file. The service's generated schema is diffed
against it in CI, so drift fails the build.

```
POST   /custom-hostnames            { domain }  -> 201 (or 200 if already known)
GET    /custom-hostnames/{domain}               -> 200 status snapshot
GET    /custom-hostnames                        -> 200 your domains only
DELETE /custom-hostnames/{domain}               -> 204
GET    /healthz  /readyz                        -> unauthenticated probes
```

`POST` example:

```jsonc
// -> POST /custom-hostnames  { "domain": "app.corpabc.com" }
{
  "domain": "app.corpabc.com",
  "profile": "fuzefront",
  "active": false,
  "dns_status": "pending",
  "tls_status": "pending_validation",
  "verification": {
    "method": "txt",
    "record": "_cf-custom-hostname.app.corpabc.com",
    "value": "3b3a5f8c-1f3d-4d1f-9d63-9f0b2e1c4a77",
    "records": [
      { "purpose": "ownership",   "method": "txt",   "record": "_cf-custom-hostname.app.corpabc.com", "value": "3b3a5f8c-…" },
      { "purpose": "certificate", "method": "txt",   "record": "_acme-challenge.app.corpabc.com",     "value": "GHi3mDIVQuKL…" },
      { "purpose": "routing",     "method": "cname", "record": "app.corpabc.com",                     "value": "connect.fuzefront.com" }
    ]
  },
  "routing": { "cname_target": "connect.fuzefront.com", "ingress_ready": true, "ingress_name": "custom-domain-app-corpabc-com-1a2b3c4d" },
  "certificate": null,
  "error": null,
  "provider": { "name": "cloudflare_for_saas", "id": "0d89c70d-…", "status": "pending", "ssl_status": "pending_validation" },
  "created_at": "2026-07-27T10:00:00Z"
}
```

`records[]` is the complete set to render in your UI. The top-level
`method`/`record`/`value` mirror the primary (ownership) record for callers that
only want one.

**`active` is the only field you need to gate on.** It is true only when DNS
validation passed, the certificate is deployed, *and* the routing Ingress
exists. Any one of those missing means a customer who visits the domain gets an
error, so collapsing them into one boolean is deliberate.

### 4.4 Domain verification — drop `_fuzefront-verify`

Cloudflare's own hostname validation **replaces** your TXT token. Cloudflare
issues `_cf-custom-hostname.<domain>` and checks it itself; that is
cryptographic proof of DNS control by the party that will also be issued a
certificate. A second `_fuzefront-verify` TXT would prove the same fact, checked
by a weaker verifier (your resolver, subject to caching and split-horizon), and
would add a third record to every customer's onboarding.

Recommendation: **delete the `_fuzefront-verify` generation from EPIC-16** and
surface `verification.records[]` instead. Keep your `portal_domains` row and its
state machine — just source the token from us.

If you want ownership proof *before* a customer is willing to point DNS at us
(a reasonable product requirement), that already works: we create the hostname
with **TXT DCV**, which validates without any traffic reaching us. The customer
can publish the two TXT records, watch `tls_status` go `active`, and only then
cut the CNAME over — a zero-downtime migration for a domain already serving
their old site. That is the main reason for TXT over HTTP DCV.

### 4.5 Pollable status

`GET /custom-hostnames/{domain}` returns two normalized enums plus `active`:

| `tls_status` | Meaning | UI |
|---|---|---|
| `pending_validation` | Waiting on the customer's TXT records | "Waiting for DNS — add these records" |
| `pending_issuance` | Records seen, CA issuing | "Issuing certificate…" |
| `pending_deployment` | Issued, propagating to the edge | "Almost ready…" |
| `active` | Serving | ✅ |
| `failed` | Validation failed; `error` has the reason | Show `error`, offer retry (re-POST) |
| `expired` | Renewal failed | Show `error`, offer retry |

`dns_status`: `pending` · `active` · `moved` (stopped pointing at us) ·
`blocked` (claimed by another Cloudflare account) · `error`.

Both enums are **ours**, mapped from Cloudflare's rawer vocabulary, so a
Cloudflare-side vocabulary change does not break your UI. Unknown upstream
states deliberately map to `pending`, never to a failure. The raw values are
echoed in `provider.status` / `provider.ssl_status` for debugging — **do not
branch on them**; they are explicitly outside the frozen contract.

**Suggested polling:** every 10s for the first 2 minutes, then every 60s.
Issuance normally completes 30s–5min after the records resolve. `GET` is a
Cloudflare API call per request, so please do not poll faster than 10s per
domain.

### 4.6 Apex domains — the honest answer

Publish this to customers, in this order of preference:

1. **`app.corpabc.com` (subdomain) — the supported path.** A plain `CNAME` to
   `connect.fuzefront.com`. Works on every DNS provider. Make this the default
   your UI suggests.
2. **Apex `corpabc.com`, customer on Cloudflare** — a proxied `CNAME` at the
   apex works via Cloudflare's CNAME flattening. Fine, no caveats.
3. **Apex, customer elsewhere** — needs an `ALIAS`/`ANAME`/ flattened-CNAME
   record type. Route 53 (`ALIAS`), DNSimple, DNS Made Easy, NS1 and Azure DNS
   support it; many registrar-bundled DNS services do not.
4. **A records — do not offer these.** Cloudflare for SaaS gives you a CNAME
   target, not stable anycast IPs. Hard-coding IPs will break without notice.

The API rejects nothing based on apex-ness — an apex domain provisions fine, it
is the *customer's DNS provider* that may not be able to point at it. Surface
option 1 prominently and treat apex as advanced; this is exactly what Vercel and
the Lovable/Replit-class platforms do, for the same reason.

### 4.7 Cost and quota

| | |
|---|---|
| Included | **100** custom hostnames (Free / Pro / Business plans) |
| Beyond that | **$0.10 / hostname / month** |
| Ceiling | 50,000 hostnames |
| Certificates | Included — issuance and renewal cost nothing extra |

The API enforces its own soft cap (`customHostnameApi.maxCustomHostnames`,
default **100**) *before* calling Cloudflare, returning `429 quota_exceeded`. So
crossing into billing is a deliberate values change, not an accident on a
Tuesday. Tell us before you expect to cross 100 and we will raise it.

Cloudflare for SaaS is available on all plans including Free —
[plans](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/plans/).
Please re-confirm current pricing at signup time rather than treating this table
as durable.

---

## 5. Credential and security model

**Where the Cloudflare token lives:** in the `fuzeinfra` namespace, in
`custom-hostname-api-secret` (a SealedSecret), read by this service only.
**FuzeFront never holds it.** It is scoped to the minimum that works:

```
Zone -> SSL and Certificates -> Edit    (fuzefront.com only)
Zone -> Zone                -> Read     (fuzefront.com only)
```

That token cannot touch DNS records, Workers, Access policies, the tunnel, other
zones, or the account. The static DNS records this feature needs are created
once by Terraform under a *different*, human-held token.

**How FuzeFront authenticates:** bearer token over in-cluster service DNS.

```
POST http://custom-hostname-api.fuzeinfra.svc.cluster.local:8080/custom-hostnames
Authorization: Bearer $CUSTOM_HOSTNAME_API_TOKEN
```

The token is generated once, sealed into **both** namespaces (`fuzeinfra` for
the server, `fuzefront` for you), and rotated by re-sealing both sides in one
change.

**Not mTLS, and here is the reasoning.** mTLS would mean a second CA, a cert
lifecycle, and rotation machinery for a hop that never leaves the cluster
network and is already fenced three ways. If the platform later adopts a mesh
with automatic mTLS, this service inherits it for free and the bearer token
becomes a redundant second factor rather than the only one. Ask if you'd rather
have it now and we will build it.

**Why it cannot be reached publicly** — three independent mechanisms, so no
single mistake exposes it:

1. **No Ingress object.** Traefik has no rule for it, so the tunnel's catch-all
   cannot reach it. It is not "unrouted by policy", it is unrouted by absence.
2. **No Cloudflare tunnel rule and no CF Access app.** Nothing outside the
   cluster has a name to resolve.
3. **NetworkPolicy** (`customHostnameApi.networkPolicy.enabled`) restricting
   ingress to the namespaces that own a route profile. It fails *closed*: if no
   namespace is allowed, no ingress rule is emitted at all, which denies
   everything. (An empty `from:` would mean "from anywhere" — the template
   avoids emitting one.)

**Authorization is the token, not just authentication.** Each token maps to
exactly one *route profile*, which pins the namespace, Service, port, and paths
its domains may be routed to. FuzeFront's token can only ever create Ingresses
in the `fuzefront` namespace pointing at `fuzefront-frontend`. Naming a profile
your token does not grant is a `403`, never a silent fallback. Attaching a
domain another profile already owns is also a `403`. The service's RBAC is
correspondingly narrow: `Ingress` objects only, in profile namespaces only — one
`Role` per profile, no ClusterRole.

**Domains inside `fuzefront.com` are rejected** with `422`. They are already
served by the wildcard, so provisioning them would burn Cloudflare quota and
shadow the wildcard cert. `prod.fuzefront.com` is likewise reserved — it sits
behind the admin OTP wall.

---

## 6. Local development parity (kind)

Enabled by default in `values-local.yaml`. No Cloudflare account, no real DNS,
no `/etc/hosts` edits:

```bash
bash k8s/kind/setup-kind.sh
helm upgrade --install fuzeinfra helm/fuzeinfra -n fuzeinfra --create-namespace \
  -f helm/fuzeinfra/values-local.yaml
```

You get:

- **`*.fuzefront.local`** wildcard resolution from the in-cluster dnsmasq — the
  local mirror of `*.fuzefront.com` (`dnsmasq.extraWildcards`).
- **The same API in stub mode.** The stub returns Cloudflare-shaped TXT/CNAME
  records and walks the *real* state machine —
  `pending_validation → pending_issuance → pending_deployment → active` — over
  5 seconds, so your polling loop, retry logic, and status rendering are all
  genuinely exercised. It still materializes a real `Ingress`, so a resolved
  host actually routes.
- A well-known dev token, `local-dev-token`. The chart **refuses to render** if
  the dev secret is ever enabled alongside `provider: cloudflare`.

```bash
kubectl -n fuzeinfra run curl --rm -it --image=curlimages/curl --restart=Never -- \
  curl -sS -X POST http://custom-hostname-api.fuzeinfra.svc.cluster.local:8080/custom-hostnames \
    -H "Authorization: Bearer local-dev-token" \
    -H "Content-Type: application/json" \
    -d '{"domain":"app.corpabc.test"}'
```

To develop against the state machine without a cluster at all:

```bash
cd services/custom-hostname-api
pip install -r requirements.txt
PROVIDER=stub ROUTING_ENABLED=false \
  ROUTE_PROFILES='- {name: fuzefront, namespace: fuzefront, service: fuzefront-frontend, port: 80, tokenEnv: T}' \
  T=dev uvicorn app.main:app --port 8080
```

---

## 7. Authentik

**Confirmed reachable — no egress or ingress constraint.** OIDC redirect URIs on
custom domains work:

- The redirect to `auth.fuzefront.com` is a **browser** redirect. Nothing needs
  to egress from the cluster.
- `auth.fuzefront.com` is a public vanity host, outside the `*.prod` Cloudflare
  Access wall, so no OTP interstitial breaks the flow.
- The redirect *back* to `https://app.corpabc.com/...` reaches the cluster over
  the same path as any other request to that domain — the CF for SaaS edge, the
  tunnel, then the materialized Ingress. Once `active` is true, it works.

Two things to watch on your side:

1. **Register the redirect URI when the domain goes active, not when it is
   created.** Authentik rejects unregistered redirect URIs, and a domain that is
   `pending_validation` will fail the callback. Gate registration on `active`.
2. **Brand-per-domain needs no extra ingress.** Authentik resolves the brand
   from its own request context, and your blueprints already configure that.

Nothing for FuzeInfra to do here.

## 8. Cookies (FYI)

Acknowledged, no action. Subdomains share the `fuzefront.com` cookie because
they are in-zone; custom domains are cross-site and use token exchange on their
own origin. No infra-side cookie domain, CORS, or header work is implied.

---

## 9. Integration checklist

FuzeInfra side (this PR, then a follow-up enablement change):

- [x] `terraform/contabo/cloudflare.tf` — wildcard + SaaS records, both gated off
- [x] `services/custom-hostname-api/` — service + frozen OpenAPI + tests
- [x] `helm/fuzeinfra/templates/custom-hostname-api.yaml` + values across overlays
- [x] `deploy/sealed-secrets/custom-hostname-api-secret.yaml.template`
- [ ] **Enablement change** (human, one PR): seal the real secret, then
      `terraform apply` with `tenant_wildcard_enabled=true` and
      `saas_custom_hostnames_enabled=true`, then flip
      `customHostnameApi.enabled=true` and `routeProfiles[0].enabled=true` in
      `values-contabo.yaml`. **In that order** — enabling the chart before the
      secret CrashLoops the pod; enabling it before Terraform issues
      certificates for domains that resolve nowhere.

FuzeFront side:

- [ ] Add the `*.fuzefront.com` Ingress rule — **in its own Ingress object, at
      `router.priority: "1"`** ([§3.1](#31-the-pattern-to-use--wildcard-alone-priority-low))
- [ ] Before enabling it, enumerate the single-label hosts it would shadow
      ([§3.2](#32-shared-cluster-warning--this-affects-other-products))
- [ ] Mirror `tests/test_ingress_wildcard_priority.py` in the FuzeFront repo —
      FuzeInfra's tests cannot see your chart ([§3.3](#33-why-the-usual-checks-do-not-catch-this))
- [ ] Generate the client from `services/custom-hostname-api/openapi.yaml`
- [ ] Consume the sealed `CUSTOM_HOSTNAME_API_TOKEN` in the `fuzefront` namespace
- [ ] Drop `_fuzefront-verify` generation; surface `verification.records[]`
      ([§4.4](#44-domain-verification--drop-_fuzefront-verify))
- [ ] Map `tls_status` / `dns_status` / `active` onto `portal_domains`
      ([§4.5](#45-pollable-status))
- [ ] Publish the apex guidance from [§4.6](#46-apex-domains--the-honest-answer)
- [ ] Register Authentik redirect URIs on `active`, not on create
      ([§7](#7-authentik))

Open questions for FuzeFront:

1. Do you need **nested** tenant subdomains (`eu.corpabc.fuzefront.com`)? If so
   we need Advanced Certificate Manager and should budget it now ([§2](#2-wildcard-tls--cloudflare-terminated-and-here-is-why)).
2. Expected custom-domain volume in year one? Under 100 is free; we would rather
   raise the cap deliberately than have you hit a `429` in front of a customer
   ([§4.7](#47-cost-and-quota)).
3. Do you want mTLS on the service-to-service hop now, or is the bearer token
   plus NetworkPolicy sufficient until the platform has a mesh ([§5](#5-credential-and-security-model))?

---

## 10. Reference

- Contract: `services/custom-hostname-api/openapi.yaml`
- Service: `services/custom-hostname-api/README.md`
- Chart: `helm/fuzeinfra/templates/custom-hostname-api.yaml`, values under `customHostnameApi`
- Terraform: `terraform/contabo/cloudflare.tf`, vars `tenant_wildcard_enabled` / `saas_custom_hostnames_enabled`
- Secret: `deploy/sealed-secrets/custom-hostname-api-secret.yaml.template`
- Tests: `tests/test_custom_hostname_api.py` (contract + provider) ·
  `tests/test_ingress_wildcard_priority.py` (wildcard-vs-exact router priority guard)
- Traefik: [rules and priority](https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/rules-and-priority/) ·
  [Kubernetes Ingress annotations](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress/)
- Incident: FuzeFront#431 (wildcard shadowed `plan.fuzefront.com`), #437 (revert)
- Cloudflare: [Cloudflare for SaaS plans & pricing](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/plans/) ·
  [hostname validation](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/domain-support/hostname-validation/) ·
  [validation status](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/domain-support/hostname-validation/validation-status/)
