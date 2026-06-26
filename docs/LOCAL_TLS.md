# Local TLS — shared cert management (issue #10)

FuzeInfra provides **centralized cert management for local (kind) development** so
consumer apps don't each install their own cert-manager + CA. `setup-kind.sh`
installs **cert-manager** and a self-signed **`fuzeinfra-local-ca` ClusterIssuer**;
any app gets a trusted cert for its `*.dev.local` host just by annotating its
Ingress.

> **Scope: local dev only.** Production terminates TLS at the **Cloudflare edge**
> (the Named Tunnel), so prod services are HTTP behind Traefik and need no
> cert-manager. Do **not** apply this to the Contabo cluster.

## What you get

- `cert-manager` running in the `cert-manager` namespace.
- A 10-year self-signed root CA → the **`fuzeinfra-local-ca`** ClusterIssuer.
- Cert manifests in [`k8s/local-tls/cluster-issuer.yaml`](../k8s/local-tls/cluster-issuer.yaml).

## Get HTTPS for your service (2 steps)

### 1. Annotate your Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: myapp
  annotations:
    cert-manager.io/cluster-issuer: fuzeinfra-local-ca   # <- issue from the shared CA
spec:
  ingressClassName: nginx
  tls:
    - hosts: [myapp.dev.local]
      secretName: myapp-tls                                # cert-manager fills this in
  rules:
    - host: myapp.dev.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: myapp
                port: { number: 80 }
```

cert-manager watches the Ingress, issues a cert signed by `fuzeinfra-local-ca`,
and stores it in `myapp-tls`. ingress-nginx then serves `https://myapp.dev.local`.

### 2. Trust the CA once (so no `-k` / browser warnings)

```bash
kubectl -n cert-manager get secret fuzeinfra-local-ca \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > fuzeinfra-local-ca.crt
```

Import `fuzeinfra-local-ca.crt` into your trust store:

- **macOS:** `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain fuzeinfra-local-ca.crt`
- **Linux:** `sudo cp fuzeinfra-local-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`
- **Windows:** `Import-Certificate -FilePath fuzeinfra-local-ca.crt -CertStoreLocation Cert:\LocalMachine\Root`

Now `https://myapp.dev.local` is trusted everywhere on your machine.

## Migrating off a per-repo cert-manager

If your repo ships its own local cert-manager + CA (e.g. FuzeFront's
`deploy/local-tls/cert-manager-local-ca.yaml`), you can **delete it** and switch
the Ingress annotation to `cert-manager.io/cluster-issuer: fuzeinfra-local-ca`.
FuzeInfra now owns it centrally.

## Notes

- The CA secret lives at `cert-manager/fuzeinfra-local-ca`. Re-running
  `setup-kind.sh` is idempotent; the CA is regenerated only if the secret is
  missing (after which re-trust the new CA).
- Bump `CERT_MANAGER_VERSION` in `k8s/kind/setup-kind.sh` to upgrade cert-manager.
