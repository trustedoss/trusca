# ADR-0002 — W6 DT vs Trivy 매칭 벤치 코호트 선정

- **Status**: Accepted (amended 2026-05-27 — gate 폐기, 정보용 측정으로 격하)
- **Date**: 2026-05-27
- **Deciders**: Haksung Jang (maintainer)
- **Related**: [ADR-0001](./0001-replace-dt-with-trivy.md) (DT 제거 + Trivy 단일 교체, Amendment 2 = shadow gate 스킵), W6 트래커 `docs/post-ga-execution-tracker.md` §0.5 W6-#41
- **SoT 책임**: 본 문서가 cohort 선정의 단일 진실. W6-#41의 `scripts/benchmark_dt_vs_trivy.py` 는 본 표를 코드/JSON 으로 옮긴 사본일 뿐 — 변경 의도가 있다면 본 문서를 먼저 갱신하고 PR에 함께 포함.

> **Amendment (2026-05-27, 같은 날 후속 결정)**: 본 ADR의 §"게이트 매핑"과 §"측정 방법론" 5(Jaccard)는 **폐기**. 사유는 [ADR-0001 Amendment 2](./0001-replace-dt-with-trivy.md) 참조 — 요약: (1) Jaccard metric이 잘못된 측정(Trivy가 DT보다 더 찾으면 좋은 일인데 fail 판정), (2) DT가 절대 기준 아님(Black Duck 대비 70~80% recall), (3) 회복 비용 0(외부 사용자 0 + repo private). cohort 선정 자체는 그대로 유효 — **정보용 측정**으로 격하해 "DT vs Trivy 차이의 종류 파악 → 미래 개선 백로그 인풋"을 목적으로 함. W6-#41은 1회 측정 후 보고서만 산출하고 게이트 없이 #43a로 진행.

---

## Context

W6-#41(Trivy persist + 벤치 코호트) 머지 후 **shadow 7일 게이트**가 시작된다. 게이트 기준:
- 7일 캘린더 운영 또는 **조기 종료**: 3일 연속 일치율 ≥95% 그리고 누적 스캔 ≥30건 (생태계 ≥3종) 모두 충족 (ADR-0001 §"Shadow 7일 게이트").
- 일치율은 DT와 Trivy가 동일 SBOM에 대해 매칭한 `(cve_id, component_purl)` 페어의 set-diff 기준.

벤치 코호트가 측정의 분모를 정한다. 코호트가 편향되면 일치율 숫자도 편향된다. 따라서 코호트 자체를 ADR로 고정해 "왜 이 repo들로 측정했나"가 시점이 지나도 추적 가능하게 한다.

사용자 확인 (2026-05-27): "OSS 인기 repo 자체 구성" — 사용자가 직접 지정하는 대신 AI가 다양성 기준으로 선정.

---

## Decision

**5개 OSS repo + 1개 stress-test repo (6개 총)** 로 cohort 구성. 매칭 일치율 측정 시 5개 코어 repo는 필수, stress-test는 별도 라인으로 보고.

### Selection 기준

| 기준 | 임계 | 이유 |
|---|---|---|
| **언어/생태계 다양성** | npm / Python / Go / Java / Rust 각 1개 이상 | Trivy의 per-ecosystem matcher (pypi/npm/go/maven/cargo)를 모두 exercise. cdxgen이 SBOM 생성 시 어느 ecosystem 라벨을 다는지에 따라 Trivy의 lookup 경로가 달라짐. |
| **알려진 CVE 노출 이력** | dep 중 최소 1건 확인된 CVE 보유 | 빈 결과 비교가 아니라 실제 매칭 동등성을 측정해야 함. 0 vs 0은 일치율 100%로 잡혀 가짜 통과 위험. |
| **transitive 깊이** | < 500 transitive deps | cdxgen + DT/Trivy 양쪽 30분 이내 종료. 코호트 1회 풀스윙이 2~3시간 안에 끝나야 7일 게이트 안에 데이터가 쌓임. |
| **활성 유지 보수** | last commit < 6개월 | stale repo는 ecosystem 변경 (PURL 명세 갱신·새 vuln 데이터 추가) 미반영. |
| **라이선스** | MIT/Apache-2.0/BSD | 매칭 외 license-카테고리 edge case 비용 줄임. |
| **lockfile 존재** | 있음 (npm/Python/Go/Cargo lock) | cdxgen이 정확한 dep graph 산출. lockfile-less 케이스는 [[feedback_dont_bias_recommendations_to_less_work]] 의 W4-D 갭이라 W6-#41 측정 신뢰도 분리. |

### Cohort

| # | 생태계 | Repo | 선정 근거 | 예상 dep 수 | 예상 CVE 수 |
|---|---|---|---|---|---|
| 1 | npm | `expressjs/express` | npm 가장 인기 웹 프레임워크. 깊은 transitive 트리(qs·body-parser·debug 등). 역사적 CVE 다수 (CVE-2024-29041 등). lockfile 있음. | ~50 direct, ~200 transitive | 10~30 |
| 2 | Python (pip) | `pallets/flask` | Python 웹 프레임워크 1순위. Werkzeug/Jinja2 의존성 체인. CVE 노출 이력 풍부 (Werkzeug CVE-2023-46136 등). requirements.txt 명시. | ~10 direct, ~20 transitive | 5~15 |
| 3 | Go | `gin-gonic/gin` | Go 가장 인기 웹 프레임워크. go.mod 깔끔. yaml.v2 등 알려진 CVE 보유. | ~15 direct, ~30 transitive | 3~8 |
| 4 | Java (Maven) | `apache/commons-text` | Apache Commons 라이브러리. CVE-2022-42889 (text4shell) 유명. Maven pom 단순해 cdxgen 안정. | ~5 direct, ~10 transitive | 1~5 |
| 5 | Rust (Cargo) | `serde-rs/json` | Rust JSON 표준. Cargo.toml/Cargo.lock 명확. 의존성 적어 baseline 역할. CVE 0이라도 5개 cohort 중 baseline-low 역할로 의도적 포함. | ~5 direct, ~10 transitive | 0~3 |
| 6 (stress) | Multi-lang (Go-primary) | `hashicorp/terraform` | Go 거대 모노레포. 깊은 transitive 트리(800+). cdxgen + Trivy 양쪽 30분 가까이 소요 예상. 일치율 측정의 stress-test. 5개 코어와 별도 라인으로 보고. | ~50 direct, ~800 transitive | 30~80 |

### 측정 방법론

1. **per-repo 스냅샷 고정** — 측정 시점 commit SHA를 본 ADR에 추가 (W6-#41 PR에서 실측 후 본 ADR 갱신, "Measurement snapshot" 절 신설).
2. **SBOM 생성은 단일 cdxgen 실행** — DT와 Trivy 양쪽에 **동일 SBOM** 투입. cdxgen 두 번 돌려서 SBOM이 미세하게 달라지면 측정이 무의미해짐.
3. **결과 정규화** — 양쪽 결과를 `(cve_id, component_purl)` 쌍 set으로 환원. Trivy는 `VulnerabilityID` + `PkgName`@`InstalledVersion`을 PURL로 변환. DT는 `vulnerability.vulnId` + `component.purl` 직접.
4. **CVE alias 정규화** — Trivy/DT가 GHSA-xxxx vs CVE-yyyy로 다르게 라벨링하는 경우 같은 vuln으로 처리 (Trivy의 `VulnerabilityID` aliases 필드 활용). 라벨 차이가 일치율 거짓 하락의 주범.
5. **FP/FN 분류 보고서** — 차이의 종류를 명시:
   - **DT only**: Trivy DB에 없는 vuln (out-of-date DB or 누락)
   - **Trivy only**: DT가 못 잡은 vuln (Trivy 통합 DB 우위)
   - **버전 범위 불일치**: 한 쪽이 fixed_version 다르게 봐서 매칭 결과 차이
6. **일치율 = `|intersect| / |union|`** (Jaccard). 단순 recall이 아니라 양방향 동등성 측정.

### 게이트 매핑

- **5개 코어 repo 평균 일치율 ≥95%** → shadow 통과 (조기 종료 조건과 정합).
- **stress-test repo (terraform)** — 정보용. 일치율 ≥90%면 만족, 미달은 별도 분석 (대규모 Go 모노레포의 trivy 대응 갭).

---

## Consequences

### 긍정적
- cohort 자체가 ADR로 고정 → 측정 결과의 재현성·감사성 확보.
- 5개 생태계 cover → 우리 사용자가 마주칠 typical SCA 시나리오 대부분 포함.
- stress-test 분리 → 코어 게이트가 단일 비대 repo에 휘둘리지 않음.

### 부정적 / 비용
- 6개 repo 측정 1회 풀스윙 = 약 2~3시간 (terraform이 30분 차지). shadow 7일 안에 일일 1회씩 측정해도 부담 적음.
- 코호트가 web 프레임워크에 치우침 (5개 중 3개) — CLI tool / DB 클라이언트 / ML 라이브러리는 미포함. 1차 측정 후 갭 발견 시 본 ADR 갱신.

### 리스크
| 리스크 | 완화 |
|---|---|
| cohort repo가 측정 기간에 새 release/CVE를 받아 데이터 휘청거림 | commit SHA 고정 (위 §"측정 방법론" 1) |
| Trivy DB가 오래되어 모든 일치율 일률 하락 | 측정 시작 시 `trivy --download-db-only` 강제 + DB version 보고서에 명시 |
| cdxgen SBOM이 양쪽에 같은 입력으로 들어가지 않음 (race) | 단일 SBOM 파일 → 양쪽 호출 (위 §"측정 방법론" 2) |
| GHSA vs CVE alias 차이로 false-negative 일치 | aliases 정규화 (위 §"측정 방법론" 4). 미정규화 시 일치율 거짓 하락. |

---

## Alternatives Considered

### A. 사용자 사내 repo 사용
- 거부 이유: 외부 사용자 0 + 사용자가 자체 구성 위임. 사내 repo 공개 불가라 ADR 작성도 불가.

### B. CVE 노출이 풍부한 deliberately-vulnerable repo (OWASP Juice Shop 등)
- 거부 이유: 매칭 비교가 아니라 vuln detection 자체를 검증하게 됨. 우리는 **DT vs Trivy 일치율**이 목표지 vuln 탐지율이 목표 아님.

### C. 매우 작은 cohort (1~2개)
- 거부 이유: 생태계 편향, 측정의 통계적 신뢰도 부족.

### D. 매우 큰 cohort (20~30개)
- 거부 이유: 측정 1회 풀스윙이 8시간+ → shadow 7일 안에 데이터 못 쌓음. 측정 빈도 부족이 통계 신뢰도 하락보다 큼.

---

## Open items (W6-#41 PR에서 채울 것)

- [ ] 각 repo의 측정 시점 commit SHA 추가
- [ ] cohort.json (스크립트 친화 포맷) — repo URL · 생태계 라벨 · commit SHA · 예상 dep 수
- [ ] 측정 일 1회 cron 또는 manual trigger 결정
- [ ] FP/FN 분류 보고서 출력 포맷 (admin 대시보드 표시 vs CLI artifact)

이 항목들은 W6-#41 PR에서 본 ADR을 update commit으로 갱신.
