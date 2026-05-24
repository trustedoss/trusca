# TrustedOSS Portal — 버그 리포트 (개발자용)

> **작성**: 외부 독립 QA | **대상**: v2.0.0 | **일자**: 2026-05-24
> **상위 문서**: `qa-report.md`(종합·출시 가부·검증 커버리지). 이 문서는 그 부록(수정 액션용 상세).
> **총 11건**: Critical 1(BUG-008) / High 1(BUG-010) / Medium 6 / Low 3
> 각 항목: 영역 · 심각도 · 재현 절차 · 기대/실제 · **근거** · 권장 조치

## 우선순위 (출시 전 조치 순서)
1. 🔴 **BUG-008** (Critical) — SCA 의존성 전 언어 미탐지 (blocker)
2. 🟠 **BUG-010** (High) — 조건부 라이선스 승인 자동 생성 누락 (컴플라이언스 blocker)
3. **BUG-005·002·001·009·006·007** (Medium) — 권한·i18n·a11y·보안헤더·SBOM·스캔UX
4. **BUG-003·004·011** (Low) — 중복호출·breadcrumb·예약 slug

## 버그 목록

### BUG-001: 취약점 상태 배지 색 대비가 WCAG AA 미달

- **영역**: Frontend / a11y
- **심각도**: Medium (WCAG 2.0/2.1 AA, 1.4.3 Contrast Minimum — axe impact: serious)
- **재현 절차**:
  1. frontend-admin으로 로그인 → `/projects` → `portal-web` 진입
  2. "취약점(Vulnerabilities)" 탭 선택
  3. CVE 행의 상태 배지(예: "New") 텍스트/배경 대비 확인
- **기대 동작**: 텍스트 대비 ≥ 4.5:1 (WCAG AA)
- **실제 동작**: `@axe-core` 검사에서 `color-contrast` 위반(serious). 대상 셀렉터 `[data-testid="vulnerability-status-badge-new"] > span`
- **근거**: `tests/trustedoss/specs/i18n-a11y/accessibility.spec.ts` (axe-core, wcag143)
- **추적**: 위 spec에서 `color-contrast`를 임시 격리(quarantine). 수정 시 격리 제거하여 회귀 방지.

### BUG-002: KO 로케일에서 에러·상태 메시지가 영어로 노출 (i18n 누락)

- **영역**: Frontend / i18n
- **심각도**: Medium (EN/KO 동시 출시 제품인데 KO 사용자에게 영어 노출, 다수 화면에서 반복)
- **재현 절차** (UI 언어 = 한국어 상태):
  1. (a) 존재하지 않는 프로젝트 접근(`/projects/<없는-uuid>`) → `"Project Not Found"` / `"project <id> not found"` (영어)
  2. (b) `portal-web` → 개요 탭 → 빌드 게이트 사유 `"2 critical CVEs detected; 1 forbidden-licensed component detected"` (영어)
- **기대 동작**: KO 로케일에서 한국어 메시지
- **실제 동작**: 백엔드 영어 메시지가 그대로 노출. 같은 화면 breadcrumb은 한국어(`"불러오는 중…"`)라 일관성 없음
- **권장 조치**: 백엔드는 메시지 코드/키 반환 + 프론트에서 i18n, 또는 프론트에 에러 메시지 매핑 테이블

### BUG-003: 동일 GET 요청 중복 호출 (성능 — 확인 필요)

- **영역**: Frontend / 성능
- **심각도**: Low (dev 환경 관찰 — **프로덕션 빌드 재확인 필요**)
- **재현 절차**: 프로젝트 상세 진입 시 `/v1/projects/{id}` 와 `/v1/projects/{id}/overview` 가 각각 2회 호출 (404 케이스에서 동일 요청 4건 관찰)
- **기대 동작**: 동일 요청 1회
- **실제 동작**: 2회 중복 — React StrictMode(dev) 또는 중복 fetch 의심
- **권장 조치**: 프로덕션 빌드에서 재확인. 실제 중복이면 React Query 키/effect 의존성 점검

### BUG-004: 404 확정 후 breadcrumb "불러오는 중…" 잔존

- **영역**: Frontend / UX
- **심각도**: Low
- **재현 절차**: 존재하지 않는 프로젝트 접근 → 본문은 "Project Not Found"인데 breadcrumb은 `"불러오는 중…"` 유지
- **기대 동작**: 404 확정 시 breadcrumb도 로딩 상태 해제
- **실제 동작**: 로딩 텍스트가 계속 표시됨 (로딩 상태 미해제)
- **근거**: `/projects/<없는-uuid>` breadcrumb 영역

### BUG-005: team_admin 계정에서 취약점 "억제(Suppress)" 버튼이 항상 비활성

- **영역**: Frontend / 권한·VEX
- **심각도**: Medium (문서-구현 정합성 / 핵심 트리아지 워크플로우)
- **재현 절차**:
  1. frontend-admin@demo(데모상 "팀 관리자")로 로그인 → portal-web → 취약점 → CVE-2024-99001 드로어
  2. New 상태: "억제" disabled (사유 35자 입력해도 변화 없음)
  3. "분석 시작"으로 Analyzing 전환: verdict 4종(악용가능/해당없음/오탐/수정됨)은 활성, **"억제"만 계속 disabled**
- **기대 동작**: 가이드(`vulnerabilities.md`) "Suppressed는 team_admin 이상만 가능" → team_admin이면 억제 가능해야
- **실제 동작**: 억제 버튼이 New·Analyzing 모두에서 항상 disabled
- **분석(셋 중 하나, 확인 필요)**: (a) frontend-admin이 해당 프로젝트의 team_admin이 아님 (b) 억제가 실제로는 super-admin 전용(가이드 "team_admin 이상" 표현 부정확) (c) 권한 판정 버그
- **근거**: VEX 드로어 (New/Analyzing 상태)
- **교차 확인 결과**:
  - developer(dev@demo)의 `/admin/users` 차단은 정상 ✅ (rbac.spec 통과)
  - super-admin(admin@demo) 억제 버튼 비교는 **보류** — admin@demo 로그인이 재현되지 않음(로그인 화면 유지). 레이트리밋(5회/분) 누적 또는 계정 이슈로 추정, 단독 확인 필요.
- **확정 사실**: 데모상 "팀 관리자"인 frontend-admin이 New·Analyzing 모두에서 억제 불가. 가이드의 "Suppressed는 team_admin 이상" 과 불일치 → 문서 수정 또는 권한 로직 수정 필요.
- **참고(테스트 데이터)**: 검증 중 CVE-2024-99001 상태가 New→Analyzing으로 변경됨(감사 로그 기록). 데모 데이터이며 New 직접 복원은 UI상 불가.

### 관찰 (버그 미확정): admin@demo(super-admin) 로그인 재현 실패
- 권한 테스트 중 admin@demo 로그인이 두 차례 로그인 화면에 머무름. 레이트리밋(IP당 5회/분) 누적 가능성이 높아 **버그로 단정하지 않음**. 충분한 간격을 두고 단독 로그인으로 재확인 필요.

### BUG-006: SBOM(CycloneDX JSON) 재내보내기 바이트 불안정

- **영역**: Backend / SBOM 무결성·컴플라이언스
- **심각도**: Medium (가이드가 보장하는 byte-stable 위반 → 해시 기반 검증·재현성 깨짐)
- **재현 절차**:
  1. portal-web → SBOM 탭 → "CycloneDX JSON 다운로드" 2회 연속 (동일 스캔)
  2. 두 파일 내용(바이트) 비교
- **기대 동작**: 가이드(`sbom.md`) "Byte-stable: 재내보내기 시 동일 바이트, serialNumber/documentNamespace 결정론적, purl 렉시콘 정렬"
- **실제 동작**: 두 다운로드의 내용이 불일치 (sbom-download.spec `SBOM-03` 실패). 4포맷 형식 마커 자체는 정상.
- **분석**: `serialNumber`(매 export UUID) 또는 export 타임스탬프가 호출마다 변동하는 것으로 추정 — 차이 지점 확인 필요
- **근거**: `tests/trustedoss/specs/critical-flow/sbom-download.spec.ts` SBOM-03
- **영향**: SBOM을 해시로 비교·아카이브·재현하는 컴플라이언스 워크플로우(Apache-2.0 §4(d) 등)에서 동일 스캔이 매번 다른 산출물로 보임

### BUG-007: 스캔 취소 후 진행 드로어가 "취소됨"으로 갱신되지 않음

- **영역**: Frontend / 실시간 동기화·UX
- **심각도**: Medium (취소했는데 "진행 중"으로 보여 사용자 혼란·중복 취소 유발 가능)
- **재현 절차**:
  1. portal-mobile 스캔(zip 업로드) 트리거 → 진행 드로어("스캔 진행 중", WebSocket %)
  2. "스캔 취소" → 확인 다이얼로그 "스캔 취소" 확정
  3. 드로어 관찰 (6초+ 대기)
- **기대 동작**: 취소 확정 후 드로어가 "취소됨" 상태로 갱신
- **실제 동작**: 드로어 progressbar **90%에서 고정**, "스캔 진행 중" 유지. 반면 전역 큐(`/scans` 전체)에는 **"취소됨"(32s)** 으로 정확히 기록됨 → 서버는 정상, **드로어 UI만 미갱신**
- **분석**: 취소 후 드로어가 WebSocket의 cancelled 상태를 수신/반영하지 못함 (큐 화면은 폴링으로 정상)
- **근거**: portal-mobile 스캔 취소 — 드로어 vs `/scans` 상태 불일치
- **긍정 확인(정상)**: SCAN-12(취소→cancelled), SCAN-15(확인 다이얼로그), SCAN-08(WS 진행률), SCAN-09(파이프라인 6단계), 그리고 "패널 닫아도 백그라운드 계속" 안내(SCAN-16 가이드 일치) 모두 정상

### BUG-008: SCA false negative — 소스 아카이브 스캔이 의존성을 전 언어에서 미탐지 ⚠️ 최우선(blocker)

> **🛠 메인테이너 부록 (2026-05-24, 런타임 검증 후 — 심각도 Critical→Medium 정정)**
> 외부 QA가 `docker exec` 차단으로 못 했던 cdxgen 런타임 검증을 워커에서 직접 수행한 결과,
> **이 "전 언어 전면 미탐지"는 cdxgen 버그가 아니라 QA 하네스(`scan-all-fixtures.js`)의 페이로드 누락 아티팩트**로 확인되었다.
> - **근본원인**: 하네스가 스캔을 `{kind:"source"}`로만 트리거하고 `metadata.source_type="upload"` + `archive_id`를 누락 → 백엔드 `_fetch_source`가 업로드 아카이브를 못 찾고 legacy no-git_url placeholder 경로로 빠져 **빈 워크스페이스**를 cdxgen에 넘김(워커 로그: cdxgen 매 실행 0.6초·SBOM 557바이트 = 빈 디렉토리 스캔).
> - **실제 UI는 정상**: `apps/frontend/src/hooks/useTriggerScan.ts:136`이 `metadata:{source_type:"upload", archive_id}`를 정확히 전송. "UI도 동일 경로(sourceArchiveApi.ts)" 주장은 부정확 — sourceArchiveApi는 업로드만, 스캔 트리거는 useTriggerScan이 담당.
> - **올바른 페이로드 재검증(end-to-end, DB scan_components 영속까지 succeeded)**: node 1(BD 1)·python-pip 5(BD 3)·maven 8(BD 3) — 전부 BD ground truth 충족. cdxgen·추출·영속·실제 UI 경로 모두 정상.
> - **남은 진짜 버그(Medium)**: 소스가 없는(`git_url` 없음 + 업로드 없음) 소스 스캔이 실패가 아니라 **조용히 `succeeded`(0개)** 로 끝남 = 진짜 "침묵 실패". 조치: `trigger_scan`에 loud-fail 가드 추가 + 하네스 `source_type=upload` 수정(완료).
> 아래 원문은 외부 QA의 기록으로 보존한다(당시 환경에서의 관찰은 정확했고, 검출 파이프라인이 "스캔 succeeded·컴포넌트 0"을 잡아낸 것 자체는 가치 있음 — 페이로드만 교정하면 그대로 회귀 방어 자산이 된다).

- **영역**: Backend / SCA 스캔 파이프라인 (cdxgen)
- **심각도**: **Critical** (SCA 포털의 존재 이유인 의존성 탐지가 **전 언어 생태계에서 전면 실패** — 컴포넌트 0이면 CVE·라이선스·승인·SBOM이 모두 빈 값)
- **재현 절차**:
  1. 의존성 매니페스트를 가진 fixture를 zip으로 업로드 스캔 (source-archive 업로드 → `POST /v1/projects/{id}/scans {kind:source}`)
  2. 스캔 `status=succeeded` + `error_message=null` 도달
  3. 컴포넌트 수 확인 → **전부 0**
- **기대 동작**: Black Duck ground truth(`summary.csv`)가 탐지한 컴포넌트 수 이상
- **실제 동작 (전수 — `compare-bd.js`, 21 fixture, 2026-05-24)**: **의존성 보유 15개 fixture 전부 componentCount=0 = false negative**. PASS 3건은 **모두 BD도 0인 케이스**(비교 로직 정상 작동 입증):

  | fixture | detector | BD bom_min | 우리 스캔 | 판정 |
  |---|---|---|---|---|
  | maven / maven-nested | MAVEN | 3 / 1 | 0 | FAIL |
  | gradle / gradle-kts / gradle-no-wrapper | GRADLE | 5 / 5 / 5 | 0 | FAIL |
  | node / multi-component | NPM | 1 / 1 | 0 | FAIL |
  | maven-node | MAVEN | 5 | 0 | FAIL |
  | python-pip / python-poetry | PIP / POETRY | 3 / 3 | 0 | FAIL |
  | go | GO_MOD | 1 | 0 | FAIL |
  | dotnet | NUGET | 2 | 0 | FAIL |
  | ruby | RUBYGEMS | 4 | 0 | FAIL |
  | rust | CARGO | 1 | 0 | FAIL |
  | php | PACKAGIST | 3 | 0 | FAIL |
  | **empty / e2e-deep-manifest / e2e-build-failure (대조군)** | none | **0** | 0 | **PASS** |

  → **15 FAIL / 3 PASS(전부 BD=0) / 3 SKIP**(gradle-android·node-yarn·iac는 BD도 bom_min=-1) / 1 NO_PROJECT(e2e-korean-dirs 스캔 누락). **BD가 1개 이상 탐지한 fixture는 예외 없이 우리 스캔 0.**
- **스크립트 오류 아님 (배제 완료)**: 우리 스캔 방식 = **실제 사용자(프론트엔드) 방식과 동일 경로**. 프론트 `apps/frontend/src/lib/sourceArchiveApi.ts:59`도 `form.append("upload", ...)`로 source-archive 업로드 후 동일 `kind:source` 스캔. → UI로 스캔해도 동일하게 0
- **근거**: `compare-bd.js` 전수 결과(14 FAIL/1 PASS/3 SKIP), `scan-result-*.json`(전부 succeeded·components=0), fixture에 실제 매니페스트 존재(maven pom.xml 의존성 2, python requirements.txt, go.mod 등), maven 스캔 상세 `error_message=null`(에러 없이 0)
- **영향**: 스캔이 "성공"으로 보이지만 컴포넌트 0 → 사용자는 "취약점·라이선스 이슈 없음"으로 오인. **SCA 제품의 핵심 기능이 조용히 무력화**. BUG-010(승인 자동 생성)·CVE 분석·SBOM이 모두 빈 결과의 연쇄 원인
- **검증 한계(정직성)**: 호스트 `docker exec` 차단으로 worker 컨테이너 내 cdxgen **런타임 실행**을 직접 확인하지 못함(설치는 Dockerfile.worker에서 확인). 현 환경(개발/데모 docker) 전수 재현이며, 동일 이미지가 프로덕션이므로 **출시 전 팀이 cdxgen 실제 실행/소스 추출 경로를 반드시 검증**해야 함. 미해결 시 Critical 확정
- **검출 경로**: 본 QA가 구축한 **Black Duck 비교 파이프라인**(`scan-all-fixtures.js`→`collect-scan-map.js`→`compare-bd.js`)이 전수 자동 검출 — 파이프라인 가치 입증 사례

### BUG-009: 프론트엔드 응답에 clickjacking 방어 헤더 부재

- **영역**: Frontend (nginx) / 보안
- **심각도**: Medium (clickjacking — SPA를 악성 사이트 iframe에 삽입 가능)
- **재현 절차**:
  1. `GET http://localhost:5173`(또는 프로덕션 frontend) 응답 헤더 확인
  2. `X-Frame-Options` 없음 + CSP `frame-ancestors` 없음
- **기대 동작**: `X-Frame-Options: SAMEORIGIN`(또는 `DENY`) 또는 CSP `frame-ancestors 'self'`
- **실제 동작**: `apps/frontend/nginx/default.conf`에 `add_header`가 `Cache-Control`·`Content-Type`만 — 보안 헤더 없음. **dev(Vite)·prod(nginx) 모두 누락**
- **근거**: `security-headers.spec.ts` SEC-03 FAIL + `apps/frontend/nginx/default.conf`
- **영향**: 악성 페이지가 포털을 iframe으로 감싸 로그인·컴포넌트 승인·스캔 트리거 등 민감 동작의 클릭을 탈취(clickjacking)
- **권장 조치**: nginx default.conf에 `add_header X-Frame-Options "SAMEORIGIN" always;` (또는 CSP frame-ancestors). 참고: 백엔드 `core/middleware.py`에 보안 헤더 로직이 있으나 frontend SPA는 nginx가 직접 서빙하므로 nginx 측 추가 필요

### BUG-010: 조건부 라이선스 컴포넌트의 승인(Pending) 자동 생성 누락 ⚠️ 컴플라이언스

- **영역**: 승인 워크플로우 / 컴플라이언스 자동화 (backend)
- **심각도**: High (조건부 라이선스가 법무 검토 큐에 자동 진입하지 못해 조용히 누락 — BUG-008과 동급의 silent gap)
- **재현 절차**:
  1. 조건부 라이선스(`license_category="conditional"`, 예: LGPL/MPL/EPL/CDDL) 컴포넌트가 존재하는 프로젝트 확인
     — 실측: 566개 프로젝트 중 `ci-epss`에 conditional 컴포넌트 **6건 존재**
  2. `GET /v1/approvals?status=pending` (super_admin) → **0건**, 전체 승인(`GET /v1/approvals`)도 **0건**
- **기대 동작** (가이드 `approvals.md`):
  - line 11: "Components carrying a **conditional license** (LGPL, MPL, EPL, CDDL) trigger an approval workflow."
  - line 26: "**Pending** | Auto, when a conditional-license component is first detected."
  - **line 50: "When the scan pipeline detects a new conditional-license component, a Pending request is created automatically. No manual action required."**
- **실제 동작**: 조건부 컴포넌트가 존재해도 Pending 승인이 생성되지 않음. 승인 워크플로우가 **수동 생성(POST /v1/approvals)** 으로만 진입 가능.
- **근거 (코드 레벨 — 확정적)**:
  - `create_approval()` 호출처는 **`api/v1/approvals.py:212`(수동 POST 엔드포인트) 단 1곳** — 스캔 경로에서 호출 없음
  - `tasks/scan_source.py`(스캔 파이프라인): 라이선스를 `conditional`로 분류(`_LICENSE_CATEGORY_DEFAULTS`)하지만 승인 생성 로직 전무. `finalize` 단계는 스테이지 마킹만
  - `tasks/` 전체에 `create_approval`/`ComponentApproval(` 호출 0건
  - `alembic/versions/0008_component_approvals.py`: 테이블·ENUM·유니크 인덱스만 생성, **승인 자동 생성 트리거 없음**
- **영향**: SCA 포털의 컴플라이언스 핵심 가치 — 조건부 라이선스의 법무 검토 자동 트리거 — 가 동작하지 않음. 빌드는 진행되므로(`build proceeds`) **조용히 검토가 누락**되어 라이선스 의무(예: LGPL 소스 공개) 위반 리스크가 사람 눈에 안 띈 채 출시될 수 있음
- **권장 조치**: 스캔 finalize 단계에서 `license_category == "conditional"` 컴포넌트에 대해 `create_approval()`(중복 방지는 기존 `ix_component_approvals_unique_open` 유니크 인덱스 활용) 호출 추가. 또는 가이드가 "수동 생성"을 의도한다면 문서를 실제 동작에 맞게 정정
- **검증 한계(정직성)**: 라이브 스캔으로 "조건부 탐지→승인 0" 직접 재현은 현 환경에서 전 fixture 스캔이 `components=0`(→ BUG-008 계열)이라 불가. 대신 ①코드 경로 부재(확정) ②조건부 컴포넌트 6건 실존 + 승인 0 ③가이드 명시로 확정

### BUG-011: 'organization' 예약 slug 거부 누락 (validator 주석-구현 불일치)

- **영역**: 입력 검증 / 스키마 (`apps/backend/schemas/scan.py`)
- **심각도**: **Low** (현재 기능 무해 · 미래 호환성/코드 품질). Phase 3+ org-wide 프로젝트 도입 시 slug 충돌 가능
- **재현 절차**:
  1. 프로젝트 생성 시 `slug: "organization"` (소문자/숫자/대시 패턴 만족) 전송
  2. → **HTTP 201 생성됨**
- **기대 동작**: validator 주석대로 거부(422). scan.py:133 주석: *"The validator below rejects 'organization' at the schema layer so the rejection lives next to the contract."*
- **실제 동작**: `_validate_slug`는 `_SLUG_PATTERN.match`(소문자/숫자/대시)만 검사하고 **'organization' 예약어 거부 로직이 없음**. 'organization'은 패턴을 통과하므로 그대로 생성됨
- **근거**: `scan.py:133`(주석) vs `_validate_slug` 구현(`p2-checks.js` P2-SLUG-RESERVED FAIL: 201)
- **영향**: 주석/설계 의도(org-wide 예약)와 구현 불일치. 현재는 무해하나 Phase 3+ org-wide 프로젝트 기능이 'organization' slug를 특수 취급하면 기존 데이터와 충돌
- **권장 조치**: `_validate_slug`에 예약어 집합(`{"organization", ...}`) 거부 추가, 또는 주석을 실제 동작에 맞게 정정
- **부수**: 이 검증으로 데모 백엔드에 `slug=organization, name=p2` 프로젝트 1건 생성됨 → `cleanup-qa-projects.js`로 정리 대상
