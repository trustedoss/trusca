---
title: 테스트 하드닝 계획 — 실제 상황 버그를 미리 잡는 6-Tier 전략
date: 2026-05-22
status: plan
owner: scan-pipeline e2e hardening
---

# 테스트 하드닝 계획 (6-Tier)

> 배경: 2026-05-22 fixtures e2e + 하드 부하 세션에서 단위/통합 테스트가 **전부 green**이었음에도
> 실제 버그 4건(pkg:nix 오탐, 복합 SPDX 오분류, license self-heal 누락, PDF 이벤트루프 블로킹)을
> 못 잡았다. 원인: 과한 mock(cdxgen mock / scan enqueue stub) + "성공했는가"만 단언(상태코드).
> 이 계획은 **실제 상황 버그를 미리 잡기 위한** 6개 테스트 층을 정의한다.

## 0. 설계 원칙 (모든 Tier 공통)
1. **출력 내용을 단언한다 — 상태코드가 아니라.** (골든 베이스라인)
2. **불변식(invariant)으로 비결정성을 다룬다.** 개별 케이스 망라 불가 영역은
   "누수 0 / 고아 0 / 중복 0 / 5xx 0"를 카오스·퍼즈로 검증.
3. **티어별 CI 배치를 분리한다.** 빠른 정적/단위 → PR 게이트, 느린 실-도구/부하/브라우저 → nightly/릴리스 전.
4. **결정성 우선.** 외부 의존(NVD feed, 레지스트리)은 핀/시드/녹화로 결정적으로 만든다.
5. **mock은 로직 단위에만.** 통합·골든 티어는 실 도구/실 DT.

---

## Tier 1 — 골든 fixture 코퍼스 + 베이스라인 diff
**잡는 버그**: 컴포넌트/transitive 누락·오탐, 라이선스 오분류, 출력 회귀 (이번 nix·복합 SPDX 류).

- **메커니즘**: 각 fixture를 실 파이프라인으로 스캔 → 출력 정규화 → 커밋된 베이스라인 JSON과 diff. 드리프트=fail.
- **재활용**: `~/projects/bd-scan/tests/fixtures/projects`(36개) + 본 세션 batch 하네스(`e2e_batch.sh`).
- **정규화**: timestamp/scan_id/UUID 제거, purl·라이선스 정렬, 카운트만. (실행 간 안정)
- **캡처 항목/베이스라인 스키마**(per fixture):
  ```json
  {"components":{"count":N,"purls":[...sorted]},
   "licenses":[{"spdx":"...","category":"...","kind":"..."}],
   "source_tree_root_entries":N,
   "notice":{"text":true,"html":true,"csp":true},
   "sbom":{"cyclonedx-json":true,...},
   "report_pdf":true}
  ```
- **위치**: `apps/backend/tests/e2e/golden/` + `tests/e2e/golden/baselines/<fixture>.json`.
  `--update-baselines` 플래그로 의도적 갱신(리뷰에서 diff 확인).
- **CI**: nightly (실 스택+도구 필요). marker `golden`.
- **선행**: 실 스택+DT 기동. 취약점은 베이스라인 제외(Tier 5에서 결정적으로).

## Tier 2 — 실-도구 통합 티어 (cdxgen/scancode/DT 언모크)
**잡는 버그**: 도구 실제 동작/버전 드리프트, DT 라운드트립(BOM hash sanitize, findings poll), prep 로직.

- **메커니즘**: `TRUSTEDOSS_SCAN_BACKEND=real` + 실 DT로 스캔 태스크를 생태계 매트릭스(npm/yarn/pnpm/gradle/maven/poetry/pip/go/rust/ruby/dotnet/php)별 1건씩 end-to-end 실행.
- **Tier 1과 관계**: Tier 2 = *실행 환경*(실 도구·핀 버전·DT), Tier 1 = 그 위 *단언*(베이스라인). 같은 nightly 잡에서 함께.
- **추가**: 도구 버전 핀 단언(cdxgen/scancode/trivy/JDK/node) — 버전 바뀌면 의도적 bump 강제.
- **CI**: nightly + 릴리스 전. marker `real_tools`.

## Tier 3 — 부하/동시성 게이트 + async-blocking 정적 감사
**잡는 버그**: 이벤트루프 블로킹(이번 PDF 류), 풀 고갈, 5xx-under-load, SLO 회귀.

- **(a) 부하 게이트** (재활용: `tests/load/locustfile.py` + `run_hard.sh`, 본 세션 산출):
  - SLO 게이트 런(낮은 부하, p95/p99/fail_ratio 엄수, exit-code) — nightly 게이트.
  - 스트레스 런(천장 탐색, **5xx=0**만 단언, 지연은 정보) — nightly/릴리스 전.
  - 이탈 부하(Tier 6와 연계): 요청 중간 취소 유저 → 서버 자원 거동 측정.
- **(b) async-blocking 정적 감사** (신규, PR 게이트): `async def` 엔드포인트가
  `run_in_threadpool`/`anyio.to_thread` 없이 알려진 블로킹 호출(weasyprint, `tarfile.open`,
  대용량 `json.dumps`/`.dumps()` 직렬화, 동기 파일 I/O)을 부르는지 AST/룰 검사. 위반=fail.
- **CI**: 부하=nightly(실 스택), 감사=PR 게이트(정적, 빠름).

## Tier 4 — 상태/재실행/교차 경로 + 서버 카오스
**잡는 버그**: 2차 호출 회귀(이번 license self-heal), IDOR, dedup 충돌, 의존 서비스 장애 거동.

- **결정적 통합 케이스**(PR 게이트, DB):
  - **재스캔**: 같은 프로젝트 2회 스캔 → 카테고리 재분류·dedup·고아 정리 정확.
  - **교차팀 IDOR**: 모든 프로젝트-스코프 read 404 existence-hide 일관.
  - **dedup 충돌**: 동일 purl/spdx 다중 프로젝트 → 전역 카탈로그 정합.
  - **빈/실패 스캔**: 0 컴포넌트, dt_upload 실패 → 보존 미수행·상태 정확.
  - **optimistic concurrency**: `if_match` 충돌 → 412/409.
- **서버 카오스**(nightly, 스택 조작):
  - **DT 다운 → breaker OPEN → PostgreSQL 캐시 응답**(circuit breaker 핵심 거동).
  - **worker 중단/OOM 중 스캔**: 스캔 failed 마킹, workspace 정리, 고아 0.
  - **디스크 가득 / quota 초과**: 보존 graceful skip, 5xx 0.
- **CI**: 결정적=PR, 카오스=nightly. marker `chaos`.

## Tier 5 — 적대적 입력 + 결정적 취약점 검출
**잡는 버그**: untrusted 파서 취약점(이번 복합 SPDX 류), **"CVE 검출이 0이 됐다"는 무성한 회귀**.

- **(a) 적대적/property 테스트**(PR 게이트, mock): SPDX 표현식, purl, license 텍스트,
  NOTICE/PDF 이스케이프, source-tree 경로에 hypothesis property + parametrize
  (복합·CRLF·과대·null byte·`javascript:`/`file:`·재귀 DoS). 기존 [[feedback_adversarial_input_parametrize]] 확장.
- **(b) 결정적 취약점 검출**(nightly — 현재 완전 공백):
  - **known-CVE fixture**: 잘 알려진 CVE를 가진 핀된 의존성(예: 특정 구버전 패키지) 포함.
  - **결정적 vuln 데이터**: 다음 중 택1 — ① 작은 OSV/NVD 서브셋을 DT에 시드,
    ② DT API로 known vuln 주입, ③ DT findings 응답을 녹화한 fixture로 어댑터 단(레벨) 테스트.
  - **단언**: 해당 CVE 검출 + severity + 취약점 보고서 PDF/SBOM 반영 + (있다면) 빌드 게이트 차단.
- **CI**: 적대적=PR, vuln 검출=nightly. marker `vuln_detect`.

## Tier 6 — 행동 불량 클라이언트 복원력 (client-abandonment)
**잡는 버그**: 중간 이탈 시 자원 낭비/누수, WS 누수, 중복 제출, 세션 만료 중 깨짐.

- **불변식**: ① 떠난 클라이언트에 무한정 작업 안 함 ② 끊김에 자원(연결·Celery task·fd·temp·WS) 누수 0 ③ 중복/재전송에 상태 무결.
- **(a) Playwright 이탈 시나리오**(nightly, 브라우저): 작업 중 `page.close()`/`context.close()`/
  `goto('about:blank')`; in-flight `route.abort()`; 스캔 진행 중 `reload()`; `clickTriggerScan` 더블클릭;
  `setOffline(true)`로 WS 중단·재연결. PortalPage 하네스에 verb 추가.
- **(b) 백엔드 단위/통합**(PR 게이트): 무거운 엔드포인트(PDF/SBOM)가 `request.is_disconnected()` 체크하는지;
  WS 핸들러가 disconnect에 정리하는지; 스캔 트리거 멱등성(더블클릭→중복 스캔 0, 팀 캡 준수).
- **(c) 누수 invariant 가드**(nightly): 이탈 폭주 전후 backend **열린 연결/Celery active/temp 파일/fd**
  스냅샷 비교 → 증가=fail.
- **(d) 이탈 부하**(Tier 3 연계): locust 유저가 요청 중간 abort → 떠난 클라 PDF 렌더 낭비/연결 누수 측정.
- **의심 우선 타깃**: ① WS 스캔 스트림 누수 ② 버려진 PDF 렌더 낭비.
- **CI**: 백엔드 단위=PR, Playwright/누수/이탈부하=nightly.

---

## 공유 인프라 (한 번 만들고 재사용)
- **실-파이프라인 하네스**: 본 세션 `e2e_batch.sh`를 pytest 하네스로 승격 (fixture 스캔 드라이버 + 출력 정규화 + 베이스라인 store).
- **결정적 vuln 시더**: OSV/NVD 미니 서브셋 또는 DT 주입 헬퍼.
- **누수 스냅샷 헬퍼**: 연결/태스크/fd/temp 카운터.
- **이탈 시뮬레이터**: Playwright verb + locust 취소 유저.
- **전용 ephemeral 스택**(권장): nightly e2e는 공유 dev 스택이 아닌 일회성 compose 스택에서 — 결정성·격리.
- **CI 워크플로 신설**: `.github/workflows/e2e-nightly.yml` (스택+DT 기동 → Tier 1/2/3-load/4-chaos/5-vuln/6-browser).

## CI 배치 매트릭스
| Tier | PR 게이트(빠름) | nightly | 릴리스 전 |
|------|----------------|---------|----------|
| 1 골든 | — | ✅ | ✅ |
| 2 실도구 | 버전 핀 단언 | ✅ | ✅ |
| 3 부하/감사 | async-blocking 감사 | 부하 SLO/스트레스 | 스트레스 |
| 4 상태/카오스 | 결정적 통합 | 카오스 | 카오스 |
| 5 적대적/vuln | 적대적 property | vuln 검출 | vuln 검출 |
| 6 이탈 | 백엔드 disconnect 단위 | Playwright/누수/이탈부하 | 전부 |

## 실행 순서 (ROI 순, PR 단위)
1. **PR-A** Tier 1 골든 베이스라인 (하네스 존재 → 최고 ROI) + 공유 하네스 승격.
2. **PR-B** Tier 3 async-blocking 정적 감사 (빠른 PR 게이트, 버그 클래스 통째 차단) + 부하 게이트 nightly 배선.
3. **PR-C** Tier 6 백엔드 disconnect 단위 + 누수 invariant 헬퍼.
4. **PR-D** Tier 4 상태/재스캔 통합 + 서버 카오스(breaker/OOM/disk).
5. **PR-E** Tier 5 적대적 property(PR) + 결정적 vuln 검출(nightly).
6. **PR-F** `e2e-nightly.yml` 워크플로 + ephemeral 스택 배선 (Tier 1/2/4-chaos/5-vuln 묶기).
7. **PR-G** Tier 6 Playwright 이탈 시나리오 + 이탈 부하.

## Definition of Done (티어별)
- 신규 코드 단위 커버리지 ≥80%, lint+typecheck green.
- 골든/실도구/vuln 티어는 nightly에서 결정적으로 green(연속 3회 flake 0).
- 부하 SLO 게이트 + async 감사 + 적대적 + 백엔드 disconnect는 PR 게이트로 상시 green.
- 각 발견 버그는 회귀 테스트로 고정 후 머지 (Producer-Reviewer: 보안/외부입력은 security-reviewer 통과).

## 확장 Tier H–O (서비스 안정화 — 운영 장애 축)

> 6-Tier가 happy-path + 발견된 버그 클래스를 덮은 뒤, *운영 장애를 일으키는* 축의
> 공백을 메운다. 동일 원칙: 결정적=PR 게이트, 느린/카오스=nightly, 비결정=불변식.

### Tier H — 거버넌스 집행 e2e (빌드 게이트)
게이트 *로직*은 unit/integration(`test_policy_gate*.py`)으로 커버됨. 공백은
**실 스캔→게이트 결과** 연결: forbidden 라이선스(mixed-policy의 GPL) / critical CVE를
가진 실 스캔이 `gate-result=fail` + 사유를 내는지. → 골든 캡처에 `gate` 축 추가
(scan→gate end-to-end). "금지 라이선스가 게이트를 통과" 회귀 차단.

### Tier I — 생성 산출물 유효성
SBOM을 외부 검증기(cyclonedx validate / SPDX)로 **스키마 검증**, NOTICE 법적 완전성
(모든 conditional/forbidden 라이선스 누락 없이 고지). 골든에 `valid` 축. nightly.

### Tier J — write/scan 경로 부하 + 동시성
스캔 트리거 폭주(팀 캡·레이트리밋·enqueue), 동일 프로젝트 동시 re-scan
(`_reset_scan_for_rerun` race + `uq_components_purl` dedup race), 혼합 read+write 풀
고갈, WS 다중 스트림. locust에 write 시나리오 추가. nightly.

### Tier K — 광범위 카오스 (불변식)
Redis 다운(broker+레이트리밋), Postgres 연결 끊김, worker OOM/kill 중 스캔, 디스크
가득, cdxgen/scancode 크래시·타임아웃. 불변식: 고아 0 / 스캔 상태 정확 / 5xx 0 /
데이터 무결 / breaker OPEN→캐시. nightly (스택 조작) + 결정적 단위(타임아웃/실패 경로).

### Tier L — authZ 매트릭스 + JWT 수명주기
모든 엔드포인트 × {anon, developer, team_admin, super_admin, cross-team} 권한 매트릭스
(parametrize). JWT 만료/refresh 회전+재사용 탐지/변조/alg-confusion. 레이트리밋 실제
429 검증. PR 게이트(통합).

### Tier M — 백업/복원 + 마이그레이션 정확성
백업→복원 후 데이터 동일성(프로젝트/스캔/findings), Alembic forward-only가 채워진
DB에 클린 적용, 데이터 마이그레이션 멱등성. nightly.

### Tier N — 계약/OpenAPI 드리프트
OpenAPI 스냅샷 diff(엔드포인트/스키마 변경), 프론트↔백엔드 계약(프론트 요청 파라미터가
백엔드 제약과 일치 — page-size 버그 클래스). PR 게이트(정적).

### Tier O — 상태머신 + 시간 의존
컴포넌트 승인 전이, VEX 7-state 전이 매트릭스, 새 CVE 재탐지(DT sync→갱신+알림), 고아
정리 beat, 알림(SMTP/Slack/Teams) 발송. 결정적 전이=PR, 비동기 파이프라인=nightly.

### H–O 빌드 순서 (안정화 ROI)
1. **H** 골든에 게이트 축 (실 스캔→게이트 집행).
2. **N** OpenAPI/계약 드리프트 (정적, PR, page-size류 차단).
3. **L** authZ 매트릭스 + JWT 수명주기 (PR).
4. **K** 카오스 (Redis/PG/worker/disk, nightly).
5. **I** SBOM/NOTICE 유효성, **M** 복원/마이그레이션, **J** write 부하, **O** 워크플로.

## 실행 관측 — e2e 스위트 단일-패스 제약 (2026-05-22 전체 실행)
전체 e2e(14 파일)를 실제 실행한 결과 실패 10건 중 **9건이 로그인 레이트리밋
(5/분/IP) 아티팩트**였다: 각 테스트가 per-test 로그인을 하는데 단일 드라이버 IP라
멀티-테스트 파일의 6번째 로그인부터 429 → `waitForURL(/projects)` 타임아웃. 나머지
1건은 scan_flow 빈-zip 테스트 결함(수정됨). **제품 버그 0건.**
→ **필수 follow-up (e2e를 CI 게이트로 만들려면 선결)**: Playwright `globalSetup`에서
**1회 로그인 → `storageState` 공유**로 전 스펙이 재사용(per-test 로그인 제거). 그래야
스위트가 단일 패스로 결정적으로 돌고 nightly 게이트가 된다. (보안 약화 없이; 레이트리밋
*동작*은 별도 Tier L에서 검증.) test-writer 핸드오프에도 동일 권고 존재.

## 알려진 선결/리스크
- **NVD 데이터 부재**(현재 fresh DT): Tier 5 vuln 검출은 결정적 vuln 데이터 시딩 전략 확정이 선결.
- **로컬 자원**(Colima 12GiB): nightly e2e는 ephemeral 스택 + 부하 레벨 조정 필요(10k는 CI 러너 한계).
- **flake 관리**: 비결정 영역은 불변식 단언으로, 시간/순서 의존은 폴링/재시도로 흡수(마스킹 금지).
