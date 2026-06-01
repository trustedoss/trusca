---
id: projects
title: 프로젝트
description: TrustedOSS Portal에서 프로젝트 등록·설정·아카이브 — 스캔·컴포넌트·취약점·의무사항을 묶는 단위입니다.
sidebar_label: 프로젝트
sidebar_position: 1
---

# 프로젝트

**프로젝트**는 포털이 인지하는 소스 추적 단위입니다. 스캔, 컴포넌트, 취약점, 라이선스 결과, 의무사항, 자동 생성된 `NOTICE` 파일을 보유합니다. 대부분의 워크플로우는 프로젝트 추가에서 시작합니다.

:::note 대상 독자
자체 서비스를 스캔하는 엔지니어와 팀 리드. 로그인 필요. 생성·아카이브는 프로젝트 소속 팀의 `developer` 이상, 가시성 변경은 `team_admin` 권한이 필요합니다.
:::

## 프로젝트 구성 요소

| 필드 | 설명 |
|---|---|
| **이름 (Name)** | 표시용 라벨(자유 텍스트). 팀 내에서 유일해야 합니다. |
| **설명 (Description)** | 선택. 프로젝트 목록과 Overview 탭에 노출되는 자유 텍스트 요약. |
| **Git URL** | 스캔 파이프라인이 클론할 git URL. HTTPS 지원. 사설 저장소는 URL에 자격증명을 포함해야 합니다 — [사설 저장소](#사설-저장소)를 보세요. |
| **기본 브랜치 (Default branch)** | 스캔 파이프라인이 체크아웃할 브랜치(기본값 `main`). 생성 후 **Project Settings**에서 수정. |
| **가시성 (Visibility)** | `team` (v0.10.0 시점에서 허용되는 유일한 값 — 소속 팀 멤버만 조회). 생성 시 자동 설정되며 PATCH로만 변경 가능. |
| **소속 팀 (Owning team)** | 프로젝트가 속한 팀. 생성 시 활성 팀으로 자동 설정. |

## 프로젝트 추가 — UI

사이드바의 **Projects** 항목은 활성 팀 범위의 프로젝트 목록을 보여줍니다 — 활성 팀에 속한 모든 프로젝트, 상태 배지, severity 카운트, 인라인 **Scan** 액션, 그리고 `n scans · m releases · last scan <상대 시간>` 형식의 프로젝트별 컴팩트 메타 행:

![/projects 목록 — 팀 범위의 표로 name, 마지막 스캔 상태 배지, severity 카운트, 행별 인라인 Scan 액션](/img/screenshots/user-projects-list.png)

<!-- 위 스크린샷은 #30 의 scan_count/release_count/last_scan_at 메타 행 추가 이전 — 머지 이후 재촬영 -->

메타 행 집계:

- **scans** — 프로젝트가 누적 실행한 스캔 총 개수(모든 상태, 아카이브된 실행 포함).
- **releases** — 프로젝트가 누적한 릴리스 스냅샷 수([릴리스](#릴리스-탭) 참고).
- **last scan** — 마지막 스캔이 종단 상태에 도달한 이후의 상대 시간. 첫 스캔이 완료되기 전까지는 `—`.

목록 엔드포인트가 세 필드를 한 번의 쿼리로 서버 측에서 집계하므로, 수백 개 프로젝트 포트폴리오에서도 행 렌더링 비용이 낮습니다.

1. 로그인.
2. 사이드바의 **Projects** 클릭.
3. 우측 상단 **New project** 클릭.
4. 폼 작성:
   - **이름** (필수)
   - **설명** (선택)
   - **Git URL** (소스 스캔에 필수)
5. **Create** 클릭.

   ![New project 폼 — name / description / Git URL 필드 — KO locale](/img/screenshots/user-projects-create-form-ko.png)

프로젝트의 **Overview** 탭으로 이동합니다. 여기서 첫 스캔을 실행할 수 있습니다 — [스캔](./scans.md) 참고.

![프로젝트 상세 — 리스크 게이지와 빠른 액션이 있는 Overview 탭](/img/screenshots/user-project-detail-overview.png)

기본 브랜치(`main`), 가시성(`team`), 소속 팀(활성 팀)은 서버에서 자동 설정되며 **Project Settings**에서 확인 가능합니다.

### 프로젝트 상세 탭 구성

상세 페이지의 탭은 왼쪽→오른쪽 순서로 다음과 같습니다.

| 탭 | 보여주는 내용 |
|---|---|
| **Overview** | 리스크 두 축(Security + License), [빌드 게이트 판정](#build-gate-verdict-overview-tab), **Project info** 카드(Git URL, default branch, 소속 팀, 생성 시각, 마지막 스캔 시각), 최근 스캔. |
| **Releases** | 종단 스캔별 프로젝트 스냅샷 — 스냅샷 목록, "View snapshot" 핀 액션, 릴리스 간 diff 진입점. [Releases 탭](#릴리스-탭) 참고. |
| **Components** | 스캔이 발견한 모든 컴포넌트. [컴포넌트·라이선스](./components-and-licenses.md) 참고. |
| **Vulnerabilities** | 열린/트리아지된 CVE 결과. [취약점](./vulnerabilities.md) 참고. |
| **Licenses** | 같은 데이터를 SPDX 식별자·티어 기준으로 본 뷰. |
| **Obligations** | 컴포넌트별 의무사항 + NOTICE 파일 생성. [컴포넌트·라이선스 → 의무사항](./components-and-licenses.md#의무사항) 참고. |
| **SBOM** | CycloneDX / SPDX 익스포트, byte-stable. [SBOM](./sbom.md) 참고. |
| **Reports** | NOTICE / SBOM / Vuln-PDF / VEX 생성 카드 **+** 프로젝트의 통합 다운로드·익스포트 이력. [Reports 탭](#reports-탭) 참고. |
| **Source** | 최근 성공한 스캔에서 fetch 된 first-party 소스 트리. 파일 단위 라이선스 결과 하이라이팅. 탭 재정렬로 **Reports** 와 **Remediation** 사이로 이동. |
| **Remediation** | 최근 스캔 기준 컴포넌트별 업그레이드 권고. 선택적 npm 자동 PR 플로 포함. |
| **Settings** | 프로젝트 메타데이터, 아카이브 액션, CI 연동 헬퍼. |

:::note 탭 순서가 변경되었습니다
**Source** 탭은 이전에 **Licenses** 바로 뒤에 있었으나, 데이터 출력 클러스터(SBOM / Reports / Source) 가 연속되도록 **Reports** 오른쪽으로 이동했습니다. 북마크와 `?tab=source` 딥링크는 슬러그가 동일하므로 계속 동작합니다.
:::

## 프로젝트 추가 — API

<!-- docs-uat: id=projects-api-create kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST https://trustedoss.example.com/v1/projects \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "checkout-service",
    "description": "Storefront checkout service",
    "git_url": "https://github.com/acme/checkout-service.git"
  }' | jq .
```

응답에 프로젝트 UUID가 포함됩니다 — GitHub Action의 `project-id` 입력값과 GitLab CI 변수에 사용하므로 보관하세요.

스키마는 알 수 없는 필드를 거부합니다(`extra="forbid"`). 생성 시 허용되는 필드는 `name`, `description`, `git_url` 뿐입니다. `default_branch`는 이후 `PATCH /v1/projects/{id}`로 설정합니다.

생성 페이로드에 `team_id` 는 **필요하지 않습니다** — 서버가 활성
팀에서 자동으로 도출합니다. 이 필드는 향후 멀티 팀 스코핑을 위해
예약되어 있으며, 그 전까지 생성 호출에서는 무시해도 됩니다.

## 가시성

- **`team`** (v0.10.0 기본값이자 유일하게 허용되는 값) — 소속 팀 멤버만 프로젝트·스캔·결과를 볼 수 있습니다.

가시성은 생성 시 자동으로 설정됩니다. PATCH는 현재 `team` 외의 값을 거부합니다. 모든 PATCH 호출에서 감사 로그가 행위자를 기록합니다.

`organization`(조직 전체 읽기) 가용 시점은 [로드맵](#로드맵) 참고.

## 아카이브

- **아카이브** — 프로젝트와 그 이력·스캔·결과는 유지하되 기본 목록에서 숨기고 새 스캔을 막습니다. 서비스가 종료되었지만 컴플라이언스 추적이 필요한 경우에 유용합니다.

`DELETE /v1/projects/{id}`는 soft-delete(아카이브)를 수행합니다. 영구 삭제 동작은 현재 노출되지 않으며, 감사 로그 항목은 어떤 경우에도 유지됩니다.

아카이브 동작은 **Project Settings → Archive**에 있으며, 사고 방지를 위해 인라인 확인 스트립을 사용합니다.

## 사설 저장소

소스 스캔은 워커 컨테이너 안에서 저장소를 클론합니다. 현재 릴리스에서 지원되는 인증 옵션:

- **HTTPS + Personal Access Token** — URL을 `https://<token>@github.com/acme/checkout-service.git` 형태로 설정. 토큰은 `git_url`의 일부로 저장되며, 읽기 엔드포인트가 평문으로 반환하지 않습니다.

:::caution v0.10.0 의 사설 저장소
현재 지원되는 자격증명 모델은 **git URL 에 PAT 를 임베드한 HTTPS**
(`https://<token>@github.com/acme/payment-service.git`) 뿐입니다.
PAT 는 프로젝트 행에 영구 저장됩니다(읽기 엔드포인트가 평문 PAT 를
절대 반환하지 않으며, `git_url` 은 감사 로그에서 마스킹됩니다).

함의:
- 유출된 DB 스냅샷은 임베드된 모든 PAT 를 함께 유출합니다.
  read-only scope 의 단기 PAT 를 사용하세요.
- SSH key 와 GitHub-App 설치는 로드맵 항목입니다;
  그때까지 적극적으로 회전하세요.
:::

SSH 배포 키는 [로드맵](#로드맵)을 보세요.

## 리스크 점수

Overview 탭은 이제 하나의 합산 점수 대신 **두 개의** 리스크 축을 게이지에 표시합니다. 두 가지 실패 모드를 독립적으로 읽을 수 있도록 분리했습니다.

- **Security risk (보안 리스크)** — 프로젝트의 열린 취약점 구성에 따라 결정됩니다. 밴드(Critical / High / Medium / Low / Info) 는 **가장 심각한** 열린 finding 이 정합니다. 밴드 내 점수는 `n / (n + 4)` 로 스케일(비포화 — finding 이 더 많아진다고 밴드를 한 단계 올리지 못함).
- **License risk (라이선스 리스크)** — 프로젝트의 라이선스 티어 구성에 따라 결정됩니다. **Forbidden** 라이선스가 밴드를 지배. **Conditional** 행은 밴드 내 점수를 올리지만 단독으로 `Critical` 로 승격하지 않습니다(이전의 "Conditional 컴포넌트 하나라도 있으면 Risk 100" 동작은  W1 에서 제거).

기존 단일 `risk_score` 필드는 빌드 게이트와 CI 연동의 하위 호환을 위해 API 에서 `max(security_axis, license_axis)` 로 계속 노출됩니다. UI 는 두 축 분해를 사용합니다.

두 축 모두 매 스캔 후, 그리고 매 CVE 재탐지 후 갱신됩니다. 절대적 SLA 가 아닌 포트폴리오 내 상대 지표로 읽으세요 — 프로젝트로 들어가면 축별 분해도가 보입니다.

:::note 옛 "단일 리스크 게이지" 스크린샷
 W1 이전에 찍은 스크린샷은 단일 게이지("Risk")를 보여줍니다. 두 축 카드가 이를 대체합니다. 옛 단일 점수와 새 두 축 중 어느 쪽도 단순 비교할 수 없으므로, 업그레이드 이후 포트폴리오 기준으로 재설정하세요.
:::

## 빌드 게이트 판정 (Overview 탭) {#build-gate-verdict-overview-tab}

**Overview** 탭은 리스크 게이지 옆에 **Build gate**(빌드 게이트) 카드를 표시합니다. CI 연동이 계산하는 것과 동일한 빌드 차단 판정을 노출하므로 — CI 로그를 열지 않고도 포털에서 게이트 결과를 확인할 수 있습니다. 이 카드는 프로젝트의 **최근 성공한 스캔**을 기준으로 평가합니다.

**빌드 게이트**(또는 **정책 게이트**)는 빌드에 critical CVE 나 금지 등급 라이선스가 있으면 0이 아닌 종료 코드를 반환하는 CI 차단 메커니즘입니다. 개념과 파이프라인 연동 방법은 [GitHub Actions → 출력](../ci-integration/github-actions.md#출력)과 [용어집 → 빌드 게이트](../reference/glossary.md#빌드-게이트)에 있으며, 이 카드는 동일한 판정을 UI에서 읽기 전용으로 보여 줍니다.

카드가 표시하는 항목:

| 요소 | 의미 |
|---|---|
| **Pass / Fail 배지** | 최근 성공한 스캔에 critical CVE 와 금지 라이선스가 없으면 `Pass`(녹색, 방패 체크), 그렇지 않으면 `Fail`(빨강, 방패 X). |
| **사유 (Reason)** | `Fail` 일 때 게이트를 트립시킨 원인을 한 줄로 설명. |
| **Critical CVEs** | 평가된 스캔의 미해결 critical 심각도 결과 수. 미해결 = 상태가 `not_affected`, `fixed`, `false_positive` 가 아닌 경우. |
| **Forbidden licenses** | 금지 등급 라이선스를 하나 이상 가진 고유 컴포넌트 수. |
| **`EPSS ≥ {threshold}`** | 운영자가 EPSS 게이트를 활성화한 경우(포털에 `GATE_EPSS_THRESHOLD` 설정)에만 표시. EPSS 점수가 임계값 이상인 미해결 결과 수. EPSS 게이트가 비활성(기본값)이면 숨겨집니다. [EPSS로 빌드 게이팅](../ci-integration/github-actions.md#epss로-빌드-게이팅-선택) 참고. |

:::note 스캔 없음 (No scan yet)
성공한 스캔이 한 번도 없는 프로젝트는 녹색 pass 대신 중립적인 **No scan yet** 상태를 표시합니다 — 평가할 대상이 없기 때문입니다. 스캔을 실행하면([스캔](./scans.md) 참고) 카드가 채워집니다.
:::

CVE — Common Vulnerabilities and Exposures, EPSS — Exploit Prediction Scoring System. 둘 다 [용어집](../reference/glossary.md)을 보세요.

이 카드는 읽기 전용입니다 — 판정을 반영하되 정책을 변경하지는 않습니다. 임계값(심각도 하한, EPSS)은 운영자·CI 측 설정입니다. [GitHub Actions](../ci-integration/github-actions.md)와 [`GATE_EPSS_THRESHOLD`](../reference/env-variables.md#빌드--정책-게이트)를 보세요.

## 프로젝트 정보 (Overview 탭) {#project-info-card}

Overview 탭의 리스크 게이지 옆에는 **Project info** 카드가 있습니다. 프로젝트의 식별 메타데이터를 한 블록에 모아 읽기 전용으로 보여줘서, 간단한 조회를 위해 **Settings** 까지 들어갈 필요가 없도록 합니다.

| 필드 | 출처 | 비고 |
|---|---|---|
| **Git URL** | 프로젝트 `git_url`. | 클릭해서 복사. PAT 가 임베드된 경우 토큰 세그먼트는 `***` 로 마스킹되어 표시됩니다. read 엔드포인트는 원본 값을 노출하지 않습니다. |
| **Default branch** | 프로젝트 `default_branch`. | **Settings** 에서 편집 가능. |
| **Owning team** | 프로젝트 `team`. | 뷰어가 `super_admin` 이면 `/admin/teams/{id}` 관리자 뷰로 링크. |
| **Created** | 프로젝트 `created_at`. | 호버 시 절대 시각, 표면에는 상대 시간. |
| **Last scan** | 프로젝트 `last_scan_at`(프로젝트 목록 메타 행과 동일 값). | 첫 스캔이 종단 상태에 도달하기 전까지는 `—`. |

카드의 데이터는 프로젝트 목록 메타 행이 노출하는 값과 동일하며, 상세 페이지 상단에 한 번 더 노출해 사용자가 레포 URL을 확인하기 위해 목록으로 돌아가지 않아도 됩니다.

## Releases 탭 {#릴리스-탭}

스캔이 `succeeded` 종단 상태에 도달할 때마다 포털은 **release snapshot** 을 기록합니다 — 컴포넌트 목록, 라이선스 티어 혼합, 취약점 결과, 스캔 id 를 포함한 시점 고정 immutable 뷰. 스캔 완료 시각으로 태깅됩니다. 프로젝트의 **Releases** 탭은 이 스냅샷들을 최신순으로 나열합니다.

| 컬럼 | 표시 내용 |
|---|---|
| **Snapshot** | 스캔 완료 시각 (`yyyy-mm-dd HH:MM`) + 상대 시간. |
| **Scan kind** | `source` 또는 `container`. |
| **Severity counts** | 스냅샷 시점의 Critical / High / Medium / Low. |
| **License mix** | 스냅샷 시점의 Allowed / Conditional / Forbidden 바. |
| **Actions** | **View components**(해당 스캔에 핀된 **Components** 탭으로 이동), **View snapshot**(`?scan=<id>` 를 핀하고 Overview 를 스냅샷 데이터로 reload). |

릴리스 행을 직접 클릭하면 핀된 스냅샷 상태로 **Components** 탭에 도달합니다. 핀은 URL 의 `?scan=<id>` 파라미터로 전파되어 reload 후에도 살아남고 동료에게 공유할 수 있습니다 — 프로젝트의 모든 탭(Components, Vulnerabilities, Licenses, …)이 핀을 풀기 전까지 핀된 스냅샷을 데이터 앵커로 읽습니다. 핀을 빵부스러기에서 해제하면 *최근 성공한* 스캔이 모든 곳의 데이터 앵커로 복귀합니다.

Releases 탭 툴바에서 진입하는 **Compare** 화면은 두 스냅샷 id 를 받아 그 사이에 추가/제거된 컴포넌트와 severity 를 표시합니다 — "릴리스 X 와 Y 사이에 무엇이 변했는가" 의 정식 diff 뷰입니다.

## Reports 탭 {#reports-탭}

**Reports** 탭은 프로젝트의 다운로드 가능한 산출물을 통합하는 단일 랜딩 페이지입니다.

- **NOTICE**, **SBOM**, **Vulnerability PDF**, **VEX** 네 가지 **generate card**. 각 카드는 딥링크입니다 — 카드의 액션 버튼을 클릭하면 실제 포맷 선택기와 다운로드 버튼이 있는 해당 도메인 탭(Obligations / SBOM / Vulnerabilities / Vulnerabilities) 으로 전환됩니다. 현재 핀된 `?scan=` 스냅샷 컨텍스트는 점프 전후로 보존됩니다.
- 우측 **export history (이력) 표** 의 컬럼: **When** (상대 + 절대 시각), **Who** (행위자 이메일 — 감사용으로 익명화 보존된 행은 `—`), **Type** (NOTICE / SBOM / Vulnerability PDF / VEX 중 하나), **Format** (정확한 포맷 문자열 — `cyclonedx-json`, `spdx-tv`, `openvex` 등), **Scan** (스캔 id 앞 8자), **Size** (humanize — 렌더러가 크기를 기록하지 않은 경우 `—`).
- 툴바의 **Type** 멀티셀렉트 필터와 Prev / Next 페이지네이션. 둘 다 URL 에 `?rpt_type=<type>` / `?rpt_page=<n>` 으로 미러링되므로 필터된 뷰는 reload 안전·링크 공유 가능합니다.

권한은 SBOM·PDF 익스포트와 동일한 자세입니다 — 팀에 `developer` 이상 멤버는 이력을 읽을 수 있고, 비멤버는 `404`(existence-hide) 를 받습니다. 이력 표는 **append-only** 입니다 — 편집·삭제·재실행 액션이 없습니다. 산출물을 다시 다운로드하려면 해당 generate card 를 클릭해 도메인 탭에서 재익스포트하세요.

:::note Reports 탭은 도메인 탭의 다운로드 UX 를 중복하지 않습니다
Generate card 는 Reports 탭 안에 생성 다이얼로그를 띄우지 않고 항상 도메인 탭(Obligations / SBOM / Vulnerabilities) 으로 딥링크합니다. 의도는 포맷별로 정식 UX 를 하나만 유지하고 두 다운로드 표면이 어긋나지 않도록 하는 것입니다. 탭의 부가가치는 모든 포맷에 걸친 **history** 뷰를 한 곳에서 본다는 점입니다.
:::

## 정상 동작 확인

프로젝트 생성 후:

<!-- docs-uat: id=projects-appears-idle kind=ui harness=projectCreateAppearsIdle(docs-uat-new-project) tier=nightly -->
1. **Projects**에 프로젝트가 **Idle**(스캔 없음) 상태로 표시됩니다.
<!-- docs-uat: id=projects-overview-zero kind=manual tier=manual -->
2. Overview 탭은 컴포넌트·취약점 모두 0을 보여줍니다.
<!-- docs-uat: id=projects-audit-create kind=manual tier=manual -->
3. 감사 로그(`/admin/audit`, super-admin 전용)에 본인의 `user_id`로 `target_table=projects&action=create`가 기록됩니다.

## 트러블슈팅

### "저장소 URL이 유효하지 않음"

마법사는 URL이 `http://` 또는 `https://`로 시작해야 합니다(HTTPS 강력 권장). v0.10.0 시점에서 `git@…`·`ssh://…` URL은 폼이 받지 않습니다. HTTPS 클론 URL을 사용하세요. 포털은 도달 가능성을 검증하지 **않으며** 그것은 스캔 시점에 일어납니다. 폼 제출에서 거부되면 오타를 다시 확인하세요.

### "이미 사용 중인 프로젝트 이름"

이름은 팀별로 유일합니다. 기존 프로젝트의 이름을 변경하거나 접미사를 추가하세요(`checkout-service-legacy`).

### 프로젝트 생성 시 Forbidden

소속 팀에서 본인의 역할이 `developer` 미만입니다. 팀 admin에게 적절한 역할로 초대를 요청하세요 — [사용자 및 팀](../admin-guide/users-and-teams.md).

## 로드맵

매뉴얼이 이전에 약속했으나 v0.10.0에 포함되지 않은 항목 — 향후 릴리스에서 다룹니다.

- 포트폴리오 그룹핑용 프로젝트 태그 — 예정.
- `organization`(조직 전체) 가시성 — 예정.
- **Project Settings**에서의 SSH 배포 키 생성 — 예정.
- 이름 입력 확인을 동반한 프로젝트 영구 삭제 — 설계 중. 현재는 soft-delete(아카이브)만 가능.
- 생성 마법사의 SSH(`git@…`, `ssh://…`) URL 수용 — 예정.

## 함께 보기

- [스캔](./scans.md) — 첫 스캔 실행
- [취약점](./vulnerabilities.md) — 결과 분류
- [컴포넌트·라이선스](./components-and-licenses.md) — 컴포넌트 목록 읽기
- [사용자 및 팀](../admin-guide/users-and-teams.md) — 역할 모델
