{{/*
Common helpers for the LiteLLM chart.

Deliberately mirrors helm/fuzeinfra/templates/_helpers.tpl so objects in the
`fuzeinfra` namespace carry a consistent label set regardless of which Argo
Application owns them. part-of=fuzeinfra is what makes this show up alongside
the umbrella chart's services in dashboards and NetworkPolicy selectors.
*/}}

{{- define "litellm.name" -}}
litellm
{{- end -}}

{{/*
Common labels applied to every object.
*/}}
{{- define "litellm.labels" -}}
app.kubernetes.io/part-of: fuzeinfra
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/*
Selector labels. Usage: {{ include "litellm.selectorLabels" $ }}

NOTE: instance derives from .Release.Name, matching the umbrella chart's
convention. Selector labels are IMMUTABLE on a Deployment — keep the Argo
Application's helm.releaseName pinned to "litellm" so this never re-renders.
*/}}
{{- define "litellm.selectorLabels" -}}
app.kubernetes.io/name: litellm
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
