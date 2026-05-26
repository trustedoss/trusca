# TrustedOSS Portal v2 — 새 세션 시작 지시문

> **이 문서를 새 레포의 CLAUDE.md로 복사하여 사용한다.**  
> 작성일: 2026-05-05 | 대상 모델: Claude Opus 4.7

---

## 프로젝트 정체성

**TrustedOSS Portal**은 기업 오픈소스 위험 관리 포털이다.  
보안 취약점(CVE)·라이선스 컴플라이언스·SBOM을 하나의 UI에서 관리한다.  
**포지셔닝**: "Black Duck/Snyk 수준의 SCA 기능을 Apache-2.0 오픈소스로, 자체 호스팅으로"

- **레포**: `github.com/trustedoss/trustedoss-portal`
- **라이선스**: Apache-2.0
- **언어**: 기본 영어, 한국어 지원 (GA 동시 출시)
- **배포**: Docker Compose(온프레미스) → Helm chart → 데모 SaaS(GCP)
- **품질 목표**: 글로벌 상용 제품 수준 (개인 프로젝트 아님)

---

## 기술 스택 (확정)

| 레이어 | 기술 |
|--------|------|
| Backend | FastAPI + SQLAlchemy 2.0 + Alembic |
| DB | PostgreSQL 17 |
| 비동기 | Celery + Redis |
| Frontend | React 18 + Vite + shadcn/ui + Tailwind CSS |
| 서버 상태 | TanStack Query (React Query) |
| 클라이언트 상태 | Zustand |
| 실시간 | WebSocket (스캔 상태 스트리밍) |
| 인증 | FastAPI-Users (JWT + OAuth2) |
| i18n | react-i18next |
| 문서 | Docusaurus (GitHub Pages) |
| 테스트 | pytest + Playwright (하네스 패턴) |
| CI/CD | GitHub Actions |
| 컨테이너 | Docker Compose (dev/prod 분리) |
| K8s | Helm chart (Phase B) |

---

## 핵심 규칙 (반드시 준수)

1. **PostgreSQL only** — SQLite 임시 사용 금지. 처음부터 PostgreSQL.
2. **Alembic 마이그레이션** — 스키마 변경은 반드시 migration 파일 생성.
3. **ORT/cdxgen/Trivy는 Celery 비동기** — 동기 처리 절대 금지. 실행 시간 5~60분.
4. **DT Circuit Breaker** — DT API 호출 전 health 확인. OPEN 상태면 PostgreSQL 캐시 반환.
5. **하네스 우선** — API와 UI 모두 테스트 하네스를 먼저 작성하고 구현.
6. **Phase 완결** — 각 Phase는 merge 가능한 상태로 완료. 미완성 WIP 없음.
7. **기능 + 완성도 동시** — 구현 후 즉시 UX·에러처리·번역까지 완성. "나중에 하기" 없음.
8. **문서 동행** — 새 기능 = 해당 Docusaurus 문서 동시 작성.
9. **Docker image `:latest` 금지** — 모든 이미지에 버전 태그 명시.
10. **`docker-compose` (V1, 하이픈)** — `docker compose` (V2) 사용 금지. 환경 미지원.
11. **`os.getenv()` 런타임 호출** — 모듈 레벨 상수로 환경변수 캐싱 금지.
12. **인증 필수** — 모든 API 엔드포인트는 JWT 인증 적용. 예외는 명시적으로 표시.
13. **CORS** — 프로덕션은 허용 오리진 명시. `allow_origins=["*"]` 는 dev 환경만.

---

## 품질·보안·운영 표준 (Phase 0 보강)

> 본 표준은 `docs/v2-execution-plan.md` §1.2의 보강 권고를 정식 채택한 것이다.
> Phase별 상세 DoD·로드맵은 `docs/v2-execution-plan.md` §3, §8을 단일 진실로 본다.

### 1. Definition of Done (DoD)
- Phase 단위 머지 가능 기준은 `docs/v2-execution-plan.md` §8 "Phase별 완료 기준 체크리스트"를 따른다.
- 공통 DoD: lint + typecheck + test 모두 green, 신규 코드 단위 커버리지 ≥ 80% line, 핵심 시나리오 Playwright green, EN/KO 번역 동시 반영, Docusaurus 문서 동시 갱신.

### 2. 테스트 임계
- **PR 머지 게이트**: 신규/변경 코드 단위 테스트 line coverage **≥ 80%**.
- **E2E**: Playwright 하네스(`PortalPage` 패턴)의 핵심 시나리오는 항상 green이어야 한다.
- 하네스가 없는 신규 화면/도메인은 PR에서 하네스를 같이 추가한다 (선구현 후테스트 금지).

### 3. 보안 기본값
- **비밀번호**: 사용자 비밀번호 최소 **8자**(NIST 800-63B 최소), bcrypt cost **12**, NIST 800-63B 권고 차단 사전 적용. (관리자 부트스트랩 `create_super_admin`/`seed_demo`는 12자 유지 — 운영 시크릿 강화 기준.)
- **JWT**: access 토큰 만료 **30분**, refresh 토큰 만료 **7일**, refresh는 회전(rotation) + 재사용 탐지.
- **레이트 리밋**: 로그인 엔드포인트 **IP당 5회/분**(429 + Retry-After 헤더 반환), 인증된 API는 사용자 단위로 별도 정책.
- **CSRF/CORS**: 프로덕션 CORS 화이트리스트 강제, SameSite=Lax 쿠키, refresh 토큰은 HttpOnly+Secure.

### 4. 에러 응답 규약 (RFC 7807 Problem Details)
- 모든 4xx/5xx 응답은 `application/problem+json` Content-Type을 사용한다.
- 필수 필드: `type`(URI), `title`, `status`, `detail`, `instance`.
- 도메인 확장 필드는 snake_case로 추가하고 OpenAPI에 모델로 등록한다.
- 예외 → Problem 변환은 FastAPI `exception_handlers`에서 일괄 처리.

### 5. 로깅 규약
- **포맷**: `structlog` + JSON 라인 출력 (1 라인 = 1 이벤트).
- **컨텍스트 전파**: `request_id`(미들웨어에서 `X-Request-ID` 헤더 또는 UUIDv7 자동 생성), `user_id`, `team_id`, `task_id`(Celery)는 모든 로그에 자동 첨부.
- **PII 마스킹**: 비밀번호, 토큰, API Key, 이메일은 로그에 평문 금지. 마스킹 헬퍼(`mask_pii`)를 통과시킨다.
- 로그 레벨: 정상 흐름 INFO, 사용자 오류 WARNING, 시스템 오류 ERROR(트레이스 포함).

### 6. 마이그레이션 정책
- Alembic 마이그레이션은 **forward-only**다. 다운그레이드는 보장하지 않으며 `downgrade()`는 `pass`/`raise NotImplementedError`로 둔다.
- **스키마 마이그레이션과 데이터 마이그레이션은 분리**한다. 데이터 마이그레이션은 별도 Alembic revision 또는 Celery 일회성 task로 작성하고 멱등성을 보장한다.
- Breaking 컬럼 변경(예: NOT NULL 추가, 컬럼 삭제)은 expand → migrate data → contract 3단계로 분리한다.

### 7. Harness 운영
- 에이전트 팀 정의·호출 패턴(Fan-out/Fan-in, Producer-Reviewer, Expert Pool, Pipeline)은 `docs/v2-execution-plan.md` §4를 단일 진실로 본다.
- 핵심 보안/안정성 코드(인증, API Key, DT 연동, OAuth, 빌드 게이트)는 Producer-Reviewer 패턴으로 `security-reviewer` 에이전트 검증을 거친 뒤에만 머지한다.

### 8. 세션 핸드오프
- 세션 종료 시 `docs/sessions/<YYYY-MM-DD>-phase<N>-<topic>.md`를 `docs/v2-execution-plan.md` §7 양식으로 작성한다.
- 다음 세션은 첫 메시지로 §6의 해당 Phase 시작 지시문을 그대로 사용하고, 메인 세션이 최근 핸드오프 1~2개를 자동으로 읽어 상태를 복원한다.

---

## 아키텍처 결정 (확정)

### 조직/팀/권한 모델
```
Organization (배포 단위, 1개)
├── Super Admin  — 시스템 전체 (배포자/IT관리자)
├── Team A
│   ├── Team Admin  — 팀 설정·팀원 관리
│   └── Developer   — 스캔 실행·결과 조회
└── Team B
    └── ...

프로젝트 가시성: team-only(기본) / org-wide(설정 가능)
데모 SaaS: 가입 시 개인 Team 자동 생성
```

### DT(Dependency-Track) 연동 전략
DT를 docker-compose에 번들로 포함. 외부 DT 연결도 지원.

```
v1 문제 해결:
① DT Health Monitor (60초 heartbeat) + unhealthy 시 docker restart
② Circuit Breaker — OPEN 상태 시 PostgreSQL 취약점 캐시로 대응
③ 취약점 데이터 PostgreSQL 캐싱 (DT 재시작 중에도 포털 정상 동작)
④ 고아 프로젝트 감지·정리 (Celery Beat 6시간 주기 + Admin UI)
⑤ DT sync 상태 Admin 대시보드 표시
```

### 스캔 파이프라인
```
소스 스캔: cdxgen → ORT → DT (라이선스 + 취약점)
컨테이너 스캔: Trivy (OS 패키지 취약점)
새 CVE 재탐지: DT NVD 동기화 → 포털 자동 갱신 + 알림 발행
```

### CI/CD 연동 (표준 SCA 수준)
- REST API + API Key 인증
- GitHub/GitLab Webhook (push, PR/MR)
- **빌드 차단 게이트**: Critical CVE or 금지 라이선스 → exit code 1
- PR/MR 코멘트 자동 게시
- GitHub Actions action, GitLab CI 템플릿, Jenkins Jenkinsfile

---

## 주요 기능 전체 목록

### 핵심 SCA 기능
- 컴포넌트 탐지 (cdxgen, 30+ 언어/빌드시스템)
- 라이선스 분류 (허용/조건부/금지, ORT 룰셋)
- 취약점 탐지 (DT, NVD/OSV/GitHub Advisory)
- 컨테이너 스캔 (Trivy, OS 패키지)
- SBOM 생성 (CycloneDX JSON/XML, SPDX JSON/Tag-Value)
- Excel/PDF 보고서
- **의무사항 추적 + NOTICE 파일 자동 생성**
- **새 CVE 자동 재탐지** (DT NVD 동기화 연동)
- **컴포넌트 승인 워크플로우** (Pending→Under Review→Approved/Rejected)

### 거버넌스
- **빌드 차단 게이트** (CI exit code 1)
- 라이선스 정책 설정 (ORT 룰 기반)
- 프로젝트별 리스크 스코어

### 운영
- **알림 시스템** (이메일 SMTP + Slack Webhook + MS Teams Webhook)
- 감사 로그 (모든 쓰기 작업 자동 기록)
- 자동 백업 (Celery Beat, 매일 자정)
- 수동 백업/복원 (Admin UI)

### 관리자
- 사용자/팀 관리
- DT 연결 설정·모니터링·고아 정리
- 스캔 큐 모니터링 (실행중/대기/실패/강제종료)
- 디스크 사용량 대시보드 (임계치 알림)
- System Health 대시보드
- 감사 로그 (검색/필터/CSV)

### CI/CD
- API Key 관리
- GitHub/GitLab Webhook
- PR/MR 자동 코멘트
- GitHub Actions action (`trustedoss/scan-action@v1`)
- GitLab CI 템플릿
- Jenkins Jenkinsfile 예제

### 인증/권한
- 비밀번호 로그인 (bcrypt + JWT)
- OAuth (GitHub, Google) — 데모 SaaS 전용
- SSO OIDC — 다음 버전
- RBAC (Super Admin / Team Admin / Developer)

---

## 화면 구조

```
Public
├── /login
├── /register
└── /forgot-password

App (인증 후)
├── /                    대시보드 (전사 리스크 포트폴리오)
├── /projects            프로젝트 목록
├── /projects/new        프로젝트 등록
├── /projects/:id        프로젝트 상세
│   ├── Overview         리스크 게이지, 분포 차트, 스캔 이력
│   ├── Components       패키지 목록 (가상 스크롤, 드로어)
│   ├── Vulnerabilities  CVE 목록, 상태 워크플로우
│   ├── Licenses         분포 도넛, 허용/조건부/금지
│   ├── Obligations      의무사항, NOTICE 파일 다운로드
│   ├── SBOM             포맷 선택 다운로드, Excel/PDF
│   └── Settings         프로젝트 설정, CI 연동, Webhook
├── /scans               전역 스캔 큐
├── /policies            라이선스 정책
├── /approvals           컴포넌트 승인 대기 목록
├── /integrations        API Key, Webhook 설정
├── /notifications       알림 센터
└── /admin
    ├── /admin/users
    ├── /admin/teams
    ├── /admin/dt
    ├── /admin/scans
    ├── /admin/disk
    ├── /admin/audit
    └── /admin/health
```

---

## 디렉토리 구조 (모노레포)

```
trustedoss-portal/
├── apps/
│   ├── backend/                FastAPI 앱
│   │   ├── api/                라우터 (v1/)
│   │   ├── core/               설정, 보안, DB
│   │   ├── models/             SQLAlchemy 모델
│   │   ├── schemas/            Pydantic 스키마
│   │   ├── services/           비즈니스 로직
│   │   ├── tasks/              Celery 태스크
│   │   ├── integrations/       DT, ORT, cdxgen, Trivy
│   │   ├── notifications/      이메일, Slack, Teams
│   │   └── tests/
│   │       ├── unit/
│   │       ├── integration/
│   │       └── e2e/
│   └── frontend/               React 앱
│       ├── src/
│       │   ├── components/     shadcn/ui 기반 공통 컴포넌트
│       │   ├── pages/          라우트별 페이지
│       │   ├── features/       도메인별 기능 모듈
│       │   ├── hooks/          커스텀 훅
│       │   ├── stores/         Zustand 스토어
│       │   ├── lib/            유틸리티
│       │   └── locales/        EN/KO 번역 파일
│       └── tests/
│           └── _harness/       Playwright 하네스 (PortalPage)
├── charts/                     Helm chart
│   └── trustedoss/
├── docs/                       Docusaurus 사이트
├── scripts/
│   ├── install.sh
│   ├── upgrade.sh
│   ├── backup.sh
│   └── restore.sh
├── .github/
│   ├── workflows/              GitHub Actions
│   ├── ISSUE_TEMPLATE/
│   └── pull_request_template.md
├── docker-compose.yml          프로덕션
├── docker-compose.dev.yml      개발
├── .env.example
└── CLAUDE.md                   (이 파일)
```

---

## 에이전트 팀 (Harness 기반)

새 세션에서 `revfactory/harness`를 사용하여 아래 에이전트 팀을 구성한다.

| 에이전트 파일 | 전문 영역 | 주요 사용 Phase |
|--------------|-----------|----------------|
| `backend-developer.md` | FastAPI 엔드포인트, Pydantic 스키마, 비즈니스 로직 | 1~5 |
| `db-designer.md` | PostgreSQL 스키마, Alembic 마이그레이션 | 0~1 |
| `scan-pipeline-specialist.md` | Celery 태스크, cdxgen/ORT/Trivy/DT 연동, Circuit Breaker | 2 |
| `frontend-dev.md` | React 18 + shadcn/ui 컴포넌트, TanStack Query | 2~6 |
| `i18n-specialist.md` | react-i18next, EN/KO 번역, 언어 토글 | 6 |
| `devops-engineer.md` | Docker Compose, GitHub Actions, Helm chart, 설치 스크립트 | 0, 7~8 |
| `test-writer.md` | pytest 단위·통합, Playwright E2E 하네스 | 매 Phase |
| `doc-writer.md` | Docusaurus, 관리자·사용자·기여자 가이드 | 7 |
| `security-reviewer.md` | OWASP Top 10, 의존성 CVE, 감사 로그 검증 | 8 |

**주요 Harness 패턴**:
- **Fan-out/Fan-in**: Phase 내 독립 작업 병렬 처리 (API 구현 + 테스트 + 문서 동시)
- **Producer-Reviewer**: 핵심 보안 코드 (인증, DT 연동, API Key) 구현 후 security-reviewer 검증
- **Expert Pool**: 도메인에 맞는 전문가 라우팅 (DB 설계 → db-designer, K8s → devops-engineer)
- **Pipeline**: Phase 0 → 1 → 2 순차 진행 (선행 조건 있는 Phase)

---

## 환경변수 (.env.example)

```bash
# Database
DATABASE_URL=postgresql+asyncpg://trustedoss:password@postgres:5432/trustedoss

# Redis / Celery
REDIS_URL=redis://redis:6379/0

# Auth
SECRET_KEY=change-this-to-a-random-secret-key-min-32-chars
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# Dependency-Track
DT_URL=http://dtrack-api:8080
DT_API_KEY=your-dt-api-key

# Workspace
WORKSPACE_HOST_PATH=/opt/trustedoss/workspace

# ORT
ORT_RULES_PATH=/opt/trustedoss/ort/rules.kts

# Notifications
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SLACK_WEBHOOK_URL=
TEAMS_WEBHOOK_URL=

# OAuth (데모 SaaS 전용)
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Jira (옵션, stub 기본)
JIRA_ENABLED=false
JIRA_URL=
JIRA_TOKEN=
```

---

## 라이선스 분류 (ORT 룰셋)

| 분류 | Severity | 라이선스 |
|------|----------|----------|
| **금지** | ERROR (빌드 차단) | AGPL-3.0, GPL-2.0/3.0, SSPL-1.0, BUSL-1.1 |
| **조건부** | WARNING (법무 검토 + 승인 워크플로우) | LGPL-2.x/3.x, MPL-2.0, EPL-1.x/2.x, CDDL-1.0 |
| **허용** | — | MIT, Apache-2.0, BSD-2/3, ISC, Unlicense, CC0-1.0 |

---

## Phase 0 시작 체크리스트

새 세션을 시작하면 아래 순서로 진행한다:

1. `revfactory/harness` 설치 → 에이전트 팀 구성 (위 목록 기준)
2. GitHub 레포 생성: `github.com/trustedoss/trustedoss-portal`
3. 브랜치 전략: `main` (프로덕션), `develop` (통합), `feature/*` (기능)
4. 모노레포 디렉토리 구조 생성 (위 구조 기준)
5. `docker-compose.dev.yml` 작성 (PostgreSQL 17, Redis 7, Celery, FastAPI, Vite HMR)
6. `docker-compose.yml` 작성 (프로덕션, Traefik 포함)
7. Alembic 초기화 + 첫 번째 빈 migration
8. GitHub Actions CI 파이프라인 (`lint`, `test`, `typecheck`)
9. `.env.example` 작성
10. 이 CLAUDE.md를 새 레포 루트에 배치

---

## 참고: v1에서 재사용할 수 있는 것

v1 디렉토리(`trustedoss-portal`의 현재 코드)에서 참고할 파일:

| v1 파일 | 재사용 방법 |
|---------|------------|
| `ort/rules.kts` | ORT 라이선스 룰셋 그대로 복사 |
| `webapp/backend/integrations/dt.py` | DT API 클라이언트 로직 참고 (안정화 레이어 추가) |
| `webapp/backend/tasks/scan.py` | Celery 스캔 태스크 구조 참고 |
| `webapp/frontend/src/components/` | shadcn/ui 컴포넌트 설계 참고 |
| `webapp/frontend/tests/_harness/` | Playwright 하네스 패턴 참고 |
| `docs/design-concept.md` | 디자인 시스템 참고 (v2에서 Black Duck 스타일로 발전) |

---

## 디자인 시스템 (v2)

**컨셉**: Enterprise SCA — Compact, Information-Dense, Risk-First

| 항목 | 결정 |
|------|------|
| 네비게이션 | 고정 사이드바 (224px) + 상단 헤더 (48px) |
| 상세 보기 | 드로어 (오른쪽 슬라이드, 페이지 이동 없음) |
| 리스크 색상 | Critical `#dc2626` / High `#ea580c` / Medium `#ca8a04` / Low `#2563eb` / Info `#71717a` |
| Primary | `#0f172a` (다크 네이비, Black Duck 스타일) |
| 테이블 밀도 | Compact (행 높이 40px) |
| 실시간 | WebSocket 진행 바 (스캔 30분+ 대기 UX) |
| 로딩 | 스켈레톤 UI |
| 필터 | 상단 인라인 (모달 없음) |
| 폰트 | Inter (UI) + JetBrains Mono (코드/해시/CVE ID) |
