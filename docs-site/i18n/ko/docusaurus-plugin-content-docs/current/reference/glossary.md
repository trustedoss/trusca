---
id: glossary
title: 용어집
description: TrustedOSS Portal 도메인 용어 — SCA, SBOM, VEX, 라이선스 단계, RBAC 역할, CycloneDX / SPDX 매핑.
sidebar_label: 용어집
sidebar_position: 4
---

# 용어집

본 사이트 전반에서 사용하는 도메인 용어의 단일 진실 문서입니다.
각 항목은 **풀네임**, **약어**(사용 시), 관련 명세나 상위 프로젝트로
연결되는 **표준 참조 링크**를 함께 제공합니다.

:::note 대상 독자
본 사이트의 나머지 페이지를 읽는 모든 사람. 첫 방문 시 한 번 훑어
보고, 사용자·관리자·기여자 가이드를 읽는 동안 탭에 열어 두세요.
:::

## SCA 핵심

- **SCA — Software Composition Analysis.** 소프트웨어 프로젝트에서
  제3자(오픈소스) 컴포넌트를 탐지하고, 라이선스를 분류하며, 알려진
  취약점을 식별하는 분야. TrustedOSS Portal은 SCA 도구입니다.
- **SBOM — Software Bill of Materials.** 소프트웨어에 포함된 모든
  컴포넌트(버전·라이선스·공급자 포함)의 기계 가독 명세. TrustedOSS
  Portal은 CycloneDX(JSON·XML)와 SPDX(JSON·Tag-Value) 형식으로
  SBOM을 내보냅니다.
  [CISA SBOM 자료](https://www.cisa.gov/sbom) 참고.
- **CycloneDX.** OWASP가 관리하는 SBOM 명세. TrustedOSS는 1.6
  버전(JSON·XML)을 사용합니다.
  [cyclonedx.org/specification](https://cyclonedx.org/specification/)
  참고.
- **SPDX — Software Package Data Exchange.** Linux Foundation이
  관리하는 SBOM 명세. TrustedOSS는 2.3 버전(JSON·Tag-Value)을
  사용합니다. [spdx.dev](https://spdx.dev/) 참고.

## 취약점

- **CVE — Common Vulnerabilities and Exposures.** 공개된 보안 결함의
  업계 표준 식별자. `CVE-YYYY-NNNN` 형식. MITRE가 관리합니다.
  [cve.org](https://www.cve.org/) 참고.
- **CWE — Common Weakness Enumeration.** 소프트웨어 약점 유형 분류
  체계(예: CWE-79 크로스 사이트 스크립팅). 각 CVE는 하나 이상의 CWE
  항목을 참조합니다.
- **NVD — National Vulnerability Database.** CVE 위에 NIST가 분석
  레이어를 얹은 것 — CVSS 점수, CPE 매칭, 참조 링크를 추가합니다.
  [nvd.nist.gov](https://nvd.nist.gov/) 참고.
- **CVSS — Common Vulnerability Scoring System.** CVE의 이론적
  **심각도**(영향)를 나타내는 0–10 점수. 실제 악용 여부는 말하지
  않습니다.
- **EPSS — Exploit Prediction Scoring System.** CVE가 향후 30일 내
  **실제 악용**될 0–1 확률. EPSS는 CVSS를 보완합니다 — CVSS는
  심각도, EPSS는 가능성. TrustedOSS는 score를 백분율로, percentile을
  "상위 N%"로 표시하며 `GATE_EPSS_THRESHOLD`로 빌드 게이트를 구동할 수
  있습니다. EPSS는 Trivy DB에서 옵니다 — Trivy가 채점하지 않는 CVE에는
  없습니다. [first.org/epss](https://www.first.org/epss/)와
  [EPSS 사용자 가이드](../user-guide/vulnerabilities.md#epss--악용-확률)
  참고.
- **OSV — Open Source Vulnerabilities database.** Google이 주도하는
  생태계별(npm, PyPI, Maven 등) 취약점 데이터베이스.
  [osv.dev](https://osv.dev/) 참고.
- **GHSA — GitHub Security Advisory.** GitHub의 생태계별 권고문
  피드. CVE ID는 종종 GHSA를 통해 발급됩니다.
- **VEX — Vulnerability Exploitability eXchange.** 알려진 취약점이
  특정 제품에 실제로 영향을 미치는지 단언하는 문서 형식. CycloneDX의
  `analysis.state`와 SPDX VEX 두 가지가 주된 인코딩입니다. TrustedOSS는
  CycloneDX 7-state 모델을 구현합니다 — `new`, `analyzing`,
  `exploitable`, `not_affected`, `false_positive`, `suppressed`,
  `fixed`. [CycloneDX VEX](https://cyclonedx.org/capabilities/vex/)
  참고.

### VEX 7-state — 상태별 액션 버튼

취약점 드로어의 Analysis 섹션은 현재 상태에 따라 최대 7개의 액션
버튼을 노출합니다. 매핑은 다음과 같습니다.

| 현재 상태 | 가능한 액션 (버튼 라벨) |
|---|---|
| `new` | Move to analyzing, Mark exploitable, Mark not affected, Mark false positive, Suppress, Mark fixed |
| `analyzing` | Mark exploitable, Mark not affected, Mark false positive, Suppress, Mark fixed |
| `exploitable` | Mark not affected, Mark false positive, Mark fixed |
| `not_affected` | Reopen as new, Mark exploitable, Mark fixed |
| `false_positive` | Reopen as new, Mark exploitable |
| `suppressed` | Reopen as new |
| `fixed` | Reopen as new |

각 버튼은 `vulnerability_findings.update` 행을 `audit_logs`에
기록하며, `diff` 컬럼에 `previous_status` → `new_status` 전환이
담깁니다.

## 도구

- **scancode — scancode-toolkit.** 프로젝트의 **first-party** 소스
  파일을 직접 읽어 *detected* SPDX 라이선스를 발신하는 라이선스
  스캐너로, 각 항목에 라이선스가 발견된 파일의 `source_path` 를
  태깅합니다. TrustedOSS는 소스 스캔의 두 번째 단계로 scancode 를
  실행합니다(v2.0.0 에서 OSS Review Toolkit, ORT 를 대체). 서드파티
  의존성 소스는 스캔하지 않으며 — 그 라이선스는 *declared*(cdxgen
  에서)로 유지됩니다.
  [github.com/aboutcode-org/scancode-toolkit](https://github.com/aboutcode-org/scancode-toolkit) 참고.
- **cdxgen — CycloneDX Generator.** 30개 이상의 언어 / 빌드 시스템
  매니페스트(`package.json`, `pom.xml`, `requirements.txt`, …)로부터
  CycloneDX SBOM을 생성하는 컴포넌트 탐지기. scancode 이전 첫 번째
  스캔 단계로 실행됩니다.
- **Trivy.** Aqua Security가 만든 컨테이너 및 OS 패키지 취약점
  스캐너. TrustedOSS는 컨테이너 스캔 파이프라인에 Trivy를 사용합니다
  (cdxgen + scancode 소스 스캔 경로와는 분리).
- **Trivy DB.** Aqua Security가 `ghcr.io/aquasecurity/trivy-db`에
  게시하는 NVD + OSV + GHSA + EPSS + KEV 통합 번들. TrustedOSS는 워커
  부팅 시 1회 다운로드하고 주간 갱신합니다(`TRIVY_DB_REPOSITORY`,
  `TRIVY_DB_REFRESH_HOURS`). [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md)와
  [데이터 출처](./data-sources.md) 참고.
- **DT — Dependency-Track.** Apache-2.0 취약점 인텔리전스 플랫폼.
  TrustedOSS는 v2.3까지 DT를 취약점 엔진으로 사용했고 v2.4.0에서 Trivy로
  교체했습니다 —
  [ADR-0001](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/decisions/0001-replace-dt-with-trivy.md)과
  [비교](../comparison.md#dependency-track과-비교) 참고. 본 용어집에 여전히
  남아 있는 이유는 레거시 audit log 행과 비교 페이지가 DT를 참조하기 때문입니다.
- **cosign.** Sigstore의 서명 CLI. TrustedOSS는 모든 소스 스캔의
  CycloneDX SBOM을 cosign(`cosign sign-blob`)으로 서명하여 소비자가
  `cosign verify-blob`으로 검증할 수 있게 합니다. 자체 호스팅에서는
  key-based 서명이 기본값이고 keyless(OIDC)는 옵트인입니다.
  [SBOM 서명 검증](./sbom-signature-verification.md)과
  [docs.sigstore.dev/cosign](https://docs.sigstore.dev/cosign/overview/) 참고.
- **Sigstore / Fulcio / Rekor.** cosign이 keyless 서명에 사용하는
  생태계: **Fulcio**는 OIDC 신원에 바인딩된 단기 서명 인증서를
  발급하고, **Rekor**는 서명이 기록되는 공개 투명성 로그입니다.
  `COSIGN_KEYLESS=true`일 때만 사용됩니다.
  [sigstore.dev](https://www.sigstore.dev/) 참고.
- **Attestation / provenance (in-toto, SLSA).** 산출물이 *어떻게*
  생성되었는지에 대한 서명된 명세. TrustedOSS는 SBOM 서명과 함께
  [SLSA](https://slsa.dev/) provenance(빌더 신원 + 빌드 컨텍스트)를 담은
  [in-toto](https://in-toto.io/) Statement를 생성합니다.
  [SBOM 서명 검증](./sbom-signature-verification.md#provenance-attestation-확인) 참고.

## 라이선스 분류

포털은 라이선스를 네 개의 **단계(tier)** 로 분류합니다.

| 단계 (코드 값) | UI 라벨 | 빌드 게이트 효과 |
|---|---|---|
| `forbidden` | Forbidden | 빌드 실패 — CI 종료 코드 1 |
| `conditional` | Conditional | 컴포넌트 승인 필요; 승인 전까지 경고 |
| `permissive` | Allowed | 제한 없음 |
| `unknown` | Unknown | 검토 대상으로 노출; 자동 차단 없음 |

분류는 `apps/backend/tasks/scan_source.py` 의
`_LICENSE_CATEGORY_DEFAULTS` 사전이 결정합니다(운영자 측 오버라이드
경로; ORT 기반 조직별 룰은 v2.2 로드맵 항목). API 응답·감사 로그·
빌드 게이트 결정에는 `forbidden` / `conditional` / `permissive` /
`unknown` 값이, UI 테이블·배지에는 `Forbidden` / `Conditional` /
`Allowed` / `Unknown` 라벨이 노출됩니다.
[컴포넌트와 라이선스](../user-guide/components-and-licenses.md#라이선스-분류)
참고.

## 빌드 게이트

포털은 CI 차단 메커니즘을 하나 노출하며 이를 **빌드 게이트**라고
부릅니다(일부 운영자 대상 맥락에서는 **정책 게이트**라는 표기도
사용 — 동일한 대상입니다). 게이트는 다음을 평가합니다.

1. 프로젝트의 심각도 하한(기본 `Critical`; 프로젝트별
   `policy_gate.severity_floor` 구성 가능) 이상에 해당하는 CVE가
   있는가?
2. `forbidden` 라이선스 단계의 컴포넌트가 있는가?
3. *(선택)* **EPSS** score가 `GATE_EPSS_THRESHOLD` 이상인 미해결
   결과가 있는가? 이 세 번째 조건은 **기본 비활성**으로, 운영자가
   `GATE_EPSS_THRESHOLD` 환경변수(`0`~`1` 값)를 설정할 때만
   활성화됩니다. 미설정 시 게이트는 조건 1·2를 기존과 동일하게
   평가합니다.
   [EPSS](../user-guide/vulnerabilities.md#epss--악용-확률) 참고.

활성 조건 중 하나라도 참이면 CI 통합의 컴포지트 액션은 종료 코드 1을
반환합니다. 실패한 게이트는 위반 CVE / 라이선스 목록과 함께
`audit_logs`에 기록되며, EPSS 조건이 활성화된 경우 게이트 결과는
`epss_gate_count`와 `epss_threshold`도 함께 담습니다.

## RBAC 역할

- **`super_admin`** — 시스템 전체. 사용자·팀·취약점 데이터(Trivy DB)·스캔 큐·디스크·감사
  로그를 관리합니다. 설치 마법사 또는 `create_super_admin.py`
  스크립트가 생성합니다.
- **`team_admin`** — 단일 팀 범위. 팀 설정·팀원·팀 내 프로젝트
  가시성을 관리합니다.
- **`developer`** — 한 팀의 프로젝트 집합 범위. 스캔 실행·결과 조회·
  승인 검토를 수행합니다.

한 사용자가 소속된 팀마다 다른 역할을 보유할 수 있습니다
(예: 팀 A에서는 `team_admin`, 팀 B에서는 `developer`). 모든 할당은
`/admin/users/<id>` 의 Memberships 드로어에 표시됩니다.

## API Key 범위

API Key는 단일 **scope** 를 가집니다.

| Scope | 발급 권한 | 효과 |
|---|---|---|
| `org` | super-admin 전용 | 조직 내 모든 엔드포인트 인증 |
| `team` | super-admin, team-admin | 한 팀의 프로젝트 범위 |
| `project` | super-admin, team-admin, developer (본인 팀 프로젝트 한정) | 한 프로젝트 범위 |

v2.0.0 에는 액션 단위 허용 목록이 없습니다 — 올바른 scope의 키로
인증된 호출자는 API Key를 받는 어떤 엔드포인트라도 호출할 수
있습니다. 액션 단위 권한은 로드맵 항목입니다.

## 운영 용어

- **회로 차단기 (CLOSED / OPEN / HALF_OPEN).** 실패 도메인을 격리하는
  패턴. TrustedOSS는 v2.3까지 Dependency-Track API 클라이언트를 차단기로
  감쌌습니다. v2.4.0+에서는 Trivy DB가 워커 로컬에 있어 취약점 경로에서
  이 패턴은 더 이상 사용하지 않습니다. 일반 용어로는 운영 문헌에 계속
  등장합니다.
- **`audit_logs`.** 상태를 변경하는 모든 작업(1급 엔티티의 CRUD,
  명시적 비즈니스 이벤트 포함)을 추가 전용으로 캡처하는 테이블.
  [감사 로그](../admin-guide/audit-log.md) 참고.
- **Workspace.** 스캔당 체크아웃 디렉터리 — 호스트는
  `/opt/trustedoss/workspace`, 컨테이너는 `/workspace`. 디스크 압박
  서브시스템이 정리합니다(30일 이상 미사용).

## 함께 보기

- [아키텍처](./architecture.md) — 구성 요소가 어떻게 맞물리는지
- [API 개요](./api-overview.md) — REST + WebSocket 표면
- [환경 변수](./env-variables.md) — 모든 설정 항목
