# 핸드오프 — W3 #30 프로젝트 목록 집계 완료 · §0.5 Wave 1~3 전체 종결 · 다음 세션 인테이크 모드 (2026-05-26)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0 대시보드 + §0.5. 이 문서는 그 세션 스냅샷이다.
> 직전 세션 핸드오프: [`2026-05-26-w3-32-reports-center-complete.md`](./2026-05-26-w3-32-reports-center-complete.md) — W3 #32 완료. 본 세션에서 #30(BE+FE) 종료 + §0.5의 의도 불명확 라벨 라인(`W4 후속/위생 | #26·#27·#19~#22`) 제거 + 트래커·핸드오프 정리.

---

## 이번 세션 결과

### 사전 갭 분석 — 트래커 본문 그대로 진짜 갭

직전 핸드오프의 사전 갭 분석 지시를 따라 실코드부터 검증:

- **BE 갭 확인**: `apps/backend/services/project_list_enrichment.py` `enrich_project_rows`는 2개 batched 쿼리(latest_attempt status + severity_summary)만 돌리고 카운트 집계 없음. `apps/backend/schemas/scan.py:374` `ProjectPublic`은 `latest_scan_id`(UUID)만 가지고 `scan_count`/`release_count`/`last_scan_at` 같은 메타데이터 없음.
- **FE 갭 확인**: `apps/frontend/src/features/projects/ProjectListPage.tsx` `ProjectRow`는 name(link) · git_url · `<SeveritySummary>` · `<ProjectStatusBadge>` · "Trigger Scan" 버튼만 노출. 발견성 메타데이터 없음.
- **결론**: 직전 #32와 달리 본문 오버스테이트 아님 — **양쪽(BE+FE) 모두 진짜 갭**. [[feedback-tracker-text-may-overstate-gaps]] 적용은 "정정 불필요"로 결론.
- **사용자 결정**: 단일 PR(BE+FE 한 번에), FE 디자인은 Compact 인라인(severity 뒤·status badge 앞 — `Rel 12 · Scn 47 · 2h ago` 패턴).

### #30a BE 완료 (`6255700`)

- **`apps/backend/services/project_list_enrichment.py`**: 신규 private `_scan_counts_map(session, *, project_ids) -> dict[uuid, dict[str, Any]]` — 단일 GROUP BY 쿼리로 `(scan_count, release_count, last_scan_at)` 동시 산출. `func.count()` 전체 + `func.count(case((cast(Scan.status, String) == "succeeded", 1)))` 성공만 + `func.max(Scan.created_at)`. `ix_scans_project_created_at`(project_id leading) 이미 access path 커버 — 신규 인덱스 불필요. 한 번도 안 스캔된 프로젝트는 결과 dict에 없음 → caller 기본값.
- **`enrich_project_rows` 시그니처 확장**: `tuple[dict, dict, dict]` 3-튜플 반환. caller 단 1곳(`list_projects_endpoint`)이라 backward-compat shim 불필요(CLAUDE.md "no shims" 준수). 모듈 docstring "3 batched queries (status / severity / counts)"로 갱신.
- **`apps/backend/schemas/scan.py:374` `ProjectPublic`**: `scan_count: int = 0`, `release_count: int = 0`, `last_scan_at: datetime | None = None` 추가. description은 기존 `latest_scan_status`/`severity_summary` 패턴 미러 — "Populated only on list endpoint; default on single-project responses." detail 라우터(`GET/POST/PATCH /v1/projects/{id}`)는 default 값으로 자연 흡수, 회귀 없음.
- **`apps/backend/api/v1/projects.py:236-265`**: enrich 결과 3-튜플 unpack, per-row item에 `counts_by_project.get(p.id)` 매핑(키 없으면 default 유지).
- **테스트**:
  - 단위(`test_project_list_enrichment.py`) — 기존 7→12 (3-튜플 시그니처 반영 + `_scan_counts_map` 3 시나리오: multi-attempt partial success / multi-project single-query grouping / empty input no-SQL). batched-ness 회귀 가드 statement-count ≤3→≤4로 uplift.
  - 통합(`test_projects_api.py`) — 기존 24→26 (list 응답 wire 검증 + detail 응답 default 직렬화 검증).
- **게이트**: ruff/mypy(전체 417 파일) clean · pytest 38 PASS · alembic head `0025` 유지(스키마 변경 없음) · OpenAPI 스냅샷 무영향(스냅샷은 endpoint paths + query params만 추적, 응답 필드 추가는 drift 아님).

### #30b FE 완료 (`971af25`)

- **`apps/frontend/src/lib/projectsApi.ts`**: `ProjectPublic` 타입에 3 필드 추가 — `scan_count: number`, `release_count: number`, `last_scan_at: string | null`. 인라인 주석 "populated only on list endpoint" 패턴 일관.
- **`apps/frontend/src/features/projects/ProjectListPage.tsx`**: 신규 `<ScanMetadataSummary>` 컴포넌트 — severity 묶음 뒤·`<ProjectStatusBadge>` 앞 인라인 배치. `font-mono text-xs text-muted-foreground` (severity가 risk 색상인 반면 본 묶음은 muted — 발견성 보조 신호이지 위험 신호 아님 명시). 형식 `Rel 12 · Scn 47 · 2h ago` (releases·scans·last_scan_at 상대시간), 절대 ISO는 `title` tooltip. null 케이스(`last_scan_at == null && scan_count === 0`) → 묶음 미렌더(SeveritySummary null-skip 패턴과 일관). `data-testid="project-row-scan-meta"`.
- **상대시간**: 기존 `formatRelativeToNow` (`@/lib/relativeTime`) 재사용 — 신규 헬퍼 작성 회피.
- **i18n EN/KO** (`apps/frontend/src/locales/{en,ko}/projects.json`):
  - `row.releases_abbrev` = "Rel" / "릴"
  - `row.scans_abbrev` = "Scn" / "스캔"
  - `row.never_scanned` = "Never scanned" / "스캔 이력 없음"
  - `row.scan_meta_aria` = "Releases {{releases}} of {{scans}} scans, last {{when}}" / "릴리스 {{releases}}개, 전체 {{scans}}회 스캔, 마지막 {{when}}"
  - plural 미사용 ([[feedback-frontend-i18n-no-plural-check]]) — `_one`/`_other` 없음.
- **테스트**: `ProjectListPage.test.tsx` 9→12 (happy / never-scanned 미렌더 / attempts-with-zero-releases). 기존 fixture 6개 spec(`ProjectPublic`을 mock하는 다른 suites)에 신규 3 필드 추가 — regression 없음.
- **scope 가드**: `compareByLatestScan`의 `updated_at` fallback은 그대로 유지. 정렬 정정(updated_at→last_scan_at)은 별도 후속 — 본 PR이 신규 필드 도입 자체라 본문에 자기-순환 TODO 주석 추가 금지.
- **게이트**: typecheck clean · lint 0 errors(기존 23 warnings 유지) · i18n:check OK · vitest 929(was 926, +3).
- **UI 수동 점검**: vitest only — 데모 시드 패스워드 mismatch로 live `/v1/projects` curl 실패. BE wire shape는 `6255700`의 단위·통합 테스트로 검증 완료.

### 트래커 갱신 (`bf9a728`) + 본 세션 정리

- `bf9a728`: §0.5 #30 ⬜ → ✅, BE/FE 구현 요지·게이트 본문 압축.
- **세션 후반 정리(별도 commit 예정)**: §0.5 헤더 `Wave 1~4` → `Wave 1~3`. 의도 불명확 라벨 라인 "W4 후속/위생 | #26·#27·#19~#22" **제거**([[feedback-handoff-next-session-must-be-self-sufficient]] 적용). §0 대시보드에 `§0.5 Wave 1~3` 행 추가 + "현재 상태: 인테이크 모드" 안내. §9 핸드오프 규약을 "라벨 단독 금지 + 다음 세션 인테이크 모드" 문구로 갱신.

## 트래커 §0.5 갱신

- #30 → ✅ 완료 (2026-05-26): BE `6255700` + FE `971af25` + 트래커 `bf9a728`.
- W3 진행 2/2: #32 ✅ · #30 ✅. **W3 완료. §0.5 Wave 1~3 전체 종결(7/7 태스크).**
- 의도 불명확 라벨 라인 `W4 후속/위생` 제거 — 트래커는 의도가 정해진 항목만 보존한다.

## 다음 세션: **사용자 발견 불편/버그 인테이크 모드**

§0.5 트래커 기반 진행은 W3 종결로 일단락. 다음 세션은 트래커 우선이 아니라 **사용자가 핸즈온 사용 중 발견한 불편함·버그를 받아 처리**하는 모드로 진행한다.

### 인테이크 패턴

1. 첫 메시지에서 사용자가 발견한 항목을 자유 형식으로 보고(증상·재현·파일·스크린샷 등).
2. 코드(`apps/backend`/`apps/frontend`)와 직전 트래커 항목들을 grep으로 대조해 **진짜 갭 vs 이미 처리됨 vs 사용자 환경 이슈** 분류.
3. 진짜 갭이면 PR scope 좁히고 단일 또는 BE→FE 분리 PR로 진행([[feedback-tracker-text-may-overstate-gaps]]).
4. 처리 항목은 §0.5에 새 라인으로 등재할 때 **라벨만 있는 한 줄 금지** — 의도·범위·출발 파일/심볼을 함께([[feedback-handoff-next-session-must-be-self-sufficient]]).

### 사용자에게 인테이크 시 묻는 것 (필요한 경우)

- 어디서(어느 화면·엔드포인트)?
- 무엇이 기대대로 동작 안 하는가?
- 재현 가능한가? 데모 시드 데이터에서? 직접 시드한 데이터에서?
- 우선순위(blocker / 큰 불편 / 사소함)?

### 알려진 부속(사용자 보고 우선순위와 무관, 인테이크 후 채택 여부 결정)

- **#32c 부속** — Vuln PDF `scan_id` pin 미지원(직전 #32 핸드오프에서 분리). 출발: `apps/backend/api/v1/reports.py:131`. 작은 BE 변경 후보.
- **#30 정렬 정정 후속** — `ProjectListPage.tsx` `compareByLatestScan`의 `updated_at` fallback을 본 PR이 추가한 `last_scan_at`으로 정정. `compareByRisk`도 severity_summary 합산으로 실 의미 부여 가능. FE only 작은 변경 후보.

위 두 항목은 사용자 보고에서 동일/유사 불편이 언급될 경우에만 묶어 처리한다. 사용자가 다른 더 시급한 항목을 보고하면 후순위로 미룬다.

### 운영 레인 미진행 (외부 블로커 대기)

- O1·O2·O3·O4. 인테이크와 무관, 코드 트랙과 비동기.

### 시작 절차 (다음 세션 첫 메시지)

사용자가 직접 보고할 첫 항목을 받아 인테이크 → 분류 → PR scope 결정. 트래커를 먼저 읽어 무엇을 할지 *제안*하지 말 것 — 본 세션 종료 시점에 §0.5는 의도적으로 비어 있다.

만약 사용자가 첫 메시지를 "트래커 어디부터?" 같은 형태로 보낸다면, **트래커 §0.5는 W3까지 모두 ✅ 완료 상태이며 다음 항목은 사용자 인테이크로 정의됨**을 안내하고 보고를 요청한다.

---

## 환경 메모 (직전 세션과 동일)

- 컨테이너 실행 중(`backend`/`celery-worker`/`celery-beat`/`postgres`/`redis`/`dtrack-api`/`frontend`). 백엔드 코드 편집 후 `docker-compose restart backend` 필수.
- 백엔드 테스트는 컨테이너에서: `docker-compose exec -T backend python -m pytest …`. 호스트 직접 실행 불가(conftest가 `redis`/`postgres` hostname에 연결).
- OpenAPI 스냅샷 의도적 갱신: `docker-compose exec -T -e REGEN_OPENAPI_SNAPSHOT=1 backend python -m pytest tests/unit/test_openapi_contract.py -q` → diff 리뷰. **본 PR은 스냅샷 무영향**(응답 필드 추가만; 스냅샷은 path/query만 추적).
- 프론트: `npm run typecheck` / `lint` / `i18n:check`(푸시 전 필수, 복수형 `_one/_other` 금지) / `npx vitest run`.
- alembic head: **0025** (변경 없음).

## 푸시 상태

- 본 세션 1차(`0af6393..6ebc71c`, 4 commit: BE `6255700` · FE `971af25` · 트래커 `bf9a728` · 핸드오프 `6ebc71c`) — 사용자 승인으로 origin/main 푸시 완료.
- 본 세션 2차(트래커 W4 라인 제거 · §0 대시보드 + §9 규약 갱신 · 본 핸드오프 갱신) — 다음 commit으로 push 예정.
