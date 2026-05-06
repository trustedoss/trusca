# Session Handoff — 2026-05-06 — chore PR #2 — worker 이미지 hardening + image-scan hard-fail 복원 (정책 완화 동반)

## 1. 무엇을 했나

- **chore PR #2 머지 완료** — feature 브랜치 `chore/worker-image-hardening`. 머지 커밋 `38236e2`. 직전 chore PR #1 (Python deps hygiene) 의 §4 "즉시 후속 — worker 이미지 bundled-tool bump + image-scan hard-fail 복원" 항목 처리. 결과: image-scan 잡 hard-fail 정상 통과, main CI 6 잡(lint × 2 / typecheck × 2 / test × 2 / image-scan / frontend-bundle-audit / e2e) 모두 green.
- **commit 트리** (3개, 33 → 0 잡힘 경로):
  1. **`8dca1fa` (Round 1)** — devops-engineer 단독:
     - `apt-get upgrade -y` 양 stage 추가 → libgnutls30 등 Debian OS 7건 (HIGH 6 + CRITICAL 1) 해결.
     - `ENV CDXGEN_VERSION=11.11.0 → 12.3.3` (cdxgen direct deps 의 cacache/pacote/cross-spawn 7.0.3 체인 제거 의도).
     - `.github/workflows/ci.yml` Trivy step `continue-on-error: true` 제거 → hard-fail 게이트 복원.
     - **결과**: Trivy 33 → 27 (HIGH 26 + CRITICAL 1). FAIL.
  2. **`a1e2e14` (Round 2)** — security-reviewer 평결 FAIL 후 devops-engineer 분담 fix:
     - **새 surface 1**: NodeSource Node 20.x 가 끌어오는 npm 10.8.2 의 cross-spawn 7.0.3 / tar 6.2.1 (×6 CVE) / glob 10.4.2 / minimatch 9.0.5 — 이는 cdxgen 가 아니라 `usr/lib/node_modules/npm/node_modules/*` 에 있음. fix = `npm install -g npm@11.13.0` (NodeSource 설치 직후). npm 11.13 은 cross-spawn 제거 + tar 7.5.13 / glob 13.0.6 / minimatch 10.2.5.
     - **새 surface 2**: cdxgen 12.x 의 `optionalDependencies` 인 `@cdxgen/cdxgen-plugins-bin` 이 vendored Go 바이너리 (osv-scanner / cdx-audit / sourcekitten 등) 를 실어옴 → Trivy 가 gobinary 12건 (HIGH 11 + CRITICAL 1, grpc CVE-2026-33186 포함) 탐지. fix 시도 = `npm install -g --omit=dev --omit=optional`.
     - **`.trivyignore` 신설** (workspace root): ORT 85.0.0 잔여 java jar 3건 (json-smart 2.5.0 / bcprov-jdk18on-in-jruby-stdlib / msgpack-core 0.9.10), 각 항목에 `apps/backend/integrations/ort.py:128-139` 의 `ort evaluate --output-formats JSON` 호출 패턴 anchor 의 reach 분석 주석 + `.github/workflows/ci.yml` Trivy step 에 `trivyignores: ./.trivyignore` 명시 핀.
     - **결과**: node-pkg 11건 + java jar 3건 통과. **그러나 gobinary 12건 잔존** — `--omit=optional` 효과 없음 발견 (cdxgen postinstall script 가 plugins-bin 을 별도로 다운로드).
  3. **`ac39bf0` (Round 3)** — 사용자 명시 정책 완화 ("개발이 우선이니까 CI build 통과되도록 정책을 완화하자"):
     - `.trivyignore` 정책 헤더에 카테고리 (3) 추가: "fixed-upstream-but-bundled-in-dev-tooling-runtime-unreached".
     - cdxgen-plugins-bin gobinary 12건 (CRITICAL grpc CVE-2026-33186 + 11 HIGH) `.trivyignore` 등록, 각 CVE 에 한 줄 reach 분석 (`apps/backend/integrations/cdxgen.py:107-115` 의 `cdxgen -r -o <path> --spec-version 1.5 <src>` 패턴 — 어떤 plugin 도 invocation 하지 않음).
     - cdxgen 의 `--omit=optional` flag 는 documentation-of-intent value 로 유지 (실효성은 0). Dockerfile 주석에 경험적 발견 명시.
     - **결과**: Trivy 0 잡힘. image-scan 잡 통과.
- **Producer-Reviewer 1라운드** (security-reviewer, Round 1 직후): 평결 = **FAIL**. CRITICAL 1 + High 3 차단 항목 — gobinary CRITICAL grpc / 11 HIGH gobinary / 11 HIGH node-pkg (depth-1 npm view 만 검사한 직전 devops-engineer 의 누락 — 정확 catch). Round 2/3 에서 점진적 해결 → 최종 PASS-with-runtime-unreached-suppressions.

## 2. 결정 사항 / 변경된 가정

- **`.trivyignore` 정책 카테고리 (1) → (3) 확장 — 문서화된 trade-off**.
  - (1) **upstream-unpatched / no-fix** CVE: 우리 코드에 미도달. 가장 보수적.
  - (2) **upstream-patched but bundled-in-tool-without-release**: 예 — ORT 85.0.0 의 json-smart 2.5.0 (2.5.2 가 maven central 에 있지만 ORT 85.0.0 fat-zip 안에는 미반영). ORT 86.x cut 또는 fork-vendoring 까지 ignore.
  - (3) **upstream-patched but bundled-in-dev-tooling-runtime-unreached**: 예 — cdxgen-plugins-bin Go 바이너리. 우리는 plugin 호출 0건이라 실행 안 됨. 가장 공격적 — 본 PR 에서 사용자가 명시 채택. 각 항목 reach 분석 의무 + 180일 재평가 의무는 동일.
  - 카테고리 (3) 의 위험: 누군가 미래에 cdxgen plugin 을 호출하기 시작하면 silent CVE 가 깨어남. mitigation = .trivyignore 헤더의 reach surface 블록이 single source of truth, integrations/cdxgen.py 변경 시 재검토 의무.
- **`apt-get upgrade -y` 의 비결정성 vs 보안 trade-off — 문서화된 채택**. CLAUDE.md 규칙 9 (`:latest` 금지) 의 정신과 일부 충돌하나 보안 trade-off 가 명백히 정당화. follow-up = base 이미지 digest pin (`python:3.12.7-slim@sha256:...`) 으로 부분 보완 — Phase 8 hardening backlog 등록.
- **cdxgen `--omit=optional` flag 유지 결정**: 실효성 0 이지만 documentation-of-intent value 가 있음. 미래 누군가 flag 가 작동한다고 가정하지 않도록 Dockerfile 주석에 경험적 발견 명시.
- **로컬 Docker 빌드 + Trivy 재현 SKIP — CI 가 검증 채널** (직전 PR #1 와 동일 사유, 사용자 환경 디스크 부족). Round 1 → 2 → 3 모두 CI 가 fail/pass 채널.
- **MEMORY.md 갱신 후보**: 본 PR 머지 후 `project_v2_roadmap.md` 또는 `project_v2_execution_plan.md` 인덱스에 "chore PR #2 — worker 이미지 hardening 완료, .trivyignore 카테고리 (3) 도입" 한 줄 추가 검토. 새 아키텍처 결정 (정책 카테고리 3) 이라 갱신 적합.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), #6 (55e67bd), #7 (d7bc929 + 93c41a4 merge), #8 (502f02f + ebb9c53 merge), #9 (f55e70d + 9da6b3c merge), **chore PR #1 (6366b62)**, **chore PR #2 (38236e2)** + chore CI fix 4건.
- **진행 중 PR**: 없음. 다음 = Phase 3 PR #10 (Project Detail).
- **GitHub origin/main**: `38236e2` (chore: worker image hardening + .trivyignore 도입).
- **변경 규모 (PR #2 누적)**: 3 files (`apps/backend/Dockerfile.worker` +80/-23, `.github/workflows/ci.yml` +44/-13, `.trivyignore` +211 신규).
- **통과 테스트** (CI green 잡):
  - lint (backend, frontend)
  - typecheck (backend, frontend)
  - test (backend unit + integration with postgres/redis sidecar, frontend vitest with coverage)
  - **image-scan (worker) — hard-fail 게이트 정상 통과** ← 본 PR 의 핵심 성과
  - frontend-bundle-audit
  - e2e (scan-flow, Playwright × 7 시나리오)
- **문서 / i18n**: 변경 없음. 본 PR 은 인프라 / CI / 정책만.

## 4. 후속 backlog

### Phase 8 hardening (이미 등록된 항목 + 본 PR 의 신규)
- **cdxgen-plugins-bin 카브** — `RUN rm -rf /usr/lib/node_modules/@cyclonedx/cdxgen/node_modules/@cdxgen/cdxgen-plugins-bin*` 를 cdxgen install RUN 마지막에 추가. 단 cdxgen 의 startup self-test 가 plugins-bin 의 존재를 가정하는지 검증 필요. 검증 후 깨지지 않으면 `.trivyignore` 의 12개 항목 모두 제거 가능.
- **Dockerfile.worker base digest pin** — `python:3.12.7-slim` → `python:3.12.7-slim@sha256:...`. dependabot/renovate 가 weekly digest + apt 결과 함께 갱신.
- **Worker container `USER` 지시문** — 현재 root 으로 celery 실행. unprivileged uid 로 분리.
- **NodeSource `curl | bash` → signed-by deb 패턴** — `apt-key adv --fetch-keys` 또는 keyring file + `signed-by=` 로 변경. CLAUDE.md / SECURITY.md 의 supply-chain 정책과 정합.
- **cdxgen install 시 `npm audit signatures` 검증** — npm 11.13.0 부터 SLSA provenance 지원. cdxgen 12.3.3 도 attestation 등록됨 (security-reviewer Round 1 검증).
- **python-jose → PyJWT 마이그레이션** — 직전 PR #1 carry-over.
- **CLAUDE.md / SECURITY.md 한 줄 추가** — "OS layer 는 `apt-get upgrade -y` 로 빌드 시점 결정 — security trade-off, deterministic 하지 않음. base image digest pin 으로 부분 보완 권고."

### 별도 follow-up PR
- **야간 Trivy soft-fail 잡** — 직전 PR #1 의 security-reviewer Medium #3 권고. 여전히 미해결. cron schedule + GitHub Issue 자동 생성 또는 Slack notify. main 차단은 PR 잡(현재 hard-gate)이 담당, drift 관찰은 nightly 가 담당.
- **`docs/security/trivyignore-policy.md`** — 정책 문서 정식 작성. `.trivyignore` 헤더의 카테고리 (1)/(2)/(3) 정의 + 변경 시 security-reviewer Producer-Reviewer 의무 + CI 에서 항목 만료 (180일) 자동 알림.
- **PR #9 follow-up backlog 7개 (L-1~L-4 / I-1~I-3)** — 직전 PR #1 carry-over.
- **PR #8 follow-up backlog 6개 (L-1·L-2·L-3·L-4·I-2·I-3)**.

### 운영 / 환경
- **로컬 Docker Desktop VM 디스크 정리** — 사용자 결정 사항. `docker system prune -a -f` 또는 Docker Desktop 설정에서 디스크 늘리기.

## 5. 다음 세션 시작 지시문

```
Phase 3 PR #10 — Project Detail (Overview / Components 탭).

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = 38236e2. 누적 머지: PR #1~#9 + chore CI fix 4건 + chore PR #1 (deps hygiene) + chore PR #2 (worker image hardening + .trivyignore 카테고리 (3) 도입).
CI green (image-scan hard-fail 정상 동작, 15 CVE 가 .trivyignore 에 reach 분석 포함 등록).

이번 세션 = Phase 3 PR #10 — Project Detail Overview + Components 탭.
docs/v2-execution-plan.md §3.4 표 3.1 / 3.2 / 3.3 산출.

시작 시 검증:
  docker-compose -f docker-compose.dev.yml ps  → 5/5 healthy
  gh run list --limit 3                          → main 최신 success

작업 내용 (Phase 3 PR #10):

1. backend (api/v1/projects.py 확장):
   - GET /v1/projects/{id}/overview — 리스크 게이지 + 분포 차트 + 최근 스캔 이력 집계.
   - GET /v1/projects/{id}/components — 1만 행 페이지네이션 (size cap 500).
   - GET /v1/components/{id} — 드로어 상세 (license + 취약점 join + raw_data).

2. db-designer (필요 시):
   - components 테이블 인덱스 (project_id, severity_max, license_category) 검증.
   - PR #8 의 jsonb_size_guard 마이그레이션과 충돌 없음 확인.

3. frontend (features/projects/ProjectDetailPage.tsx + 4 탭):
   - shadcn Tabs (Overview / Components / Vulnerabilities / Licenses).
   - react-virtuoso 1만 행 가상 스크롤.
   - 드로어 (Sheet) 열림 — 컴포넌트 상세.
   - 검색 / 필터 / 정렬 인라인 toolbar (모달 없음).

4. i18n-specialist:
   - EN/KO 번역 (project_detail / components 네임스페이스).
   - shadcn/ui Tabs 컴포넌트 신규 시 i18n 키 동시 추가.

5. test-writer:
   - 단위 (overview 집계, components 페이지네이션, 드로어 데이터 페치).
   - e2e 6 시나리오 (Overview 진입 / Components 가상 스크롤 60fps / 드로어 열림 / 검색 / 필터 / 정렬).

6. security-reviewer (Producer-Reviewer):
   - components / vulnerabilities IDOR 가드 검증.
   - recharts XSS 회귀 점검 (PR #7 동일 패턴).

핵심 라우팅:
  - backend-developer: API 확장.
  - db-designer: 인덱스 검증.
  - frontend-dev: Tabs + 가상 스크롤 + 드로어.
  - i18n-specialist: 번역 동시.
  - test-writer: 단위 + e2e.
  - security-reviewer: Producer-Reviewer.

DoD:
  - main CI 전체 잡 success (image-scan hard-fail 포함).
  - 신규/변경 backend + frontend coverage ≥ 80%.
  - Components 탭 1만 행에서 60fps 스크롤 (Lighthouse 측정).
  - Overview API p95 < 200ms (locust 또는 pytest-benchmark).
  - e2e 6 시나리오 green.
  - security-reviewer 평결 PASS 또는 PASS-with-follow-ups.

주의:
  - 사용자 정책: rm/push/docker prune 거부 — 사용자가 ! 프리픽스로.
  - CLAUDE.md 규칙 4 (DT Circuit Breaker — Components 탭이 vulnerabilities join), 11
    (os.getenv 런타임), 12 (인증 surface), 13 (CORS).
  - .trivyignore 정책 카테고리 (3) 도입됨 — 본 세션 새 surface 가 .trivyignore 추가 트리거하면
    카테고리 명시 + reach 분석 의무 + Phase 8 backlog 등록.

세션 종료 시 docs/sessions/2026-05-XX-phase3-pr10-project-detail.md 를 §7 양식으로 작성.
```
