---
id: scans
title: 스캔
description: 소스·컨테이너 스캔 실행, 진행 상황 실시간 모니터링, 종료 상태 읽기 — TrustedOSS Portal의 전체 스캔 수명 주기.
sidebar_label: 스캔
sidebar_position: 2
---

# 스캔

**스캔**은 프로젝트의 컴포넌트·라이선스·취약점을 탐지하는 end-to-end 실행입니다. 스캔은 Celery 워커에서 실행되며(API 인라인 절대 금지), 일반적으로 5분(작은 npm 프로젝트)에서 60분(큰 멀티 모듈 Java 레포)까지 소요됩니다.

:::note 대상 독자
프로젝트 소속 팀의 `developer` 이상 권한 보유 엔지니어. 사설 저장소 스캔은 프로젝트의 `git_url`에 자격증명을 포함해야 합니다 — [프로젝트 → 사설 저장소](./projects.md#사설-저장소).
:::

## 스캔 종류

| 종류 | 파이프라인 | 탐지 대상 |
|---|---|---|
| **`source`** | `cdxgen` → ORT → Dependency-Track | 컴포넌트와 그 declared / detected / concluded 라이선스, NVD/OSV/GitHub Advisory의 CVE. |
| **`container`** | Trivy | 컨테이너 이미지의 OS 패키지 취약점(언어 패키지 CVE는 제한적). |

v2.0.0의 UI 트리거에서는 `source`만 노출됩니다. 직접 호출하는 클라이언트라면 API가 `container`도 수용합니다. UI 페어리티는 [로드맵](#로드맵-v2x) 참고.

## 스캔 트리거

### UI에서

1. 사이드바에서 **Projects** 열기.
2. 프로젝트 행을 찾고, 행 끝의 **Scan** 버튼 클릭.
3. 스캔이 즉시 시작됩니다 — 프로젝트 기본 브랜치 대상 `source` 스캔.

v2.0.0 UI에는 종류 선택 다이얼로그나 브랜치 오버라이드 필드가 없습니다 — 해당 컨트롤은 v2.1로 이연되었습니다([로드맵](#로드맵-v2x) 참고). 페이지가 WebSocket 기반 실시간 진행 뷰로 전환됩니다. 탭을 닫아도 스캔은 워커에서 계속됩니다. 프로젝트를 다시 열면 언제든 재연결됩니다.

### API에서

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source"}' | jq .
```

응답에 스캔 UUID가 포함됩니다. 폴링:

```bash
curl -sS "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" | jq .status
```

### CI에서

권장 경로는 [GitHub Action](../ci-integration/github-actions.md), [GitLab CI 템플릿](../ci-integration/gitlab-ci.md), [Jenkinsfile 예시](../ci-integration/jenkins.md)입니다. 모두 API를 감싸고 빌드 게이트를 추가합니다.

## 수명 주기

```
queued ─────► running ─────► succeeded
                      │
                      └────► failed
```

| 상태 | 의미 |
|---|---|
| `queued` | 큐에 들어감; 빈 워커 슬롯 대기. |
| `running` | 워커가 작업을 받아 파이프라인 실행 중. |
| `succeeded` | 파이프라인 종료, 컴포넌트와 결과를 조회 가능. |
| `failed` | 워커가 오류를 일으킴. API 응답의 `error_detail` 또는 워커 로그를 확인. |

`cancelled` 종단 상태는 데이터 모델에 예약되어 있지만 v2.0.0에서는 API/UI 컨트롤로 노출되지 않습니다 — [로드맵](#로드맵-v2x) 참고.

### 파이프라인 단계 (source)

진행 뷰는 단계 전환을 실시간 표시합니다.

1. **Bootstrapping** — 작업 공간 준비.
2. **Fetching source** — `git clone`(또는 기존 작업 공간이면 `git fetch` + checkout).
3. **Detecting components** — `cdxgen`이 레포를 탐색하여 CycloneDX SBOM을 생성.
4. **Analyzing licenses** — ORT가 `ort/rules.kts`의 룰셋을 적용.
5. **Resolving vulnerabilities** — Dependency-Track이 SBOM을 피드 미러와 대조.
6. **Persisting** — 컴포넌트·라이선스·결과를 PostgreSQL에 저장.

5단계 실행 시 Dependency-Track이 사용 불가하면 [DT 회로 차단기](../admin-guide/dt-connector.md)가 OPEN으로 전환되며 PostgreSQL 취약점 캐시에서 읽습니다. 스캔은 `succeeded`로 표시되되 UI에 경고가 노출됩니다.

## 평균 소요 시간

| 프로젝트 크기 | 소스 스캔 | 컨테이너 스캔 |
|---|---|---|
| 소형 (≤ 50 컴포넌트) | 3–8분 | 1–3분 |
| 중형 (50–500) | 8–20분 | 2–5분 |
| 대형 (≥ 500, 멀티 모듈) | 20–60분 | 5–10분 |

소스 스캔의 비용은 `cdxgen`이 아니라 ORT + Dependency-Track 상관관계 분석에 집중됩니다. 컨테이너 스캔은 워커 캐시에 이미지가 없을 때 풀 시간이 지배적입니다.

## 전역 스캔 큐

좌측 사이드바의 **Scans**는 모든 실행 중·대기 중 스캔의 조직 단위 뷰입니다. 필터: 상태·종류·프로젝트·팀. super-admin은 워커별 큐 깊이도 봅니다.

이 뷰의 취소 동작은 v2.0.0에서 노출되지 않습니다 — [로드맵](#로드맵-v2x) 참고.

## WebSocket 진행 피드

UI는 단계·진행률 실시간 갱신을 위해 `ws(s)://<host>/ws/scans/{scan_id}`를 구독합니다. 네트워크가 끊어지면 지수 백오프로 자동 재연결합니다. 재연결 시 최신 단계를 다시 발행해 UI가 빠르게 동기화됩니다.

커스텀 클라이언트의 메시지 형식:

```json
{
  "step": "resolving_vulnerabilities",
  "percent": 62,
  "ts": "2026-05-09T13:42:11Z"
}
```

`percent`는 0–100 정수입니다. `step`은 위 파이프라인 단계 슬러그와 일치합니다. 프레임은 `scan_id`를 다시 보내지 않습니다 — 구독자가 URL에서 이미 알고 있기 때문입니다.

## 정상 동작 확인

스캔 완료 후:

1. 프로젝트 상태가 **Succeeded**로 전환.
2. 컴포넌트 수 > 0.
3. 취약점 수가 표시(프로젝트가 정말 깨끗하면 0일 수도 있음).
4. Overview 탭의 마지막 스캔 타임스탬프가 "방금"을 반영.
5. 감사 로그에 `scan.create`와 `scan.update` 이벤트가 기록.

## 트러블슈팅

### 스캔이 `Queued`에서 멈춤

워커가 아직 받지 못했습니다. 워커가 다운되었거나 큐가 포화 상태입니다.

```bash
docker-compose -f docker-compose.yml ps worker
docker-compose -f docker-compose.yml logs --tail=200 worker
```

워커가 unhealthy면 재시작:

```bash
docker-compose -f docker-compose.yml restart worker
```

큐가 포화면 `.env`의 `CELERY_CONCURRENCY`를 늘리고 `docker-compose up -d worker`로 스케일 업. 동시 슬롯당 ~2 GB RAM 필요.

### `git clone` 오류로 스캔 실패

워커가 저장소에 도달하지 못했습니다. 확인:

- 레포 URL이 정확한가? (워커에서 테스트: `docker-compose exec worker git ls-remote <url>`)
- 사설 레포인가? `git_url`에 자격증명을 포함하세요 — [프로젝트 → 사설 저장소](./projects.md#사설-저장소).
- 워커가 git 호스트로 outbound HTTPS 가능? 사내 프록시는 `.env`(`HTTP_PROXY`, `HTTPS_PROXY`)에 설정.

### 스캔은 끝났는데 취약점이 누락

Dependency-Track이 사용 불가했을 수 있습니다. **/admin/dt** 확인 — 회로 차단기가 `CLOSED`여야 합니다. `OPEN`이면 스캔이 캐시 기반으로 성공한 것이며, 다음 DT 왕복(보통 다음 시간 단위 동기화)에서 취약점이 갱신됩니다.

### 스캔에 "DT unreachable" 경고

위와 동일 — 회로 차단기가 트립되었습니다. 스캔은 캐시로 완료되었고 경고는 정보성입니다. 근본 DT 장애를 해결하고 새 스캔을 트리거하면 갱신됩니다.

## 로드맵 (v2.x)

매뉴얼이 이전에 약속했으나 v2.0.0에 포함되지 않은 항목.

- 프로젝트 단위 **Scan** 트리거의 종류 선택 다이얼로그(Source / Container)와 브랜치 오버라이드 필드 — v2.1 예정.
- `cancelled` 수명 주기 전환과 `DELETE /v1/scans/{id}`, 전역 큐의 UI 취소 버튼 — v2.1 예정.

## 함께 보기

- [컴포넌트·라이선스](./components-and-licenses.md)
- [취약점](./vulnerabilities.md)
- [GitHub Actions](../ci-integration/github-actions.md)
- [DT 커넥터](../admin-guide/dt-connector.md)
