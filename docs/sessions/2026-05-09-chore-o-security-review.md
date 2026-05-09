# Session Handoff — 2026-05-09 — Chore O (security-reviewer pass)

> Post-GA cleanup 세션 3. CLAUDE.md §7 Producer-Reviewer 회수 — PR #29/#32/#33 사후 보안 검토 + Tier 1+2 fix.
> 시작 시점: main HEAD = `0a9b1b5`. 종료 시점: main HEAD = `a1755cb`.

## 1. 처리한 PR

| Chore | PR | 머지 commit | 비고 |
|-------|-----|-------------|------|
| O | #36 | `a1755cb` | security-reviewer 사후 검토 + H1/H2/H3/M2/M3 fix |

## 2. security-reviewer 결과 요약

| Severity | Count | 처리 |
|----------|-------|------|
| Critical | 0 | — |
| High | 3 | H1/H2/H3 모두 본 PR fix |
| Medium | 5 | M2/M3 본 PR fix; M1/M4/M5 backlog Chore Q/R로 이월 |
| Low | 4 | L1/L2/L3/L4 backlog Chore S/T로 이월 |
| Info(passed) | 2 | I1 (tar `filter='data'`) + I2 (mark_read existence-hide) — 회귀 가드 유지 |

## 3. 적용한 fix

### H1 — OAuth identity unlink TOCTOU race
- `apps/backend/services/oauth_identity_service.py` — User SELECT에 `with_for_update()` 추가
- 회귀 가드: `tests/unit/services/test_oauth_identity_service.py::test_unlink_acquires_for_update_lock_on_owning_user` — 실제 unlink 흐름에서 emit된 SQL을 capture하여 `FROM users ... FOR UPDATE` 검증

### H2 — Terraform `__DB_PASSWORD__` placeholder 미동작
- `terraform/modules/cloud_run_backend/main.tf` — `DATABASE_URL` 단일 env (literal `__DB_PASSWORD__`) → 4분리 env (`DB_USER` / `DB_PASSWORD` (Secret Manager) / `DB_HOST` / `DB_NAME`)
- `apps/backend/core/config.py` — `database_url()` 런타임 합성. 우선순위: `DATABASE_URL` 직접 → 4분리 env 합성 → DEFAULT (dev). `quote_plus(password)` URL 인코딩
- 14 unit tests in `tests/unit/core/test_config_database_url.py`
- docker-compose `DATABASE_URL` backwards compat 유지 (온프레미스 영향 없음)

### H3 — Backup restore decompression bomb
- `apps/backend/api/v1/admin/backup.py` — tar 멤버 preflight loop에 `_MAX_MEMBER_BYTES = 5 GiB` + `_MAX_EXTRACTED_BYTES = 50 GiB` cap 추가
- 신규 예외 `_DecompressionBombError` → 413 + RFC 7807 problem (`https://docs.trustedoss.io/errors/backup-decompression-bomb`)
- 기존 `extractall(filter='data')` (path traversal 가드) 유지 — Info I1

### M2 — Demo super-admin 비밀번호 하드코드
- `apps/backend/scripts/seed_demo.py` — `_DEMO_PASSWORD = "DemoAdmin2026!"` 모듈 상수 → `_resolve_demo_password()` 런타임 함수 (CLAUDE.md core rule #11)
- 우선순위: `DEMO_SUPER_ADMIN_PASSWORD` env (≥12자) → dev/demo는 `secrets.token_urlsafe(18)` + JSON stdout 1회 출력 → production 거부
- 5 new unit tests in `tests/unit/test_seed_demo_env_guard.py`

### M3 — In-app notification opt-out drift
- `apps/backend/api/v1/users_me.py` — PUT `/v1/users/me/notification-prefs`에서 `in_app_enabled=false` 시 422 + RFC 7807 problem (`urn:trustedoss:problem:notification_in_app_required`)
- 기존 3 테스트 (`test_put_prefs_round_trips_all_toggles`, `test_put_prefs_extra_user_id_in_body_is_ignored`, `test_put_prefs_isolated_between_users`)에서 `in_app_enabled=False`를 `True`로 갱신
- 신규 2 테스트 (`test_put_prefs_in_app_disabled_returns_422`, `test_put_prefs_in_app_disabled_does_not_persist`)

## 4. 측정 가능한 결과

- **신규 테스트**: 22개 (14 config + 5 seed_demo + 1 oauth + 2 in-app)
- **변경된 테스트**: 3개 (notification prefs)
- **Lint**: ruff clean
- **Typecheck**: mypy clean (한 차례 fix-forward — `quote_plus(password)` 인자 타입 narrowing)
- **CI**: 11/11 green

## 5. 자율 실행 중 발생한 이슈와 처리

| 이슈 | 처리 |
|------|------|
| backend-developer 에이전트가 2회 연속 timeout (모두 ~18분) — `Stream idle timeout - partial response received`. 첫 번째는 docstring만 갱신, 두 번째는 아예 변경 없음 | main 세션이 직접 4개 fix 적용. 다음 세션부터는 단일 에이전트에 큰 fix 묶음 위임 대신 chunk 단위 (1 fix per agent call)로 분할 권고 |
| 첫 CI 실행에서 `core/config.py:84` mypy `arg-type` 실패 — `quote_plus(password)` 인자가 `str | None` | fix-forward commit (`6613562`)으로 `assert ... is not None` 4개 추가하여 narrowing |
| 로컬 환경 `aiosmtplib` 부재로 `test_admin_backup_api.py` + `test_users_me_notification_prefs_api.py` 통합 테스트 collection error | CI에서 검증 (CI runner는 모두 설치됨). 모두 PASS |

## 6. backlog 갱신

`docs/chore-backlog.md`:
- ~~Chore O~~ ✅ PR #36 (2026-05-09) — 처리 결과 요약 + 이월 항목 명시
- 신규 항목 4개 등재:
  - **Chore Q** — Cloud Run backend 외부 노출 가드 (M1 이월)
  - **Chore R** — Backup upload 이름 충돌 + restore.sh confirm flag (M4 + M5 이월)
  - **Chore S** — Notification.link 스킴 검증 + Memorystore AUTH (L1 + L2 이월)
  - **Chore T** — Audit 로그 PII 마스킹 + provider_user_id_hash HMAC salt (L3 + L4 이월)

## 7. follow-ups

- 위 Chore Q/R/S/T 4건 — 후속 chore PR로 처리. 우선순위는 "Demo SaaS 운영 단계 진입 시" Chore Q 부터.
- 본 PR에서 H3에 대한 통합 테스트 (decompression bomb fixture)는 로컬 환경 제약으로 추가 못함. follow-up 필요 — `test_admin_backup_api.py`에 작은 cap을 monkeypatch로 적용한 fixture 기반 테스트 1~2개 추가 권장.
- backend-developer 에이전트의 1M context 환경에서도 timeout 발생. 분할 위임 또는 mid-task checkpoint 패턴 검토 필요.

## 8. 다음 세션

§3 우선순위 표대로 **세션 4 — Chore N (UAT 시나리오 갱신)** 진행. PR #28~#33 반영 ~12개 새 시나리오 추가, docs-only PR.
