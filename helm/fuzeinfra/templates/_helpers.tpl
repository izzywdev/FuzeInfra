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
