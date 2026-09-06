{{/*
Common helpers for the FuzeInfra chart.
*/}}

{{- define "fuzeinfra.name" -}}
fuzeinfra
{{- end -}}

{{/*
Common labels applied to every object.
*/}}
{{- define "fuzeinfra.labels" -}}
app.kubernetes.io/part-of: fuzeinfra
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/*
Per-component selector labels. Usage: {{ include "fuzeinfra.selectorLabels" (dict "component" "postgres" "root" $) }} — root REQUIRED (for .Release.Name)
*/}}
{{- define "fuzeinfra.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{/*
Name of the Secret holding credentials (existing or chart-managed).
*/}}
{{- define "fuzeinfra.secretName" -}}
{{- if .Values.credentials.existingSecret -}}
{{ .Values.credentials.existingSecret }}
{{- else -}}
fuzeinfra-secrets
{{- end -}}
{{- end -}}

{{/*
imagePullPolicy shortcut.
*/}}
{{- define "fuzeinfra.pullPolicy" -}}
{{ .Values.global.imagePullPolicy | default "IfNotPresent" }}
{{- end -}}

{{/*
storageClassName helper - emits the field only when a class is set.
Usage:
  {{- include "fuzeinfra.storageClass" . | nindent 8 }}
*/}}
{{- define "fuzeinfra.storageClass" -}}
{{- if .Values.global.storageClass }}
storageClassName: {{ .Values.global.storageClass | quote }}
{{- end }}
volumeMode: Filesystem
{{- end -}}

{{/*
Ingress host for a component: <component>.<domain>
Usage: {{ include "fuzeinfra.host" (dict "root" $ "sub" "grafana") }}
*/}}
{{- define "fuzeinfra.host" -}}
{{ .sub }}.{{ .root.Values.global.domain }}
{{- end -}}

{{/*
The namespace consumers use to address the shared services.

Every in-cluster service address this chart hands out (Kafka's advertised
listener, NOTES.txt) is an FQDN, and the namespace segment of that FQDN decides
whether a consumer in ANOTHER namespace can resolve it. Deriving it purely from
`.Release.Namespace` makes the value depend on whether the caller remembered
`--namespace` — and Helm silently substitutes "default" when they did not. That
renders `fuzeinfra-kafka.default.svc.cluster.local`, which resolves nowhere, so
the broker hands an unroutable address back in its metadata: bootstrap succeeds
and every produce/consume then fails. That is a silent, cluster-wide outage
produced by a missing flag.

So: `global.serviceNamespace` pins it explicitly, and a namespace-less render is
a hard failure rather than a broken artifact. See values.yaml for the knob.
Usage: {{ include "fuzeinfra.serviceNamespace" . }}
*/}}
{{- define "fuzeinfra.serviceNamespace" -}}
{{- $ns := .Values.global.serviceNamespace | default .Release.Namespace -}}
{{- if eq $ns "default" -}}
{{- fail "fuzeinfra: refusing to render service addresses in namespace \"default\" — Helm fell back to it because no --namespace was passed (or global.serviceNamespace is literally \"default\"). Kafka's advertised.listeners would become fuzeinfra-kafka.default.svc.cluster.local, which no consumer can resolve. Re-run with `--namespace fuzeinfra`, or set global.serviceNamespace explicitly." -}}
{{- end -}}
{{- $ns -}}
{{- end -}}

{{/*
Cluster-wide FQDN for a service in this release, e.g.
  fuzeinfra-kafka.fuzeinfra.svc.cluster.local
Consumers in other namespaces MUST get the FQDN — a bare service name only
resolves inside this chart's own namespace (issue #104).
Usage: {{ include "fuzeinfra.serviceFqdn" (dict "root" $ "svc" "fuzeinfra-kafka") }}
*/}}
{{- define "fuzeinfra.serviceFqdn" -}}
{{ .svc }}.{{ include "fuzeinfra.serviceNamespace" .root }}.svc.cluster.local
{{- end -}}

{{/*
Soft anti-affinity: spread heavy stateful DBs across nodes (avoid piling all onto
one node — root cause of the 2026-07-24 OOM). Preferred (never blocks scheduling).
Usage in a pod spec: {{- include "fuzeinfra.dbSpread" $ | nindent 6 }}
*/}}
{{- define "fuzeinfra.dbSpread" -}}
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: kubernetes.io/hostname
          labelSelector:
            matchExpressions:
              - key: app.kubernetes.io/instance
                operator: In
                values: ["{{ .Release.Name }}"]
{{- end -}}

{{/*
Toleration for the durable-node taint.

WHY. The durable nodes are meant to carry ONLY core infrastructure (this chart)
and the FuzeFront platform. In practice they had accumulated 12 mendys-prod, 2
mendys-wp, 2 fuzemarket and one each of fuzesales / fuzequality / fuzeexecutive /
fuzedeploy / fuzeagent pods, because the only thing guarding them was

    node-role.kubernetes.io/control-plane : PreferNoSchedule

and PreferNoSchedule is ADVISORY -- the scheduler places pods there anyway under
pressure. Per-repo nodeAffinity is opt-in, so any repo nobody has touched still
lands wherever it likes. A NoSchedule taint is the only mechanism that makes this
policy structural rather than a convention.

TWO-PHASE ROLLOUT; this helper is phase one.
  Phase 1 (this chart): every workload here tolerates the taint. A toleration for
          a taint that does not exist yet is a complete NO-OP, so merging this
          changes nothing at runtime and cannot evict anything.
  Phase 2 (deliberate, attended): apply the taint to the durable nodes --
              kubectl taint node <node> fuzeinfra.io/durable=true:NoSchedule
          At that instant anything WITHOUT the toleration stops being schedulable
          there. Do it one node at a time and verify: a workload missed in phase 1
          surfaces here as Pending, which is why phase 2 is attended.

Gated off by default (global.durableNodeTaint.enabled) so local/kind/EKS overlays,
which have no such taint, render byte-identically.

Usage in a pod spec: {{- include "fuzeinfra.durableToleration" $ | nindent 6 }}
*/}}
{{- define "fuzeinfra.durableToleration" -}}
{{- if .Values.global.durableNodeTaint.enabled -}}
tolerations:
  - key: {{ .Values.global.durableNodeTaint.key | quote }}
    operator: Equal
    value: {{ .Values.global.durableNodeTaint.value | quote }}
    effect: NoSchedule
{{- end -}}
{{- end -}}

{{/*
Same toleration, but as a bare LIST ITEM for pod specs that already render their
own `tolerations:` key (kube-state-metrics, monitoring). Emitting the full block
there would produce a duplicate mapping key and fail the render.
Usage: {{- include "fuzeinfra.durableTolerationItem" $ | nindent 8 }}
*/}}
{{- define "fuzeinfra.durableTolerationItem" -}}
{{- if .Values.global.durableNodeTaint.enabled -}}
- key: {{ .Values.global.durableNodeTaint.key | quote }}
  operator: Equal
  value: {{ .Values.global.durableNodeTaint.value | quote }}
  effect: NoSchedule
{{- end -}}
{{- end -}}
