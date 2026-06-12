---
id: dashboard
title: 대시보드
description: TRUSCA 로그인 직후 착지 화면 — 포트폴리오 심각도·라이선스 혼합·스캔 큐 상태·최근 실행 한눈에 보기.
sidebar_label: 대시보드
sidebar_position: 0
---

# 대시보드

로그인하면 포털은 **대시보드**(`/`)로 이동합니다 — 사용자가 볼 수 있는 포트폴리오(활성 팀 소속 프로젝트 전체)를 한 페이지로 요약합니다.

![대시보드 — 로그인 직후 루트 URL 의 KPI 타일, 심각도·라이선스 분포, 최근 스캔 목록](/img/screenshots/user-dashboard.png)

이 페이지는 사용자가 보통 가지고 들어오는 네 가지 질문에 답하기 위해 존재합니다.

- *새로운 Critical 이 있나?* (심각도 타일)
- *내가 담당해야 할 프로젝트가 몇 개고, 그중 진행 중은?* (포트폴리오·스캔 상태 타일)
- *라이선스 혼합이 변하고 있나?* (라이선스 바)
- *최근에 무엇이 실행되었나?* (최근 스캔 목록)

:::note 대상 독자
로그인된 모든 사용자. 데이터 범위는 팀 멤버십을 따릅니다 — 소속되지 않은 팀의 프로젝트는 합산되지 않습니다. super-admin 은 모든 팀의 데이터를 봅니다.
:::

## 페이지 구성

대시보드는 위→아래로 네 개 밴드를 렌더합니다.

1. **Vulnerabilities by severity (심각도별 취약점)** — Critical / High / Medium / Low / Info 다섯 타일. 사용자가 볼 수 있는 모든 프로젝트의 열린 finding 합계를 표시. VEX 상태가 `Not affected` / `False positive` / `Fixed` / `Suppressed` 인 finding 은 제외 — [빌드 게이트](./projects.md#build-gate-verdict-overview-tab) 와 같은 제외 규칙.
2. **Portfolio (포트폴리오)** — 여섯 타일: 프로젝트 수, 대기 중 승인 수, 그리고 네 가지 스캔 상태 카운트(Queued / Running / Succeeded / Failed) 의 포트폴리오 합산.
3. **License classification (라이선스 분류)** — 네 티어(Permissive / Conditional / Prohibited / Unknown)의 수평 바와 그 아래 티어별 카운트 범례.
4. **Recent scans (최근 스캔)** — 포트폴리오 전체의 가장 최근 스캔 행. 각 행은 프로젝트 상세로 이동합니다. 각 행은 프로젝트 이름, 릴리스 태그(릴리스 스냅샷이 기록된 경우), 스캔 종류(`source` / `container`), 상태 배지, 상대 시간을 포함합니다.

페이지는 백엔드 `/v1/dashboard/summary` 엔드포인트를 폴링하며 첫 응답 대기 동안 스켈레톤을 표시합니다. 이후 새로고침은 캐시된 응답을 사용하고 백그라운드로 refetch 합니다.

## 빈 상태

프로젝트가 하나도 없는 새 배포에서는 0 으로 채운 타일 대신 가운데 정렬된 CTA("No projects yet — register your first project to start scanning…")가 표시됩니다. **Register project** 버튼을 클릭하면 `/projects/new` 로 이동합니다.

## 에러 상태

대시보드 엔드포인트가 비-2xx 응답을 반환하면 페이지는 타일 영역을 인라인 에러("Couldn't load the dashboard. Please try again.")와 재시도 컨트롤로 교체합니다. 최근 스캔 목록과 나머지 내비게이션은 그대로 동작합니다 — 에러는 요약 위젯에 한정됩니다.

## 정상 동작 확인

처음 로그인한 뒤:

<!-- docs-uat: id=dashboard-active-in-nav kind=ui harness=dashboardActiveInNav tier=nightly -->
1. 헤더 아바타에 이니셜이 표시되고 사이드바에 **Dashboard** 가 강조됩니다.
<!-- docs-uat: id=dashboard-severity-tiles kind=ui harness=dashboardSeverityTiles tier=nightly -->
2. 심각도 타일 다섯 개에 값이 표시됩니다(0 도 정상).
<!-- docs-uat: id=dashboard-recent-scans kind=ui harness=dashboardRecentScans tier=nightly -->
3. 최근 스캔 목록에 최소 한 줄이 나오거나 비어 있을 때는 "No scans have run yet." 빈 상태 메시지가 표시됩니다.

## 함께 보기

- [프로젝트](./projects.md) — 최근 스캔 행에서 단일 프로젝트로 진입.
- [스캔](./scans.md) — 스캔 상태 타일이 미러링하는 글로벌 큐 화면.
- [승인](./approvals.md) — Pending-approvals 타일이 가리키는 큐.
