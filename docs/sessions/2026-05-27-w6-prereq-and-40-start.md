# 핸드오프 — W6 prereq 완료 + #40 PR (Trivy adapter) + 운영 결정 다수 (2026-05-27)

> SoT는 [`post-ga-execution-tracker.md`](../post-ga-execution-tracker.md) §0.5 W6. 이 문서는 그 세션 스냅샷이다.
> 직전 세션: [`2026-05-27-w6-dt-removal-plan.md`](./2026-05-27-w6-dt-removal-plan.md) — W6 계획 v2 수립 (계획만, 코드 0).
> 본 세션: 계획 → 실행 첫 사이클. **W6-#45 머지 + v2.3.1 prereq 스킵 결정 + repo private 전환 + #40 PR (security-reviewer fix 포함, 머지 대기)**.
> 결정 메모리: [[project_dt_removal_decision]] (방향 + amendment) + 본 핸드오프 (실행).

---

## 이번 세션 결과

### 머지된 PR (5건)

| PR | 제목 | scope | commit |
|---|---|---|---|
| #192 | docs(W6-#45): ADR-0001 — DT removal + Trivy replacement plan | ADR 신규 + CLAUDE.md 11곳 + post-ga-roadmap 14곳 + 트래커 §0.5 W6 + 핸드오프 신규 | `f68d688` |
| #193 | chore(env): default DEMO_SUPER_ADMIN_PASSWORD for seeded demo accounts | `.env.example` 1줄 + 코멘트 확장 | `88598b3` |
| #194 | ci: skip CI for docs/config-only PRs (paths-ignore) | `ci.yml`·`sast.yml` paths-ignore (`docs/**`·`*.md`·`.env.example`·`CLAUDE.md` 등) | `e6b2638` |
| #195 | docs(W6): skip v2.3.1 prereq tag — v2.4.0 = first DT-free public release | ADR-0001 amendment + 트래커 §0.5 W6-prereq ⛔ 스킵 + roadmap §0 W6 note 정정 + #40 🟦 표기 | `b2623ce` |
| (PR 없음) | repo visibility flip | `gh repo edit --visibility private` (사용자 직접 실행, 권한 정책상 AI 차단) | — |

main HEAD: `b2623ce` (2026-05-27 23:55 UTC 기준). **#196 머지 후 갱신 필요.**

### 진행 중 PR

| PR | 제목 | 상태 |
|---|---|---|
| #196 | feat(W6-#40): add run_trivy_sbom adapter for CycloneDX → CVE matching | 🟦 CI 진행 중 (backend test ~14m). 2 commits: `cea2a9a` (어댑터 + 33 tests) + `5bf6970` (security-reviewer M1+L3 fix + 3 tests). 머지 대기. |
| (다음) #197 추정 | docs(W6): benchmark cohort ADR-0002 + W6-#40 session handoff | 본 핸드오프 + ADR-0002 (벤치 코호트 선정) — docs-only, CI skip |

### 결정 — v2.3.1 prereq 스킵 (같은 날 amendment)

본 세션 중반에 사용자 결정 2건이 W6 계획을 단순화:
1. **CI 시간 최소화** → PR #194 paths-ignore 도입. docs/config-only PR은 0초 CI.
2. **repo W6 동안 private 전환** → v2.3.1 "Final DT Release" 공개 마커 무의미 → prereq **스킵**. v2.4.0이 DT-free 첫 공개 릴리스. O1(첫 이미지 게시)/O3(차트 ArtifactHub)은 v2.4.0 GA에 통합.

→ W6 시퀀스 단축: ~~prereq → #45~~ → **#45 ✅ → #40 → #41 → shadow → #42 → #43a~e → #44** (8 PR + 선택 1). ADR-0001에 amendment 블록 추가.

### W6-#40 (PR #196) — Trivy SBOM adapter

`apps/backend/integrations/trivy.py`에 `run_trivy_sbom(sbom_path, output_dir, *, timeout_seconds, backend) -> TrivyResult` 추가. 기존 `run_trivy_image` 패턴 (TrivyNotInstalled/Failed/Timeout/Result/`_load_json`) 100% 재사용.

- 출력: `<output_dir>/trivy-sbom.json` (이미지 스캔 `trivy.json`과 구분)
- mock backend 헬퍼 `_write_mock_sbom_report` 함께 추가
- `__all__`에 `run_trivy_sbom` 추가

**테스트**: `tests/unit/integrations/test_trivy_sbom.py` (745+200 lines, 36 tests).
- happy/input-validation/subprocess-error/adversarial severity 9건/adversarial URL 6건/shape-gap 7건
- **+ security-reviewer fix 3건**: env scrub 검증, TRIVY_DB_REPOSITORY 전달, `--scanners vuln` 핀

**security-reviewer 결과 (background agent `a764f067a51cf129e`)**: 평결 **CHANGES REQUESTED**, Critical/High 0.
- **M1 (본 PR fix 완료)**: subprocess env 스크럽 누락 → `scrubbed_env_for_trivy()` 신규 (`_subprocess_env.py`). 양쪽 subprocess.run에 `env=` 명시. cdxgen/scancode/cosign 표준 정합.
- **L3 (본 PR fix 완료)**: `run_trivy_sbom`에 `--scanners vuln` 추가.
- **L1·L2·L4 (후속 chore PR)**: path-traversal guard / output JSON size cap / error message path redaction. W6-#41 PR과 묶어 처리.
- **I1 무시**: agent의 base 비교 오해 (ci.yml/sast.yml/.env.example diff 멘션은 본 PR diff에 없음).

**검증 (로컬 docker pytest + ruff + mypy)**:
- `pytest tests/unit/integrations/{test_trivy_sbom,test_subprocess_env}.py`: **84 passed** (33 sbom + 48 env + 3 신규, 회귀 0)
- ruff/mypy: 4 files clean

### 운영 변화 (코드 변경 외)

| 항목 | 변경 |
|---|---|
| Repo visibility | public → **private** (사용자 직접 실행 2026-05-27) |
| CI 워크플로 트리거 | ci.yml·sast.yml에 paths-ignore 도입 — docs/config-only PR은 CI 0초 |
| dependabot | private 전환으로 활성화. push 출력 51 alerts (10 high / 35 moderate / 6 low) 노출. **현재 미트리아지** — [[feedback_ci_hardening_deferred_prerelease]] 정합 ("출시 전 일괄") 또는 사용자 결정 |

### 메모리 추가 (4건 + 1건 갱신)

- 신규 [[feedback_v1_reuse_table_as_silent_default]] — 메이저 재설계 시 v1 재사용 표는 default가 됨
- 신규 [[feedback_category_label_blocks_capability_search]] — 도구 카테고리 라벨링이 능력 검색 차단
- 신규 [[feedback_operational_cost_only_visible_after_annual_accrual]] — 운영 비용은 1년 누적 후 가시화. 도입 PR에 측정 가능한 KPI 강제
- 갱신 [[project_dt_removal_decision]] — v2.3.1 prereq 스킵 amendment 반영

이 3건은 "왜 처음에 Trivy로 안 했나" 회고 질문에서 도출 — DT 도입(2026-05-05) 시점의 3가지 의사결정 누락을 일반화.

---

## 다음 세션이 해야 할 일 (자립 가능 형식)

### 1) PR #196 머지 + 트래커 갱신 — 가장 먼저

- main 동기화 (`git pull --ff-only`)
- 트래커 §0.5 W6 row: `#40 🟦` → `✅ (PR #196)`, count `1 → 2`. 본문 W6-#40 row도 ✅ 갱신.
- 별도 docs PR로 묶어 처리 가능 (paths-ignore → CI 0초)

### 2) L1·L2·L4 후속 chore PR (W6-#40 보안 리뷰 잔여) — W6-#41과 묶어 처리

**파일**: `apps/backend/integrations/trivy.py` + 신규 helper.

- **L1**: `output_dir`/`sbom_path` workspace_root 경계 검증 헬퍼 `_ensure_inside_workspace(p, label) -> Path`. `resolve()` 후 `is_relative_to(workspace_root())` 검사. `run_trivy_image`/`run_trivy_sbom` 양쪽 진입부에서 호출.
- **L2**: `report_path.stat().st_size > 256MB` 시 `TrivyFailed("trivy output too large")`. `_load_json` 직전 가드.
- **L4**: `TrivyFailed`에 `safe_detail` 속성 (sbom_path basename만) 추가. 어댑터 메시지는 절대경로 유지 (운영 로그용), 호출자가 problem+json detail로 surface 시 `safe_detail` 사용.

테스트 3건 추가 권고.

### 3) W6-#41 — Trivy persist + 벤치 코호트 (본 사이클의 핵심)

**파일**:
- `apps/backend/tasks/scan_source.py:2067` `_poll_dt_findings_with_retry` → 신규 `_run_trivy_match` (또는 `_poll_trivy_findings` — 이름 유지 옵션은 시간순). `run_trivy_sbom(sbom_path, output_dir)` 호출 → Trivy JSON `Results[].Vulnerabilities[]` shape를 DT shape `[{vulnerability: {vulnId, source, ...}, component: {purl}}]`로 변환 (또는 `_persist_findings` Trivy shape 직접 수용).
- `scan_source.py:535` `_set_stage("dt_findings")` 호출은 **stage 이름 유지** (WS frame·E2E 하네스 호환, rename은 #43f).
- `apps/backend/services/vulnerability_matching.py` 신규 — Trivy JSON → finding row 변환 로직 (severity 정규화·References URL sanitize·idempotency key `(scan_id, VulnerabilityID, PkgName, InstalledVersion)`).
- `scripts/benchmark_dt_vs_trivy.py` 신규 — [ADR-0002](../decisions/0002-w6-trivy-benchmark-cohort.md)의 cohort 표를 cohort.json으로 옮긴 다음, 두 어댑터 호출 + 결과 set-diff `(cve_id, component_purl)`. 일치율 + FP/FN 분류 보고서.
- ADR-0002 update commit — 측정 시점 commit SHA + cohort.json 첨부.

**security-reviewer 필수** (untrusted JSON 파싱 + 정규화 로직). adversarial parametrize 강제.

**종료 조건**: 매칭 일치율 ≥95% + FP/FN 분류 보고서 + e2e green.

### 4) shadow 7일 게이트

#41 머지 직후 시작. 평균 일치율 ≥95% 또는 조기 종료 조건 (3일 연속 ≥95% + 누적 ≥30건 + 생태계 ≥3종) 충족 → #43a 진행 승인.

미달 시 Plan B/C 회의 트리거 — 미달 시점 실측 데이터 (어떤 PURL/CVE가 누락) 보고 결정 (사용자 선호 미정).

### 5) dependabot 51 alerts 처리 결정

옵션:
- (a) [[feedback_ci_hardening_deferred_prerelease]] 정합 — 출시 전 일괄. high 10건 정도만 미리 트리아지.
- (b) 우선 high 10건 즉시 처리 (보안 리스크 노출 줄임).
- (c) 모두 일괄 (작업량 큼, 다른 W6 작업 일정 영향).

사용자 결정 필요.

---

## 핵심 참조 파일

- **계획 SoT**: `docs/post-ga-execution-tracker.md` §0.5 W6
- **결정 ADR**:
  - `docs/decisions/0001-replace-dt-with-trivy.md` (DT 제거, v2.3.1 스킵 amendment 포함)
  - `docs/decisions/0002-w6-trivy-benchmark-cohort.md` (벤치 코호트 선정)
- **방향 메모리**: [[project_dt_removal_decision]] (방향 + amendment)
- **출발 코드**:
  - W6-#40 결과물: `apps/backend/integrations/trivy.py` (`run_trivy_sbom`), `apps/backend/integrations/_subprocess_env.py` (`scrubbed_env_for_trivy`)
  - W6-#41 출발: `apps/backend/tasks/scan_source.py:535` (stage trigger), `:2067` (`_poll_dt_findings_with_retry` 교체 대상), `:2779` (`_persist_findings` 입력 shape)
- **인벤토리 (변경 없음, 직전 핸드오프 참조)**:
  - BE: `integrations/dt/` 4파일 · `tasks/dt_*.py` 4파일 · `api/v1/admin/dt.py` · `core/config.py:434-465` 8 getter
  - FE: `features/admin/dt/` 3파일 · `router.tsx` · `AppShell.tsx`
  - 인프라: `docker-compose.yml`·`docker-compose.dt.yml`·`scripts/install.sh:400`·`scripts/ci/provision-dt.sh`·`charts/trustedoss/` 6파일
  - 문서: Docusaurus 15페이지

---

## 컨벤션 알림 (W6 진행 특화)

- **stage 이름 유지**: `dt_upload`/`dt_findings`는 WS frame·E2E 하네스 의존. #41은 이름 유지, #43f(선택·v2.4.1)에서 별도.
- **데이터 손실 0**: DT 전용 테이블 없음. `vulnerability_findings`는 캐시였고 Trivy 재매칭으로 새로 채워짐 — 마이그레이션 불필요.
- **audit_log 보존**: DT 액션 타입은 역사 사실로 보존. admin UI 필터는 deprecated 표기.
- **release-notes/v2.0.0.md 보존**: 역사 사실.
- **security-reviewer 필수**: #41·#43a (untrusted JSON 파싱·권한 우회/잔여 endpoint).
- **adversarial parametrize**: Trivy JSON 파서는 [[feedback_adversarial_input_parametrize]] 강제.
- **EN/KO 동시**: 모든 문서/UI 변경. `i18n:check`·복수형 금지 [[feedback_frontend_i18n_no_plural_check]].
- **docker-compose V1**: `docker-compose` (하이픈) — `docker compose` 금지 [[feedback_docker_compose]].
- **paths-ignore 활용**: docs/config-only PR은 자동 CI skip — 작은 doc 갱신은 따로 묶지 말고 단독 PR도 OK.
- **subagent worktree 격리**: 병렬 작업 시 `isolation: "worktree"` 명시 [[feedback_parallel_subagent_worktree_isolation]].
- **메모리 절차는 본문 인용**: 인덱스 한 줄 요약만 보고 답하지 말고 본문 Read [[feedback_quote_memory_steps_verbatim]].

---

## 본 세션 메타 (회고)

- **작업량**: 5 PR 머지 + 1 PR 진행 중 + 4 메모리 신규/갱신 + 2 ADR + 1 핸드오프. 사용자 결정 6건 (벤치 코호트 방식·air-gapped·shadow 7d·Plan B/C·CI 시간·repo private).
- **CI 시간 절감 효과**: PR #194(paths-ignore) 도입으로 본 세션 후반 docs PR (#195·#197 추정)이 14m → 0초. 향후 W6 docs PR 다수 (#43c 15페이지·#43d·트래커 갱신)에 큰 영향.
- **회고 질문에서 메모리 3건 도출**: "왜 처음에 Trivy로 안 했나" 질문이 단순 회상이 아니라 의사결정 누락 패턴 일반화 → 메모리 자산화. 다음 메이저 재설계 시 v1 재사용 표 자동 의문시 + 도구 카테고리 라벨링 의문시 + 1년 운영 KPI 강제 가능.
- **paths-ignore 도입 사이드 이펙트**: dependabot이 private 전환과 동시 활성화돼 51 alerts 노출. 사용자 트리아지 결정 대기.
