{{/*
Chart name / fullname helpers (standard Helm idiom).
*/}}
{{- define "trustedoss.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "trustedoss.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "trustedoss.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "trustedoss.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels applied to every object.
*/}}
{{- define "trustedoss.labels" -}}
helm.sh/chart: {{ include "trustedoss.chart" . }}
{{ include "trustedoss.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "trustedoss.selectorLabels" -}}
app.kubernetes.io/name: {{ include "trustedoss.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Per-component ServiceAccount name. component arg is the workload suffix.
*/}}
{{- define "trustedoss.serviceAccountName" -}}
{{- printf "%s-%s" (include "trustedoss.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Secret-backed env entries (DATABASE_URL, SECRET_KEY). Shared by all three
workloads so the secret wiring stays in one place. Call with the root context.
*/}}
{{- define "trustedoss.secretEnv" -}}
{{- if .Values.env.database.existingSecret }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.env.database.existingSecret }}
      key: {{ .Values.env.database.secretKey }}
{{- else if .Values.env.database.url }}
- name: DATABASE_URL
  value: {{ .Values.env.database.url | quote }}
{{- end }}
{{- if .Values.env.secret.existingSecret }}
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.env.secret.existingSecret }}
      key: {{ .Values.env.secret.secretKey }}
{{- else if .Values.env.secret.value }}
- name: SECRET_KEY
  value: {{ .Values.env.secret.value | quote }}
{{- end }}
{{- end -}}
