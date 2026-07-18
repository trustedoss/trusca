---
id: scan-frequency
title: 스캔 주기
description: TRUSCA 프로젝트를 얼마나 자주 스캔할지 결정합니다 — 소스 변경 시 CI로 스캔하고, CVE 최신성은 Trivy DB 재매칭 beat에 맡깁니다.
sidebar_label: 스캔 주기
sidebar_position: 1
---

# 스캔 주기

프로젝트를 얼마나 자주 스캔해야 할까요? 짧게 답하면, **새 CVE를 잡으려고 시간에 맞춰 스캔하지 말고 소스가 바뀔 때 스캔합니다.** 소스 스캔은 의존성 트리를 다시 읽습니다. 트리가 그대로인데 새로 알려진 취약점은 재스캔 없이 Trivy DB 재매칭 beat가 자동으로 탐지합니다. 이 페이지는 큐나 디스크를 넘치게 하지 않으면서 결과를 최신으로 유지하는 주기를 정하도록 돕습니다.

:::note 대상 독자
자기 팀의 스캔 정책을 정하는 `team_admin`·`super_admin`. CI 트리거와 [스캔 수명 주기](../user-guide/scans.md)에 익숙하다고 가정합니다. 이 문서는 결정 가이드입니다 — 멈추거나 실패한 스캔의 복구는 [on-call 런북](../admin-guide/oncall-runbook.md)을 참고하십시오.
:::

## 자주 혼동되는 두 축 {#two-axes}

발견 항목은 서로 독립적인 두 입력의 조합에서 나옵니다. 이 둘을 나누는 것이 결정의 전부입니다.

| 축 | 언제 바뀌는가 | 무엇이 최신으로 유지하는가 |
|---|---|---|
| **SBOM** (배포하는 컴포넌트) | 의존성을 추가·제거·버전 변경할 때 — 즉 소스 변경. | 트리를 다시 읽는 **소스 스캔**. |
| **취약점 데이터** (알려진 CVE) | 이미 배포 중인 컴포넌트에 대해 Trivy가 새 권고를 게시할 때. | **Trivy DB 갱신 + 재매칭 beat** — 재스캔 없음. |

SBOM(소프트웨어 자재 명세)은 스캔이 만들어 내는 컴포넌트의 기계 판독 가능한 목록입니다. CVE(공통 취약점·노출 식별자)는 알려진 취약점의 공식 ID입니다.

"새 CVE를 잡으려고" 매일 밤 재스캔을 거는 것이 흔한 실수입니다. 그럴 필요가 없습니다. TRUSCA는 갱신된 데이터베이스에 대해 기존 SBOM을 beat로 이미 재매칭합니다. CVE 최신성을 좇아 스캔을 걸면 큐 슬롯과 디스크만 소모합니다.

## 소스 변경 시 스캔 {#on-source-change}

실제로 SBOM을 바꾸는 이벤트에서 스캔을 실행하십시오.

- **모든 pull·merge request** — 의존성 변경의 리스크 차이를 병합 전에 리뷰어가 보게 합니다. [빌드 차단 게이트](../ci-integration/github-actions.md)가 값을 하는 지점입니다.
- **보호 브랜치(`main`, `release/*`)로의 모든 push** — 브랜치의 *live* 스냅샷이 항상 병합된 내용을 반영하게 합니다.
- **태그·릴리스 빌드** — SBOM을 영구 컴플라이언스 기록으로 남기도록 `release` 레이블을 붙입니다([스캔 보존](../admin-guide/scan-retention.md#keep-a-scan-forever-release-label) 참고).

CI 템플릿이 이를 대신 연결하고 ref를 전달하므로, 브랜치와 PR이 각자의 보존 대상으로 묶입니다.

- [GitHub Actions](../ci-integration/github-actions.md)
- [GitLab CI](../ci-integration/gitlab-ci.md)
- [Jenkins](../ci-integration/jenkins.md)
- [Webhook](../ci-integration/webhooks.md) — 전체 CI 작업 없이 push·PR 이벤트만.

:::tip 진짜 트리거는 lockfile 변경입니다
CI가 지나치게 잦다면, 스캔 단계를 의존성 매니페스트(`package-lock.json`, `pom.xml`, `go.mod`, `requirements.txt` 등) 변경에 한정하십시오. 문서만 고친 커밋은 SBOM을 바꾸지 않으므로 스캔이 필요 없습니다. 이렇게 하면 의존성 변경을 하나도 놓치지 않으면서 큐를 줄입니다.
:::

## CVE 최신성은 beat에 맡깁니다 {#cve-freshness}

이미 배포 중인 의존성에 대해 새로 공개된 CVE를 탐지하려고 스캔을 **예약하지 않습니다**. 두 백그라운드 작업이 그 일을 처리합니다.

1. **Trivy DB 갱신**이 갱신된 취약점 데이터베이스를 받아 옵니다(기본 주간). [취약점 데이터](../admin-guide/vulnerability-data.md)를 참고하십시오.
2. **재매칭 beat**가 몇 시간마다 기존 SBOM을 갱신된 데이터에 다시 대조하고, 새 항목마다 `cve_detected` 알림을 올립니다 — 사용자 관점의 설명은 [재탐지](../user-guide/vulnerabilities.md#재탐지)입니다.

그래서 소스 변경이 없는 유휴 프로젝트도 새 CVE 알림을 받습니다. 주기는 **소스 변경**의 문제이고 최신성은 **데이터베이스**의 문제이며, 데이터베이스는 이미 처리됩니다.

:::caution 오래된 Trivy DB는 재탐지를 조용히 굶깁니다
재매칭 beat는 자신이 읽는 데이터베이스만큼만 최신입니다. Trivy DB 갱신이 계속 실패하면, 스캔은 여전히 성공하더라도 새 CVE가 들어오지 않습니다. 이는 스캔 주기 결정이 아니라 데이터 최신성 사고입니다 — [on-call 런북 시나리오 1](../admin-guide/oncall-runbook.md#시나리오-1--trivy-db-stale-또는-누락)로 진단·복구하십시오. `/admin/health`의 **Vulnerability data** 카드에서 오래됨 여부를 확인하십시오.
:::

## 규모에 맞는 주기 {#cadence}

한 규칙을 모든 곳에 적용하지 말고 브랜치 역할에 트리거를 맞추십시오.

| 브랜치·이벤트 | 권장 트리거 | 이유 |
|---|---|---|
| pull·merge request | PR마다 스캔, 병합을 게이트 | 위험한 의존성이 들어오기 전에 잡습니다. |
| `main`·보호 브랜치 | push마다 스캔 | live 스냅샷을 정확하게 유지합니다. |
| 릴리스 태그 | `release` 레이블로 한 번 스캔 | 배포된 버전의 영구 SBOM. |
| 변동이 적은 장수 서비스 | PR + push만, 예약 없음 | 새 CVE는 beat가 처리하고 소스는 거의 안 움직입니다. |
| 벤더링·외부 도입 | 벤더링 트리 변경 시 스캔 | 패키지 매니저 이벤트가 스스로 발생하지 않습니다. |

**예약** 스캔이 슬롯 값을 하는 유일한 경우는, 커밋 *없이* 의존성이 흐르는 프로젝트입니다 — 예를 들어 빌드 시점에 유동 버전 범위를 해석하는 빌드입니다. 그런 경우 주간 예약 스캔이 관측된 트리를 다시 고정합니다. 그 외에는 소스 변경 트리거와 재매칭 beat가 더 낫습니다.

보존 덕분에 이를 감당할 수 있습니다. 브랜치·PR별로 최신 스캔만 live로 남고, 대체된 스냅샷은 유예 기간 뒤 회수되며, `release` 레이블 스캔은 영구 보존됩니다. PR이 많은 저장소가 디스크를 채우지 않도록 [스캔 보존](../admin-guide/scan-retention.md)에서 기간을 조정하십시오.

## 검증

<!-- docs-uat: id=bp-scan-frequency-cadence-review kind=manual tier=manual -->
스캔 정책을 다음 항목으로 점검하십시오.

<!-- docs-uat: id=bp-scan-frequency-1 kind=manual tier=manual -->
1. 의존성 매니페스트를 바꾸는 PR을 열면 스캔과 빌드 차단 판정이 그 PR에 나타나며, 문서만 고친 PR에는 나타나지 않습니다.
<!-- docs-uat: id=bp-scan-frequency-2 kind=manual tier=manual -->
2. `main`으로의 push가 `main` 대상의 새 live 스냅샷을 만듭니다(이전 스냅샷은 프로젝트 스캔 이력에서 대체됨으로 표시됩니다).
<!-- docs-uat: id=bp-scan-frequency-3 kind=manual tier=manual -->
3. 최근 소스 스캔이 **없는** 프로젝트도 Trivy DB 갱신 후 새 `cve_detected` 알림을 보여 줍니다 — 예약이 아니라 재매칭 beat가 CVE 최신성을 담당한다는 증거입니다.
<!-- docs-uat: id=bp-scan-frequency-4 kind=manual tier=manual -->
4. `/admin/health`의 **Vulnerability data** 카드가 최근 갱신(최신성 `fresh`)을 보고하여, beat가 대조할 최신 데이터가 있습니다.
<!-- docs-uat: id=bp-scan-frequency-5 kind=manual tier=manual -->
5. 단지 CVE를 잡으려고 매일 밤 재스캔을 돌리고 있지 **않습니다** — 그렇다면 제거하고 beat에 맡기십시오.

## 함께 보기

- [스캔](../user-guide/scans.md) — 스캔별 수명 주기
- [스캔 보존](../admin-guide/scan-retention.md) — 이력을 유용하게, 디스크를 한정되게 유지
- [취약점 데이터 (Trivy DB)](../admin-guide/vulnerability-data.md) — 갱신 + 재매칭 수명 주기
- [재탐지](../user-guide/vulnerabilities.md#재탐지) — 재스캔 없이 새 CVE가 드러나는 방식
- [on-call 런북 — 시나리오 1](../admin-guide/oncall-runbook.md#시나리오-1--trivy-db-stale-또는-누락) — 오래된 Trivy DB 복구
- [GitHub Actions](../ci-integration/github-actions.md) · [GitLab CI](../ci-integration/gitlab-ci.md) · [Jenkins](../ci-integration/jenkins.md) · [Webhook](../ci-integration/webhooks.md)
