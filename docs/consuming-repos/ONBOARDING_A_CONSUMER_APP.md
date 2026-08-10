# Onboarding a Consumer App to the Shared Cluster

How a product repo (FuzeMarket, FuzePicker, FuzeQuality, …) gets deployed onto the
shared Contabo k3s cluster, and the traps that have actually bitten.

Written from the FuzeMarket onboarding, where every one of the failures below was
hit in sequence. Each section states the rule, then why — the reasons matter more
than the rule, because they generalise.

## Pattern

**FuzeInfra owns the ArgoCD `Application` and the `AppProject`. The consumer owns
its Helm chart and nothing else in the deploy path.**

| Artefact | Lives in | Why |
|---|---|---|
| `AppProject` | FuzeInfra `argocd/projects/<app>.yaml` | It is the destination/security boundary (FuzeInfra#99) |
| `Application` | FuzeInfra `argocd/applications/<app>.yaml` | Points at the consumer's chart; registered by `deploy-prod.yml` |
| Helm chart | Consumer repo | The product's own concern |
| Cross-namespace RBAC | FuzeInfra `helm/fuzeinfra` | Access to another namespace is the platform's to grant |

Both FuzeInfra files are applied automatically on merge by
`.github/workflows/deploy-prod.yml` — projects first, then a glob over
`argocd/applications/*.yaml`. No manual `kubectl apply` step.

## Do not self-register from the consumer repo

It is tempting to add `deploy/argocd/<app>.yaml` plus a CI job that
`kubectl apply`s it. FuzeMarket did, and it collided head-on with FuzeInfra's copy:
two `Application/fuzemarket` objects in the `argocd` namespace, different specs,
two appliers, flip-flopping `project` on alternating deploys.

The consumer's copy also used `project: default`. Argo's stock `default` project
is unrestricted — `'*'` destinations, including `fuzeinfra`. That is precisely the
condition behind the duplicate-FuzeInfra incident (FuzeInfra#93 / FuzeFront#95:
split-brain Postgres/Kafka, control-plane saturation, cluster-wide 502). A
consumer that can name any destination can redeploy the platform underneath
itself.

## Your chart must render only into your own namespace

The restricted project permits exactly one workload namespace. If the chart
renders **anything** into another namespace, Argo rejects the sync with:

```
namespace fuzefront is not permitted in project 'fuzemarket'
```

and that blocks **every resource in the app**, not just the offending one. The
same failure is annotated in `argocd/projects/fuzeinfra.yaml` for
`custom-hostname-api`.

This is easy to do by accident, because RBAC feels like part of the app. It is
not. Check before opening the PR:

```bash
helm template <app> deploy/helm/<app> -n <ns> -f values-prod.yaml \
  | grep -E '^\s+namespace:' | sort -u
```

Anything other than your own namespace will wedge the sync.

## Need to read a Secret in another namespace?

FuzeInfra issues the grant; you do not assert it. Add an entry to
`consumerRegistrationRbac.grants` in `helm/fuzeinfra/values-contabo.yaml`:

```yaml
consumerRegistrationRbac:
  enabled: true
  grants:
    - namespace: fuzefront          # where the Secret lives
      secretName: fuzefront-registration
      subjectName: arc-runner-sa    # the ServiceAccount being granted access
      subjectNamespace: arc-runners
```

That renders `get` on **one named Secret** — no `list`, because `list` exposes
every Secret in the namespace and `resourceNames` cannot constrain it. Revoking
access is deleting the entry; nothing in the consumer repo can re-grant it.

## Ingress is tunnel-only

Traefik is pinned to `ClusterIP` (`argocd/cluster-bootstrap/traefik-clusterip.yaml`)
so nothing binds host ports 80/443. Every request arrives via the Cloudflare
tunnel, whose catch-all forwards `*.prod.fuzefront.com` to Traefik for host
routing.

So the host **must** sit under `prod.fuzefront.com`. Use `ingressClassName:
traefik`, not the deprecated `kubernetes.io/ingress.class` annotation. No
Terraform change is needed — the wildcard DNS record and catch-all route already
exist — and the host inherits the Cloudflare Access OTP wall for free.

FuzeMarket originally shipped `fuzemarket.fuze.internal`, a zone that exists in
neither environment. Note the shape of that failure: the Ingress is created, the
pod goes Ready, CI is green, and the app is simply unreachable from everywhere.
Nothing reports an error. For local work the equivalent zone is `*.dev.local`
(dnsmasq).

## Sealing a secret for your namespace

Seal **offline** against the published public cert. You do not get a kubeconfig —
see [SECRETS_MANAGEMENT.md](../SECRETS_MANAGEMENT.md). In particular do **not**
use `kubeseal --controller-namespace kube-system`: it reaches the controller
through the API server and needs `services/proxy` on `kube-system`, which no
consumer runner is granted. That form fails 100% of the time, and it has been
copy-pasted into more than one repo.

```bash
kubeseal --cert https://sealed-secrets.prod.fuzefront.com/v1/cert.pem --format yaml
```

Keep plaintext in a file rather than argv (argv is visible to other processes),
and make the job **fail loudly** if the value is empty or `kubeseal` emits
nothing. A sealing job that exits 0 having written a placeholder is how a
placeholder ends up committed and believed.

If your pod must restart when the secret rotates, add a `checksum/` annotation —
**not** a stakater/reloader annotation, which is inert here. See
[SECRETS_MANAGEMENT.md §5](../SECRETS_MANAGEMENT.md).

## Registering with the FuzeFront portal

Products that appear in the portal register via an init container carrying a
pre-shared Bearer token from `Secret/<your-ns>/fuzefront-registration`.

The source of that token is `Secret/fuzefront/fuzefront-registration`, created by
FuzeFront's `consumer-registration-seed` post-upgrade Job. **That Job exits 0 when
`CONSUMER_REGISTRATION_SECRET` is absent from the sealed `fuzefront-secrets`** —
it logs a diagnostic and succeeds. So a missing token looks like a healthy
deploy, and the symptom surfaces far away: FuzeFront's `consumer-auth.ts` falls
through to JWT auth on a missing or mismatched secret, so every registration
attempt 401s with nothing explaining why.

Two checks worth doing before assuming your own code is wrong:

- `kubectl -n fuzefront get secret fuzefront-registration` — `NotFound` means the
  seed Job no-opped, and no amount of work on your side will fix it.
- If the Secret exists but registration 401s, your sealed copy's plaintext may
  have drifted from FuzeFront's current value. Do not hand-seal a token you
  obtained separately; run a workflow that derives your copy **from the live
  Secret**, so the two cannot diverge.

Diagnostic note: `Forbidden` and `NotFound` from `kubectl get secret` mean
different things and are worth distinguishing carefully. `Forbidden` is RBAC —
your grant is missing. `NotFound` means authorization **passed** and the object
does not exist. Watching that transition is how the FuzeMarket RBAC grant was
confirmed working without any cluster access.

## Before you conclude "it deployed"

A green `deploy-prod` now means the sync landed, but read
[CLAUDE.md's gotchas](../../CLAUDE.md) first — a `governance-sync` bot commit in
your PR can carry a CI-skip token into the squashed merge commit and skip the
prod deploy entirely, producing **no run at all** rather than a red one.

To verify from your own repo without cluster access, use the self-service
read-only `kubectl` in [CLUSTER_QUERY.md](./CLUSTER_QUERY.md) rather than asking a
human to relay output.
