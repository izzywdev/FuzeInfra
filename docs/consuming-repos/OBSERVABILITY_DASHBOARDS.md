# Observability for a Consumer App

How a product repo (FuzeMarket, FuzePicker, SkyWatch, …) gets metrics, logs and
traces flowing, and ships its own Grafana dashboards, without forking
FuzeInfra.

## What FuzeInfra owns vs. what you own

Same split as [`ONBOARDING_A_CONSUMER_APP.md`](./ONBOARDING_A_CONSUMER_APP.md):
FuzeInfra runs the shared **backend** (Prometheus, Loki, Tempo, the
otel-collector, Grafana); your product owns its **own instrumentation and its
own dashboards**. Dashboards that describe one product's domain (its API, its
pipeline, its business metrics) are application code — they do not belong in
this repo, same as the product's own source doesn't.

| Piece | Lives in | Why |
|---|---|---|
| Prometheus / Loki / Tempo / otel-collector / Grafana | FuzeInfra | Shared backend, one per cluster |
| Generic platform dashboards (K8s, node, observability-stack health) | FuzeInfra | Cluster-wide, not product-specific |
| Prebuilt community dashboards (Node Exporter Full, …) | FuzeInfra (`monitoring/grafana/dashboards/community/`) | Generic infra, not product-specific |
| Your app's metrics/log/trace instrumentation | Your repo | Only you know your domain |
| Your app's dashboards (Overview, API, pipeline, …) | Your repo, shipped via the sidecar (below) | App-specific — this is the "SkyWatch" pattern generalized |

## Sending telemetry: one endpoint, not three

Send OTLP to the collector, not to Tempo/Loki/Prometheus directly — you never
need to know their hostnames or handle three different SDKs' export configs:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://fuzeinfra-otel-collector.fuzeinfra:4317   # grpc
# or :4318 for http/protobuf
```

Local docker-compose dev (`docker-compose.FuzeInfra.yml`): the same collector
runs as the `otel-collector` service on the `FuzeInfra` network — from another
container on that network use `http://otel-collector:4317`/`:4318` (the
compose file remaps the HOST-side ports to 4319/4320 only to avoid colliding
with Tempo's own 4317/4318, which does not affect container-to-container
traffic).

The collector fans out: traces → Tempo, OTLP logs → Loki, OTLP metrics → its
own Prometheus exporter (scraped like any other target). See
`helm/fuzeinfra/templates/tracing.yaml` for the exact pipeline.

**Metrics you scrape directly are unaffected.** If your app already exposes a
`/metrics` endpoint, keep doing that — annotate the pod
`prometheus.io/scrape: "true"` and Prometheus picks it up (existing
`kubernetes-pods` scrape job). The otel-collector's metrics pipeline is for
apps that only speak OTLP.

## The three-pillar contract: link them, don't silo them

> Prometheus tells you something is wrong. Loki tells you what happened.
> Tempo tells you where the time or the error went.

That only works if a person (or a dashboard) can jump between the three on
one identifier. Two things make that automatic instead of a copy-paste hunt:

1. **Structured JSON logs with a `trace_id` field.** Use the family logging
   standard (`logging` skill — pino, `reqId`/trace correlation). FuzeInfra's
   Loki datasource already has a `derivedFields` rule that turns a `trace_id`
   in a log line into a "View trace" link straight into Tempo — nothing to
   configure on your side beyond emitting the field.
2. **Exemplars on your Prometheus metrics**, if your client library supports
   them (most OTel-SDK-backed Prometheus exporters do by default once
   tracing is active in the same process). Prometheus here runs with
   `--enable-feature=exemplar-storage`, and the datasource is wired with
   `exemplarTraceIdDestinations` → Tempo, so a latency spike's exemplar dot
   is already clickable into the trace that caused it.

Propagate `trace_id`/`span_id` through whatever you use for cross-service
calls (HTTP headers via the OTel SDK's propagator does this automatically) —
that's what makes a multi-service pipeline's trace actually show every hop
instead of one.

## Shipping your own dashboards (the "Overview / API / Pipeline / Tracing / Logs" pattern)

Ship dashboards from YOUR repo as a ConfigMap labeled for Grafana's sidecar —
no PR to FuzeInfra, no chart fork:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: skywatch-grafana-overview
  namespace: skywatch
  labels:
    grafana_dashboard: "1"
data:
  overview.json: |
    { ... dashboard JSON ... }
```

The sidecar (`grafana.dashboardSidecar`, on by default) watches every
namespace for ConfigMaps with that label and files their JSON under the
**"Discovered"** Grafana folder automatically (`updateIntervalSeconds: 30` —
no restart needed). This is the actual mechanism behind the "SkyWatch"-style
dashboard set — it's a naming convention for your own dashboards, not
something FuzeInfra defines for you.

A well-scoped product dashboard set, generalized from the SkyWatch example
this doc replaced:

1. **Overview** — env selector, service health, request rate, error rate,
   p50/p95/p99, your domain's key gauges (queue depth, active-X count, …),
   recent alerts, links into Logs/Tracing.
2. **API** — per-route request rate/latency/status codes, DB latency, error
   count, replica/restart count, recent logs, a link into a trace.
3. **Pipeline** (if you have a producer → queue → consumer → datastore
   shape) — throughput and errors at *each* stage, end-to-end latency, a
   trace panel showing the hop-by-hop path. This is usually the dashboard
   that tells the real story of whether the product works, more than raw
   CPU/memory ever does.
4. **Tracing** — trace rate/duration/error rate, service graph, a searchable
   TraceQL trace table. FuzeInfra's own `Tracing` dashboard
   (`monitoring/grafana/dashboards/tracing.json`) already gives you the
   generic RED-from-spans version, driven by Tempo's metrics-generator with
   zero app-side dashboard work — clone it and add your domain-specific span
   attributes (e.g. queue producer/consumer spans) if you need more than
   that.
5. **Logs** — log volume/level distribution, structured-field filters
   (`request_id`, `trace_id`, `event`, `status`), a live logs panel,
   trace_id click-through (automatic, see above). FuzeInfra's
   `Logs Explorer` dashboard covers the namespace/service/pod-scoped generic
   version; a product-specific one adds domain fields.

Use `${datasource}` (Prometheus), `${loki_datasource}` (Loki) and a Tempo
datasource variable the same way FuzeInfra's own dashboards do (see any file
in `monitoring/grafana/dashboards/`) rather than hardcoding a datasource
`uid` — the family Prometheus/Loki/Tempo uids are `fuzeinfra-prometheus` /
`fuzeinfra-loki` / `fuzeinfra-tempo`, but resolving them via a templated
`datasource` variable means your dashboard JSON survives a rename.

## Platform / Kubernetes and generic infra dashboards — already provided

You do not need to build cluster/node/pod dashboards, or re-import Node
Exporter Full yourself. FuzeInfra ships:

- `Kubernetes Cluster Overview`, `Kubernetes Nodes`, `Kubernetes Pods & Containers`,
  `FuzeInfra Infrastructure Overview`, `FuzeInfra Services` — cluster/node/pod
  level.
- `Observability Stack Health` — Prometheus/Loki/Tempo/otel-collector health,
  scrape failures, collector dropped spans/logs/metrics. Check this FIRST if
  your own dashboards show no data — it tells you whether the backend itself
  is the problem.
- Community dashboards vendored via `scripts-tools/vendor-grafana-dashboard.sh`
  (Node Exporter Full, etc.), filed under the "Infrastructure (Community)"
  folder. See that script for how to add more.

## Checklist for a new product coming online

- [ ] `OTEL_EXPORTER_OTLP_ENDPOINT` set to the otel-collector (traces at minimum)
- [ ] Structured JSON logs with `trace_id` (logging skill)
- [ ] Own Overview + API (+ Pipeline, if applicable) dashboards shipped via
      the sidecar label, in your own repo
- [ ] Confirm in Grafana: `Observability Stack Health` is green, your traces
      show up in `Tracing`, clicking a `trace_id` in `Logs Explorer` lands in
      Tempo
