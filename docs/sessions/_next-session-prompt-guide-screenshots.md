# 다음 세션 시작 prompt — Guide Screenshot Capture Automation

> docs-site 의 사용자 / 관리자 / 컨트리뷰터 / 설치 가이드 각 페이지에 적합한 화면 캡처 이미지를 Playwright 자동화로 생성·삽입.
> 한 세션 자율 실행으로 Phase 1 → 2 → 3 순차 처리. 각 Phase = 한 머지 가능한 PR.
> 본 파일을 새 세션 첫 메시지에 그대로 붙여넣으면 정확한 컨텍스트로 시작.
> **세션 중간에 끊겨도 본 파일 + main HEAD + chore-backlog.md 만으로 이어 진행 가능.**

---

TrustedOSS Portal v2 작업을 `~/projects/trustedoss-portal/` 에서 이어서 진행한다.

## 0. 현재 상태 (2026-05-10 기준)

- main HEAD = `5e6ca27` (PR #51 머지 — Post-Walkthrough Stabilization 핸드오프 정리)
- 누적 머지: PR #1 ~ #51 + tag `v2.0.0`
- 직전 세션 통합 핸드오프: `docs/sessions/2026-05-10-stabilization-cad-bundle.md`
- 본 prompt 의 세션 1~3 은 **가이드 화면 캡처 생성** 단계

```bash
git log --oneline -3
# 5e6ca27 docs(handoff): post-walkthrough stabilization C+A+D bundle complete (#51)
# 4401a8a chore(docs-site): commit package-lock.json so Deploy Docs CI runs (#50)
# defc7e2 fix: audit immutability + restore 412 + CSV BOM (A bundle) (#48)
```

본 작업의 단일 목표:
> **사용자 / 관리자 / 컨트리뷰터 가이드의 각 단락이 자명한 화면 캡처를 동반하도록 한다.**

현재 상태 (스캔 결과):
- **15 가이드 페이지** (user-guide 9, admin-guide 6) + **4 contributor-guide** + **4 installation** + **reference 등** = 22 ~ 25 페이지
- **이미지 reference 10건** (EN), 동일 path 10건 (KO) — 모두 **1x1 placeholder PNG** (`file ... → PNG image data, 1 x 1`)
- **`docs-site/i18n/ko/.../img/` 디렉토리 부재** → KO 의 모든 이미지 참조는 현재 404 (broken). Docusaurus build 가 경고를 출력하지 않아 그동안 미발견.
- **이미지가 없는 단락 다수** (`projects`, `scans`, `components-and-licenses`, `vulnerabilities`, `approvals`, `sbom`, admin `users-and-teams`, admin `audit-log`, admin `dt-connector`, admin `disk-and-health`, admin `api-keys` 등 거의 전 페이지)

## 1. 단일 진실

- `docs-site/docs/` (EN 가이드) — 본 작업의 단락 inventory 출처
- `docs-site/i18n/ko/docusaurus-plugin-content-docs/current/` (KO 미러)
- `apps/frontend/tests/_harness/` — 기존 Playwright 하네스 (재사용 가능: `AuthHarness`, `PortalPage`, `AdminBackupHarness`, `AdminUsersHarness`, `AdminTeamsHarness`, `AdminAuditHarness`, `AdminDTHarness`, `AdminDiskHarness`, `AdminHealthHarness`, `AdminScansHarness`, `NotificationsHarness`, `ProfileHarness`)
- `apps/backend/scripts/seed_e2e_user.py` — fixture 시드 (`--super-admin --with-scan --component-count 50 --vulnerability-count 30 --with-obligations --with-oauth-identity github`)
- `docker-compose.dev.yml` — Vite proxy + dev DB (PR #47 정리 후 health 양호 가정)
- `Makefile` (PR #47) — `make dev-up` / `make dev-rebuild-worker` / `make dev-reset-rebuild`
- `CLAUDE.md` — 핵심 규칙 + 품질·보안 표준 (변경 X)
- 본 파일 — **세션 1~3 의 단일 진실**. 각 세션 prompt 가 self-contained.

## 2. 시작 시 검증 (반드시)

```bash
# 환경 + 진행 상태
git status                                            # working tree clean (untracked는 무시)
git checkout main && git pull --ff-only               # 최신 반영
git log --oneline -3                                  # 5e6ca27 가 HEAD 인지 확인
gh run list --branch main --limit 3                   # main CI green 확인

# Dev stack 가용 (이미지 캡처는 라이브 SPA 가 필수)
docker-compose -f docker-compose.dev.yml ps           # 6/6 healthy 가 이상적
# 없거나 unhealthy 면:
make dev-reset-rebuild                                # PR #47 의 헬퍼 (dev-reset.sh + worker rebuild + seed)

# 기존 이미지 reference 카탈로그
grep -rEn '!\[[^\]]*\]\([^)]+\.(png|jpg|jpeg|gif|svg|webp)\)' docs-site/docs/  | tee /tmp/img-refs-en.txt
grep -rEn '!\[[^\]]*\]\([^)]+\.(png|jpg|jpeg|gif|svg|webp)\)' docs-site/i18n/  | tee /tmp/img-refs-ko.txt
find docs-site/docs -type f \( -name '*.png' -o -name '*.jpg' \)              | tee /tmp/img-files-en.txt
file docs-site/docs/user-guide/img/auth-login.png                              # placeholder 확인 (1 x 1 RGBA)

# Playwright 하네스 가용
ls apps/frontend/tests/_harness/                      # 12 + 하네스
cat apps/frontend/playwright.config.ts | head -30
```

main 의 untracked 잔여 (무시):
- `.claude/scheduled_tasks.lock`
- `apps/frontend/@/` (artifact 폴더)
- `docs/review-binaryanalysis-ng.md`
- `docs/sessions/_next-session-prompt-guide-screenshots.md` (본 파일 — push 후 tracked)

## 3. 진행 우선순위 (전체 상)

| 세션 | 묶음 | 작업 | PR | 추정 | 의존 |
|------|------|------|-----|------|------|
| 1 | **Infra** — capture 스크립트 + 1 페이지 PoC | Playwright `tests/screenshots/capture.spec.ts` + `Makefile` `screenshots-capture` 타겟 + 디렉토리/명명 규약 + admin/backup 페이지 4~6 컷으로 검증 | 1 PR `chore/screenshot-capture-infra` | 1 세션 | 없음 |
| 2 | **EN 일괄** — 22 페이지 단락 단위 캡처 + 마크다운 삽입 | 18~22 페이지 × 2~6 컷 = ~80 컷 캡처, EN 마크다운에 `![…](/img/screenshots/<page>-<slug>.png)` 삽입 | 1 PR `chore/screenshot-en-guides` | 1 세션 | 세션 1 머지 |
| 3 | **KO 미러 + 마이그레이션 + Pages 검증** | KO 마크다운에 동일 image reference 삽입 (절대 경로 → 단일 자산) + 기존 `./img/` 상대경로 10건 절대경로 마이그레이션 + 빌드 + GitHub Pages 시각 검증 | 1 PR `chore/screenshot-ko-mirror` | 0.5~1 세션 | 세션 2 머지 |

**총합 ~2.5 세션.** 각 세션은 독립 PR. 1 → 2 → 3 순차.

## 4. 핵심 결정 (사전 합의)

본 작업 진행 시 다음 8개 결정을 적용한다:

1. **이미지 저장소: `docs-site/static/img/screenshots/`** — Docusaurus `static/` 디렉토리 표준 사용. 마크다운에서 `![…](/img/screenshots/<file>.png)` 절대 경로로 참조하면 EN + KO 가 동일 자산을 공유 (KO i18n 디렉토리에 별도 복사 불필요). 기존 `docs-site/docs/{user,admin}-guide/img/` 의 10개 placeholder 도 본 위치로 마이그레이션 + 마크다운 reference 갱신.
2. **명명 규약**: `<page-slug>-<section-slug>.png` (kebab-case). 예: `admin-backup-list.png`, `admin-backup-restore-typing-gate.png`, `user-projects-list.png`, `user-projects-create-form.png`, `notifications-bell-unread-badge.png`. 슬러그가 길어도 의미 명료성 우선. 페이지 슬러그는 markdown 파일명에서 일치.
3. **이미지 포맷·해상도**: PNG. 뷰포트 `1440 × 900` (Macbook 표준). lossless. 캡처 후 `oxipng` 또는 `pngquant` 압축은 옵션 (선택지 — 결과 < 200KB 면 생략).
4. **하네스 우선**: 신규 캡처 코드는 기존 하네스 (Phase 5 PR #45 산출 + Phase 4 산출) 의 `goto*()` + `expect*()` verb 를 호출. 새 selector 도입 시에는 동일 PR 안에서 하네스에 verb 추가 (CLAUDE.md §품질·보안·운영 §2 + test-writer.md). 직접 `page.click()` / `page.locator()` 사용 금지.
5. **Locale**: 이미지는 **EN 한 벌만** 캡처 후 EN/KO 양쪽이 동일 이미지 사용. KO 텍스트가 보이는 이미지가 필요한 단락이 있으면 (예: 한글 데이터 렌더링 검증) 별도 `<file>-ko.png` 보조 캡처 — 본 세션 범위 OUT, 향후 chore.
6. **Seed 데이터**: 모든 캡처는 `seed_e2e_user.py --super-admin --with-scan --component-count 50 --vulnerability-count 30 --with-obligations --with-oauth-identity github` 으로 시드된 단일 사용자로 진행. 시나리오별 seed 분기 금지 (재현성). 시드 결과 stdout JSON 의 `email` / `password` 를 capture spec 의 한 fixture 가 공유.
7. **PII 마스킹**: 캡처 자동화는 시드 사용자 (`e2e-<suffix>@example.com`) 만 사용. 실제 PII 가 화면에 등장할 일 없음. 다만 OAuth identity row 의 `provider_user_id` 는 `e2e-<suffix>` deterministic fixture (PR #49) 라 그대로 노출 가능.
8. **시각 회귀 가드**: 본 PR 머지 시점 이후 SPA 가 시각적으로 변하면 같은 캡처가 다른 픽셀을 만들어낸다. 결정: 본 작업은 매뉴얼 정렬용 1회성 캡처. **시각 회귀 테스트 (Percy / Chromatic / pixel-diff) 는 별도 sprint 의 의제** — 본 PR 머지 시 자동 회귀 가드 도입 X. 캡처 스크립트는 `make screenshots-capture` 로 향후 운영자가 수동 재생성 가능하도록만 유지.

---

## 세션 1 — Infra: capture 스크립트 + admin/backup PoC

**브랜치**: `chore/screenshot-capture-infra`

**자율 실행 프로토콜 그대로 따른다** (`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜").

### 1.1 작업 범위 (4 sub-task)

#### S1 — capture spec scaffolding

- **신규 파일**: `apps/frontend/tests/screenshots/capture.spec.ts`
  - Playwright spec, e2e suite 와 분리 (`tests/screenshots/` 하위)
  - `playwright.config.ts` 의 `testDir` / `projects` 가 capture 디렉토리도 잡도록 옵션 추가 (또는 별도 config 파일 `playwright.screenshots.config.ts`)
  - `test.describe.serial("@screenshots …")` 로 단일 시드 사용자 컨텍스트 공유
  - 화면 단위 `test("user-projects-list — Projects toolbar + 5 rows", async ({ page }) => { … })` 구조
  - 각 test 는: 하네스로 navigate → seeded 상태 검증 → `page.screenshot({ path: 'docs-site/static/img/screenshots/<slug>.png', fullPage: false })` (뷰포트 1440x900)
  - `--update-snapshots` 같은 sentinel env 로 의도된 캡처만 실행 (실수 회귀 가드)
- **PoC 범위 (S1 안)**: `admin/backup` 페이지 4 컷
  1. `admin-backup-list.png` — 자동 + 수동 백업 행 혼합
  2. `admin-backup-trigger-toast.png` — 수동 백업 트리거 직후 toast (`backup.sh` 실패 fixme 와 무관 — toast 만 검증됨)
  3. `admin-backup-restore-modal.png` — 파일 선택 후 경고 패널 + 타이핑 게이트
  4. `admin-backup-restore-typing-gate-enabled.png` — `restore` 입력 후 Submit 활성화

#### S2 — Makefile 타겟 + 디렉토리 구조

- **수정 파일**: `Makefile` (root)
  - 신규 타겟 `screenshots-capture` — `cd apps/frontend && PYTHON=python3 npx playwright test --project=chromium tests/screenshots/`
  - 신규 타겟 `screenshots-clean` — `rm -rf docs-site/static/img/screenshots/staging/*` (CI 가 새로 만든 것 정리, 정식 자산은 보존)
  - 도움말 (`make help`) 에 두 타겟 추가
- **신규 디렉토리**: `docs-site/static/img/screenshots/` — 정식 자산 위치. `.gitkeep` 추가 (커밋 트리거)
- **신규 디렉토리**: `docs-site/static/img/screenshots/staging/` (선택) — 대체 캡처 임시 보관, 커밋 X (`.gitignore` 처리)

#### S3 — admin/backup 페이지 마크다운 갱신 (PoC)

- **수정 파일**: `docs-site/docs/admin-guide/backup-and-restore.md` (EN)
  - 기존 `![…](./img/admin-backup.png)` → `![…](/img/screenshots/admin-backup-list.png)` (절대 경로로 마이그레이션)
  - 기존 `![…](./img/admin-backup-restore.png)` → `![…](/img/screenshots/admin-backup-restore-modal.png)`
  - 신규 단락 (toast / restore typing-gate enabled 부분) 에 신규 이미지 reference 삽입
- **수정 파일**: `docs-site/i18n/ko/.../admin-guide/backup-and-restore.md` (KO)
  - EN 과 동일 절대 경로 사용 → 동일 자산 공유 (별도 KO 이미지 X)
- **삭제 후보**: `docs-site/docs/admin-guide/img/admin-backup.png`, `admin-backup-restore.png` (placeholder, 1x1) — S3 안에서 같이 정리

#### S4 — `docs/contributor-guide/getting-started.md` 에 capture 운영 섹션 추가

- 새 단락 "Regenerate guide screenshots" — `make screenshots-capture` 사용법, 시드 의존성, 단락 단위 추가 시 spec + 마크다운 동기 절차
- EN + KO 양쪽

### 1.2 검증

```bash
# Dev stack
make dev-up && sleep 20 && docker-compose -f docker-compose.dev.yml ps
# Seed
docker-compose -f docker-compose.dev.yml exec -T backend python scripts/seed_e2e_user.py \
  --super-admin --with-scan --component-count 50 --vulnerability-count 30 \
  --with-obligations --with-oauth-identity github

# Capture run
make screenshots-capture
ls -la docs-site/static/img/screenshots/admin-backup-*.png  # 4 files
file docs-site/static/img/screenshots/admin-backup-list.png  # PNG ≥ 100KB, 1440x900

# Markdown reference 정합
grep -n 'admin-backup' docs-site/docs/admin-guide/backup-and-restore.md
grep -n 'admin-backup' docs-site/i18n/ko/docusaurus-plugin-content-docs/current/admin-guide/backup-and-restore.md

# Docusaurus build
cd docs-site && npm run build
cd .. && cd docs-site && npm run build -- --locale ko

# 기존 e2e 회귀
cd apps/frontend && npm run test:e2e -- --grep-invert "@screenshots"  # 본 캡처가 e2e 매트릭스를 깨지 않음
```

### 1.3 PR + 머지

```bash
git checkout -b chore/screenshot-capture-infra
git add apps/frontend/tests/screenshots/ apps/frontend/playwright*.config.ts \
        Makefile docs-site/static/img/screenshots/ \
        docs-site/docs/admin-guide/backup-and-restore.md \
        docs-site/i18n/ko/docusaurus-plugin-content-docs/current/admin-guide/backup-and-restore.md \
        docs-site/docs/contributor-guide/getting-started.md \
        docs-site/i18n/ko/docusaurus-plugin-content-docs/current/contributor-guide/getting-started.md
git rm docs-site/docs/admin-guide/img/admin-backup.png docs-site/docs/admin-guide/img/admin-backup-restore.png
git commit -m "chore(screenshots): Playwright capture infra + admin/backup PoC"
git push -u origin chore/screenshot-capture-infra
gh pr create --title "chore(screenshots): Playwright capture infra + admin/backup PoC" --body "…"
```

### 1.4 세션 종료 시

`docs/sessions/2026-05-XX-screenshot-infra.md` 핸드오프. backlog 의 "screenshots" 항목 등재 + PR # + commit sha.

---

## 세션 2 — EN 일괄 캡처

**브랜치**: `chore/screenshot-en-guides`

**선결 조건**: 세션 1 머지 (capture spec + Makefile 타겟 + admin/backup PoC).

### 2.1 작업 범위 (페이지 단위 sub-task)

S1 의 capture spec 을 페이지별로 확장. 각 페이지의 단락을 분석해서 "이미지가 필요한 단락" 을 결정. 권장 단락 분류 휴리스틱:

- **반드시 캡처** (✅): "page mounts", "the toolbar", "click X", "the form", "filter", "pagination", "drawer", "modal", "warning panel", "empty card" 같이 화면 요소를 직접 묘사하는 단락
- **선택** (⚠️): 절차 설명 (1단계 / 2단계) → 한 단계에 1 컷
- **불필요** (X): API 응답 본문, JSON snippet, curl 예제, env 변수표, terminal 출력

#### 사용자 가이드 (9 페이지, ~50 컷)

| 페이지 | 권장 컷 |
|--------|---------|
| `auth-and-profile.md` | login / forgot / profile + Connected Accounts / OAuth unlink confirm strip / unlink success toast (5 컷, 기존 3 컷 갱신 + 2 신규) |
| `projects.md` | list with rows / create form / detail Overview / detail breadcrumb (4 컷) |
| `scans.md` | scans queue / scan detail progress / scan completed / scan failed banner (4 컷) |
| `components-and-licenses.md` | components list virtualized / component drawer / licenses donut / forbidden license badge (4 컷) |
| `vulnerabilities.md` | vuln list with severity filter / vuln drawer / VEX status dropdown / suppressed badge (4 컷) |
| `approvals.md` | approval inbox / approval detail with policy hits / approve confirm modal (3 컷) |
| `sbom.md` | SBOM tab / format selector / download in progress (3 컷) |
| `obligations.md` (만약 있으면) | obligations distribution / NOTICE 다운로드 (2 컷) |
| `notifications.md` | header bell with badge / inbox / preferences (3 컷, 기존 placeholder 갱신) |
| `integrations.md` | API keys list / API key create / Webhooks tab / Webhook test (4 컷, 기존 placeholder 갱신 + 2 신규) |

#### 관리자 가이드 (6 페이지, ~30 컷)

| 페이지 | 권장 컷 |
|--------|---------|
| `users-and-teams.md` | admin users list / role dropdown / team detail / member add / last super-admin guard alert (5 컷) |
| `dt-connector.md` | DT status card healthy / circuit-breaker open banner / orphan list / cleanup confirm (4 컷) |
| `audit-log.md` | audit list with filters / time-range filter / CSV export button / detail drawer (4 컷) |
| `disk-and-health.md` | health 4 cards (postgres/redis/celery/dt) / disk usage gauge / threshold warning state (3 컷) |
| `backup-and-restore.md` | (세션 1 PoC 에서 4 컷 처리 완료, 보강 X) |
| `api-keys.md` | API keys list / scope picker / brute-force alert / delete confirm (4 컷) |

#### 컨트리뷰터 + 설치 가이드 (8 페이지, ~10 컷)

대부분 코드/터미널 중심 — 캡처 최소화:

| 페이지 | 권장 컷 |
|--------|---------|
| `contributor-guide/getting-started.md` | dev stack `docker-compose ps` 출력 (terminal) / OpenAPI `/docs` 페이지 (2 컷) |
| `contributor-guide/testing-guide.md` | Playwright HTML report (1 컷) |
| `installation/docker-compose.md` | 처음 부팅 후 `/login` 페이지 / `install.sh` 출력 (2 컷) |
| `installation/upgrade.md` | upgrade 출력 (1 컷) |

#### Reference 페이지

대부분 표 / curl 예제 — 캡처 X.

### 2.2 검증

```bash
make screenshots-capture                              # 모든 ✅ 단락의 PNG 생성
ls docs-site/static/img/screenshots/ | wc -l           # 80~100 PNG 가까이
find docs-site/static/img/screenshots -name '*.png' -size -10k  # 10KB 미만 = 캡처 실패 의심

# Markdown reference 카운트
grep -rEn '/img/screenshots/' docs-site/docs/ | wc -l   # 80+ (페이지당 평균 4)

# Docusaurus 빌드 양쪽
cd docs-site && npm run build && npm run build -- --locale ko

# Visual sanity (수동) — Docusaurus dev 서버 띄워서 한 번 훑기
cd docs-site && npm run start
# http://localhost:3000 → 사용자 가이드 9 + 관리자 가이드 6 클릭하며 이미지 누락 / 깨짐 / 절단 확인
```

### 2.3 PR + 머지

```bash
git checkout -b chore/screenshot-en-guides
git add docs-site/static/img/screenshots/ \
        docs-site/docs/user-guide/ \
        docs-site/docs/admin-guide/ \
        docs-site/docs/contributor-guide/ \
        docs-site/docs/installation/ \
        apps/frontend/tests/screenshots/capture.spec.ts
# 기존 placeholder 일괄 정리
git rm docs-site/docs/user-guide/img/*.png docs-site/docs/admin-guide/img/*.png || true
git rmdir docs-site/docs/user-guide/img docs-site/docs/admin-guide/img || true
git commit -m "chore(screenshots): EN guide screenshots — 80+ captures"
git push -u origin chore/screenshot-en-guides
gh pr create --title "chore(screenshots): EN guide screenshots — 80+ captures" --body "…"
```

### 2.4 세션 종료 시

`docs/sessions/2026-05-XX-screenshot-en.md` 핸드오프. PNG 파일 카운트 + 페이지별 컷 수 통계.

---

## 세션 3 — KO 미러 + Pages 검증

**브랜치**: `chore/screenshot-ko-mirror`

**선결 조건**: 세션 2 머지 (모든 EN 마크다운에 절대 경로 image reference 삽입 완료).

### 3.1 작업 범위 (3 sub-task)

#### M1 — KO 마크다운에 EN reference 미러

- 세션 2 의 EN 마크다운 변경 diff 와 1:1 대응하여 KO 마크다운에 같은 절대 경로 image reference 삽입
- alt text 만 한국어 번역 (KO 매뉴얼이 이미 사용하는 톤 따라 — 예: `![Header bell with unread badge]` → `![읽지 않음 배지가 표시된 헤더 종 아이콘]`)
- 페이지 단위로 EN diff → KO mirror 가 같은 단락에 들어갔는지 줄 단위 비교 (기존 PR #43, #44 의 reverse-drift 양식 따른다)

#### M2 — 기존 `./img/...` 상대 경로 잔여 정리

- 세션 1 PoC 가 admin/backup 의 2개만 마이그레이션. 세션 2 가 EN 일괄 처리. 본 세션에서 잔여 KO 측 `./img/...` reference (사용자 가이드 user-guide notifications/integrations/auth-and-profile 의 7건) 도 절대 경로로 일괄 정정.
- 검증: `grep -rEn "\./img/" docs-site/i18n/` 가 0 lines 일 것

#### M3 — Docusaurus 빌드 + GitHub Pages 배포 검증

- `npm run build` (EN) + `npm run build -- --locale ko` 양쪽 success
- `Deploy Docs to GitHub Pages` 워크플로 머지 직후 자동 발화 — green 확인
- 배포된 https://docs.trustedoss.io/ (또는 GitHub Pages URL) 의 사용자/관리자 가이드 5~10 페이지 시각 점검 (운영자 수동, 본 PR description 에 체크리스트 명시)

### 3.2 검증

```bash
# 잔여 ./img/ reference 0 건
grep -rEn "(?:\!?\[).*\]\(\.\/img\/" docs-site/ | wc -l    # 0

# alt text 누락 체크 (![] 빈 alt 는 접근성 위반)
grep -rEn '!\[\]\([^)]+\)' docs-site/ | wc -l               # 0

# Docusaurus 양쪽 빌드
cd docs-site && rm -rf build/ .docusaurus/
npm run build
npm run build -- --locale ko

# CI green 후 GitHub Pages 시각 점검 (수동)
gh run watch                                               # Deploy Docs 잡 실시간 트래킹
```

### 3.3 PR + 머지

```bash
git checkout -b chore/screenshot-ko-mirror
git add docs-site/i18n/ko/ docs-site/docs/  # 잔여 ./img/ 정정 포함
git commit -m "chore(screenshots): KO mirror + path migration + Pages verify"
git push -u origin chore/screenshot-ko-mirror
gh pr create --title "chore(screenshots): KO mirror + path migration" --body "…"
```

### 3.4 세션 종료 시

`docs/sessions/2026-05-XX-screenshot-ko-final.md` 통합 핸드오프 (3 PR 머지 완료 양식, `docs/sessions/2026-05-10-stabilization-cad-bundle.md` 와 동일 구조). chore-backlog "screenshots" 섹션 ~~strikethrough~~ 처리.

---

## 5. 자율 실행 프로토콜 (모든 세션 공통)

`docs/autonomous-execution-plan.md` §"자율 실행 프로토콜" 그대로:

```
LOOP per session:
  1. 시작 시 검증 (§2)
  2. 새 브랜치 생성 + push (-u origin)
  3. capture spec / 마크다운 직접 작업 (에이전트 위임은 §"에이전트 권장 매핑" 따름)
  4. 로컬 검증 (lint + typecheck + Docusaurus build EN + KO + 시각 sanity)
  5. PR 생성 + CI 모니터링 (background polling)
  6. CI 실패 시 fix + push (최대 3회 retry → 초과 시 BLOCKED 표시 + 다음 세션 이월)
  7. 머지 후 chore-backlog.md 에 ~~취소선~~ + PR # + commit sha
  8. 세션 종료 핸드오프 노트 작성 → 본 prompt 의 "현재 상태" 섹션 갱신
```

### 에이전트 권장 매핑

| 세션 | 묶음 | 주 에이전트 | 보조 |
|------|------|-----------|------|
| 1 | Infra | test-writer (capture spec + 하네스 verb) | devops-engineer (Makefile + static/img 디렉토리), doc-writer (contributor-guide 운영 섹션) |
| 2 | EN | test-writer (capture spec 페이지별 확장) + doc-writer (마크다운 reference 삽입) | frontend-dev (시드 부재 화면 / 새 hardness verb) |
| 3 | KO + Pages | doc-writer + i18n-specialist (KO alt text 톤) | devops-engineer (Pages 워크플로 재실행 검증) |

### CI 게이트 동작

본 prompt 의 모든 PR 은 자동으로 다음 잡 통과 필요:
- `e2e (scan-flow)` — 39 시나리오 (capture spec 은 `--grep-invert @screenshots` 로 격리)
- `e2e (manual-aligned)` — 31 시나리오 (D 묶음 머지 후 31)
- `lint (frontend)` + `typecheck (frontend)` — Playwright capture spec 에 대해서도 체크
- `Deploy Docs to GitHub Pages` — Docusaurus build EN + KO success → Pages 자동 배포

PR 분리 원칙: 한 PR = 한 묶음 (Infra / EN / KO+Pages). 시각 회귀 가드 도입은 본 시리즈 OUT.

## 6. 세션 종료 체크리스트 (매 세션 끝 반드시)

- [ ] 작업한 chore 가 머지됐는지 (`gh pr view <num> --json mergedAt`)
- [ ] main 으로 checkout + pull 완료
- [ ] `docs/chore-backlog.md` 의 "Screenshots automation" 섹션 (신규) 에 ~~취소선~~ + PR # + commit sha
- [ ] `docs/sessions/2026-05-XX-screenshot-<phase>.md` 핸드오프 노트 작성
- [ ] 본 파일의 "현재 상태" 섹션 — main HEAD + 최근 commits + 처리율 갱신
- [ ] BLOCKED 항목 발생 시 본 파일 + chore-backlog 양쪽에 BLOCKED 표시

## 7. 세션 끊김 시 복구 절차

새 세션 시작:
1. 본 파일을 첫 메시지로 그대로 붙여넣음
2. **§2 시작 시 검증** 실행 → main HEAD + dev stack 확인
3. `cat docs/chore-backlog.md | grep -E "Screenshots|✅"` 로 처리 완료 항목 파악
4. §3 우선순위 표에서 **첫 번째 미처리 묶음** 선택
5. 해당 세션 prompt 그대로 실행

만약 직전 세션이 **PR CI 대기 중에 끊겼다면**:
1. `gh pr list --state open --author "@me"` 로 미머지 PR 확인
2. `gh pr checks <num>` 로 잡 상태 확인
3. green 이면 머지, 실패면 logs 분석 → fix → push retry
4. retry 3회 초과 시 BLOCKED 표시 + 다음 세션으로 이월

만약 **로컬 dev stack 이 unhealthy 상태로 끊겼다면**:
1. `make dev-reset-rebuild` (PR #47 헬퍼) 로 재기동 + seed
2. `docker-compose -f docker-compose.dev.yml ps` 로 6/6 healthy 확인
3. 본 prompt §1 의 단일 진실 파일들 다시 정독 후 재개

## 8. 참조 문서

- `CLAUDE.md` — 핵심 규칙 + 품질·보안 표준
- `docs/v2-execution-plan.md` — Phase 별 상세
- `docs/autonomous-execution-plan.md` — 자율 실행 프로토콜
- `docs/chore-backlog.md` — **본 세션의 단일 진실** (Screenshots automation 신규 섹션)
- `docs/sessions/2026-05-10-stabilization-cad-bundle.md` — 직전 통합 핸드오프
- `MEMORY.md` — 장기 기억

## 9. 주의사항

- **세션 내 1 PR 원칙** — Infra / EN / KO+Pages 각 묶음별 단일 PR. 사용자 / 관리자 / 컨트리뷰터 가이드 분할 금지 (시각 일관성 유지).
- **하네스 우선** (CLAUDE.md §품질·보안·운영 §4): capture spec 은 직접 selector 호출 금지. 신규 verb 가 필요하면 동일 PR 안에 하네스 추가.
- **시드 일관성** — 모든 캡처는 `--super-admin --with-scan --component-count 50 --vulnerability-count 30 --with-obligations --with-oauth-identity github` 한 가지 시드만 사용. 시나리오별 시드 분기는 향후 chore.
- **EN/KO 절대 경로** — 본 시리즈가 도입하는 모든 image reference 는 `/img/screenshots/<file>.png` 절대 경로. KO 의 `./img/...` 잔여는 세션 3 에서 반드시 정리 (그동안 broken 이었음 — `docs-site/i18n/.../img/` 디렉토리 부재).
- **시각 회귀 OUT** — Percy / Chromatic / pixel-diff CI 가드 도입은 별도 sprint. 본 시리즈는 1회성 캡처.
- **PII 무관** — 시드 fixture (`e2e-<suffix>@example.com`) 만 노출. OAuth identity row 의 deterministic `e2e-<suffix>` ID 도 PII 아님.
- **Memory `feedback_push_pr_authorized`** — push / `gh pr create` 자동 허용. force-push 는 사용자 명시 승인 필요.
- **Memory `feedback_audit_logs_fk_cascade_set_null`** / `feedback_asyncpg_double_colon_param` / `feedback_semgrep_self_match` — 본 세션에서 직접 발화하지 않으나 신규 capture 코드가 raw SQL / asyncpg / semgrep ignore 와 만나면 적용.
- **에이전트 한도** — capture spec 페이지별 확장은 ~22 페이지 → test-writer 1회 호출에 부족할 수 있음. 세션 2 에서 페이지 그룹별 (user 9 / admin 6 / 기타 8) 분할 위임 고려.
- **Auto 모드 가정** — 본 prompt 는 사용자가 관여하지 않는 자율 실행을 전제. 모든 디자인 결정은 §4 에서 미리 합의됨. 세션 진행 중 사용자 입력 요구하지 않는다.

## 10. 후속 (별도 sprint 협의)

본 prompt 의 세션 1~3 모두 처리 후, 다음 후속 후보:

- **Visual regression CI** — Percy / Chromatic / Playwright `expect(page).toHaveScreenshot()` 기반 픽셀 diff 가드 도입 (1~1.5 세션)
- **Animated walkthroughs** — `.gif` / `.mp4` 로 5~10초 워크플로 시연 (예: 백업 → 복원 / OAuth 연결 → 해제) (1 세션)
- **Locale-specific 이미지** — 한글 데이터가 보이는 캡처 보강 (한국어 매뉴얼이 영어 화면을 보여주면 이질감) (1 세션)
- **a11y alt text 감사** — 알트 텍스트가 화면 의미를 정확히 전달하는지 i18n-specialist 검토 (0.5 세션)
- **이미지 압축 자동화** — `oxipng` / `pngquant` Makefile 타겟 + CI 파일 크기 게이트 (0.5 세션)

별도 prompt 파일 (`_next-session-prompt-screenshot-followups.md`) 작성 권고.

본 작업 예상 시간: 세션당 1~2 시간, 3 세션 전체 약 4~6 시간.
