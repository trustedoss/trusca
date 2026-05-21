---
id: intro
title: 소개
description: TrustedOSS Portal — 자체 호스팅 가능한 Apache-2.0 SCA 포털. CVE, 라이선스 컴플라이언스, SBOM을 하나의 UI에서 관리합니다.
sidebar_label: 소개
sidebar_position: 1
slug: /intro
---

# TrustedOSS Portal

:::tip 버전 2.0.0 (GA) — 2026-05-09
TrustedOSS Portal이 정식 출시되었습니다! 새로 추가된 내용은 [v2.0.0 릴리스 노트](./release-notes/v2.0.0.md)를 참고하세요.
:::

**TrustedOSS Portal**은 자체 호스팅이 가능한 오픈소스 SCA(Software Composition Analysis) 플랫폼입니다. 취약점 추적, 라이선스 컴플라이언스, SBOM 관리를 한 화면에서 통합 제공하며, 상용 제품의 좌석당 라이선스 비용 없이 운영할 수 있습니다.

:::note 대상 독자
SCA 포털 도입을 검토하는 엔지니어·플랫폼 담당자·법무 및 컴플라이언스 리드. 바로 설치하려면 [Docker Compose 설치](./installation/docker-compose.md)로 이동하세요.
:::

## 2.0.0의 새로운 점

- **인증 UX** — `/forgot-password` + `/reset-password` 흐름, `/login`의 OAuth(GitHub + Google), EN / KO를 한 박자로 유지하는 `i18next-parser` 드리프트 게이트.
- **`/integrations` 페이지** — 1회 평문 노출이 있는 셀프 서비스 API Key, 폐기 확인, GitHub / GitLab Webhook URL 인라인 정보.
- **백업 자동화** — 매일 00:00 UTC Celery Beat 백업과 7일 자동 보존, 그리고 수동 트리거·스트리밍 다운로드·타이핑 게이트 Upload + Restore를 지원하는 `/admin/backup` UI.
- **CI의 SAST hard-fail** — `bandit`, `semgrep`, Trivy 이미지 스캔이 각각 High / ERROR / CRITICAL에서 머지를 차단합니다.
- **SCA self-scan** — 포털 자체 의존성을 스캔하고 GitHub 이슈를 자동으로 열고 닫는 야간 워크플로 — 프로젝트가 자기 도그푸드를 먹기 시작했습니다.

전체 내용은 [v2.0.0 릴리스 노트](./release-notes/v2.0.0.md)에 있습니다.

## 제공 기능

| 기능 | 설명 |
|---|---|
| 컴포넌트 탐지 | `cdxgen`(CycloneDX generator)으로 30개 이상의 생태계(npm, Maven, PyPI, Go, Cargo, NuGet, Composer, RubyGems, Gradle, Hex 등)에서 패키지를 식별합니다. |
| 라이선스 분류 | 모든 라이선스를 **허용 / 조건부 / 금지**로 분류합니다; declared 라이선스는 `cdxgen` 에서, detected first-party 라이선스는 scancode 에서 나옵니다. 금지 라이선스는 빌드를 차단합니다. |
| 취약점 탐지 | Dependency-Track(DT)이 NVD, OSV, GitHub Advisory와 컴포넌트를 대조합니다. |
| 컨테이너 스캔 | Trivy(Aqua Security 컨테이너 스캐너)로 컨테이너 이미지의 OS 패키지 CVE(Common Vulnerabilities and Exposures)를 탐지합니다. |
| SBOM 내보내기 | CycloneDX(JSON·XML)와 SPDX(JSON·Tag-Value). diff 가능한 byte-stable 출력. |
| 의무사항 및 NOTICE | 라이선스별 의무사항을 추적하고 최신 스캔 기준 `NOTICE` 파일을 자동 생성합니다. |
| CI/CD 통합 | REST API + API Key 인증, GitHub·GitLab Webhook, GitHub Action, GitLab CI 템플릿, Jenkinsfile. Critical CVE 또는 금지 라이선스 발견 시 빌드 게이트가 종료 코드 1을 반환합니다. |
| 알림 | 이메일(SMTP), Slack, Microsoft Teams Webhook으로 여섯 가지 트리거 종류 — 스캔 완료 / 스캔 실패 / CVE 탐지 / 라이선스 위반 / 승인 대기 / 빌드 게이트 실패. (대부분의 종류에 대한 producer 측 emit 포인트는 v2.1에 도착합니다. 인박스 UI는 현재 동작합니다.) |
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
 ┌───────────┐       ┌──────────┐           ┌────────────────────────────┐
 │ Postgres  │       │ Celery   │ → 작업 →  │ cdxgen / scancode / Trivy /│
 │   (17)    │       │ + Redis  │           │ Dependency-Track           │
 └───────────┘       └──────────┘           └────────────────────────────┘
```

프로덕션에서는 **traefik**, **postgres**, **redis**, **backend**, **worker**, **beat**(Celery 스케줄러), **frontend** 7개의 컨테이너 서비스가 동작합니다. 선택적 Dependency-Track 오버레이가 번들 취약점 데이터를 추가합니다.

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
