# 다음 세션 시작 prompt — Post-GA Cleanup (v2.0.0 후속)

> v2.0.0 정식 태그 + chore-backlog 11항목 머지 완료 (2026-05-09).
> 본 파일을 새 세션 첫 메시지에 그대로 붙여넣으면 정확한 컨텍스트로 시작.
> **세션 중간에 끊겨도 본 파일 + main HEAD + chore-backlog.md 만으로 이어 진행 가능.**

---

TrustedOSS Portal v2 작업을 `~/projects/trustedoss-portal/` 에서 이어서 진행한다.

## 0. 현재 상태 (2026-05-09 기준)

- main HEAD = `f0a86e1` (docs: chore-backlog + handoff)
- 누적 머지: PR #1 ~ #33 + tag `v2.0.0`
- 자율 실행 6 세션 완료. 본 prompt 의 세션 1~6 은 **post-GA 정리** 단계.

```bash
git log --oneline -3
# f0a86e1 docs: chore-backlog + handoff for PRs #28-#33 + v2.0.0 release
# f684ed3 feat: Demo SaaS bundle — GCP Terraform + OAuth identity unlink (Chore F + G) (#33)
# df54562 feat: Chore A2 — in-app notification center (backend + frontend) (#32)
```

세션 1 핸드오프: `docs/sessions/2026-05-09-all-chores-complete.md`

## 1. 단일 진실

- `docs/chore-backlog.md` — 잔여 chore 목록. **Chore E, L2, M, N, O, P** 가 미처리.
- `docs/sessions/2026-05-09-all-chores-complete.md` — 직전 6 세션 통합 핸드오프.
- `CLAUDE.md` — 핵심 규칙 13개 + 품질·보안 표준. **§8 "문서 동행" 위반 상태** (PR #28~#33 신규 기능 가이드 부재) → 세션 1 의 우선 처리 동기.
- `MEMORY.md` — 사용자 피드백 + 프로젝트 상태 인덱스.
- 본 파일 — **세션 1~6 의 단일 진실**. 각 세션 prompt 가 self-contained.

## 2. 시작 시 검증 (반드시)

```bash
# 환경 + 진행 상태 확인
git status                                            # working tree clean (untracked는 무시)
git checkout main && git pull --ff-only               # 최신 반영
git log --oneline -3                                  # 최신 commit 확인
gh run list --branch main --limit 3                   # main CI 상태
cat docs/chore-backlog.md | grep -E "^### " | head -20   # 잔여 chore 목록
ls docs/sessions/ | tail -5                           # 직전 세션 핸드오프 확인
```

main 의 untracked 잔여 (무시):
- `.claude/scheduled_tasks.lock`
- `apps/frontend/@/` (artifact 폴더, .gitignore 추가 권장)
- `docs/review-binaryanalysis-ng.md`, `docs/sessions/2026-05-08-uat-manual-test-scenarios.md`, `docs/sessions/_next-session-prompt-phase4-pr15-plus-chore-pr9.md`

## 3. 진행 우선순위 (전체 상)

| 세션 | Chore | 작업 | PR 묶음 | 추정 |
|------|-------|------|---------|------|
| 1 | M (신규) | 문서화 회수 (user/admin/contributor 가이드 + KO 미러) | 1 PR | 1.5 세션 |
| 2 | L2 | Webhook fixture HMAC drift 13 xfail 정리 | 1 PR | 0.25 세션 |
| 3 | O (신규) | security-reviewer pass (PR #29/#32/#33) | 1 PR | 0.5~1 세션 |
| 4 | N (신규) | UAT 시나리오 갱신 (PR #28~#33 반영) | 1 PR (docs-only) | 0.5 세션 |
| 5 | E | install/restore fresh-Linux UAT + shellcheck | 1 PR (operator) | 0.5 세션 |
| 6 | P (신규) | Trivy HIGH hard-fail + worker-image base refresh | 1 PR | 1 세션 |

**총합 ~4~4.5 세션.** 각 세션은 독립 PR. 중간에 끊기면 그 세션의 chore 만 backlog 에 그대로 남고, 머지된 항목은 ~~취소선~~ 처리됨.

---

## 세션 1 — Chore M: 문서화 회수 (`docs-site/`)

**브랜치**: `chore/docs-refresh-post-ga`

**자율 실행 프로토콜 그대로 따른다** (`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜"):
```
LOOP:
  1. 구현 (에이전트 배치 또는 직접)
  2. 로컬 빌드 검증 (`cd docs-site && npm run build`)
  3. PR 생성 + CI 모니터링
  4. CI 실패 시 fix + push (최대 3회 retry)
  5. 머지 후 chore-backlog.md 에서 항목 ~~취소선~~ + PR # 추가
```

### 1.1 작업 범위

PR #28~#33 신규 기능 가이드 + KO 미러 누락 회수. CLAUDE.md §8 "문서 동행" 회복.

신규 작성 (EN + KO 동시):

**user-guide/**
- `auth-and-profile.md` — 로그인 (이메일+비밀번호), 비밀번호 찾기 (`/forgot-password`), 비밀번호 재설정 (`/reset-password?token=...`), OAuth 로그인 (GitHub / Google), `/profile` Connected Accounts (Unlink + last-method 보호 안내).
- `notifications.md` — 헤더 벨 아이콘 동작, `/notifications` 페이지 (Inbox + Preferences), 채널 ON/OFF (email / slack / teams / in-app — in-app 비활성 불가 안내), 60초 polling 동작 (탭 hidden 시 정지).
- `integrations.md` — `/integrations` 페이지, API Key 생성 (scope: org/team/project) + 평문 1회 노출 + 폐기, Webhook URL 안내 (GitHub HMAC + GitLab token + project's `webhook_secret`).

**admin-guide/**
- `backup-and-restore.md` 갱신 — 기존 CLI 스크립트 절차 유지 + `/admin/backup` UI 사용법 (수동 트리거 / 다운로드 / 업로드+복원 typing-gate / auto-* 7일 retention) 신규 섹션 추가.
- `api-keys.md` 갱신 — `/integrations` UI 사용 흐름 추가 (백엔드 관점만 있는 기존 내용에 사용자 perspective 결합).

**contributor-guide/** (NEW directory)
- `getting-started.md` — 로컬 설정 (docker-compose dev, 의존성, 첫 PR 흐름).
- `coding-standards.md` — TypeScript strict, Pydantic v2, Alembic forward-only, RFC 7807 problem-details, structlog JSON, i18n 키 패턴 (EN + KO 미러 강제, `i18next-parser` drift gate), `# nosec`/`# nosemgrep` 정당화 패턴.
- `testing-guide.md` — pytest 셋업, Playwright 하네스 (`PortalPage`), 적대적 input parametrize 의무 (memory `feedback_adversarial_input_parametrize`), 단위 ≥80% 커버리지.
- `agent-team.md` — Producer-Reviewer / Fan-out / Pipeline 패턴 + 보안 코드 security-reviewer 의무 트리거 조건 (인증 / API Key / DT / OAuth / 빌드 게이트 / backup destructive flow).

**ci-integration/** (KO 미러)
- `github-actions.ko.md`, `gitlab-ci.ko.md`, `jenkins.ko.md`, `webhooks.ko.md` 신규 (현재 KO 부재).

**reference/** (KO 미러)
- `architecture.ko.md`, `api-overview.ko.md`, `env-variables.ko.md` 신규.

**installation/**
- `gcp-deploy.md` 와 `gcp-deploy.ko.md` 를 `docs/installation/` → `docs-site/docs/installation/` 으로 **이동** (또는 복사 + sidebar 등록). 현재 docs-site 외부 → Docusaurus 게시 안 됨.

**intro.md** + **`docs-site/docs/release-notes/v2.0.0.md`** (NEW)
- intro 에 v2.0.0 GA 배지 + "What's new in 2.0.0" 단락.
- `release-notes/v2.0.0.md` — CHANGELOG 의 [2.0.0] 섹션 복사 + 사용자 친화 예시.

`docs-site/sidebars.ts` 갱신 — 새 카테고리 + 항목 등록.

### 1.2 에이전트 위임 (병렬)

**doc-writer 에이전트 1차 (EN, 신규 + 갱신)**:
```
한 prompt 안에:
- user-guide/{auth-and-profile, notifications, integrations}.md 신규 작성
- admin-guide/{backup-and-restore, api-keys}.md 갱신
- contributor-guide/{getting-started, coding-standards, testing-guide, agent-team}.md 신규
- intro.md GA 배지 + What's new 단락
- release-notes/v2.0.0.md 신규
- sidebars.ts 갱신
- gcp-deploy{,.ko}.md 를 docs-site/docs/installation/ 로 이동
```

각 가이드는 600~1000 단어 + 스크린샷 placeholder (`![<설명>](./img/<file>.png)` — 실제 이미지는 사용자가 추가; placeholder 만 둠).

**i18n-specialist 에이전트 (KO 미러)** — 위 EN 신규 작성 직후:
```
- user-guide/{auth-and-profile,notifications,integrations}.ko.md
- admin-guide/{backup-and-restore,api-keys}.md KO 동기화
- contributor-guide/* KO 미러 (4 파일)
- ci-integration/* KO 신규 (4 파일)
- reference/* KO 신규 (3 파일)
- intro.md, release-notes/v2.0.0.md KO 미러
```

용어집 (`docs/glossary.md`) 의 EN/KO term 일관성 유지.

### 1.3 검증

```bash
cd docs-site
npm install                                   # 첫 실행시
npm run build                                 # Docusaurus 빌드 — 깨진 링크 / sidebar mismatch 잡음
npm run start                                 # 로컬 미리보기 (선택)
```

빌드 성공 + 모든 EN 페이지에 KO 페어가 있어야 한다 (Docusaurus 가 missing locale 페이지를 EN 으로 fall back 하지만 본 PR 은 100% mirror 강제).

### 1.4 PR + 머지

```bash
git checkout -b chore/docs-refresh-post-ga
# (에이전트 작업)
git add docs-site/ docs/installation/ docs/chore-backlog.md
git commit -m "docs: refresh user/admin/contributor guide for v2.0.0 (Chore M)"
git push -u origin chore/docs-refresh-post-ga
gh pr create --title "docs: post-GA documentation refresh (Chore M)" --body "..."
# CI 모니터링 → 머지 → chore-backlog.md 에 ~~Chore M~~ 표시
```

CI 가 `docs-site` 빌드 step 을 가지지 않을 수 있다 — 만약 없으면 `.github/workflows/docs.yml` 에 `npm run build` step 추가 (별도 commit 으로 분리).

### 1.5 세션 종료 시

`docs/sessions/2026-05-XX-chore-m-docs-refresh.md` 핸드오프 작성 + chore-backlog.md 의 Chore M 항목에 ~~취소선~~ + PR # + commit sha.

---

## 세션 2 — Chore L2: Webhook fixture HMAC drift fix

**브랜치**: `chore/phase5-pr16-webhook-fixture-fix`

### 2.1 작업 범위

PR #31 의 13 xfail 정리:
- `apps/backend/tests/integration/test_webhooks_github.py` — 6 xfail
- `apps/backend/tests/integration/test_webhooks_gitlab.py` — 6 xfail
- `apps/backend/tests/integration/test_api_keys_api.py` — 1 xfail (`test_get_developer_does_not_see_foreign_team_keys`)

원인:
1. **Webhook fixture (12 tests)**: 픽스처가 `project.webhook_secret = secrets.token_urlsafe(32)` 로 setattr 만 하고 `await session.commit()` 누락 → backend 가 DB 에서 원래 값을 읽어 HMAC 검증 → 401.
2. **API Key (1 test)**: POST validation 시그니처 정렬 — 422 vs 200. 실제 backend 동작 vs 테스트 기대 mismatch.

### 2.2 fix 절차

```bash
git checkout -b chore/phase5-pr16-webhook-fixture-fix

# 1. fixture 에 commit 추가 — `_make_project_with_secret` 같은 패턴 찾아서 setattr 직후 await session.commit() / session.refresh().
# 2. 각 test 함수 위 @pytest.mark.xfail 데코레이터 제거.
# 3. test_get_developer_does_not_see_foreign_team_keys: backend 실제 응답 확인 후 expected status code / payload 정렬.
# 4. 회귀 가드: strict=True 로 가도록 모든 xfail 제거 후 strict=False 가 새로 추가될 때 즉시 실패하게.

cd apps/backend
python3 -m pytest tests/integration/test_webhooks_github.py tests/integration/test_webhooks_gitlab.py tests/integration/test_api_keys_api.py -v
# 13 tests previously xfail → all pass
```

로컬 backend (docker-compose dev) 가 살아있어야 integration tests 동작. 안 되면 `docker-compose -f docker-compose.dev.yml up -d` 후 진행.

### 2.3 PR + 머지

PR title: `test: unxfail 13 webhook + api-key tests (Chore L2)`
chore-backlog.md 의 Chore L2 항목에 ~~취소선~~ + PR # 추가.

---

## 세션 3 — Chore O: security-reviewer pass

**브랜치**: `chore/security-reviewer-pass`

### 3.1 작업 범위

CLAUDE.md §7 "핵심 보안/안정성 코드는 Producer-Reviewer 패턴으로 security-reviewer 검증 후 머지" 규약을 자율 실행에서 시간 압박으로 생략. 회수.

대상:
- **PR #29 (Chore D — backup automation)**: backup tar extraction (path traversal / symlink 공격), restore destructive flow (super_admin auth + X-Confirm-Restore + 10 GB cap 가드), Celery task subprocess argv injection, `BACKUP_DIR` env 변조.
- **PR #32 (Chore A2 — notifications)**: dispatcher fan-out 의 `notification_preferences` lookup race, 알림 read 권한 (다른 사용자 알림 mark-read 가능성), `target_id` UUID validation, 헤더 벨 polling DoS amplification.
- **PR #33 (Chore G — OAuth unlink)**: last-method 가드 race (concurrent unlink), `provider_user_id_hash` salt 부재 (rainbow attack), audit row PII 누출, IDOR (식별자 순회로 다른 사용자 identity 발견).

### 3.2 에이전트 위임

**security-reviewer 에이전트** (1 회):
```
prompt:
- 위 3 PR 의 변경 범위를 PR diff (gh pr view <num> --patch) 로 받아 OWASP Top 10 + IDOR / BOLA / Race / Audit log PII 관점에서 검토.
- 출력: Critical / High / Medium / Low / Info finding 표 + 각 finding 별 file:line + 1줄 권고 + RFC 7807 type URI 제안 (해당 시).
- 코드 수정 X. 보고만.
```

산출 example:
```
| ID | Severity | 위치 | 요약 | 처리 |
|----|----------|------|------|------|
| C1 | Critical | tasks/backup.py:xxx | extractall filter='data' Python 3.11 fallback 부재 | 본 PR 수정 |
| H1 | High | services/oauth_identity_service.py:xxx | last-method 검사와 DELETE 사이 race (TOCTOU) | 본 PR 수정 — SELECT FOR UPDATE 패턴 |
| M1 | Medium | api/v1/notifications.py:xxx | mark_read on someone else's id → 404 (existence-hide 미적용) | 본 PR 수정 |
```

### 3.3 fix 적용

Critical / High 는 **본 PR 에서 즉시 수정**. Medium 이하는 별도 backlog 또는 인라인 fix (시간 여유에 따라).

각 fix 에 단위/통합 테스트 추가 — 적대적 input parametrize (memory `feedback_adversarial_input_parametrize`).

### 3.4 PR + 머지

PR title: `fix(security): post-merge review of PR #29/#32/#33 (Chore O)`
PR body: security-reviewer 보고서 그대로 inline + 처리 결과 표.
chore-backlog.md 에 Chore O 항목 추가 후 ~~취소선~~ 처리.

---

## 세션 4 — Chore N: UAT 시나리오 갱신

**브랜치**: `chore/uat-scenarios-v2.0.0`

### 4.1 작업 범위

`docs/sessions/2026-05-08-uat-manual-test-scenarios.md` 가 PR #14 시점 작성 — 6 개월간의 변경 미반영. v2.0.0 GA 사용자 검증을 위한 시나리오 추가.

신규 시나리오 추가 (기존 9개 + 신규 ~12개):

| 시나리오 | 출처 PR |
|----------|---------|
| 비밀번호 찾기 + 재설정 (이메일 토큰 → 새 비밀번호) | #28 |
| OAuth 로그인 (GitHub) — 신규 사용자 자동 가입 | #28 |
| OAuth 로그인 (Google) — 기존 사용자 식별자 매칭 | #28 |
| `/profile` Unlink GitHub — last-method 보호 시나리오 (password 없는 사용자가 단일 OAuth 만 → 409 alert) | #33 |
| `/integrations` API Key 생성 + 평문 1회 노출 + 폐기 | #28 |
| `/admin/backup` 수동 백업 트리거 + 다운로드 | #29 |
| `/admin/backup` 업로드+복원 typing-gate (오타 → 버튼 비활성) | #29 |
| 알림 벨 미읽음 카운트 99+ → 0 전이 | #32 |
| `/notifications` 페이지 Inbox 클릭 → mark-read + link 이동 | #32 |
| 알림 채널 ON/OFF (email 끄기 + slack 켜기) → 새 알림 발생 시 채널별 발송 검증 | #32 |
| WebSocket 탭 이탈 후 30초 → 복귀 시 즉시 재연결 + 진행률 동기화 | #29 |
| GCP Demo SaaS 첫 배포 (`terraform apply` + seed_demo + 로그인) | #33 |
| EN/KO 언어 토글 — 모든 신규 페이지에서 KO 텍스트 정상 노출 | #28~#33 |

### 4.2 작성 형식

기존 `2026-05-08-uat-manual-test-scenarios.md` 의 시나리오 1~9 형식 그대로 (단계 + 기대결과 + 스크린샷 placeholder). 추가 시나리오는 `docs/sessions/2026-05-XX-uat-v2.0.0-scenarios.md` 에 작성하거나 기존 파일에 append 후 헤더 갱신.

문서만 변경. 코드 변경 없음.

### 4.3 PR + 머지

PR title: `docs: UAT scenarios for v2.0.0 (Chore N)`
docs-only PR 이므로 lint/typecheck/test 영향 없음 — CI markdown lint 만 통과.
chore-backlog.md 에 Chore N 항목 추가 후 ~~취소선~~ 처리.

---

## 세션 5 — Chore E: install/restore fresh-Linux UAT

**브랜치**: `chore/install-restore-uat`

### 5.1 작업 범위

운영자 환경 필요 (fresh Ubuntu 22.04 LTS / Rocky Linux 9 VM 또는 GitHub Actions Linux runner).

체크리스트:
1. `bash scripts/install.sh` end-to-end — fresh VM 에서 docker-compose 설치 → `.env` 생성 → migrate → seed → first login.
2. `bash scripts/backup.sh` → 다른 머신으로 tar 전송 → `bash scripts/restore.sh` → 데이터 복원 검증.
3. 멀티 PostgreSQL 버전 마이그레이션 (PG 16 dump → PG 17 restore) — 운영자가 보통 16 → 17 업그레이드 시나리오.
4. shellcheck CI 게이트 추가 — 현재 syntax-only check 만 있음. `.github/workflows/ci.yml` 에 `shellcheck scripts/*.sh` step 추가.

### 5.2 운영자 가이드

본 chore 의 일부 단계는 자동화 어려움 (fresh VM 프로비저닝). 다음 둘 중 하나:

**옵션 A — GitHub Actions Linux runner 활용 (자동)**:
- `.github/workflows/install-uat.yml` 신규 — manual `workflow_dispatch` 트리거 + Ubuntu 22.04 runner.
- runner 에서 `bash scripts/install.sh --no-prompt` 실행 + `curl http://localhost:8000/health` 응답 확인.

**옵션 B — 운영자 수동 (반자동)**:
- `docs/installation/uat-checklist.md` (NEW) — 체크리스트 + 예상 결과 + 디버그 명령.
- 운영자가 사내 VM 에서 실행 후 결과 PR 댓글로 회신.

권장: **옵션 A + B 병행**. CI 자동화는 회귀 가드, 운영자 수동은 실제 환경 검증.

### 5.3 PR + 머지

PR title: `test: install/restore UAT + shellcheck CI (Chore E)`
체크리스트 통과 + CI green → 머지.

---

## 세션 6 — Chore P: Trivy HIGH hard-fail + worker-image refresh

**브랜치**: `chore/phase8-worker-image-refresh`

### 6.1 작업 범위

PR #30 (Chore H) 에서 Trivy CRITICAL 만 hard-fail, HIGH 는 advisory 유지 — Phase 8 hardening backlog 로 표시. 본 chore 가 그 회수.

작업:
1. **`apps/backend/Dockerfile.worker` base image 업그레이드**:
   - Python 3.12.7 → 최신 stable patch (3.12.x)
   - Go SDK (cdxgen 호환 버전 — CVE-2025-68121 수정 포함)
   - Temurin JRE 21 → 최신 patch
   - Trivy 0.70.0 → 최신 stable
   - cdxgen / ORT / 기타 도구 버전 정합성 확인
2. **`.trivyignore` 정비** — 새 image scan 후 잔여 finding 의 reach 분석 갱신, 해소된 CVE 항목 제거.
3. **`.github/workflows/ci.yml` Trivy 스텝 변경**: `Trivy scan (HARD FAIL on CRITICAL)` 와 별도로 `Trivy scan (HARD FAIL on HIGH)` 스텝 추가 (또는 단일 스텝에 `severity=CRITICAL,HIGH` 결합).
4. 회귀 테스트: PR 발행 시 Trivy 가 HIGH 잔여 0 으로 green 통과.

### 6.2 worker image rebuild

```bash
cd apps/backend
docker build -f Dockerfile.worker -t trustedoss/backend-worker:dev .
trivy image --severity CRITICAL,HIGH trustedoss/backend-worker:dev
# 출력 finding 가 모두 .trivyignore 에 잡히거나 의존성 bump 로 해소되어야 한다.
```

### 6.3 위험성

- ORT / cdxgen / Trivy 버전 bump 가 SBOM/스캔 출력 형식을 깰 수 있음. 회귀:
  - `apps/backend/tests/integration/test_scan_*.py` 통합 테스트 통과 확인.
  - dev 환경에서 `my-nodejs-app` 시드 프로젝트 스캔 → 컴포넌트 / 취약점 / 라이선스 카운트가 시드 데이터와 일치 (`docs/sessions/2026-05-08-uat-manual-test-scenarios.md` 의 expected counts).

### 6.4 PR + 머지

PR title: `chore(worker): refresh base image deps + Trivy HIGH hard-fail (Chore P)`
chore-backlog.md 에 Chore P 항목 추가 후 ~~취소선~~ 처리.

---

## 4. 자율 실행 프로토콜 (모든 세션 공통 적용)

`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜" 그대로:

```
LOOP per session:
  1. 시작 시 검증 (§2)
  2. 새 브랜치 생성 + push (-u origin)
  3. 에이전트 위임 또는 직접 구현
  4. 로컬 검증 (lint + typecheck + test + i18n:check + docs build 해당 시)
  5. PR 생성 + CI 모니터링 (background polling, 사용자 task-notification 으로 깨움)
  6. CI 실패 시 fix + push (최대 3회 retry → 초과 시 BLOCKED 표시 + 다음 세션 이월)
  7. 머지 후 chore-backlog.md 에 ~~취소선~~ + PR # + commit sha
  8. 세션 종료 핸드오프 노트 작성 → 본 prompt 의 "현재 상태" 섹션 갱신
```

PR 분리 원칙: 한 PR = 한 chore. 두 chore 묶어서 머지하지 말 것 (revert 비용 증가).

## 5. 세션 종료 체크리스트 (매 세션 끝 반드시)

- [ ] 작업한 chore 가 머지됐는지 (`gh pr view <num> --json mergedAt`)
- [ ] main 으로 checkout + pull 완료
- [ ] `docs/chore-backlog.md` 에 ~~취소선~~ + PR # + commit sha 추가
- [ ] `docs/sessions/2026-05-XX-<chore>-<topic>.md` 핸드오프 노트 작성 (직전 세션 형식 참고)
- [ ] 본 파일 (`_next-session-prompt-post-ga-cleanup.md`) 의 "현재 상태" 섹션 — main HEAD + 최근 commits + 처리율 갱신
- [ ] BLOCKED 항목 발생 시 본 파일 + chore-backlog 양쪽에 BLOCKED 표시 + 차단 사유 + 해소 조건

## 6. 세션 끊김 시 복구 절차

새 세션 시작:
1. 본 파일을 첫 메시지로 그대로 붙여넣음.
2. **§2 시작 시 검증** 실행 → main HEAD 확인.
3. `cat docs/chore-backlog.md | grep -E "✅|~~Chore"` 로 처리 완료 항목 파악.
4. §3 우선순위 표에서 **첫 번째 미처리 chore** 선택.
5. 해당 세션 prompt 그대로 실행.

만약 직전 세션이 **PR 생성 후 CI 대기 중에 끊겼다면**:
1. `gh run list --branch <branch> --limit 3` 으로 CI 상태 확인.
2. CI green → 머지 진행, CI fail → log-failed 분석 후 fix.
3. 어떤 브랜치에서 작업 중이었는지 모르면 `git branch -a | grep -v main` 으로 후보 확인.

만약 **로컬 working tree 가 dirty 상태로 끊겼다면**:
1. `git status --short` 로 변경 사항 확인.
2. 의도한 변경이면 commit + push, 아니면 `git stash` 또는 `git restore` 로 정리.
3. **untracked `apps/frontend/@/`, `.claude/scheduled_tasks.lock`, `docs/review-binaryanalysis-ng.md` 등은 무시** (§2 참조).

## 7. 참조 문서

- `CLAUDE.md` — 핵심 규칙
- `docs/v2-execution-plan.md` — Phase 별 상세
- `docs/autonomous-execution-plan.md` — 자율 실행 프로토콜
- `docs/chore-backlog.md` — **본 세션의 단일 진실** (잔여 chore + 처리 결과)
- `docs/sessions/2026-05-09-all-chores-complete.md` — 직전 6 세션 통합 핸드오프
- `MEMORY.md` — 장기 기억

## 8. 주의사항

- **추측 금지**: 본 prompt 의 각 세션 작업 범위를 그대로 따른다. 새 기능 임의 추가 X.
- **하나씩 머지**: 각 chore 는 독립 PR. revert 용이성 우선.
- **CI 모니터링**: PR 생성 후 `until` 패턴으로 background polling. polling 명령은 `--commit <sha>` 필터를 쓰지 말 것 (timing race 로 일찍 종료) — `--branch <name>` + `select(.status == "in_progress" or "queued")` 패턴 사용.
- **에이전트 한도**: 한 prompt 당 한 에이전트는 약 80~150 도구 호출. 분량 큰 작업 (세션 1) 은 EN / KO 따로 위임.
- **destructive 작업 사용자 승인**: `git tag` / `gh release create` / `terraform apply` 같은 published-artifact 가 발생하면 사용자에게 명시 위임 확인 (memory `feedback_push_pr_authorized`).
- **EN / KO 동시 출시**: 새 가이드를 EN 만 쓰고 KO 미루지 말 것 (CLAUDE.md 정책 위반).
- **세션 내 1 PR 원칙**: 자율 실행 시간이 너무 길어지면 (한도 초과 위험) 다음 세션으로 이월.

본 작업 예상 시간: 세션당 0.5~1.5 시간, 6 세션 전체 약 5~7 시간.
