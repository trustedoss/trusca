---
id: data-sources
title: 취약점 데이터 출처
description: Trivy DB의 출처별 커버리지 매트릭스 — NVD, OSV, GHSA, EPSS, KEV — 갱신 주기, 생태계 커버리지, 분석 유형.
sidebar_label: 데이터 출처
sidebar_position: 5
---

# 취약점 데이터 출처

TrustedOSS Portal은 5개 공개 취약점 피드를 컴파일한 번들인 **Trivy DB**로 SBOM을 CVE에 대조합니다. 본 페이지는 각 출처가 **무엇을 기여**하는지, **언제 갱신**되는지, **어떤 생태계를 커버**하는지의 reference입니다.

:::note 대상 독자
"이 CVE는 어디서 오는가?", "왜 이 생태계는 빠졌나?"에 답해야 하는 보안 리드·감사인·운영자. 다운로드·갱신·air-gapped 미러 같은 운영자 라이프사이클은 [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md)를 참조하세요.
:::

## 출처 매트릭스

| 출처 | 주체 | 업스트림 갱신 | 기여 항목 |
|---|---|---|---|
| **NVD** — National Vulnerability Database | NIST | 약 6시간 | CVE ID, CVSS v3 벡터, CPE 매칭, 참조. CVE 백본. |
| **OSV** — Open Source Vulnerabilities | Google | 연속 | 생태계별 어드바이저리 + 정밀 버전 범위(`introduced` / `fixed` / `last_affected`). |
| **GHSA** — GitHub Security Advisories | GitHub | 연속 | 어드바이저리 메타데이터, 수정 버전, 철회 상태. npm / pip / Maven은 GHSA가 먼저인 경우가 많음. |
| **EPSS** — Exploit Prediction Scoring System | FIRST | 일간 | 30일 익스플로잇 확률 `0.0–1.0` + 백분위. 선택적 EPSS 게이트 구동. |
| **KEV** — Known Exploited Vulnerabilities | CISA | 발표 시 | 해당 CVE의 실환경 익스플로잇 확인 여부 boolean. 최우선 트리아지 신호. |

다섯 출처 모두 동일한 Trivy DB 번들에 함께 들어 있습니다 — 포털은 스캔 시점에 이 API들을 **호출하지 않습니다**. 스캔별 매칭은 워커의 `/var/lib/trivy/db/`에서 읽으며, [Trivy DB refresh 태스크](../admin-guide/vulnerability-data.md)가 이를 최신으로 유지합니다.

## 포털에서의 갱신 주기

| 레이어 | 주기 | 노브 |
|---|---|---|
| 업스트림 Trivy DB 재빌드 | 약 6시간 (Aqua가 새 OCI 태그 게시) | — |
| 포털 워커가 새 DB pull | 주간 | `TRIVY_DB_REFRESH_HOURS` (기본 `168`) |
| 로컬 DB에 대한 스캔별 매칭 | 스캔당 (네트워크 없음) | — |
| 기존 SBOM 자동 재매칭 | DB 성공 갱신 후 매번 | Celery beat 태스크 `tasks.rematch.run_rematch` |

**자동 재매칭 beat**(roadmap)은 DT가 우리 배포에서 제공하지 못했던 킬러 기능입니다 — 새 CVE가 갱신된 DB에 들어오면, beat 태스크가 모든 프로젝트의 최신 SBOM을 순회해 매칭되는 `vulnerability_findings` 행을 새로 씁니다. 사용자는 재스캔 없이 Vulnerabilities 탭에서 새 발견을 확인합니다.

재탐지의 사용자 뷰(배너·알림 트리거)는 [재탐지](../user-guide/vulnerabilities.md#재탐지)를 참조하세요.

## 생태계 커버리지

Trivy DB는 컴포넌트를 **package URL**(`purl`)로 매칭합니다. 아래 생태계는 커버리지가 조밀합니다 — 각각 전용 OSV 스트림 + GHSA 기여가 있습니다.

| 생태계 | `purl` 타입 | 주 피드 | 비고 |
|---|---|---|---|
| npm (JavaScript / Node) | `pkg:npm/*` | OSV + GHSA + NVD | 1급 — 대부분 CVE는 GHSA에 먼저. |
| PyPI (Python) | `pkg:pypi/*` | OSV + GHSA + NVD | 1급. |
| Maven (Java / Kotlin) | `pkg:maven/*` | OSV + GHSA + NVD | 1급. v0.10.0(roadmap)부터 classifier 인식. |
| Go 모듈 | `pkg:golang/*` | OSV + GHSA + NVD | 1급. 취약점 DB는 `vuln.go.dev`. |
| RubyGems | `pkg:gem/*` | OSV + GHSA | 1급. |
| crates.io (Rust) | `pkg:cargo/*` | OSV + GHSA | 1급. |
| Packagist (PHP) | `pkg:composer/*` | OSV + GHSA | 1급. |
| NuGet (.NET) | `pkg:nuget/*` | OSV + GHSA + NVD | 1급. |
| Hex (Elixir / Erlang) | `pkg:hex/*` | OSV | 견고. |
| Pub (Dart / Flutter) | `pkg:pub/*` | OSV | 견고. |
| Conan (C / C++) | `pkg:conan/*` | OSV | 위보다 희소 — C/C++ CVE는 OS 패키지 CVE인 경우가 많음(아래). |
| OS 패키지 (Alpine, Debian, RHEL 등) | `pkg:apk/*`, `pkg:deb/*`, `pkg:rpm/*` | NVD + 배포판별 보안 어드바이저리 | **컨테이너 스캔** 파이프라인(`scan_container.py`)이 사용. C/C++ 코드 레포의 소스 스캔에선 생성되지 않음. |

컴포넌트의 `purl`이 피드 엔트리와 일치하지 않으면 finding이 만들어지지 않습니다 — 설계상 조용히. 흔한 두 원인:

- **비정규 `purl`.** `cdxgen`은 보수적입니다 — 잘못된 `package.json`은 정규화되지 않는 `purl`을 낼 수 있습니다. 스캔 ID와 함께 이슈로 보고하세요. 생성기는 점진 강화.
- **OSV / GHSA에 아직 없는 생태계.** 커버리지는 매월 늘어납니다. 업스트림 진실은 [OSV 생태계 목록](https://ossf.github.io/osv-schema/).

## 분석 유형

Dependency-Track 급 도구와 비교해 Trivy DB는 finding별로 다음 신호를 노출합니다. 포털은 Vulnerabilities 탭과 API로 모두 노출합니다.

| 신호 | 출처 | 포털 위치 |
|---|---|---|
| **CVE ID** | NVD / OSV / GHSA | 행 식별자, 헤더 chip. |
| **Severity** (`critical` / `high` / `medium` / `low`) | NVD CVSS v3 (우선) → GHSA → OSV | 행 배지, 분포 카드, severity 필터. |
| **CVSS v3 벡터** | NVD | Finding drawer → Summary. |
| **설명 / 제목** | NVD / GHSA | Finding drawer → Summary. |
| **CWE** | NVD | Finding drawer → Summary. |
| **수정 버전** | GHSA → OSV (`fixed` 범위) | Finding drawer → Affected component → "Fixed in". |
| **영향 버전 범위** | OSV (`introduced` / `last_affected` / `fixed`) | 매처가 사용; 직접 노출은 없음. |
| **EPSS 점수 / 백분위** | EPSS | Finding drawer → Summary; 정렬 가능 컬럼; `GATE_EPSS_THRESHOLD` 게이트. |
| **KEV (실환경)** | KEV 카탈로그 | Finding 행 배지; 게이트 구동 가능 (post-GA 로드맵). |
| **References** | NVD / GHSA / OSV (URL 중복 제거) | Finding drawer → References. |

포털이 **소비하지 않는** 것:

- 상용 피드의 큐레이션된 취약점 리서치(Black Duck KnowledgeBase, Snyk DB) — 설계상 공개 피드만 사용.
- NVD로 흘러드는 것 외 벤더 어드바이저리(Oracle CPU, Microsoft MSRC). 향후 릴리스에서 추가 Trivy data source로 붙일 수 있음.
- Reachability 분석. 포털은 콜 그래프를 파싱하지 않으며, 매칭된 CVE는 모두 "영향 가능"으로 표시.

## 트리아지에의 의미

위 매트릭스는 모든 분석가가 **자동으로** 받는 입력입니다. 다음 두 관행이 신호 대 잡음을 높입니다.

1. **CVSS 위에 EPSS를 겹쳐 보세요.** EPSS 백분위 `> 95`인 `medium`은 EPSS `< 5`인 `critical`보다 먼저 봐야 합니다. 컬럼 정렬 또는 `GATE_EPSS_THRESHOLD` env로 게이트.
2. **KEV로 필터링하세요.** Vulnerabilities 탭은 KEV 필터를 제공합니다 — CISA 카탈로그에 있는 것은 실환경 익스플로잇이 확인된 것이며, severity 단독 순위보다 먼저 패치해야 합니다.

사용자 흐름(drawer, VEX 상태 머신, suppression)은 [Vulnerabilities](../user-guide/vulnerabilities.md)를 참조하세요.

## 참고

- [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) — 운영자 라이프사이클, air-gapped 미러, 트러블슈팅.
- [Vulnerabilities](../user-guide/vulnerabilities.md) — 분석가 흐름.
- [용어집](./glossary.md#취약점) — CVE, CWE, NVD, EPSS, KEV 정의.
- [ADR-0001 — Dependency-Track 제거](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/decisions/0001-replace-dt-with-trivy.md) — Trivy가 v0.10.0부터 단일 엔진인 이유.
