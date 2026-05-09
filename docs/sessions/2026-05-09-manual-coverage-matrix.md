# Manual coverage matrix — TrustedOSS Portal v2.0.0

> 작성일: 2026-05-09
> 대상: 사용자 매뉴얼 9 페이지 + 관리자 매뉴얼 6 페이지 (총 2,356 라인)
> 목적: 매뉴얼 walkthrough 자동화 비율 결정 + 후속 세션 (Phase 2~6) 우선순위
> 단일 진실 prompt: `docs/sessions/_next-session-prompt-manual-walkthrough.md`
> 페이지 인벤토리:
> - 사용자 매뉴얼: projects (131) + scans (171) + vulnerabilities (121) + components-and-licenses (133) + sbom (176) + approvals (123) + auth-and-profile (98) + notifications (102) + integrations (129) = 1,184 라인
> - 관리자 매뉴얼: users-and-teams (181) + dt-connector (211) + disk-and-health (159) + audit-log (163) + backup-and-restore (296) + api-keys (162) = 1,172 라인

## 분류 기준

- **A** — Playwright headed mode 로 자동 검증 가능 (UI 클릭 / 입력 / 네비게이션, 응답 검증). 가능하면 Phase 5 에서 정식 E2E 시나리오로 등재.
- **B** — 외부 통합 필요 (외부 OAuth 동의 화면, SMTP 수신함, GitHub Webhook 발신, sudo, 호스트 디스크, S3, gpg, cron / systemd, 외부 DT). 자동화 가능하나 별도 환경 / 자격증명 필요. Walkthrough 시 자동화 단계와 분리해서 사람이 직접 또는 fixture / mock 으로 처리.
- **C** — 시각 / Copy 검증만 (스크린샷 일치, 라벨 텍스트, 위치, color token). 자동화 가능하나 가치-대비-비용 낮음. 시각 회귀는 Phase 5 우선순위 X.
- **D** — 진정한 수동 (운영자 판단, 정책 결정, 외부 통보, 운영 장기 추세 분석). 매뉴얼 정확성만 검증.

## 단계 매핑 메모

- 단계 ID 는 페이지 ID + 섹션 인덱스 + 단계 인덱스 (예: `projects-3-2` = projects.md 의 `## Adding a project — UI` (섹션 3) 의 step 2).
- 매뉴얼이 내부적으로 이미 numbered list 인 경우 그 번호를 사용. `**Verify it worked**` / `## Troubleshooting` 항목은 별도 섹션 인덱스로 매김.
- 한 매뉴얼 단계가 여러 sub-action 을 포함하면 sub 단계로 분해 (예: `dt-connector-4-4a` ~ `4d` = OSV 8 ecosystem 활성화).
- E2E 매핑은 spec 의 `test(...)` 라인 번호 또는 시나리오 이름 기준. 한 단계가 여러 spec 으로 부분 가드되면 콤마 구분.
- ⚠ 마크 = 매뉴얼 자체에 의심 또는 명백한 drift 후보. 본 phase 에서 수정 X (Phase 4 에서 walkthrough 후 일괄 fix).

---

## 사용자 매뉴얼 (Developer 페르소나)

### user-guide/projects.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| projects-1-1 | "Anatomy of a project" 표 (Name / Repository URL / Default branch / Visibility / Owning team / Container image / Tags) 7 필드 정의 확인 | 매뉴얼 표가 New Project 폼 필드와 1:1 일치 | C | 없음 | 시각 / 라벨 검증. UI 폼 fields 와 매뉴얼 표 정합성 |
| projects-2-1 | Sign in | 로그인 성공, 사이드바 노출 | A | `auth.spec.ts:27` ("register → auto-login") | 다른 페이지에서도 공통 |
| projects-2-2 | 사이드바 **Projects** 클릭 | `/projects` 도달, 프로젝트 목록 표시 | A | `project_detail.spec.ts:104` (gotoProjects) | `scan_flow.spec.ts` 도 매번 사용 |
| projects-2-3 | 우측 상단 **New project** 클릭 | 새 프로젝트 폼 모달 / 페이지 열림 | A | 없음 | New Project 진입 가드 부재. Phase 5 권고 |
| projects-2-4a | Name (required) 입력 | input 수락 | A | 없음 | |
| projects-2-4b | Repository URL (required) 입력 | URL shape 검증 통과 | A | 없음 | adversarial input parametrize (memory) — `javascript:`, oversized 등 |
| projects-2-4c | Default branch — defaults to `main` 입력 | default value `main` 표시 | A | 없음 | |
| projects-2-4d | Visibility — defaults to `team_only` 토글 | default badge `team_only` 표시 | A | 없음 | |
| projects-2-4e | Container image (optional) 입력 | optional 표시 | A | 없음 | |
| projects-2-5 | **Create** 클릭 | 프로젝트 Overview 탭 도착, status `Idle`, components 0, vulns 0 | A | 부분 — `project_detail.spec.ts:95` (Overview 4 panels) | "lands on Overview" 단계는 가드 부재. Phase 5 권고 |
| projects-3-1 | API: `POST /api/v1/projects` curl with API Key | 201 응답, project UUID 반환 | A | 없음 | 새 spec 권고 — API 단독 시나리오 |
| projects-4-1 | Visibility 변경 = privileged action | audit log 에 actor + previous value 기록 | A | 없음 | audit 검증은 `admin_dt_scans_disk_audit_health.spec.ts` 의 audit harness 활용 가능 |
| projects-5-1 | Tags 추가 / 변경 | non-destructive, 스캔 차단 X | A | 없음 | |
| projects-6-1 | **Archive** 액션 | 프로젝트 hide from default lists, 새 스캔 disable, 기록 보존 | A | 없음 | Phase 5 권고 |
| projects-6-2 | **Delete** 액션 (typed-name confirmation modal) | 영구 삭제, audit-log 잔존 (UUID reference) | A | 없음 | adversarial input parametrize — wrong-name confirmation |
| projects-7-1 | Private repo HTTPS + PAT URL 등록 | token encrypted-at-rest, API 응답에 미노출 | A | 없음 | Phase 5 권고 (보안 회귀 가드) |
| projects-7-2 | Project Settings → Repository 에서 SSH deploy key 생성 | deploy key 표시 | A | 없음 | ⚠ — Project Settings 화면에서 deploy key 생성 UI 존재 여부 확인 필요 |
| projects-8-1 | Risk score 0–100 표시 (Critical/High/Medium/Low + 라이선스 mix + 스캔 경과시간 가중) | 스캔 후 갱신, CVE re-detection 후 갱신 | A | `project_detail.spec.ts:95` (overview-risk-card) | 정확한 score 계산식 검증은 unit test 영역 |
| projects-9-1 (Verify) | 프로젝트가 **Projects** 에 status **Idle** 으로 표시 | status badge `Idle` | A | 없음 | Phase 5 권고 |
| projects-9-2 (Verify) | Overview 탭이 components 0, vulnerabilities 0 표시 | 두 카운트가 0 | A | 부분 가드 | |
| projects-9-3 (Verify) | `/admin/audit` 에 `project.create` 기록 (with user_id) | audit row | A | 부분 가드 (audit harness) | super_admin 페르소나 필요 |
| projects-10-1 (Trbl) | "Repository URL is invalid" — wizard 가 URL shape 검증 (HTTPS / git@ / ssh) | reachability 는 검증 X | A | 없음 | adversarial input parametrize 권고 |
| projects-10-2 (Trbl) | "Project name already in use" | 동일 이름 reject | A | 없음 | |
| projects-10-3 (Trbl) | Forbidden 발생 시 role < `developer` | 403 response / 알림 | A | 없음 | RBAC 가드 — Phase 5 권고 |

**Subtotal**: 24 단계 / A 21 / B 0 / C 1 / D 0 / 신규 spec 0 (모두 unknown 항목은 ⚠ 1)

### user-guide/scans.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| scans-1-1 | scan kinds 표 (`source` cdxgen→ORT→DT, `container` Trivy) | 두 종류 trigger 가능 | C | 없음 | 라벨 / 표 검증 |
| scans-2-1-1 | 프로젝트 열기 | project detail 도달 | A | `project_detail.spec.ts:95` | |
| scans-2-1-2 | 우측 상단 **Scan** 클릭 | scan kind 선택 dialog | A | `scan_flow.spec.ts:104` (clickTriggerScan) | ⚠ — 단, scan_flow spec 의 4 시나리오 모두 `KNOWN_PAGE_SIZE_BUG=false` 가드로 fixme 처리 — 본 PR 시점 이미 fix 됐는지 확인 필요 |
| scans-2-1-3 | **Source** 또는 **Container** 선택 | dialog 의 두 옵션 노출 | A | 없음 | container 옵션 가드 부재 |
| scans-2-1-4 | branch override (default = project default branch) | 입력 수락 | A | 없음 | |
| scans-2-1-5 | **Start scan** 클릭 | 스캔 시작, live progress view + WebSocket 전환 | A | `scan_flow.spec.ts:111` (expectScanProgress + percent) | |
| scans-2-1-6 | 탭 닫고 다시 열기 → progress 재연결 | 최신 stage 재출력 | A | `scan_flow.spec.ts:139` (no `Reconnecting…`) | reconnect 시나리오 자체는 부분 가드 |
| scans-2-2-1 | API: `POST /api/v1/projects/{id}/scans` with `{"kind":"source"}` | 201 + scan UUID | A | 없음 | 새 spec 권고 |
| scans-2-2-2 | API: `GET /api/v1/scans/{id}` 폴링 → status | status 진행 | A | 없음 | |
| scans-2-3 | CI 경로 (GitHub Action / GitLab CI / Jenkinsfile) — wraps API + build gate | exit code 1 on Critical CVE | B | 없음 | 외부 CI runner 필요. fixture 로 부분 자동화 가능 |
| scans-3-1 | Lifecycle: queued → running → succeeded / failed / cancelled | 5 상태 모두 trigger 가능 | A | 부분 — succeeded만 `admin_dt_scans_disk_audit_health.spec.ts:113` (drawer status badge regex 5가지) | failed / cancelled E2E 부재 |
| scans-3-2-1 | source pipeline stage `Bootstrapping` | live update | A | 없음 | mock backend 에서 stage 검증 가능 |
| scans-3-2-2 | source pipeline stage `Fetching source` | live update | A | 없음 | |
| scans-3-2-3 | source pipeline stage `Detecting components` (cdxgen) | live update | A | 없음 | |
| scans-3-2-4 | source pipeline stage `Analyzing licenses` (ORT) | live update | A | 없음 | |
| scans-3-2-5 | source pipeline stage `Resolving vulnerabilities` (DT) | live update | A | 없음 | |
| scans-3-2-6 | source pipeline stage `Persisting` | live update | A | 없음 | |
| scans-3-3 | DT unavailable → circuit breaker OPEN → cache fallback → scan = succeeded with warning | warning surface in UI | A | 없음 | Phase 5 우선 — DT outage simulation 필요 |
| scans-4-1 | duration 표 (S/M/L source/container) | 정보성 가이드 | D | 없음 | |
| scans-5-1 | 사이드바 **Scans** → 전역 스캔 큐 | org-wide running + queued list | A | `admin_dt_scans_disk_audit_health.spec.ts:99` (gotoAdminScans) | super_admin 가드. Developer view 검증 부재 |
| scans-5-2 | 필터 (status / kind / project / team) | URL 갱신, 결과 narrow | A | `admin_dt_scans_disk_audit_health.spec.ts:104` (selectTab "all") | 부분 |
| scans-5-3 | Cancel any team's scan (super-admin: any) | scan 취소, audit 기록 | A | 없음 | Phase 5 권고 |
| scans-6-1 | WebSocket 메시지 shape `{scan_id, stage, progress, message, ts}` | json 메시지 schema 일치 | A | 없음 | 새 spec — WS payload contract |
| scans-6-2 | reconnect with exponential backoff | 네트워크 drop 후 재연결 | A | 부분 — `scan_flow.spec.ts:139` (no reconnect notice) | drop 시뮬레이션 부재 |
| scans-7-1 (Verify) | 프로젝트 status switches to **Completed** | badge `Completed` | A | 없음 | ⚠ — UI 의 status 라벨이 `succeeded` 인지 `Completed` 인지 검증 필요 (lifecycle 표는 `succeeded`) |
| scans-7-2 (Verify) | Components count > 0 | 카운트 노출 | A | `project_detail.spec.ts:138` (expectComponentsTabReady) | |
| scans-7-3 (Verify) | Vulnerabilities count visible (may be 0) | 카운트 노출 | A | `vulnerabilities.spec.ts:78` (S1 list render) | |
| scans-7-4 (Verify) | Last scan timestamp Overview 반영 | timestamp = "now" | A | 없음 | |
| scans-7-5 (Verify) | audit log `scan.create` + `scan.update` 기록 | audit rows | A | 부분 — audit harness | super_admin 가드 |
| scans-8-1 (Trbl) | scan stuck in `Queued` — worker 점검 (`docker-compose ps worker`) | worker unhealthy 시 restart | B | 없음 | docker-compose 호출 — sudo 또는 컨테이너 권한 |
| scans-8-2 (Trbl) | `git clone` 실패 — repo URL / 권한 / 프록시 점검 | 명확한 error_detail | B | 없음 | 외부 git 호스트 |
| scans-8-3 (Trbl) | 스캔 끝났는데 vulns 누락 — `/admin/dt` breaker = OPEN | cache 사용 알림 | A | 없음 | Phase 5 권고 |
| scans-8-4 (Trbl) | "DT unreachable" 경고 — same as 8-3 | informational | A | 없음 | |

**Subtotal**: 33 단계 / A 28 / B 3 / C 1 / D 1 / ⚠ 2 (scans-2-1-2, scans-7-1)

### user-guide/vulnerabilities.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| vulns-1-1 | Severity 표 (Critical/High/Medium/Low/Info + color tokens + CVSS + build gate effect) | 5 단계 표시 | C | 없음 | color token 시각 검증 |
| vulns-1-2 | 기본 build gate fail = Critical only, project owner 가 High 로 lower 가능 | per-project setting | A | 없음 | Phase 5 권고 — gate config 화면 |
| vulns-2-1 | VEX 7-state 표 (New / Analyzing / Exploitable / Not affected / False positive / Suppressed / Fixed) | 7 상태 모두 transition 가능 | A | 부분 — `vulnerabilities.spec.ts:172` (S4: new→analyzing) + S5 (suppressed denial) | 나머지 5 transition 가드 부재 |
| vulns-2-2 | New / Analyzing 상태 외부로 나갈 때 justification ≥ 10 chars 필수 | 10 chars 미만 시 error | A | 부분 — `vulnerabilities.spec.ts:197` (setVulnerabilityStatus with text) | adversarial parametrize — empty / 9-char / oversized / CRLF |
| vulns-3-1 | 컬럼 (CVE / Component / Severity / State / Discovered / Last seen) | 6 컬럼 | A | 부분 — `vulnerabilities.spec.ts:78` (S1 list) | 컬럼 schema 검증 부재 |
| vulns-3-2 | filter bar — severity / state / component / discovered range | URL 갱신 | A | `vulnerabilities.spec.ts:97` (S2 sev+status filter + URL persist) | discovered range 미가드 |
| vulns-3-3 | 행 클릭 → drawer 열림 | 우측 슬라이드 | A | `vulnerabilities.spec.ts:130` (S3 drawer detail render) | |
| vulns-4-1 | Drawer: CVE summary / Affected versions / References / Fix availability / Project history / Triage | 6 섹션 | A | 부분 — meta + summary + analysis + history (4) | affected versions / fix availability 가드 부재 |
| vulns-4-2 | VEX 상태 dropdown + justification box + Save (developer or higher) | RBAC 가드 | A | `vulnerabilities.spec.ts:211` (S5 developer cannot suppress) | |
| vulns-5-1 | Re-detection — DT 가 NVD/OSV/GHA ingest → 자동 재상관 | 새 finding 자동 출현 | B | 없음 | DT NVD ingest 필요 (외부 통합) |
| vulns-5-2 | Dashboard 에 "CVE re-detection" banner | feeds processed / new findings / timestamp | A | 없음 | Phase 5 권고 |
| vulns-5-3 | Notify on new CVE trigger 활성 시 → email / Slack / Teams | 알림 발송 | B | 없음 | SMTP / Slack / Teams 외부 |
| vulns-6-1 | Suppression vs Not affected vs Fixed 정의 | 사용 시점 가이드 | D | 없음 | 정책 가이드 |
| vulns-7-1 (Verify) | 상태 badge 즉시 갱신 | UI 즉시 반영 | A | 부분 — `vulnerabilities.spec.ts:208` (history grows) | |
| vulns-7-2 (Verify) | audit log 에 `vuln_finding.update` (previous_state, new_state, justification) | audit row | A | 없음 | super_admin audit page |
| vulns-7-3 (Verify) | excluded findings → risk score 계산 미포함 | score 갱신 | A | 없음 | 새 spec 권고 |
| vulns-7-4 (Verify) | excluded findings → 다음 scan build gate 제외 | gate exit code 0 | B | 없음 | CI runner 필요 |
| vulns-8-1 (Trbl) | suppression 후 finding 재출현 — scan-level vs project-level 미스매치 | 메타 검증 권장 | A | 없음 | drift 가능 — 스코프 검증 필요 |
| vulns-8-2 (Trbl) | severity 가 scan 간 변경 — drawer 가 두 값 표시 | drawer 의 prev/curr severity | A | 없음 | Phase 5 권고 |
| vulns-8-3 (Trbl) | CVE 가 report 에 누락 — purl 미스매치 / DT outage / DT 미지원 ecosystem | 3 가지 원인 가이드 | D | 없음 | |

**Subtotal**: 20 단계 / A 14 / B 3 / C 1 / D 2

### user-guide/components-and-licenses.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| comps-1-1 | 컬럼 (Name / Version / Type / Concluded license / Classification / Findings) | 6 컬럼 | A | 부분 — `project_detail.spec.ts:138` (expectComponentsTabReady) | 컬럼 schema 가드 부재 |
| comps-1-2 | virtualized table | 1000+ rows 스크롤 smooth | A | `project_detail.spec.ts:125` (S2: 250 rows + scroll → endReached) | |
| comps-2-1 | filter — Classification multi-select (Allowed/Conditional/Forbidden/Unknown) | URL 갱신 | A | `licenses.spec.ts:104` (S2 category multi-filter + URL persist) | 단, license tab 의 category. components tab 의 classification filter 는 다를 수 있음 — drift 후보 |
| comps-2-2 | filter — License (exact SPDX) | filter 적용 | A | 없음 | adversarial parametrize — `LicenseRef-`, oversized, special chars |
| comps-2-3 | filter — Has open CVE 토글 | filter 적용 | A | 없음 | |
| comps-2-4 | filter — Search (substring `name@version`) | URL `?search=…` 갱신 | A | `project_detail.spec.ts:184` (S4 search + URL mirror) | |
| comps-3-1 | drawer: Identity (purl / homepage / repo URL) | 정보 표시 | A | `project_detail.spec.ts:155` (S3 drawer meta + vulns + raw) | meta 일부만 가드 |
| comps-3-2 | drawer: All license findings (declared / detected / concluded + 출처 파일) | 3 카테고리 | A | 없음 | Phase 5 권고 |
| comps-3-3 | drawer: Obligations | 의무 list | A | `obligations.spec.ts` (전체) | obligations tab 별도, drawer 안 의 obligations 는 미가드 |
| comps-3-4 | drawer: CVEs (open + resolved, deep-link) | linked rows | A | `project_detail.spec.ts:175` (component-drawer-vulns) | |
| comps-3-5 | drawer: Approval status (Pending/Under Review/Approved/Rejected) | 상태 badge | A | 없음 | Phase 5 권고 |
| comps-3-6 | drawer: Override concluded license (team_admin 만, 사유 audit log) | RBAC + audit | A | 없음 | Phase 5 권고 — 보안 회귀 가드 |
| comps-4-1 | License classification 표 (Allowed / Conditional / Forbidden / Unknown) | 3 + 1 tier | A | `licenses.spec.ts:96` (S1 4 legend buckets present) | |
| comps-4-2 | rule set tunable (`ort/rules.kts`) | 수정 후 재스캔 시 반영 | B | 없음 | host file edit + 재스캔 |
| comps-5-1 | Declared vs Detected vs Concluded 정의 | 3 신뢰 레벨 | C | 없음 | 정의 검증 |
| comps-6-1 | Obligations 7 종 (Attribution / NOTICE / Source / Copyleft / Modifications / Dynamic linking / No endorsement) | 7 종 정확 | A | `obligations.spec.ts:78` (S1 distribution chips) | 7 종 정확 매핑은 글로서리 의존 |
| comps-6-2 | Obligations 탭 → Generate NOTICE 다운로드 | NOTICE.txt 다운로드 | A | `obligations.spec.ts:161` (S4 NOTICE download with filename + body) | |
| comps-7-1 | SPDX expressions — 단순 / OR / WITH / LicenseRef-* 처리 | hover 시 SPDX URL 노출 | A | 없음 | Phase 5 권고 — SPDX 파싱 회귀 가드 |
| comps-8-1 (Verify) | Component count ≈ lockfile pinned 갯수 | 일치 | A | `project_detail.spec.ts:146` (총 count assertion) | |
| comps-8-2 (Verify) | Overview 분류 도넛 100% | sum = 100% | A | 없음 | Phase 5 권고 |
| comps-8-3 (Verify) | Forbidden license 컴포넌트 빨간 강조 + CTA approvals queue | 시각 + 링크 | C | 없음 | 시각 검증 |
| comps-9-1 (Trbl) | 다수 컴포넌트 `Unknown` license — 메타 부재 / 미인식 / 소스 fetch 실패 | 3 가지 원인 | D | 없음 | |
| comps-9-2 (Trbl) | 분류 결과 잘못 — `ort/rules.kts` 편집 후 worker 재시작 + 재스캔, 또는 drawer 에서 override | 정정 가능 | B | 없음 | host file edit |
| comps-9-3 (Trbl) | Lockfile 미인식 — repo root or one level | drift 가능 | D | 없음 | |

**Subtotal**: 24 단계 / A 16 / B 3 / C 2 / D 3

### user-guide/sbom.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| sbom-1-1 | 4 포맷 표 (CycloneDX 1.6 JSON/XML, SPDX 2.3 JSON/Tag-Value) + MIME | dropdown 4 옵션 | A | 없음 | Phase 5 권고 |
| sbom-2-1 | byte-stable output — 재export 동일 bytes (sort by purl, alphabetic license, deterministic serialNumber, no body timestamp) | sha256sum 일치 | A | 없음 | Phase 5 강력 권고 — byte-stability 회귀 가드 |
| sbom-3-1 | UI: 프로젝트 → SBOM 탭 → 포맷 dropdown → Download | 파일 다운로드, 이름 `<project>-<scan-iso>.sbom.<ext>` | A | 없음 | 새 spec — 파일 download 가드 |
| sbom-4-1 | API: CycloneDX JSON `GET /api/v1/projects/{id}/sbom?format=cyclonedx-json` | curl 200, 파일 | A | 없음 | adversarial parametrize — invalid format query |
| sbom-4-2 | API: SPDX JSON 동일 | 200, 파일 | A | 없음 | |
| sbom-4-3 | API: CycloneDX XML / SPDX Tag-Value | 모두 지원 | A | 없음 | |
| sbom-4-4 | `?scan_id=<uuid>` 로 특정 scan 고정 | 해당 scan 의 SBOM | A | 없음 | adversarial parametrize — invalid uuid |
| sbom-5-1 | NOTICE 파일 — 헤더 + per-component (name/version/license/copyright/license URL) + grouped by license | 모든 필드 포함 | A | `obligations.spec.ts:161` (filename pattern + body contains project name + SPDX prefix) | grouping by license 가드 부재 |
| sbom-5-2 | UI: Project → Obligations → Download NOTICE | 다운로드 | A | `obligations.spec.ts:161` | |
| sbom-5-3 | API: `GET /api/v1/projects/{id}/notice` | curl 200 | A | 없음 | |
| sbom-5-4 | NOTICE byte-stable | 동일 hash | A | 없음 | Phase 5 권고 |
| sbom-6-1 | Excel 보고서 — Components Excel (행 = 컴포넌트, 컬럼 = name/version/type/license/classification/CVE count) | xlsx 다운로드 | A | 없음 | Phase 5 권고 |
| sbom-6-2 | Excel 보고서 — Vulnerabilities Excel | 행 = finding | A | 없음 | |
| sbom-6-3 | Compliance PDF — risk-score / classification / top-10 / obligations / NOTICE preview | pdf 다운로드 | A | 없음 | Phase 5 권고 |
| sbom-6-4 | UI: Project → Reports menu | 메뉴 노출 | A | 없음 | ⚠ — Reports 메뉴가 실제 어떤 위치에 있는지 검증 필요 (top-right of any tab 라고만 기술) |
| sbom-6-5 | API: 3 종 reports curl | 200 + 파일 | A | 없음 | |
| sbom-7-1 | VEX export — CycloneDX 가 VEX 포함, SPDX 는 분리 | `analysis.state` 매핑 표 7 행 | A | 없음 | Phase 5 권고 — CycloneDX validate |
| sbom-8-1 (Verify) | `cyclonedx validate` 통과 | exit 0 | B | 없음 | 외부 cli 필요 (cyclonedx-cli) |
| sbom-8-2 (Verify) | `pyspdxtools` 통과 | exit 0 | B | 없음 | 외부 cli 필요 |
| sbom-8-3 (Verify) | sha256sum 동일 (재다운로드) | 동일 | A | 없음 | Phase 5 강력 권고 |
| sbom-9-1 (Trbl) | `404` /sbom — successful scan 없음 | 명확한 에러 | A | 없음 | |
| sbom-9-2 (Trbl) | Excel 비-ASCII 깨짐 — UTF-8 BOM, Numbers / iconv | 가이드 | D | 없음 | |
| sbom-9-3 (Trbl) | NOTICE 일부 컴포넌트 copyrights 누락 — drawer override | 정정 | A | 없음 | Phase 5 권고 |

**Subtotal**: 23 단계 / A 19 / B 2 / C 0 / D 1 / ⚠ 1 (sbom-6-4)

### user-guide/approvals.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| approvals-1-1 | 4 state 표 (Pending / Under Review / Approved / Rejected + setter + meaning) | state machine 정확 | C | 없음 | |
| approvals-2-1 | 사이드바 → Approvals → filter (state/project/license/component/requested-by) | filter 동작 | A | 없음 | Phase 5 권고 |
| approvals-2-2 | 행 표시 — 컴포넌트 / license / 영향 프로젝트 / requested ts / reviewer / justification | 6 필드 | A | 없음 | |
| approvals-3-1 | conditional license 컴포넌트 detect 시 자동 Pending 생성 | scan 후 자동 row | A | 없음 | Phase 5 권고 — conditional license seed 필요 |
| approvals-3-2 | 사이드바 → Approvals → New request → Project / purl / Justification | 수동 request 가능 | A | 없음 | adversarial parametrize — invalid purl |
| approvals-4-1 | 행 열림 → drawer | 우측 슬라이드 | A | 없음 | |
| approvals-4-2 | **Claim** 클릭 → 상태 Under Review + reviewer = me | 상태 전이 | A | 없음 | Phase 5 권고 |
| approvals-4-3 | **Approve** / **Reject** + justification (≥10 chars) | 두 verb 가능 | A | 없음 | adversarial parametrize — short / empty / oversized justification |
| approvals-4-4 | disposition → verdict lock + audit log + risk score 갱신 + notification | 4 효과 | A | 없음 | |
| approvals-5-1 | Bulk: team_admin+ 가 multi-select + bulk verdict (justification 공유) | 가능 | A | 없음 | RBAC 검증 필요 |
| approvals-6-1 | Cross-project: 같은 컴포넌트 → 프로젝트별 별도 Pending (자동 propagate X) | 분리 row | A | 없음 | |
| approvals-7-1 | external Jira 연동 — webhook trigger | outbound HTTP POST | B | 없음 | 외부 Jira automation 필요 |
| approvals-8-1 (Verify) | state badge 즉시 갱신 | UI 반영 | A | 없음 | |
| approvals-8-2 (Verify) | 다음 scan 이 verdict 반영 (Rejected → forbidden) | gate 차단 | A | 없음 | Phase 5 권고 |
| approvals-8-3 (Verify) | audit log `approval.update` (previous_state, new_state, justification) | audit row | A | 없음 | |
| approvals-8-4 (Verify) | 원 requester → 알림 | notification 발송 | B | 없음 | SMTP / Slack |
| approvals-9-1 (Trbl) | 큐 비었지만 conditional license 존재 — 이미 disposed, 필터 All 로 | 기본 필터 = Pending+UR | A | 없음 | |
| approvals-9-2 (Trbl) | claim 불가 — RBAC 부족 | 403 | A | 없음 | RBAC 가드 |
| approvals-9-3 (Trbl) | verdict 가 다음 scan 반영 X — 이미 in-flight scan 영향 X | 새 scan 필요 | D | 없음 | 정책 안내 |

**Subtotal**: 19 단계 / A 16 / B 2 / C 1 / D 0 (Phase 5 우선순위 매우 높음 — 전체가 가드 부재)

### user-guide/auth-and-profile.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| auth-1-1 | `/login` 진입 | login form 노출 | A | `auth.spec.ts:50` (gotoLogin) | |
| auth-1-2 | email + password 입력 | 입력 수락 | A | `auth.spec.ts:51` | |
| auth-1-3 | submit | 성공 시 토큰 발급 + redirect | A | `auth.spec.ts:27` (register→auto-login) | |
| auth-1-4 | bcrypt cost 12 + access 30min + refresh 7d (rotation + reuse-detection + HttpOnly+Secure+SameSite=Lax) + 로그인 5/min/IP rate limit (429+Retry-After) | 보안 정책 정확 | A | 부분 — `auth.spec.ts:43` (bad creds inline alert) | refresh rotation / rate limit 가드 부재. memory `feedback_adversarial_input_parametrize` 적용 권고 |
| auth-1-5 | "Invalid email or password" 메시지 — anti-enumeration | 일반 메시지 (구체 X) | A | `auth.spec.ts:55` (expectAlert) | |
| auth-2-1 | `/login` → Forgot password? → `/forgot-password` | 페이지 도달 | A | `auth.spec.ts:142` (gotoForgotPassword) | |
| auth-2-2 | email 입력 + submit | 항상 204 (anti-enum) | A | `auth.spec.ts:147` (forgot-success visible regardless) | |
| auth-2-3 | reset link 24h valid, one-use | 만료 / 재사용 시 invalid | A | `auth.spec.ts:158` (reset-password without token → invalid-link) | one-use / 24h expiry 동작 가드 부재 |
| auth-3-1 | reset 링크 → `/reset-password?token=…` | 페이지 도달 | A | `auth.spec.ts:158` | |
| auth-3-2 | 새 password (≥12 chars, breach dict 차단) + 확인 | 검증 통과 | A | 없음 | adversarial parametrize 권고 — short/blank/breach |
| auth-3-3 | submit → `/login` redirect, refresh tokens 모두 revoke | 다른 세션 강제 재인증 | A | 없음 | Phase 5 권고 |
| auth-3-4 | token 만료 / 사용됨 → error + `/forgot-password` link | 명확한 fallback | A | 부분 — `auth.spec.ts:163` (reset-forgot-link visible) | |
| auth-4-1 | OAuth: GitHub / Google 버튼 (configured 시) | 버튼 노출 | A | `auth.spec.ts:187` (login-oauth-github + google visible) | |
| auth-4-2 | Continue with provider → consent → redirect back → signed in | 흐름 완결 | B | 없음 | 외부 provider consent 화면 — 진정한 외부 통합 |
| auth-4-3 | first-time OAuth: provider verified email 으로 자동 account + 개인 team | 자동 provision | B | 없음 | 외부 provider 필요 |
| auth-4-4 | subsequent: `(provider, provider_user_id)` lookup. email 으로 X (anti-takeover) | identity 매칭 | B | 없음 | 외부 provider |
| auth-4-5 | OAuth 7 error codes (denial / scope / expired state / repeated state / collision / suspended / 5xx) | 모두 i18n 매핑 | A | 부분 — `auth.spec.ts:172` (oauth_denied 만) | 6개 미가드 — Phase 5 우선 |
| auth-5-1 | `/profile` — Password / GitHub / Google 정체성 list | 행 단위 표시 | A | 없음 | Phase 5 강력 권고 — 전체 신규 페이지 |
| auth-5-2 | Unlink 버튼 — 마지막 sign-in method 시 409 + alert | 잠금 보호 | A | 없음 | 보안 회귀 가드 — 새 spec 필수 |
| auth-5-3 | linking 새 provider — sign out → 새 provider sign in → 자동 attach (verified email match) | 자동 link | B | 없음 | 외부 provider |
| auth-6-1 (Verify) | password sign-in 후 헤더 avatar = initials, navbar = active team | UI 갱신 | A | 부분 — `auth.spec.ts:40` (app-sidebar visible) | avatar / team 명시 가드 부재 |
| auth-6-2 (Verify) | OAuth sign-in 후 `/profile` 에 provider list | `/profile` 검증 | B | 없음 | 외부 provider |
| auth-6-3 (Verify) | unlink 후 row 사라짐 + 마지막 row Unlink 비활성 | UI 즉시 반영 | A | 없음 | 새 spec 권고 |

**Subtotal**: 23 단계 / A 16 / B 7 / C 0 / D 0 (Phase 5 강력 우선 — `/profile` 전체 부재)

### user-guide/notifications.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| notif-1-1 | 헤더 bell — unread 0 / 1-99 / 99+ 캡 | badge 정확 | A | 없음 | Phase 5 강력 권고 — 전체 신규 페이지 |
| notif-1-2 | bell 클릭 → 5 most recent dropdown | dropdown 노출 | A | 없음 | |
| notif-1-3 | 행 클릭 → mark read + dismiss + navigate to source | 3 동작 | A | 없음 | |
| notif-1-4 | dropdown footer → full inbox link | navigate `/notifications` | A | 없음 | |
| notif-2-1 | `/notifications` — newest first, infinite scroll (page 25) | virtual / lazy | A | 없음 | Phase 5 권고 |
| notif-2-2 | 행 — Title (bold while unread) / Body / channel icons / timestamp / Mark read / Open | 6 필드 | A | 없음 | |
| notif-2-3 | toolbar — Mark all as read + Filter (trigger / date range) | 두 액션 | A | 없음 | |
| notif-3-1 | Preferences tab — channel × trigger toggle | 4채널 × 5트리거 | A | 없음 | |
| notif-3-2 | In-app — disabled toggle (always on) + tooltip | 비활성 + tooltip 메시지 | A | 없음 | |
| notif-3-3 | Email — on by default, requires `SMTP_*` | 기본 ON | A | 없음 | |
| notif-3-4 | Slack — off by default, requires `SLACK_WEBHOOK_URL` | 기본 OFF | A | 없음 | |
| notif-3-5 | Teams — off by default, requires `TEAMS_WEBHOOK_URL` | 기본 OFF | A | 없음 | |
| notif-3-6 | toggle 즉시 저장, Save button 없음, toast feedback | UI 동작 | A | 없음 | |
| notif-4-1 | bell polling 60s, hidden tab 시 일시정지, focus 시 즉시 poll | API 호출 빈도 | A | 없음 | Phase 5 권고 — 422 guard (PR #36 M3) |
| notif-5-1 | 5 trigger 표 (`scan_finished` / `gate_failed` / `new_cve` / `approval_request` / `disk_pressure`) | 5 trigger 모두 등록 | A | 없음 | |
| notif-6-1 (Verify) | scan trigger → 완료 후 bell 증가 + `/notifications` 새 row | 알림 도착 | A | 없음 | Phase 5 권고 |
| notif-6-2 (Verify) | 다른 탭에서 mark read → 60s 내 첫 탭 badge 감소 | cross-tab sync | A | 없음 | |
| notif-6-3 (Verify) | email 비활성 후 scan → in-app only | preference 적용 | B | 없음 | SMTP 수신함 검증 |
| notif-7-1 (Trbl) | bell badge never updates — hidden tab | foreground 시 갱신 | A | 없음 | |
| notif-7-2 (Trbl) | email never arrives — SMTP / verified email | 외부 SMTP | B | 없음 | |
| notif-7-3 (Trbl) | Slack 메시지 도착 X — webhook 만료 → 404 silent | 가이드 | B | 없음 | 외부 Slack |

**Subtotal**: 21 단계 / A 18 / B 3 / C 0 / D 0 (Phase 5 강력 우선 — 전체 신규 페이지)

### user-guide/integrations.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| integ-1-1 | `/integrations` — API keys + Webhooks 두 탭 분리 | 두 탭 노출 | A | `integrations.spec.ts:67` (gotoIntegrations + expectMounted) | |
| integ-2-1 | API keys tab — 행 (label / prefix / scope / expiry / last-used) | 5 컬럼 | A | 없음 | 컬럼 schema 가드 부재 |
| integ-2-2 | New API key → Label / Scope (org/team/project) / Expiry (30/90/180/365/custom) | 폼 노출 | A | `integrations.spec.ts:78` (create-form + create-name visible) | scope / expiry 가드 부재 |
| integ-2-3 | Create → 일회성 reveal modal (`tos_<8>_<32>`) + Copy 버튼 + 경고 | 보안 가드 | A | `integrations.spec.ts:83` (closeCreateDialog) | one-time reveal contract 가드 부재 — 보안 회귀 가드 권고 |
| integ-2-4 | 사용: `Authorization: Bearer <key>` (또는 `ApiKey`) | 두 scheme 모두 가능 | A | 없음 | adversarial parametrize — empty / oversized / leading whitespace |
| integ-2-5 | GitHub Actions / Jenkins 예제 코드 | yaml / groovy snippet 정확 | C | 없음 | |
| integ-2-6 | Revoke — hover row → Revoke → confirm. immediate (~5s cache TTL) irreversible | 즉시 무효화 | A | 없음 | Phase 5 권고 — 보안 회귀 가드 |
| integ-3-1 | Webhooks tab — fixed URLs | URL 표시 | A | 없음 | |
| integ-3-2 | GitHub URL `/v1/webhooks/github` + content-type + X-Hub-Signature-256 HMAC + push/pull_request | 정확 | A | 없음 | 보안 가드 — HMAC verify |
| integ-3-3 | GitLab URL `/v1/webhooks/gitlab` + X-Gitlab-Token + push/MR | 정확 | A | 없음 | |
| integ-3-4 | Project Settings → CI/CD 에서 webhook_secret 생성 / rotate | 생성 + rotate | A | 없음 | adversarial parametrize — webhook URL (memory) |
| integ-3-5 | rotate 후 old secret ~5s 내 reject. 401 까지 short window | 명확한 동작 | A | 없음 | Phase 5 권고 |
| integ-4-1 (Verify) | curl 200 with team's projects | API 응답 | A | 없음 | |
| integ-4-2 (Verify) | GitHub push → Webhook deliveries 202 | 외부 GitHub | B | 없음 | 외부 GitHub 발신 |
| integ-4-3 (Verify) | audit log `api_key.create` + `webhook.delivery` events | audit row | A | 없음 | |
| integ-5-1 (Trbl) | 401 — key 모름 / expired / revoked. 401 vs 403 구분 | 명확 코드 | A | 없음 | adversarial parametrize |
| integ-5-2 (Trbl) | 403 — scope 부족 | 명확 코드 | A | 없음 | |
| integ-5-3 (Trbl) | 429 — per-key rate limit + Retry-After | 명확 코드 | A | 없음 | Phase 5 권고 |
| integ-5-4 (Trbl) | GitHub webhook 401 — HMAC mismatch (raw body) | 가이드 | B | 없음 | GitHub 외부 |
| integ-5-5 (Trbl) | GitLab webhook 401 — token mismatch | 가이드 | B | 없음 | GitLab 외부 |

**Subtotal**: 20 단계 / A 17 / B 3 / C 1 / D 0

---

### 사용자 매뉴얼 합계

| 페이지 | 단계 수 | A | B | C | D | ⚠ |
|--------|---------|---|---|---|---|---|
| projects | 24 | 21 | 0 | 1 | 0 | 1 |
| scans | 33 | 28 | 3 | 1 | 1 | 2 |
| vulnerabilities | 20 | 14 | 3 | 1 | 2 | 0 |
| components-and-licenses | 24 | 16 | 3 | 2 | 3 | 0 |
| sbom | 23 | 19 | 2 | 0 | 1 | 1 |
| approvals | 19 | 16 | 2 | 1 | 0 | 0 |
| auth-and-profile | 23 | 16 | 7 | 0 | 0 | 0 |
| notifications | 21 | 18 | 3 | 0 | 0 | 0 |
| integrations | 20 | 17 | 3 | 1 | 0 | 0 |
| **사용자 매뉴얼 합계** | **207** | **165** | **26** | **7** | **7** | **4** |

---

## 관리자 매뉴얼 (Super Admin 페르소나)

### admin-guide/users-and-teams.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| u&t-1-1 | Org / Teams / Roles 모델 (1 org, n teams, 3 roles) | ASCII 다이어그램 정확 | C | 없음 | |
| u&t-2-1 | super_admin / team_admin / developer 권한 표 | 3 행 정확 | A | `admin_users_teams.spec.ts:104` (developer → /admin/* → AdminNotFound), `:175` (cannot_modify_self) | RBAC 정확 매핑은 backend integration test 영역 |
| u&t-3-1 | 추가 cross-team role: 한 사용자 다중 팀 다중 role 가능 | 가능 | A | 없음 | Phase 5 권고 |
| u&t-4-1 | super_admin: /admin/users → Invite user → email/name/team/role | 폼 노출 | A | `admin_users_teams.spec.ts:63` (gotoAdminUsers) | invite flow 가드 부재 |
| u&t-4-2 | one-time invite link 24h expiry → password set (≥12, bcrypt 12, no NIST-banned) | 보안 정책 정확 | A | 없음 | Phase 5 권고 |
| u&t-5-1 | team_admin invite — same minus team selector | 폼 차이 | A | 없음 | RBAC 검증 |
| u&t-6-1 | 기존 user 추가 → /admin/teams or Team settings → Members → Add member | flow 동작 | A | `admin_users_teams.spec.ts:163` (addMember + member_added toast) | |
| u&t-7-1 | role 변경 → /admin/users → Memberships → Change role | dropdown + submit | A | `admin_users_teams.spec.ts:91` (changeRoleTo) | |
| u&t-7-2 | audit log `team_membership.update` (previous_role, new_role) | audit row | A | 없음 | Phase 5 권고 |
| u&t-8-1 | remove user from team → Members → Remove | UI 액션 | A | `admin_users_teams.spec.ts:170` (removeMember + member_removed toast) | |
| u&t-9-1 | last-super-admin protection: 마지막 super_admin demote/deactivate 거부 | 409 + RFC 7807 problem | A | `admin_users_teams.spec.ts:175` (cannot_modify_self error) | ⚠ — spec 의 주석에 따르면 "the seeded super-admin is the only super-admin in the test DB" 라서 last_super_admin guard 와 cannot_modify_self guard 가 항상 동시 trip. 매뉴얼은 두 가드를 별개로 설명. backend integration test 가 진짜 last-super-admin 시나리오 가드 |
| u&t-9-2 | 대안: 두번째 super_admin 승격 후 demote | 정상 흐름 | A | 없음 | Phase 5 권고 |
| u&t-9-3 | DB-level CHECK constraint + API pre-flight | 직접 SQL 도 차단 | A | 없음 | unit test 영역 |
| u&t-10-1 | deactivate user → 모든 세션/refresh 무효 | 강제 sign-out | A | 없음 | Phase 5 권고 |
| u&t-10-2 | reactivation = 단일 클릭 | 간단 복원 | A | 없음 | |
| u&t-11-1 | delete vs deactivate — soft-delete (typed-email confirmation modal) | UUID 잔존 | A | 없음 | adversarial parametrize — wrong-email confirm |
| u&t-12-1 | super_admin 만 team 생성 → /admin/teams → New team → name/desc/default visibility | 폼 동작 | A | `admin_users_teams.spec.ts:156` (createTeam + created toast) | |
| u&t-13-1 | team rename — super_admin or team_admin | 두 페르소나 모두 가능 | A | 없음 | Phase 5 권고 |
| u&t-13-2 | team archive — super_admin only — hides + disables new project + 기존 readable | 4 효과 | A | 없음 | |
| u&t-13-3 | team delete — projects 모두 archive/move 후만 | 사전 조건 | A | 없음 | |
| u&t-14-1 | sessions 표 — access 30min memory + refresh 7d cookie (rotation, reuse-detection, HttpOnly+Secure+SameSite=Lax) | 보안 정책 정확 | A | 없음 | unit test 영역 + 부분 auth.spec |
| u&t-14-2 | reuse detection: refresh 두번 사용 → 전체 family invalidate | 강제 재인증 | A | 없음 | Phase 5 권고 — 보안 회귀 가드 |
| u&t-15-1 (Verify) | 초대 후 /admin/users 가 user `pending` 상태 | UI 표시 | A | 없음 | |
| u&t-15-2 (Verify) | audit log `user.invite` | audit row | A | 없음 | |
| u&t-15-3 (Verify) | activation 후 status `active` | UI 갱신 | A | 없음 | Phase 5 권고 |
| u&t-15-4 (Verify) | team member list 에 노출 | UI 갱신 | A | 부분 — `admin_users_teams.spec.ts:168` (expectMemberRow) | |
| u&t-16-1 (Trbl) | 초대 email 도착 X — `SMTP_*` / log inspect / spam | 외부 SMTP | B | 없음 | 외부 SMTP |
| u&t-16-2 (Trbl) | self-elevation 차단 | 다른 super_admin 필요 | A | `admin_users_teams.spec.ts:175` (cannot_modify_self) | |
| u&t-16-3 (Trbl) | "User already exists" — Add to team 으로 우회 | 가이드 | A | 없음 | |

**Subtotal**: 28 단계 / A 27 / B 1 / C 1 / D 0 / ⚠ 1 (u&t-9-1 last-super-admin vs cannot_modify_self drift)

### admin-guide/dt-connector.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| dt-1-1 | DT 운영 pain 3종 (slow startup / stale projects / sync windows) | 컨셉 정확 | D | 없음 | 가이드 |
| dt-2-1 | 운영 layer 다이어그램 (CB → health probe → cache) | 흐름 정확 | C | 없음 | |
| dt-3-1-1 | health monitor — Celery Beat 60s ping `${DT_URL}/api/version` → `dt_health` table | 60s tick | A | 없음 | beat 동작 검증 |
| dt-3-1-2 | 3 consecutive fail → `degraded` → 4번째 fail → `down` | state machine | A | 없음 | Phase 5 권고 |
| dt-3-1-3 | /admin/dt — current state / last successful / last error / 24h sparkline | 4 정보 | A | `admin_dt_scans_disk_audit_health.spec.ts:55` (gotoAdminDT + getBreakerState) | sparkline 가드 부재 |
| dt-3-1-4 | `down` 시 `docker restart dt` 1회 + 90s 대기 → 회복 시 `healthy`, 실패 시 CB OPEN | 자동 복구 시도 | B | 없음 | docker restart — 호스트 권한 |
| dt-3-2-1 | CB 3 state (CLOSED / HALF_OPEN / OPEN) | state machine | A | `admin_dt_scans_disk_audit_health.spec.ts:61` (3 state 중 하나) | |
| dt-3-2-2 | OPEN: 캐시 즉시 반환 (DT round-trip X) | 동작 | A | 없음 | Phase 5 권고 |
| dt-3-2-3 | HALF_OPEN: 30s 마다 1 call. success → CLOSED, fail → OPEN | probe 주기 | A | 없음 | |
| dt-3-2-4 | /admin/dt + `GET /api/v1/admin/dt/state` | endpoint 응답 | A | 없음 | |
| dt-3-3-1 | DT 응답 → `vuln_cache` mirror (project/component/cve + severity/summary/fix) | mirror table | A | 없음 | unit test 영역 |
| dt-3-3-2 | cache best-effort, 1h lag 가능 | 가이드 | D | 없음 | |
| dt-3-4-1 | orphan cleanup — 6h Celery Beat 주기 | beat 등록 | A | 없음 | beat 검증 |
| dt-3-4-2 | DT-only projects → orphan, 삭제 (DT_ORPHAN_AUTODELETE=false 시 confirm) | 동작 | A | 없음 | Phase 5 권고 |
| dt-3-4-3 | portal-only projects → next scan 자동 생성 | 동작 | A | 없음 | |
| dt-3-4-4 | /admin/dt → Orphan projects + Delete selected | UI | A | 없음 | Phase 5 권고 |
| dt-4-1 | bootstrap: docker-compose dt overlay 기동 | up -d | B | 없음 | docker-compose |
| dt-4-2 | http://localhost:8080 접속 + admin/admin 로그인 + password 갱신 | 외부 DT UI | B | 없음 | 외부 DT |
| dt-4-3-1 | OSV 8 ecosystem (npm/Maven/PyPI/RubyGems/crates/Go/Packagist/NuGet) 활성 | 8 토글 | B | 없음 | 외부 DT |
| dt-4-4 | mirror sync 시간 — Maven ~1h, others 5-15min | 정보 | D | 없음 | |
| dt-4-5 | Automation team API key copy | DT UI | B | 없음 | 외부 DT |
| dt-4-6 | `.env` 에 `DT_API_KEY` 설정 + 서비스 재시작 | host file edit | B | 없음 | 호스트 권한 |
| dt-4-7 | /admin/dt 가 60s 내 `healthy`, orphan 빈 list | 검증 | A | 부분 — `admin_dt_scans_disk_audit_health.spec.ts:61` | |
| dt-5-1 | 외부 DT 연결 — `DT_URL` + `DT_API_KEY` 설정, dt overlay 미가동 | env 설정 | B | 없음 | 외부 DT |
| dt-5-2 | orphan-cleanup manual-confirm 정책 권장 | 가이드 | D | 없음 | |
| dt-6-1 | manual probe — `POST /api/v1/admin/dt/probe` (super_admin) | curl 200 | A | `admin_dt_scans_disk_audit_health.spec.ts:68` (forceHealthProbe) | |
| dt-6-2 | manual orphan cleanup — `POST /api/v1/admin/dt/orphans/cleanup` | curl 200 | A | 없음 | Phase 5 권고 |
| dt-7-1 | notifications 5 trigger 표 (scan finished off / gate failed on / new CVE on / approval on / disk pressure on) | 5 trigger | A | 없음 | duplicate of notif page |
| dt-7-2 | 채널 (email/Slack/Teams) — webhook URL `.env` | env config | B | 없음 | 외부 SMTP/Slack/Teams |
| dt-8-1 (Trbl) | /admin/dt = down but DT browser 가능 — compose network 분리 | 진단 명령 | B | 없음 | docker-compose exec |
| dt-8-2 (Trbl) | breaker stuck OPEN — `POST /api/v1/admin/dt/breaker/reset` | curl 200 | A | 없음 | Phase 5 권고 |
| dt-8-3 (Trbl) | resync after DT 회복 — `POST /api/v1/admin/dt/resync` | curl 200 idempotent | A | 없음 | |
| dt-8-4 (Trbl) | orphan list 모르는 projects — 외부 DT 공유 시 | 정책 안내 | D | 없음 | |

**Subtotal**: 33 단계 / A 17 / B 11 / C 1 / D 4

### admin-guide/disk-and-health.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| disk-1-1 | /admin/health 페이지 — 8 컴포넌트 (backend / postgres / redis / worker / beat / frontend / traefik / dt) | 8 행 | A | `admin_dt_scans_disk_audit_health.spec.ts:204` (gotoAdminHealth + names contain postgres/redis/active_scans) | ⚠ — spec 은 `postgres`/`redis`/`active_scans` 만 required, 매뉴얼은 8 컴포넌트 명시. 매뉴얼이 정확하다면 spec 부족, 시스템이 부족하다면 매뉴얼 drift |
| disk-1-2 | 행 — Component / State (healthy/degraded/down) / Last check / Detail | 4 컬럼 | A | 부분 — `admin_dt_scans_disk_audit_health.spec.ts:213` (status ok/degraded/down) | ⚠ — spec 의 status 값 `ok` vs 매뉴얼 `healthy` — 라벨 drift 가능 |
| disk-1-3 | 5s WebSocket auto-refresh, 벽 디스플레이 핀 가능 | 5s 갱신 | A | 없음 | Phase 5 권고 |
| disk-2-1 | health probes 표 — 8 컴포넌트별 probe 정확 (`curl /health` / `pg_isready` / `redis-cli ping` / Celery `inspect ping` / beat heartbeat 90s / `curl /healthz` / traefik / dt) | probe 매핑 | A | 없음 | unit test 영역 |
| disk-2-2 | 1 miss → degraded, 3 consecutive miss → down | state machine | A | 없음 | Phase 5 권고 |
| disk-3-1 | /admin/disk — Workspace + PostgreSQL gauge | 2 gauge | A | `admin_dt_scans_disk_audit_health.spec.ts:131` (gotoAdminDisk) | ⚠ — spec 은 `workspace` + `postgres` 카드만 required. 매뉴얼은 2 gauge 표현. 카드 vs gauge UI 의 차이 검증 필요 |
| disk-3-2 | warn 70% / hard 90% threshold + 효과 (yellow vs red + scans block + admin notification) | 2 threshold | A | 없음 | Phase 5 권고 |
| disk-3-3 | env override — `DISK_WARN_LIMIT_PCT=70` / `DISK_HARD_LIMIT_PCT=90` | env config | B | 없음 | 호스트 .env edit |
| disk-3-4 | hard 시 `POST /v1/projects/{id}/scans` → RFC 7807 503 (`type=disk-pressure`) | 응답 contract | A | 없음 | adversarial parametrize — boundary 89/90/91 |
| disk-3-5 | 기존 in-flight scans 미킬, 신규만 reject | 동작 | A | 없음 | Phase 5 권고 |
| disk-4-1 | offender 식별 — `du -sh /workspace/* | sort -h | tail -20` | 진단 명령 | B | 없음 | docker-compose exec + sudo |
| disk-4-2 | free space — analyzer-result 30d delete + archived workspace 삭제 | rm 명령 | B | 없음 | docker-compose exec + 파괴적 |
| disk-4-3 | /admin/disk 10s 내 갱신, hard 미만 시 자동 재개 | 동작 | A | 없음 | Phase 5 권고 |
| disk-4-4 | long-term — WORKSPACE_HOST_PATH 이동 + BACKUP_RETENTION_DAYS 낮추기 + off-host backup | 정책 | D | 없음 | |
| disk-5-1 | hard 트립 시 disk pressure notification (super_admin email + Slack + Teams) | 4 채널 | B | 없음 | SMTP/Slack/Teams 외부 |
| disk-5-2 | crossing 당 1회, recovered 도 1회 | 중복 X | A | 없음 | Phase 5 권고 |
| disk-6-1 (Verify) | /admin/health all green | 전체 healthy | A | 부분 — spec 5 가 정상 status check | |
| disk-6-2 (Verify) | /admin/disk 가 warn 미만 | gauge | A | 없음 | |
| disk-6-3 (Verify) | test scan 성공 | end-to-end | A | 부분 — `scan_flow.spec.ts` (단, fixme 가드) | |
| disk-7-1 (Trbl) | health all healthy 인데 사용자 불만 — worker hang or DT mirror stale | 진단 가이드 | D | 없음 | |
| disk-7-2 (Trbl) | disk gauge 잘못 — host volume 변경 후 backend restart 필요 | 가이드 | A | 없음 | |
| disk-7-3 (Trbl) | hard limit 너무 공격적 — 95% 까지 raise 가능 | 정책 | D | 없음 | |

**Subtotal**: 22 단계 / A 14 / B 5 / C 0 / D 3 / ⚠ 3 (disk-1-1 8 components, disk-1-2 ok vs healthy, disk-3-1 gauge vs card)

### admin-guide/audit-log.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| audit-1-1 | append-only 정책 + CHECK constraint (no UPDATE / DELETE) | DB 가드 | A | 없음 | unit test 영역 |
| audit-2-1 | schema 표 (id UUIDv7 / ts / actor_user_id / actor_kind / action / target_kind / target_id / request_id / payload jsonb / ip / user_agent) | 11 필드 | A | 없음 | unit test 영역 |
| audit-3-1 | 모든 인증 POST/PATCH/PUT/DELETE 1 row, GET 는 X. SBOM/report 다운로드만 `*.export` | 정확 | A | 없음 | Phase 5 권고 |
| audit-3-2 | system jobs (Celery): scan.create / dt_orphan.delete / backup.complete / notification.send | 4 종 | A | 없음 | Phase 5 권고 |
| audit-4-1 | /admin/audit — paginated filterable | UI | A | `admin_dt_scans_disk_audit_health.spec.ts:165` (gotoAdminAudit + filterByTargetTable) | |
| audit-4-2 | filter — actor / action / target_kind / target_id / date range / request_id (filters compose, URL 갱신) | 6 filter | A | 부분 — target_table only | 5개 미가드 — Phase 5 우선 |
| audit-4-3 | 기본 컬럼 (ts / actor / action / target / ip), 행 클릭 → payload diff expand | UI | A | 없음 | |
| audit-4-4 | 10k entries virtualized scroll | smooth | A | 없음 | |
| audit-5-1 | Export CSV — 현재 filtered, 100k cap, UTF-8 BOM | xlsx 호환 | A | `admin_dt_scans_disk_audit_health.spec.ts:184` (audit.exportCsv → filename .csv + csv_started toast) | |
| audit-5-2 | API: `GET /api/v1/admin/audit?from=…&to=…&page=…&size=…` + cursor `next` | API contract | A | 없음 | adversarial parametrize — invalid date / oversized size |
| audit-6-1 | "who deleted project X?" — `action=project.delete` + `target_id=<uuid>` | 정확 1 row | A | 없음 | Phase 5 권고 |
| audit-6-2 | "what did user Y do" — `actor=y@acme.com` + last 7 days | 행 list | A | 없음 | |
| audit-6-3 | "who suppressed CVE-2024-12345" — `action=vuln_finding.update` + payload expand | 가이드 | A | 없음 | first-class CVE filter는 roadmap |
| audit-6-4 | trace one request — `X-Request-ID` 헤더 + filter | 1:1 매핑 | A | 없음 | request_id 가드 부재 |
| audit-7-1 | retention — 자동 prune X, ~50MB/year/active user | 정책 | D | 없음 | |
| audit-7-2 | archive then truncate — `pg_dump` + `psql DELETE` (수동 SQL) | 가이드 | B | 없음 | DB 직접 access |
| audit-7-3 | DELETE 시 immutability constraint 일시 disable → 자체가 audit row | 자기-기록 | A | 없음 | unit test 영역 |
| audit-8-1 (Verify) | privileged action 후 1s 내 /admin/audit 새 row 상단 | UI 갱신 | A | 없음 | Phase 5 권고 |
| audit-8-2 (Verify) | request_id = X-Request-ID 응답 헤더 | 매칭 | A | 없음 | |
| audit-8-3 (Verify) | payload diff 정확, PII (email/password hash/API key) 마스킹 | mask_pii 적용 | A | 없음 | 보안 회귀 가드 — 강력 권고 |
| audit-9-1 (Trbl) | 누락 — 읽기 / 500 / RBAC 가시성 | 3 가지 원인 | D | 없음 | |
| audit-9-2 (Trbl) | CSV truncated — 100k cap | 가이드 | A | 없음 | |
| audit-9-3 (Trbl) | payload grep — `payload @> '{"new_state":"suppressed"}'::jsonb` (super_admin SQL session) | DB 가이드 | B | 없음 | DB direct |

**Subtotal**: 23 단계 / A 17 / B 3 / C 0 / D 3

### admin-guide/backup-and-restore.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| bkp-1-1 | 백업 구성 — postgres.sql.gz + workspace.tar.gz + manifest.json (timestamp/alembic_head/db_size/workspace_path) | 3 파일 | A | 없음 | Phase 5 강력 권고 |
| bkp-1-2 | `.env` / Traefik ACME state 미백업 | 가이드 | D | 없음 | |
| bkp-2-1 | manual backup — `bash scripts/backup.sh` | 출력 명확 | A | 없음 | install/restore UAT (PR #38) 에 부분 가드 — chore E |
| bkp-2-2 | 7일 retention prune (BACKUP_RETENTION_DAYS) + `--no-prune` 옵션 | env / flag | A | 없음 | Phase 5 권고 |
| bkp-3-1 | UI: /admin/backup — 사이드바 진입 | 페이지 도달 | A | 없음 | Phase 5 강력 권고 — 신규 페이지 PR #29 |
| bkp-3-2 | Trigger backup now — Celery 큐 row 생성 + status `running` + live progress bar | 라이브 진행 | A | 없음 | |
| bkp-3-3 | 완료 시 `succeeded` + Download 링크 | UI 변경 | A | 없음 | Phase 5 권고 |
| bkp-3-4 | 행 — timestamp / size / **auto** badge / Download / Delete + auto = 7d retention + lock icon | 6 필드 | A | 없음 | |
| bkp-3-5 | Celery Beat 일일 00:00 UTC default + `BACKUP_DAILY_ENABLED=false` 로 비활성 | beat 등록 | A | 없음 | |
| bkp-3-6 | Upload + Restore — Choose file (max 10GB) | 폼 | A | 없음 | adversarial parametrize — > 10GB / decompression bomb (PR #36 H3) |
| bkp-3-7 | typing-gate `restore` 정확 입력 + Restore 비활성 → 활성 | UI 가드 | A | 없음 | adversarial parametrize — variants of `restore` (Restore, RESTORE, restor) |
| bkp-3-8 | frontend `X-Confirm-Restore: yes` 헤더 + super_admin role + 둘 다 검증 (412 if missing) | double gate | A | 없음 | Phase 5 강력 권고 — 보안 회귀 가드 |
| bkp-3-9 | 진행 stream + 완료 시 row succeeded + JWT revoke (user table 교체) | 강제 재인증 | A | 없음 | |
| bkp-4-1 | cron schedule — `0 3 * * *` | crontab edit | B | 없음 | sudo crontab |
| bkp-5-1 | off-host — aws s3 sync / rclone / rsync | 외부 cloud | B | 없음 | 외부 S3/B2/etc |
| bkp-6-1 | restore — `bash scripts/restore.sh backups/...` + interactive [y/N] | 확인 prompt | A | 없음 | install/restore UAT 부분 가드 |
| bkp-6-2 | restore 단계 — backend/frontend/worker/beat stop → postgres restore (--clean) → workspace restore (rm + extract) → restart → alembic head verify | 5 단계 | A | 없음 | install/restore UAT |
| bkp-6-3 | 성공 출력 — 4 lines (database / workspace / app / alembic match) | 정확 | A | 없음 | |
| bkp-7-1 | DR runbook — provision host → install.sh → stop stack → copy backup from S3 → restore.sh → sign-in | 6 단계 | B | 없음 | 호스트 전체 교체 |
| bkp-8-1 | forward-only migrations + restore — manifest mismatch warning | 동작 | A | 없음 | Phase 5 권고 |
| bkp-8-2 | 옵션 1 (roll code back) vs 옵션 2 (alembic upgrade head) | 정책 | D | 없음 | |
| bkp-9-1 | encrypted backups — gpg --symmetric AES256 + shred | 외부 gpg | B | 없음 | |
| bkp-10-1 | systemd timer recipe — service + timer file + enable | systemd | B | 없음 | sudo systemd |
| bkp-11-1 (Verify) | 새 디렉토리 + 3 파일 존재 | ls | A | 없음 | |
| bkp-11-2 (Verify) | manifest.json json decode + non-empty alembic_head | jq | A | 없음 | install/restore UAT |
| bkp-11-3 (Verify) | `gunzip -t postgres.sql.gz` 성공 | 무결성 | A | 없음 | |
| bkp-11-4 (Verify) | restore 후 portal sign-in 가능 (백업 시점 자격증명) | 동작 | A | 없음 | install/restore UAT |
| bkp-11-5 (Verify) | project / scan / audit row 갯수 매칭 | 데이터 무결성 | A | 없음 | Phase 5 권고 |
| bkp-11-6 (Verify) | /admin/health all green | 정상 | A | 부분 — spec 5 | |
| bkp-12-1 (Trbl) | pg_dump permission denied — POSTGRES_USER mismatch | 진단 | A | 없음 | |
| bkp-12-2 (Trbl) | restore aborts at workspace step — read-only mount or in-use | 가이드 | B | 없음 | 호스트 mount 진단 |
| bkp-12-3 (Trbl) | alembic head mismatch warning | 가이드 | A | 없음 | |
| bkp-12-4 (Trbl) | empty workspace tarball — worker 가 churn 시 tar skip → worker stop 권장 | 가이드 | B | 없음 | 호스트 진단 |

**Subtotal**: 33 단계 / A 22 / B 9 / C 0 / D 2 (Phase 5 강력 우선 — /admin/backup 신규 페이지 PR #29 전체)

### admin-guide/api-keys.md

| 단계 ID | 본문 | 기대 결과 | 분류 | 기존 E2E | 비고 |
|---------|------|----------|------|----------|------|
| apik-1-1 | API key 비-대화형 client용 정의 — JWT session 미사용 | 컨셉 | D | 없음 | |
| apik-2-1 | /integrations UI 가 user-facing entry — list / create one-time reveal modal / per-row Revoke ~5s | duplicate of integrations | A | `integrations.spec.ts:67` (gotoIntegrations) | |
| apik-3-1 | key shape — `tos_<8>_<32>` + prefix public + secret bcrypt hash + 일회성 reveal | shape 정확 | A | 없음 | adversarial parametrize — invalid prefix / oversized / null bytes |
| apik-3-2 | constant-time prefix lookup + `bcrypt.checkpw` (timing attack 방어) | 보안 contract | A | 없음 | unit test 영역 |
| apik-4-1 | scope 모델 — owning team + effective role (developer default, team_admin 가능) + allowed actions (`scan:trigger`, `scan:read`, `report:download`, `webhook:receive`, `*`) + expiry | 4 필드 | A | 없음 | Phase 5 권고 |
| apik-4-2 | 일반 CI key = developer + 3 actions + 1년 expiry | 권장 정책 | D | 없음 | |
| apik-5-1 | team_admin: Project Settings → CI/CD → API keys → New API key → Label / Allowed actions / Expiry | 폼 흐름 | A | 부분 — `integrations.spec.ts` (general /integrations entry) | per-project Settings → CI/CD path 가드 부재 |
| apik-5-2 | 일회성 modal 노출, copy 후 close. prefix 만 잔존 | 보안 가드 | A | 없음 | Phase 5 강력 권고 — 보안 회귀 |
| apik-5-3 | super_admin: team selector unlocked | RBAC 차이 | A | 없음 | |
| apik-6-1 | 사용 — `Authorization: ApiKey <key>` (Bearer 도 가능, ApiKey 권장) | 두 scheme | A | 없음 | adversarial parametrize |
| apik-7-1 | rotation 사유 — compromise / personnel / 정책 (분기) | 가이드 | D | 없음 | |
| apik-7-2 | rotation 절차 — issue new → CI secret update → CI cycle 1회 → revoke old | 4 단계 | A | 없음 | |
| apik-7-3 | revoke 후 ~5s auth cache TTL | 동작 | A | 없음 | Phase 5 권고 |
| apik-8-1 | revocation — Project Settings → CI/CD → API keys → Revoke + 즉시 + irreversible | UI | A | 없음 | adversarial parametrize — confirm dialog |
| apik-9-1 | listing — UI 표시 (label / prefix / team / role / actions / expiry / last-used ts / IP). secret 복구 X | 컬럼 | A | 부분 — `integrations.spec.ts` | last-used 가드 부재 |
| apik-10-1 | audit — `api_key.create` / `api_key.revoke` / `api_key.use` (actor_kind=api_key) | 3 event | A | 없음 | Phase 5 권고 |
| apik-10-2 | filter audit by `actor_kind=api_key` | actor view | A | 없음 | |
| apik-11-1 | webhook secrets vs API keys — 인바운드 vs 아웃바운드 | 구분 | C | 없음 | duplicate of integrations |
| apik-12-1 (Verify) | curl 200 with team's projects | API | A | 없음 | |
| apik-12-2 (Verify) | audit log `api_key.create` with prefix | audit row | A | 없음 | Phase 5 권고 |
| apik-12-3 (Verify) | CI build 1회 통과 | 외부 CI | B | 없음 | 외부 CI runner |
| apik-13-1 (Trbl) | 401 with fresh key — whitespace 또는 actions 미포함. 401 vs 403 distinguish | 가이드 | A | 없음 | adversarial parametrize |
| apik-13-2 (Trbl) | "prefix exists secret mismatch" — brute-force attempt. 5 misses/60s → super_admin Slack alert. revoke + rotate | 보안 alert | A | 없음 | Phase 5 강력 권고 — 보안 회귀 가드 |
| apik-13-3 (Trbl) | local OK but CI X — secret env / outbound IP firewall / Authorization 헤더 proxy 보존 | 가이드 | B | 없음 | CI 외부 |

**Subtotal**: 24 단계 / A 18 / B 2 / C 1 / D 3

---

### 관리자 매뉴얼 합계

| 페이지 | 단계 수 | A | B | C | D | ⚠ |
|--------|---------|---|---|---|---|---|
| users-and-teams | 28 | 27 | 1 | 1 | 0 | 1 |
| dt-connector | 33 | 17 | 11 | 1 | 4 | 0 |
| disk-and-health | 22 | 14 | 5 | 0 | 3 | 3 |
| audit-log | 23 | 17 | 3 | 0 | 3 | 0 |
| backup-and-restore | 33 | 22 | 9 | 0 | 2 | 0 |
| api-keys | 24 | 18 | 2 | 1 | 3 | 0 |
| **관리자 매뉴얼 합계** | **163** | **115** | **31** | **3** | **15** | **4** |

---

## 분류 통계 (최종)

| 지표 | 값 | % |
|------|-----|------|
| Total 단계 수 | **370** | 100.0% |
| A (Playwright 자동) | **280** | 75.7% |
| B (외부 통합) | **57** | 15.4% |
| C (시각/Copy) | **10** | 2.7% |
| D (수동/정책) | **22** | 5.9% |
| 기존 E2E 가드 보유 (전부 또는 부분) | **약 51** | 13.8% |
| 신규 E2E 추가 권고 (A 분류 중 가드 부재) | **약 229** | 61.9% |
| 매뉴얼 의심 (⚠) | **8** | 2.2% |

기존 E2E 가드 매핑 요약:

| Spec 파일 | 시나리오 수 | 매뉴얼 단계 커버 |
|-----------|-------------|-------------------|
| auth.spec.ts | 6 | auth-and-profile 11 / login 절반 |
| integrations.spec.ts | 1 | integrations 4 / api-keys 5 |
| project_detail.spec.ts | 6 | projects 3 / components-and-licenses 6 / scans 1 |
| scan_flow.spec.ts | 4 (모두 fixme) | scans 3 (단, fixme guard) |
| vulnerabilities.spec.ts | 5 | vulnerabilities 7 |
| licenses.spec.ts | 4 | components-and-licenses 4 |
| obligations.spec.ts | 4 | components-and-licenses 3 / sbom 2 |
| admin_users_teams.spec.ts | 4 | users-and-teams 9 |
| admin_dt_scans_disk_audit_health.spec.ts | 5 | dt-connector 3 / scans 2 / disk-and-health 4 / audit-log 2 |
| **합계** | **39 시나리오** | **약 51 매뉴얼 단계 부분 매핑** |

---

## 자동화 비율 권고

### Phase 2 / 3 walkthrough 분리 기준

walkthrough 시 단계 분류별 처리:

1. **A 단계 (280)** — Playwright headed mode 로 자동 실행. 페르소나별 1 spec 파일 (`apps/frontend/tests/walkthrough/<page>.walkthrough.ts`) 임시 작성. 결과 분류 (✅/📝/🐛/⏭) + 스크린샷 캡처.
2. **B 단계 (57)** — 자동화 프레임워크 X. 외부 통합 별 처리:
   - **OAuth (auth-4-2~5-3)** — 사람이 직접 GitHub/Google consent → portal redirect 확인 (제한된 1회)
   - **SMTP / Slack / Teams notifications** — fixture inbox (예: maildev) 또는 사람이 실 수신함 점검
   - **외부 CI** — fixture mock CI runner (curl 으로 API 호출 시뮬레이션) 또는 사람이 실 GitHub Actions 트리거
   - **외부 DT bootstrap** — bundled DT overlay 띄우고 자동 검증, 외부 DT 는 사람 (조직 소유 DT 가 없으므로)
   - **호스트 명령 (sudo / cron / systemd / docker-compose ps / du / rm)** — dev compose 환경에서 자동 실행 가능 (root in container), 호스트 직접 명령은 별도 진단
   - **gpg / aws s3 / rclone** — fixture 환경에서 자동 가능
3. **C 단계 (10)** — Phase 2/3 에서 사람이 시각 점검만 (스크린샷 캡처 + 매뉴얼과 비교). Phase 5 우선순위 X.
4. **D 단계 (22)** — 매뉴얼의 정확성 / 정책 안내 / 추세 가이드. walkthrough 시 본문 정확도만 검증.

**결론**: 약 **75.7% (280/370)** 가 Playwright 자동화 가능. 남은 24.3% 는 외부 통합 또는 정책 가이드.

### Phase 5 (E2E 추가) 우선순위 영역

기존 E2E 가드 부재인 A 단계 약 229 개 중 **회귀 가치 높은 영역** 우선:

#### 우선순위 P1 (보안 / 데이터 무결성 / 신규 페이지 — 즉시 추가)

1. **`/profile` 전체** (auth-and-profile-5-1~6-3) — Connected Accounts list / Unlink / 마지막 sign-in method 잠금 가드 (409 alert). PR #34 신규 페이지로 가드 부재. **신규 spec `auth_and_profile.spec.ts`.**
2. **`/notifications` Inbox + Preferences 전체** (notif-1-1~7-3) — 21 단계 중 18 A 단계 모두 가드 부재. PR #34/36 신규 페이지. **신규 spec `notifications.spec.ts`.**
3. **`/admin/backup` UI 전체** (bkp-3-1~3-9) — Trigger backup now / Download / Upload+Restore typing-gate / `X-Confirm-Restore` double gate. PR #29 신규 페이지. PR #36 H3 의 decompression bomb 가드. **신규 spec `admin_backup.spec.ts`.**
4. **API Key one-time reveal contract** (apik-5-2, integ-2-3) — 보안 회귀 가드 (modal 닫은 후 secret 복구 불가). 새 spec 또는 `integrations.spec.ts` 확장.
5. **API Key brute-force alert** (apik-13-2) — 5 misses/60s → super_admin Slack notification. 보안 알림 회귀 가드.
6. **vulnerabilities suppression RBAC** (vulns-7-2~7-4) — audit log + risk score 갱신 + 다음 scan gate 제외 (현재 가드는 표시 단의 disabled 만).
7. **last-super-admin guard 진짜 시나리오** (u&t-9-1) — 두 명 super_admin → 한 명 demote → 마지막 demote 시 409. backend integration test 가 보장하나 frontend RBAC 가드 별도 spec 권고.

#### 우선순위 P2 (회귀 가치 높음 — 다음 phase)

8. **SBOM 4 포맷 download + byte-stable** (sbom-2-1, 3-1, 4-1~4-4, 8-3) — 4 포맷 × byte-stable 회귀 가드. CycloneDX validate / SPDX validate 는 외부 cli 라 부분 자동화.
9. **Excel/PDF 보고서** (sbom-6-1~6-5).
10. **Approvals 전체 흐름** (approvals-3-1~5-1) — auto Pending 생성 / Claim → Under Review → Approve/Reject + justification ≥10 chars + audit. **신규 spec `approvals.spec.ts`.**
11. **Project private repo + risk score** (projects-7-1, 8-1) — encrypted PAT + risk score breakdown.
12. **Audit log 6 filter + payload PII 마스킹** (audit-4-2, 8-3) — actor / action / target_kind / target_id / date / request_id + mask_pii.
13. **DT circuit breaker / cache 동작 회귀** (dt-3-2-2, 8-2~8-3) — OPEN 시 cache 응답 + breaker reset endpoint.

#### 우선순위 P3 (가능하면 추가)

14. **scan lifecycle 5 상태 trigger** (scans-3-1) — failed / cancelled trigger.
15. **scan WebSocket reconnect + payload contract** (scans-6-1~6-2).
16. **CI integration adversarial parametrize** (memory `feedback_adversarial_input_parametrize`) — webhook URL / API Key scope / backup upload 의 적대적 input.

---

## 매뉴얼 자체의 의심 단계 (⚠ 8 개)

Phase 4 walkthrough 후 일괄 검증 / fix 권고. **본 phase 에서는 수정 X**.

| ID | 의심 내용 | Phase 4 처리 권고 |
|----|-----------|-------------------|
| projects-7-2 | "Project Settings → Repository 에서 deploy key 생성" — 실제 UI 에 deploy key 생성 화면 존재 여부 미확인 | walkthrough 시 실제 UI 확인. 부재 시 매뉴얼 제거 또는 시스템 추가 |
| scans-2-1-2 | scan_flow.spec.ts 의 `KNOWN_PAGE_SIZE_BUG=false` 가드 → 4 시나리오 fixme. 실제 fix 됐는지 확인 | bug 가 머지됐다면 fixme 제거 + spec 활성. 미머지면 issue 등재 |
| scans-7-1 | "프로젝트 status switches to **Completed**" — lifecycle 표는 `succeeded`. UI 라벨이 어느 쪽? | walkthrough 시 실제 라벨 확인. 매뉴얼 또는 UI 정합 |
| sbom-6-4 | "Project → Reports menu (top-right of any tab)" — 위치 정확? | UI 확인 |
| u&t-9-1 | last-super-admin protection 매뉴얼 vs cannot_modify_self guard. spec 의 주석에 따르면 두 가드가 동시 trip. 매뉴얼이 마지막 super_admin 시나리오 정확히 가드되는지 검증 | backend integration test 의 실제 시나리오와 매뉴얼 정합 |
| disk-1-1 | /admin/health 가 8 컴포넌트 (backend/postgres/redis/worker/beat/frontend/traefik/dt). spec 은 postgres/redis/active_scans 만 required. 8 컴포넌트 모두 노출 / 매뉴얼이 정확? | walkthrough 시 실제 노출 컴포넌트 확인 |
| disk-1-2 | State 값 — 매뉴얼 `healthy` vs spec `ok`. 라벨 drift 가능 | UI 의 실제 status 값 확인. 매뉴얼 또는 코드 정합 |
| disk-3-1 | Workspace + PostgreSQL **gauge** — 매뉴얼은 2 gauge, spec 은 카드 (`getCardStatus`). 두 표현이 같은 UI? | UI 확인 |

---

## 다음 세션 (Phase 2) 시작점

`docs/sessions/_next-session-prompt-manual-walkthrough.md` §2 (User persona walkthrough — Developer 9 페이지) 그대로 실행. 본 matrix 의 사용자 매뉴얼 207 단계를 분류별로 처리:

1. **A 단계 165 개** — Playwright headed mode 로 자동 실행 (페이지별 spec)
2. **B 단계 26 개** — 외부 통합 별 처리 (OAuth: 1회 사람 / SMTP: maildev fixture / CI: curl mock / DT: bundled overlay)
3. **C 단계 7 개 + D 단계 7 개** — walkthrough 시 본문 정확도만 검증
4. **⚠ 4 개** — Phase 4 fix 후보로 backlog 등재

Phase 3 (Admin walkthrough — 6 페이지 163 단계) 는 동일 절차로 super_admin 페르소나 적용.
