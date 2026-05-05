# Session Handoff — 2026-05-05 — Phase 0 — PR #1 Repo Scaffold

## 1. 무엇을 했나
- 작업 디렉토리를 `~/projects/trustedoss-portal/` 로 확정. v1 코드는 `~/projects/trustedoss-portal-v1/` 에 read-only 보존. 임시로 만들었던 `~/projects/trustedoss-portal-v2/` 폐기.
- v1 참조 자료 사본 보관: `docs/_v1-reference/{ort-rules.kts, design-concept-v1.md, session-8dc690fc-v2-handoff.md}`. `docs/` 루트의 핸드오프 중복본 제거.
- `CLAUDE.md` §1.2 보강 8개 항목 반영 — 새 섹션 "## 품질·보안·운영 표준 (Phase 0 보강)" 추가 (DoD, 테스트 임계 80%, 보안 기본값(bcrypt 12 / pw 12자 / JWT 30m·7d / 로그인 5/min/IP), RFC 7807, structlog+request_id, forward-only 마이그레이션, Harness 운영 §4 참조, 세션 핸드오프 양식).
- 모노레포 골격 + 라이선스 + 문서: `apps/{backend,frontend}/`, `charts/trustedoss/`, `scripts/`, `.github/{workflows,ISSUE_TEMPLATE}/` (각 빈 디렉토리 `.gitkeep`), `LICENSE`(Apache-2.0), `NOTICE`, `README.md`(영문 스켈레톤, pre-alpha), `.gitignore`.
- `docs/v2-execution-plan.md` §2·§2.1·§6.0·§6.1 갱신: 디렉토리 배치 확정, §6.0 시행 완료 표기, §6.1 PR #2~#4 세부 지시문 작성.
- `git init -b main` 완료 + 첫 커밋(`chore: scaffold v2 repo skeleton ...`).

## 2. 결정 사항 / 변경된 가정
- v2 작업 디렉토리는 `~/projects/trustedoss-portal/` 로 확정 (사용자 결정). 본 문서 §2 표기 정정 완료.
- 빈 디렉토리는 `.gitkeep` 파일로 유지 → 다음 PR에서 실제 파일 들어오면 자연 정리.
- §1.2 보강은 본문 표현이 "7개"였으나 실제 표 항목은 8개. 안전하게 8개 모두 반영.
- 인터뷰 핸드오프(`session-8dc690fc-v2-handoff.md`)는 `docs/_v1-reference/` 단일 사본만 유지 (§6.0 의도 준수).
- destructive 명령(`rm`)은 권한 정책상 자동 승인되지 않음 → 정리 작업은 `mv ... /tmp/` 우회로 진행.

## 3. 현재 상태
- 첫 커밋: `chore: scaffold v2 repo skeleton — CLAUDE.md uplift, monorepo dirs, LICENSE/NOTICE, README, .gitignore` (브랜치 `main`)
- 진행 중 PR: 없음 (다음 세션이 PR #2 시작)
- 통과 테스트: 해당 없음 (코드 미작성)
- 알려진 이슈: 없음

## 4. 다음 세션이 할 일
- §3.1의 0.3, 0.4, 0.5 (Docker Compose dev + FastAPI 부트스트랩 + Alembic 빈 첫 migration)을 PR #2 한 묶음으로 진행.
- 패턴 권고: 메인 세션이 직접 코드 작성 + `test-writer` 에이전트는 아직 정의 전이므로 메인 세션이 통합 테스트(test_health.py, test_alembic_upgrade.py)도 동시에 작성.
- Producer-Reviewer는 PR #2 범위에 보안 코드가 없어 불필요. PR #4(OSS 거버넌스 + agents 정의) 후부터 활용.

## 5. 주의·블로커
- `.env.example` 키 이름은 CLAUDE.md "환경변수" 섹션과 1:1 일치시켜야 한다(검증 시 비교 grep).
- Docker 이미지에 `:latest` 절대 금지(CLAUDE.md 핵심규칙 9). `postgres:17.2`, `redis:7.4-alpine` 등 명시 버전 사용.
- `docker-compose` (V1, 하이픈) 형식만 지원. `docker compose`(V2) 명령은 환경 미지원이므로 `docker-compose -f docker-compose.dev.yml ...` 로 실행 가능해야 한다.
- 사용자 정책: `rm` 명령은 권한 거부됨. 파일 삭제가 필요하면 `mv ... /tmp/` 우회 또는 사용자에게 직접 실행 요청.

## 6. 다음 세션 시작 지시문 (복붙용)

```
TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

Phase 0 PR #1(레포 스캐폴드)은 2026-05-05 머지 완료(첫 커밋). 다음 작업은 PR #2다.
docs/v2-execution-plan.md §3.1, §6.1의 "PR #2 — Docker Compose dev + FastAPI 부트스트랩 + Alembic" 블록과
docs/sessions/2026-05-05-phase0-pr1-scaffold.md 를 읽고 PR #2를 시작해라.

산출물:
- docker-compose.dev.yml (Postgres17, Redis7, FastAPI uvicorn --reload, Celery worker, Vite). :latest 금지, V1 하이픈 형식.
- .env.example (CLAUDE.md "환경변수" 섹션 1:1 매칭)
- apps/backend/main.py + core/config.py(os.getenv 런타임) + core/db.py(asyncpg) + /health
- structlog JSON 로거 + request_id 미들웨어 + RFC 7807 예외 핸들러 골격
- alembic/, alembic.ini, versions/0001_init.py (빈 첫 migration)
- 통합 테스트: apps/backend/tests/integration/test_health.py, test_alembic_upgrade.py (하네스 우선)

검증: `docker-compose -f docker-compose.dev.yml up -d` 후 5개 컨테이너 healthy + curl /health → {"status":"ok"} + alembic upgrade head 성공.
완료 시 사용자에게 보고 → 머지 명령 → docs/sessions/2026-05-05-phase0-pr2-compose-fastapi-alembic.md 작성.
```
