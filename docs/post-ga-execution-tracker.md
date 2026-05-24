# TrustedOSS Portal — v2.1~v2.3 실행 트래커 (Execution Tracker)

> 작성일: 2026-05-24 | 기준: main HEAD `#144` (17ea353)
> **이 문서는 v2.1~v2.3의 "어떻게/언제/지금 어디" 단일 진실(single source of truth)이다.**
> "무엇을/왜"의 전략 근거는 [`post-ga-roadmap.md`](./post-ga-roadmap.md)에 있다(상위 문서).
> 본 문서는 그 로드맵을 **PR 단위로 분해 + 진행 상태를 살아있게(living) 추적**한다.
>
> **운영 규칙: 모든 PR 머지 직후 이 문서의 해당 체크박스를 갱신한다. 갱신 안 된 진행은 진행이 아니다.**

---

## 0. 진행 대시보드 (한눈에)

| 마일스톤 | 트랙 | PR 수 | 완료 | 상태 |
|---|---|---|---|---|
| v2.1 | A. VEX 소비 (트리아지) | 3 | 3 | ✅ 완료 (#145,#148,#150) |
| v2.1 | B. 평가·배포 경로 | 5 | 5 | ✅ 완료 (#146,#147,#149,#151,#152) |
| v2.2 | 리메디에이션 + 정책 | 10 | 5 | 🟦 a(#153,#154,#156)+문서(#155)+b1(#157)+c1(#158) 완료 · 진행 b2(통합)·c2 |
| v2.3 | 무결성 + 우선순위화 | 6 | 0 | ⬜ 대기 |
| — | 운영 레인 (외부 블로커) | 4 | 0 | ⬜ 대기 |

범례: ⬜ 대기 · 🟦 진행중 · ✅ 완료 · ⛔ 블로킹

---

## 1. 흐지부지 방지 메커니즘 (Anti-Fizzle Backbone)

> 이 마일스톤이 "중간 이후 흐지부지"되는 전형적 원인과 그 차단 장치. **이 절이 본 계획의 핵심이다.**

| # | 실패 원인 | 차단 장치 |
|---|---|---|
| 1 | "지금 어디였지?" 컨텍스트 유실 | **이 트래커가 유일 SoT.** 매 PR 머지 시 체크박스+한 줄 갱신. 세션 종료 시 `docs/sessions/` 핸드오프. |
| 2 | 거대 PR이 리뷰에서 정체 | **PR 단위 분해.** 각 PR은 독립 머지 가능 + green (CLAUDE.md 규칙 6 "미완성 WIP 없음"). |
| 3 | "나중에 할 일" 백로그 누적 | **PR마다 완성도 동시** (규칙 7): lint+typecheck+test+coverage≥80%+EN/KO+Docusaurus 동시. DoD 미충족 = 미머지. |
| 4 | 진행 중 의존성 뒤늦게 발견 | **각 PR에 의존(`dep:`) 명시.** 선행 PR 미완 시 착수 금지. §6 의존성 그래프 참조. |
| 5 | 외부 블로커가 코드까지 정지시킴 | **운영 레인 분리**(§5). 이미지 게시·클라우드 배포 등 사용자 작업은 코드 트랙과 비동기. |
| 6 | 보안 결함이 머지 후 발견 | **Producer-Reviewer 게이트.** untrusted-input·외부쓰기·서명 키 PR은 `security-reviewer` 통과 후 머지. |
| 7 | 가정 오류로 헛작업 (Explore 오탐 전력 있음) | **착수 전 실코드 검증.** frontend-dev/backend가 심볼 존재 확인 후 구현. → [[feedback-explore-gap-false-positives]] |
| 8 | 초반 의욕 소진 | **각 트랙은 최소·저위험·독립가치 PR로 시작**(VEX export A1 / `/health/ready` B1)해 조기 승리로 모멘텀 확보. |
| 9 | main이 조용히 red로 누적 | **자율 머지여도 main CI(integration/brittle) 확인.** → [[feedback-autonomous-merge-ci-check]] |

**머지 1건 = 완결된 거래(transaction):** 머지 → 트래커 갱신 → (트랙 마지막이면) 마일스톤 종료조건 점검 → 다음 PR. 이 사이클을 깨지 않는다.

---

## 2. 실행 모델 (확정)

- **v2.1 = 병렬 2트랙.** Track A(VEX, `backend-developer`+`security-reviewer`)와 Track B(배포/평가, `devops-engineer`+`doc-writer`+`backend-developer`)는 담당·파일 영역이 겹치지 않아 병렬. (A: `services/vulnerability_service.py`·`api/v1/vulnerabilities.py`·VEX UI / B: `charts/`·`docker-compose*.yml`·`docs-site/`·`terraform/`·`core/`health.)
- **v2.2, v2.3 = 순차.** v2.1 종료조건 충족 후 v2.2, v2.2 후 v2.3.
- **데모 호스팅 = 기존 GCP terraform 재사용** (이미 Cloud Run scale-to-0 + Cloud SQL + Redis 구축됨, idle ~$46/mo). 신규 IaC 불필요 — 일일 리셋 + read-only 모드만 추가.

---

## 3. 검증된 현재 상태 (2026-05-24, 실코드 기준)

> 로드맵의 "미구현" 주장을 실코드로 재확인하며 발견한 **정정 사항 포함**. 계획은 이 사실 위에 선다.

**v2.1**
- EPSS ✅ 완전 구현 — 모범 템플릿: `alembic/versions/0015_vulnerability_epss.py`, `tasks/dt_resync.py`(`_coerce_epss`), `services/vulnerability_service.py`(sort=`epss`/`min_epss`), `services/policy_gate.py`(`GATE_EPSS_THRESHOLD`), 프론트 `features/projects/lib/epss.ts`+`VulnerabilitiesTab.tsx`, 테스트 양쪽. **신규 컬럼 추가 패턴은 이걸 그대로 따른다.**
- VEX: **⚠️ 정정** — `vulnerabilities.py:221`은 export가 **아니라** status 전이 PATCH다. 실제로는 (a) 내부 finding별 VEX status(7-state enum `models/scan.py:88-96`, 전이행렬 `vulnerability_service.py:148-158`, `analysis_state`가 CycloneDX `analysis.state` 미러 `vulnerability_service.py:856-857`, 감사로그 자동)만 있고, (b) **VEX 문서 export 없음**, (c) **VEX 문서 import(소비) 없음**. → A 트랙은 export+import **둘 다** 만들어야 왕복 테스트 성립.
- `vulnerability_findings` 컬럼: `status`/`analysis_state`/`analysis_justification`/`analysis_response`(JSONB)/`analyst_user_id`/`analyzed_at` 보유. `fixed_version`·VEX 출처 컬럼 없음.
- 감사로그: SQLAlchemy `before_flush` 리스너 자동(`core/audit.py`) + Celery용 명시 `_emit_audit`. 민감/PII 마스킹 내장.

**배포/평가**
- Helm `charts/trustedoss` 0.1.0: backend/worker/beat Deployment·configmap·service·SA·HPA만. **Ingress/TLS·postgres·redis·frontend·migration Job 없음.** 이미지 게시는 `release.yml`(멀티아치 ghcr)로 **이미 됨**.
- compose: `profiles:` 미사용(`-f` 오버레이 관례). **eval 경량 프로파일 없음.** DT 4GB 힙은 `docker-compose.dt.yml`.
- health: `/health`(liveness, `main.py:207`)·`/v1/admin/health`(super admin 전체)만. **`/health/ready` 없음.**
- AUTO_MIGRATE: `docker-entrypoint.sh` + `alembic/env.py`의 `pg_advisory_xact_lock`(키 `TOSSMIGR`). owner/app 역할 분리(`DATABASE_URL_OWNER`/`_APP`).
- seed: `scripts/seed_demo.py`(멱등, APP_ENV=dev/demo 가드, org/3팀/5유저/5프로젝트/10 CVE). **1커맨드 평가 시드·일일 리셋·read-only 데모모드 없음.**

**v2.2/v2.3**
- `fixed_version`: **고스트 필드** — 스키마엔 있으나 서비스가 항상 `None` 반환(`vulnerability_service.py`). 채우는 로직 없음.
- 의존성 그래프: `ScanComponent.direct`(bool)·`dependency_path`·`dependency_scope`·`raw_data`만. **depth/`dependsOn` 그래프 없음.**
- GitHub: webhook 수신(`api/v1/webhooks/github.py`) + PR 코멘트 쓰기(`services/sca_comment.py`, 토큰은 env `GITHUB_TOKEN`만, DB 미저장). **GitHub App·PR 자동생성 없음.**
- 라이선스 정책: 정적 카탈로그(`tasks/scan_source.py:1637-1673`) + SPDX expression 평가(`_classify_license_category`). **per-team/org 정책 모델·편집 UI 없음.**
- Policy Gate: 완전 구현(critical CVE·forbidden 라이선스·EPSS 3조건, `services/policy_gate.py`).
- SBOM: 생성/export 완전(cyclonedx json·xml / spdx json·tv, `api/v1/sbom.py`·`services/sbom_export.py`·`ScanArtifact`). **cosign/in-toto/SLSA 서명 전무.**
- Reachability(취약점 도달성): **전무**(govulncheck/call-graph 없음).

**문서/데모 인프라**
- Docusaurus v3.6.3, OpenAPI 플러그인 **없음**, `reference/api-overview.md`는 수기. **OpenAPI drift 게이트는 있음**(`tests/unit/test_openapi_contract.py`). 정적 스펙 export 스크립트 없음.
- terraform GCP IaC **완비**(Cloud Run scale-to-0 + Cloud SQL 17 + Memorystore). **일일 리셋·read-only 데모모드 없음.**

---

## 4. PR 단위 작업 분해

> 표기: `dep:` 선행 PR · `rev:` security-reviewer 필수 · `owner:` 주담당 에이전트.
> 모든 PR 공통 DoD(반복 생략): lint+typecheck+test green · 신규코드 line coverage ≥80% · 핵심 시나리오 Playwright green · EN/KO 동시 · Docusaurus 동시 · Alembic forward-only(expand→contract).

### v2.1 — Track A: VEX 소비 (트리아지 신뢰성)

- [x] **A1 — VEX 문서 export** ✅ #145 (머지 `2c959ca`) `owner: backend-developer`
  - `GET /v1/projects/{id}/vex?format=openvex|cyclonedx` — 현재 finding status로부터 VEX 문서 생성.
  - 내부 7-state → VEX 상태 매핑표 확정·문서화 (OpenVEX: `not_affected`/`affected`/`fixed`/`under_investigation` · CycloneDX `analysis.state`: `resolved`/`exploitable`/`in_triage`/`false_positive`/`not_affected`). justification 보존.
  - 바이트 안정성(byte-stability) 테스트 — SBOM 선례 [[project-qa-followup-marathon]] BUG-006 동일 기법(정렬·canonical JSON).
  - **저위험·읽기전용 → 트랙 모멘텀 시작점.** 모델 변경 없음.
- [x] **A2 — VEX 문서 import (소비)** ✅ #148 (머지 `a6ea314`) — security-reviewer Critical/High 0, Medium+Low2 fix-first 후 회귀 PASS. 마이그레이션 0016(provenance). `dep: A1` · `rev: ✅` `owner: backend-developer → security-reviewer`
  - `POST /v1/projects/{id}/vex:import` (OpenVEX/CycloneDX VEX 업로드) → 문장을 finding(vuln id + purl/component)에 매칭 → `not_affected`/`suppressed`/`fixed` 자동 전이 + justification·출처 보존.
  - 기존 `STATUS_TRANSITIONS` + 권한 게이팅 준수(`suppressed`는 team_admin↑). import는 team_admin 게이팅 액션으로 결정.
  - 출처 추적 컬럼(`vex_source`/`analysis_source`) 추가 — `db-designer` expand 마이그레이션(EPSS 0015 패턴).
  - 멱등성: 동일 VEX 재import = no-op (EPSS upsert 패턴).
  - **adversarial 입력 테스트 필수**(untrusted-input 규칙 [[feedback-adversarial-input-parametrize]]): 깨진 VEX, 충돌 문장, 미지 vuln/purl, oversized, justification 인젝션, 중복 문장, enum 외 상태.
  - **왕복 일관성 테스트**: export→import→export 안정.
- [x] **A3 — VEX 소비 UI + 필터 + i18n** ✅ #150 (머지 `1a796fb`) — import 다이얼로그·export 메뉴·"VEX 억제" 필터·드로어 출처 배지. XSS-inert(React 기본 이스케이프, `dangerouslySetInnerHTML` 0건) 테스트 포함. **Track A 완결.** `dep: A2` `owner: frontend-dev → i18n-specialist`
  - `VulnerabilitiesTab.tsx`(EPSS 컬럼 위치): VEX import 버튼(team_admin), "VEX로 억제됨" 필터, 드로어에 출처 배지.
  - EN/KO 키 + `npm run i18n:check` 통과(복수형 금지 [[feedback-frontend-i18n-no-plural-check]]).
  - E2E: VEX 업로드 → finding suppressed → 필터 노출.
  - **종료조건:** 외부 VEX로 노이즈 억제 가능 · 왕복 안정 · adversarial green · security-reviewer green.
  - **보안 후속(A2 리뷰 Info 핸드오프):** `analysis_justification`·`vex_origin.*`(author/id 등)는 의도적으로 미이스케이프 저장됨 → A3 UI는 반드시 React 기본 텍스트 이스케이프로 렌더(`dangerouslySetInnerHTML` 금지) + `<script>` justification이 inert하게 렌더되는지 테스트 추가.

### v2.1 — Track B: 평가·배포 경로

- [x] **B1 — `/health/ready`** ✅ #146 (머지 `aefc610`) `owner: backend-developer`
  - `GET /health/ready`: alembic 마이그레이션 HEAD vs `alembic_version` 비교 → at-head면 200, 아니면 503. (AUTO_MIGRATE follow-up, 로드맵 §3 security-reviewer M2.)
  - compose/Helm의 worker·beat `depends_on` 게이트를 `service_healthy`(liveness) → readiness 기준으로 전환.
  - 테스트: head보다 뒤처지면 503. **저위험·독립 → 트랙 시작점, Helm migration Job 설계 선행.**
- [x] **B2 — 평가용 경량 compose 프로파일 + 1커맨드 시드** ✅ #147 (머지 `c745fbc`) — `docker-compose.eval.yml`(2vCPU/4GB, DT-less) + `scripts/eval-up.sh`. `dep: B1` `owner: devops-engineer + backend-developer`
  - `docker-compose.eval.yml` 오버레이(관례상 `-f`): DT 외부연결 또는 축소힙/비활성(circuit breaker+PG 캐시로 DT-less 동작), 목표 2vCPU/4GB.
  - `seed_demo.py`를 1커맨드 경로로 연결(`scripts/eval-up.sh` 또는 install.sh `--seed`).
  - **종료조건:** 2vCPU/4GB 호스트에서 eval 기동 + 시드 1커맨드.
- [x] **B3 — Helm chart 프로덕션화** ✅ #149 (머지 `7216b41`) — bundled/external PG+Redis·frontend·ingress TLS(cert-manager)·migration Job(pre-install/upgrade, owner role)·AUTO_MIGRATE=false·OCI `chart-release.yml`+ArtifactHub 메타. Chart 0.2.0. `dep: B1` `owner: devops-engineer`
  - 신규 템플릿: postgres(StatefulSet, 번들/외부 토글)·redis·frontend Deployment+Service·Ingress+cert-manager TLS·**migration Job**(`pre-install`/`pre-upgrade` 훅, `alembic upgrade head`, owner 역할, 1회).
  - backend 파드 `AUTO_MIGRATE=false`(로드맵 §3: advisory lock은 안전망일 뿐). values: 번들/외부 DB·Redis, ingress host/TLS, 이미지 태그 `release.yml` 동기.
  - OCI(ghcr) 차트 게시 + ArtifactHub 등록(등록은 운영 레인 O3).
  - **종료조건:** `helm install`로 단일 네임스페이스 기동, lint/template green.
- [x] **B4 — API 레퍼런스 호스팅** ✅ #151 (머지 `05e7961`) — redocusaurus `/reference/api` + `scripts/dump_openapi.py`. docs.yml가 빌드 시 스펙 재생성(버전-안정), backend 변경에도 재배포. (브리틀했던 ci.yml byte-exact 게이트는 제거.) `dep: 없음` `owner: doc-writer + backend-developer`
  - `scripts/dump_openapi.py`: FastAPI `app.openapi()` → 정적 스펙 파일. 기존 drift 게이트(`test_openapi_contract.py`)를 진실원으로 재사용.
  - Docusaurus에 OpenAPI 통합 — **redocusaurus 권장**(단일 스펙 임베드, 저유지보수) / docusaurus-openapi-docs(엔드포인트별 MDX, "try-it"·고유지보수)는 대안. `reference/api-overview.md` 연결.
  - `docs.yml`이 빌드, EN/KO.
  - **종료조건:** 공개 문서에서 전체 API 탐색 가능.
- [x] **B5 — 라이브 데모 (GCP terraform 재사용)** ✅ #152 (머지 `9d87763`) — DemoReadOnlyMiddleware(allowlist deny-by-default, 우회 하드닝, security-reviewer "우회 없음")·OAuth 데모 차단(M-1)·비번 평문로그 제거(M-2)·membership-scoped reset(L-3)·terraform demo_reset(Cloud Scheduler→Job)·프론트 배너/게이팅. 실제 배포는 O2. **Track B 완결.** `dep: B2` · `rev: ✅` `owner: devops-engineer + backend-developer + frontend-dev`
  - 잔여 Info(비머지): demo-read-only problem `type` URI가 미들웨어(`urn:`)와 OAuth 핸들러(`https:`)로 갈림 — 차후 상수 통일(선택).
  - **read-only 데모모드(신규 백엔드):** `DEMO_READ_ONLY` 런타임 가드(`os.getenv` 규칙 11) 미들웨어로 쓰기 차단(또는 demo 역할). 프론트는 플래그 시 쓰기 액션 숨김/비활성.
  - **일일 자동 리셋:** Cloud Scheduler → Cloud Run Job(seed_demo drop+reseed) 또는 Actions cron.
  - terraform 재사용(Cloud Run scale-to-0). README/랜딩에서 연결.
  - 실제 GCP 배포는 운영 레인 O2(사용자 클라우드 자격).
  - **종료조건:** 설치 없이 핵심 화면 체험, 데모 격리·일일 리셋 검증.
  - **v2.1 마일스톤 종료조건:** Track A+B 전 PR 머지 green · 데모 도달 가능 · EN/KO · 문서 동기 → §7 v2.1 게이트 통과 후 v2.2 착수.

### v2.2 — 리메디에이션 & 정책 (순차)

> 선행: v2.1 종료. **2.2-b 착수 전 GitHub 쓰기 통합 방식 결정(§8 D1).**

- [x] **2.2-a1 — `fixed_version` 실데이터화** ✅ #153 (머지 `979f459`) — DT findings patched 버전 추출 → `vulnerability_findings.fixed_version`(마이그 0017), adversarial 30케이스. `owner: scan-pipeline-specialist`
  - 고스트 필드 제거: DT/OSV 피드에서 fix 버전 수집 → `vulnerability_findings`/`vulnerabilities`에 저장(필요 시 expand 마이그레이션). 드로어 노출.
- [x] **2.2-a2 — 의존성 그래프 수집** ✅ #154 (머지 `8b1799e`) — `component_dependency_edges` 테이블 + `scan_components.depth`(마이그 0018), cycle-safe BFS, adversarial 전수. `dep: 2.2-a1` `owner: scan-pipeline-specialist`
  - cdxgen `dependsOn` 파싱 → depth/그래프 저장(신규 테이블 또는 `raw_data` 구조화). 직접/전이 depth 산출.
- [x] **2.2-a3 — 업그레이드 추천 엔진 + UI** ✅ #156 (머지 `69c4b96`) — 컴포넌트별 최소 안전 업그레이드(semver 최대), 드로어·게이트 코멘트 노출, 우선순위 신호(direct/severity/EPSS), adversarial 99%. **a-트랙(리메디에이션 추천) 완결.** `dep: 2.2-a2` `owner: backend-developer + frontend-dev`
  - `fixed_version`+그래프+severity/EPSS → "최소 안전 업그레이드" 계산. 취약점 드로어·게이트 코멘트에 권장 버전.
- [x] **2.2-b1 — GitHub 쓰기 자격 모델** ✅ #157 (머지 `96fdeae`) — `github_app_credentials`+`github_app_installations`(마이그 0019), `core/crypto.py` Fernet 암호화저장(prod fail-closed), 설치별 단기 RS256 App JWT→installation token(`mint_installation_token`), per-project 옵트인, per-team RBAC. security-reviewer PASS-WITH-FOLLOWUPS(Crit/High 0)→Medium 3(redirect-disable·GITHUB_API_URL allowlist·installation_id 재검증)+Low 2 fix-first(`70eb877`). `dep: 2.2-a3` · `rev: ✅` `owner: backend-developer → security-reviewer`
  - GitHub App/토큰 자격 DB 저장(암호화) — 현 env-only 확장. 최소 권한·옵트인 per-project.
  - **후속(비머지, b3 전):** MultiFernet 롤링 키회전(현재 단일키 회전 시 기존 자격 brick) — 트래커 task #8.
- [ ] **2.2-b2 — 생태계 어댑터(npm 우선) + dry-run** `dep: 2.2-b1` `owner: scan-pipeline-specialist`
  - manifest 수정 어댑터(npm→pip→maven 순). dry-run 기본.
- [ ] **2.2-b3 — 자동 PR 생성(옵트인) + UI + 감사** `dep: 2.2-b2` · `rev: ✅` `owner: backend-developer + frontend-dev → security-reviewer`
  - 브랜치→PR 자동생성, 옵트인, 감사로그. **종료조건: 최소 1개 생태계 PR 생성 + 보안리뷰 통과.**
- [x] **2.2-c1 — 동적 라이선스 정책 모델** ✅ #158 (머지 `2453501`) — `license_policies` 테이블(마이그 0020, org/team 스코프, `category_overrides`/`license_exceptions`(시한부 waiver)/`unknown_license_category` posture/`compound_operator_strategy`/`enabled`), CRUD API `/v1/license-policies`, `get_effective_policy` 우선순위 resolver(team>org>static) + 단일-id `effective_category` 헬퍼, adversarial 스키마검증. b1과 병렬개발→통합 시 마이그 0019→0020 재번호. `policy_gate` 미수정(c2). `dep: 2.2-a3 (병렬 가능)` `owner: db-designer + backend-developer`
  - per-team/org 정책(허용/조건부/금지 + 예외 + SPDX expression 룰) 모델 + 마이그레이션.
- [ ] **2.2-c2 — Policy Gate 동적 룰 평가** `dep: 2.2-c1` `owner: backend-developer`
  - 게이트가 정적 lookup 대신 동적 룰 평가(정적 카탈로그는 기본값으로 유지). SPDX expression **adversarial 테스트**([[feedback-adversarial-input-parametrize]] normalize_spdx_id 재귀 DoS 선례).
- [ ] **2.2-c3 — 정책 편집 Admin UI** `dep: 2.2-c2` `owner: frontend-dev + i18n-specialist`
- [ ] **2.2-c4 — 라이선스 텍스트/의무 카탈로그 보강** `dep: 2.2-c1` `owner: backend-developer + doc-writer`
  - **v2.2 마일스톤 종료조건:** ≥1 생태계 자동 PR + 보안리뷰 · 코드 변경 없이 팀이 정책 편집 · adversarial SPDX green.

### v2.3 — 공급망 무결성 & 우선순위화 (순차)

> 선행: v2.2 종료.

- [ ] **2.3-s1 — SBOM 서명 인프라(cosign)** `rev: ✅` `owner: devops-engineer + backend-developer → security-reviewer`
  - worker 이미지에 cosign. SBOM 생성 시 서명(keyless OIDC vs key-based 결정 §8 D2). 키 취급은 보안리뷰.
- [ ] **2.3-s2 — in-toto attestation + SLSA provenance** `dep: 2.3-s1` `owner: scan-pipeline-specialist`
  - attestation 생성. CISA 2025(component hash·tool/generation context)·NTIA 7요소 점검.
- [ ] **2.3-s3 — 서명 다운로드 UX + 검증 문서** `dep: 2.3-s2` `owner: frontend-dev + doc-writer`
  - 다운로드 시 서명 동봉. **종료조건: `cosign verify` 외부 검증 가능.**
- [ ] **2.3-r1 — Reachability 스캔 태스크(Go govulncheck 우선)** `owner: scan-pipeline-specialist`
  - Celery 태스크(규칙 3: 동기 금지). finding에 reachability 신호 저장(expand 마이그레이션). 베스트에포트 라벨.
- [ ] **2.3-r2 — reachability 정렬·게이트·UI 배지** `dep: 2.3-r1` `owner: backend-developer + frontend-dev`
- [ ] **2.3-r3 — 차기 언어 확대(베스트에포트)** `dep: 2.3-r2` `owner: scan-pipeline-specialist`
  - **v2.3 마일스톤 종료조건:** 서명 SBOM 외부 검증 · ≥1 언어 reachable/unreachable 구분 노출.

---

## 5. 운영 레인 (외부 블로커 — 코드와 비동기)

> 사용자(GitHub/클라우드 admin) 작업. **코드 PR을 막지 않는다.** 준비되면 처리.

- [ ] **O1 — v2.0.1 이미지 게시** — 첫 `v2.0.1` 태그 cut → `trustedoss` org Actions `packages:write` + ghcr public → `install-uat.yml`의 `published-image-pull` `continue-on-error` 제거. **공개 차단점.**
- [ ] **O2 — 데모 GCP 배포** — terraform apply(사용자 GCP 자격). B5 코드 선행.
- [ ] **O3 — Helm 차트 ArtifactHub 등록** — B3 완료(`chart-release.yml` 추가됨). 남은 운영 작업: `chart-vX.Y.Z` 태그로 OCI 게시 → ghcr `charts/trustedoss` 패키지 public → ArtifactHub 등록(`artifacthub-repo.yml`의 repositoryID 기입) + `docs/static/img/logo.png` 추가(Chart icon 참조).
- [ ] **O4 — 데모/문서 스크린샷 갱신** — VEX·정책편집·서명 UI 반영(EN/KO). 각 UI PR 후속.

---

## 6. 의존성 그래프 (착수 순서)

```
v2.1 (병렬 2트랙)
 ├─ Track A:  A1(export) ─→ A2(import, rev) ─→ A3(UI)
 └─ Track B:  B1(/health/ready) ─→ B2(eval+seed) ─→ B5(데모, rev)
              B1 ─→ B3(Helm)
              B4(API문서, 독립) ──────────────────────────────────┐
   v2.1 게이트(§7) ◀── A3 · B3 · B4 · B5 ───────────────────────┘
        │
        ▼
v2.2 (순차)  a1→a2→a3 ─→ b1(rev)→b2→b3(rev)
                       └→ c1→c2→c3 ;  c1→c4     (c트랙은 a3 후 b와 병렬 가능)
   v2.2 게이트(§7)
        │
        ▼
v2.3 (순차)  s1(rev)→s2→s3   ‖   r1→r2→r3
   v2.3 게이트(§7)
```

---

## 7. 마일스톤 종료 게이트 (다음으로 넘어가기 전 필수 점검)

각 마일스톤은 아래를 **모두** 만족해야 "완료" 선언 + 다음 착수.

- **v2.1: ✅ 충족 (2026-05-24, 8 PR #145–#152 머지, main CI green).** A1–A3·B1–B5 머지 green · VEX export/import/UI 왕복 + adversarial + security-reviewer green · helm 프로덕션 차트(lint/template) · `/health/ready` 게이트 전환 · eval 프로파일 · API 레퍼런스(redoc) 공개 · 데모 read-only+일일리셋(코드) · EN/KO·문서 동기. **잔여=운영 레인만**: O1(이미지 게시·공개차단점), O2(데모 GCP 배포), O3(차트 ArtifactHub). → v2.2 착수 가능.
- **v2.2:** ≥1 생태계 자동 PR 생성 + security-reviewer green(b1·b3) · 팀이 코드 변경 없이 정책 편집 · SPDX adversarial green · `fixed_version` 실데이터 노출 · main CI green.
- **v2.3:** 서명 SBOM `cosign verify` 외부 검증 + security-reviewer green(s1) · ≥1 언어 reachability 구분 노출(베스트에포트 라벨) · main CI green.

---

## 8. 미결 결정 (착수 시점에 확정)

- **D1 ✅ 결정(2026-05-24): GitHub App** — 설치형, per-repo 세밀 권한(contents/pull_requests:write), 설치별 단기 토큰, 멀티테넌트. 2.2-b1에서 App 자격 모델 구현, security-reviewer 동반.
- **D2 (2.3-s1 착수 전): cosign 서명 방식** — keyless(OIDC, CI 친화, 키 관리 없음) vs key-based(온프렘/에어갭 적합). → 셀프호스팅 포지셔닝상 key-based 기본 + keyless 옵션 검토.
- **D3 (B4): OpenAPI 통합 라이브러리** — redocusaurus(권장) vs docusaurus-openapi-docs. 착수 시 빌드 호환 확인.

---

## 9. 세션 핸드오프 규약

- 세션 종료 시 `docs/sessions/<YYYY-MM-DD>-v2.x-<topic>.md` 작성(`v2-execution-plan.md` §7 양식).
- 다음 세션 첫 메시지: "이 트래커(`docs/post-ga-execution-tracker.md`) §0 대시보드 + 미체크 PR부터 이어가자."
- **이 문서의 체크박스·대시보드가 항상 현재 진실.**
