---
id: intro
title: 소개
description: TrustedOSS Portal — 자체 호스팅 가능한 Apache-2.0 SCA 포털. CVE, 라이선스 컴플라이언스, SBOM을 하나의 UI에서 관리합니다.
sidebar_label: 소개
sidebar_position: 1
slug: /intro
---

# TrustedOSS Portal

**TrustedOSS Portal**은 자체 호스팅이 가능한 오픈소스 SCA(Software Composition Analysis) 플랫폼입니다. 취약점 추적, 라이선스 컴플라이언스, SBOM 관리를 한 화면에서 통합 제공하며, 상용 제품의 좌석당 라이선스 비용 없이 운영할 수 있습니다.

:::note 대상 독자
SCA 포털 도입을 검토하는 엔지니어·플랫폼 담당자·법무 및 컴플라이언스 리드. 바로 설치하려면 [Docker Compose 설치](./installation/docker-compose.md)로 이동하세요.
:::

## 제공 기능

| 기능 | 설명 |
|---|---|
| 컴포넌트 탐지 | `cdxgen`으로 30개 이상의 생태계(npm, Maven, PyPI, Go, Cargo, NuGet, Composer, RubyGems, Gradle, Hex 등)에서 패키지를 식별합니다. |
| 라이선스 분류 | ORT 룰셋이 모든 라이선스를 **허용 / 조건부 / 금지**로 분류합니다. 금지 라이선스는 빌드를 차단합니다. |
| 취약점 탐지 | Dependency-Track이 NVD, OSV, GitHub Advisory와 컴포넌트를 대조합니다. |
| 컨테이너 스캔 | Trivy로 컨테이너 이미지의 OS 패키지 CVE를 탐지합니다. |
| SBOM 내보내기 | CycloneDX(JSON·XML)와 SPDX(JSON·Tag-Value). diff 가능한 byte-stable 출력. |
| 의무사항 및 NOTICE | 라이선스별 의무사항을 추적하고 최신 스캔 기준 `NOTICE` 파일을 자동 생성합니다. |
| CI/CD 통합 | REST API + API Key 인증, GitHub·GitLab Webhook, GitHub Action, GitLab CI 템플릿, Jenkinsfile. Critical CVE 또는 금지 라이선스 발견 시 빌드 차단 게이트가 종료 코드 1을 반환합니다. |
| 알림 | 이메일(SMTP), Slack, Microsoft Teams Webhook으로 다섯 가지 핵심 트리거(스캔 완료·게이트 실패·신규 CVE·승인 요청·디스크 압박) 발신. |
| 감사 로그 | 모든 쓰기 작업의 추가 전용 기록 — 행위자·동작·대상·요청 ID. |
| 다국어 | 영어·한국어 동시 출시. UI, 오류 메시지, 본 문서 모두 이중 언어로 제공됩니다. |

## 제공하지 않는 기능

- **SAST 스캐너 아님.** 자체 작성한 코드의 정적 분석은 다루지 않습니다. 본 포털은 제3자 컴포넌트에 집중합니다.
- **취약점 데이터베이스 아님.** Dependency-Track을 통해 NVD·OSV·GitHub Advisory를 소비할 뿐, 직접 큐레이션하지 않습니다.
- **호스팅 SaaS 아직 아님.** 기본 배포 형태는 직접 운영하는 인프라에 `docker-compose`로 설치하는 방식입니다. GCP 데모 SaaS는 Phase 8에서 후속 제공 예정입니다.

## 아키텍처 개요

```
┌────────────┐   ┌────────────────────────────────┐   ┌──────────────────┐
│ 브라우저    │ → │ Traefik (TLS, HTTP→HTTPS)       │ → │ Frontend (Vite)  │
└────────────┘   └────────────────────────────────┘   └──────────────────┘
                            │
                            ↓
                   ┌────────────────┐
                   │ FastAPI 백엔드 │
                   └────────────────┘
                            │
       ┌────────────────────┼────────────────────────┐
       ↓                    ↓                        ↓
 ┌───────────┐       ┌──────────┐           ┌────────────────────────┐
 │ Postgres  │       │ Celery   │ → 작업 →  │ cdxgen / ORT / Trivy / │
 │   (17)    │       │ + Redis  │           │ Dependency-Track       │
 └───────────┘       └──────────┘           └────────────────────────┘
```

프로덕션에서는 **traefik**, **postgres**, **redis**, **backend**, **worker**, **beat**(Celery 스케줄러), **frontend** 6~7개 컨테이너 서비스가 동작합니다. Dependency-Track과 Jaeger 오버레이는 선택 사항입니다.

전체 아키텍처와 결정 기록, 파이프라인 상세는 [아키텍처 참고](./reference/architecture.md)를 보세요.

## 라이선스 및 거버넌스

- **라이선스**: Apache-2.0 — [`LICENSE`](https://github.com/trustedoss/trustedoss-portal/blob/main/LICENSE) 참고.
- **소스**: [github.com/trustedoss/trustedoss-portal](https://github.com/trustedoss/trustedoss-portal).
- **로드맵**: [`docs/v2-execution-plan.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/v2-execution-plan.md) — 진행 중 작업의 단일 진실 문서.
- **보안 신고**: [`SECURITY.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/SECURITY.md).

## 다음으로 읽을 곳

- **자체 호스트에 설치** → [Docker Compose 설치](./installation/docker-compose.md)
- **첫 스캔 실행** → [스캔](./user-guide/scans.md)
- **CI 연동** → [GitHub Actions](./ci-integration/github-actions.md), [GitLab CI](./ci-integration/gitlab-ci.md), [Jenkins](./ci-integration/jenkins.md)
- **운영** → [사용자 및 팀](./admin-guide/users-and-teams.md), [백업·복원](./admin-guide/backup-and-restore.md)
- **API 사용자** → [API 개요](./reference/api-overview.md)
