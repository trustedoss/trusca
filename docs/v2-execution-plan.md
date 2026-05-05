# TrustedOSS Portal v2 — 실행 계획서

> **목적**: 새 세션·새 디렉토리에서 Opus 4.7과 함께 v2를 GA까지 완주하기 위한 단일 진실 문서.
> **사용법**: 매 세션 시작 시 이 문서를 읽고, 해당 Phase 섹션의 "세션 시작 지시문"을 복사해 사용한다. 세션 종료 시에는 §7 핸드오프 양식으로 진행 상황을 기록한다.
> 작성일: 2026-05-05 | 작성자 모델: Claude Opus 4.7 (1M context)

---

## 0. TL;DR — 한 페이지 요약

| 항목 | 결정 |
|---|---|
| **레포** | `github.com/trustedoss/trustedoss-portal` (별도 신규 디렉토리 `~/projects/trustedoss-portal`) |
| **기간** | 8주 (Phase 0~8) — 매주 1 Phase 평균 |
| **하루 가용 시간** | 8h × 5d/주 = 40h/주 |
| **모델** | Claude Opus 4.7 (1M context) — 메인 오케스트레이터 |
| **개발 기법** | Harness(`revfactory/harness`) 기반 다중 에이전트 팀 |
| **품질 기준** | 글로벌 상용 SCA 제품 수준 — Black Duck/Snyk 대등 |
| **출발점** | Phase 0 = 신규 레포·모노레포·Docker Compose dev/prod·Alembic·CI 셋업 |
| **마일스톤** | Week 4 = 핵심 SCA 동작, Week 6 = 관리자+CI연동 완료, Week 8 = GA |

> 본 문서 = "프로젝트의 OS". CLAUDE.md = "런타임 규칙". MEMORY.md = "장기 기억 인덱스". 셋이 분담한다.

---

## 1. CLAUDE.md 검토 결과

현재 `CLAUDE.md`(=`docs/CLAUDE_V2_HANDOFF.md`의 산출물)는 자체 신규 레포에 그대로 복사해 사용할 수 있도록 잘 작성되어 있다. 다만 실행 단계에서 다음 보강이 필요하다.

### 1.1 강점 (그대로 유지)
- 기술 스택 결정이 명확하다 (FastAPI / PostgreSQL 17 / Celery / React 18 / shadcn/ui).
- v1 학습이 반영된 핵심 규칙 13개 — 특히 `docker-compose`(V1) 강제, `os.getenv()` 런타임 호출, `:latest` 금지는 v1 사고 사례에서 배운 가드레일이다.
- 아키텍처 결정(DT Circuit Breaker, RBAC 3단계, CI 연동 수준)이 구체적이다.
- 디자인 시스템 토큰(색·폰트·밀도·드로어)이 화면 설계와 정합한다.

### 1.2 보강 권고 (Phase 0에서 CLAUDE.md에 반영)

| 항목 | 현재 상태 | 보강안 |
|---|---|---|
| **Definition of Done (DoD)** | "Phase 완결" 추상적 | Phase별 머지 가능 기준을 본 문서 §3에 정의 → CLAUDE.md에서 본 문서 참조 |
| **테스트 임계** | "하네스 우선"만 명시 | "PR 머지 = 단위 80% line coverage + Playwright 핵심 시나리오 green" 명시 |
| **보안 기본값** | "JWT 인증 필수"만 | 비밀번호 정책(min 12자, bcrypt cost 12), JWT 만료(access 30m / refresh 7d), 레이트 리밋(IP당 5 login/min) 추가 |
| **에러 응답 규약** | 미정 | RFC 7807 Problem Details (JSON `type/title/status/detail/instance`) 채택 명시 |
| **로깅 규약** | 미정 | structlog + JSON 라인, request_id 컨텍스트 전파, PII 마스킹 |
| **마이그레이션 정책** | "Alembic 필수" | 다운그레이드 미보장(forward-only), 데이터 마이그레이션은 별도 task로 분리 |
| **Harness 운영** | 에이전트 표만 존재 | 본 문서 §4 운영 매뉴얼을 CLAUDE.md에서 참조 |
| **세션 핸드오프** | 없음 | 세션 종료 시 `docs/sessions/<date>-<topic>.md` 생성 룰 추가 |

→ **Phase 0 작업 1번**으로 위 7개 보강을 CLAUDE.md에 반영한다 (§6.1 참조).

### 1.3 의심·재확인 권고

- **8주 GA의 현실성**: 9개 Phase × 평균 1주 = 8주에 빠듯하다. Phase 3(상세 UX)와 Phase 4(관리자) 각각 1.5주가 필요할 가능성이 높다. → Week 7 막바지에 **스코프 컷(예: 한국어 번역 v2.1로 미루기)** 결정 게이트를 §3.9에 명시.
- **DT 번들 vs 외부 DT 동시 지원**: 두 모드 모두 GA에 넣으면 안정화 부담 2배. → GA는 **번들만**, 외부 DT는 v2.1로 분리 권고.
- **Helm chart**: Phase B(차기)로 명시되어 있으나 GA 전 데모 SaaS(Phase 8)에서 필요할 수 있다. 차라리 **Cloud Run + 외부 PostgreSQL(Cloud SQL)** 로 데모 운영 → Helm은 v2.1.

---

## 2. 디렉토리 배치 (확정)

```
v1 코드 (보존, read-only):
~/projects/trustedoss-portal-v1/
  ├── webapp/, ort/, docs/, ...         ← v1 구현체 (재사용 참조용)
  └── CLAUDE.md                         ← v1 시점의 지시문

v2 작업 디렉토리:
~/projects/trustedoss-portal/
  ├── CLAUDE.md                         ← v1에서 옮겨와 §1.2 보강 적용 (단일 진실)
  ├── README.md, LICENSE, NOTICE
  ├── docs/v2-execution-plan.md         ← 본 문서 (v2 단일 진실)
  ├── docs/sessions/                    ← 세션별 핸드오프 누적
  ├── docs/_v1-reference/               ← v1에서 복사한 참조 자료 (rules.kts, design-concept, 인터뷰 핸드오프)
  └── apps/, charts/, scripts/, .github/
```

### 2.1 분리 절차 (Phase 0 PR #1에서 1회 수행, 완료됨)

```bash
# 1. v2 작업 디렉토리 (이미 존재)
cd ~/projects/trustedoss-portal

# 2. v1에서 재사용 참조용 자료를 _v1-reference/에 사본만 두기
mkdir -p docs/sessions docs/_v1-reference
cp ~/projects/trustedoss-portal-v1/ort/.ort/rules.kts             docs/_v1-reference/ort-rules.kts
cp ~/projects/trustedoss-portal-v1/docs/design-concept.md         docs/_v1-reference/design-concept-v1.md 2>/dev/null || true
# 인터뷰 핸드오프(session-8dc690fc-*.md)는 인터뷰 시점에 docs/에 있던 사본을 그대로 _v1-reference/로 이동

# 3. git init + 첫 커밋 (사용자 명시 명령 후 진행)
git init -b main
# .gitignore 는 PR #1 산출물에 포함됨
```

> **주의**: v1 코드는 v2 작업 디렉토리로 복사하지 않는다. 필요한 시점에 `~/projects/trustedoss-portal-v1/`에서 직접 참조하거나, 위처럼 `docs/_v1-reference/`에 사본만 둔다 (오염 방지).

---

## 3. 마이크로 로드맵 (Phase 0~8, 작업·PR 단위)

원칙:
- **각 Phase는 1~3개의 PR로 머지 완료**한다. 큰 Phase는 PR을 쪼갠다.
- 각 작업은 **(입력 → 산출물 → DoD)** 형태로 명세한다.
- 세션 1개 = PR 1개를 권장. 세션이 길어지면 컨텍스트 오염이 생긴다.

### 3.1 Phase 0 — 기반 구축 (Week 1, Days 1~4 / 32h)

**목표**: 새 레포에서 `docker-compose -f docker-compose.dev.yml up`만 치면 PostgreSQL/Redis/FastAPI/Vite가 떠야 한다.

| # | 작업 | 입력 | 산출물 | DoD |
|---|---|---|---|---|
| 0.1 | CLAUDE.md 보강 (§1.2의 7개 항목) | 본 문서 §1.2 | `CLAUDE.md` 갱신 | 보강 7개 모두 반영, 표 마크다운 검증 |
| 0.2 | 모노레포 디렉토리 + 라이선스 + README 스켈레톤 | CLAUDE.md "디렉토리 구조" | `apps/`, `docs/`, `charts/`, `scripts/`, `LICENSE`(Apache-2.0), `README.md` | 트리 구조 일치, 라이선스 SPDX 헤더 cron |
| 0.3 | `docker-compose.dev.yml` (Postgres17, Redis7, FastAPI, Celery worker, Vite) | CLAUDE.md "환경변수" | 컴포즈 파일 + `.env.example` | `docker-compose -f docker-compose.dev.yml up` 후 5개 컨테이너 healthy |
| 0.4 | FastAPI 부트스트랩 (`apps/backend/main.py`, `core/config.py`, `core/db.py`) | 0.3 | `/health` 엔드포인트 + structlog JSON 로그 | curl `/health` → `{"status":"ok"}`, JSON 로그에 request_id |
| 0.5 | Alembic 초기화 + 빈 첫 migration | 0.4 | `alembic/`, `alembic.ini`, `versions/0001_init.py` | `alembic upgrade head` 성공 |
| 0.6 | React + Vite + shadcn/ui 부트스트랩 | CLAUDE.md "기술 스택" | `apps/frontend/`, `vite.config.ts`, `tailwind.config.js`, `components.json` | `npm run dev` → 빈 헬로페이지 렌더 |
| 0.7 | GitHub Actions CI: lint(ruff/eslint) + typecheck(mypy/tsc) + test(pytest/vitest) | 0.4, 0.6 | `.github/workflows/ci.yml` | PR마다 3개 잡 모두 green |
| 0.8 | Issue/PR 템플릿 + CONTRIBUTING.md 스켈레톤 | OSS 거버넌스 | `.github/ISSUE_TEMPLATE/*.yml`, `pull_request_template.md`, `CONTRIBUTING.md` | gh CLI로 이슈 생성 시 템플릿 적용됨 |
| 0.9 | Harness 에이전트 팀 정의 파일 작성 | 본 문서 §4 | `.claude/agents/*.md` (9개) | 9개 에이전트 정의 완성, 호출 가능 |

**Phase 0 PR 분할**:
- PR #1: 0.1~0.2 (CLAUDE.md + 디렉토리 + 라이선스)
- PR #2: 0.3~0.5 (도커 + FastAPI + Alembic)
- PR #3: 0.6~0.7 (프론트엔드 + CI)
- PR #4: 0.8~0.9 (OSS 거버넌스 + Harness 에이전트)

**Phase 0 완료 기준**: `make dev` 한 줄로 풀 스택이 뜨고, `gh pr create`가 템플릿을 띄우며, CI가 자동 실행된다.

---

### 3.2 Phase 1 — 인증 & RBAC (Week 1~2, Days 4~10 / 48h)

**목표**: 회원가입 → 로그인 → 보호된 페이지 접근 → 로그아웃 흐름이 영문/한글 둘 다 자연스럽게 동작.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 1.1 | DB 모델: User, Organization, Team, Membership, Role, AuditLog | `models/auth.py`, alembic migration | `alembic upgrade head` 후 5개 테이블 + FK 검증 |
| 1.2 | FastAPI-Users 통합 (bcrypt cost 12, JWT access 30m / refresh 7d) | `api/v1/auth.py` | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` 작동 |
| 1.3 | RBAC 미들웨어 (`Depends(require_role("super_admin"))`) | `core/security.py` | 유닛 테스트 8개 통과 (각 role × 각 endpoint) |
| 1.4 | Audit Log 자동 기록 (SQLAlchemy event listener) | `core/audit.py` | 모든 INSERT/UPDATE/DELETE 자동 기록, 사용자 ID·IP·user-agent 포함 |
| 1.5 | 레이트 리밋 (slowapi, IP당 5 login/min) | `core/ratelimit.py` | 6번째 시도 시 429 반환 |
| 1.6 | React 인증 화면 (Login/Register/ForgotPassword) | `pages/auth/*.tsx` | shadcn Form + zod validation, 에러 inline 표시 |
| 1.7 | Zustand 인증 스토어 + axios 인터셉터 (refresh token rotation) | `stores/authStore.ts`, `lib/api.ts` | access 만료 시 자동 refresh, refresh 만료 시 로그인 페이지 리다이렉트 |
| 1.8 | i18n 부트스트랩 (en/ko, 인증 화면 번역 키) | `locales/{en,ko}/auth.json` | 언어 토글 시 인증 화면 즉시 전환 |
| 1.9 | Playwright 하네스: `AuthHarness` 클래스 (login/register/expectLoggedIn) | `tests/e2e/_harness/auth.ts` | 로그인 시나리오 3개 green |

**Phase 1 PR**: PR #5(모델+API), PR #6(UI+i18n+E2E)

---

### 3.3 Phase 2 — 스캔 파이프라인 코어 (Week 2~3, Days 8~17 / 72h)

**목표**: Git URL 등록 → "Scan" 클릭 → cdxgen+ORT+DT(또는 Trivy) Celery 실행 → WebSocket으로 진행률 실시간 표시 → 완료 시 컴포넌트·취약점·라이선스가 DB에 저장.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 2.1 | DB 모델: Project, Scan, Component, Vulnerability, License, Obligation | migration | 6개 테이블 + JSONB 인덱스(GIN on raw_data) |
| 2.2 | Project CRUD API (team-scoped) | `api/v1/projects.py` | RBAC 적용, 다른 팀 프로젝트 조회 시 403 |
| 2.3 | Scan 트리거 API (`POST /projects/{id}/scans`) | `api/v1/scans.py` | Celery task ID 반환, 동일 프로젝트 동시 스캔 1개 제한 |
| 2.4 | Celery 스캔 태스크 (소스: cdxgen → ORT → DT upload) | `tasks/scan_source.py` | 워크스페이스 격리 (`/tmp/trustedoss/<scan_id>/`), 완료 시 cleanup |
| 2.5 | Celery 컨테이너 스캔 태스크 (Trivy) | `tasks/scan_container.py` | docker pull or local image, OS+lang 패키지 통합 |
| 2.6 | DT 안정화 레이어 (HealthMonitor + CircuitBreaker) | `integrations/dt/{health.py,breaker.py}` | DT down 시 PostgreSQL 캐시 응답, 자동 docker restart |
| 2.7 | DT 새 CVE 재탐지 (Celery Beat 1시간 주기) | `tasks/dt_resync.py` | 새 CVE 감지 시 Notification 큐에 push |
| 2.8 | 고아 프로젝트 감지 (Celery Beat 6시간) | `tasks/dt_orphan_cleaner.py` | 고아 목록을 `dt_orphans` 테이블에 저장, Admin UI에서 정리 |
| 2.9 | WebSocket 엔드포인트 (`/ws/scans/{scan_id}`) | `api/v1/ws.py` | 스캔 진행률(0~100) + 단계명 실시간 푸시 |
| 2.10 | Project List UI + 스캔 상태 실시간 배지 | `pages/projects/list.tsx` | 가상 스크롤(react-virtuoso), 검색·정렬·필터 |
| 2.11 | 스캔 진행 모달 (단계 5개 프로그레스) | `features/scan/ScanProgress.tsx` | WebSocket 끊김 시 자동 재연결, 페이지 이탈해도 백그라운드 진행 |
| 2.12 | E2E 시나리오: 스캔 등록→실행→완료→결과조회 (mock DT) | `tests/e2e/scan_flow.spec.ts` | 시나리오 4개 green |

**Phase 2 PR**: PR #7(모델+API), PR #8(Celery+DT 안정화), PR #9(WebSocket+UI+E2E) — **3개로 쪼개기 권장**

---

### 3.4 Phase 3 — 프로젝트 상세 UX (Week 3~4, Days 14~22 / 64h)

**목표**: `/projects/:id`의 6개 탭(Overview/Components/Vulnerabilities/Licenses/Obligations/SBOM)이 Black Duck 수준으로 작동.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 3.1 | Overview API (리스크 스코어 산식, 스캔 이력) | `api/v1/projects.py:get_overview` | 응답시간 p95 < 200ms |
| 3.2 | Overview UI (리스크 게이지, 분포 도넛, 스캔 이력 테이블) | `features/project/Overview.tsx` | recharts 사용, 다크모드 대응 |
| 3.3 | Components API + 가상 스크롤 UI + 드로어 | `features/project/Components.tsx` | 1만 개 컴포넌트도 60fps 스크롤 |
| 3.4 | Vulnerabilities API + 상태 워크플로우 (New→Analyzing→Suppressed→Fixed) | migration + UI | 상태 변경 시 audit log 기록 |
| 3.5 | Licenses 도넛 + 분류별 컴포넌트 드릴다운 | `features/project/Licenses.tsx` | 허용/조건부/금지 색상 일관 |
| 3.6 | Obligations Tab + NOTICE 파일 자동 생성 (서버 사이드) | `services/notice_generator.py` | NOTICE 다운로드 = 모든 의무사항 포함 검증 |
| 3.7 | SBOM 다운로드 (CycloneDX JSON/XML, SPDX JSON/Tag-Value) | `services/sbom_export.py` | 4개 포맷 모두 검증기 통과 (`cyclonedx validate`, `spdx-tools validate`) |
| 3.8 | Excel 보고서 (openpyxl) | `services/report_xlsx.py` | 4개 시트(Summary/Components/Vulns/Licenses), 조건부 서식 |
| 3.9 | PDF 보고서 (WeasyPrint) | `services/report_pdf.py` | 한국어 폰트 임베드(NotoSansKR), 페이지 번호 |
| 3.10 | E2E: 탭 6개 전체 시나리오 | `tests/e2e/project_detail.spec.ts` | 시나리오 12개 green |

**Phase 3 PR**: PR #10(API), PR #11(UI), PR #12(보고서+SBOM)

---

### 3.5 Phase 4 — 관리자 패널 (Week 4~5, Days 20~28 / 64h)

**목표**: Super Admin이 DT를 직접 만지지 않아도, Users/Teams/DT/Scans/Disk/Audit/Health 7개 화면에서 모든 운영 가능.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 4.1 | Admin 라우트 가드 + 사이드바 |  | Super Admin이 아니면 404 |
| 4.2 | Users 관리 (역할 변경, 비활성화, 비밀번호 리셋) | | 변경 시 사용자에게 이메일 알림 |
| 4.3 | Teams 관리 (생성/삭제/멤버) | | 마지막 Team Admin 제거 시 차단 |
| 4.4 | Scan Queue (실행/대기/실패, Celery Inspect 연동, 강제 종료) | | revoke/terminate 시 워크스페이스 cleanup |
| 4.5 | DT Connector (URL/API Key, 연결 테스트, 수동 sync, 고아 정리) | | 고아 정리 후 DT/포털 카운트 일치 |
| 4.6 | Disk Usage (psutil + workspace/db/logs 분리) | | 임계치 80% 도달 시 알림 |
| 4.7 | Audit Log (검색·필터·CSV 내보내기) | | 100만 행에서도 검색 < 1초 (인덱스) |
| 4.8 | System Health (각 서비스 헬스체크, 응답시간 그래프) | | 5초 폴링, 다운 시 빨간 카드 |
| 4.9 | 컴포넌트 승인 워크플로우 UI (`/approvals`) | migration + UI | Pending → Under Review → Approved/Rejected, 알림 발송 |
| 4.10 | E2E: Admin 7개 화면 + 승인 워크플로우 | | 시나리오 9개 green |

**Phase 4 PR**: PR #13(Users/Teams), PR #14(DT/Scans/Disk/Audit/Health), PR #15(승인 워크플로우)

---

### 3.6 Phase 5 — CI/CD 연동 (Week 5, Days 26~32 / 48h)

**목표**: GitHub PR 푸시 → 자동 스캔 → 결과를 PR 코멘트로 자동 게시 → Critical CVE 발견 시 PR 머지 차단.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 5.1 | API Key 발급/폐기/스코프 (project / team / org) | `api/v1/api_keys.py` | 키 prefix `tos_xxxx_`, hash 저장 |
| 5.2 | 외부 Scan Trigger API (API Key 인증) | | OpenAPI에 별도 태그, 인증 분기 |
| 5.3 | GitHub Webhook 수신 (push, pull_request) | `api/v1/webhooks/github.py` | HMAC 서명 검증, 재시도 멱등성(delivery_id) |
| 5.4 | GitLab Webhook 수신 (push, merge_request) | `api/v1/webhooks/gitlab.py` | 토큰 검증 |
| 5.5 | 빌드 차단 게이트 로직 | `services/policy_gate.py` | Critical CVE OR 금지 라이선스 → exit code 1 |
| 5.6 | PR/MR 자동 코멘트 게시 (App Token / PAT) | `services/sca_comment.py` | 동일 PR 재스캔 시 코멘트 업데이트 (idempotent) |
| 5.7 | GitHub Actions composite action `trustedoss/scan-action@v1` | 별도 레포 또는 `actions/` | marketplace 등록 준비 (도큐먼트 포함) |
| 5.8 | GitLab CI 템플릿 (`templates/gitlab-ci.yml`) | | include 한 줄로 사용 가능 |
| 5.9 | Jenkinsfile 예제 + 공유 라이브러리 | | declarative pipeline 예제 |
| 5.10 | E2E: 가짜 GitHub PR 시뮬레이터로 게이트 동작 검증 | | 코멘트 게시 + exit code 검증 |

**Phase 5 PR**: PR #16(API Key+Webhook), PR #17(게이트+코멘트+Action)

---

### 3.7 Phase 6 — 다국어 & 안정성 강화 (Week 6, Days 33~38 / 40h)

**목표**: 영문/한글 100% 번역, 디스크/DB/브라우저 닫힘/DT 다운 모든 시나리오에서 데이터 손실 없음.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 6.1 | 모든 UI 문자열 t() 추출 (i18next-parser CI 게이트) | `locales/en/*.json` | `untranslated` 카운트 0 |
| 6.2 | 한국어 번역 완성 | `locales/ko/*.json` | 도메인 용어집 정합 (`docs/glossary.md`) |
| 6.3 | 알림 시스템 (이메일 SMTP + Slack/Teams Webhook) | `notifications/` | 5개 트리거 모두 작동 |
| 6.4 | 알림 센터 UI (인앱 벨 + 히스토리) | `features/notifications/` | 읽음/안읽음, 묶음 |
| 6.5 | 디스크 가드 (90% 도달 시 스캔 차단 + 알림) | `core/disk_guard.py` | 임계치 환경변수 `DISK_HARD_LIMIT_PCT` |
| 6.6 | 브라우저 닫힘 복구 — 스캔은 Celery에서 계속, 재접속 시 WebSocket 재연결 + 진행률 즉시 동기화 | | 통합 테스트로 검증 |
| 6.7 | DB Integrity Check (시작 시 `pg_amcheck`) | `scripts/integrity_check.sh` | 손상 시 백엔드 fail-fast + 알림 |
| 6.8 | 자동 백업 (Celery Beat 매일 자정, S3/로컬) | `tasks/backup.py` | `pg_dump` + workspace tar, 7일 보존 |
| 6.9 | 수동 백업/복원 Admin UI | | 다운로드 + 업로드 복원 |
| 6.10 | React Error Boundary (전역 + 페이지별) | | 에러 시 fallback + 자동 보고 |

**Phase 6 PR**: PR #18(i18n+알림), PR #19(안정성+백업)

---

### 3.8 Phase 7 — 배포 & 문서 (Week 7, Days 38~44 / 40h)

**목표**: 처음 보는 사람이 30분 안에 설치 완료. 모든 가이드가 EN+KO 둘 다 존재.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 7.1 | `install.sh` 인터랙티브 wizard | | 비밀번호 자동 생성, .env 자동 작성, 종료 시 URL 출력 |
| 7.2 | `upgrade.sh` (pull + alembic upgrade head + zero-downtime 권고) | | DB 백업 자동 선행 |
| 7.3 | `backup.sh` / `restore.sh` | | 단일 명령 |
| 7.4 | Production `docker-compose.yml` (Traefik + TLS + restart policy + 리소스 제한) | | letsencrypt 자동 발급 |
| 7.5 | Docusaurus 사이트 + GitHub Pages 자동 배포 | `docs/`, `.github/workflows/docs.yml` | docs.trustedoss.io 라이브 |
| 7.6 | 관리자 가이드 EN+KO (설치/설정/백업/업그레이드/트러블슈팅) | | 스크린샷 포함, KO는 ko 사이드바 |
| 7.7 | 사용자 가이드 EN+KO (프로젝트 등록/스캔/결과 해석/SBOM) | | 동일 |
| 7.8 | API Reference (FastAPI 자동 OpenAPI → Docusaurus) | | redoc 통합 |
| 7.9 | CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md | | 표준 OSS 거버넌스 |
| 7.10 | 랜딩 페이지 (`trustedoss.io`, GitHub Pages) | 별도 레포 또는 `landing/` | Lighthouse 95+ |

**Phase 7 PR**: PR #20(설치 스크립트+prod compose), PR #21(Docusaurus+가이드), PR #22(랜딩)

---

### 3.9 Phase 8 — 데모 SaaS & GA (Week 7~8, Days 42~48 / 40h)

**목표**: GCP에 데모 SaaS 라이브, GitHub Release v2.0.0 태그, 오픈소스 공개 발표.

| # | 작업 | 산출물 | DoD |
|---|---|---|---|
| 8.1 | OAuth (GitHub, Google) 구현 | | 가입 시 개인 Team 자동 생성 |
| 8.2 | 멀티테넌트 격리 검증 (보안 리뷰) | OWASP Top 10 체크리스트 | IDOR/BOLA 통과 |
| 8.3 | GCP 배포 — Cloud Run + Cloud SQL Postgres + Memorystore Redis | `terraform/` 또는 `gcloud` 스크립트 | 비용 < $50/월 (small instance) |
| 8.4 | Demo SaaS sandbox 데이터 시드 | `scripts/seed_demo.py` | 가입자에게 샘플 프로젝트 제공 |
| 8.5 | 보안 점검 (SCA on self, SAST: bandit/semgrep) | CI에 추가 | High+ 0건 |
| 8.6 | 부하 테스트 (Locust, 동시 스캔 3개 / 동시 사용자 50명) | 보고서 | p95 < 1s, error < 0.1% |
| 8.7 | v2.0.0 태그 + GitHub Release (changelog, 바이너리, 가이드 링크) | | semantic-release 자동화 |
| 8.8 | **스코프 컷 결정 게이트** | 본 문서 §1.3 | Helm/외부 DT 모드/IDE 플러그인 등 v2.1로 미루기 결정 |
| 8.9 | 오픈소스 공개 발표 (HN, Reddit r/devops, dev.to, X) | 발표문 | 첫 24h GitHub stars 모니터링 |

**Phase 8 PR**: PR #23(OAuth+데모SaaS), PR #24(보안+성능), PR #25(릴리스)

---

## 4. Harness 운영 매뉴얼

### 4.1 Harness가 무엇이고 우리에게 무엇을 주는가

`revfactory/harness`는 Claude Code 위에서 **다중 에이전트 팀 정의를 자동 생성·운영하는 메타 프레임워크**다. 우리는 이걸로 다음 효과를 노린다:

1. **컨텍스트 분리** — 메인 세션(오케스트레이터)은 결정·통합만 하고, 무거운 탐색/구현은 서브 에이전트가 격리된 컨텍스트에서 수행한다. 메인 세션의 1M 토큰을 아낀다.
2. **병렬성** — 독립 작업은 서브 에이전트들이 동시에 실행한다 (메인의 `Agent` 다중 호출).
3. **전문성** — 각 에이전트는 자기 영역 가이드라인만 본다. 백엔드 개발자가 Tailwind 토큰 신경 안 써도 된다.
4. **Producer-Reviewer 품질 게이트** — 보안·DT 안정화 같은 핵심 코드는 구현 → 리뷰 → 수정 루프로 머지한다.

### 4.2 에이전트 팀 (`.claude/agents/*.md`)

각 에이전트는 Phase 0.9에서 `.claude/agents/<name>.md`로 정의한다. 정의 파일에는 (a) 역할 한 줄, (b) 사용 가능 도구, (c) 영역 가이드라인, (d) 출력 양식을 적는다.

| 에이전트 | 한 줄 정의 | 주 사용 Phase | 핵심 가이드라인 |
|---|---|---|---|
| `backend-developer` | FastAPI 엔드포인트·Pydantic·비즈로직 작성 | 1~6 | RFC 7807 에러, async 우선, RBAC dependency |
| `db-designer` | PostgreSQL 스키마·Alembic 작성 | 0~2 | forward-only, JSONB는 GIN, FK 명시 |
| `scan-pipeline-specialist` | Celery 태스크·DT/ORT/cdxgen/Trivy 통합 | 2 | 워크스페이스 격리, 멱등성, Circuit Breaker |
| `frontend-dev` | React 18 + shadcn/ui 컴포넌트 | 1~7 | 가상 스크롤·드로어 패턴, Tailwind 토큰만 |
| `i18n-specialist` | react-i18next, EN/KO 번역 | 1, 6 | 키 평탄화, 도메인 용어집 일관성 |
| `devops-engineer` | Docker/Compose/CI/Helm/스크립트 | 0, 7~8 | `docker-compose` V1, `:latest` 금지 |
| `test-writer` | pytest + Playwright 하네스 | 매 Phase | 하네스 우선, 도메인 언어, retry는 Playwright 자동 |
| `doc-writer` | Docusaurus + EN/KO 가이드 | 7 | 코드 블록 동작 검증, 스크린샷 포함 |
| `security-reviewer` | OWASP Top 10, 의존성 CVE, 인증 검토 | 1, 5, 8 | IDOR/BOLA 우선, 시크릿 스캔, JWT 검증 |

### 4.3 패턴별 운영 규칙

#### 패턴 A — Fan-out/Fan-in (가장 많이 사용)
**언제**: 독립적인 API 3개를 동시에 만들 때, 또는 백엔드+프론트+테스트를 동시에 작성할 때.

**호출 방식**: 메인 세션에서 **단일 메시지에 `Agent` 다중 호출**을 넣는다 (병렬 실행).

```
오케스트레이터 (메인 Opus 4.7)
  ├─ Agent(subagent_type=backend-developer, "Components API GET/PUT 구현")
  ├─ Agent(subagent_type=backend-developer, "Vulnerabilities API GET/PUT 구현")
  ├─ Agent(subagent_type=frontend-dev,     "Components 탭 + 드로어 UI")
  └─ Agent(subagent_type=test-writer,       "탭 6개 시나리오 12개 작성")
[3개 결과 도착]
  → 오케스트레이터가 통합·머지 충돌 해소·E2E 실행
```

#### 패턴 B — Producer-Reviewer (품질 게이트)
**언제**: 인증/JWT/API Key/DT Circuit Breaker/빌드 게이트/OAuth 같은 핵심 보안·안정성 코드.

**규칙**: 최대 2회 루프. 3회 이상이면 메인이 직접 결정.

```
1차: backend-developer 또는 scan-pipeline-specialist가 구현
2차: security-reviewer가 검토 (OWASP 체크리스트 적용)
3차: 1차 에이전트가 수정 + diff만 다시 제출
```

#### 패턴 C — Expert Pool (라우팅)
**언제**: 작업 종류가 다양하고, 메인이 어떤 에이전트가 적합한지 결정해야 할 때.

```
사용자: "API Key 탈취 시나리오 점검해줘"  → security-reviewer
사용자: "Helm chart 초안 만들어줘"        → devops-engineer
사용자: "도넛 차트가 다크모드에서 깨져"   → frontend-dev
```

#### 패턴 D — Pipeline (Phase 진행)
**언제**: Phase 자체. Phase 0 → 1 → 2 순서는 선행 의존성이 있다.

**규칙**: 이전 Phase가 PR 머지될 때까지 다음 Phase 시작 금지. 예외 = 독립적 부속 작업(예: 문서, 디자인).

### 4.4 에이전트 호출 가이드라인

좋은 호출 = (1) 목표 한 줄 + (2) 컨텍스트(왜 필요한지) + (3) 산출물 명세 + (4) DoD.

**나쁜 예**: `Agent("Components API 만들어줘")`

**좋은 예**:
```
Agent(subagent_type=backend-developer, prompt="""
목표: GET /api/v1/projects/{id}/components 구현
컨텍스트: Phase 3.3에서 Components Tab UI가 이 API를 호출함. 가상 스크롤이라 페이지네이션 필수.
산출물: apps/backend/api/v1/projects.py에 함수 추가, schemas/component.py에 응답 스키마 추가, tests/unit/test_components_api.py에 단위 테스트 5개.
DoD: 응답시간 p95 < 200ms (10k 컴포넌트 기준), RBAC 적용(team-only 가시성), 정렬·필터·검색 쿼리 파라미터 지원, OpenAPI 자동 생성됨.
참조: 기존 projects.py의 get_project를 패턴으로 따라가되 페이지네이션은 keyset(id+created_at) 사용.
""")
```

### 4.5 에이전트가 하면 안 되는 것

- 메인 세션의 결정을 대체하는 행위 (스코프 변경, 기술 선택)
- PR 생성·머지 (메인 또는 사용자만)
- Production 배포·외부 시스템에 영향 주는 작업 (DT 재시작 자동화 코드는 OK, 실제 운영 DT 재시작은 사용자 확인 필요)
- CLAUDE.md/본 문서/MEMORY.md 갱신 (메인이 통합 후 갱신)

---

## 5. 세션 운영 방식

### 5.1 세션 = PR 단위

**한 세션은 한 PR을 머지할 수 있는 단위로 끊는다.** 그보다 길면 컨텍스트가 오염되어 품질이 떨어진다.

권장: 4~6시간 / 세션. 세션 종료 시 §7 핸드오프 작성.

### 5.2 매 세션 시작 루틴

```
1. cd ~/projects/trustedoss-portal
2. claude  (또는 IDE에서 Claude Code 열기)
3. 첫 메시지로 §6의 해당 Phase "세션 시작 지시문" 복붙
4. 메인 세션이 다음을 자동 수행:
   - CLAUDE.md 읽기
   - docs/v2-execution-plan.md 읽기 (이 파일)
   - docs/sessions/ 디렉토리에서 가장 최근 핸드오프 1~2개 읽기
   - 현재 Phase의 §3 마이크로 로드맵에서 미완료 첫 작업 확인
   - 작업 계획을 한 번 사용자에게 보고 후 진행
```

### 5.3 매 세션 종료 루틴

```
1. PR 생성 or 작업 단위 마무리
2. docs/sessions/<YYYY-MM-DD>-<phase>-<topic>.md 작성 (§7 양식)
3. 필요하면 MEMORY.md 갱신 (메인 세션이 자동 판단)
4. 다음 세션 시작 시 사용할 지시문 한 줄 제안
```

### 5.4 컨텍스트 단절 방지 메커니즘

| 위협 | 방어책 |
|---|---|
| 세션 끊김 | 매 종료 시 핸드오프 (§7) 누적 — 다음 세션이 최근 1~2개를 읽으면 100% 복원 |
| 결정 사항 손실 | MEMORY.md 인덱스 + 본 문서 갱신 — "왜" 가 살아남음 |
| Phase 이탈(스코프 크리프) | §3 마이크로 로드맵 표를 매 세션 시작 시 확인 |
| 에이전트 정의 표류 | `.claude/agents/*.md`가 git 추적 — PR로 변경 |
| CLAUDE.md 표류 | 본 문서가 CLAUDE.md를 참조하므로 본 문서가 진실 |

---

## 6. Phase별 세션 시작 지시문 (복붙용)

각 지시문은 **새 세션의 첫 메시지로 그대로 복붙**한다. 메인 세션이 컨텍스트를 자동 로드하고 작업을 시작한다.

### 6.0 — 디렉토리 분리 + Phase 0 시작 (단 한 번, 완료됨)

> ✅ 2026-05-05 시행 완료. 작업 디렉토리는 `~/projects/trustedoss-portal/`로 확정. v1 코드는 `~/projects/trustedoss-portal-v1/`에 read-only로 보존.
> 시행 내용: v1 참조 자료(ort/.ort/rules.kts, design-concept.md, session-8dc690fc-...md) → `docs/_v1-reference/` 사본 / CLAUDE.md §1.2 보강 8개 항목 반영 / 모노레포 골격 + LICENSE(Apache-2.0) + NOTICE + README 스켈레톤 + .gitignore + git init.

(원본 지시문, 참고용으로 보존)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 시작한다.

먼저 ~/projects/trustedoss-portal-v1 디렉토리가 있는지 확인하고,
v1 참조용 자료(ort/.ort/rules.kts, docs/design-concept.md, docs/session-8dc690fc-...md)는
docs/_v1-reference/에 사본만 둔다.

복사가 끝나면 docs/v2-execution-plan.md §1.2와 §3.1을 읽고
Phase 0의 첫 PR(0.1 CLAUDE.md 보강 + 0.2 모노레포 구조 + LICENSE + README 스켈레톤)을 작성한 뒤 사용자 확인을 받아라.

Harness 에이전트는 Phase 0.9에서 정의하므로 지금은 메인 세션만으로 진행한다.
```

### 6.1 — Phase 0 후속 (PR #2 ~ #4)

> 다음 세션 진입점. PR #1은 2026-05-05에 머지(첫 커밋) 완료. 이어서 PR #2(0.3~0.5)부터 시작.

**PR #2 — Docker Compose dev + FastAPI 부트스트랩 + Alembic** (다음 작업)
```
Phase 0 PR #2 작업을 시작한다. docs/v2-execution-plan.md §3.1과 docs/sessions/의 가장 최근 핸드오프를 읽고
다음 3개 작업(0.3, 0.4, 0.5)을 한 PR로 묶어 작성해라.

0.3 docker-compose.dev.yml — PostgreSQL 17, Redis 7, FastAPI(uvicorn --reload), Celery worker, Vite 컨테이너.
   `.env.example` 작성 (CLAUDE.md "환경변수" 섹션 기준). 모든 이미지에 버전 태그 명시(:latest 금지).
   docker-compose V1(하이픈) 형식 준수.
0.4 FastAPI 부트스트랩 — apps/backend/main.py, core/config.py(os.getenv 런타임 호출), core/db.py(asyncpg).
   /health 엔드포인트, structlog JSON 로거(request_id 미들웨어), RFC 7807 예외 핸들러 골격.
0.5 Alembic 초기화 — alembic/, alembic.ini, versions/0001_init.py(빈 첫 migration).
   `alembic upgrade head` 가 docker-compose dev에서 성공해야 한다.

작업 순서:
1. 하네스 우선 — apps/backend/tests/integration/test_health.py 와 test_alembic_upgrade.py 를 먼저 작성.
2. 위 3개 산출물을 만든 뒤 `docker-compose -f docker-compose.dev.yml up -d` 로 5개 컨테이너 healthy 검증.
3. CLAUDE.md "품질·보안·운영 표준" 항목 5(로깅) · 4(에러 응답)이 코드에 반영됐는지 자체 검토.
4. 완료되면 사용자에게 보고하고 머지(커밋) 명령을 받는다.
5. 세션 종료 시 docs/sessions/<YYYY-MM-DD>-phase0-pr2-compose-fastapi-alembic.md 를 §7 양식으로 작성.
```

**PR #3 — React + Vite + shadcn/ui 부트스트랩 + GitHub Actions CI**
```
Phase 0 PR #3 작업을 시작한다. docs/v2-execution-plan.md §3.1과 docs/sessions/의 최근 핸드오프를 읽고
다음 2개 작업(0.6, 0.7)을 한 PR로 묶어 작성해라.

0.6 apps/frontend/ 부트스트랩 — Vite + React 18 + TypeScript, Tailwind, shadcn/ui(components.json),
   TanStack Query 프로바이더, Zustand 스토어 골격, react-i18next(EN/KO 빈 리소스), 디자인 토큰(CLAUDE.md 디자인 시스템 색상).
   `npm run dev` → 헬로 페이지 렌더링.
0.7 .github/workflows/ci.yml — 3 잡 매트릭스: lint(ruff + eslint), typecheck(mypy + tsc), test(pytest + vitest).
   PR/푸시 트리거, Node 20 / Python 3.12 캐시. PR마다 3 잡 모두 green 이어야 한다.

세션 종료 시 docs/sessions/<YYYY-MM-DD>-phase0-pr3-frontend-ci.md 작성.
```

**PR #4 — OSS 거버넌스 + Harness 에이전트 정의**
```
Phase 0 PR #4 (Phase 0 마무리). docs/v2-execution-plan.md §3.1, §4.2, §6.2를 읽고
다음 2개 작업(0.8, 0.9)을 한 PR로 묶어 작성해라.

0.8 OSS 거버넌스 — CONTRIBUTING.md(코드 스타일/PR 절차/CLA 무), CODE_OF_CONDUCT.md(Contributor Covenant 2.1),
   .github/ISSUE_TEMPLATE/{bug_report.yml, feature_request.yml, security.yml},
   .github/pull_request_template.md(요약/관련 이슈/체크리스트/테스트 결과).
0.9 .claude/agents/*.md 9개 — §4.2 표 기준. 각 에이전트마다 (a) 역할 (b) 도구 (c) 영역 가이드라인(CLAUDE.md 핵심규칙·보강표준 인용) (d) 출력 양식 (e) mock task 1개. §6.2 드라이런 결과 보고.

이 PR이 머지되면 Phase 0 완료. 다음 세션은 §6.3 양식으로 Phase 1을 시작한다.
세션 종료 시 docs/sessions/<YYYY-MM-DD>-phase0-pr4-governance-agents.md 작성.
```

### 6.2 — Phase 0.9 (Harness 에이전트 팀 정의)

```
Phase 0의 마지막 작업(0.9)으로 .claude/agents/ 아래 9개 에이전트를 정의한다.
docs/v2-execution-plan.md §4.2의 표를 기준으로,
각 에이전트마다 (a) 역할 한 줄, (b) 사용 도구, (c) 영역 가이드라인 (CLAUDE.md 핵심 규칙 13개 중 해당 항목 인용), (d) 출력 양식을 명시한 마크다운 파일을 작성해라.

작성 후 간단한 드라이런(에이전트마다 1개씩 mock task를 던져보고 응답 형식 점검)을 하고 결과를 보고해라.
```

### 6.3 — Phase 1 ~ Phase 8 공통 양식

```
Phase <N> 작업을 시작한다.

1. docs/v2-execution-plan.md의 §3.<N>을 읽고 미완료 작업 첫 1~2개를 식별해라.
2. docs/sessions/의 최근 핸드오프 1~2개를 읽어 현재 상태를 복원해라.
3. 작업이 Fan-out 가능하면 Agent 다중 호출로 병렬 실행해라.
   핵심 보안/안정성 코드(인증, API Key, DT, OAuth, 빌드 게이트)는 Producer-Reviewer 패턴으로 security-reviewer를 호출해라.
4. PR 단위가 완성되면 사용자에게 보고하고 머지 명령을 받아라.
5. 세션 종료 시 docs/sessions/<YYYY-MM-DD>-phase<N>-<topic>.md를 §7 양식으로 작성해라.
```

> 각 Phase별로 위 양식의 `<N>`만 바꿔 사용한다. 추가 컨텍스트는 메인 세션이 본 문서에서 자동으로 가져온다.

---

## 7. 세션 핸드오프 양식 (`docs/sessions/<날짜>-<주제>.md`)

```markdown
# Session Handoff — <YYYY-MM-DD> — <Phase> — <Topic>

## 1. 무엇을 했나
- (구체 작업 5개 이내, 파일/PR 링크)

## 2. 결정 사항 / 변경된 가정
- (이번 세션에서 결정·번복된 것. 본 문서·CLAUDE.md·MEMORY.md 갱신 여부 표시)

## 3. 현재 상태
- 머지된 PR: #N
- 진행 중 PR: #N (브랜치 이름)
- 통과 테스트: 단위 X / 통합 Y / E2E Z
- 알려진 이슈: ...

## 4. 다음 세션이 할 일
- (§3.<N>의 어느 작업부터 시작할지 명시)
- (Fan-out으로 갈지, Producer-Reviewer로 갈지 권고)

## 5. 주의·블로커
- (DT 가짜 응답 필요 / GCP 키 발급 필요 / 사용자 확인 필요한 결정)

## 6. 다음 세션 시작 지시문 (복붙용)
```
<§6의 해당 Phase 양식에 이번 컨텍스트를 한두 줄 추가>
```
```

---

## 8. Phase별 완료 기준 체크리스트 (Definition of Done)

각 Phase는 아래를 모두 만족해야 다음 Phase로 넘어간다.

### 공통 DoD (모든 Phase)
- [ ] CI 모든 잡 green (lint, typecheck, test, build)
- [ ] 단위 테스트 line coverage ≥ 80% (해당 PR 변경 파일 기준)
- [ ] Playwright E2E 핵심 시나리오 green
- [ ] OpenAPI에 신규/변경 엔드포인트 반영
- [ ] CLAUDE.md / 본 문서 / MEMORY.md 정합성 유지
- [ ] 에러 응답 RFC 7807 양식
- [ ] 로그에 PII 미노출

### Phase별 추가 DoD
- **Phase 0**: `make dev` 한 줄로 5개 컨테이너 healthy + 9개 에이전트 정의 파일 존재
- **Phase 1**: 가입→로그인→로그아웃 EN/KO 양쪽 동작, security-reviewer 통과
- **Phase 2**: 실제 cdxgen+ORT(또는 mock) 스캔 1회 완주, WebSocket 진행률 표시, DT 다운 시 캐시 응답
- **Phase 3**: 6개 탭 모두 동작, SBOM 4개 포맷 검증기 통과, NOTICE 자동 생성
- **Phase 4**: Admin 7개 화면, 승인 워크플로우 동작, 고아 정리 후 DT/포털 카운트 일치
- **Phase 5**: GitHub 가짜 PR 시뮬레이터로 게이트+코멘트 검증, security-reviewer 통과
- **Phase 6**: untranslated 카운트 0, 알림 5개 트리거 동작, 백업 1회 복원 검증
- **Phase 7**: 새 머신에서 install.sh로 30분 내 설치 완료, Docusaurus 라이브
- **Phase 8**: GCP 데모 SaaS 라이브, OWASP Top 10 통과, v2.0.0 태그 + GitHub Release

---

## 9. 리스크 & 컨틴전시

| 리스크 | 영향 | 트리거 | 대응 |
|---|---|---|---|
| DT 안정화가 예상보다 어려움 | Phase 2 +1주 지연 | Health Monitor 구현 시점에 발견 | 외부 DT 모드 v2.1로 미루고, 번들에 집중 |
| 8주 내 한국어 100% 어려움 | Phase 6 막힘 | Week 6 시작 시 미번역 키 200+ | KO는 GA 시 80%만, v2.0.1로 보완 |
| GCP 비용 초과 | 데모 SaaS 운영 부담 | 트래픽 폭증 (HN 노출 등) | Cloud Run min-instance=0, 일일 한도 알림 |
| 보안 리뷰에서 IDOR 발견 | 머지 차단 | security-reviewer 알림 | 즉시 수정 PR, 다음 Phase 시작 전 머지 |
| 컨텍스트 1M 한계 | 메인 세션 메모리 부족 | 세션 5h 초과 | 즉시 §7 핸드오프 후 새 세션 시작 |

---

## 10. 최종 산출물 (Week 8 종료 시)

- [ ] GitHub 레포 `trustedoss/trustedoss-portal` public, Apache-2.0
- [ ] v2.0.0 GitHub Release (changelog, install 가이드 링크)
- [ ] `docs.trustedoss.io` Docusaurus 라이브 (EN+KO)
- [ ] `trustedoss.io` 랜딩 페이지 라이브
- [ ] GCP 데모 SaaS 라이브 (small instance, 비용 < $50/월)
- [ ] CONTRIBUTING.md / CODE_OF_CONDUCT.md / SECURITY.md
- [ ] GitHub Actions marketplace에 `trustedoss/scan-action@v1` 등록 준비
- [ ] 본 문서가 끝까지 살아 있고, MEMORY.md에 핵심 결정 인덱싱 완료

---

> 본 문서는 v2 작업의 단일 진실이다. 본 문서와 CLAUDE.md / MEMORY.md / `.claude/agents/*` 가 충돌하면 **본 문서를 기준으로 다른 셋을 갱신**한다.
> 본 문서를 변경할 때는 PR로 변경하고 변경 이유를 commit message에 남긴다.
