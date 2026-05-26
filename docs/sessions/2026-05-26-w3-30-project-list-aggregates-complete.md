# 핸드오프 — W3 #30 프로젝트 목록 집계 완료, W3 다음 단계 (2026-05-26)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5. 이 문서는 그 세션 스냅샷이다.
> 직전 세션 핸드오프: [`2026-05-26-w3-32-reports-center-complete.md`](./2026-05-26-w3-32-reports-center-complete.md) — W3 #32 완료, #30을 이 세션에서 BE+FE 모두 종료.

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

### 트래커 갱신 (`bf9a728`)

§0.5 #30 ⬜ 대기 → ✅ 완료, BE/FE 양쪽 구현 요지·게이트·후속(정렬 정정) 본문에 압축.

## 트래커 §0.5 갱신

- #30 → ✅ 완료 (2026-05-26): BE `6255700` + FE `971af25` + 트래커 `bf9a728`.
- W3 진행 2/2: #32 ✅ · #30 ✅. **W3 완료**.

## 다음 세션: **W4 후속/위생** 또는 부속 후속

### 우선순위 후보

1. **W4 후속/위생** — `#26`(vex_import 앵커 보안검토 · #24 동일 클래스) · `#27`(vuln 툴바 레이아웃) · `#19~#22`(콘솔 위생/임계/정리/housekeeping). 트래커 §0.5 라인 42.
2. **#32c 부속** — Vuln PDF `scan_id` pin 미지원(스냅샷 보기에서 PDF 무력화). 직전 #32 핸드오프에서 분리한 작은 BE 변경.
3. **#30 정렬 정정 후속** — `compareByLatestScan`의 `updated_at` fallback을 실제 `last_scan_at` 기반으로 정정 + `compareByRisk`도 severity_summary 합산 등 실 의미 부여. FE only, 작은 변경.
4. **OpenAPI 라우터 노출** — 본 PR이 응답 필드 3개를 추가했지만 OpenAPI 스냅샷이 path/query만 추적해 drift 안 잡힘. 스냅샷 generator를 schema-aware로 확장하는 follow-up은 별도 위생 작업.

### 사전 갭 분석 (각 후보별 착수 전 검증 필수)

- W4 후속의 #26·#27·#19~#22 중 어느 것이 진짜 갭이고 어느 것이 이미 다른 PR에 흡수됐는지 [[feedback-tracker-text-may-overstate-gaps]] 패턴 적용 필요.
- #32c는 `apps/backend/api/v1/reports.py:131`의 Vuln PDF endpoint에 `scan_id: uuid.UUID | None = Query(None)` 추가 + `latest_succeeded_scan_id` 해석 분기 추가가 전부일 가능성(작음).

### 운영 레인 미진행 (외부 블로커 대기)

- O1·O2·O3·O4.

### 시작 절차 (다음 세션 첫 메시지)

"트래커 §0.5 W4 후속(#26/#27/#19~#22) 중 진짜 갭부터. 핸드오프 `docs/sessions/2026-05-26-w3-30-project-list-aggregates-complete.md` §"다음 세션"의 우선순위 후보별 사전 갭 분석부터 실코드 검증 — 각 태스크의 트래커 본문이 오버스테이트한 갭이 있으면 정정한다. 진짜 갭 기준으로 PR scope 좁힌다."

---

## 환경 메모 (직전 세션과 동일)

- 컨테이너 실행 중(`backend`/`celery-worker`/`celery-beat`/`postgres`/`redis`/`dtrack-api`/`frontend`). 백엔드 코드 편집 후 `docker-compose restart backend` 필수.
- 백엔드 테스트는 컨테이너에서: `docker-compose exec -T backend python -m pytest …`. 호스트 직접 실행 불가(conftest가 `redis`/`postgres` hostname에 연결).
- OpenAPI 스냅샷 의도적 갱신: `docker-compose exec -T -e REGEN_OPENAPI_SNAPSHOT=1 backend python -m pytest tests/unit/test_openapi_contract.py -q` → diff 리뷰. **본 PR은 스냅샷 무영향**(응답 필드 추가만; 스냅샷은 path/query만 추적).
- 프론트: `npm run typecheck` / `lint` / `i18n:check`(푸시 전 필수, 복수형 `_one/_other` 금지) / `npx vitest run`.
- alembic head: **0025** (변경 없음).

## 미푸시 잔여

- `6255700` (BE) · `971af25` (FE) · `bf9a728` (트래커) — 3 commit이 main 로컬에 있음. 사용자가 push 명시할 때까지 보류 ([[feedback-push-pr-authorized]] — push 허용되어 있으나 명시 승인 후).
- 본 핸드오프 commit은 다음 commit으로 처리.
