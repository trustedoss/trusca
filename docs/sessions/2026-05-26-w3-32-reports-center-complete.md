# 핸드오프 — W3 #32 Reports 센터 완료, W3 #30 다음 (2026-05-26)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5. 이 문서는 그 세션 스냅샷이다.
> 직전 세션 핸드오프: [`2026-05-26-w2-33b-bulk-actions-complete.md`](./2026-05-26-w2-33b-bulk-actions-complete.md) — W2 #33b 종결, W3 #32를 이 세션에서 BE+FE 모두 종료.

---

## 이번 세션 결과

### 사전 갭 분석으로 #32 scope 좁힘

직전 핸드오프의 사전 갭 분석 지시를 따라 실코드 검증부터 시작:

- **Notice** (`obligations.py:272`) — 엔드포인트·scan_id pin ✅ · UI `ObligationsToolbar` 안 · **이력 ❌**.
- **SBOM** (`sbom.py:103` + signature 5종) — 엔드포인트 4 포맷 + scan_id pin ✅ · UI `SbomTab` · **이력 ❌**.
- **Vulnerability PDF** (`reports.py:131`) — 엔드포인트 ✅ · `VulnerabilitiesToolbar` · **scan_id pin 미지원** + **이력 ❌**.
- **VEX export/import** (`vex.py:161,240`) — 두 엔드포인트 다 있음 · UI `VulnerabilitiesToolbar`에 한 자리 모임 · **이력 ❌** (단 import는 audit_log 자연 흡수).

`audit_log`는 `before_flush` 리스너 기반 — read-only 다운로드는 잡지 못함.

**결론**: "생성·이력 통합"이라는 원안 중 **생성 진입점은 이미 도메인 탭에 자연 배치돼 있고, 진짜 신규 가치는 이력 통합 단일점**. 사용자가 B안(이력 중심, 생성 UI는 도메인 탭 유지) 선택. 트래커 §0.5 #32 라인 정정 완료. [[feedback-tracker-text-may-overstate-gaps]]

### #32a BE 완료 (`dbd8c31`)

- **DB**: `report_downloads` 테이블 (Alembic 0025, forward-only, append-only — no updated_at). `report_type_enum` PG ENUM (`notice`/`sbom`/`vuln_pdf`/`vex_export`). FK 4개 — `project_id`/`team_id` CASCADE, `scan_id`/`user_id` SET NULL (이력 보존 정책). 3 compound 인덱스 — `(project_id, created_at DESC)` · `(team_id, created_at DESC)` · `(scan_id)` (leftmost-prefix로 FK equality도 커버).
- **모델**: `apps/backend/models/report_download.py` (SA 2.0 Mapped/mapped_column, `relationship()` 없음 — 단방향).
- **스키마**: `apps/backend/schemas/report_download.py` — `ReportType` Literal, `user`는 inline summary(id+email), `client_ip`/`user_agent` 응답 미노출(PII).
- **서비스**: `apps/backend/services/report_download_service.py`
  - `record_report_download` — best-effort emit. DB 에러는 `report_downloads.emit_failed` ERROR 로그 + swallow(다운로드 자체는 이미 성공). 명시 commit(read-only 라우터 컨텍스트). UA는 `mask_pii` 통과 후 512자 truncate, IP는 XFF-aware (`_client_ip_from`).
  - `list_report_history` — 페이지 1..200 기본 50. cross-team은 404 existence-hide ([[feedback-admin-existence-hide-pattern]]), super_admin bypass. OUTER JOIN으로 anonymized 행(user=null)도 노출.
- **라우터 emit 통합** (4 read-only):
  - NOTICE (`obligations.py:272`) — fmt(text/markdown/html), size_bytes=len(body)
  - SBOM (`sbom.py:103`) — 메인 4 포맷; signature/cert/attestation/public-key/bundle 5종은 본 PR 범위 밖
  - Vuln PDF (`reports.py:131`) — `latest_succeeded_scan_id` 해석 후 scan_id 동행
  - VEX export (`vex.py:161`) — scan_id=NULL(transitions 기반)
  - VEX import는 mutation이라 기존 audit_log로 자연 흡수
- **신규 라우터**: `GET /v1/projects/{project_id}/reports/history?type=…&scan_id=…&page=…&page_size=…`. 401/404(existence-hide)/422/429 RFC 7807. `@limiter.limit("10/minute")` + slowapi wrapper globals 패치 obligations.py 패턴 그대로.
- **테스트**: 신규 unit 21건(`test_report_download_service.py`) + integration 9건(`test_report_history_api.py`) — happy/best-effort swallow/cross-team 404/페이지 boundary/type 멀티/super_admin bypass/4 라우터 emit 검증.
- **회귀 fix**: `test_report_disconnect.py` — 신규 import 2개(`latest_succeeded_scan_id`, `record_report_download`) monkeypatch stub 누락 보완(stub 2줄 추가).
- **게이트**: ruff/mypy(전체 417 파일) clean · 신규 30 PASS · OpenAPI +1엔트리(7줄) · alembic head=0025.

### #32b FE 완료 (`689baa4`)

- **신규 탭** `Reports` (sbom↔source 사이). `ALLOWED_TABS` 등록, `?tab=reports` URL 거울로 reload·deeplink 영속. 탭 이탈 시 `?rpt_type=`/`?rpt_page=` 정리.
- **좌측 4 generate 카드** (NOTICE/SBOM/Vuln-PDF/VEX). Action 버튼은 `setSearchParams`로 `tab`만 바꾸고 `?scan=` 스냅샷 컨텍스트는 보존(`new URLSearchParams(prev)` 패턴).
- **우측 이력 테이블**: When(상대시간 + abs tooltip) / Who(email 또는 "—") / Type(tinted Badge + 라벨 — color-only signal 회피) / Format(font-mono) / Scan(첫 8자) / Size(humanize, null="—"). 필터 type MultiSelect, URL state로 `?rpt_type=`/`?rpt_page=` mirror. 빈 상태·로딩 스켈레톤·404 일반화 메시지("Reports unavailable" — existence-hide 일관)·429 처리.
- **API client/훅**: `reportHistoryApi.ts` + `useReportHistory.ts`. `paramsSerializer: { indexes: null }`로 `type[]`를 `?type=a&type=b` 반복 직렬화(BE Query() 컨트랙트 정합). queryKey prefix-invalidate, `placeholderData: keepPreviousData`로 페이지 전환 깜빡임 제거.
- **i18n EN/KO**: `tabs.reports` + `reports.*` 전체 서브트리(생성 카드 4 · 이력 컬럼 6 · 4 enum 라벨 · pagination/filter/empty/errors). plural 미사용([[feedback-frontend-i18n-no-plural-check]]) — `page_of`만 `{{page}}/{{total}}` interpolation.
- **하네스**: `PortalPage`에 3 verb 추가 — `selectReportsTab()`, `expectReportsTabReady()`, `clickReportsGenerateCard(slug)`.
- **테스트**: vitest `ReportsTab.test.tsx` 8 케이스(로딩+4카드, happy+pager, empty, 404 일반화, type 필터→refetch+URL, 카드 deeplink→`?scan=` 보존, next page→refetch+URL, null fallback) · Playwright `reports.spec.ts` 1 시나리오(spec만; 실행은 후속).
- **게이트**: typecheck clean · lint 0 errors(기존 파일 23 warnings) · i18n:check OK · vitest 926(was 918, +8).

### 트래커 갱신 (`24b4053`)

§0.5 #32 라인 ⬜ 대기 → ✅ 완료, BE/FE 양쪽 구현 요지·게이트·후속(#32c) 본문에 압축.

## 트래커 §0.5 갱신

- #32 → ✅ 완료 (2026-05-26): BE `dbd8c31` + FE `689baa4` + 트래커 `24b4053`.
- W3 진행 1/2: #32 ✅ · #30 ⬜.

## 다음 세션: **W3 #30 — 프로젝트 목록 행에 릴리스/스캔 수 표시 (발견성)**

### #30 범위 (가설)

프로젝트 목록 페이지(`apps/frontend/src/features/projects/ProjectListPage.tsx` 추정)에서 각 행이 현재 어떤 정보를 노출하는지가 출발점. 트래커 본문은 "릴리스/스캔 수 표시 — 발견성 보완"이지만, 다른 경우와 마찬가지로 본문이 실제 갭을 오버스테이트할 수 있다 [[feedback-tracker-text-may-overstate-gaps]] — 착수 전 검증 필수.

### 사전 갭 분석 (착수 전 검증 필수)

다음을 코드로 확인하고 트래커 본문 정정 가능성 검토:

- 프로젝트 목록 응답 스키마(`apps/backend/api/v1/projects.py` + `apps/backend/services/project_service.py`)에 이미 `scan_count` / `release_count` / `last_scan_at` 같은 집계 필드가 있는가?
- 있다면 FE `ProjectListPage` 행 컴포넌트가 이미 노출하는가? 안 노출하면 단순 FE 가시화 PR.
- 없다면 BE 집계 추가 필요 — 페이지 쿼리 cost 영향 검토(서브쿼리 vs 별도 카운트 API).
- "릴리스 = 성공한 스캔" 모델 ([[CLAUDE 트래커 §0.5 노트]]) — release_count는 `scans WHERE status='succeeded'` 카운트, scan_count는 전체 카운트.
- `apps/frontend/src/features/projects/components/ProjectListToolbar.tsx`에 정렬/필터로 노출할 가치 있는가?

### #32c 부속 (선택)

Vuln PDF `scan_id` pin 미지원 — 스냅샷 보기에서 PDF 무력화. #30과 묶거나 단독. 이번 세션 종료 시점에는 미진행.

### W4 후속/위생

- #26·#27·#19~#22 (vex_import 앵커 보안검토 · vuln 툴바 레이아웃 · 콘솔 위생/정리)

### 운영 레인 미진행 (외부 블로커 대기)

- O1·O2·O3·O4.

### 시작 절차 (다음 세션 첫 메시지)

"트래커 §0.5 #30부터. 핸드오프 `docs/sessions/2026-05-26-w3-32-reports-center-complete.md` §"다음 세션"의 사전 갭 분석부터 실코드 검증 — 프로젝트 목록 응답 스키마와 ProjectListPage 행이 이미 노출하는 메타데이터를 grep으로 확인하고, 트래커 본문이 오버스테이트한 갭이 있으면 정정한다. 진짜 갭(BE 집계 / FE 가시화 / 둘 다) 기준으로 PR scope 좁힌다."

---

## 환경 메모 (직전 세션과 동일)

- 컨테이너 실행 중(`backend`/`celery-worker`/`celery-beat`/`postgres`/`redis`/`dtrack-api`/`frontend`). 백엔드 코드 편집 후 `docker-compose restart backend` 필수.
- 백엔드 테스트는 컨테이너에서: `docker-compose exec -T backend python -m pytest …`. 호스트 직접 실행 불가(conftest가 `redis`/`postgres` hostname에 연결).
- OpenAPI 스냅샷 의도적 갱신: `docker-compose exec -T -e REGEN_OPENAPI_SNAPSHOT=1 backend python -m pytest tests/unit/test_openapi_contract.py -q` → diff 리뷰.
- 프론트: `npm run typecheck` / `lint` / `i18n:check`(푸시 전 필수, 복수형 `_one/_other` 금지) / `npx vitest run`.
- 풀-런 격리 오염 패턴 동일: license 클러스터 UniqueViolation(`API-Apache-2.0`) + admin audit export 413(audit_logs append-only 누적). 단독 PASS 확인. CI clean DB에서는 무관 — 직전 핸드오프와 동일 결론.
- alembic head: **0025**.

## 미푸시 잔여

없음(이 핸드오프 포함 다음 커밋으로 처리).
