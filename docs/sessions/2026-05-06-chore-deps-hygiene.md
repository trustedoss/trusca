# Session Handoff — 2026-05-06 — chore PR — dependency hygiene + image-scan hard-fail 복원

## 1. 무엇을 했나

- **chore PR 작성 완료** — feature 브랜치 `chore/deps-hygiene-image-scan-hardgate` (working dir `~/projects/trustedoss-portal/`). PR #9 핸드오프 §4 "별도 chore PR F (의존성 hygiene)" 항목 처리. 머지는 사용자 명령 대기.
- **호환 매트릭스 검증 (devops-engineer 단독 선행)**: `fastapi 0.115.6` 이 `Requires-Dist: starlette<0.42.0,>=0.40.0` 으로 starlette 0.49.x 를 거부 → fastapi 동시 bump 필수. `fastapi 0.120.x` 가 starlette<0.50.0 을 받아주는 첫 라인. `python-jose 3.4.0` 도 `pyasn1<0.5.0` 을 핀해 pyasn1 0.6.3 차단 → `python-jose 3.5.0` 로 동시 bump 필요. `pip install --dry-run` 으로 충돌 0 확인. fastapi 0.120+ 가 신규 transitive `annotated-doc>=0.0.2` 추가 — 명시 핀.
- **starlette 0.41 → 0.49 영향 분석 (backend-developer 분담)**: 우리 코드에 영향 0건 결론.
  - `core/middleware.py` 의 `RequestIDMiddleware`/`AuditContextMiddleware` 는 순수 ASGI(BaseHTTPMiddleware 미사용) → 0.45 의 BaseHTTPMiddleware 예외 전파 변경 영향 없음.
  - `iter_text/iter_bytes/iter_json` 미사용 → 0.47 deprecated alias 제거 영향 없음.
  - `request.form() / UploadFile / File()` 미사용 → 0.46 multipart strict 영향 없음.
  - `TestClient` 는 `tests/integration/test_ws_scan_progress.py` 에서만 사용 → 0.48 httpx 전환은 pytest 회귀로 검증.
- **requirements.txt 핀 갱신** (15 라인 변경):
  - `fastapi`: `0.115.6` → **`0.120.4`**
  - `starlette`: 신규 명시 핀 **`0.49.3`** (CVE-2025-62727 Range header DoS 패치)
  - `python-multipart`: `0.0.20` → **`0.0.22`** (CVE-2026-24486 path traversal 패치)
  - `pyasn1`: 신규 명시 핀 **`0.6.3`** (CVE-2026-30922 ASN.1 unbounded recursion DoS 패치)
  - `python-jose[cryptography]`: `3.4.0` → **`3.5.0`** (pyasn1 0.6.x 호환 unblocker)
  - `annotated-doc==0.0.4` 신규 추가 (fastapi 0.120+ 신규 transitive)
  - 주석에 각 핀 변경의 CVE 번호와 변경 사유 명시.
- **`.github/workflows/ci.yml` image-scan hard-fail 복원**:
  - Trivy step 의 step-level `continue-on-error: true` 제거.
  - 잡 위 주석 "Soft-fail until Phase 1 dependency hygiene PR..." → "Hard gate restored after the Phase 1 dependency hygiene chore PR bumped..." 로 갱신.
  - Trivy 설정(action 버전, severity HIGH/CRITICAL, ignore-unfixed, exit-code 1, vuln-type os/library) 모두 그대로.
- **Producer-Reviewer 1라운드** (security-reviewer): 평결 = **CONDITIONAL PASS** (Critical 0 / High 0 / Medium 1 / Low 2 / Info 1). 머지 차단 항목 없음 — 조건은 "재빌드된 worker 이미지의 Trivy 스캔 통과"이며, 본 PR 의 image-scan CI 잡 자체가 그 검증 채널.
  - **[Medium] worker 이미지의 비-Python 레이어 (cdxgen/ORT/Trivy/JRE/Node) 가 추후 advisory 발표 시 main 을 즉시 red 로 만들 수 있음** — image-scan hard-fail 의 정책 비용. follow-up: 야간 Trivy soft-fail 잡 추가 검토.
  - **[Low] python-jose 는 upstream 유지보수 종료 상태** (3.5.0 이 마지막 릴리즈, 2025-01) — Phase 8 hardening 시 PyJWT/authlib 마이그레이션 권고. 우리 코드는 `algorithms=[JWT_ALGORITHM]` (HS256) 핀이라 CVE-2024-33663 미도달.
  - **[Low] CVE-2025-62727 (starlette Range DoS) 미도달** — backend grep 결과 `StaticFiles/FileResponse/StreamingResponse/Range` 사용 0건. hygiene 성격.
  - **[Info] CVE-2026-24486 (multipart) / CVE-2026-30922 (pyasn1) 도 미도달** — multipart 는 outbound DT 호출 헤더에만, pyasn1 은 PEM key parsing 경로(우리 HS256 대칭키라 ASN.1 미경유). hygiene 성격.

## 2. 결정 사항 / 변경된 가정

- **fastapi 동시 bump 채택**. 최소-변경 후보 0.120.4 선택 (0.115.x→0.119.x 라인은 starlette<0.49.0 핀해서 차단). 0.136.1(latest) 도 가능하지만 blast radius 가 크므로 0.120.4 가 minimal.
- **starlette / pyasn1 명시 핀**. 둘 다 transitive 였지만 직접 핀해서 Trivy / pip-audit 가 CVE 패치 버전을 결정적으로 검증하도록 함. 향후 fastapi 또는 python-jose bump 시 자동 상위 핀이 적용되는 위험 회피.
- **로컬 worker 이미지 빌드 + 풀 pytest 회귀 SKIP — CI 가 검증 채널**.
  - 로컬 Docker Desktop VM 디스크 포화(58.67GB 사용 / 16.13GB 회수 가능) 로 발생한 두 환경 문제:
    1. worker 이미지 apt 단계 GPG signature error (재현됨, 캐시 손상 또는 Release 키 만료 의심).
    2. postgres 컨테이너 checkpoint PANIC ("No space left on device") → 재시작 루프.
  - backend 이미지는 새 deps 로 정상 재빌드 — `fastapi-0.120.4 / starlette-0.49.3 / python-multipart-0.0.22 / pyasn1-0.6.3 / python-jose-3.5.0 / annotated-doc-0.0.4` 모두 컨테이너에 설치됨. 의존성 충돌 0.
  - ruff `All checks passed!` / mypy `Success: no issues found in 96 source files` (재빌드된 backend 이미지 기준).
  - 첫 pytest 회귀 시도(206초, postgres 디스크 패닉 직전) 결과 "93 failed / 273 passed / 33 skipped" — 그러나 이 결과는 postgres 가 패닉 루프로 진입하던 시점과 겹치므로 신뢰 불가. 재실행 시 모든 통합 테스트 skip(`_migrate_once` 의 alembic upgrade 가 connection refused).
  - **사용자 정책**: rm/push/docker prune 같은 destructive 명령은 사용자가 `!` 프리픽스로 — 디스크 정리 위임 후 wakeup 동안 응답 없어 비파괴 경로 채택.
  - **신뢰 채널**: GitHub Actions runner 는 클린 환경에서 worker 이미지를 처음부터 빌드 + Trivy 스캔 + 풀 pytest 회귀(unit + integration with postgres/redis sidecar) + e2e 실행. CI 결과가 본 PR 의 회귀 검증.
- **CONDITIONAL PASS 의 조건 = image-scan CI 잡 green**. CI 가 fail 하면 즉시 진단:
  1. Python 레이어 신규 CVE → 추가 bump.
  2. 비-Python 레이어 (Node/JRE/ORT/cdxgen/Trivy 자체) 신규 CVE → 해당 도구 bump 또는 `.trivyignore`.
  3. CI 만의 환경 차이 → image-scan hard-fail 일시 롤백 후 별도 PR.
- **MEMORY.md / CLAUDE.md 갱신 불필요** — 본 PR 은 기존 핀 갱신만, 새로운 아키텍처 결정 없음.

## 3. 현재 상태

- **머지된 PR**: #1 (54e858f), #2 (ca8ab41), #3 (9c19b5a), #4 (8ddedfb), #5 (4325835), #6 (55e67bd), #7 (d7bc929 + 93c41a4 merge), #8 (502f02f + ebb9c53 merge), #9 (f55e70d + 9da6b3c merge) + chore CI fix 4건 (de36a38, d5c1052, 6823100, e57a894).
- **진행 중 PR**: 없음. **본 세션 산출물 = `chore/deps-hygiene-image-scan-hardgate` 브랜치, 머지 대기**.
- **GitHub origin/main**: `e57a894` (chore CI fix — e2e rate-limit + image-scan workflow conclusion).
- **변경 규모**: **2 modified = 2 files** (working dir, 미커밋).
  - `apps/backend/requirements.txt` (+18 / -3)
  - `.github/workflows/ci.yml` (-10 라인 — Trivy step continue-on-error 제거 + 주석 단축).
- **통과 테스트**:
  - **backend 단위 / 통합**: 로컬 회귀 SKIP (디스크 문제). CI 가 검증.
  - **backend lint / typecheck**: ruff `All checks passed!` / mypy `--strict` clean (재빌드된 backend 이미지 기준).
  - **frontend**: 변경 없음 — 회귀 불필요.
- **문서 / i18n**: 변경 없음. 본 PR 은 인프라 / 의존성만.

## 4. 후속 backlog

- **CI image-scan 잡 green 확인** — 본 PR 푸시 직후 CI watch. fail 시 위 §2 의 3 분기로 진단.
- **PR #9 follow-up backlog 7개 (L-1~L-4 / I-1~I-3)** — 본 세션 미처리, 별도 chore PR.
  - L-1 / L-2 / L-4 (DoS 류) 우선.
  - L-3 (멀티 worker 전 Redis-backed WS connection counter) 는 멀티 replica 활성화 직전.
  - I-2 / I-3 는 후속 Phase 흡수 가능.
- **PR #8 follow-up backlog 6개 (L-1·L-2·L-3·L-4·I-2·I-3)** — scan-pipeline-specialist 주도 chore PR.
- **python-jose → PyJWT 마이그레이션** (Phase 8 hardening). 본 PR 의 security-reviewer Low 권고.
- **야간 Trivy soft-fail 잡 추가 검토** — 비-Python 레이어 CVE drift 를 차단 게이트와 분리해 관찰. security-reviewer Medium 권고.
- **로컬 Docker Desktop VM 디스크 정리** — 사용자 결정 사항. `docker system prune -a -f` (16.13GB 회수 가능) 또는 Docker Desktop 설정에서 디스크 늘리기. 본 세션의 worker 이미지 빌드 + postgres 안정성 모두 영향.
- **MEMORY.md 갱신 후보**: 본 chore PR 머지 후 `project_v2_roadmap.md` 또는 `project_v2_execution_plan.md` 인덱스에 "Phase 1 dependency hygiene chore 완료" 한 줄 추가.

## 5. 다음 세션 시작 지시문

```
chore PR 머지 후 Phase 3 PR #10 — Project Detail (Overview/Components 탭, 1만 행 가상 스크롤, 드로어).

TrustedOSS Portal v2 작업을 ~/projects/trustedoss-portal/ 에서 이어서 진행한다.

main HEAD = (chore PR 머지 후 갱신). 누적 머지: PR #1~#9 + chore CI fix 4건 + chore deps-hygiene 1건. CI green (image-scan hard-fail 복원 상태).

이번 세션 = Phase 3 PR #10 — Project Detail Overview + Components 탭.
docs/v2-execution-plan.md §3.4 표 3.1 / 3.2 / 3.3 산출.

시작 시 검증:
  docker-compose -f docker-compose.dev.yml ps  → 5/5 healthy
  gh run list --limit 3                          → main 최신 success
                                                  (image-scan hard-fail 상태)

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
  - main CI 전체 잡 success (image-scan 포함 hard-fail).
  - 신규/변경 backend + frontend coverage ≥ 80%.
  - Components 탭 1만 행에서 60fps 스크롤 (Lighthouse 측정).
  - Overview API p95 < 200ms (locust 또는 pytest-benchmark).
  - e2e 6 시나리오 green.
  - security-reviewer 평결 PASS 또는 PASS-with-follow-ups.

주의:
  - 사용자 정책: rm/push/docker prune 거부 — 사용자가 ! 프리픽스로.
  - CLAUDE.md 규칙 4 (DT Circuit Breaker — Components 탭이 vulnerabilities join), 11
    (os.getenv 런타임), 12 (인증 surface), 13 (CORS).
  - PR #9 follow-up backlog 7개 (L-1~L-4 / I-1~I-3) 별도 chore PR — Phase 3 와 병렬 가능.

세션 종료 시 docs/sessions/2026-05-XX-phase3-pr10-project-detail.md 를 §7 양식으로 작성.
```
