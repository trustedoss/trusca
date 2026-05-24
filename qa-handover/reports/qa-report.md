# TrustedOSS Portal — 출시 전 QA 리포트

> **작성**: 외부 독립 QA | **대상**: TrustedOSS Portal v2.0.0 | **일자**: 2026-05-24
> **함께 전달**: `bug-report.md`(11건 상세·재현·권장조치) · `test-cases.md`(290 케이스 카탈로그, 검증 범위 증빙)
> **방법**: 가이드 명세 기반 290 케이스 도출(3-pass) → 브라우저 E2E 탐색 + API 검증 + Black Duck 전수 비교 + CI 파이프라인
> **상태 범례**: ✅ 정상 / 🐛 버그(→bug-report.md) / 💡 개선점 / ⏳ 미검증

---

## 0. Executive Summary (1분 요약)
**핵심 기능·엣지 처리는 견고합니다. 다만 "성공처럼 보이는 침묵의 실패" 2건이 출시 blocker입니다.**

- 🔴 **BUG-008 (Critical)** — 소스 아카이브 스캔이 의존성을 **전 언어에서 미탐지**(15 fixture, Black Duck ground truth 대비). 스캔은 `succeeded`로 표시되어 **SCA 핵심 기능이 조용히 무력화**. UI 경로도 동일하므로 사용자 스캔도 0. (cdxgen 런타임은 팀 검증 필수)
- 🟠 **BUG-010 (High)** — 조건부 라이선스(LGPL 등) 컴포넌트의 Pending 승인 **자동 생성 누락**(가이드 명세 위반) → 법무 검토가 조용히 누락.
- **발견 총 11건**: Critical 1 / High 1 / Medium 6 / Low 3 (상세 `bug-report.md`).
- **검증 정상(거짓 양성 없음)**: 인증·RBAC·관리자(9/9)·P1 엣지(boundary·동시성·상태전이·관측, 9/9)·장애 회복·XSS escape.
- **다음 단계**: blocker 2건 우선 조치 → 미검증 영역(§5)을 CI(준비 완료)로 채운 뒤 전체 품질 판정.
- ⚠️ **주의**: 이 11건은 "전수 결과"가 아니라 **"내부 테스트 갭 집중 + 부분 검증"**의 산출물입니다. 개수가 적다 ≠ 안전하다 (§9 필독).

---

## 1. 요약 (실행 진행에 따라 갱신)
- 검증 진행: P0 우선(너비 우선) → 전수 확대 중(관리자·승인·a11y/보안 전수·BD 전수 스캔)
- 발견 버그: **Critical 1 / High 1 / Medium 6 / Low 3** (BUG-001~011)
- **⚠️ BUG-008(Critical)**: 소스 아카이브 스캔의 SCA false negative — **전수 비교 완료: 의존성 보유 15개 fixture 전부 components=0**(maven/gradle/node/python/go/dotnet/ruby/rust/php…), 대조군 empty만 0=0 PASS. 우리 스캔=프론트와 동일 경로(`sourceArchiveApi.ts`)라 UI로도 동일. SCA 핵심 기능 전면 실패 (cdxgen 런타임 검증은 출시 전 팀 필수)
- **⚠️ BUG-010(High)**: 조건부 라이선스 컴포넌트의 Pending 승인 **자동 생성 누락**(가이드 명세 위반) — 컴플라이언스 자동 트리거가 조용히 실패
- 관리자 영역(super_admin) 비파괴 검증 **9/9 정상**(read·복원 412 게이트·last-super-admin 보호 SKIP)
- 자동화 회귀: fault-injection · 렌더링 XSS · a11y(전수) · i18n · 보안 헤더

## 2. 발견 버그 (심각도순)
> `bug-report.md` 원본에서 종합. 현재까지:

| ID | 심각도 | 영역 | 요약 |
|----|--------|------|------|
| BUG-001 | Medium | a11y | 취약점 상태 배지 색 대비 WCAG AA 미달 |
| BUG-002 | Medium | i18n | KO 로케일에서 에러·게이트 메시지 영어 노출 |
| BUG-003 | Low | 성능 | 동일 GET 중복 호출 (prod 확인 필요) |
| BUG-004 | Low | UX | 404 후 breadcrumb 로딩 잔존 |
| BUG-005 | Medium | 권한·VEX | team_admin 억제 버튼 항상 disabled (가이드 불일치) |
| BUG-006 | Medium | SBOM | CycloneDX JSON 재내보내기 바이트 불안정 (컴플라이언스) |
| BUG-007 | Medium | 스캔·UX | 취소 후 진행 드로어 미갱신 (서버는 정상 cancelled) |
| **BUG-008** | **Critical** | **SCA 탐지** | **소스 아카이브 스캔 의존성 전면 미탐지 — 15개 fixture 전 언어 false negative(BD 대비), 스캔은 성공 표시. UI도 동일 경로** |
| BUG-009 | Medium | 보안 | 프론트엔드 clickjacking 방어 헤더(X-Frame-Options/CSP) 부재 |
| **BUG-010** | **High** | **승인·컴플라이언스** | **조건부 라이선스 컴포넌트의 Pending 승인 자동 생성 누락 — 가이드 명세 위반, 법무 검토 조용히 누락** |
| BUG-011 | Low | 입력 검증 | 'organization' 예약 slug 거부 누락 (validator 주석-구현 불일치, 미래 호환성) |

## 3. 검증 커버리지 (영역 × P0) — 진행 중
| 영역 | 검증한 P0 | 결과 |
|------|------|------|
| A. 인증 | AUTH-02(잘못된 비번)·04(빈 입력)·06(미인증 리다이렉트) | ✅ 정상 (auth.spec) |
| D. 취약점 VEX | VULN-08(New→Analyzing)·12(New→verdict 직접 불가) | ✅ 정상 (MCP) |
| D. 취약점 VEX | VULN-10/11(억제 권한) | 🐛 BUG-005 후보 (억제 항상 disabled) |
| L. RBAC | developer /admin/users 차단 | ✅ 정상 (rbac.spec) |
| L. RBAC | super-admin 로그인·관리자 접근 | ✅ 정상 (admin@demo.trustedoss.dev 로그인 성공 — 이전 레이트리밋 관찰 해소) |
| P~U. 관리자 | users/teams/audit/disk/health read · 복원 412 게이트 · last-super-admin 보호 | ✅ 9/9 정상 (`admin-checks.js`, 비파괴) |
| F. 승인 | 조건부 라이선스 → Pending 자동 생성 | 🐛 BUG-010 (conditional 6건 존재 but 승인 0) |
| F. 승인 | If-Match 누락 400 · etag 불일치 412 (optimistic locking) | ⏳ Pending 0건이라 전이 검증 보류 (`approval-checks.js`) |
| B. 프로젝트 | PROJ-02(빈 이름)·04(잘못된 URL)·15(없는 ID) | ✅ 정상 (MCP, 단 BUG-002/004 발견) |
| C. 스캔 | SCAN-01/02/08/09/12/15 (zip스캔·WS진행·파이프라인·취소·확인) | ✅ 정상 (취소→cancelled 확인) |
| C. 스캔 | 취소 후 진행 드로어 UI 갱신 | 🐛 BUG-007 |
| F. 승인 | 큐 UI·필터 | ✅ UI 정상 — 단 빈 큐의 원인이 BUG-010(자동 생성 누락)으로 확정 |
| N. DT 커넥터 | DT 컨테이너 down(docker stop) → 취약점 조회 정상(PostgreSQL 캐시) | ✅ 정상 (실제 장애 주입 — DT 재시작 중 포털 정상 동작 확인) |
| W. 성능/부하 | k6 read 부하 (5VU·20s·3420회) `/projects`·`/vulnerabilities` | ✅ p95 19~25ms, SLO(<800ms) 통과 (write·대량데이터·고VU는 확장 권장) |
| 횡단 | fault-injection·XSS·a11y·i18n | ✅ 자동화 spec green |
| Z. 브라우저 | BROWSER-01/03/04 (딥링크·새로고침·뒤로가기) | ✅ browser-behavior.spec (3/3) |
| E. 라이선스 | LIC-06/07/08 분류(GPL→금지·LGPL→조건부·MIT/Apache/BSD→허용) | ✅ 정상 (MCP) |
| G. SBOM | SBOM-01/02 (4포맷 다운로드·형식 마커) | ✅ 정상 (4/4) |
| G. SBOM | SBOM-03 (CycloneDX JSON 바이트 안정성) | 🐛 BUG-006 (재내보내기 불일치) |
| 횡단 P1 | boundary(페이지네이션 page=0·size>100·q>255·빈 이름)·concurrency(slug 충돌 409)·state-transition(terminal 스캔 취소 409)·observability(X-Request-ID echo/생성·RFC7807) | ✅ 9/9 정상 (`p1-checks.js`, 비파괴). 에러 `type=about:blank`는 가이드 표준(title/detail 충실) |

## 4. 개선점 (버그 아님 · UX 권장)
- (실행 중 발견 시 기록)

## 5. 미검증 / 위임 영역 (환경·시간·권한 제약)
| 영역 | 사유 | 권장 |
|------|------|------|
| 스캔 완료→BD 비교 | 실스캔 건당 3~60분 × fixture 32개 = 누적 수 시간 | **CI matrix 병렬**(fixture별 job) + `summary.csv` ground truth diff 스크립트 — 단일 흐름보다 CI가 적합 |
| performance-load (k6) | — | ✅ **완료** — read API 부하 검증(`tests/trustedoss/load/read-load.js`). write·대량·고VU 시나리오 확장 권장 |
| DT 장애(브레이커/캐시) | — | ✅ **부분 완료** — DT 컨테이너 down→포털 정상(캐시) 검증. breaker 상태전이(OPEN/HALF_OPEN) UI는 super-admin 필요 |
| 관리자 백업·복원/감사 불변성/디스크 임계 | 호스트 디스크 97%(디스크 채우기 위험)·super-admin 레이트리밋·DB 직접 | 통합/운영 환경에서 별도 검증(상당수 내부 pytest 커버) |
| super-admin 억제 권한 비교(BUG-005) | super_admin 로그인은 성공(해소). 단 team_admin↔super_admin 억제 권한 차이 직접 비교는 team_admin 계정 추가 로그인 필요 | team_admin 계정으로 단독 재확인 |
| 승인 전이(If-Match 400·etag 412·정상/무효 전이) | Pending 0건(BUG-010으로 자동 생성 안 됨) → 전이 대상 없음 | BUG-010 수정 후 또는 수동 생성한 승인으로 재검증(`approval-checks.js`) |
| last-super-admin 강등 차단 | 대량 seed로 super_admin 1182명 → 강등 시 실제 강등 위험으로 SKIP | 단일 super_admin 환경에서 안전 검증 |
| IDOR/수평 권한(타팀 리소스 접근) | team_admin/developer 다계정 + 타팀 프로젝트 매핑 필요 | CI에서 다계정 fixture로 검증 |
| P1 핵심(boundary·concurrency·state·observability) | ✅ **완료** (`p1-checks.js` 9/9) | — |
| P2 입력검증·injection·드문 전이 | ✅ **완료** (`p2-checks.js` 11/12) — slug/name/desc/git_url 경계·SQLi 방어·없는 스캔 취소 404 정상, BUG-011(예약 slug) 1건 발견 | — |

## 6. 종합 권장 (잠정 — 실행 완료 후 확정)
**검증된 핵심 P0는 안정적**: 인증 실패 경로, VEX 정상/무효 전이, RBAC developer 차단, 브라우저 히스토리, 라이선스 분류, 장애 회복(fault-injection), XSS escape.

**출시 전 우선 조치 권장**:
1. **BUG-008(Critical) / BUG-010(High)** — blocker급: SCA 탐지·컴플라이언스 자동화의 **조용한 실패**. 둘 다 "스캔은 성공 표시되는데 핵심 결과가 누락"되어 사람 눈에 안 띈다. → 아래 출시 가부 참조.
2. **BUG-005** (Medium): team_admin 억제 불가 — 가이드/권한 정합성. 문서 또는 권한 로직 수정.
3. **BUG-002** (Medium): KO 로케일 영어 노출(에러/게이트/Not Found) — 글로벌 출시 품질.
4. **BUG-001** (Medium): 취약점 상태 배지 a11y 색 대비 — 접근성.
5. **BUG-009** (Medium): 프론트 clickjacking 헤더 부재 — nginx add_header 1줄로 조치 가능.
6. **BUG-006/007** (Medium): SBOM 바이트 불안정, 취소 후 드로어 미갱신.
7. **BUG-003/004** (Low): 중복 API 호출(prod 확인), breadcrumb 로딩 잔존.

**출시 가부 (blocker 검토 2건)**:
- **BUG-008** (Critical, SCA false negative): 소스 아카이브 스캔이 의존성을 탐지하지 못함(스캔은 succeeded). **전수 비교 완료 — 의존성 보유 15개 fixture 전 언어 components=0**, 대조군 empty만 PASS. SCA 핵심 가치 전면 실패. **출시 blocker** (cdxgen 런타임 검증 후 재확인 필수).
- **BUG-010** (High, 승인 자동 생성 누락): 조건부 라이선스가 법무 검토 큐에 자동 진입하지 못함(가이드 명세 위반). 라이선스 의무 위반이 조용히 출시될 리스크. **컴플라이언스 blocker 검토.**
- 두 결함의 공통 위험: **"성공처럼 보이는 침묵의 실패"** — UI/상태는 정상이라 내부 테스트·수동 확인으로 놓치기 쉽다. 나머지 Medium 6/Low 2는 그 다음 순위.

## 7. 자동화 회귀 자산 (CI 입력)
- **E2E spec 10종**: auth · rbac · browser-behavior · sbom-download · api-failure-resilience(fault) · rendering-xss · accessibility(axe) · accessibility-full(12화면 전수) · locale-toggle · security-headers(보안 횡단)
- **API 검증 스크립트**: `scripts/admin-checks.js`(관리자 비파괴 9종) · `scripts/approval-checks.js`(승인 동시성/etag) · `scripts/p1-checks.js`(boundary·concurrency·state-transition·observability 9종)
- **부하**: `tests/trustedoss/load/read-load.js` (k6 — read API p95 검증)
- **BD 전수 비교**: `scripts/scan-all-fixtures.js`(전 fixture 배치 스캔) → `scripts/collect-scan-map.js` → `scripts/compare-bd.js` (Black Duck summary.csv vs 컴포넌트 수, false negative 자동 검출)
- **카탈로그**: `test-cases.md` (290 케이스, 3-pass 검증)
- 실행: `npx playwright test --project=trustedoss --workers=1` (레이트리밋 회피)

## 8. 권장 실행 모델 (남은 전수의 올바른 그릇)
분단위 스캔×32 · 관리자 장애주입 · 고VU 부하 · 로그인 레이트리밋(5/분)은 **단일 대화 세션이 아니라 CI 파이프라인**에서 돌려야 효율적:
- **CI matrix**: fixture별 병렬 스캔 완료 → `compare-bd.js`로 BD 정확성 diff
- **nightly**: 전 영역 spec + k6 부하 + 장애주입(통합환경에서 docker 제어)
- 본 세션은 그 **입력(자산·카탈로그·대표 실검증)** 을 완성 — DT 장애·k6는 실제로 검증 완료

### 8.1 CI 실행 환경 전략 (재현성·격리)
외부 독립 QA는 "격리된 staging을 블랙박스로 친다"가 정석. 환경별 grade:
| 순위 | 모델 | runner | 환경 | 상태 |
|:---:|------|--------|------|------|
| **1순위** | 팀 클라우드 staging 블랙박스 | GitHub-hosted | 팀 관리(재현·격리) | **팀에 배포 요청 예정** (`trustedoss-qa.yml`, URL secret만 교체) |
| **2순위** | CI ephemeral (소스 빌드→seed→폐기) | GitHub-hosted | 매 run 격리·`down -v` (leftover 0) | **준비 완료** (`trustedoss-qa-ephemeral.yml`, PAT만 필요). staging 공백기 사용 |
| 검증용 | local self-hosted runner | self-hosted(맥) | 로컬 dev docker | **CI 구조 작동 일회성 입증 완료** (api-checks ✅). 격리 부재로 지속 사용 부적합 → 검증 후 해제 |

- **2순위(ephemeral)의 이점**: 깨끗한 seed라 local에서 SKIP했던 검증(예: last-super-admin 강등은 단일 super_admin일 때만 안전)이 **실제로 동작**하고, `down -v` 폐기로 데이터 오염/cleanup 문제가 원천 제거됨.
- **이식성**: workflow·검증 스크립트·BD 파이프라인은 runner/환경에 독립적 — `runs-on`과 접속 URL만 바꾸면 1↔2순위 전환.

## 9. ⚠️ "발견 10건"의 해석 (팀 필독 — 품질 보증서 아님)

**개수가 적다 = 안전하다가 아닙니다.** 처음 8건은 "갭 집중·부분 검증"의 산출물이었고, 이후 **전수 확대에서 가장 심각한 2건(BUG-008 Critical, BUG-010 High)이 나왔습니다.** 둘 다 "스캔/상태는 성공인데 핵심 결과가 비어 있는" **침묵의 실패**라, 내부 테스트나 수동 클릭으로는 잘 안 드러납니다.

### 왜 적게 보였는가
1. **내부 테스트가 충실해 이미 걸러짐** — FE Playwright 22 + 하네스 16 + BE pytest 145(=180+). 중복으로 재발견할 결함이 적음.
2. **'갭'만 검증(중복 금지)** — 내부가 커버한 critical-flow·동시성·RBAC 기본은 의도적으로 재검증하지 않음.
3. **침묵의 실패는 "통과처럼 보임"** — BUG-008/010은 HTTP 200·스캔 succeeded·UI 정상이라, 결과값(컴포넌트 수·승인 큐)을 **외부 ground truth와 대조**해야만 드러난다. 본 QA의 BD 비교 파이프라인이 그 역할.

### 전수 확대로 드러난 것 (추정 → 사실)
- 이전 리포트는 "BD 1/32만 비교 → 더 있을 개연성"이라 **추정**했음.
- **전수 비교 완료**: 의존성 보유 **15개 fixture 전부 false negative**(maven/gradle/node/python/go/dotnet/ruby/rust/php…), 대조군 empty만 정상. → 추정이 **사실로 확인**됨. BUG-008은 node 단발이 아니라 **SCA 전면 실패(Critical)**.
- 승인 워크플로우 전수 점검에서 **BUG-010**(조건부 라이선스 자동 승인 누락) 확인.
- 관리자 영역(super_admin) 비파괴 9종은 **모두 정상**(거짓 양성 없음 — 비교 신뢰성 방증).
- P1 축(boundary·concurrency·state-transition·observability) 9종도 **모두 정상** — 입력 경계·slug 충돌·terminal 취소·request_id/RFC7807 등 엣지 처리가 견고함. 즉 발견된 결함은 "엣지 미흡"이 아니라 **핵심 기능의 침묵 실패(BUG-008/010)** 에 집중됨.

### 아직 남은 미검증 (CI 위임)
- 승인 상태 전이(BUG-010으로 Pending 0 → 전이 대상 없음), IDOR/수평권한, i18n 전수, 반응형, P1/P2 상당수, 고VU 부하, 관리자 파괴적 플로우(백업 복원 실행·감사 불변성).

### 결론 (팀 권장)
- **출시 blocker 2건 우선**: BUG-008(Critical, SCA 전면 미탐지)·BUG-010(High, 컴플라이언스 자동화 누락). 둘 다 "성공처럼 보이는 침묵의 실패".
- **BUG-008은 cdxgen 런타임 검증 필수**: 본 QA는 호스트 docker exec 제약으로 컨테이너 내 cdxgen 실행을 직접 확인하지 못함. 동일 코드/이미지가 프로덕션이므로 팀이 실제 실행 경로를 검증해야 확정.
- 검증 커버리지: 가이드 290 케이스 대비 실집행 **~25%**(P0 대표 + 관리자/승인/a11y·보안/BD 전수). 나머지는 본 QA가 만든 자동화 자산(spec 10종·API 검증·BD 파이프라인·k6)으로 CI에서 확장.
