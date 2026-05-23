---
id: scans
title: 스캔
description: 소스·컨테이너 스캔 실행, 진행 상황 실시간 모니터링, 종료 상태 읽기 — TrustedOSS Portal의 전체 스캔 수명 주기.
sidebar_label: 스캔
sidebar_position: 2
---

# 스캔

**스캔**은 프로젝트의 컴포넌트·라이선스·취약점을 탐지하는 end-to-end 실행입니다. 소스 스캔 파이프라인은 `cdxgen`(CycloneDX generator), scancode(first-party 라이선스 탐지), Dependency-Track(DT)을 체이닝하며, 컨테이너 스캔 파이프라인은 Trivy(Aqua Security 컨테이너 스캐너)를 사용해 CVE(Common Vulnerabilities and Exposures)와 라이선스 이슈를 탐지합니다. 스캔은 Celery 워커에서 실행되며(API 인라인 절대 금지), 일반적으로 5분(작은 npm 프로젝트)에서 60분(큰 멀티 모듈 Java 레포)까지 소요됩니다.

:::note 대상 독자
프로젝트 소속 팀의 `developer` 이상 권한 보유 엔지니어. 사설 저장소 스캔은 프로젝트의 `git_url`에 자격증명을 포함해야 합니다 — [프로젝트 → 사설 저장소](./projects.md#사설-저장소).
:::

## 스캔 종류

| 종류 | 파이프라인 | 탐지 대상 |
|---|---|---|
| **`source`** | `cdxgen` → scancode(first-party 라이선스 탐지) → Dependency-Track | 컴포넌트와 그 **declared** 라이선스(의존성 메타데이터에서), **detected** 라이선스(scancode 가 직접 스캔한 first-party 소스), NVD/OSV/GitHub Advisory 의 CVE(Common Vulnerabilities and Exposures). |
| **`container`** | Trivy | 컨테이너 이미지의 OS 패키지 취약점(언어 패키지 CVE는 제한적). |

v2.1부터 두 종류 모두 UI 스캔 다이얼로그에서 선택할 수 있습니다 — 스캔을 트리거할 때 **Source** 또는 **Container** 를 고르세요([스캔 트리거 → UI에서](#ui에서) 참고). API도 두 종류를 모두 수용합니다.

## 스캔 트리거

### UI에서

1. 사이드바에서 **Projects** 열기.
2. 프로젝트 행을 찾고, 행 끝의 **Scan** 버튼 클릭.
3. **스캔 다이얼로그**가 열립니다. 상단에서 스캔 종류를 선택합니다.
   - **Source** — 프로젝트 소스에 cdxgen + scancode + Dependency-Track 실행. 기본값입니다.
   - **Container** — 지정한 컨테이너 이미지에 Trivy 실행. [컨테이너 이미지 스캔](#컨테이너-이미지-스캔) 참고.
4. **Source** 스캔이면 소스 제공 방식(Git URL, 업로드한 `.zip`, 브라우저에서 압축할 폴더)을 고른 뒤 **Start scan** 을 클릭합니다.

프로젝트 목록 페이지에서 우측 슬라이드 드로어가 열리며 WebSocket 기반의 실시간 진행 뷰가 표시됩니다. 탭을 닫아도 스캔은 워커에서 계속됩니다. 프로젝트를 다시 열면 언제든 재연결됩니다. 스캔이 `queued` 또는 `running` 인 동안 드로어에는 **Cancel scan**(스캔 취소) 동작이 함께 표시됩니다 — [스캔 취소](#스캔-취소) 참고.

![스캔 진행 드로어 — bootstrap → fetch → cdxgen → scancode → DT → finalize 단계, WebSocket 실시간 표시](/img/screenshots/user-scans-progress-drawer.png)

:::warning 소스 스캔의 브랜치 선택
소스 스캔은 프로젝트의 `default_branch`(보통 `main`) 에 대해 실행됩니다.
UI 와 API 모두 브랜치 오버라이드를 노출하지 않습니다 —
`ScanCreate` 페이로드는 `kind` 와 `metadata` 만 허용합니다
(`apps/backend/schemas/scan.py` 참조). `develop` 이나 feature
브랜치를 스캔하려면 트리거 전에 **Project Settings** 에서
`default_branch` 를 임시로 변경한 뒤 되돌리세요. 트리거에 정식
`branch` 필드를 추가하는 작업은 v2.x 로드맵 항목입니다.
:::

### 컨테이너 이미지 스캔

스캔 다이얼로그에서 **Container** 를 선택하면 소스 대신 빌드된 이미지를 스캔합니다. Trivy(Aqua Security 컨테이너 스캐너)가 이미지의 **OS 패키지**에서 알려진 취약점을 검사합니다 — 애플리케이션 의존성 트리를 다루는 소스 스캔과 상호 보완적입니다.

1. 프로젝트 행의 **Scan** 버튼에서 스캔 다이얼로그를 엽니다.
2. 다이얼로그 상단에서 **Container** 를 선택합니다.
3. **컨테이너 이미지** 참조를 `name:tag` 형식으로 입력합니다. 예: `alpine:3.19` 또는 `ghcr.io/org/app:1.2.3`. 워커가 풀할 수 있는 이미지여야 합니다(공개 레지스트리, 또는 워커가 인증된 레지스트리).
4. **Start scan** 을 클릭합니다.

동일한 진행 드로어가 열립니다. 스캔이 `succeeded` 에 도달하면 OS 패키지 취약점이 프로젝트의 **Vulnerabilities** 탭에 나타납니다.

:::note 컨테이너 스캔은 Git URL이 필요 없음
컨테이너 스캔은 저장소가 아닌 이미지 참조를 읽습니다. `git_url` 이 없는 프로젝트도 컨테이너 스캔을 실행할 수 있습니다. Source / Container 선택은 프로젝트의 소스 설정과 무관합니다.
:::

### API에서

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source"}' | jq .
```

응답에 스캔 UUID가 포함됩니다. 폴링:

```bash
curl -sS "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .status
```

컨테이너 스캔은 `kind` 를 `container` 로 설정하고 이미지 참조를 `metadata.image_ref` 에 전달합니다.

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "container", "metadata": {"image_ref": "alpine:3.19"}}' | jq .
```

### CI에서

권장 경로는 [GitHub Action](../ci-integration/github-actions.md), [GitLab CI 템플릿](../ci-integration/gitlab-ci.md), [Jenkinsfile 예시](../ci-integration/jenkins.md)입니다. 모두 API를 감싸고 빌드 게이트를 추가합니다.

## 수명 주기

```
queued ─────► running ─────► succeeded
   │                  │
   │                  └────► failed
   │                  │
   └──────────────────┴────► cancelled
```

| 상태 | 의미 |
|---|---|
| `queued` | 큐에 들어감; 빈 워커 슬롯 대기. |
| `running` | 워커가 작업을 받아 파이프라인 실행 중. |
| `succeeded` | 파이프라인 종료, 컴포넌트와 결과를 조회 가능. |
| `failed` | 워커가 오류를 일으킴. API 응답의 `error_detail` 또는 워커 로그를 확인. |
| `cancelled` | `queued` 또는 `running` 중에 사용자나 관리자가 취소함. 워커 작업이 중단되고 워크스페이스가 정리됩니다. [스캔 취소](#스캔-취소) 참고. |

`queued`, `running` 은 비종단 상태이고, `succeeded`, `failed`, `cancelled` 는 종단 상태입니다. 스캔은 비종단 상태에서만 취소할 수 있습니다.

### 파이프라인 단계 (source)

진행 뷰는 단계 전환을 실시간 표시합니다.

1. **Bootstrapping** — 작업 공간 준비.
2. **Fetching source** — `git clone`(또는 기존 작업 공간이면 `git fetch` + checkout).
3. **Detecting components** — `cdxgen`이 레포를 탐색하여 CycloneDX SBOM을 생성하고, 각 의존성의 패키지 메타데이터에서 **declared** 라이선스를 읽습니다.
4. **Detecting first-party licenses** — scancode 가 프로젝트 자체 소스 파일을 스캔하여 발견한 **detected** 라이선스를 기록하며, 각 항목에 라이선스가 발견된 파일의 `source_path` 를 함께 태깅합니다([컴포넌트·라이선스 → declared vs. detected](./components-and-licenses.md#declared-vs-detected) 참고). 이 단계는 best-effort 입니다: scancode 가 미설치이거나 타임아웃이거나 트리가 너무 크면 declared 라이선스만으로 스캔을 계속합니다 — 저하되었으나 비치명적인 결과입니다. 이후 v2.0.0 의 법적 단계 분류는 `apps/backend/tasks/scan_source.py` 의 하드코딩된 `_LICENSE_CATEGORY_DEFAULTS` 사전에서 적용됩니다([컴포넌트·라이선스 → 분류 출처](./components-and-licenses.md#라이선스-분류) 참고).
5. **Resolving vulnerabilities** — Dependency-Track이 SBOM을 피드 미러와 대조.
6. **Persisting** — 컴포넌트·라이선스·결과를 PostgreSQL에 저장.

:::note ORT 는 scancode 로 교체됨
이전 빌드는 라이선스 단계에서 OSS Review Toolkit(ORT)을 실행했습니다. v2.0.0 은 이를 **first-party** 탐지를 위한 scancode 로 교체했습니다. 서드파티 의존성 소스는 의도적으로 다운로드하지 않으며 — 이는 스캔당 실행 시간을 예산 내로 유지하기 위함입니다 — 따라서 의존성 라이선스는 **declared**(cdxgen 에서)로 유지되고, scancode 는 팀이 실제로 작성한 코드에 대한 **detected** 라이선스를 추가합니다.
:::

5단계 실행 시 Dependency-Track이 사용 불가하면 [DT 회로 차단기](../admin-guide/dt-connector.md)가 OPEN으로 전환되며 PostgreSQL 취약점 캐시에서 읽습니다. 스캔은 `succeeded`로 표시되되 UI에 경고가 노출됩니다.

## 평균 소요 시간

| 프로젝트 크기 | 소스 스캔 | 컨테이너 스캔 |
|---|---|---|
| 소형 (≤ 50 컴포넌트) | 3–8분 | 1–3분 |
| 중형 (50–500) | 8–20분 | 2–5분 |
| 대형 (≥ 500, 멀티 모듈) | 20–60분 | 5–10분 |

소스 스캔의 비용은 Dependency-Track 상관관계 분석이 지배적이며, scancode 는 first-party 트리의 크기에 비례하여 시간을 추가합니다. 컨테이너 스캔은 워커 캐시에 이미지가 없을 때 풀 시간이 지배적입니다.

## 전역 스캔 큐

좌측 사이드바의 **Scans**는 모든 실행 중·대기 중 스캔의 조직 단위 뷰입니다. 큐는 5개의 상태 탭으로 나뉘어 있습니다: Running, Queued, Succeeded, Failed, All. 프로젝트·팀 단위 필터와 워커별 뷰는 로드맵 항목입니다.

![전역 /scans 큐 — Running / Queued / Succeeded / Failed / All 상태 탭과 project · kind · started-at 컬럼의 최근 실행 표](/img/screenshots/user-scans-queue.png)

각 `queued` 또는 `running` 행에는 Actions 컬럼에 **Cancel scan**(스캔 취소) 동작이 있습니다 — [스캔 취소](#스캔-취소) 참고.

## 스캔 취소

아직 `queued` 또는 `running` 상태인 스캔을 중단할 수 있습니다 — 예: 잘못된 브랜치에 대해 트리거했거나, 큰 레포의 스캔이 예상보다 오래 걸려 워커 슬롯을 비우고 싶을 때.

:::note 대상 독자
**소유 팀**의 `developer` 이상 권한 보유 팀 멤버. 본인 팀의 스캔만 취소할 수 있으며, 다른 팀에 속한 스캔은 보이지 않고 취소할 수도 없습니다. super-admin 은 [관리자 스캔 큐](../admin-guide/oncall-runbook.md#시나리오-3--스캔이-running-에서-4시간-이상-멈춤)에서 모든 스캔을 취소할 수 있습니다.
:::

### UI에서

**Cancel scan**(스캔 취소) 동작은 두 곳에 나타납니다.

- **스캔 진행 드로어**(스캔을 트리거할 때 열리거나, 실행 중인 스캔을 다시 열 때).
- 전역 [스캔 큐](#전역-스캔-큐)(`/scans`)의 각 `queued` 또는 `running` 행 **Actions** 컬럼.

취소 방법:

1. **Cancel scan** 클릭.
2. 인라인 확인이 나타납니다. 다시 **Cancel scan** 을 클릭해 확정하거나, **Keep running**(계속 실행)을 클릭해 닫습니다.
3. 스캔이 `cancelled` 로 이동하고 진행 바가 멈춥니다.

확정 시 서버에서 일어나는 일:

- 워커 작업이 중단됩니다(Celery 작업이 `SIGTERM` 으로 revoke 됨).
- 스캔의 워크스페이스(클론된 소스 트리)가 정리됩니다.
- 상태가 `cancelled` 로 바뀌고, 완료 타임스탬프와 `error_message = "cancelled by user"` 가 기록됩니다.
- 이 동작은 [감사 로그](../admin-guide/audit-log.md)에 `scans` `update` 로 기록됩니다.

:::tip 브라우저를 닫아도 안전
취소는 전적으로 서버에서 처리됩니다. 확정 후에는 패널이나 브라우저 탭을 닫아도 됩니다 — 워커는 멈추고 워크스페이스는 어느 쪽이든 정리됩니다.
:::

:::caution 이미 종료된 스캔은 취소 불가
이미 종단 상태(`succeeded`, `failed`, `cancelled`)에 도달한 스캔은 취소할 수 없습니다. UI 는 *"This scan already finished and can no longer be cancelled."* 메시지를 표시합니다. 이는 정상이며 — 멈출 것이 남아 있지 않습니다.
:::

### API에서

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/scans/${SCAN_ID}/cancel" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .
```

| 응답 | 의미 |
|---|---|
| `200 OK` | 스캔이 취소되었습니다. 본문에 `status: "cancelled"` 의 갱신된 스캔 레코드가 포함됩니다. |
| `404 Not Found` | 스캔이 존재하지 않거나, 멤버가 아닌 팀에 속해 있습니다. 다른 팀의 스캔은 존재가 숨겨집니다 — `404` 가 스캔의 존재를 확인해 주지 않습니다. |
| `409 Conflict` | 스캔이 이미 종단 상태입니다. RFC 7807 본문에 확장 필드 `scan_already_cancelled: true` 가 포함됩니다. |

### 취소 정상 동작 확인

1. 드로어와 `/scans` 큐에서 스캔 상태가 **Cancelled**(취소됨)로 표시됩니다(Cancelled 는 **All** 탭에 나타납니다).
2. 진행 바가 더 이상 진행하지 않습니다.
3. 워커 슬롯이 비어 — 뒤에 있던 `queued` 스캔이 `running` 을 시작합니다.
4. 감사 로그에 새 상태의 `scans` `update` 이벤트가 기록됩니다.

## WebSocket 진행 피드

UI는 단계·진행률 실시간 갱신을 위해 `ws(s)://<host>/ws/scans/{scan_id}`를 구독합니다. 네트워크가 끊어지면 지수 백오프로 자동 재연결합니다. 재연결 시 최신 단계를 다시 발행해 UI가 빠르게 동기화됩니다.

커스텀 클라이언트의 메시지 형식:

```json
{
  "step": "dt_findings",
  "percent": 62,
  "ts": "2026-05-09T13:42:11Z"
}
```

`percent`는 0–100 정수입니다. `step`은 파이프라인 슬러그(`bootstrap`, `fetch`, `prep`, `cdxgen`, `scancode`, `dt_upload`, `dt_findings`, `finalize`)와 2개의 종단 상태(`succeeded`, `failed`) 중 하나입니다. `scancode` 슬러그는 이전의 `ort` 슬러그를 같은 진행 percent 에서 대체했습니다. 프레임은 `scan_id`를 다시 보내지 않습니다 — 구독자가 URL에서 이미 알고 있기 때문입니다.

## 정상 동작 확인

스캔 완료 후:

1. 프로젝트 상태가 **Succeeded**로 전환.
2. 컴포넌트 수 > 0.
3. 취약점 수가 표시(프로젝트가 정말 깨끗하면 0일 수도 있음).
4. Overview 탭의 마지막 스캔 타임스탬프가 "방금"을 반영.
5. 감사 로그에 `target_table=scans&action=create`와 `target_table=scans&action=update` 이벤트가 기록.

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

### 스캔이 4시간 이상 `running` 상태로 멈춤

먼저 드로어나 `/scans` 큐에서 **Cancel scan**(스캔 취소)을 시도하세요([스캔 취소](#스캔-취소) 참고). 예를 들어 브로커가 도달 불가하여 스캔이 `cancelled` 로 이동하지 않으면, 강제 취소 + 워커 점검은 온콜 플레이북을 사용하세요:
[온콜 런북 → 스캔 멈춤](../admin-guide/oncall-runbook.md#시나리오-3--스캔이-running-에서-4시간-이상-멈춤).

### "Cancel scan" 을 눌러도 아무 일이 없음 / 스캔이 계속 실행됨

취소 요청은 API 에 도달했으나 워커가 제때 멈추지 않았습니다.

- 브로커(Redis)가 잠깐 도달 불가였다면, 스캔은 여전히 `cancelled` 로 표시되고 워크스페이스는 고아-워크스페이스 정리기와 워커 hard-limit 백스톱이 회수합니다 — 재시도할 필요가 없습니다.
- 1분 후에도 행이 `running` 으로 표시되면, 워커가 떠 있는지 확인하고(`docker-compose -f docker-compose.yml ps worker`) [온콜 런북](../admin-guide/oncall-runbook.md#시나리오-3--스캔이-running-에서-4시간-이상-멈춤)으로 에스컬레이션하세요.

### "This scan already finished and can no longer be cancelled"

페이지가 로드된 시점과 **Cancel scan** 을 클릭한 시점 사이에 스캔이 종단 상태(`succeeded` / `failed` / `cancelled`)에 도달했습니다. 큐를 새로고침하여 최신 상태를 확인하세요 — 별도 조치는 필요 없습니다.

### detected(first-party) 라이선스가 누락됨

**Detected** 라이선스는 scancode 에서 나오며 best-effort 입니다. 다음의 경우 누락될 수 있습니다.

- 워커 이미지에 scancode 가 미설치(스캔은 **declared** 라이선스만으로 성공 — 비치명적). `docker-compose -f docker-compose.yml logs worker | grep scancode_stage_skipped` 로 확인.
- first-party 트리가 `SCANCODE_MAX_FILES` 한도를 초과했거나, scancode 가 타임아웃되었거나, 결과가 너무 큼 — 모두 경고를 남기고 declared 전용으로 폴백.
- 해당 코드가 제외 디렉토리(`node_modules`, `vendor`, `.git`, `dist`, `build`, `out`, `target`, `.venv` 등) 안에 위치. 자원 가드로 인해 의도적으로 건너뜁니다 — [컴포넌트·라이선스 → declared vs. detected](./components-and-licenses.md#declared-vs-detected) 참고.

## 로드맵 (v2.x)

향후 릴리스에서 다룰 항목.

- 프로젝트 단위 **Scan** 트리거의 브랜치 오버라이드 필드 — 이후 v2.x 예정. (Source / Container 종류 선택 다이얼로그는 v2.1 에 출시되었습니다 — [스캔 트리거 → UI에서](#ui에서) 참고.)

## 함께 보기

- [컴포넌트·라이선스](./components-and-licenses.md)
- [취약점](./vulnerabilities.md)
- [GitHub Actions](../ci-integration/github-actions.md)
- [DT 커넥터](../admin-guide/dt-connector.md)
