# `cluster-query` — read-only cluster access for consuming repos

**You can inspect the prod cluster yourself.** You do not need a kubeconfig, an
operator, or a human to paste `kubectl` output back to you. FuzeInfra publishes a
dispatchable read-only introspection workflow —
[`.github/workflows/cluster-query.yml`](../../.github/workflows/cluster-query.yml) —
that any repo with a FuzeInfra dispatch credential can trigger.

This is the one thing a consumer may do **directly against the cluster**. Everything
that *changes* the cluster stays GitOps or `@claude`-delegated (see
[`CONSUMER_ONBOARDING_SHARED_CLUSTER.md`](../CONSUMER_ONBOARDING_SHARED_CLUSTER.md)) —
`cluster-query` is read, and only read.

> **Why it exists.** "Is my app actually running in prod?" used to be answered by a
> human (or a Cowork session) relaying `kubectl` output by hand. That relay is slow,
> lossy, and it tempted people to hand kubeconfigs around. `cluster-query` answers the
> question directly without any session ever holding the raw kubeconfig — the
> credential stays in FuzeInfra's CI, and the caller gets output, not access.

---

## 1. Before you dispatch: the job log is PUBLIC

`izzywdev/FuzeInfra` is a **public** repo (`class: oss-public`). Actions job logs on a
public repo are readable by **anyone on the internet**, and they are retained.

Whatever you query is published. That is the single rule that governs how you use
this workflow:

- **Never query anything whose *output* is a credential**, even though reading it is a
  read. Reading `Secret` objects is blocked outright for exactly this reason (§4) —
  but the guard cannot know that *your* app logs a token, so `logs` is on you.
- Prefer the narrowest query that answers your question (`-n <ns> get pods` beats
  `get all -A -o yaml`).
- If something sensitive does land in a log, deleting the run is **after-the-fact** —
  treat the value as exposed and rotate it.

---

## 2. What you need — a dispatch credential

Triggering a `workflow_dispatch` on FuzeInfra needs a token that can write Actions on
`izzywdev/FuzeInfra`, held by an identity with write access to the repo:

| Token type | Required permission |
|---|---|
| Fine-grained PAT | Repository access: **FuzeInfra** → Repository permissions → **Actions: Read and write** |
| Classic PAT | `repo` scope |
| GitHub App installation token | Installed on FuzeInfra with **Actions: Read and write** |

Notes:

- **`FUZEINFRA_DISPATCH_TOKEN` is not enough on its own.** The token described in
  [`INFRA_REQUEST_DISPATCH.md`](../INFRA_REQUEST_DISPATCH.md) carries *Contents: Read
  and write*, which authorizes `repository_dispatch` but **not**
  `workflow_dispatch`. To reuse it here, a human adds **Actions: Read and write** to
  it; otherwise use a separate secret.
- Many repos already carry a `GH_TOKEN` PAT for the `@claude` handler. If that PAT has
  the scope above, no new secret is needed.
- **This grant is repo-wide.** GitHub cannot scope Actions:write to one workflow, so a
  token that can dispatch `cluster-query` can dispatch *any* `workflow_dispatch`
  workflow in FuzeInfra. Provision it deliberately, keep it in Actions secrets, and
  give it a short expiry.

Check whether the token you have works — a `403`/`404` means it does not:

```bash
GH_TOKEN=<token> gh workflow run cluster-query.yml --repo izzywdev/FuzeInfra \
  -f kubectl_args='version'
```

**No token, or you'd rather not hold one?** Delegate instead: open an `@claude` issue
on FuzeInfra asking for the read. That path always works; it is just slower.

---

## 3. Dispatch and read the result

```bash
# 1. fire the query
gh workflow run cluster-query.yml --repo izzywdev/FuzeInfra \
  -f kubectl_args='-n mendys-prod get pods -o wide'

# 2. grab the run it created, wait for it, print the output
RUN=$(gh run list --workflow=cluster-query.yml --repo izzywdev/FuzeInfra \
        --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN" --repo izzywdev/FuzeInfra --exit-status
gh run view  "$RUN" --repo izzywdev/FuzeInfra --log
```

> `gh workflow run` does not return the run id, and the run takes a moment to appear —
> if step 2 comes back with an older run, wait a few seconds and re-list. Both inputs
> are optional; dispatching with neither runs the `get nodes` default.

Same thing over the REST API:

```bash
curl -X POST -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/izzywdev/FuzeInfra/actions/workflows/cluster-query.yml/dispatches \
  -d '{"ref":"main","inputs":{"kubectl_args":"-n mendys-prod get pods -o wide"}}'
```

### Optional second input — `curl_url`

Probe a public URL from inside the cluster's network path (`curl -sSIL`, headers only):

```bash
gh workflow run cluster-query.yml --repo izzywdev/FuzeInfra \
  -f kubectl_args='-n mendys-prod get ingress' \
  -f curl_url='https://www.mendysrobotics.com/'
```

Only `*.mendysrobotics.com` and `*.prod.fuzefront.com` are allowlisted; anything else
fails the step. Adding a hostname is a FuzeInfra change — delegate via `@claude`.

---

## 4. What is allowed, and what is refused

A dispatch must contain **at least one read verb**, and must contain **no** mutating or
exec token, and must not name the `Secret` resource. All three checks run before
`kubectl` is invoked; the invariants are executable in
[`tests/test_cluster_query_guard.py`](../../tests/test_cluster_query_guard.py).

**Read verbs (one must be present):**
`get` · `describe` · `logs` · `top` · `events` · `version` · `api-resources` ·
`api-versions` · `explain` · `cluster-info` · `config`

**Refused anywhere in the args — mutation and exec:**
`exec` · `attach` · `cp` · `port-forward` · `proxy` · `run` · `delete` · `apply` ·
`edit` · `patch` · `replace` · `scale` · `rollout` · `cordon` · `uncordon` · `drain` ·
`annotate` · `label` · `set` · `create` · `taint` · `debug`

**Refused — reads whose output is a credential:**

- **`Secret` objects are blocked**, in every spelling (`secret/foo`, `pods,secrets`,
  `secrets.v1.`, `Secret`). `get secret -o jsonpath=…` is a read that prints the
  credential verbatim into a public log — this is not hypothetical, it happened on
  2026-07-29 and the run's logs had to be deleted after the fact. Read-only is not the
  same as safe-to-log. To recover a live secret value, use the operator SSH path in
  [`SECRETS_MANAGEMENT.md` §4](../SECRETS_MANAGEMENT.md#4-decryption-is-cluster-only),
  or just rotate it.
- **`SealedSecrets` are readable on purpose** — they are ciphertext at rest and useless
  to a reader, so `get sealedsecret <name> -o yaml` is allowed and is usually what you
  actually want when debugging a secret that isn't materializing.
- **`--raw` is blocked.** `kubectl config view --raw` would print the runner's
  cluster-admin kubeconfig into the public log.

A name that merely *contains* "secret" is fine — `describe deployment
litellm-secret-reader` and `logs job/fuzeinfra-sealed-secrets-sync` both pass.

---

## 5. Limits

- **Serialized.** The workflow has `concurrency: cluster-query` with
  `cancel-in-progress: false`, so queries queue behind each other rather than
  cancelling. Expect to wait if someone else is querying.
- **Truncated.** kubectl output is cut at 200 KB, curl output at 20 KB.
- **Self-hosted runners.** It runs on `runs-on: staging` (the ARC scale set), so it is
  independent of hosted-minutes exhaustion — but if the scale set is down, the run
  queues instead of failing fast.
- **One cluster.** The kubeconfig is the Contabo k3s prod cluster. There is no
  staging-cluster equivalent.
- **Read, not reach.** No `port-forward`, no `exec`, no `proxy` — you cannot open a
  channel into the cluster, only print things out of it.

---

## 6. When to use it

Use it whenever you would otherwise ask a human "what's it doing in prod?":

```bash
# did my Argo app actually sync?
-n argocd get applications
# are my pods up?
-n <my-namespace> get pods -o wide
# why is that pod not up?
-n <my-namespace> describe pod <pod>
-n <my-namespace> get events --sort-by=.lastTimestamp
# what did it say before it died?
-n <my-namespace> logs <pod> --previous --tail=200
# is my ingress wired to the tunnel?
-n <my-namespace> get ingress,svc,endpoints
```

This is the evidence half of the verification protocol for anything you ship to the
shared cluster: **a deploy is not verified because CI is green — it is verified
because the pods are `Running` and the endpoint answers.** Quote the run URL as your
evidence.

When the answer is "it's broken and needs a change," stop querying and go back to the
normal paths: your own `deploy/**` (GitOps) for your app, or an `@claude` issue on
FuzeInfra for anything the platform owns.

---

## Related docs

- [`CONSUMER_ONBOARDING_SHARED_CLUSTER.md`](../CONSUMER_ONBOARDING_SHARED_CLUSTER.md) — how a product goes live on the shared cluster.
- [`DEPLOYING_A_SERVICE_TO_K8S.md`](../DEPLOYING_A_SERVICE_TO_K8S.md) — namespace, Argo Application, SealedSecrets, resource limits.
- [`SECRETS_MANAGEMENT.md`](../SECRETS_MANAGEMENT.md) — why Secret reads are blocked here and what to do instead.
- [`INFRA_REQUEST_DISPATCH.md`](../INFRA_REQUEST_DISPATCH.md) — the *write* side: declaring infra needs and dispatching them to FuzeInfra.
- [`../../CONTRACT.md`](../../CONTRACT.md) — FuzeInfra's stable service interface.
- [`../argo-selfheal-autofix.md`](../argo-selfheal-autofix.md) — the automatic loop that opens an `@claude` issue when your app goes unhealthy.
