# TLS / Certificate Management

FuzeInfra owns TLS for the shared Kubernetes clusters via
[cert-manager](https://cert-manager.io). Dependent products get HTTPS **for
free** — just add a `cert-manager.io/cluster-issuer` annotation and a `tls:`
block to an Ingress. No dependent repo needs to install cert-manager or ship its
own ClusterIssuer/CA anymore.

> This replaces the per-repo workaround (e.g. FuzeFront's
> `deploy/local-tls/cert-manager-local-ca.yaml`, ClusterIssuer
> `fuzefront-local-ca`). Once FuzeInfra provides the shared issuers, that file
> can be deleted from FuzeFront — switch its Ingress to the
> `fuzeinfra-local-ca` issuer below.

## What FuzeInfra provides

| Environment | Cluster | ClusterIssuer | Cert type |
|-------------|---------|---------------|-----------|
| Local dev | kind (`fuzeinfra`) | `fuzeinfra-local-ca` | Self-signed root CA → trusted `*.dev.local` certs |
| Production | k3s (Contabo) / EKS | `letsencrypt-prod` | Real, browser-trusted Let's Encrypt certs |
| Prod testing | k3s / EKS | `letsencrypt-staging` | Untrusted, high rate limits — test before prod |

All of this lives in [`k8s/cert-manager/`](../k8s/cert-manager/):

```
k8s/cert-manager/
  setup-cert-manager.sh          # install cert-manager + configure issuers (local|prod|none)
  issuers/local-ca.yaml          # self-signed root + CA ClusterIssuer "fuzeinfra-local-ca"
  issuers/letsencrypt.yaml       # letsencrypt-staging + letsencrypt-prod ClusterIssuers
  wildcard-dev-local.yaml        # OPTIONAL: one *.dev.local wildcard cert
```

cert-manager is bootstrapped **separately** from the FuzeInfra Helm chart (its
CRDs must exist before any `Certificate`/`Issuer` is created), the same way Argo
CD is bootstrapped separately (see [gitops.md](gitops.md)).

## Local (kind) — automatic with `make kind-up`

`make kind-up` now installs cert-manager and the local CA issuer for you (via
`k8s/cert-manager/setup-cert-manager.sh local`), and `values-local.yaml` enables
TLS on the FuzeInfra ingress with `clusterIssuer: fuzeinfra-local-ca`. So after
`make kind-up`, `https://grafana.dev.local` etc. serve a cert signed by the
local dev CA.

To set it up by itself on an existing cluster:

```bash
make cert-manager-local          # or: ./k8s/cert-manager/setup-cert-manager.sh local
```

### One-time: trust the root CA

Browsers/curl won't trust the local CA until you import it once. The setup
script exports the CA to `k8s/cert-manager/fuzeinfra-local-ca.crt` (gitignored)
and prints OS-specific steps. You can also fetch it any time:

```bash
kubectl -n cert-manager get secret fuzeinfra-local-ca \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > fuzeinfra-local-ca.crt
```

```bash
# Linux (Debian/Ubuntu)
sudo cp fuzeinfra-local-ca.crt /usr/local/share/ca-certificates/fuzeinfra-local-ca.crt
sudo update-ca-certificates

# macOS
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain fuzeinfra-local-ca.crt

# Windows (PowerShell, admin)
Import-Certificate -FilePath fuzeinfra-local-ca.crt \
  -CertStoreLocation Cert:\LocalMachine\Root
```

Firefox has its own trust store: **Settings → Privacy & Security → Certificates →
View Certificates → Authorities → Import**.

> Trusting the root once means **every** `*.dev.local` service signed by it —
> across all dependent repos — is trusted. That's the whole point of a shared CA.

## Production (k3s on Contabo / EKS) — Let's Encrypt

```bash
ACME_EMAIL=you@example.com ./k8s/cert-manager/setup-cert-manager.sh prod
# or: ACME_EMAIL=you@example.com make cert-manager-prod
```

This installs cert-manager and the `letsencrypt-staging` + `letsencrypt-prod`
ClusterIssuers (HTTP-01 solver via ingress-nginx). Requirements:

- The service hostname's **public DNS** points at this cluster's ingress-nginx.
- Ports **80/443** are reachable from the internet (HTTP-01 challenge).

Validate against **staging** first (avoids Let's Encrypt prod rate limits), then
switch the annotation to `letsencrypt-prod`.

For the FuzeInfra chart itself on prod, set in your prod overlay
(`values-aws.yaml` or a k3s overlay):

```yaml
ingress:
  tls:
    enabled: true
    clusterIssuer: letsencrypt-prod
```

## Consumer guide — "how do I get HTTPS?"

You don't deploy cert-manager. You don't ship a CA. You just annotate your
Ingress and add a `tls:` block. cert-manager mints the cert into the named Secret
automatically.

### Local (`*.dev.local`)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: myapp
  annotations:
    cert-manager.io/cluster-issuer: fuzeinfra-local-ca   # <-- shared local CA
spec:
  ingressClassName: nginx
  tls:
    - hosts: [myapp.dev.local]
      secretName: myapp-tls                              # cert-manager creates this
  rules:
    - host: myapp.dev.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: myapp
                port:
                  number: 80
```

### Production (real domain)

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod     # or letsencrypt-staging while testing
spec:
  ingressClassName: nginx
  tls:
    - hosts: [myapp.example.com]
      secretName: myapp-tls
```

That's it — the two-line annotation + `tls:` block is the entire consumer
contract. cert-manager watches the Ingress, issues the certificate, stores it in
`secretName`, and renews it before expiry.

### Optional: one wildcard `*.dev.local` cert

If you'd rather not get a per-host cert for every dev hostname, issue a single
wildcard cert from the local CA and reference its Secret directly:

```bash
kubectl apply -f k8s/cert-manager/wildcard-dev-local.yaml   # creates secret wildcard-dev-local-tls in ns fuzeinfra
```

Secrets are namespace-scoped, so to use a wildcard in your own namespace copy
that manifest and change `namespace:`. For most cases the per-host annotation
above is simpler.

## Troubleshooting

```bash
# Are the issuers healthy?
kubectl get clusterissuers              # want READY=True
make cert-status

# Why isn't my cert ready?
kubectl describe certificate <name> -n <namespace>
kubectl get certificaterequests,orders,challenges -A   # ACME (prod) progress

# Local CA secret present?
kubectl -n cert-manager get secret fuzeinfra-local-ca
```

- **Cert stuck `Pending` locally** → the `fuzeinfra-local-ca` issuer isn't
  installed. Run `make cert-manager-local`.
- **Browser still warns on `*.dev.local`** → you haven't imported the root CA
  (see above), or the browser (Firefox) uses its own store.
- **ACME challenge fails in prod** → public DNS isn't pointing at the ingress, or
  80/443 aren't reachable. Use `letsencrypt-staging` until it resolves, then
  switch to `letsencrypt-prod`.

## Notes

- cert-manager version is pinned in `setup-cert-manager.sh`
  (`CERT_MANAGER_VERSION`, currently `v1.16.2`). Bump deliberately.
- The local root CA is valid for 10 years; leaf certs auto-renew.
- For wildcard **public** certs (e.g. `*.example.com`) Let's Encrypt requires a
  DNS-01 solver (an API token for your DNS provider) rather than HTTP-01 — add a
  `dns01` solver to `issuers/letsencrypt.yaml` if you need this.
