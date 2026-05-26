# ADR-0001 — Dependency-Track 제거 및 Trivy 단일 교체

- **Status**: Accepted (amended 2026-05-27)
- **Date**: 2026-05-27
- **Deciders**: Haksung Jang (maintainer)
- **Supersedes**: 이전 "DT Circuit Breaker / DT 캐시" 아키텍처 결정 (CLAUDE.md "핵심 규칙 4", post-ga-roadmap §3 "DT 종속 완화")
- **Related**: W6 마일스톤 (`docs/post-ga-execution-tracker.md` §0.5), 핸드오프 `docs/sessions/2026-05-27-w6-dt-removal-plan.md`

> **Amendment (2026-05-27, 같은 날 후속 결정)**: §"Decision" 본문의 **v2.3.1 동결 태그 prereq + 운영 레인 O1/O3 통합 + SECURITY.md backport 정책**은 **스킵**한다. Repo를 W6 진행 동안 private으로 전환하고, **v2.4.0이 DT-free 첫 공개 릴리스**가 된다. 외부 사용자 0이라 "Final Dependency-Track Release" 공개 마커는 불필요. Appendix A의 "prereq" 행과 §Decision의 "v2.3.1 동결 태그 설계" 절은 본 amendment로 무효화. W6 PR 시퀀스는 `#45 → #40 → #41 → shadow → #42 → #43a~e → #44`로 단축. 본문은 결정 사료로 보존.

---

## Context

### v2.0~v2.3 시점의 가정
TrustedOSS는 OWASP Dependency-Track(DT)을 docker-compose에 번들로 포함시키고, 우리 백엔드는 DT를 호출해 CVE 매칭 결과를 받아 PostgreSQL에 캐시하는 **2차 래퍼**로 설계했다. v2.1에선 "DT 종속 완화"를 마일스톤 테마로 잡았다 — DT를 유지하면서 EPSS 노출·VEX 소비 등으로 의존도를 낮추는 방향.

### 2026-05 누적된 운영 사고와 재평가
DT 운영을 1년 가까이 끌고 오면서 다음이 누적됐다.

| 항목 | 실측 |
|---|---|
| 메모리 풋프린트 | DT JVM 힙 4GB (전체 워크로드 6~7GB 중 절반) — Apple Silicon Colima 4CPU/8GB 한계 |
| H2 손상 사고 | 2026-05-27 `Chunk 113620 not found` → DT 부팅 불가, 수동 복구 |
| 부트스트랩 | 첫 기동 시 admin/admin → 패스워드 변경 → API Key 발급 수동 절차 |
| 백업 갭 | W5 트리아지에서 4개 항목 도출 (DT 볼륨 백업·복원·헬스 모니터·고아 정리) — 모두 DT 인프라가 사라지면 무효화 |

### 우리 코드의 실제 DT 활용도
인벤토리(2026-05-27 실측)에 따르면:

- **DT의 차별화 기능은 모두 우리가 자체 구현했다**
  - 정책 엔진: `services/policy_gate.py` (DT Evaluator 대체)
  - VEX 7-state: `vuln_finding_status` enum (DT 자체 VEX 미사용)
  - 컴포넌트 승인 워크플로우: `ComponentApproval` 모델 (DT에 없음)
  - 리스크 UI: 자체 React (DT UI 미사용)

- **DT의 킬러 기능 "새 NVD → 기존 SBOM 자동 재매칭"은 사용 안 함**
  - `tasks/dt_resync.py`는 NVD 카탈로그만 폴링하고, 기존 프로젝트 재분석 트리거 없음

- **실효 기능은 단일 호출 쌍 하나**
  - `PUT /api/v1/bom` → `GET /api/v1/finding/project/{uuid}` (CycloneDX → CVE 매칭)

즉 DT의 4GB JVM + H2 + 부트스트랩 + 백업 인프라를 통째로 떠안고 얻는 가치는 "CycloneDX → CVE 매칭" **한 줄**뿐이다.

### Trivy의 위치
Trivy는 이미 워커 이미지에 설치돼 있다 (`apps/backend/integrations/trivy.py`, 현재는 `trivy image`로 컨테이너 OS 패키지 스캔만 수행). Trivy의 `trivy sbom --format json` 명령은 CycloneDX SBOM을 직접 받아 NVD + OSV + GHSA + EPSS + KEV 통합 DB로 매칭한다. 결과는 우리 `vulnerability_findings` 컬럼과 1:1 매핑 가능하다. 풋프린트는 ~500MB로 DT의 1/8.

---

## Decision

**Dependency-Track을 통째로 제거하고 Trivy를 CVE 매칭 단일 엔진으로 교체한다.**

`v2.4.0`이 DT 없는 첫 릴리스다. `v2.3.1`을 "Final Dependency-Track Release"로 동결 태그하고 backport 정책으로 기존 사용자 경로를 보장한다.

### 범위
- **제거**: DT 컨테이너, `integrations/dt/`, `tasks/dt_*.py`, `api/v1/admin/dt.py`, DT 관련 환경변수(`DT_URL` · `DT_API_KEY` 등 8개), DT 관련 프론트엔드 화면, Helm 차트 DT 서브차트, `docker-compose.dt.yml`, `scripts/ci/provision-dt.sh`, DT 관련 Docusaurus 페이지.
- **교체**: `scan_source.py`의 `dt_findings` 스테이지를 Trivy 호출로 교체. **stage 이름은 유지** (`dt_upload`/`dt_findings`) — WS frame과 E2E 하네스가 의존. rename은 선택 PR `#43f` (v2.4.1)로 분리.
- **신규**: 자동 재매칭 Celery beat (`#42`) — DT로도 못 했던 기능. 시간대 분산 스케줄링.
- **유지**: `cdxgen`(SBOM 생성), 자체 정책 엔진, VEX 7-state, 컴포넌트 승인, 리스크 UI, `vulnerability_findings` 모델·인덱스.

### 비가역 안전장치 — Shadow 7일 게이트
`#41`(Trivy persist) 머지 후, `#43a`(BE DT 제거) 진행 전에 **shadow 운영 기간**을 둔다.
- DT와 Trivy를 양쪽 동시 실행, 결과를 admin 대시보드에 일일 일치율 노출.
- **통과 기준**: 7일 캘린더 운영 또는 다음 조기 종료 조건 충족 시.
  - **조기 종료**: ① 3일 연속 일치율 ≥95% **그리고** ② 누적 스캔 ≥30건 (생태계 ≥3종 포함). 두 조건 모두 충족해야 통과 처리.
  - 통계적 신뢰도 부족 시 7일 캘린더 완주.
- **미달 시**: Plan B(Trivy + OSV-Scanner 하이브리드, 머지/dedupe 부담 +0.5d) 또는 Plan C(DT 유지 + Trivy 보조 = W6 자체 일부 번복) 회의 트리거. 선호는 미정 — 미달 시점 실측 데이터(어떤 PURL/CVE가 누락됐는지)를 보고 결정.
- **비가역 지점**: `#43a` 머지 순간. 그 전엔 언제든 회귀 가능.

### v2.3.1 동결 태그 설계
- **태그명**: `v2.3.1` (SemVer 정식 — `v2.3.1-dt-final`은 pre-release 식별자 충돌로 기각).
- **별칭**: Docker `:v2.3-dt`, GitHub Release 제목 "Final Dependency-Track Release".
- **자산**: 멀티아치 이미지 3종(backend/worker/frontend) + Helm 차트 0.2.1 + Release body(ADR-0001 링크).
- **운영 레인 통합**: 이번 prereq에 운영 백로그 O1(첫 이미지 게시)과 O3(차트 ArtifactHub)을 흡수.
- **동결 브랜치 만들지 않음**: 외부 사용자 0, tag→branch는 사후 무료. `SECURITY.md` backport 정책 한 줄로 갈음.

---

## Consequences

### 긍정적
- **메모리 풋프린트 -50%**: 4GB JVM 제거 → Apple Silicon Colima dev 환경 안정성 큰 폭 상승, 운영 호스트 사양 요구 완화 (v2.0.1 §P2 "평가용 경량 프로파일" 자동 달성).
- **운영 사고 표면 제거**: H2 손상 위험 0, 부트스트랩 수동 절차 0, 백업 인프라(W5 4건) 무효화.
- **신규 기능 획득**: 자동 재매칭 Celery beat (`#42`) — DT가 우리 코드 기준으로 제공 못 하던 기능. NVD 갱신 시 기존 SBOM 자동 재스캔으로 새 CVE 즉시 반영.
- **데이터 소스 다양성 무료 획득**: Trivy는 NVD + OSV + GHSA + EPSS + KEV 통합. v2.1·v2.4 로드맵의 일부(EPSS/KEV)가 의존성 변경 없이 들어옴.
- **부트스트랩 마찰 제거**: DT API Key 발급 절차가 없어짐 → v2.0.1 §P0 "저마찰 설치" 강화.

### 부정적 / 비용
- **shadow 운영 7일 캘린더 비용**: 코드 변경 0 + 캘린더 7일 대기. 일정 vs 안전 트레이드오프에서 안전 선택 (사용자 확인 2026-05-27).
- **마이그레이션 작업량**: 코드 8d + 캘린더 7d ≈ 2~2.5주.
- **이전 사용자 경로 유지 부담**: v2.3.1 동결 태그 + SECURITY.md backport 정책 + Release body 안내 필요. 단 외부 사용자 0이라 사후 부담 작음.
- **사용자 문서 전면 교체**: Docusaurus 15페이지(EN/KO 동시) — `admin-guide/dt-connector.md` 삭제, `vulnerability-data.md` 신규, `release-notes/v2.4.0.md` 신규, air-gapped 매뉴얼 신설.

### 리스크
| 리스크 | 완화 |
|---|---|
| Trivy CVE 매칭이 DT보다 누락 多 | shadow 7일 게이트로 사전 감지. 평균 ≥95% 미달 시 Plan B/C로 회귀. |
| `vulnerability_findings` 컬럼 매핑 갭 | `#41`에서 `affected_component_name/version/license` 컬럼(W4 #191) 포함 1:1 매핑 검증, security-reviewer 통과 후 머지. |
| Trivy DB air-gapped 운영 | `#44`(필수 승격)에서 `--download-db-only` 부팅 + weekly refresh + `TRIVY_DB_REPOSITORY` 미러 + `#43c` 절차 문서. 사용자 확인 2026-05-27에서 air-gapped 있다고 가정. |
| stage 이름 `dt_*` 잔존이 코드 일관성 해침 | `#43f` (v2.4.1)에서 `sbom_upload`/`vuln_match`로 rename. 단기 부채로 의식적 수용. |
| Trivy JSON 파싱이 untrusted input | `#40`·`#41` adversarial parametrize 필수 (severity 비정상값·중첩 깊이·CRLF·NULL byte·oversized·javascript:/file: scheme). security-reviewer 통과 게이트. |
| 외부 사용자가 v2.3 라인 계속 사용 | v2.3.1 동결 태그 + `:v2.3-dt` Docker 별칭 + Release body 안내 + SECURITY.md backport 정책. |

### 데이터 보존
- DT 전용 테이블 없음. `vulnerability_findings`는 캐시였고 Trivy 재매칭으로 새로 채워짐 → **데이터 마이그레이션 불필요**.
- `audit_log`의 DT 액션 타입은 **역사 사실로 보존**. admin UI 필터는 deprecated 표기.
- `release-notes/v2.0.0.md`는 보존 (역사 사실 — v2.0 시점은 DT 포함).

---

## Alternatives Considered

### A. DT 유지 + 견고화 (W5 4건 처리)
- **내용**: W5 트리아지대로 DT 백업·복원·헬스·고아 정리 4건을 견고화 PR로 묶어 끝.
- **거부 이유**: DT의 실효 활용도(매칭 단일 호출)가 인프라 비용(4GB JVM + H2 + 부트스트랩 + 백업)을 정당화 못 함. 견고화로 사고 빈도는 줄지만 사양·복잡도는 그대로.

### B. Grype로 교체
- **거부 이유**: Trivy 대비 명확한 이점 없음. DB는 유사(NVD + GHSA + OSV), Aqua의 운영 규모가 더 큼. 우리 워커 이미지에 Trivy 이미 설치돼 있어 신규 의존 0.

### C. OSV-Scanner 단독으로 교체
- **거부 이유**: EPSS·KEV 부재. v2.1·v2.4 로드맵에서 EPSS 1급화·KEV 통합이 핵심이라 별도 통합 작업 필요.

### D. 자체 매칭 레이어 (NVD/OSV 직접 미러)
- **거부 이유**: 작업량 2~3주 (Trivy 교체와 동급), NVD/OSV 미러 운영 부담을 우리가 떠안음. 이득 없음.

### E. 하이브리드 (Trivy + OSV-Scanner)
- **거부 이유**: recall +3~5%p 예상되나 merge/dedupe 부담 +0.5d, dual-source 복잡도. **Plan B로 보류** — shadow 일치율이 ≥95% 미달일 때만 도입 검토.

### F. cdxgen도 Trivy fs로 통합 (SBOM 생성기 단일화)
- **거부 이유 (cdxgen 유지 확정)**: Trivy fs 통합 시 다음 손실이 발생.
  - `dependsOn` graph 손실 (W3 의존성 트리 UI 의존)
  - `scope`/usage 정보 손실 (W4 component scope 필드 의존)
  - evidence 깊이 손실 (W3 컴포넌트 출처 추적 의존)
  - W4-D npm lockfile fallback 자산 폐기 (PR #190 무효화)
  - 5~6개 기능 동시 retrofit 필요
- **결론**: **DT 교체 ↔ cdxgen 유지** 조합이 최선. Syft 교체도 검토했으나 cdxgen 우위 (CycloneDX 1.6 evidence 깊이·생태계 커버리지).

---

## Appendix A — 8 PR + 선택 1 분해

| # | PR | 작업 | 코드 추정 |
|---|---|---|---|
| prereq | tag + ADR + 문서 | v2.3.1 태그/Release + 본 ADR + CLAUDE.md/post-ga-roadmap 동기화 | 0.5d + 0.5d |
| #40 | Trivy 어댑터 | `integrations/trivy.py`에 `run_trivy_sbom` 추가 | 1d |
| #41 | persist + 벤치 | Trivy 결과 → `vulnerability_findings` persist (stage 이름 유지), 벤치 코호트 5~10개 OSS repo, security-reviewer | 1.5d |
| — | **shadow 7d** | DT/Trivy 양쪽 실행 + 일일 일치율 admin 대시보드 | 캘린더 7d (코드 0) |
| #42 | 자동 재매칭 beat | Celery beat 시간대 분산 | 1d |
| #43a | BE DT 제거 (비가역) | `integrations/dt/` + `tasks/dt_*.py` + `api/v1/admin/dt.py` + `core/config.py:434-465` + 테스트 8개. security-reviewer | 0.5d |
| #43b | FE DT 제거 | `features/admin/dt/` + `router.tsx` + `AppShell.tsx` | 0.5d |
| #43c | 사용자 문서 EN/KO | Docusaurus 15페이지 — `vulnerability-data.md` 신규 + air-gapped 매뉴얼 + `dt-connector.md` 삭제 + `release-notes/v2.4.0.md` 신규 | 1d |
| #43d | 배포·Helm·upgrade.sh | `upgrade.sh` v2.3→v2.4 절(큐 drain + DT 컨테이너/볼륨 정리 + 1-click 전체 재매칭) + `docker-compose.dt.yml` 삭제 + Helm 차트 0.3.0 | 0.5d |
| #43e | admin/health Trivy 패널 | DT 패널 자리에 Trivy DB 상태 노출 | 0.5d |
| #44 | Trivy DB 라이프사이클 | 부팅 시 `--download-db-only` + weekly refresh + `TRIVY_DB_REPOSITORY` air-gapped 미러 | 0.5d |
| #43f | (선택, v2.4.1) | stage rename `dt_upload`/`dt_findings` → `sbom_upload`/`vuln_match` | — |
| **합계** | | | **코드 8d + 캘린더 7d ≈ 2~2.5주** |

상세 작업·검증·DoD는 `docs/post-ga-execution-tracker.md` §0.5 W6 절을 SoT로 본다.

## Appendix B — 인벤토리 (제거 대상, 2026-05-27 실측)

| 영역 | 파일/경로 |
|---|---|
| Backend integrations | `apps/backend/integrations/dt/` 4파일 |
| Backend tasks | `apps/backend/tasks/dt_*.py` 4파일 |
| Backend API | `apps/backend/api/v1/admin/dt.py` |
| Backend config | `apps/backend/core/config.py:434-465` (8 getter) |
| Backend Celery | `apps/backend/tasks/celery_app.py:39-81` (import + beat) |
| Frontend | `apps/frontend/src/features/admin/dt/` 3파일 · `router.tsx` · `AppShell.tsx` |
| 인프라 | `docker-compose.yml`(코멘트 6곳) · `docker-compose.dt.yml` · `scripts/install.sh:400` · `scripts/ci/provision-dt.sh` · `charts/trustedoss/` 6파일 |
| 문서 | Docusaurus 15페이지 + `CLAUDE.md` 11곳(본 PR에서 처리) + `docs/post-ga-roadmap.md` 14곳(본 PR에서 처리) |
| 테스트 | 8개 파일 |

## Appendix C — Backport 정책 요약 (SECURITY.md 신규 절로 반영)

```
## Backport policy

- v2.3.1 is the final Dependency-Track-based release.
- Critical-severity security fixes for v2.3.x will be backported to a
  `v2.3` line for 6 months after v2.4.0 GA, on a best-effort basis.
- Non-security backports are not provided. Migration to v2.4.0+ is
  the supported upgrade path (see docs/release-notes/v2.4.0.md).
```
