# 문서 e2e 검증 환경 (docs-uat) — 설계·타당성 보고서

> 작성: 2026-05-29 · 갱신: 2026-05-29 (인터뷰 후속 결정 + 코드 조사 반영) · 세션: docs-verify e2e plan
> 선행 지시문: `docs/sessions/2026-05-29-docs-verification-ci-investigation.md`
> 사용자 인터뷰 결정 9건(§0) + 검증된 코드 조사(부록 A·B·C) 반영.
> 본 문서는 **계획**이다. 구축 코드(`docs-uat.yml`, extractor, 하네스 verb)는 다음 세션이 작성한다.

---

## 0. 인터뷰 결정 (이 계획의 전제)

| # | 질문/주제 | **결정** |
|---|---|---|
| 1 | 이번 세션 산출물 범위 | **계획 문서만** (구축은 다음 세션) |
| 2 | 문서 ↔ 테스트 single source of truth | **문서가 곧 테스트 (HTML annotation 추출)** — 본문이 단일 진실 |
| 3 | 커버리지 깊이 | **전수 전사(manifest) + tier 샘플 실행 + 회전** |
| 4 | CI 실행 형태 | **신규 `docs-uat.yml` 별도 lane** (`install-uat`/Playwright 재사용) |
| **D1** | **명칭** | **`docs-uat`(문서 인수 테스트).** 기능 검증 e2e와 구분 — 도구는 같이 쓰되 검증 대상은 *문서* |
| **D2** | **차단 정책** | **PR/merge를 막는 job은 없음.** PR엔 가벼운 lint advisory만, 실제 실행은 야간/주간 |
| **D3** | **실패 알림** | **`sca-self.yml` 자동 이슈 패턴 복사** (라벨 `docs-uat-drift` open/edit/close) |
| **D4** | **우선순위** | **3기둥 = Install · 사용자 가이드 · 관리자 가이드.** Quickstart=Install 경량경로, CI 게이트 다음 |
| **D5** | **하네스** | **기존 verb 재사용**(재구현 금지) — UI 단언은 기존 verb 호출 + 문서 고유 사실만 얹음 |
| **D6** | **Phase A 시작** | **Quickstart** (가장 싼 파이프라인 증명, Install로 이어짐) |

> 정체성: **본문이 진실 → 추출 → 전사 강제 → tier 샘플 실행 → 비차단 야간/주간 → 깨지면 자동 이슈.**

---

## 1. Executive summary

선행 지시문의 3축(A 가독성 · B broken UI · **C procedural correctness**) 중, 사용자는 **축 C의 실제 e2e 구현**에 초점을 맞췄다. 축 A·B는 인접 lane으로 §8에서만 다룬다.

**우선순위(D4) — 가치 기준 3기둥:**

| 순위 | 영역 | 왜 | 1차 tier |
|---|---|---|---|
| ⭐ **최고** | **Install** (wizard·backup/restore·readiness·V1/V2·단일롤/L1) | 설치 실패 = 사용자 0. 모든 것의 입구. 이미 `install-uat.yml`이 절반 구현 | nightly |
| ⭐ **최고** | **사용자 가이드 + 관리자 가이드** | 매일 따라 하는 문서. 가이드의 약속이 실제와 맞나 — 기능 e2e가 못 잡는 영역 | nightly |
| ⭐ 높음 | **Quickstart** | Install 경량경로 + 첫인상. Install 작업에 묻어감 | nightly |
| ○ 다음 | **CI 빌드 차단(에러코드)** | 제품 핵심, 아무도 검증 안 함 | weekly |
| △ 수동 | OS 전부 / 진짜 Jenkins / behavioral 재스캔 / OAuth·SMTP·GCP | 외부 의존·비용 대비 | manual |

**결론**: 전분야 docs-uat은 **구축 가능**. 전수 *실행*은 비현실적(코드블록 ~196개·외부 의존 多)이므로 **전수 전사(manifest) + tier 샘플 + 회전**으로 "N주에 실행가능 블록 전수 ≥1회"를 보장. 본문이 진실이라 drift는 PR의 가벼운 lint(비차단)가 알린다.

---

## 2. 자산 인벤토리 (재사용 대상)

- **`install-uat.yml`** — `install.sh --no-prompt → /health 폴링 → login+projects smoke → backup/restore round-trip`. scenario-driven 완성형. **이 시나리오를 manifest로 흡수**(Phase B)해 본문과 동기화. compose V1 pin·dev swap·health 루프·log dump·teardown 컨벤션 차용.
- **Playwright 하네스** — `PortalPage.ts`(75KB) + 도메인 하네스 12종(auth/seed/Admin*/Approvals/Notifications/integrations). **가이드 UI 단언의 ~90%가 기존 verb로 매핑**(부록 B).
- **기존 21개 e2e 스펙** — 거의 모든 가이드 흐름을 이미 운전(부록 B 도메인→스펙 맵). D5 재사용 근거.
- **견본 데이터** — `seed_demo.py`(결정적·멱등) + golden baselines 15 + scan-bench 32. "정답을 아는" 입력(부록 A).
- **`sca-self.yml`** — 자동 이슈 open/edit/close 패턴(§5.5 알림에 복사).

본문 procedural 규모: 코드블록 ~196개. fence 언어 `bash 120 · json 21 · yaml 16 · python 9 · sh 8 · groovy 7 · sql 3 · http 2`. 진입점 `## Verify it worked` **19페이지**.

---

## 3. authoring 모델 — annotation 스키마 (결정 2)

본문 마크다운에 HTML 주석으로 테스트 메타를 부착, extractor가 추출해 manifest를 만든다. 주석은 렌더에 안 보임(독자 경험 무손상).

**(a) 블록 단위** (fenced 코드블록 앞):
```markdown
<!-- docs-uat: id=qs-up kind=shell ctx=host expect=exit:0 tier=nightly prelude=clone -->
​```bash
docker-compose -f docker-compose.dev.yml up -d
​```
```
**(b) 산문 스텝** (`## Verify it worked` 항목 앞):
```markdown
<!-- docs-uat: id=scan-succeeded kind=ui harness=expectScanCompleted tier=nightly -->
1. The project status switches to **Succeeded**.
```

필드: `id`(필수·유일) · `kind`(shell/api/ui/sql/lint/manual) · `ctx`(host/backend/worker/postgres) · `expect`(exit:N/status:N/match:/re//rows:>N/jsonpath:…) · `harness`(ui verb+args) · `fixture` · `after`(선행 id) · `tier` · `waiver`(사유).

**extractor**(`tools/docs-uat/extract.ts`, 다음 세션): 본문 순회 → `docs-uat:` 파싱 → `docs-uat/manifest.json` 전사 → **커버리지 lint**(모든 bash/sh/http/sql fence·모든 Verify 스텝이 annotated 또는 waiver, 아니면 실패) → KO 미러 구조 패리티.

---

## 4. 전수 전사 + tier 샘플 실행 (결정 3 · D2)

manifest는 **전수**(모든 스텝 전사), 실행은 **tier 샘플**. **PR을 막는 job은 없다.**

| tier | 트리거 | 차단? | 대상 |
|---|---|---|---|
| **pr-lint** | 문서 바꾼 PR | ❌ **advisory 코멘트만** (스택 안 띄움, 몇 초) | 추출+커버리지/KO 패리티/비실행 정합 lint |
| **nightly** | cron 야간 | ❌ | **Install · 가이드 · Quickstart** 실행 (dev compose + Playwright) |
| **weekly** | cron 주간 | ❌ | Helm/kind · published-image · air-gap Trivy · CI 빌드차단 · **회전 샘플** |
| **manual** | 미실행 | — | OAuth/SMTP/GCP/실 Git host · github-app(UI 미구현) · oncall-runbook. 전사+패리티만 |

> 메모리 `feedback_ci_hardening_deferred_prerelease`와 무충돌: 신규 *별도* lane이며 PR을 막지 않음. 기존 `e2e-nightly`(야간)·`install-uat`(주간) 운영과 동일 결.

**회전(rotation)**: weekly에서 nightly에 안 든 전사 블록을 **week-of-year 결정적 분할**로 매주 일부 실행 → N주에 모든 실행가능 블록 ≥1회. `docs-uat/coverage-ledger.json`에 "id별 마지막 실행 주차" 기록(감사 가능). 매 run은 **건너뛴 스텝과 사유 로그**(침묵 truncation 금지).

**비실행 블록 정합 lint**: json/yaml/groovy/hcl는 실행 대신 유효성 + **본문 인용 스니펫 ↔ 실파일 대조**(`templates/gitlab-ci.yml`·`charts/.../values.yaml` 등 노후화 차단).

---

## 5. CI 실행 형태 — `docs-uat.yml` 매트릭스 (결정 4)

별도 lane. `install-uat.yml` 컨벤션 차용.

| job | 트리거 | 차단? | 스택 | 역할 |
|---|---|---|---|---|
| `pr-lint` | PR | ❌ advisory | 없음(정적) | extractor + 커버리지/패리티/정합 lint |
| `docs-uat-nightly` | 야간 | ❌ | dev compose + Playwright | 영역 매트릭스 `[install, user-guide, admin-guide, quickstart]` |
| `docs-uat-weekly` | 주간 | ❌ | + kind | helm-on-kind · published-image · air-gap · CI 게이트 · 회전 |

## 5.5 운영 · 알림 (D3)

**알림 = `sca-self.yml` 자동 이슈 패턴 복사** (`.github/workflows/sca-self.yml`):
- `permissions: { contents: read, issues: write }` · concurrency single-flight.
- 야간/주간 실패 → 라벨 **`docs-uat-drift`** 이슈를 `gh issue list/create/edit`로 열거나 갱신(어느 페이지·스텝이 깨졌는지 본문).
- 다음 실행에서 복구 → `gh issue close`로 자동 닫기. 같은 라벨 하나라 **매일 새 이슈 안 쌓임**.
- (선택) `SLACK_WEBHOOK_URL` 즉시 알림 — 시크릿 필요, 자동 이슈만으로 충분.

**트리아지 규칙** — 야간 실패 시 **기능 e2e 교차 확인**으로 원인 판정:

| 기능 e2e | docs-uat | 원인 | 고칠 곳 |
|---|---|---|---|
| 🔴 | 🔴 | 코드 회귀 (b) | **코드** (기능 e2e가 먼저 잡음) |
| 🟢 | 🔴 | 기능은 되는데 **문서만 낡음** (a) | **문서** (대부분 — 마크다운 1줄) |
| 🟢 | 🔴(반복) | 검사/표시 오류 (c) | **annotation**(expect/verb) |

PR을 막지 않으니 **급한 불이 아니라 다음 날 일반 후속**. 솔로/소규모라 이슈/알림으로 트리아지.

---

## 6. 권고 stack — 최소·균형·최대

| 옵션 | 포함 | cost | coverage |
|---|---|---|---|
| **최소** | extractor + `pr-lint` + Quickstart nightly | 낮음 | 첫인상 + drift 알림 |
| **균형 (권장)** | 최소 + Install 흡수 + 가이드 nightly + 비실행 정합 | 중간 | 3기둥 전사 + 핵심 실행 |
| **최대** | 균형 + weekly(helm/published/air-gap/CI게이트/회전) + manual-checklist 자동생성 | 높음 | 전수 회전 |

Phase로 최소→균형→최대 점증.

---

## 7. 단계 전략 (Phase A → D) — D4 우선순위 반영

| Phase | 머지 단위 | 핵심 |
|---|---|---|
| **A** | annotation spec + extractor + `pr-lint` + Quickstart nightly + **Quickstart annotate** | 파이프라인 1개 수직 슬라이스 증명(가장 쌈). `expectVisibleProjectCount(5)` 등 기존 verb만 |
| **B** | **Install 흡수** | `install-uat` 시나리오를 manifest로. matrix `[V1,V2]`×`[단일롤,L1]`. `/admin/health` green(AdminHealthHarness 기존 verb) + backup/restore round-trip(기존) |
| **C** | **가이드** (최우선 가치) | (1) admin-guide **api+sql 먼저**(UI verb 불필요, audit_logs 단언) → (2) 가이드 UI(seed_demo 정답에 단언, **신규 verb 4~5개**: 대시보드 타일·nav highlight·라이선스 100%·금지 강조) |
| **D** | CI 빌드차단(에러코드, golden/scan-bench fixture) + helm-on-kind + air-gap Trivy + 회전 + manual-checklist 자동생성 | weekly. 전수 커버리지 종결 |

각 Phase 머지 가능 상태로 종료. Phase A 상세 = 로컬 브리프 `docs/sessions/2026-05-30-docs-verify-phase-A-impl.md`.

---

## 8. 인접 lane — 축 A(가독성)·B(broken UI) (defer)

사용자 초점은 축 C이므로 기본 defer. 착수 시: **B** = Docusaurus `onBrokenLinks/Anchors: "throw"` + `lychee`(외부, weekly) + 빌드후 `<img>/<video>` HEAD + KO locale prefix 검증 → `pr-lint`에 흡수. **A** = `Vale`(EN) + 자체 metric(문장>35단어·EN/KO 비율) → 별도 `docs-lint.yml`.

---

## 9. 리스크 & 남은 사용자 결정

### 리스크
- **annotation churn**: 본문 수정 시 주석 동기화 → 가벼운 lint가 강제(결정 2 수용 trade-off, 비차단이라 마찰 최소).
- **UI 단언 취약성**: 하네스 verb ↔ UI 동기화 → 기존 nightly e2e 안정화 자산에 편승.
- **외부/manual**: OAuth/SMTP/GCP·github-app·oncall → manual로 솔직히 전사+로그.
- **시간 예산**: cdxgen/Trivy 실스캔 수 분 → nightly/weekly로(Trivy DB 캐시). Quickstart는 콜드부트~30s+seed~10s.
- **견본 정답의 brittleness**: seed_demo 수치(10 CVE 등)에 정확히 단언하면 seed 변경 시 깨짐 → **정확값 + 의도된 동기화 게이트**로 수용(seed 바뀌면 문서/테스트 같이 갱신).

### 남은 결정 (다음 세션 착수 전)

| # | 질문 | 권장 |
|---|---|---|
| 1 | KO 미러 검증 깊이 | 구조 패리티만(권장) vs 명령 텍스트 동등성 |
| 2 | CI dry-run 엔진 | `gitlab-ci-local`+`act` 둘 다 vs 하나 vs manual |
| 3 | Helm e2e | weekly kind(권장) vs manual checklist만 |
| 4 | 축 A/B 인접 lane | 지금 defer(권장) vs 병행 |
| 5 | manual-checklist | 자동생성이 hand `uat-checklist.md` 대체(권장) vs 병존 |

> 이미 확정(재논의 불필요): 명칭 docs-uat, 비차단, 자동 이슈 알림, 3기둥 우선순위, Phase A=Quickstart, 하네스 재사용.

---

## 부록 A — 정답을 아는 견본 데이터

docs-uat 단언은 **결과를 미리 아는 고정 입력**에만 건다. 검증된 자산:

### A.1 `seed_demo.py` (결정적·멱등, `demo-org` 존재 시 short-circuit) — `apps/backend/scripts/seed_demo.py`
- **1 org / 3 team(frontend·backend·security) / 5 user / 5 project.** 계정 비번 `DemoTest2026!` (APP_ENV ∈ dev/demo).
- 계정: `admin@`(super) · `{frontend,backend,security}-admin@`(team_admin) · `dev@`(developer) `@demo.trustedoss.dev`.
- **`portal-web`·`portal-mobile`** (둘 다 **frontend** 팀)가 풍부:
  - **CVE 정확히 10개** = `CVE-2024-99001..99010`, **2 critical / 3 high / 3 medium / 2 low**.
  - **라이선스 5종**: MIT·Apache-2.0·BSD-3-Clause(**allowed**) / LGPL-2.1-only(**conditional**) / GPL-3.0-only(**forbidden**).
  - 의무 7건.
- `portal-api`(backend) = 알림 3건. `scan-pipeline`·`vuln-feed` = CVE/라이선스 없음.
- ⚠️ **교정**: 기존 메모리/문서의 "frontend-admin 프로젝트가 풍부"는 **부정확** — 실제 풍부한 건 **`portal-web`/`portal-mobile`** 프로젝트(frontend 팀 소속)다.

→ 가이드 UI 단언("프로젝트 5개", "critical 2개", "GPL-3.0 forbidden 강조", "분류 바 100%")을 **정확값**으로 검증 가능. 스캔 불필요(즉시 사용).

### A.2 스캔 결과 정답
- **golden baselines** `apps/backend/tests/e2e/golden/baselines/*.json` — 15 fixture, 컴포넌트/라이선스/gate verdict 정확(커밋됨). 예: `node`=1 comp/3 critical/gate fail, `maven`=8 comp/4 forbidden, `multi-component`=69 comp/11 CVE.
- **scan-bench** `scripts/scan-bench/sources/fx-*.zip` + `out/*.csv` — 32 fixture 정확 카운트.
- **실세계(Juice Shop/WebGoat)** — 비결정적(zip-bomb/dup-key 실패) → **사용 금지**. (v1 webapp 542 comp는 참고만)

---

## 부록 B — 하네스 verb 재사용 맵 + 실제 갭 (D5)

가이드 UI 단언의 **~90%가 기존 verb로 매핑**. 진짜 신규는 4~5개.

**존재 (재사용)**: `expectVisibleProjectCount`·`getTotalComponentCount`·`getVulnerabilityRowCount`·`openVulnerabilityDrawer`·`selectTab`/`selectVulnerabilitiesTab`/`selectLicensesTab`·`getLicenseRowCount`·`assertRiskScore` (PortalPage) / `getComponentStatus`·`getComponentNames`(AdminHealth=`/admin/health` green) / `expectRowVisible`·`filterByTargetTable`(AdminAudit=audit row) / AdminBackup·AdminUsers·AdminTeams·Notifications·integrations 전체 / `loginViaRefreshCookie`(rate-limit 우회).

**신규 필요 (Phase C)**: ① 대시보드 severity 타일 N값 ② nav highlight/avatar 이니셜 ③ 라이선스 분포 합=100% ④ 금지 라이선스 행 강조+CTA ⑤ (확인 필요) vuln triage 상태변경 verb.

**대시보드는 기존 e2e 스펙 자체가 없음**(v2 drop) → docs-uat이 유일한 커버.

**도메인 → 기존 e2e 스펙 재사용 맵**(21 스펙): components→`project_detail` · vulnerabilities→`vulnerabilities`/`_epss`/`vex` · licenses→`licenses` · obligations→`obligations` · scans→`scan_flow`/`scan_detail_page` · admin users/teams→`admin_users_teams` · admin scans/disk/audit/health→`admin_dt_scans_disk_audit_health` · backup→`admin_backup` · integrations→`integrations` · notifications→`notifications` · auth/profile→`auth`/`auth_and_profile`. → docs-uat은 흐름 재구현 없이 **verb 호출 + 문서 고유 사실**만.

---

## 부록 C — 가이드 페이지별 검증 실현성

### C.1 procedural 밀도 (kind 분포)
- **고밀도(shell+curl+sql/ui+verify)**: admin `api-keys`·`audit-log`, user `scans`·`vulnerabilities`.
- **중밀도(shell/docker + ui verify)**: admin `backup-and-restore`·`disk-and-health`·`users-and-teams`·`vulnerability-data`.
- **UI 전용**: user `approvals`·`components-and-licenses`·`dashboard`·`notifications`·`projects`·`sbom`(+ shell validator).
- **검증 제외**: admin `github-app`(UI 미구현, 로드맵) · `oncall-runbook`(진단 전용, happy-path verify 없음) → manual 전사만.

### C.2 audit_logs SQL 실현 — `apps/backend/models/auth.py` (AuditLog)
컬럼: `target_table`·`action`·`actor_user_id`·`team_id`·`request_id`·`ip`·`user_agent`·`diff`(JSONB, **previous_status/new_status/justification** 포함). append-only 트리거 2개(읽기 무영향).
CI 쿼리:
```bash
docker-compose -f docker-compose.dev.yml exec postgres \
  psql -U trustedoss -d trustedoss -c \
  "SELECT diff FROM audit_logs WHERE target_table='vulnerability_findings' AND action='update' ORDER BY created_at DESC LIMIT 1;"
```

### C.3 비대화식 인증 (api kind)
- **JWT**: `POST /v1/auth/login`(install-uat 기사용) → `access_token`(30분).
- **API 키**: bootstrap(`create_super_admin`) → login → `POST /v1/api-keys` → `raw_key`(1회 반환). 3-curl. (`services/api_key_service.py`, `api/v1/api_keys.py`)

---

## 10. 종료 조건 (선행 지시문 §10)
- [x] 설계·타당성 보고서(본 문서) — 인터뷰 9결정 + 코드 조사 반영.
- [x] `docs/sessions/2026-05-30-docs-verify-phase-A-impl.md` — Phase A 자립 시작 지시문(로컬·gitignore).
- [x] 공개 문서 단일 PR 머지.
- [ ] 다음 세션이 cold start로 브리프를 첫 메시지로 사용.
