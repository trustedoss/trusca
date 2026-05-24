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
Object names for the optional bundled datastores. Stable, fullname-prefixed.
*/}}
{{- define "trustedoss.postgres.fullname" -}}
{{- printf "%s-postgres" (include "trustedoss.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "trustedoss.redis.fullname" -}}
{{- printf "%s-redis" (include "trustedoss.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Name of the Secret this chart creates to hold DATABASE_URL* / REDIS_URL /
SECRET_KEY material. Only rendered when env.secret.existingSecret is unset.
*/}}
{{- define "trustedoss.secretName" -}}
{{- if .Values.env.secret.existingSecret -}}
{{- .Values.env.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "trustedoss.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Effective in-cluster DSN for the BUNDLED Postgres, RUNTIME/app-role view
(asyncpg). Used only when postgres.bundled=true. When role separation is on,
the runtime uses the DML-only app role; otherwise the owner role serves both.
*/}}
{{- define "trustedoss.postgres.appDsn" -}}
{{- $svc := include "trustedoss.postgres.fullname" . -}}
{{- $port := .Values.postgres.service.port | toString -}}
{{- $db := .Values.postgres.auth.database -}}
{{- if .Values.postgres.auth.roleSeparation -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:%s/%s" .Values.postgres.auth.appUsername (.Values.postgres.auth.appPassword | urlquery) $svc $port $db -}}
{{- else -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:%s/%s" .Values.postgres.auth.username (.Values.postgres.auth.password | urlquery) $svc $port $db -}}
{{- end -}}
{{- end -}}

{{/*
Effective in-cluster DSN for the BUNDLED Postgres, OWNER/DDL-role view
(asyncpg). Always the owning role; consumed by the migration Job (alembic).
*/}}
{{- define "trustedoss.postgres.ownerDsn" -}}
{{- $svc := include "trustedoss.postgres.fullname" . -}}
{{- $port := .Values.postgres.service.port | toString -}}
{{- $db := .Values.postgres.auth.database -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:%s/%s" .Values.postgres.auth.username (.Values.postgres.auth.password | urlquery) $svc $port $db -}}
{{- end -}}

{{/*
Effective in-cluster REDIS_URL for the BUNDLED Redis. db 0.
*/}}
{{- define "trustedoss.redis.url" -}}
{{- printf "redis://%s:%s/0" (include "trustedoss.redis.fullname" .) (.Values.redis.service.port | toString) -}}
{{- end -}}

{{/*
Resolve whether this chart should render its own Secret (true) or the operator
supplied an existingSecret (false). Centralised so templates stay consistent.
*/}}
{{- define "trustedoss.createSecret" -}}
{{- if .Values.env.secret.existingSecret -}}false{{- else -}}true{{- end -}}
{{- end -}}

{{/*
RUNTIME secret-backed env (backend / worker / beat). These workloads see only
the DML-only app DSN — never DATABASE_URL_OWNER — so a runtime RCE cannot run
DDL (drop the audit-log trigger, TRUNCATE, etc). Mirrors docker-compose.yml's
`DATABASE_URL_APP` / `DATABASE_URL` wiring. Call with the root context.

Sources, in precedence order:
  1. env.secret.existingSecret  — operator-managed Secret; we reference its keys.
  2. postgres.bundled / env.* values — we render a chart Secret (secret.yaml)
     and reference it here.
*/}}
{{- define "trustedoss.runtimeSecretEnv" -}}
{{- $secretName := include "trustedoss.secretName" . -}}
- name: DATABASE_URL_APP
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: DATABASE_URL_APP
# Backend's database_url() reads DATABASE_URL_APP first, then DATABASE_URL.
# We set both to the SAME app DSN so single-role and role-separated stacks
# behave identically at runtime (owner DSN is only ever given to the Job).
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: DATABASE_URL_APP
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: REDIS_URL
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: SECRET_KEY
{{- if and .Values.env.dt.apiKey (eq (include "trustedoss.createSecret" .) "true") }}
- name: DT_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: DT_API_KEY
{{- end }}
{{- end -}}

{{/*
MIGRATION secret-backed env (pre-install/pre-upgrade Job ONLY). The Job runs
`alembic upgrade head` as the OWNER role, so it gets DATABASE_URL_OWNER (and
DATABASE_URL pointed at the owner DSN, which alembic/env.py also honours).
SECRET_KEY/REDIS_URL are not needed for migrations but are harmless to omit.
*/}}
{{- define "trustedoss.migrationSecretEnv" -}}
{{- $secretName := include "trustedoss.secretName" . -}}
- name: DATABASE_URL_OWNER
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: DATABASE_URL_OWNER
# alembic/env.py prefers DATABASE_URL_OWNER; we also set DATABASE_URL to the
# owner DSN so the legacy single-role fallback path resolves correctly too.
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: DATABASE_URL_OWNER
{{- end -}}
