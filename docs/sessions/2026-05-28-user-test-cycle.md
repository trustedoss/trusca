# 핸드오프 — 사용자 테스트 사이클 (2026-05-27 ~ 2026-05-28)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 W6. 본 문서는 사용자 테스트 동안 발생한 25개 PR(#206 ~ #230)의 누적 결과 + 다음 세션 시작 지점을 담는다.
> 직전 핸드오프: [`2026-05-27-w6-43b-fe-dt-removal.md`](./2026-05-27-w6-43b-fe-dt-removal.md).
> 다음 세션: **W6-#43c 사용자/관리자 문서 교체** (트래커 §0.5 W6).
> 결정 메모리: [[project_dt_removal_decision]], [[feedback_pr_immediate_merge]].

---

## 이번 사이클 결과 — 25 PR 머지

### W6 라인 (트래커 등록 row)

| PR | 제목 | scope |
|---|---|---|
| #205 | feat(W6-#43a): remove DT from backend | (직전 핸드오프) |
| #206 | feat(W6-#43b): remove DT from frontend | `/admin/dt` 제거 + i18n + visual baseline |
| #207 | chore(W6): drop dt_upload noop step + retire DT mentions in scan progress | PIPELINE_STEPS 7→6 + i18n 정리 |
| #208 | chore(ci): temporarily disable test matrix jobs | dev velocity |
| #209 | chore(ui): rename "Releases" tab → "Versions" | label-only EN/KO |

### 사용자 테스트 라인 — DT 무관, UX 개선

| PR | 영역 | 변경 요약 |
|---|---|---|
| #210 | Overview | severity/license 좌우 + 헤더 RiskGauge 제거 + Recent Scans Version 컬럼 |
| #211 | Trivy 매칭 | `_TRIVY_TYPE_TO_PURL`에 sbom-mode 토큰 추가 (`node-pkg`/`python-pkg`/`go-module` 등) — 11 vuln 적재 정상화 |
| #212 | Sheet 폭 | scan progress drawer `sm:max-w-3xl` |
| #213 | Overview | Project info ↔ Build gate 좌우 페어 + Severity ↔ License 좌우 + `items-start` |
| #214 | Components | LICENSE 분리(SPDX + POLICY) + `min-w-[820]` + checkbox 축소로 정렬 |
| #215 | Overview | License chart 범례 2×2 grid (label 겹침 fix) |
| #216 | List 탭 | Components/Vulnerabilities/Compliance 탭에 분포 카드 + segment filter |
| #217 | Tabs nav | tab 전환 push history (뒤로 가기로 이전 탭 복귀) |
| #218 | Vulnerabilities | finding-level severity_distribution (component-axis가 가짜 Info=1 보여주던 갭) |
| #219 | Overview | 차트 deep-link도 history push |
| #220 | CI fix | ruff E501 (line length) |
| #221 | Distribution 카드 | AxisPill ("by component"/"by finding") + Vuln 탭 subtitle 분리 |
| #222 | Vulnerabilities | row column 재정렬 (CVE/Component/Severity/...) + Summary header 라벨 + PDF Reports 탭으로 이동 + License 컬럼 drop |
| #223 | Vulnerabilities | Component 검색 + Component sort key |
| #224 | Vulnerabilities | checkbox `w-6→w-4` + gap `gap-3→gap-2` → CVE 컬럼이 카드 헤더와 정렬 |
| #225 | BE fix | route 의 sort regex pattern 에 `component` 추가 (422 fix) |
| #226 | dev velocity | uvicorn `--reload-exclude tests/*` + `--timeout-graceful-shutdown 5` + Component flex-1 + Status fixed |
| #227 | Nav | Dashboard 제거 + `/` → `/projects` redirect |
| #228 | Projects | 헤더 분포 카드 (Severity interactive + License 표시만) |
| #229 | Shell | 헤더 중복 "TrustedOSS Portal" 제거 (justify-end) |
| #230 | Projects | **by-project axis** (Severity FE client / License BE+FE) + License 필터 wire-up + Created-by 컬럼 |

### 즉시 머지 정책 ([[feedback_pr_immediate_merge]])

세션 도중 사용자가 명시: `gh pr create` 직후 곧바로 `gh pr merge --squash --delete-branch`. CI 폴링 백그라운드 잡 금지. 사전 로컬 게이트(typecheck/lint/i18n:check/vitest/ruff/mypy/pytest)로 검증 충족. 본 사이클 PR 25건 모두 이 패턴으로 진행.

---

## 사용자 테스트 종료 상태

**maven-node 프로젝트 (univ4 데모)**:
- 11 vuln finding 적재 (node-pkg 매핑 fix 후, PR #211)
- jar 1건은 별도 — purl namespace 미스매치, 후속 분석 backlog
- Versions 탭 / 분포 카드 / Component 검색-정렬 / VEX 흐름 / Reachability(Go-only) / EPSS 모두 정상 동작 확인

**대시 환경**:
- Frontend vite HMR 정상
- Backend uvicorn reload — PR #226 적용 후 hang 재발 0건
- demo-org 시드 (PR #204) 5 user 그대로

---

## 다음 세션이 해야 할 일

### 1) W6-#43c 사용자/관리자 문서 교체 (메인 작업)

EN/KO 동시. 신규 `docs-site/docs/admin-guide/vulnerability-data.md` (Trivy DB 운영 — 동기화/air-gapped/트러블슈팅) + `docs-site/docs/reference/data-sources.md` (NVD/OSV/GHSA/EPSS/KEV reference + DT 문서 비교 갭 분석). `admin-guide/dt-connector.md` 삭제 + sidebars 정리. 핵심 4페이지 교체 (`reference/{architecture,env-variables}`, `installation/{docker-compose,helm}`) + 9건 멘션 정리(`intro`/`comparison`/`user-guide/{scans,vulnerabilities}`/`admin-guide/{disk-and-health,oncall-runbook}`/`ci-integration/github-actions`/`reference/glossary`/`contributor-guide/agent-team`). `release-notes/v2.0.0.md` 보존(역사) + `release-notes/v2.4.0.md` 초안 (Breaking changes + Migration — 본 사이클 25 PR도 v2.4 release notes에 합산). 검증 — docusaurus build EN/KO green + 깨진 링크 0 + i18n:check OK. **dep: W6-#43b**. owner: `doc-writer` + `i18n-specialist`. **예상 1.5d**.

### 2) W6-#43d 배포·Helm·운영자 마이그레이션

`scripts/install.sh` DT_API_KEY 안내 제거 + Trivy DB 안내 추가. `scripts/upgrade.sh` v2.3→v2.4 절: Celery 큐 drain 대기 + `dtrack-api` 컨테이너 정리 + DT 볼륨 archive 안내 + `.env` DT_* 자동 주석처리 + 부팅 후 "전체 재매칭" admin UI 트리거. `docker-compose.yml` DT 코멘트 6곳 + `docker-compose.dt.yml` 삭제. `charts/trustedoss/` 0.2.x → 0.3.0. `release-notes/v2.4.0.md` 최종화 + GitHub Release body draft. **dep: W6-#43c**. owner: `devops-engineer`.

### 3) W6-#43e admin/health Trivy DB 패널 신설

BE 신규 `GET /v1/admin/trivy/health` (last update, vuln count, DB version, next refresh ETA). FE admin/health에 `TrivyDBPanel` 추가. EN/KO. **dep: W6-#43b (FE) + W6-#44 (라이프사이클 데이터 소스)**.

### 4) W6-#44 Trivy DB 라이프사이클 관리

worker 부팅 시 `trivy --download-db-only` + weekly beat refresh + air-gapped `TRIVY_DB_REPOSITORY` 미러 매뉴얼 + `trivy image`/`trivy sbom` 동시성 lock 테스트. **dep: W6-#40**.

### 5) W6-#43f (선택) stage rename v2.4.1

`scan_source.py` `dt_upload`/`dt_findings` → `sbom_upload`/`vuln_match`. WS frame + E2E 하네스 + 진행 i18n 동시. **dep: W6-#43a 머지 후 임의**.

### 6) 후속 chore (어디든 가능)

- **W6-chore-#43a-doc-drift** — Low 4건 + Info 2건 docstring 정리 (트래커 본문 참조)
- **W6-chore-#42-followup** — M-2/M-4/L-1/L-2 (rematch poison-pill, scan_artifact flag, diff key, critical→unknown 가시화)
- **사용자 테스트 chore** — Vuln 탭의 `jar=1 vuln 미적재` 원인 분석 (purl namespace mismatch 추정), `useProjectsSummary` 제거 후 `/v1/dashboard/summary` BE endpoint rename 또는 retire, Compliance 탭/Components 탭의 분포 카드도 by-project 검토 (현재 component-level 그대로 — 페이지 컨텍스트가 component 중심이라 적절할 수 있음)

---

## 핵심 참조 파일

- **계획 SoT**: `docs/post-ga-execution-tracker.md` §0.5 W6
- **결정 ADR**: `docs/decisions/0001-replace-dt-with-trivy.md`, `0002-w6-trivy-benchmark-cohort.md`
- **방향 메모리**: [[project_dt_removal_decision]], [[feedback_pr_immediate_merge]], [[feedback_ci_hardening_deferred_prerelease]]
- **사이클 PR 인벤토리**: 위 표 (PR #206~#230)

---

## 컨벤션 알림 (사이클 도출)

- **PR 즉시 머지**: `gh pr create` 직후 곧바로 squash merge. CI 폴링 금지. ([[feedback_pr_immediate_merge]])
- **stage 이름 유지**: `dt_upload`/`dt_findings` WS frame + E2E 하네스 호환 — #43f까지. PIPELINE_STEPS 표시 텍스트만 정정 (PR #207).
- **axis pill 표시 의무**: 분포 카드는 axis(by component/by finding/by project) 명시 — 같은 제목의 두 카드가 다른 측정값을 보여줄 때 사용자 혼란 방지 (PR #221, #230).
- **batched query ceiling**: `project_list_enrichment.enrich_project_rows`는 6 queries 한도 (status + succeeded-id + severity + counts + license + user). 다음 필드 추가 시 같은 패턴 유지.
- **dev backend reload**: `--reload-exclude 'tests/*'` + `--timeout-graceful-shutdown 5` (PR #226). 테스트 편집이 prod reload 트리거 안 함.
