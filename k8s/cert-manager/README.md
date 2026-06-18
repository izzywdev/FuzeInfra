# FuzeInfra cert-manager (shared TLS)

Centralized TLS for the shared clusters so dependent repos get HTTPS **for free**
by annotating an Ingress — no per-repo cert-manager install or CA.

| File | Purpose |
|------|---------|
| `setup-cert-manager.sh` | Install cert-manager + configure issuers: `local` \| `prod` \| `none` |
| `issuers/local-ca.yaml` | Self-signed root + CA ClusterIssuer `fuzeinfra-local-ca` (trusted `*.dev.local`) |
| `issuers/letsencrypt.yaml` | `letsencrypt-staging` + `letsencrypt-prod` ClusterIssuers (real certs) |
| `wildcard-dev-local.yaml` | Optional single `*.dev.local` wildcard cert |

```bash
# Local (kind) — also run automatically by `make kind-up`
make cert-manager-local          # ./setup-cert-manager.sh local

# Production (k3s on Contabo / EKS)
ACME_EMAIL=you@example.com make cert-manager-prod
```

Consumer contract — add to any Ingress:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: fuzeinfra-local-ca   # prod: letsencrypt-prod
spec:
  tls:
    - hosts: [myapp.dev.local]
      secretName: myapp-tls
```

Full guide, root-CA trust steps, and troubleshooting:
[`docs/cert-management.md`](../../docs/cert-management.md).
