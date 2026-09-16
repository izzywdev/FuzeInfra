# Self-heal loop metrics

## What this measures

FuzeInfra runs a **self-closed automation loop**: something breaks in the
cluster, a workflow opens a labelled GitHub issue or PR, a cloud coding agent
picks it up, and the PR that fixes it merges. Three workflows feed the loop:

| Workflow | Label | `system` | Trigger |
|---|---|---|---|
| `.github/workflows/argo-outofsync-autofix.yml` | `argo-autofix` | `argocd` | An ArgoCD Application goes out-of-sync or unhealthy |
| `.github/workflows/grafana-crit-fix.yml` | `crit-autofix` | `loki` | A Grafana CRIT-level log alert (log source is Loki) |
| `.github/workflows/alertmanager-fuze.yml` | `alertmanager-autofix` | `alertmanager` | A Prometheus Alertmanager alert routed to this repo |

The question "how many of those loops have actually reached resolution?" had no
persisted answer. It could only be reconstructed by hand-running GitHub label
queries, which means it could not be graphed, alerted on, or compared over time.
This exporter closes that gap.

## Historical snapshot (2026-09-14)

Reconstructed **by hand via GitHub label queries, before this exporter existed** —
recorded here because the exporter cannot backfill a time series it was not
running for. Treat it as the baseline the graphs start from, not as exporter
output.

| System | Opened | Resolved (merged agent PR) | Open | Manual close |
|---|---|---|---|---|
| `argocd` | 0 | **0** | 0 | 0 |
| `loki` | 3 | **2** | 0 | 1 |
| `alertmanager` | 2 | **0** | 2 | 0 |

**2 confirmed full self-closed-loop resolutions, both Loki-driven, zero
Argo-driven.** The two are PRs #21 and #22 (June 2026), both fixing Postgres
`pg_isready` log spam surfaced through Loki. The one manual close is issue #103,
closed `not_planned` because Claude could not determine a fix in this repo. The
two `alertmanager` items (#960, #961) were still open on that date — the label
mechanism was only wired up in #958, days earlier.

## Classification

The exporter polls `GET /repos/{repo}/issues?labels=<label>&state=all` (which
returns **both** issues and pull requests — a PR row carries a `pull_request`
key) and reduces each item to:

- **`opened`** — every labelled item, counted once. The denominator.
- **`resolved`** — a **pull request** whose `pull_request.merged_at` is set.
  This is the only thing that counts as the loop closing itself: an agent wrote
  a fix and it landed. The list endpoint already returns `merged_at`, so there
  is no per-PR follow-up call (verified against the live API, 2026-09-14) —
  which matters, because unauthenticated polling gets 60 requests/hour.
- **`open`** — `state == "open"`. A loop still in flight, or stalled.
- **`manual_close`** — a **closed non-PR issue** whose `state_reason` is not
  `completed`. Best-effort, and deliberately so: GitHub stamps `completed` when
  an issue is closed by the merged PR referencing it, so excluding that value
  keeps a loop that *did* self-close from being counted as both a resolution and
  a manual close. The cost is that a human closing an issue as "completed" by
  hand is not counted here. Exact issue↔PR cross-linking is out of scope for v1.

Latency (`created_at` → `merged_at`) is recorded per resolved PR into a
histogram labelled by `system`.

### Gauge semantics

`fuzeinfra_selfheal_loop_*_total` are **gauges**, despite the `_total` suffix.
Every value is recomputed from GitHub — the source of truth — on each poll,
rather than incremented in-process. That is what makes the numbers survive a pod
restart and stay correct if an item is relabelled or deleted. The practical
consequence: **read them directly, never through `rate()` or `increase()`**.
The same applies to the histogram buckets, which is why the dashboard's quantile
panel takes `histogram_quantile` over the raw bucket values with no `rate()`.

`fuzeinfra_selfheal_loop_scrape_errors_total` is a real in-process counter and
*is* meant to be `rate()`d.

## Adding a fourth autofix workflow

Values change only — no code, no image rebuild:

```yaml
# helm/fuzeinfra/values.yaml (or values-contabo.yaml)
selfHealMetrics:
  labelSystemMap:
    argo-autofix: argocd
    crit-autofix: loki
    alertmanager-autofix: alertmanager
    my-new-autofix: my-system      # <- the whole change
```

The map is passed to the pod as the `LABEL_SYSTEM_MAP` JSON env var. Commit to
`main`, let Argo sync; the new `system` label appears on every metric on the
next poll and the dashboard picks it up with no edit.

## Where things live

| Piece | Path |
|---|---|
| Exporter source + env/metric reference | `tools/selfheal-metrics-exporter/` |
| Image build | `.github/workflows/build-selfheal-metrics-exporter.yml` → `ghcr.io/izzywdev/fuzeinfra/selfheal-metrics-exporter` |
| Deployment + Service | `helm/fuzeinfra/templates/selfheal-metrics.yaml` (gate: `selfHealMetrics.enabled`) |
| Prometheus scrape job | `helm/fuzeinfra/templates/configmaps-monitoring.yaml`, job `selfheal-metrics` |
| Dashboard | `helm/fuzeinfra/dashboards/selfheal-loop.json`, uid `fuzeinfra-selfheal-loop`, Grafana folder **FuzeInfra** |
| GitHub token (optional) | `deploy/sealed-secrets/selfheal-metrics-github-token.yaml.template` |
| Guard test | `tests/test_selfheal_metrics.py` |

Enabled **only** in `values-contabo.yaml`. Prod is the cluster whose alerts open
the autofix issues, and the counts come from one shared GitHub repo, so a second
exporter on kind or EKS would publish a duplicate copy of the same numbers.

## Operating it

- **The loop's own health** is on the dashboard: *Last Poll Age* (staleness of
  `fuzeinfra_selfheal_loop_last_scrape_success_timestamp_seconds`) and *Scrape
  Errors (1h)*. If Last Poll Age climbs past ~30 minutes at the default 10-minute
  interval, every count on the page is stale and the exporter — not the loop —
  is what needs looking at.
- **The human-readable feed** is the *Recently Self-Healed* logs panel, backed by
  `{app="selfheal-metrics"} | json | event="selfheal_resolved"`. The exporter
  prints one JSON line per **newly observed** resolution; the first poll after a
  restart seeds silently, so a rollout does not republish the whole history.
- **No token is required.** The `GITHUB_TOKEN` env ref is `optional: true`; a
  cluster without the SealedSecret polls unauthenticated and produces identical
  numbers under a 60 req/h ceiling (~18 req/h at the default settings).
- **Prod is GitOps.** Change the chart or values and merge to `main`; Argo syncs.
  Never `kubectl patch` this Deployment — `selfHeal` reverts it within seconds.
