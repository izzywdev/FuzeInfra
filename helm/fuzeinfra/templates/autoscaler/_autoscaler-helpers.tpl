{{/*
Shared helpers for the Cluster Autoscaler stack (provider + CA + overprovisioning).
*/}}

{{/*
Selector labels for the cluster-autoscaler component itself.
Usage: {{ include "fuzeinfra.clusterAutoscaler.selectorLabels" $ }}
*/}}
{{- define "fuzeinfra.clusterAutoscaler.selectorLabels" -}}
{{- include "fuzeinfra.selectorLabels" (dict "component" "cluster-autoscaler" "root" .) -}}
{{- end -}}
