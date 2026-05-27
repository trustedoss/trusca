# 핸드오프 — W6-#43b FE DT 제거 (2026-05-27)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 W6. 이 문서는 그 세션 스냅샷이다.
> 직전 세션: [`2026-05-27-w6-43a-be-dt-removal.md`](./2026-05-27-w6-43a-be-dt-removal.md) — W6-#43a 백엔드 DT 제거 머지.
> 본 세션: **W6-#43b(FE DT 제거)**.
> 결정 메모리: [[project_dt_removal_decision]] (방향 + amendment).

---

## 이번 세션 결과

### 진행 중 PR — W6-#43b (`feat/w6-43b-fe-dt-removal`)

**비가역 삭제** — `git revert`로만 회복 가능. 외부 사용자 0이라 안전 (메모리 [[project_dt_removal_decision]] amendment).

| 영역 | 변경 |
|---|---|
| **완전 삭제 (8 파일 + 2 디렉토리)** | `apps/frontend/src/features/admin/dt/`(3) · `tests/unit/features/admin/dt/`(2) · `tests/_harness/AdminDTHarness.ts` · `tests/visual/visual.spec.ts-snapshots/admin-dt-status.png` |
| **수정 — 라우팅/네비** | `router.tsx` (`AdminDTPage` import + `<Route path="dt">` 제거 — `<Route path="*" element={<AdminNotFound />}>` fallback이 404 처리) · `components/AppShell.tsx` (admin nav `Network` icon + DT entry 제거) |
| **수정 — 도메인 타입** | `features/admin/disk/api/adminDiskApi.ts` (`DiskItemName` 에서 `"dt_volume"` 제거 — BE W6-#43a `AdminDiskItem.name.shrink` 정합) · `features/admin/health/api/adminHealthApi.ts` (`HealthComponentName` 에서 `"dt"` 제거 — BE W6-#43a H-1 `HealthComponent.name` 6-name trim 정합) · `features/admin/lib/adminErrorMessage.ts` (`EXTENSION_KEY_MAP` DT 3 entry 제거) · `lib/problem.ts` (`KNOWN_PROBLEM_EXTENSION_KEYS` + `KNOWN_EXTENSION_SCHEMAS` 의 `dt_unreachable`/`dt_orphan_cleanup_in_progress`/`dt_breaker_already_closed` 3 키 제거) |
| **수정 — 컴포넌트 코멘트** | `features/admin/disk/AdminDiskPage.tsx` (헤더 코멘트 "Four cards (workspace / dt_volume / ...)" → "Three cards (workspace / postgres / redis)" + skeleton `Array.from({ length: 4 })` → `length: 3`) |
| **수정 — i18n EN/KO** | `locales/{en,ko}/admin.json` — `nav.admin.dt` 제거 · `admin.dt` 객체 전체 제거(~58 키) · `admin.disk.subtitle` DT 멘션 정리(EN/KO) · `admin.disk.card.dt_volume` 제거 · `admin.health.component.dt` 제거 · `admin.errors.dt_unreachable`/`dt_orphan_cleanup_in_progress`/`dt_breaker_already_closed` 제거 |
| **수정 — 단위 테스트** | `tests/unit/App.test.tsx` (`nav-admin-dt` 어서션 → `queryByTestId(...).not.toBeInTheDocument()` + `/admin/dt` → `admin-not-found` 시나리오 추가) · `tests/unit/features/admin/adminErrorMessage.test.ts` (DT extension 어서션 제거) · `tests/unit/lib/problem.test.ts` (DT extension whitelist + preservation 어서션 제거) · `tests/unit/features/admin/disk/AdminDiskPage.test.tsx` (4 카드 → 3 카드 + dt_volume → workspace 케이스 재명명) · `tests/unit/features/admin/health/AdminHealthPage.test.tsx` (7 컴포넌트 → 6 컴포넌트 + `dt` 픽스처 제거) |
| **수정 — Playwright 하네스** | `tests/_harness/PortalPage.ts` (`AdminDTHarness` import + `gotoAdminDT()` 제거, "PR #14 — Admin operational dashboards" 코멘트 갱신) · `tests/_harness/AdminDiskHarness.ts` (`DiskCardName` 에서 `dt_volume` 제거) |
| **수정 — Playwright spec** | `tests/e2e/admin_dt_scans_disk_audit_health.spec.ts` (테스트 1 — DT status/orphan/probe → "/admin/dt 는 AdminNotFound로 폴스루" 시나리오로 교체, describe 제목 `@critical admin scans / disk / audit / health` 로 정정, disk/health 케이스의 DT 멘션 코멘트 정리) · `tests/screenshots/capture_admin_guide.spec.ts` (`admin-guide/dt-connector` describe + `AdminDTHarness` import 제거) · `tests/screenshots/capture_ko_locale.spec.ts` (`admin-dt-status-ko` describe + `PortalPage` import 제거, 모듈 doc 정리) · `tests/visual/visual.spec.ts` ("admin dt status" 시나리오 제거 + 헤더 코멘트 4 페이지로 정정) |

**검증**:
- `grep -rIn "AdminDTPage\|adminDTApi\|useAdminDT\|admin-dt\|/admin/dt\|AdminDTHarness\|gotoAdminDT\|dt_volume\|dt_unreachable\|dt_orphan_cleanup_in_progress\|dt_breaker_already_closed\|admin\.dt\b\|nav\.admin\.dt\b\|nav-admin-dt\|component\.dt\b" apps/frontend/src apps/frontend/tests` = 의도된 fallthrough 어서션 라인 9개만 (`tests/unit/App.test.tsx` `queryByTestId(...).not.toBeInTheDocument()` + `tests/e2e/admin_dt_scans_disk_audit_health.spec.ts` 의 404 시나리오 + 헤더 코멘트들).
- `npm run typecheck` clean (407 source files 후 0 errors)
- `npm run lint` 0 errors (기존 warnings 18개 그대로)
- `npm run i18n:check` OK (EN/KO 키 미러 정합)
- `npm test` 947 pass (이전 972에서 −25 — DT 단위 케이스 흡수)

**의도적으로 유지된 잔재 (W6-#43f scope)**:

- `ScanProgress.tsx` / `useScanWebSocket.ts` 의 `dt_upload`/`dt_findings` stage 이름은 트래커 §0.5 W6 의 "stage 이름 유지" 정책에 따라 유지. WS frame + E2E 하네스 호환성 보호. W6-#43f (선택, v2.4.1 minor)에서 `sbom_upload`/`vuln_match` 로 일괄 rename.
- `locales/{en,ko}/scans.json` 의 `step_dt_upload` / `alerts.dt_unavailable` 도 같은 이유로 #43f 까지 유지.

**라우트 동작 확인**:
- `<Route path="*" element={<AdminNotFound />}>` 가 `<Route path="admin">` 내부에 있어 `/admin/dt` 가 자동으로 AdminNotFound 로 fallthrough. SuperAdmin 가드는 `<AdminLayout>` 에서 통과시키므로 비-superadmin 은 admin layout 자체가 안 뜨고, superadmin 은 admin nav 가 보이는 상태에서 AdminNotFound 페이지를 본다. `tests/unit/App.test.tsx` 신규 시나리오 + `tests/e2e/admin_dt_scans_disk_audit_health.spec.ts` 테스트 #1 이 양쪽에서 회귀 방지.

**비-DT 무관 작업**:

- 워킹 트리에 stale 한 `apps/backend/tasks/scan_source.py` 변경(`from tasks._progress import publish_log, reset_log_counter` — 존재하지 않는 export 참조)이 있어 `git checkout main --` 로 원복. 본 PR scope 밖.

---

## 다음 세션이 해야 할 일

### 1) security-reviewer 결과 처리 + PR 머지

- agent 결과 확인 → Critical/High → 본 PR fix → 머지
- 트래커 §0.5 W6: `#43b 🟦` → `✅`, 5/7 → **6/7**

### 2) W6-#43c 사용자/관리자 문서 교체

EN/KO 동시. 신규 `docs-site/docs/admin-guide/vulnerability-data.md` (Trivy DB 운영 — 동기화/air-gapped/트러블슈팅) + `docs-site/docs/reference/data-sources.md` (NVD/OSV/GHSA/EPSS/KEV reference + DT 문서 비교 갭). `dt-connector.md` 삭제 + sidebars 정리. 핵심 4페이지 교체(`reference/{architecture,env-variables}`, `installation/{docker-compose,helm}`) + 9건 멘션 정리(`intro`/`comparison`/`user-guide/{scans,vulnerabilities}`/`admin-guide/{disk-and-health,oncall-runbook}`/`ci-integration/github-actions`/`reference/glossary`/`contributor-guide/agent-team`). `release-notes/v2.0.0.md` 보존(역사) + `release-notes/v2.4.0.md` 초안(Breaking changes + Migration). docusaurus build EN/KO green · 깨진 링크 0 · i18n:check OK. **dep: W6-#43b**. owner: doc-writer + i18n-specialist.

### 3) W6-#43d 배포/Helm/upgrade.sh

`scripts/install.sh:400` DT_API_KEY 안내 제거 + Trivy DB 안내 추가. `scripts/upgrade.sh` v2.3→v2.4 절: Celery 큐 drain 대기, `dtrack-api` 컨테이너 정리, DT 볼륨 archive 안내, `.env` DT_* 자동 주석처리, 부팅 후 1-click "전체 재매칭" admin UI 트리거 + 진행 표시. `docker-compose.yml` DT 코멘트 6곳 제거 + `docker-compose.dt.yml` 삭제. `charts/trustedoss/` 0.2.x → 0.3.0 (values/configmap-env/secret/deployment-beat/_helpers/README 6 파일 정리). helm lint/template green + 시뮬 upgrade(v2.3.1→v2.4.0) ≤5분 다운타임·데이터 유실 0. **dep: W6-#43c**. owner: devops-engineer.

### 4) W6-#43e admin/health Trivy DB 패널 신설

BE: `GET /v1/admin/trivy/health` (last update, vuln count, DB version, next refresh ETA). FE: admin/health 에 `TrivyDBPanel` 추가. EN/KO. **dep: W6-#43b (FE) + W6-#44 (라이프사이클 데이터 소스)**.

### 5) W6-#44 Trivy DB 라이프사이클 관리 (필수)

worker 부팅 시 `trivy --download-db-only` 1회 + weekly beat refresh + air-gapped `TRIVY_DB_REPOSITORY` 미러 매뉴얼 + `trivy image`/`trivy sbom` 동시성 lock 테스트. **dep: W6-#40**.

### 6) W6-#43f stage rename (선택, v2.4.1)

`scan_source.py` stage 키 `dt_upload`/`dt_findings` → `sbom_upload`/`vuln_match`. WS frame · E2E 하네스 · 진행 표시 i18n 동시 갱신. **dep: W6-#43a 머지 후 임의 시점**.

### 7) W6-chore 백로그 (어디든 가능)

- **W6-chore-#43a-doc-drift** (Low 4건 + Info 2건): `tests/unit/services/test_admin_health_service.py` docstring + "seven_components" 함수명 · `tasks/{backup,notify,scan_reachability,source_archive_cleaner}.py` `:mod:` cross-ref · `core/config.py` "DT polling" 멘션 · `tasks/scan_container.py` 반사실 DT 설명 · `models/scan.py` + `schemas/{project_detail,vulnerability_detail}.py` "from DT" provenance 멘션. **dep: W6-#43a 머지 후, #43e 전에 처리 권고**.
- **W6-chore-#42-followup** (M-2/M-4/L-1/L-2): scan_artifact 분리 kind/컬럼 · rematch_failure_count poison-pill 가드 · diff key external_id → vulnerability_id · critical→unknown 가시화. **dep: W6-#42 머지 후**.

---

## 핵심 참조 파일

- **계획 SoT**: `docs/post-ga-execution-tracker.md` §0.5 W6
- **결정 ADR**:
  - `docs/decisions/0001-replace-dt-with-trivy.md` (DT 제거 + amendment)
  - `docs/decisions/0002-w6-trivy-benchmark-cohort.md` (벤치 코호트, 정보용)
- **방향 메모리**: [[project_dt_removal_decision]]
- **본 PR 출발 코드**:
  - 삭제 인벤토리: 위 표 참조
  - BE 가 이미 6-component health + 3-item disk + DT extension 응답 0으로 trim 됨(W6-#43a)
  - 신규 PR 출발: `docs-site/docs/admin-guide/dt-connector.md` 삭제 + `vulnerability-data.md` 신규 (W6-#43c)

---

## 컨벤션 알림 (W6-#43b 특화)

- **비가역**: `git revert` 외 회복 경로 없음. 외부 사용자 0이라 안전.
- **stage 이름 유지**: `dt_upload`/`dt_findings` WS frame · E2E 하네스 호환 (#43f까지 유지). 본 PR 은 admin DT UI 만 제거.
- **admin nav 위계**: SuperAdmin 가드는 `<AdminLayout>` 에서 통과. `/admin/dt` 는 admin nav 안 뜨고 사용자가 직접 URL 친 경우만 AdminNotFound 로 폴스루. nav 미렌더 + AdminNotFound 라우트 폴스루 모두 회귀 방지 테스트가 보호함.
- **openapi 영향 없음**: BE 가 이미 W6-#43a 에서 dt 엔드포인트 0으로 trim. FE 만 그 정합을 따라잡는 PR.
- **visual baseline**: `admin-dt-status.png` 만 삭제. 다른 4 페이지 baseline 은 무영향. W6-#43e 의 Trivy DB 패널이 그 자리를 후속 PR에서 차지.
