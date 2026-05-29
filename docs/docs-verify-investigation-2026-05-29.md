# 문서 e2e 검증 환경 — 설계·타당성 보고서

> 작성: 2026-05-29 · 세션: docs-verify e2e plan
> 선행 지시문: `docs/sessions/2026-05-29-docs-verification-ci-investigation.md`
> 사용자 인터뷰 결정 4건 반영 (§0).
> 본 문서는 **계획**이다. 구축 코드(`docs-uat.yml`, extractor, 하네스)는 다음 세션이 작성한다.

---

## 0. 인터뷰 결정 (이 계획의 4대 전제)

본 세션 시작 시 사용자와의 인터뷰에서 확정한 4건. 이 계획서 전체는 아래 결정 위에 세워진다.

| # | 질문 | **결정** | 영향 |
|---|---|---|---|
| 1 | 이번 세션 산출물 범위 | **계획 문서만** | 구축은 다음 세션. 본 세션은 §6 두 문서 머지로 종결. |
| 2 | 문서 ↔ 테스트 single source of truth | **문서가 곧 테스트 (annotation 추출)** | 본문에 `<!-- docs-uat: ... -->` 메타를 부착, CI가 추출·실행. 본문이 단일 진실. §3. |
| 3 | '전분야 e2e' 커버리지 깊이 | **전수 전사 + 샘플링 실행** | 모든 코드블록/Verify 스텝을 manifest로 *전사*(누락=lint 실패), 실행은 tier 샘플링. §4. |
| 4 | CI 실행 환경 형태 | **신규 `docs-uat.yml` 매트릭스 (별도 lane)** | `install-uat` 컨테이너 패턴 + Playwright 하네스를 재사용하되 독립 lane. §5. |

> 결정 2·3의 조합이 본 설계의 정체성이다: **본문이 진실 → 추출 → 전사 강제 → 샘플 실행**.
> 결정 1 때문에 본 문서는 "무엇을·왜·어떻게"까지만 적고, 실제 `.yml`/스크립트는 §7 Phase A 지시문이 다음 세션으로 넘긴다.

---

## 1. Executive summary

선행 지시문의 3축(A 가독성 · B broken UI · C procedural correctness) 중, 사용자는 **축 C(절차적 정합성)의 실제 e2e 구현**에 초점을 맞췄다("문서대로 따라했을 때 그대로 동작하는가"를 설치/Quickstart/사용자/관리자/CI 전분야에서). 축 A·B는 인접 lane으로 분리해 §8에서 짧게만 다룬다.

| 영역 | 본문 procedural 밀도 | e2e 자동화 가능성 | 1차 실행 tier |
|---|---|---|---|
| Quickstart | 코드블록 6 (compose up / seed / down) | **완전 가능** — dev compose 그대로 | **gate** (PR 블로킹) |
| Installation | 코드블록 ~52 (install.sh / alembic / backup) | **대부분 가능** — `install-uat.yml`이 이미 검증 중 | nightly |
| Admin guide | 코드블록 ~49 (curl API + SQL audit + 백업) | **가능** — api/sql 단언으로 기계 검증 | nightly |
| User guide | "Verify it worked" 19개 + UI 절차 | **가능** — Playwright 하네스 verb 매핑 | nightly (UI) |
| CI integration | github-actions/gitlab-ci/jenkins/webhooks | **부분 가능** — YAML lint + dry-run(act/gitlab-ci-local) + build-gate exit code | weekly |
| Helm | `helm install` 2회 | **부분 가능** — kind 클러스터 필요, 느림 | weekly |
| OAuth/SMTP/Slack/GCP | 외부 크레덴셜 의존 | **불가(자동)** — manual tier로 전사만 | manual |

**핵심 결론**: 전분야 e2e는 **구축 가능**하다. 단 전수 *실행*은 비현실적(코드블록 ~196개·외부 의존 다수)이므로, 결정 3대로 **전수 전사(manifest) + tier 샘플 실행 + 회전(rotation)** 으로 "N주에 걸쳐 실행 가능한 모든 블록이 최소 1회 실행"을 보장한다. 본문이 진실이므로 drift는 lint가 PR에서 차단한다.

---

## 2. 자산 인벤토리 (이 계획이 재사용하는 것)

### 2.1 procedural 모범 사례 — `install-uat.yml`
`scripts/install.sh --no-prompt → /health 폴링 → login+projects API smoke → backup.sh → restore.sh round-trip`를 fresh Ubuntu에서 실행. **scenario-driven의 완성형**이며, dev compose swap·docker-compose V1 pin·healthcheck 루프·실패 시 log dump·teardown 컨벤션을 그대로 차용한다. 단 본 계획은 이 시나리오를 **manifest의 한 entry로 흡수**(Phase B)해 본문과 동기화한다.

### 2.2 UI 단언 기반 — Playwright 하네스
- `PortalPage.ts` (75KB, 도메인 verb 다수): `expectScanCompleted`, `openProjectDetail`, `selectTab`, `filterVulnerabilitiesBySeverity`, `openVulnerabilityDrawer`, `getDrawerEpssScore`, `expectProjectListVisible` 등.
- 도메인 하네스 9종: `auth.ts`, `seed.ts`, `AdminBackupHarness`, `AdminUsersHarness`, `AdminTeamsHarness`, `AdminScansHarness`, `AdminAuditHarness`, `AdminHealthHarness`, `AdminDiskHarness`, `ApprovalsHarness`, `NotificationsHarness`, `integrations.ts`.
- config 4종: `playwright.config.ts` / `.screenshots` / `.visual` / `.walkthroughs`. → 5번째 `playwright.docs-uat.config.ts` 추가.

→ User guide "Verify it worked"의 UI 단언(예: scans.md "project status switches to Succeeded")은 대부분 **이미 존재하는 verb**로 매핑된다. 없는 것만 신규 작성(규칙: 신규 화면/도메인은 하네스 verb 선작성).

### 2.3 본문 procedural 규모 (전수 전사 대상)
코드블록 약 **196개**. fence 언어 분포:

```
120 bash   21 json   16 yaml   10 text    9 python
  8 sh      7 groovy   3 ts      3 sql     2 http   2 mermaid  (기타 ini/hcl/css)
```

진입점: `## Verify it worked` **19개 페이지**(선행 지시문은 8로 기록 — 그새 증가), `## Troubleshooting` 14, `## Prerequisites`·`## Quick start` 3.

→ 전사 manifest의 1차 모수: **bash/sh(64블록) + http(2) + sql(3) + Verify-it-worked 스텝**. json/yaml/groovy는 실행이 아니라 **YAML/JSON 유효성 + 본문-실파일 정합 lint** 대상(§4.3).

---

## 3. authoring 모델 — annotation 스키마 (결정 2)

**본문이 곧 테스트.** 본문 마크다운에 HTML 주석으로 테스트 메타를 부착하고, extractor가 추출해 실행 manifest를 만든다. 주석은 Docusaurus 렌더에 보이지 않으므로 독자 경험 무손상.

### 3.1 두 가지 부착 지점

**(a) 블록 단위** — fenced 코드블록 바로 앞:
```markdown
<!-- docs-uat: id=quickstart-up kind=shell ctx=host expect=exit:0 tier=gate -->
​```bash
docker-compose -f docker-compose.dev.yml up -d
​```
```

**(b) 산문 스텝 단위** — `## Verify it worked` 리스트 항목 앞 (UI/관찰 단언):
```markdown
<!-- docs-uat: id=scan-succeeded kind=ui harness=expectScanCompleted tier=nightly -->
1. The project status switches to **Succeeded**.
```

### 3.2 필드 스펙

| 필드 | 필수 | 값 | 의미 |
|---|---|---|---|
| `id` | ✅ | kebab, 전역 유일 | 안정 식별자. 회전 ledger·리포트 키. |
| `kind` | ✅ | `shell`·`api`·`ui`·`sql`·`lint`·`manual` | 실행 디스패치 대상. |
| `ctx` | shell/sql | `host`·`backend`·`worker`·`postgres`·`kind` | 실행 컨테이너/위치. |
| `expect` | kind별 | `exit:0`·`exit:1`·`status:200`·`match:/re/`·`rows:>0`·`schema:<OpenAPI ref>` | 단언. |
| `harness` | ui | verb 이름(+args JSON) | PortalPage/도메인 하네스 verb. |
| `fixture` | 선택 | `seed_demo`·`scenario:scan-running`·… | 사전 상태. |
| `tier` | ✅ | `gate`·`nightly`·`weekly`·`manual` | 샘플 실행 등급(§4). |
| `waiver` | 선택 | 사유 문자열 | 실행 제외를 *명시*. drift lint가 "미커버"와 구분. |

### 3.3 extractor (다음 세션 산출)
`tools/docs-uat/extract.ts`(또는 .py):
1. `docs-site/docs/**/*.md`(EN canonical) 순회, `docs-uat:` 주석을 파싱해 다음 블록/리스트에 바인딩.
2. **전사 manifest** `docs-uat/manifest.json` 생성 — 모든 annotated step을 정규화. ← 결정 3 "전수 전사".
3. **커버리지 게이트**: 모든 `bash`/`sh`/`http`/`sql` fence와 모든 `## Verify it worked` 스텝은 *annotated 또는 waiver* 여야 한다. 미커버 → **lint 실패**. (= 침묵 누락 금지.)
4. **KO 패리티**: `i18n/ko/...` 미러는 동일 `id`·블록 구조 존재만 확인(명령 텍스트 실행은 EN만). KO 명령 텍스트 동등성은 §9 미해결 질문.

---

## 4. 전수 전사 + 샘플링 실행 (결정 3)

manifest는 **전수**(모든 스텝 전사), 실행은 **tier 샘플**. 이 둘의 분리가 "전분야를 커버하되 CI를 현실적으로"의 핵심.

### 4.1 tier 정의

| tier | 트리거 | 시간 예산 | 대상 |
|---|---|---|---|
| **gate** | PR (블로킹) | < 10분 | Quickstart 골든패스 + 핵심 api/sql/ui smoke 소수. dev compose. |
| **nightly** | cron 야간 | < 40분 | 영역별 대표 흐름 1~2개(install·user-guide UI·admin api/sql·ci dry-run). |
| **weekly** | cron 주간 | < 90분 | 무거운 것 — Helm on kind, published-image pull, air-gapped Trivy mirror, **회전 샘플**. |
| **manual** | 미실행 | — | OAuth/SMTP/Slack/GCP/실제 Git host. 전사+패리티만, 자동 실행 X. |

### 4.2 회전(rotation)으로 전수 실행 보장
weekly에서 gate/nightly에 들지 않은 전사 블록을 **week-of-year 키로 결정적 분할**해 매주 일부 실행 → N주에 모든 실행가능 블록이 ≥1회. `Date.now()`/random 미사용(재현성). 실행 ledger(`docs-uat/coverage-ledger.json`)에 "id별 마지막 실행 주차"를 기록해 **감사 가능**. 매 run은 **건너뛴 전사 스텝과 사유(tier/rotation/waiver)를 로그**로 노출(침묵 truncation 금지).

### 4.3 비-실행 블록의 정합 lint
json/yaml/groovy/hcl/ini fence는 실행 대신:
- **유효성**: YAML/JSON 파서 통과, groovy(Jenkinsfile)·hcl(terraform) 구문 검사.
- **본문 ↔ 실파일 정합**: 본문에 인용된 `templates/gitlab-ci.yml`·`.github/actions/...`·`charts/trustedoss/values.yaml` 스니펫이 실제 레포 파일과 drift 없는지(인용 블록을 실파일 substring으로 대조). ← 문서 예시 노후화 차단.

---

## 5. CI 실행 형태 — `docs-uat.yml` 매트릭스 (결정 4)

별도 lane. `install-uat.yml`의 컨벤션(V1 pin·dev compose swap·log dump·teardown·concurrency) 차용.

| job | 트리거 | 스택 | 역할 |
|---|---|---|---|
| `extract-and-lint` | **PR (블로킹)** | 없음(정적) | extractor 실행 → 미커버/waiver·스키마·KO 패리티·비실행 정합 lint. **drift 차단**. 빠름. |
| `quickstart-gate` | **PR (블로킹)** | dev compose | up → seed_demo → gate-tier shell/api/sql + 대시보드 Playwright probe. < 10분. |
| `docs-uat-nightly` | cron 야간 | dev compose + Playwright | 영역 매트릭스 `[install, user-guide, admin-guide, ci-integration]` nightly-tier 실행. |
| `docs-uat-weekly` | cron 주간 | + kind | helm-on-kind · published-image · air-gapped Trivy · 회전 샘플. |

> `feedback_ci_hardening_deferred_prerelease`와 무충돌: 이 lane은 신규 *별도* 추가이며 보류 대상(SAST/e2e flake 재활성화)과 무관. 단 PR 블로킹 두 job(`extract-and-lint`·`quickstart-gate`)은 첫 공개 릴리스 전까지 `continue-on-error`/non-blocking으로 두고, manifest 충실도가 안정화된 뒤 블로킹 승격 — §9 결정 7.

---

## 6. 권고 stack — 최소·균형·최대

| 옵션 | 포함 | cost | coverage | maintenance |
|---|---|---|---|---|
| **최소** | extractor + `extract-and-lint` + `quickstart-gate`만 | 낮음 | Quickstart + drift 차단 | 낮음 |
| **균형 (권장)** | 최소 + nightly(install/user-guide/admin) + 비실행 정합 lint | 중간 | 전분야 전사 + 핵심 실행 | 중간 |
| **최대** | 균형 + weekly(helm-kind/published/air-gap) + ci dry-run(act·gitlab-ci-local) + manual-checklist 자동생성 | 높음 | 전수 회전 실행 | 높음(외부 도구 의존) |

**권고**: Phase로 **최소→균형→최대** 점증. Phase A=최소(다음 세션), Phase B/C=균형, Phase D=최대.

---

## 7. 단계 전략 (Phase A → D)

| Phase | 머지 단위 | 산출 | 블로커 |
|---|---|---|---|
| **A** | annotation 컨벤션 spec + extractor + `extract-and-lint` + `quickstart-gate` + **Quickstart 전 구간 annotate** | 파이프라인 1개 수직 슬라이스 동작 증명 | 없음(자립) |
| **B** | Install/Backup·Restore를 manifest로 흡수(`install-uat` 시나리오 통합) + admin-guide api/sql 단언 | nightly install+admin | seed 픽스처 정의 |
| **C** | user-guide 10개 "Verify it worked" → Playwright verb 매핑, 누락 verb 신규 | nightly UI 매트릭스 | 하네스 verb 갭 |
| **D** | ci-integration dry-run(act/gitlab-ci-local + build-gate exit code) + helm-on-kind + air-gap Trivy + manual-checklist 자동생성 | weekly + 회전 | 외부 도구/시간 예산 |

각 Phase는 머지 가능 상태로 종료(CLAUDE.md 규칙 #6). Phase A 상세는 §6 산출물 #2(`2026-05-30-docs-verify-phase-A-impl.md`)에 자립 지시문으로.

---

## 8. 인접 lane — 축 A(가독성)·B(broken UI) (참고만)

사용자 초점은 축 C이므로 기본 **defer**(§9 결정 5). 착수 시:
- **축 B**: Docusaurus `onBrokenLinks/Anchors: "throw"`(0-cost, 이미 0건) + `lychee`(외부 링크, weekly+allowlist+retry) + 빌드후 `<video>/<img>` HEAD probe + KO locale prefix(`#268`형) 검증. → `extract-and-lint`에 흡수 가능.
- **축 A**: `Vale`(EN style) + 자체 metric script(문장>35단어·EN/KO 줄수 비율·heading 점프). KO 룰 부족이 한계. 별도 `docs-lint.yml` 권고.

---

## 9. 리스크 & 사용자 결정 필요 항목

### 리스크
- **annotation churn**: 본문 수정 시 주석 동기화 부담. → drift lint가 강제(장점이자 마찰). 결정 2의 수용된 trade-off.
- **UI 단언 취약성**: 하네스 verb ↔ UI 동기화 필요. → 기존 nightly e2e 안정화 자산에 편승.
- **외부/manual 흐름**: OAuth/SMTP/GCP/실 Git host는 자동 불가 → manual tier로 솔직히 전사+로그. 커버리지 갭을 *숨기지 않음*.
- **gate 시간 예산**: dev compose 콜드부트(~30s)+seed+scan은 빠듯. → 이미지 캐시, scan류는 nightly로 강등.
- **scan 류 길이**: cdxgen/Trivy 실스캔 수 분 → gate 불가, Trivy DB 캐시한 nightly/weekly.

### 사용자 결정 필요 (다음 세션 착수 전)

| # | 질문 | 권장 기본 |
|---|---|---|
| 1 | KO 미러 검증 깊이 | 구조 패리티만(권장) vs 명령 텍스트 동등성까지 |
| 2 | Phase A annotate 범위 | Quickstart만(권장) vs Quickstart+Install |
| 3 | CI dry-run 엔진 | `gitlab-ci-local`+`act` 둘 다 vs 하나 vs manual로 미룸 |
| 4 | Helm e2e | weekly kind(권장) vs manual uat-checklist만 |
| 5 | 축 A/B 인접 lane | 지금 defer(권장) vs C와 병행 착수 |
| 6 | manual-checklist | 자동생성이 hand `uat-checklist.md` 대체(권장) vs 병존 |
| 7 | drift lint 강도 | 릴리스 전 non-blocking → 안정 후 blocking 승격(권장) vs 즉시 blocking |

---

## 10. 종료 조건 (선행 지시문 §10 대응)
- [x] 설계·타당성 보고서(본 문서) — 결정 2·3·4 반영.
- [ ] `docs/sessions/2026-05-30-docs-verify-phase-A-impl.md` — Phase A 자립 시작 지시문.
- [ ] 두 파일 단일 PR 머지.
- [ ] 다음 세션이 cold start로 산출물 #2를 첫 메시지로 사용 가능.
