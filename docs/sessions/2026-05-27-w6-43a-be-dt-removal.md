# 핸드오프 — W6-#43a BE DT 제거 (2026-05-27)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 W6. 이 문서는 그 세션 스냅샷이다.
> 직전 세션: [`2026-05-27-w6-42-vulnerability-rematch-beat.md`](./2026-05-27-w6-42-vulnerability-rematch-beat.md) — W6-#42 자동 재매칭 beat 머지.
> 본 세션: **W6-chore-seed(PR #204) 머지 + W6-#43a(BE DT 제거, PR 대기) 작업**.
> 결정 메모리: [[project_dt_removal_decision]] (방향 + amendment).

---

## 이번 세션 결과

### 머지된 PR

| PR | 제목 | scope | commit |
|---|---|---|---|
| #204 | chore(W6-seed): fixed dev demo password + append-only .env sync | seed_demo dev/demo 디폴트 `DemoTest2026!` + `scripts/lib/env_sync.sh` + install/upgrade/dev-reset 통합 + 6 bash 테스트(9 asserts) + 33 pytest. shellcheck clean. | (squash) |

### 진행 중 PR — W6-#43a (`feat/w6-43a-be-dt-removal`)

**비가역 삭제** — `git revert`로만 회복 가능. 외부 사용자 0이라 안전 (메모리 [[project_dt_removal_decision]] amendment).

| 영역 | 변경 |
|---|---|
| **완전 삭제 (10 파일 + 2 디렉토리)** | `apps/backend/integrations/dt/`(4) · `api/v1/admin/dt.py` · `services/admin_dt_service.py` · `tasks/dt_{health,orphan_cleaner,orphan_cleanup,resync}.py`(4) · `tests/integration/scan/test_dt_circuit_breaker_cache.py` · `tests/unit/integrations/dt/`(4) · `tests/unit/services/test_admin_dt_service.py` · `tests/unit/tasks/test_dt_orphan_cleanup.py` |
| **수정 — 코드** | `api/v1/admin/__init__.py` (dt 라우터 제거) · `core/config.py` (8 getter 제거 + ADR 참조) · `tasks/celery_app.py` (4 task include + 3 beat schedule 제거 + docstring 갱신) · `services/admin_health_service.py` (`_probe_dt`/breaker import 제거, health 6-component) · `services/admin_disk_service.py` (`_dt_volume_path` + dt_volume entry 제거, disk 3-item) · `schemas/admin_ops.py` (BreakerState + 5 DT schema 제거 + AdminDiskItem.name shrink) |
| **수정 — 코멘트** | `integrations/_subprocess_env.py` · `integrations/cdxgen.py` (보안 의도 코멘트는 그대로) · `integrations/cosign.py` (그대로) · `services/oauth_identity_service.py` · `tasks/_progress.py` (모두 stale 코멘트 정리) |
| **수정 — 테스트** | `tests/integration/admin/test_admin_ops_api.py` (DT section 8건 삭제 + auth matrix dt 4건 + disk 3-item + health 6-component) · `tests/unit/services/test_admin_health_service.py` (_probe_dt 3건 삭제 + get_system_health 6-component) · `tests/unit/openapi_endpoints.json` (regen: /v1/admin/dt/{status,orphans} 제거) |
| **수정 — 운영** | `.env.example` Dependency-Track 섹션 삭제 + ADR-0001 안내 코멘트 |

**검증**:
- `grep -rIln "DT_API_KEY\|DT_URL\|integrations.dt\|tasks.dt_\|api.v1.admin.dt"` apps/backend = 0 (코드)
- `grep -rIn "from .*dt\|DT_" apps/backend/ \| grep -v trivy \| grep -v audit_log` = 코멘트만(historical reference) — 트래커 종료 조건 충족
- ruff clean / mypy clean (424 → **406 source files** — DT 파일 18개 삭제 반영)
- pytest collect-only: 4025 tests, 0 import error
- unit: 3287 pass + 1 openapi drift (regen으로 해결) + 7 skip
- 통합 admin/health/disk: 핵심 케이스 green (audit_export 413은 누적 DB 데이터로 unrelated)

**audit_log 보존**: DT 액션 타입(`dt_breaker`, `dt_projects`, `dt_health`)은 역사 사실로 그대로 남음. audit listener는 `integrations.dt.*`에 의존 안 함.

**security-reviewer (a3cf57b0051879c0c)**: 진행 중. 권한 우회 / 잔여 endpoint / Celery in-flight 메시지 drift 검증.

---

## 다음 세션이 해야 할 일

### 1) security-reviewer 결과 처리 + PR 머지

- agent 결과 확인 → Critical/High → 본 PR fix → 머지
- 트래커 §0.5 W6: `#43a 🟦` → `✅`, 5/7 → **6/7**

### 2) W6-#43b 프론트엔드 DT 제거

`apps/frontend/src/features/admin/dt/` 디렉토리 삭제(AdminDTPage·adminDTApi·useAdminDT). `router.tsx` `/admin/dt` 라우트 제거 → 404. `AppShell.tsx` admin nav DT 항목 제거. EN/KO 번역 키 정리 + `npm run i18n:check`. Playwright admin/dt 시나리오 → 404 redirect로. visual-regression DT baseline 삭제. **dep: W6-#43a 머지 후**.

### 3) W6-#43c 사용자/관리자 문서 교체

EN/KO 동시, 신규 admin-guide/vulnerability-data.md + reference/data-sources.md, dt-connector.md 삭제, 멘션 9개 정리. `release-notes/v2.4.0.md` 초안. dep: W6-#43b.

### 4) 나머지 — W6-#43d (배포), #43e (admin/health Trivy 패널), #44 (Trivy DB 라이프사이클), #43f (stage rename, 선택)

각 row dep 그래프 참조.

### 5) W6-chore-#42-followup (선택, 어디든 가능)

PR #203 security-reviewer 후속 4건: M-2(SBOM-present DB promote) / M-4(rematch_failure_count poison-pill) / L-1(diff key vulnerability_id) / L-2(critical→unknown 가시화).

---

## 핵심 참조 파일

- **계획 SoT**: `docs/post-ga-execution-tracker.md` §0.5 W6
- **결정 ADR**:
  - `docs/decisions/0001-replace-dt-with-trivy.md` (DT 제거 + amendment)
  - `docs/decisions/0002-w6-trivy-benchmark-cohort.md` (벤치 코호트, 정보용)
- **방향 메모리**: [[project_dt_removal_decision]]
- **본 PR 출발 코드**:
  - 삭제 인벤토리: 위 표 참조
  - audit_log DT 액션은 보존(역사 사실)
  - 신규 PR 출발: `apps/frontend/src/features/admin/dt/` (W6-#43b)

---

## 컨벤션 알림 (W6-#43a 특화)

- **비가역**: `git revert` 외 회복 경로 없음. 외부 사용자 0이라 안전.
- **stage 이름 유지**: `dt_upload`/`dt_findings` WS frame · E2E 하네스 호환 (#43f까지 유지).
- **audit_log DT 액션 보존**: dt_breaker / dt_projects / dt_health 행은 역사 사실. admin UI 필터는 deprecated 표기 (W6-#43b/c 후속).
- **Celery in-flight 안전성**: 머지 시 큐에 `trustedoss.dt_*` 메시지 있으면 NACK forever. 머지 전 `celery -A tasks.celery_app inspect active` 확인 권고. 운영 SOP는 W6-#43d upgrade.sh에 포함.
- **openapi 스냅샷**: 2 endpoint 제거 (regen 완료). 후속 PR에서 추가 endpoint 변경 시 다시 regen.
