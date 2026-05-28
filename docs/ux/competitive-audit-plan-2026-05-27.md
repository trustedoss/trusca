# 경쟁 UX 감사 — 계획서 (2026-05-27)

> **이 문서는 본 감사의 단일 진실(SoT)이다.** 다른 세션이 이어받을 때도 본 문서만 보고 다음 단계를 알 수 있도록, 단계별 산출물·실행 명령·결정 게이트를 모두 본문에 명시한다.
>
> 작업이 중간에 끊기면 §10 "재개 절차"를 따른다.

---

## 0. 목적 & 범위

### 0.1 목적
TrustedOSS Portal v2의 UI/UX가 글로벌 상용 SCA / 데이터-중심 SaaS와 비교해 충분한 경쟁력을 갖는지 **실제 화면 비교**로 판정. 추정 금지([[feedback_v1_reuse_table_as_silent_default]]).

### 0.2 진단 산물
- **갭 매트릭스** — 우리 8 화면 × 5 도구 × 8 평가축
- **갭 분석** — 우열·모방 가치·우선순위
- **PR 후보 scope** — 진짜 갭만(0건일 수도, 10건일 수도)

### 0.3 in-scope
- 우리 portal의 핵심 사용자 여정 8 화면 (사이드바·헤더 포함)
- 5 경쟁 도구의 공개 가능한 화면 (스크린샷·공식 데모·G2 리뷰·YouTube 데모)
- 정보 밀도·인터랙션·시각 폴리시 평가

### 0.4 out-of-scope
- 백엔드 성능·feature parity 비교 (별도 트랙)
- 브랜드·로고·마케팅 페이지
- 모바일 반응형 (현재 데스크탑 전용 SaaS 컨벤션 따름)
- 접근성(a11y) — 별도 audit이 필요한 깊이의 작업

---

## 1. 평가 대상

### 1.1 우리 portal — 8 화면 (이 순서대로 캡처)
| # | 화면 | URL | 비고 |
|---|---|---|---|
| O1 | Dashboard | `/` | 전사 리스크 포트폴리오 |
| O2 | Project List | `/projects` | 정렬·필터·release/scan count |
| O3 | Project Detail — Overview | `/projects/:id?tab=overview` | Risk gauge·차트·recent scans |
| O4 | Project Detail — Components | `/projects/:id?tab=components` | 가상스크롤·필터·sortable header |
| O5 | Project Detail — Vulnerabilities | `/projects/:id?tab=vulnerabilities` | bulk action bar 노출 상태 |
| O6 | Drawer detail (CV 또는 Vuln) | 위에서 한 행 클릭 | 슬라이드 드로어 깊이 |
| O7 | Project Detail — Reports | `/projects/:id?tab=reports` | W3 #32 산출 |
| O8 | Scans Queue | `/scans` | dense table 샘플 (admin/health는 super_admin 필요 → 교체, §11 결정 로그 참조) |

선택지: 더 추가 — Compliance(O9), Policies(O10), Notifications(O11) — Phase B 시작 전 결정. **기본 8.**

### 1.2 경쟁 도구 — 5종
| # | 도구 | 위상 | 화면 수집 경로 (1순위 → 백업) |
|---|---|---|---|
| C1 | **Black Duck SCA** (Synopsys) | 직접 경쟁자 — 우리 포지셔닝 기준 | synopsys.com/software-integrity → Black Duck product tour, G2 screenshots, YouTube official walkthroughs |
| C2 | **Snyk** | 모던·개발자 친화 표준 | snyk.io product pages, snyk docs, G2, YouTube |
| C3 | **Sonatype Lifecycle (Nexus IQ)** | 엔터프라이즈 표준 | sonatype.com, G2, 컨퍼런스 발표 |
| C4 | **Mend (전 WhiteSource)** | 대체 SCA | mend.io, G2 |
| C5 | **Datadog Vulnerability Management** | 신규·모니터링-스타일 정합 | datadoghq.com, official blog |

후보 보강(필요 시): JFrog Xray (C6), GitHub Advanced Security (C7), Aikido (C8). **기본 5.**

### 1.3 평가축 — 8개 (각 1~5 점수 + 정성 노트)
| # | 축 | 측정 항목 |
|---|---|---|
| A1 | **정보 밀도** | 1080px 높이 뷰포트에서 보이는 row 수, 헤더·여백 비율, 컴팩트 모드 유무 |
| A2 | **발견성** | global search(CMD+K) 유무, in-context tooltip 빈도, 필터 발견성 |
| A3 | **필터 UX** | active filter chip 가시화, multi-select, 저장/공유, URL 거울 |
| A4 | **드로어/세부보기** | 슬라이드 드로어 vs 페이지 nav, 깊이(1단/2단), 컨텍스트 보존 |
| A5 | **Empty / Loading** | 스켈레톤 vs 스피너, zero-state CTA 명시성, 에러 메시지 |
| A6 | **Bulk action** | 선택 모델, bulk transition, undo, per-row result |
| A7 | **차트 인터랙션** | hover tooltip, click deep-link, brush/zoom, legend toggle |
| A8 | **마이크로인터랙션** | hover transition, focus ring, sortable header animation, polish 감 |

점수 부여:
- **5** = 업계 최상위 (industry-leading)
- **4** = 충분히 경쟁력 있음
- **3** = 표준 충족 (기본기 OK)
- **2** = 명백한 갭 — PR 후보
- **1** = 부재 또는 심각 결함 — P0/P1 후보

---

## 2. 단계별 실행 계획

각 Phase는 **독립 산출물**을 가진다. 끊겨도 직전 Phase 산출물은 보존된다.

### Phase A — 우리 화면 캡처 (예상 45~60분, dual-purpose 반영으로 +15분)

**중요: 본 Phase의 산출물은 dual-purpose다 — audit (즉시) + Docusaurus 문서 (W7 / v2.4.0 release-notes 등 후속). §13 참조.**

**산출물**:
- `docs/ux/screens/ours/*.png` 8장 (EN UI, 2x DPR, viewport + full-page 각 1장)
- `docs/ux/screens/ours/ko/*.png` 핵심 3장 (KO 버전, audit-out-of-scope이지만 KO 문서 대비)
- `docs/ux/raw/ours-observations.md` (audit 메모)
- `docs/ux/raw/capture-metadata.md` (각 PNG의 캡처 컨텍스트 — 데이터셋·날짜·언어·DPR — docs 작성자가 동일 컨텍스트로 재캡처 필요할 때 참조)

**선행조건**:
- portal dev stack 기동 (`docker-compose -f docker-compose.dev.yml ps` 모두 healthy)
- frontend-admin@demo.trustedoss.dev / DemoTest2026! 로그인 가능
- scan-bench 실행으로 풍부한 데모 데이터 존재 (방금 끝났으니 OK — Juice Shop 1714 comp / 121 CVE / fx-maven-node 등)
- 데모 데이터 고정: 모든 화면이 동일 프로젝트(`fx-maven-node`, 69 comp + 11 CVE)에서 캡처 — docs 산문이 일관된 숫자를 인용할 수 있게

**실행**:
1. Playwright 스크립트 신규 `scripts/ux-audit/capture_ours.ts` 작성 — frontend-admin 로그인 후 8 화면 순차 navigate + screenshot.
   - `apps/frontend/tests/_harness/PortalPage.ts`의 기존 verb 재사용
   - viewport 1440×900 + **`deviceScaleFactor: 2`** (Retina 2x = 2880×1800 effective)
   - **UI 언어 = English** 우선 (i18n 토글로 EN 고정 후 캡처) — docs 산문의 기능명과 정확히 일치 (예: 산문 "Vulnerabilities tab" ↔ 스크린샷 "Vulnerabilities")
   - 각 화면 2장: **viewport-only**(audit용·접힌 영역만) + **full-page**(docs용·전체 스크롤 영역). Playwright `page.screenshot({ fullPage: true })` 와 default 둘 다 호출
   - PII 가리기: frontend-admin@demo.trustedoss.dev 가 데모 계정이므로 그대로 캡처 OK. 단 헤더의 사용자 이름·이메일이 표시된다면 데모 계정인 것이 명확하게 보이는지 점검
   - 동일 프로젝트 사용 (`fx-maven-node` — 69 comp + 11 CVE, 적당한 데이터)
2. KO 핵심 3장 (Dashboard·Project Detail Overview·Vulnerabilities) 별도 캡처 → `screens/ours/ko/`
3. 캡처 후 즉시 각 화면 1줄 관찰 메모를 `raw/ours-observations.md`에 기록.
4. `raw/capture-metadata.md` 에 다음을 기록:
   - 캡처 일자 (2026-05-27)
   - 데이터셋 (frontend-admin / fx-maven-node / scan id `xxx`)
   - portal 버전 (git commit SHA)
   - viewport·DPR·UI 언어
   - 재캡처 절차 (스크립트 재실행 명령 1줄)

**파일 명명 규칙 (docs 재사용 가능한 의미 이름)**:
| audit 식별자 | 파일명 | docs 의미 |
|---|---|---|
| O1 | `dashboard.png` / `dashboard-full.png` | 대시보드 |
| O2 | `project-list.png` / `project-list-full.png` | 프로젝트 목록 |
| O3 | `project-detail-overview.png` / `project-detail-overview-full.png` | 프로젝트 상세 — Overview |
| O4 | `project-detail-components.png` / `...-full.png` | Components |
| O5 | `project-detail-vulnerabilities.png` / `...-full.png` | Vulnerabilities |
| O6 | `drawer-vulnerability-detail.png` | 드로어 (CV 상세) |
| O7 | `project-detail-reports.png` / `...-full.png` | Reports |
| O8 | `scans-queue.png` / `scans-queue-full.png` | 스캔 큐 (admin/health 대체) |

audit 보고서(§Phase D)에서만 `O1`~`O8` 식별자를 사용. 파일은 의미 이름.

**완료 기준 (DoD)**:
- [ ] 8개 의미 이름 PNG (viewport-only) 저장
- [ ] 8개 `*-full.png` (full-page) 저장
- [ ] KO 핵심 3장 저장
- [ ] `raw/ours-observations.md` 화면당 3~5줄
- [ ] `raw/capture-metadata.md` 재캡처 절차 + 컨텍스트 기록
- [ ] git commit SHA + 캡처 날짜가 메타데이터에 명시
- [ ] Phase B로 진행 가능 신호

**실패 처리**:
- 로그인 실패 시 `frontend-admin` 계정 재시드 후 재시도 ([[project_demo_test_setup]] 참조)
- 스크립트 실패 시 Playwright codegen으로 selector 확인
- i18n 토글이 EN 고정 안 되면 localStorage 직접 주입으로 우회

### Phase B — 경쟁 도구 화면 수집 (예상 1.5~2시간)
**산출물**: `docs/ux/screens/competitors/<vendor>/*.png` (도구당 5~8장) + `docs/ux/raw/competitor-sources.md`

**도구별 수집 절차**:
각 도구에 대해 다음 순서로 시도하고, 찾은 모든 화면을 저장:

| 우선 | 경로 | 방법 |
|---|---|---|
| 1 | 공식 product page 스크린샷 | WebFetch로 페이지 HTML → 이미지 URL 추출 → curl 다운로드 |
| 2 | 공식 docs/user guide 스크린샷 | docs.{vendor}.com WebFetch |
| 3 | G2 / Capterra 사용자 업로드 스크린샷 | WebSearch "{vendor} screenshot dashboard 2025" |
| 4 | YouTube 공식 walkthrough 비디오 썸네일 / 캡처 | WebSearch + 비디오 ID 추출 → thumbnail |
| 5 | 공식 블로그 / 컨퍼런스 발표 슬라이드 | WebSearch site:vendor.com |

**커버해야 할 화면 분석론** (도구마다):
- 대시보드 (전체 리스크 뷰) — 우리 O1 대응
- 프로젝트/리포지토리 목록 — 우리 O2
- 프로젝트 상세 / 컴포넌트 인벤토리 — 우리 O3/O4
- 취약점 목록 + 트리아지 UI — 우리 O5/O6
- 정책/관리 — 우리 O8

**완료 기준 (DoD)**:
- [ ] 5개 도구 × 최소 4 화면 = 최소 20장 PNG
- [ ] `raw/competitor-sources.md` 에 각 이미지의 출처 URL·캡처 일자 명시 (저작권/공정사용 메타)
- [ ] 누락 도구 또는 핵심 화면 미수집 시 그 사실을 명시 (가짜 정보 금지)

**리스크 처리**:
- 공개 자료 부족한 도구 (Mend 등): 가능한 만큼만 수집, 부족분 명시
- 자료 0건이면 해당 도구 자체를 audit에서 제외하고 §11 결정 로그에 기록

### Phase C — 정성 평가 + 매트릭스 채우기 (예상 1~1.5시간)
**산출물**: `docs/ux/raw/scoring-matrix.md` (8축 × 6주체[우리 + 5도구] 점수 + 정성 노트)

**실행**:
1. 각 평가축(A1~A8)별로 6주체(우리 + 5경쟁) 점수 부여
2. 모든 점수에 **1줄 근거** 의무 ("Snyk A1=5: 1080px 뷰포트에서 22 row 표시 + density 토글, 우리는 18 row")
3. 모방 가치 있는 패턴 발견 시 `screens/reference/` 에 별도 보관 + 패턴 노트

**평가 룰**:
- 점수 5는 "업계 최상위" — 신중하게. 우리가 5 받을 가능성 낮음.
- "모름 / 자료 부족" 은 빈 칸 + 사유 (0점 부여 금지)
- 우리 점수는 우리 화면 보고, 경쟁자 점수는 그들 화면 보고 — **추정 금지**

**완료 기준 (DoD)**:
- [ ] 8 × 6 = 48 셀 중 채워진 셀 ≥ 36 (75%) — 미만이면 Phase B로 돌아가서 자료 보강
- [ ] 빈 셀에 사유 (e.g. "Mend A6 unknown — public screenshot 없음")

### Phase D — 갭 분석 + PR scope 도출 (예상 1시간)
**산출물**: `docs/ux/competitive-audit-2026-05-27.md` (최종 보고서, audit-plan과 짝)

**구조**:
```markdown
# 경쟁 UX 감사 보고서 (2026-05-27)

## 1. 한눈 요약 (Executive summary)
- 우리 평균 점수 X.X / 5 / 5도구 평균 Y.Y
- 강한 축 / 약한 축 / 명백한 갭 N건

## 2. 점수 매트릭스 (8축 × 6주체)
| 축 | 우리 | C1 BD | C2 Snyk | C3 Sonatype | C4 Mend | C5 Datadog |

## 3. 축별 상세 (A1~A8)
각 축에 대해:
- 우리 위치 정성 평가
- 경쟁자 최우수 사례 (스크린샷 reference)
- 모방 가치 있는 패턴
- 우리에게 필요한 변화 (있다면)

## 4. 화면별 우열 (O1~O8)
각 화면에 대해 1줄 우열 + 1줄 권장.

## 5. PR 후보 (W8 또는 W9 등재 후보)
| # | 우선 | 항목 | 영향 | 예상 |

## 6. 사용자 결정 필요
- 등재할 항목 / 보류할 항목
- 추가 도구 비교 필요 여부
```

**완료 기준 (DoD)**:
- [ ] 보고서 작성 완료
- [ ] PR 후보 0~N건 도출 (0건이면 "현 UX는 충분히 경쟁력 있음" 결론을 명시적으로 적기)
- [ ] 각 PR 후보에 출발 파일/심볼·예상 작업량 명시 ([[feedback_handoff_next_session_must_be_self_sufficient]])

### Phase E — 트래커 등재 + 핸드오프 (예상 20분)
**산출물**: tracker 본문 갱신 + 핸드오프 세션 doc

**실행**:
1. PR 후보가 1건 이상이면 `docs/post-ga-execution-tracker.md` 에 W9(또는 W8 확장) 신규 wave 등재
2. `docs/sessions/2026-05-27-ux-competitive-audit.md` 작성 (v2-execution-plan §7 양식)
3. 메모리 업데이트:
   - `project_v21_v23_execution_tracker.md` 본문 1줄 수정
   - 신규 메모리 `project_ux_competitive_audit.md` (이번 감사 결과 요약 + 다음 재실행 트리거)
   - MEMORY.md 인덱스 추가

**완료 기준 (DoD)**:
- [ ] tracker 갱신 (또는 "갭 0건" 결론 시 그 사실만 기록)
- [ ] 핸드오프 doc 작성
- [ ] 메모리 인덱스 반영

---

## 3. 산출물 디렉토리 구조

```
docs/ux/
├── competitive-audit-plan-2026-05-27.md     ← 이 문서
├── competitive-audit-2026-05-27.md           ← Phase D 최종 보고서
├── screens/
│   ├── ours/                                  ← Phase A
│   │   ├── O1-dashboard.png
│   │   ├── O2-project-list.png
│   │   ├── ...
│   │   └── O8-admin-health.png
│   ├── competitors/                           ← Phase B
│   │   ├── blackduck/
│   │   ├── snyk/
│   │   ├── sonatype/
│   │   ├── mend/
│   │   └── datadog/
│   └── reference/                             ← Phase C에서 모방 가치 별보관
├── raw/
│   ├── ours-observations.md                   ← Phase A
│   ├── competitor-sources.md                  ← Phase B (출처 URL 메타)
│   └── scoring-matrix.md                      ← Phase C
└── scripts/                                   ← Phase A 자동화
    └── capture_ours.ts                        ← Playwright 캡처 스크립트
```

---

## 4. 도구·자동화 분담

| 작업 | 도구 |
|---|---|
| 우리 화면 캡처 | Playwright (`apps/frontend/playwright.config.ts` 재사용) |
| 경쟁자 자료 검색 | WebSearch ("{vendor} dashboard screenshot 2024", "{vendor} site:youtube.com walkthrough") |
| 경쟁자 자료 다운로드 | WebFetch (HTML 페이지) → curl (이미지) |
| 매트릭스 평가 | 수동 + 화면 직접 보기 (image read tool) |
| 보고서 작성 | Write + Edit |

---

## 5. 시간 추정 + 체크포인트

| Phase | 추정 | 누적 | 체크포인트 |
|---|---|---|---|
| A | 30~45m | 0.75h | 우리 8 PNG 확보 |
| B | 1.5~2h | 2.75h | 경쟁자 20+ PNG 확보 |
| C | 1~1.5h | 4.25h | 매트릭스 75% 채움 |
| D | 1h | 5.25h | 보고서 + PR 후보 |
| E | 20m | 5.5h | 트래커 + 핸드오프 |

**총 5~6시간**. 1 세션에 다 못 끝나면 Phase 단위로 끊고 §10 재개.

---

## 6. Anti-Fizzle 메커니즘

W6 계획서의 backbone 차용:

1. **Phase 단위 독립 머지** — Phase A 산출물(스크린샷)은 Phase B/C/D 실패와 무관하게 보존됨
2. **DoD 명시** — 각 Phase 끝에 체크박스 의무
3. **재개 절차 명시 (§10)** — 다른 세션이 이어받아도 본 문서만 보고 다음 단계 시작 가능
4. **결정 게이트 명시 (§7)** — 자율 진행 vs 사용자 확인 명확화
5. **품질 게이트 (§8)** — 가짜 정보 차단

---

## 7. 결정 게이트 (자율 vs 사용자 확인)

### 7.1 자율 진행 (사용자 미확인)
- Phase A: 우리 8 화면 모두 캡처. 추가 화면(O9/O10/O11) 필요 시 자율 추가.
- Phase B: 5 도구 자료 수집. 도구당 5~8 화면.
- Phase C: 점수 부여 (각 셀에 근거 1줄).
- Phase D: 보고서 작성 + PR 후보 0~N건 도출.

### 7.2 사용자 확인 필요
- Phase B에서 어떤 도구의 공개 자료가 거의 없으면 → 그 도구 제외 결정 사용자에게 알림
- Phase D에서 PR 후보 5건 초과면 → 우선순위 사용자에게 확인
- Phase E에서 트래커 등재 형태 (W8 확장 vs W9 신규)는 결정 후 본문에 통지

### 7.3 멈춤 신호
- 가짜 정보로 셀 채울 유혹 → 멈추고 사용자에게 알림
- 5시간 초과 시 → 멈추고 진행 상황 보고

---

## 8. 품질 게이트 (가짜 정보 차단)

1. **모든 경쟁자 점수는 출처 스크린샷 1장 이상에 근거** — 추론·일반 인상 금지
2. **점수 5 부여는 신중** — "업계 최상위" 의미. 흔치 않게.
3. **"우리가 더 나음" 주장은 같은 화면 비교 후만** — 우리 O3 vs 그들 dashboard 처럼 다른 화면 비교 금지
4. **모름은 모름이라 표기** — 빈 셀 + 사유, 0점 부여 금지
5. **이미지 출처 URL · 캡처 일자 의무 기록** — 6개월 후 재감사 가능성

---

## 9. 알려진 한계 (선언)

1. **공정 사용** — 경쟁자 스크린샷은 공개된 자료에 한정. 모두 출처 명시. 비공개 / 유료 데모 캡처 제외.
2. **데이터 다양성** — 우리 화면은 frontend-admin 1 계정 데이터로만 캡처. real-world 시나리오(대규모 organization, 다수 팀) 미반영.
3. **시간 정점** — 2026-05-27 기준 스냅샷. 도구는 매 분기 UI 변경 — 6개월 후 재실행 권장.
4. **bias** — 평가자는 우리 portal 개발자. 외부 디자이너 리뷰 별도 권장 (옵션 C).
5. **a11y / 다국어 / 모바일** — out of scope (별도 audit 필요).

---

## 10. 재개 절차 (세션 끊김 대응)

다른 세션이 본 작업을 이어받을 때:

1. **첫 번째**: 본 문서 (`docs/ux/competitive-audit-plan-2026-05-27.md`) 끝까지 읽는다.
2. **두 번째**: `docs/ux/` 디렉토리에 어떤 산출물이 있는지 확인 → 마지막으로 완료된 Phase 식별.
3. **세 번째**: 해당 Phase의 DoD 체크리스트 점검 → 미완 항목 채우거나 다음 Phase 시작.
4. **§11 결정 로그** 확인 → 사용자가 내린 결정 반영.

산출물별 진행도 매핑:
- `screens/ours/*.png` 8장 있음 → Phase A 완료
- `screens/competitors/*/...` 도구당 4장+ 있음 → Phase B 완료
- `raw/scoring-matrix.md` 36+ 셀 채워짐 → Phase C 완료
- `competitive-audit-2026-05-27.md` 존재 → Phase D 완료
- `docs/sessions/2026-05-27-ux-competitive-audit.md` 존재 → Phase E 완료 = 전체 완료

---

## 13. Dual-purpose: 캡처 자산의 문서 재사용

본 audit의 우리 화면 캡처(`docs/ux/screens/ours/`)는 **단일 진실(SoT)** 로 운용한다.
동일 PNG가 두 곳에서 소비된다:

### 13.1 즉시 소비 (audit)
- Phase C 매트릭스 셀의 근거 첨부
- Phase D 보고서의 화면별 우열 비교

### 13.2 후속 소비 (Docusaurus 문서)
| 후속 작업 | 사용할 화면 |
|---|---|
| **W6-#43c 사용자/관리자 문서 교체** (DT 제거) | admin/health (Trivy DB 패널 추가 후 재캡처 필요) · user/scans · user/vulnerabilities |
| **v2.4.0 release-notes** | 새 IA(8 탭) Dashboard + Compliance + Reports · 차트 deep-link 데모 |
| **W7-PR-A Triage 통합 가이드** | drawer-vulnerability-detail · bulk action bar |
| **W7-PR-B Analysis Types** | project-detail-overview (Recent scans 영역) |
| **landing page / comparison** | dashboard · project-detail-overview (가장 임팩트 큰 2장) |
| **튜토리얼 / onboarding** | project-list · project-detail (각 탭) |

### 13.3 재사용 절차

**옵션 A (권장) — Docusaurus가 직접 참조**:
```markdown
<!-- docs-site/docs/user-guide/vulnerabilities.md -->
![Vulnerabilities tab](../../../docs/ux/screens/ours/project-detail-vulnerabilities.png)
```
또는 Docusaurus static asset에 symlink:
```bash
ln -s ../../docs/ux/screens/ours docs-site/static/img/screenshots
```
한 군데서만 수정 → 모든 참조 갱신.

**옵션 B — 가공본 별도 보관**:
docs용으로 annotation·crop이 필요하면 source 파일을 복사한 뒤 `docs-site/static/img/annotated/`에 별도 보관. source는 그대로 유지.

### 13.4 재캡처 트리거
다음 변경 시 본 audit의 capture 스크립트(`scripts/ux-audit/capture_ours.ts`) 재실행:
- 메이저 UI 변경 (W4 같은 대규모 IA / UX 정리)
- 새 탭 추가 / 제거
- W6-#43e Trivy DB 패널 신설 → admin/health 재캡처
- 분기마다 1회 (스크린샷 stale 방지)

재캡처는 1 명령:
```bash
cd apps/frontend && npx playwright test scripts/ux-audit/capture_ours.ts
```

`raw/capture-metadata.md` 의 git SHA를 갱신해서 "어느 버전에서 찍었는지" 추적 가능하게 유지.

### 13.5 docs용으로 부족할 수 있는 영역 (미리 인지)
- **온보딩 / 빈 상태** — 본 audit은 데이터-풀 시나리오 위주. 빈 프로젝트 / 첫 사용자 캡처는 별도 셋업 필요 (docs 작성 시 추가)
- **에러 / 경고 모달** — 동일
- **차트 hover 툴팁** — 정적 PNG로는 표현 어려움. docs에서 필요하면 GIF / video 별도 제작
- **모바일 / 좁은 viewport** — out of scope

이런 영역은 docs 작성 시 별도 캡처 PR로 보강. 본 audit은 "기본 자산" 제공이 임무.

---

## 14. 버그 인테이크 루프 (감사 중 발견 처리)

UX 감사는 본질적으로 "전체 화면을 평소보다 천천히, 의도적으로 본다" — 그래서 평소 흘려보내던 visual / 동작 / 데이터 / i18n / console 버그가 자주 드러난다. 본 트랙의 흐름을 끊지 않으면서 버그를 잃지 않기 위한 운영 규칙.

### 14.1 버그 분류 (3단계)

| 등급 | 정의 | 대응 |
|---|---|---|
| **B0 — Critical** | 데이터 손실·보안 노출·다른 사용자 데이터 노출·인증 우회·즉시 사용자 알림이 필요한 종류 | **감사 즉시 중단** → 사용자에게 즉시 보고 → fix 우선 → 감사 재개 |
| **B1 — Blocking** | 화면이 전혀 안 뜸 / 핵심 동작 작동 안 함 / 캡처 자체가 불가능 | 감사 진행하되 해당 화면은 "캡처 불가 + 사유" 로 기록 → 별도 PR 후보 등재 → 감사 종료 시 일괄 보고 |
| **B2 — Non-blocking** | 시각 / 폴리시 / i18n / console warning · 작동은 하지만 부자연스러움 | `raw/bugs-found.md` 에 1줄 등재 → 감사 계속 → Phase E에서 트래커 등재 후보로 일괄 검토 |

판단 어려우면 B1로 분류 (보수적). 모르겠으면 사용자 confirm.

### 14.2 버그 기록 양식 (`docs/ux/raw/bugs-found.md`)

각 버그마다:

```markdown
## B[0/1/2] — <짧은 제목> (yyyy-mm-dd HH:MM)
- **화면**: <O1~O8 또는 경로>
- **재현**: <1~3 step>
- **기대**: <…>
- **실제**: <…>
- **증거**: `screens/ours/bug-<slug>.png` (있으면)
- **추정 원인 영역**: <컴포넌트 / 파일 경로 — 알면>
- **분류 사유**: <B0/1/2 선택 근거>
```

### 14.3 흐름 차단 vs 비차단 결정 매트릭스

```
B0 발견 → 즉시 중단. 사용자에게:
  "감사 도중 Critical 발견: <요약>. 감사 일시 중단 권장. 진행 의견?"
B1 발견 → 본 Phase 마치고 사용자에게 통지 (다음 Phase 시작 전).
B2 발견 → 누적. Phase E에서 한꺼번에 보고.
```

### 14.4 Phase E 통합 보고

감사 보고서(`competitive-audit-2026-05-27.md`)의 §5 "PR 후보" 절 옆에 신규 §6 **"감사 중 발견된 버그"** 추가:
- B0 (있다면, 이미 처리됐어야)
- B1 (감사 종료 시 등재해야)
- B2 (배치 등재 — W9 또는 W8 확장 후보)

각 버그가 트래커 항목으로 등재되면 본문에서 그 라인 → ✅ 마크.

### 14.5 이미 발견된 사례 (이전 트랙)

본 audit 시작 전 시점의 알려진 버그 (감사 중 재확인되면 ✅ 마크):
- W8-#46 Maven classifier UniqueViolation (P0, GA blocker)
- W8-#47 zip-bomb UX가 OSS 첫 업로드 차단 (P1)

본 audit이 새로 발견하는 버그는 W9 또는 W8 확장으로 신규 등재.

---

## 11. 결정 로그 (작업 진행 중 갱신)

| 일자 | 결정 | 사유 |
|---|---|---|
| 2026-05-27 | 우리 화면 8개 기본 (O9/O10/O11 옵션) | 본 SoT 수립 |
| 2026-05-27 | 경쟁 도구 5개 — BD, Snyk, Sonatype, Mend, Datadog | 사용자 사전 동의 (CLAUDE.md 포지셔닝 기반) |
| 2026-05-27 | 평가축 8개 (A1~A8) | 위 본문 |
| 2026-05-27 | 1440×900 viewport | 표준 SaaS dashboard 사이즈 |
| 2026-05-27 | 데모 데이터셋 = `fx-maven-node` (69 comp / 11 CVE) | scan-bench 직후 풍부한 데이터 |
| 2026-05-27 | **캡처 = Dual-purpose (audit + docs SoT)** | 사용자 질의 반영. §13. 2x DPR · EN UI 우선 · 의미 이름 · viewport + full-page 양쪽 |
| 2026-05-27 | KO 핵심 3장 별도 캡처 | KO 문서 대비. audit out-of-scope |
| 2026-05-27 | **O8 변경**: `/admin/health` → `/scans` | super_admin 비번 리셋은 권한 분류기 차단(사용자 인증 = frontend-admin만). `/scans` 가 team_admin 접근 가능한 가장 유사한 dense table. admin/health 캡처는 super_admin 권한 별도 승인 후 후속. |
| — | (Phase B 중 결정) | — |

작업 중 새 결정은 본 표에 추가.

---

## 12. 사용자 사전 결정 필요

본 계획서 승인 전 확인하고 싶은 것이 있으면 알려주세요. 그렇지 않으면:

1. 본 계획서 → Phase A 자동 진행
2. Phase 끝마다 1~2줄 진행 보고
3. 결정 게이트(§7.2)에 해당하는 사항만 사용자 confirm

승인 신호 받으면 즉시 Phase A 착수합니다.
