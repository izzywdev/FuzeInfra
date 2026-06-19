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
Per-component selector labels. Usage: {{ include "fuzeinfra.selectorLabels" (dict "component" "postgres") }}
*/}}
{{- define "fuzeinfra.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: fuzeinfra
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
