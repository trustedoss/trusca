---
id: helm
title: Helm으로 Kubernetes에 설치
description: 프로덕션 등급 Helm 차트로 TrustedOSS Portal을 Kubernetes에 배포합니다 — 번들 또는 외부 PostgreSQL·Redis, Ingress TLS, 마이그레이션 Job.
sidebar_label: Helm / Kubernetes
sidebar_position: 3
---

# Helm으로 Kubernetes에 설치

:::note 대상 독자
프로덕션 등급 Helm 차트로 TrustedOSS Portal을 배포하려는 Kubernetes 운영자.
`kubectl`, Helm 3, 기본 클러스터 관리(Ingress, StorageClass, cert-manager)
숙련도를 전제합니다. 단일 호스트를 운영한다면
[Docker Compose 설치](./docker-compose.md)가 더 간단합니다.
:::

Helm 차트(`charts/trustedoss`, 차트 버전 **0.10.0**)는 포털 전체를 배포합니다.
FastAPI 백엔드, Celery 워커와 beat 스케줄러, React 프론트엔드, TLS가 적용된
Ingress, 데이터베이스 마이그레이션 Job을 포함합니다. PostgreSQL과 Redis는
클러스터 내부에 번들(평가용)하거나 외부 관리형 데이터스토어를 가리킬 수
있습니다(프로덕션 권장).

:::info 취약점 매칭은 차트 내장
워커 파드는 Trivy DB를 포함하며 `ghcr.io/aquasecurity/trivy-db`에서(또는 `env.trivy.dbRepository` 미러에서) 다운로드·갱신합니다. 외부 취약점 엔진은 필요하지 않습니다. [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) 참조.
:::

## 차트가 배포하는 것

| 워크로드 | 종류 | 비고 |
|---|---|---|
| backend | Deployment | FastAPI API. `AUTO_MIGRATE=false` — 마이그레이션은 Job이 수행합니다. |
| worker | Deployment (+ 선택적 HPA) | Celery 워커 (cdxgen / scancode / Trivy). |
| beat | Deployment (replicas: 1) | Celery 스케줄러 — 싱글턴. |
| frontend | Deployment | nginx 위 React SPA (`:8080`). |
| postgres | StatefulSet | 선택적 번들 (`postgres.bundled`). |
| redis | Deployment | 선택적 번들 (`redis.bundled`). |
| migrate | Job (pre-install / pre-upgrade 훅) | owner 역할로 `alembic upgrade head`. |
| ingress | Ingress | cert-manager TLS; API + SPA 라우팅. |

## 사전 요구사항

- 네임스페이스와 워크로드를 생성할 권한이 있는 Kubernetes 클러스터와 `kubectl`
  컨텍스트.
- Helm 3.
- **인그레스 컨트롤러**(차트 기본 클래스는 `nginx`).
- 기본 TLS 구성을 위한 `letsencrypt-prod` 이름의 `ClusterIssuer`가 있는
  **cert-manager**(`ingress.annotations`로 재정의 가능).
- 다중 노드 클러스터에서는 공유 스캔 워크스페이스용 **`ReadWriteMany`
  StorageClass**(`workspace.persistence.storageClassName`). 단일 노드
  클러스터는 파드별 `emptyDir` 폴백을 사용할 수 있습니다.

## 설치 전 차트 검증

배포 전에, 클러스터 없이 in-repo 차트를 로컬에서 렌더링하여 values·템플릿
오류를 잡습니다(Helm 3+, 저장소 루트에서 실행):

<!-- docs-uat: id=helm-chart-validate kind=shell ctx=host expect=exit:0 tier=nightly -->
```bash
SECRET=$(openssl rand -hex 32)
helm lint charts/trustedoss \
  --set env.secret.secretKey="$SECRET" \
  --set postgres.auth.password=throwaway \
  --set ingress.host=trustedoss.example.com
helm template trustedoss charts/trustedoss --namespace trustedoss \
  --set env.secret.secretKey="$SECRET" \
  --set postgres.auth.password=throwaway \
  --set ingress.host=trustedoss.example.com \
  >/dev/null
```

`helm lint`는 차트 구조 문제를 보고하고, `helm template`은 최소 필수 values로
모든 매니페스트를 완전히 렌더링하므로 0이 아닌 종료 코드는 차트가 설치되지
않음을 뜻합니다. 여기 `--set` 값은 일회용이며 — 실제 설치는 아래에서 본인의
시크릿을 사용합니다.

## 빠른 시작 (번들 데이터스토어, 평가용)

PostgreSQL과 Redis를 클러스터 내부에서 실행합니다 — 빠르게 띄울 수 있지만
프로덕션 데이터에는 **권장하지 않습니다**.

<!-- docs-uat: id=helm-install-bundled kind=shell ctx=host tier=manual waiver=needs-live-cluster-and-published-oci-chart -->
```bash
helm install trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version 0.10.0 \
  --namespace trustedoss --create-namespace \
  --set env.secret.secretKey="$(openssl rand -hex 32)" \
  --set postgres.auth.password="$(openssl rand -hex 24)" \
  --set ingress.host=trustedoss.example.com \
  --set env.corsAllowedOrigins=https://trustedoss.example.com
```

`trustedoss.example.com`을 자신의 호스트명으로 바꾸고, 해당 호스트의 DNS가 인그레스
컨트롤러를 가리키는지 확인하십시오.

:::caution 번들 데이터스토어는 평가용입니다
클러스터 내부 PostgreSQL과 Redis는 기본값이 소박하고 단일 레플리카입니다. 시험
이상의 용도라면 외부 관리형 데이터스토어(아래)를 사용하십시오.
:::

## 프로덕션 (외부 관리형 데이터스토어 — 권장)

클러스터 내부 번들 대신 PostgreSQL은 Cloud SQL / RDS, Redis는 Memorystore /
ElastiCache를 권장합니다. values 파일을 제공하십시오.

```yaml
# values.prod.yaml
postgres:
  bundled: false
redis:
  bundled: false
env:
  database:
    url: postgresql+asyncpg://app:***@cloudsql-proxy:5432/trustedoss
    # DDL/owner 역할을 런타임 역할과 분리하는 경우:
    ownerUrl: postgresql+asyncpg://owner:***@cloudsql-proxy:5432/trustedoss
  redis:
    url: redis://memorystore:6379/0
  secret:
    # 네 개 키를 모두 담은 사전 생성 Secret (아래 참고)
    existingSecret: trustedoss-prod-secrets
  corsAllowedOrigins: https://trustedoss.example.com
ingress:
  host: trustedoss.example.com
```

설치합니다.

<!-- docs-uat: id=helm-install-prod kind=shell ctx=host tier=manual waiver=needs-live-cluster-and-published-oci-chart -->
```bash
helm install trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version 0.10.0 \
  --namespace trustedoss --create-namespace \
  -f values.prod.yaml
```

:::warning Secret 구성은 필수입니다
`env.secret.existingSecret`을 설정하면 차트는 자체 Secret을 렌더링하지
**않습니다**. 참조하는 Secret은 네 개 키를 모두 담아야 하며, 그렇지 않으면 파드가
시작되지 않습니다.

- `DATABASE_URL_APP`
- `DATABASE_URL_OWNER`
- `REDIS_URL`
- `SECRET_KEY` (최소 32자)
:::

:::note 프로덕션 CORS
`env.corsAllowedOrigins`는 SPA를 제공하는 정확한 오리진을 **열거**해야 합니다 —
프로덕션에서 와일드카드 금지. 브라우저가 사용할 모든 scheme + host를 나열하십시오.
:::

## 마이그레이션 동작 방식

Helm `pre-install` + `pre-upgrade` 훅 Job이 **owner** DB 역할
(`DATABASE_URL_OWNER`)로 `alembic upgrade head`를 **한 번** 실행합니다. 애플리케이션
파드는 `AUTO_MIGRATE=false`로 실행되므로 Job이 유일한 마이그레이터입니다.

백엔드 파드는 스키마가 HEAD에 도달할 때까지 `NotReady`(`/health/ready`가 `503`
반환)로 유지되므로, 트래픽은 마이그레이션된 스키마에만 도달합니다. 마이그레이션은
forward-only이며 Job은 절대 다운그레이드하지 않습니다. 번들 케이스의 훅 순서는
Secrets → Postgres Service / StatefulSet → 마이그레이션 Job이며, Job의 init
컨테이너는 alembic 실행 전 Postgres가 연결을 받을 때까지 대기합니다.

## 업그레이드

<!-- docs-uat: id=helm-upgrade kind=shell ctx=host tier=manual waiver=needs-live-cluster-and-published-oci-chart -->
```bash
helm upgrade trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version <새-차트-버전> \
  --namespace trustedoss \
  -f values.prod.yaml
```

pre-upgrade 마이그레이션 Job이 새 파드 롤아웃 전에 새 스키마를 적용합니다.
마이그레이션은 forward-only이므로 업그레이드 전에 데이터베이스를 백업하십시오 —
[백업 및 복원](../admin-guide/backup-and-restore.md)을 참고하십시오.

## 주요 values

전체 표는 [차트 README](https://github.com/trustedoss/trustedoss-portal/blob/main/charts/trustedoss/README.md)에
있습니다. 가장 자주 설정하는 값은 다음과 같습니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `image.tag` | `0.10.0` | backend / worker / frontend 이미지 태그(절대 `:latest` 금지). |
| `ingress.host` | `""` | **필수.** 공개 호스트명. |
| `env.corsAllowedOrigins` | `""` | **프로덕션 필수.** 허용 브라우저 오리진(와일드카드 금지). |
| `env.secret.secretKey` | `""` | `SECRET_KEY`(≥32자). `existingSecret`이 없으면 필수. |
| `env.secret.existingSecret` | `""` | 네 개 키를 담은 사전 생성 Secret; 차트 Secret을 비활성화. |
| `postgres.bundled` | `true` | `false` → `env.database.*`(외부) 사용. |
| `redis.bundled` | `true` | `false` → `env.redis.url`(외부) 사용. |
| `env.trivy.dbRepository` | `ghcr.io/aquasecurity/trivy-db` | air-gapped 사내 미러로 오버라이드 — [Air-gapped 운영](../admin-guide/vulnerability-data.md#air-gapped) 참조. |
| `env.trivy.dbRefreshHours` | `168` | 주간 Trivy DB refresh. 낮추면 신선도↑. |
| `worker.trivyDbPersistence.enabled` | `true` | `/var/lib/trivy`에 PVC 마운트해 워커 재시작마다 재다운로드 방지. |
| `workspace.persistence.storageClassName` | `""` | 다중 노드 클러스터의 공유 스캔 볼륨용 RWX 클래스. |
| `worker.replicaCount` | `2` | 파드별 `concurrency`보다 워커 파드 스케일링을 권장. |

## 작동 확인

<!-- docs-uat: id=helm-verify-migrate-job kind=manual tier=manual -->
1. 마이그레이션 Job이 완료되었는지 확인합니다.

   ```bash
   kubectl -n trustedoss get jobs
   # trustedoss migrate Job의 COMPLETIONS가 1/1 이어야 합니다.
   ```

<!-- docs-uat: id=helm-verify-pods-ready kind=manual tier=manual -->
2. 모든 파드가 `Running`이고 백엔드 파드가 `Ready`인지 확인합니다.

   ```bash
   kubectl -n trustedoss get pods
   # 백엔드 파드 Ready = /health/ready가 200 반환(스키마 HEAD).
   ```

<!-- docs-uat: id=helm-verify-readiness-probe kind=manual tier=manual -->
3. 클러스터 내부에서 readiness 프로브가 통과하는지 확인합니다.

   ```bash
   kubectl -n trustedoss exec deploy/trustedoss-backend -- \
     curl -fsS http://localhost:8000/health/ready
   # → {"status":"ready"}
   ```

<!-- docs-uat: id=helm-verify-ingress-cert kind=manual tier=manual -->
4. Ingress에 주소와 유효한 인증서가 있는지 확인한 뒤 브라우저에서
   `https://<ingress.host>/`를 열어 로그인합니다.

## 문제 해결

- **백엔드 파드가 `NotReady`에서 멈춤.** 스키마가 HEAD에 도달할 때까지
  `/health/ready`는 `503`을 반환합니다. 마이그레이션 Job 로그를 확인하십시오.

  ```bash
  kubectl -n trustedoss logs job/trustedoss-migrate
  ```

  Job 실패는 보통 owner DSN(`DATABASE_URL_OWNER`)에 DDL 권한이 없거나
  데이터베이스에 도달할 수 없다는 의미입니다.

- **기존 Secret 사용 시 파드가 `CreateContainerConfigError`.** 참조 Secret에 네 개
  필수 키 중 하나가 없습니다. 확인하십시오.

  ```bash
  kubectl -n trustedoss get secret trustedoss-prod-secrets -o jsonpath='{.data}' | tr ',' '\n'
  # DATABASE_URL_APP, DATABASE_URL_OWNER, REDIS_URL, SECRET_KEY 가 있어야 합니다.
  ```

- **다중 노드 클러스터에서 스캔 실패.** 백엔드와 워커가 스캔 워크스페이스를
  공유합니다. `ReadWriteMany` StorageClass가 없으면 워커가 백엔드의 쓰기 내용을
  읽을 수 없습니다. `workspace.persistence.storageClassName`을 RWX
  클래스(nfs / efs / filestore / longhorn)로 설정하십시오.

- **TLS 인증서가 발급되지 않음.** 기본 어노테이션은 `letsencrypt-prod` 이름의
  cert-manager `ClusterIssuer`를 기대합니다. Certificate를 점검하십시오.

  ```bash
  kubectl -n trustedoss describe certificate
  ```

차트 버그를 만나면 [버그 신고 템플릿](https://github.com/trustedoss/trustedoss-portal/issues/new/choose)으로
이슈를 열어 주십시오.

## 함께 보기

- [Docker Compose로 설치](./docker-compose.md) — 단일 호스트 설치
- [업그레이드](./upgrade.md) — Docker Compose 업그레이드 경로
- [백업 및 복원](../admin-guide/backup-and-restore.md) — 업그레이드 전 백업
- [환경 변수](../reference/env-variables.md) — 차트가 매핑하는 모든 설정
- [아키텍처](../reference/architecture.md) — 서비스, Trivy DB 라이프사이클, 마이그레이션 모델
- [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) — air-gapped 운영과 DB refresh
- [v0.10.0 릴리스 노트](../release-notes/v0.10.0.md) — 차트 0.10.0 breaking changes
