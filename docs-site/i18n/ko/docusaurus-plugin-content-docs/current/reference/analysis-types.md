---
id: analysis-types
title: 분석 유형
description: TRUSCA가 실행하는 분석의 종류 — 소스 SBOM 스캔, 컨테이너 이미지 스캔, 빌드 게이트, reachability — 각각이 무엇을 입력받고 어떤 도구로 실행되며 무엇을 산출하는지.
sidebar_label: 분석 유형
sidebar_position: 6
---

# 분석 유형

TRUSCA는 코드와 그 의존성에 대해 서로 다른 여러 **종류**의 분석을 실행합니다. 각 종류는 다른 입력을 받고 다른 도구를 실행하며 다른 결과를 냅니다 — finding 집합, 품질 점수, 또는 pass/fail 빌드 판정. 본 페이지는 **무엇을 실행할지** 정하는 진입 매트릭스입니다. 물음에 답하는 분석을 고른 뒤, 해당 분석을 자세히 다루는 페이지로 이어지는 링크를 따라가세요.

:::note 대상 독자
어떤 분석을 실행할지 고르는 신규 도입자·플랫폼 담당자, 그리고 TRUSCA의 기능을 내부 체크리스트에 대응시키는 검토자. SBOM(Software Bill of Materials — 빌드의 의존성 목록), CVE(Common Vulnerabilities and Exposures), CI 빌드 게이트에 익숙하면 도움이 됩니다. 용어 정의는 [용어집](./glossary.md)을 참조하세요.
:::

:::note 이 페이지는 데이터 신호가 아니라 파이프라인의 매트릭스입니다
본 페이지는 **분석 파이프라인** — 소스 스캔, 컨테이너 스캔, 빌드 게이트, reachability — 을 나열합니다. [데이터 출처](./data-sources.md#분석-유형) 페이지에도 이름이 비슷한 `## 분석 유형` 섹션이 있지만, 그쪽은 Trivy DB가 노출하는 finding별 **데이터 신호**(NVD · OSV · GHSA · EPSS · KEV, 그리고 CVSS·CWE·수정 버전 등)의 매트릭스입니다. 두 페이지를 함께 읽으세요. 본 페이지는 *어떤 분석이 실행되는가*이고, 그 페이지는 *각 취약점 finding이 어떤 데이터를 담는가*입니다.
:::

## 매트릭스

| 분석 | 입력 | 도구 | 산출 | 사용 시점 | 자세히 |
|---|---|---|---|---|---|
| **소스 SBOM 스캔** | Git 레포지토리 (또는 업로드한 소스 아카이브) | `cdxgen` → scancode → `trivy sbom` | CycloneDX SBOM, 법적 tier 분류가 붙은 declared + detected 라이선스, 매칭된 CVE | 기본값. 프로젝트 의존성 트리의 전체 컴포넌트 목록·라이선스·취약점이 필요할 때. | [스캔 → 스캔 종류](../user-guide/scans.md#스캔-종류), [SBOM](../user-guide/sbom.md) |
| **컨테이너 이미지 스캔** | 빌드된 컨테이너 이미지 참조 (`name:tag`) | Trivy | OS 패키지 CVE와 베이스 이미지 OS 지원 종료(EOSL) 판정 | 컨테이너를 배포하며, 애플리케이션 의존성뿐 아니라 OS 계층의 취약점도 알고 싶을 때. | [스캔 → 컨테이너 이미지 스캔](../user-guide/scans.md#컨테이너-이미지-스캔), [컨테이너 OS 지원 종료](../user-guide/scans.md#container-os-eol) |
| **빌드 게이트** | 완료된 스캔의 finding과 라이선스 | 포털 게이트 평가기 | pass/fail 빌드 판정 (CI exit code `0` 또는 `1`) | 금지 라이선스나 임계값 초과 취약점에서 빌드를 자동으로 실패시키고 싶을 때 — CI 집행 지점. | [승인](../user-guide/approvals.md), [라이선스 정책](./license-policies.md), [GitHub Actions](../ci-integration/github-actions.md) |
| **Reachability 분석** | 스캔한 모듈의 보존된 Go 소스 | `govulncheck` (Go) | finding별 reachable / not-reachable / not-analysed 신호 (Go finding에 한함) | 취약 코드가 실제로 호출되는 finding에 우선순위를 매기고 싶을 때. **현재 Go만 지원 — 아래 상태 안내를 확인하세요.** | [비교 → reachability](../comparison.md) |

네 가지 모두 현재 제공·실행됩니다. reachability는 `govulncheck`로 **Go에 대해** 제공됩니다 — 범위는 아래 안내를 읽으세요. Go 모듈만 커버하며, 다른 모든 생태계의 finding은 아직 분석되지 않습니다.

## 소스 SBOM 스캔 {#source-detail}

소스 스캔은 기본 분석입니다. `cdxgen`(30개 이상 생태계를 커버하는 CycloneDX SBOM 생성기)이 레포지토리를 순회해 의존성 트리의 SBOM을 내며, 각 패키지의 메타데이터에서 읽은 **declared** 라이선스를 함께 담습니다. 이어서 scancode가 팀이 직접 작성한 first-party 소스를 읽어 **detected** 라이선스를 찾습니다(베스트에포트). 마지막으로 `trivy sbom`이 SBOM을 로컬 Trivy DB에 대조해 CVE finding을 내고, 내장 분류기가 각 라이선스에 법적 tier(`permissive` / `conditional` / `forbidden` / `unknown`)를 부여합니다.

결과는 모든 프로젝트 탭 — Components, Licenses, Vulnerabilities, SBOM — 으로 흘러갑니다. 단계별 흐름은 [아키텍처 → 스캔 파이프라인](./architecture.md#스캔-파이프라인)을, 실행 방법은 [스캔](../user-guide/scans.md)을 참조하세요.

**업로드한 SBOM**(팀의 빌드가 이미 만든 SBOM)은 이 종류의 변형입니다. TRUSCA는 소스를 클론하거나 빌드하지 않고, SBOM의 적합성을 채점하고 컴포넌트를 저장하며 동일한 `trivy sbom` 매칭을 실행합니다. [SBOM 업로드](../user-guide/scans.md#받은-sbom-업로드)을 참조하세요.

## 컨테이너 이미지 스캔 {#container-detail}

컨테이너 스캔은 소스가 아니라 **빌드된 이미지**를 대상으로 합니다. Trivy가 이미지의 OS 패키지(Alpine `apk`, Debian `deb`, RHEL `rpm` 등)에서 알려진 CVE를 검사합니다 — 애플리케이션 의존성 트리를 다루는 소스 스캔과 상호 보완합니다. 또한 이미지의 베이스 OS 릴리스가 **지원 종료**를 지났는지 보고합니다. 업스트림 보안 수정을 더 받지 못하는 릴리스는 그 은퇴 이후 공개된 CVE에 대해 결코 패치되지 않으므로, 지원되는 릴리스로 재빌드하기를 권장합니다.

[컨테이너 이미지 스캔](../user-guide/scans.md#컨테이너-이미지-스캔)과 [베이스 이미지 OS 지원 종료](../user-guide/scans.md#container-os-eol)를 참조하세요.

## 빌드 게이트 {#gate-detail}

빌드 게이트는 스캐너가 아닙니다 — 완료된 스캔의 출력을 규칙에 대조해 빌드 판정을 내리는 **평가** 단계입니다. 라이선스가 `forbidden`으로 판정되는 컴포넌트와 설정된 임계값을 넘는 취약점을 세어 pass/fail 결과를 냅니다. CI에서 실패한 게이트는 exit code `1`로 빌드를 차단합니다. 임계값과 태세는 `GATE_*` 환경변수([환경변수](./env-variables.md) 참조)로, 팀·조직 단위로는 카운트 전에 라이선스를 동적으로 재분류하는 [라이선스 정책](./license-policies.md)으로 설정합니다.

조건부 라이선스를 둘러싼 사람의 워크플로우는 [승인](../user-guide/approvals.md)을, CI 배선은 [GitHub Actions](../ci-integration/github-actions.md)를 참조하세요.

## Reachability 분석 {#reachability-detail}

:::note Go는 제공, 다른 생태계는 아직 미분석
Reachability는 현재 **Go에 대해** 제공됩니다. 성공한 소스 스캔마다 워커가 베스트에포트 후속 단계로 `govulncheck`를 실행합니다(`scan_reachability.py`, `scan_source`에서 디스패치. `govulncheck`는 워커 이미지에 내장). 기본으로 켜져 있으며, 워커 부하를 덜려면 `REACHABILITY_ENABLED=false`로 끌 수 있습니다. 원래 스캔을 실패시키지 않습니다 — 소스가 보존되지 않았거나 프로젝트가 Go 모듈이 아니거나 `govulncheck`가 없거나 시간이 초과되면 finding은 그대로 "not analysed"로 남습니다.

각 **Go** finding(`govulncheck`가 보고한 CVE / GHSA / GO id를 지닌 `pkg:golang/` 컴포넌트)에 분석기가 판정을 찍습니다: `reachable = true`(콜 그래프에서 취약 심볼에 도달 가능), `reachable = false`(분석기는 실행됐으나 심볼에 도달 불가), `reachable = null`(미분석). 이 신호는 reachability 배지, `?reachable=true|false|unknown` 필터, `sort=reachable` 순위로 노출되며, 정책·빌드 게이트에도 반영될 수 있습니다.

**다른 생태계(Java, JS/TS, Python 등)의 finding은 아직 분석되지 않습니다** — `reachable = null`로 남으며 "영향 가능"으로 표시됩니다. 다중 언어 reachability가 현재의 상용 격차입니다: Black Duck와 Snyk는 독점 다중 언어 reachability를 실행하지만, TRUSCA는 `govulncheck`로 Go 전용 reachability를 제공합니다.
:::

다중 언어 로드맵은 [비교 페이지](../comparison.md)와 [로드맵](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md)에서 확인하세요.

## 동작 확인

<!-- docs-uat: id=analysis-types-pipelines-match-scans kind=manual tier=manual -->
1. 본 페이지의 제공되는 세 분석 종류(소스, 컨테이너, 빌드 게이트)는 [스캔 → 스캔 종류](../user-guide/scans.md#스캔-종류)와 [라이선스 정책 → 동적 게이트 평가](./license-policies.md#동적-게이트-평가)에 문서화된 스캔 종류·게이트와 일치합니다 — 그곳에 문서화되지 않은 파이프라인이 여기 등장하지 않습니다.

<!-- docs-uat: id=analysis-types-reachability-go kind=manual tier=manual -->
2. reachability 행과 그 상태 안내는 현재 제공되는 **Go 전용 베스트에포트** 신호를 기술합니다: Go finding은 `govulncheck`의 `reachable = true / false / null` 판정을 담을 수 있고, non-Go finding은 "not analysed"로 남습니다. 이는 [비교](../comparison.md)("Go만 지원", reachability 우선순위화는 Go에 제공)·[데이터 출처](./data-sources.md)(reachability는 Trivy DB 신호가 아니라 Go를 위한 별도 `govulncheck` 파이프라인)와 일관됩니다.

## 트러블슈팅

- **"라이선스와 CVE는 어떤 분석으로 얻나요?"** 소스 스캔입니다 — 한 번의 실행으로 둘 다 냅니다. 이후 빌드 게이트가 그 결과를 빌드 판정으로 바꿉니다.
- **"컨테이너 스캔이 애플리케이션 의존성 CVE를 하나도 찾지 못했습니다."** 컨테이너 스캔은 OS 패키지만 다룹니다. 애플리케이션 의존성 트리는 소스 스캔으로 실행하세요. 둘은 상호 보완적입니다.
- **"모든 finding에서 reachability 배지가 비어 있습니다."** non-Go finding에서는 정상이며, Go finding도 reachability가 실행되지 못한 경우(소스 미보존, `REACHABILITY_ENABLED=false`, 또는 `govulncheck` 없음·시간 초과)에는 마찬가지입니다. finding은 "not analysed"로 남고 compact 목록은 그 상태에 아무것도 렌더링하지 않습니다. 분석된 Go finding은 reachable / not-reachable 배지를 표시합니다. [상태 안내](#reachability-detail)를 참조하세요.

## 참고

- [스캔](../user-guide/scans.md) — 소스·컨테이너 스캔 실행, 진행 확인.
- [SBOM](../user-guide/sbom.md) — 소스 스캔이 내는 SBOM의 내보내기·읽기.
- [아키텍처](./architecture.md) — 서비스, 스캔 파이프라인 단계, Trivy 매칭.
- [데이터 출처](./data-sources.md) — 각 취약점 뒤의 finding별 데이터 신호(NVD · OSV · GHSA · EPSS · KEV).
- [라이선스 정책](./license-policies.md) — 빌드 게이트가 라이선스를 분류·게이트하는 방식.
- [비교](../comparison.md) — reachability 범위(현재 Go)와 다른 계획 항목의 현황.
