---
id: comparison
title: TRUSCA 비교
sidebar_label: 비교
description: TRUSCA와 상용 SCA(Black Duck, Snyk), Dependency-Track, SW360을 정직하게 비교합니다 — 강점과 현재 한계.
---

# TRUSCA 비교

:::note 대상 독자
TRUSCA가 조직에 적합한지 판단하려는 엔지니어·플랫폼 담당자·법무 및
컴플라이언스 리드. 이 페이지는 의도적으로 정직합니다. 포털이 잘하는 점과 아직
하지 못하는 점을 함께 기재합니다. "로드맵" 항목의 배경은
[`ROADMAP.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md)를
참고하십시오.
:::

TRUSCA의 핵심 아이디어는 검증된 오픈소스 도구(cdxgen, scancode, Trivy —
Trivy가 단일 취약점 엔진)를 팀·역할·승인·CI 게이트가 포함된 하나의 자체 호스팅
UI로 묶는 것입니다. 아래 비교는 그 아이디어를 세 가지 대표 대안과 견줍니다. 모든
내용은 현재 릴리스의 출시 기능을 설명하며(계획 항목은 별도 표시), 벤치마크가
아니고 다른 프로젝트를 비방하지 않습니다 — 그중 여럿은 TRUSCA가 직접
활용하는 도구입니다.

용어 정의(SCA, SBOM, VEX, EPSS, reachability)는 [용어집](./reference/glossary.md)을
보십시오.

## 한눈에 보기

| | TRUSCA | 상용 SCA (Black Duck / Snyk) | Dependency-Track | Eclipse SW360 |
|---|---|---|---|---|
| 라이선스 | Apache-2.0 | 독점 | Apache-2.0 | EPL-2.0 |
| 호스팅 | 자체 호스팅 (Docker / Helm) | SaaS 또는 자체 관리 | 자체 호스팅 | 자체 호스팅 |
| 가격 모델 | 무료, 좌석당 비용 없음 | 좌석당 / 프로젝트당 | 무료 | 무료 |
| 컴포넌트 탐지 | cdxgen (30+ 생태계) | 광범위, 독점 | SBOM 소비 | SBOM 소비 |
| 라이선스 탐지 | declared + detected (scancode) | 깊음, 큐레이션 | 제한적 | 강함 (라이선스 clearing) |
| 취약점 데이터 | Trivy DB (NVD + OSV + GHSA + EPSS + KEV) | 큐레이션 독점 피드 | NVD / OSV / GHSA | 애드온 경유 |
| 컨테이너 스캔 | Trivy (OS 패키지) | 지원 | 미지원 | 미지원 |
| SBOM 내보내기 | CycloneDX + SPDX, byte-stable | 지원 | CycloneDX | SPDX / CycloneDX |
| RBAC | 3개 역할 (super / team / developer) | 풍부 | 팀 + 권한 | LDAP 역할 |
| 승인 워크플로우 | 내장 | 지원 | 미지원 | clearing 워크플로우 |
| CI 빌드 게이트 | Critical CVE / 금지 라이선스 시 exit 1 | 지원 | API 경유 | 미지원 |
| 이중 언어 UI (EN/KO) | 지원 | 부분 | 미지원 | 미지원 |
| 자동 리메디에이션 / PR | 로드맵 | 지원 | 미지원 | 미지원 |
| EPSS 우선순위화 | **지원** | 지원 | 부분 | 미지원 |
| VEX 소비 | **지원** | 지원 | 부분 | 미지원 |
| Reachability 분석 | 로드맵 | 일부 지원 | 미지원 | 미지원 |
| SBOM 서명 / provenance | 로드맵 | 부분 | 미지원 | 미지원 |

## 상용 SCA(Black Duck, Snyk)와 비교

**TRUSCA를 선택하는 경우:** 데이터를 직접 소유하고 좌석당 라이선스
비용을 피하고 싶을 때, 자체 호스팅에 익숙할 때, 그리고 탐지·라이선스·SBOM·승인·CI
게이트를 아우르는 통합 오픈소스 포털이 요구사항을 충족할 때입니다.

**오늘 상용 도구가 앞서는 지점:**

- **큐레이션된 취약점·라이선스 인텔리전스.** 상용 벤더는 독점 데이터베이스와 전담
  연구 조직을 운영합니다. TRUSCA는 Trivy DB로 전달되는 공개
  피드(NVD + OSV + GHSA + EPSS + KEV)에 의존합니다.
- **자동 리메디에이션.** Snyk 등은 수정 PR을 자동으로 생성합니다. TRUSCA는
  finding별 `fixed_version`과 의존성 그래프 depth는 제공하지만 업그레이드
  PR을 아직 생성하지 않습니다 — 추천 업그레이드는 계획 단계입니다.
- **우선순위화 신호.** EPSS 우선순위화는 1급 신호로 제공됩니다 — 컬럼·정렬·필터·
  정책 게이트 임계값. Reachability 분석은 아직 계획 단계입니다.

**TRUSCA가 경쟁력 있는 지점:** 좌석 비용 없는 자체 호스팅, Apache-2.0
라이선스, 여러 콘솔 대신 하나의 포털, 내장 컴포넌트 승인 워크플로우, 빌드 차단 CI
게이트, 완전 이중 언어(EN/KO) UI 및 문서입니다.

## Dependency-Track과 비교

Dependency-Track(DT)은 본연의 역할에 탁월합니다 — 사용자가 공급하는 SBOM에 집중한
취약점 인텔리전스 플랫폼. TRUSCA는 Trivy를 단일 내장 취약점 엔진으로
사용합니다(결정 배경은 [ADR-0001](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/decisions/0001-replace-dt-with-trivy.md) 참조).
관건은 어떤 형태의 플랫폼이 팀에 맞는가입니다.

**TRUSCA가 DT를 직접 운영하는 것과 다른 점:**

- **스캔 오케스트레이션.** SBOM을 직접 생성·업로드하도록 요구하는 대신 cdxgen,
  scancode, Trivy를 자동 실행하고 결과를 투입합니다.
- **라이선스 컴플라이언스.** 허용 / 조건부 / 금지 분류, 의무사항 추적, `NOTICE`
  자동 생성 — DT 범위 밖입니다.
- **워크플로우와 거버넌스.** 컴포넌트 승인, 빌드 차단 게이트, 추가 전용 감사 로그,
  3개 역할 RBAC.
- **운영 풋프린트.** ~500 MB Trivy DB vs DT의 4 GB JVM + H2 — 4 GB 호스트에서 동작.
- **이중 언어 UI.** 영어와 한국어.
- **트리아지 신호.** EPSS는 1급 신호(컬럼·정렬·필터·정책 게이트 임계값). KEV 배지가
  실환경 익스플로잇을 표시하며, 외부 VEX(OpenVEX / CycloneDX VEX)를 임포트해
  finding을 자동 억제할 수 있습니다.

**Dependency-Track을 직접 쓰는 경우:** DT의 네이티브 기능(UI, 정책 엔진, 기존
통합)을 원하거나, 이미 DT를 운영화했거나, DT만이 제공하는 조직별 컴포넌트 그래프
거버넌스 모델이 필요할 때입니다.

## Eclipse SW360과 비교

SW360은 **라이선스 clearing**과 컴포넌트 카탈로깅에 집중하는 성숙한 오픈소스
플랫폼입니다.

**SW360이 앞서는 지점:** 라이선스 clearing 워크플로우의 깊이, 대규모 컴포넌트
clearing 카탈로그, 정착된 엔터프라이즈 통합 패턴.

**TRUSCA가 앞서는 지점:** 기본 제공되는 통합 스캔 파이프라인(cdxgen /
scancode / Trivy), 컨테이너 스캔, 1급 CI 빌드 게이트, byte-stable CycloneDX **및**
SPDX 내보내기, 현대적 단일 페이지 UI, EN/KO 이중 언어 지원. SW360은 일반적으로
SBOM·컴포넌트가 공급되는 것을 전제하며 스캔보다 clearing을 강조합니다.

**SW360을 선택하는 경우:** 깊고 형식화된 라이선스 clearing이 주된 요구사항이며 이미
그 주위로 프로세스를 구축한 경우입니다.

## 현재 한계 (도입 전 유의)

다음은 실제이며 의도된 갭입니다. 각 항목은
[로드맵](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md)에
있습니다:

- **자동 리메디에이션 PR은 계획 단계.** 탐지·게이트를 하고 finding별 `fixed_version`과
  의존성 그래프 depth는 노출하지만 업그레이드 PR은 아직 생성하지 않습니다 — 추천
  업그레이드는 계획 단계입니다.
- **취약점 데이터가 Trivy DB에 의존.** 신호는 NVD + OSV + GHSA + EPSS + KEV
  노출분에 더해 1급 EPSS 우선순위화, KEV 실환경 배지, 임포트된 VEX로 보강됩니다.
- **Reachability 우선순위화 없음.** 취약 코드의 도달 가능성으로 순위를 매기는 대신
  전체를 나열합니다(계획, 베스트에포트).
- **정적 라이선스 정책.** 분류는 고정 카탈로그를 사용하며, per-team / per-org 편집
  가능 정책은 계획 단계입니다.
- **SBOM 서명 / provenance 없음.** SBOM 서명과 SLSA provenance는 계획
  단계입니다.
- **네이티브 Jenkins 플러그인 없음.** GitHub Actions와 GitLab CI는 1급 지원이며,
  Jenkins는 worked `Jenkinsfile` 예제로 지원합니다.
- **SSO / OIDC 없음.** 비밀번호와 OAuth(GitHub / Google, 데모 전용) 인증은 현재
  제공되며, SSO / OIDC는 백로그입니다.

## 함께 보기

- [소개](./intro.md) — 포털이 제공하는 기능과 제공하지 않는 기능
- [용어집](./reference/glossary.md) — SCA, SBOM, VEX, EPSS 등
- [로드맵](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md) — "로드맵" 항목이 도착하는 곳
