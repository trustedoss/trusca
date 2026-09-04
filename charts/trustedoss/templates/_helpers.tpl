{{- /*
SPDX-License-Identifier: Apache-2.0
Copyright 2026 TRUSCA contributors
*/ -}}
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
{{/*
Extra environment for the three application workloads (backend / worker /
beat), from `env.extraEnv` and `env.extraEnvFrom`.

Why the chart has these at all: every other key here is spelled out 1:1, which
is honest about what the chart supports but leaves it a release behind the
portal. The portal grew OAuth, SMTP / Slack / Teams notifications, the vendored
code identification service and the Jira link since chart 0.12.0, none of which
could be set at all on a Helm install. Enumerating them would have the same
problem again at the next release.

Secrets go through `extraEnvFrom` referencing a Secret the operator created,
not through `extraEnv`: a value in `extraEnv` lives in values.yaml, and an SMTP
password or an OAuth client secret does not belong in a file people commit.
*/}}
{{- define "trustedoss.extraEnv" -}}
{{- with .Values.env.extraEnv }}
{{- range $key, $value := . }}
- name: {{ $key }}
  value: {{ $value | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "trustedoss.extraEnvFrom" -}}
{{- with .Values.env.extraEnvFrom }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
Extra volumes and mounts for the same four workloads.

Why the chart needs these and `extraEnv` was not enough: a setting that names a
FILE is only half a setting. The certificate variables an operator sets for a
private authority (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`,
`GIT_SSL_CAINFO`) could already be passed through `extraEnv`, and had nothing to
point at: the chart could not mount the certificate, and its default
`readOnlyRootFilesystem: true` rules out writing one in at start. So the
variables arrived and every path they named was absent.

Raw lists rather than a shape of our own, for the reason `extraEnvFrom` is one:
the source is the operator's (a Secret, a ConfigMap, a CSI driver), and a
wrapper would support the sources we thought of.

Both go to backend, beat and both workers, not to frontend or redis. A
certificate is for the processes that make outbound calls, and the two that do
not are left alone rather than given a mount they would not read.
*/}}
{{- define "trustedoss.extraVolumes" -}}
{{- with .Values.env.extraVolumes }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{- define "trustedoss.extraVolumeMounts" -}}
{{- with .Values.env.extraVolumeMounts }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
Whether this workload renders a `volumes:` key at all.

Beat and the workers only mount anything when the root filesystem is sealed, so
the key is inside that conditional. An operator who turns the seal off and adds
an extra volume would otherwise get a volume list with no key above it.
*/}}
{{- define "trustedoss.hasExtraVolumes" -}}
{{- if .Values.env.extraVolumes }}true{{- end }}
{{- end -}}

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
# A5 — dedicated, rotatable HMAC key for hashing stored API-key secrets
# (core.config.api_key_hmac_secret). Mirrors SECRET_KEY's wiring, including
# the fail-closed render policy: secret.yaml requires
# env.secret.apiKeyHmacSecret to be set explicitly (no derivation from
# secretKey) whenever env.secret.existingSecret is unset.
- name: API_KEY_HMAC_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: API_KEY_HMAC_SECRET
{{- /* Chart 0.3.0 (W6 / ADR-0001) — DT_API_KEY removed. Trivy DB is local to
       the worker (no external engine), so no secret material is injected for
       vulnerability matching. */ -}}
{{- end -}}

{{/*
MIGRATION secret-backed env (pre-install/pre-upgrade Job ONLY). The Job runs
`alembic upgrade head` as the OWNER role, so it gets DATABASE_URL_OWNER (and
DATABASE_URL pointed at the owner DSN, which alembic/env.py also honours).
SECRET_KEY/REDIS_URL are not needed for migrations but are harmless to omit.
*/}}
{{/*
S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4/§5): comma-joined `-Q`
argument for one worker kind. Call with a dict `{root: $, own: "scan"}` (or
"default"). The list comes straight from worker.subscriptions.<own>, so what
a worker consumes is stated in values rather than derived from a flag. By
default worker-default takes only the default queue, and worker-scan takes
both so it can still drain a pre-split scan message left on
worker.queues.default. See that value's comment in values.yaml and README.md
"Queue split transition" for narrowing worker-scan too.
*/}}
{{- define "trustedoss.worker.subscribedQueues" -}}
{{- $w := .root.Values.worker -}}
{{- $subs := index $w.subscriptions .own -}}
{{- if not $subs -}}
{{- fail (printf "worker.subscriptions.%s must list at least one queue" .own) -}}
{{- end -}}
{{- join "," $subs -}}
{{- end -}}

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
