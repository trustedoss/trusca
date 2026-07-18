---
id: faq
title: FAQ
description: TRUSCA에 대한 자주 묻는 질문 — 설치, 스캔, 에어갭 운영, Trivy 데이터베이스, 라이선스와 정책, 빌드 게이트, CI 연동. 각 항목은 전체 답이 있는 페이지로 연결됩니다.
sidebar_label: FAQ
sidebar_position: 12
---

# 자주 묻는 질문

이 페이지는 새로 도입하는 사용자가 가장 많이 묻는 질문에 답하고, 각 항목의 전체 내용을 다루는 페이지로 연결합니다. 상세 가이드를 대신하는 문서가 아니라 안내 지도이므로, 각 답변 아래의 링크를 따라가세요.

## 시작하기 {#getting-started}

### TRUSCA는 무엇을 하나요? {#what-is-trusca}

프로젝트에서 오픈소스 컴포넌트를 스캔하고, 라이선스를 분류하고, 알려진 취약점(CVE)을 매칭하며, 금지 라이선스나 Critical 취약점이 있으면 CI 빌드를 실패시킬 수 있습니다. 자체 호스팅하는 소프트웨어 구성 분석(SCA) 포털입니다. [소개](../intro.md)와 상용 도구와의 [비교](../comparison.md)를 참고하세요.

### 어떻게 설치하나요? {#install}

지원하는 방식은 단일 호스트에서의 Docker Compose이며, Kubernetes용 Helm 차트도 제공합니다. [설치 → Docker Compose](../installation/docker-compose.md)와 [설치 → Helm](../installation/helm.md)을 참고하세요.

### 가장 빠르게 동작을 확인하는 방법은? {#quickstart}

[Quickstart](../quickstart.md)를 따라가면 스택을 띄우고 프로젝트를 만들어 첫 스캔까지 처음부터 끝까지 실행합니다.

### 어떤 종류의 분석을 실행할 수 있나요? {#analysis-types}

네 가지입니다. 소스 SBOM 스캔, 컨테이너 이미지 스캔, 정책 게이트, 그리고 계획 중인 reachability 신호입니다. [분석 유형](./analysis-types.md) 레퍼런스가 각각이 무엇을 입력받고 산출하는지 한 매트릭스로 정리합니다.

## 스캔 {#scanning}

### 프로젝트를 어떻게 스캔하나요? {#how-to-scan}

프로젝트를 등록한 뒤 소스 스캔(Git URL 또는 업로드한 아카이브)이나 컨테이너 스캔(이미지 참조)을 실행합니다. [스캔](../user-guide/scans.md)을 참고하세요.

### 어떤 언어와 생태계를 지원하나요? {#languages}

컴포넌트 탐지는 cdxgen을 사용하며 30개 이상의 언어·빌드 시스템을 다룹니다. 라이선스 보강은 여러 레지스트리(PyPI, Maven, crates.io, Go, RubyGems, NuGet)에서 declared 라이선스를 추가로 해석합니다. [컴포넌트와 라이선스](../user-guide/components-and-licenses.md)를 참고하세요.

### 스캔은 끝났는데 취약점이 하나도 없습니다 — 결함인가요? {#no-vulns}

대개는 스캔이 실행될 때 Trivy 데이터베이스 다운로드가 아직 끝나지 않은 경우입니다. 데이터베이스가 도착하면 finding이 자동으로 나타나며(재매칭 beat이 기존 SBOM을 다시 스캔), 재스캔은 필요 없습니다. [관리자 → 취약점 데이터](../admin-guide/vulnerability-data.md)와 [온콜 런북 시나리오 1](../admin-guide/oncall-runbook.md)을 참고하세요.

### 스캔은 얼마나 걸리나요? {#scan-duration}

소스 스캔은 cdxgen 순회와 scancode가, 컨테이너 스캔은 이미지 풀 시간이 대부분을 차지합니다. Trivy 매칭 단계 자체는 1초 미만입니다. [스캔 → 평균 소요 시간](../user-guide/scans.md#평균-소요-시간)을 참고하세요.

### 얼마나 자주 스캔해야 하나요? {#scan-frequency}

소스가 바뀔 때(CI를 통해 매 PR/push마다) 스캔하고, 바뀌지 않은 코드의 새 CVE는 재매칭 beat에 맡기세요. [모범 사례 → 스캔 빈도](../best-practices/scan-frequency.md)를 참고하세요.

## 에어갭·오프라인 {#air-gapped}

### TRUSCA를 완전히 오프라인·에어갭으로 운영할 수 있나요? {#offline}

가능합니다. 취약점 매칭은 Trivy의 번들 데이터베이스를 사용하며, 이를 내부에 미러링할 수 있습니다(`TRIVY_DB_REPOSITORY`). 네트워크에 접근하는 기능(핑거프린트 기반 스니펫 매칭, 라이선스 보강 조회)은 게이트로 제어하며 끌 수 있습니다. [관리자 → 취약점 데이터](../admin-guide/vulnerability-data.md)와 [환경 변수](./env-variables.md)를 참고하세요.

### 스캔 중에 네트워크 밖으로 나가는 데이터가 있나요? {#egress}

기본적으로 스캔 데이터는 네트워크 밖으로 나가지 않습니다. 라이선스 보강은 공개 레지스트리에서 declared 라이선스를 가져올 수 있으며, 에어갭 배포에서는 `LICENSE_FETCH_ENABLED=false`로 끌 수 있습니다. [컴포넌트와 라이선스](../user-guide/components-and-licenses.md)와 [환경 변수](./env-variables.md)를 참고하세요.

### 에어갭 설치에서 Trivy 데이터베이스를 어떻게 최신으로 유지하나요? {#airgap-db}

`TRIVY_DB_REPOSITORY`를 내부 미러로 지정하고 자체 일정에 따라 갱신하면, 워커가 갱신 후 기존 SBOM을 다시 매칭합니다. [관리자 → 취약점 데이터](../admin-guide/vulnerability-data.md)와 [모범 사례 → 업그레이드 주기](../best-practices/upgrade-cadence.md#trivy-db)를 참고하세요.

## 라이선스와 정책 {#licenses-policy}

### 라이선스는 어떻게 분류되나요? {#license-tiers}

세 tier로 나뉩니다 — 금지(빌드 차단), 조건부(검토·승인 필요), 허용입니다. [라이선스 정책](./license-policies.md)과 [모범 사례 → 정책 설계](../best-practices/policy-design.md#license-tiers)를 참고하세요.

### 허용 라이선스 목록을 바꿀 수 있나요? {#change-policy}

가능합니다. 팀 단위로 런타임에 라이선스 tier를 올리거나 내릴 수 있으며 재배포가 필요 없습니다. [라이선스 정책 → 동적 게이트 평가](./license-policies.md#동적-게이트-평가)를 참고하세요.

### 컴포넌트 승인 워크플로우는 무엇을 위한 것인가요? {#approvals}

조건부 라이선스를 가진 컴포넌트를 처리하기 위한 것입니다(Pending → Under Review → Approved / Rejected). 승인 판정은 감사를 위해 기록되지만, 그 자체로 빌드를 차단하지는 않습니다. [승인](../user-guide/approvals.md)과 [Triage](../user-guide/triage.md#approval-does-not-gate)를 참고하세요.

### 라이선스 의무사항(예: NOTICE 요건)은 어디에서 오나요? {#obligations}

SPDX 식별자로 키가 붙은 내장 카탈로그에서 오며, 프로젝트별로 표시되고 NOTICE 파일로 내려받을 수 있습니다. [의무사항 카탈로그](./obligation-catalog.md)를 참고하세요.

## 빌드 게이트와 CI {#gate-ci}

### 무엇이 빌드를 실패시키나요? {#gate-fail}

정확히 두 조건입니다. 열려 있는 Critical CVE, 또는 라이선스가 `forbidden` tier로 해석되는 컴포넌트입니다. [Triage → 각 결정이 빌드 게이트에 닿는 지점](../user-guide/triage.md#gate-reach)을 참고하세요.

### Rejected한 컴포넌트가 CI를 막지 않았습니다 — 왜인가요? {#rejected-not-blocked}

설계상 그렇습니다. 게이트는 `forbidden` 라이선스 tier와 열린 Critical CVE만 읽고, 승인 판정은 읽지 않습니다. 라이선스를 차단하려면 그 tier를 `forbidden`으로 올리세요. [Triage → 컴포넌트 승인은 빌드를 게이팅하지 않는다](../user-guide/triage.md#approval-does-not-gate)를 참고하세요.

### Critical은 아니지만 위험도가 높은 CVE로 빌드를 실패시킬 수 있나요? {#epss-gate}

가능합니다. 선택적 EPSS 차원을 추가하면 확률이 높은 CVE가 심각도와 무관하게 실패합니다. [GitHub Actions → EPSS로 빌드 게이팅](../ci-integration/github-actions.md#epss로-빌드-게이팅-선택)을 참고하세요.

### 스캔을 CI에 어떻게 연결하나요? {#ci-wiring}

API 키를 쓰는 REST API를 사용하거나 준비된 CI 템플릿을 사용하세요. [CI 연동 → GitHub Actions](../ci-integration/github-actions.md), [GitLab CI](../ci-integration/gitlab-ci.md), [Jenkins](../ci-integration/jenkins.md)를 참고하세요.

### API 키는 어떻게 발급하나요? {#api-key}

관리자가 API 키 관리 화면에서 생성합니다. [관리자 → API 키](../admin-guide/api-keys.md)를 참고하세요.

## Triage {#triage}

### VEX가 무엇인가요? {#vex}

VEX(Vulnerability Exploitability eXchange)는 CVE가 제품에 실제로 영향을 주는지 기록하는 표준 어휘로, finding이 triage 중에 거치는 상태들입니다. [취약점 → VEX 상태 머신](../user-guide/vulnerabilities.md#vex-상태-머신)을 참고하세요.

### CVE가 우리에게 영향을 주지 않는다고 어떻게 표시하나요? {#not-affected}

finding을 제외 VEX 상태(`Not affected`, `False positive`, `Suppressed`, `Fixed`)로 옮기면 다음 스캔에서 빌드 게이트 카운트에서 빠집니다. [Triage](../user-guide/triage.md)와 [취약점](../user-guide/vulnerabilities.md#vex-상태-머신)을 참고하세요.

### VEX 문서를 생성하거나 가져올 수 있나요? {#vex-export}

가능합니다. VEX는 내보내고 다시 가져올 수 있습니다. [취약점](../user-guide/vulnerabilities.md)을 참고하세요.

## SBOM과 보고서 {#sbom-reports}

### 어떤 SBOM 포맷을 지원하나요? {#sbom-formats}

CycloneDX(JSON/XML)와 SPDX(JSON/Tag-Value)를 지원하며, 선택적으로 정책 주석·정책 필터 수출 프로파일을 제공합니다. [SBOM](../user-guide/sbom.md)을 참고하세요.

### SBOM이 TRUSCA로 서명되었는지 검증할 수 있나요? {#sbom-verify}

가능합니다. 기본 SBOM 수출은 cosign으로 서명되며 오프라인에서 검증할 수 있습니다. 수출 프로파일은 서명되지 않습니다. [SBOM 서명 검증](./sbom-signature-verification.md)을 참고하세요.

## 운영·관리 {#admin-ops}

### 역할에는 무엇이 있나요? {#roles}

Super Admin(배포 전체), Team Admin(팀 설정·팀원), Developer(스캔 실행·결과 조회)입니다. [관리자 → 사용자와 팀](../admin-guide/users-and-teams.md)과 [모범 사례 → 팀 구조](../best-practices/team-structure.md)를 참고하세요.

### 백업과 복원은 어떻게 하나요? {#backup}

기본적으로 매일 자동 백업이 실행되며, 관리자 UI에서 수동 백업·복원도 가능합니다. 마이그레이션은 forward-only이므로 업그레이드 전에는 항상 백업하세요. [관리자 → 백업과 복원](../admin-guide/backup-and-restore.md)과 [모범 사례 → 업그레이드 주기](../best-practices/upgrade-cadence.md)를 참고하세요.

### TRUSCA를 어떻게 업그레이드하나요? {#upgrade}

릴리즈 노트를 읽고, 백업한 뒤 업그레이드를 실행하세요. 마이그레이션은 forward-only로 적용됩니다. [설치 → 업그레이드](../installation/upgrade.md)와 [모범 사례 → 업그레이드 주기](../best-practices/upgrade-cadence.md)를 참고하세요.

### 프로덕션에서 문제가 생겼습니다 — 어디부터 봐야 하나요? {#incident}

[온콜 런북](../admin-guide/oncall-runbook.md)에 흔한 장애(Trivy DB 노후, 백업 실패, 멈춘 스캔, 디스크 압박)의 단계별 복구 절차가 있습니다.

## 함께 보기 {#see-also}

- [Quickstart](../quickstart.md) — 가장 빠른 처음부터 끝까지의 경로
- [분석 유형](./analysis-types.md) — 각 스캔 종류가 산출하는 것
- [Triage](../user-guide/triage.md) — finding이 빌드 게이트 결정이 되는 과정
- [모범 사례](../best-practices/scan-frequency.md) — 운영 결정(빈도·정책·팀·업그레이드)
- [비교](../comparison.md) — TRUSCA와 상용 SCA 도구의 관계
