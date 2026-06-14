---
id: intro
title: 소개
description: TRUSCA — 자체 호스팅 가능한 Apache-2.0 SCA 포털. CVE, 라이선스 컴플라이언스, SBOM을 하나의 UI에서 관리합니다.
sidebar_label: 소개
sidebar_position: 0
slug: /intro
---

# TRUSCA

**TRUSCA**는 [TrustedOSS](https://trustedoss.github.io/)의 SCA 도구로,
자체 호스팅이 가능한 Apache-2.0 라이선스의 SCA
(Software Composition Analysis) 플랫폼입니다. CVE 추적, 라이선스 컴플라이언스,
SBOM 관리를 한 화면에서 통합 제공하며, 상용 제품의 좌석당 라이선스 비용 없이
운영할 수 있습니다.

## 시작 지점

- **5분 안에 체험** → [Quickstart](./quickstart.md) — 단일 명령으로 데모 데이터셋과 함께 실행.
- **자체 호스트에 설치** → [Docker Compose](./installation/docker-compose.md) 또는 [Helm 차트](./installation/helm.md).
- **다른 도구와 비교** → [비교](./comparison.md) — 상용 SCA / Dependency-Track / SW360 대비.

## 제공 기능

| 기능 | 설명 |
|---|---|
| 컴포넌트 탐지 | `cdxgen`이 30개 이상의 언어 생태계(npm, Maven, PyPI, Go, Cargo, NuGet, RubyGems 등)에서 패키지를 식별합니다. |
| 라이선스 분류 | 허용 / 조건부 / 금지 3단계, `NOTICE` 파일 자동 생성. 금지 라이선스는 빌드를 차단합니다. |
| 취약점 탐지 | Trivy가 로컬 DB를 통해 NVD + OSV + GHSA + EPSS + KEV와 컴포넌트를 매칭합니다. 주간 DB 갱신 시 새로운 CVE가 자동으로 반영됩니다. |
| 컨테이너 스캔 | Trivy가 컨테이너 이미지의 OS 패키지 CVE를 탐지합니다. |
| SBOM 내보내기 | CycloneDX(JSON / XML) + SPDX(JSON / Tag-Value), byte-stable. |
| SBOM 받기 | 고객 도구가 만든 CycloneDX 또는 SPDX SBOM을 업로드하면, TRUSCA가 소스를 복제하지 않고 적합성(pass / warn / fail)을 채점하고 CVE를 매칭합니다. |
| CI/CD 통합 | GitHub Action, GitLab CI 템플릿, Jenkinsfile 예제, REST API + API Key. Critical CVE 또는 금지 라이선스 발견 시 빌드 게이트가 `exit 1`을 반환합니다. |
| 워크플로 | 컴포넌트 승인, 추가 전용 감사 로그, 이메일·Slack·Teams 알림. |
| 다국어 | 영어·한국어 — UI·오류 메시지·본 문서 모두. |

## 제공하지 않는 기능

- **SAST 스캐너 아님.** 자체 작성한 코드의 정적 분석은 다루지 않습니다 — 본 포털은
  제3자 컴포넌트에 집중합니다.
- **취약점 데이터베이스 아님.** Trivy DB를 통해 NVD·OSV·GHSA·EPSS·KEV를 소비할 뿐,
  직접 큐레이션하지 않습니다.
- **기본 배포는 호스팅 SaaS가 아님.** 자체 인프라에서 `docker-compose` 또는 Helm
  차트로 운영합니다. 읽기 전용 [라이브 데모](./installation/live-demo.md)도 제공합니다.

## 프로젝트

- **라이선스** — Apache-2.0.
- **소스** — [github.com/trustedoss/trusca](https://github.com/trustedoss/trusca).
- **로드맵** — [`ROADMAP.md`](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md).
- **보안 신고** — [`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md).
- **아키텍처 / 결정 기록** — [아키텍처 참고](./reference/architecture.md).
