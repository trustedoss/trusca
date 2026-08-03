---
id: dashboard
title: 대시보드
description: TRUSCA 로그인 직후 착지 화면 — 포트폴리오 심각도·라이선스 혼합·스캔 큐 상태·최근 실행 한눈에 보기.
sidebar_label: 대시보드
sidebar_position: 0
---

# 대시보드

로그인하면 포털은 **대시보드**(`/`)로 이동합니다 — 사용자가 볼 수 있는 포트폴리오를 한 페이지로 요약합니다 — 소속 팀으로 접근할 수 있는 모든 프로젝트입니다.

:::note 팀을 바꿔도 이 페이지는 좁혀지지 않습니다
글로벌 바의 팀 컨트롤은 지금 어느 팀으로서 작업하는지를 기록합니다. 새 프로젝트가 그 팀 아래 생성되지만, 보이는 내용을 걸러 내지는 않습니다. 대시보드와 프로젝트 목록은 어떤 팀을 선택하든 소속으로 닿는 범위 전부를 보여 줍니다. 화면을 좁히는 일은 각 화면의 필터로 명시적으로 합니다.
:::

![대시보드 — 로그인 직후 루트 URL의 KPI 타일, 심각도·라이선스 분포, 최근 스캔 목록](/img/screenshots/user-dashboard.png)

이 페이지는 사용자가 보통 가지고 들어오는 네 가지 질문에 답하기 위해 존재합니다.

- *새로운 Critical이 있나?* (심각도 타일)
- *내가 담당해야 할 프로젝트가 몇 개고, 그중 진행 중은?* (포트폴리오·스캔 상태 타일)
- *라이선스 혼합이 변하고 있나?* (라이선스 바)
- *최근에 무엇이 실행되었나?* (최근 스캔 목록)

:::note 대상 독자
로그인된 모든 사용자. 데이터 범위는 팀 멤버십을 따릅니다 — 소속되지 않은 팀의 프로젝트는 합산되지 않습니다. super-admin은 모든 팀의 데이터를 봅니다.
:::

## 페이지 구성

대시보드는 위→아래로 다음 밴드를 렌더합니다. 앞의 세 밴드는 한 번 실행하고 끝나는 스캔 보고서가 만들 수 없는 화면입니다. 계정과 팀, 그리고 스캔이 끝난 뒤에도 남는 이력이 있어야 성립하기 때문입니다.

1. **Needs attention (확인이 필요한 일)** — 액션 큐. 아무도 검토하지 않은 승인, CISA 기한과 대조한 KEV finding, 빌드 게이트가 막고 있는 프로젝트, 14일간 성공한 스캔이 없는 프로젝트. 각 타일은 그 일을 처리하는 페이지로 이동하고, 차단·방치된 프로젝트는 아래에 이름으로 나열됩니다. 타일은 그 숫자가 문제를 뜻할 때만 색이 들어오며, 색을 보지 못해도 숫자와 설명이 같은 상태를 전달합니다. 큐를 불러오지 못하면 빈 큐 대신 실패했다고 밝힙니다. "처리할 일이 없다"와 "확인하지 못했다"는 다른 답입니다.
2. **Risk over time (기간별 위험 추이)** — 7·30·90일 창의 작은 차트 세 개. *미해결 critical*과 *미해결 KEV*는 잔량입니다. 그날 기준 각 프로젝트의 최신 성공 스캔에서 읽습니다. *신규 대비 해소*는 흐름입니다. 같은 프로젝트·브랜치·스캔 종류의 직전 스캔과 비교해 그 스캔이 연 것과 닫은 것을 셉니다. 스캔이 없는 날은 잔량을 그대로 잇기 때문에 선이 평평한 것은 변화가 없었다는 뜻일 수도, 아무도 확인하지 않았다는 뜻일 수도 있습니다. 그날 스캔이 몇 건 있었는지로 어느 쪽인지 알립니다. 비교할 직전 스캔이 없는 스캔은 흐름에 아무것도 더하지 않습니다. 스캔 보관 정책이 대체된 스냅샷을 실제로 지우므로, "기록에 없음"이 "이전에 없었음"을 뜻하지 않기 때문입니다.
3. **Portfolio by team (팀별 포트폴리오)** — 볼 수 있는 모든 프로젝트를 소유 팀별로 묶어 위험이 큰 순으로 배열합니다. 셀은 가장 높은 심각도로 색을 입히고 그 등급의 건수를 함께 적습니다. 스캔한 적 없는 프로젝트는 깨끗한 프로젝트와 다르게 그립니다. 숫자는 같지만 뜻이 다르기 때문입니다. 격자는 팀당 셀 수와 팀 수에 상한을 두고, 무엇을 뺐는지 밝힙니다. 나머지는 프로젝트 목록에서 봅니다.
4. **Vulnerabilities by severity (심각도별 취약점)** — Critical / High / Medium / Low / Info 다섯 타일. 사용자가 볼 수 있는 모든 프로젝트의 열린 finding 합계를 표시. VEX 상태가 `Not affected` / `False positive` / `Fixed` / `Suppressed` 인 finding은 제외 — [빌드 게이트](./projects.md#build-gate-verdict-overview-tab)와 같은 제외 규칙.
5. **Portfolio (포트폴리오)** — 여섯 타일: 프로젝트 수, 대기 중 승인 수, 그리고 네 가지 스캔 상태 카운트(Queued / Running / Succeeded / Failed)의 포트폴리오 합산.
6. **License classification (라이선스 분류)** — 네 티어(Permissive / Conditional / Prohibited / Unknown)의 수평 바와 그 아래 티어별 카운트 범례.
7. **Recent scans (최근 스캔)** — 포트폴리오 전체의 가장 최근 스캔 행. 각 행은 프로젝트 상세로 이동합니다. 각 행은 프로젝트 이름, 릴리스 태그(릴리스 스냅샷이 기록된 경우), 스캔 종류(`source` / `container`), 상태 배지, 상대 시간을 포함합니다.

밴드들은 엔드포인트 네 개를 읽습니다 — `/v1/dashboard/action-queue`, `/v1/dashboard/trends`, `/v1/dashboard/portfolio`, `/v1/dashboard/summary`. 모두 서버에서 소속 팀이 닿는 프로젝트로 스코프가 제한됩니다. 첫 응답을 기다리는 동안에는 스켈레톤을 표시하고, 이후 새로고침은 캐시된 응답을 쓰면서 백그라운드로 refetch 합니다.

## 전역 검색 (⌘K)

**⌘K**(macOS) / **Ctrl+K**(Windows·Linux)를 누르거나 헤더의 검색 상자를 클릭하면 앱 어디서든 커맨드 팔레트가 열립니다. 접근 가능한 모든 프로젝트를 가로질러 검색하며 — 서버에서 소속 팀으로 스코프가 제한되어 다른 팀의 데이터는 결과에 포함되지 않습니다 — 네 그룹으로 나뉩니다:

- **Projects** — 이름으로 프로젝트로 이동.
- **Pages** — 상위 페이지(Dashboard·Scans·Policies 등)로 이동.
- **Components** — 접근 가능한 프로젝트에서 패키지를 이름이나 purl로 검색; 결과를 선택하면 해당 프로젝트의 **Components** 탭이 그 검색어로 필터링되어 열립니다.
- **CVEs** — 취약점을 CVE id로 검색; 결과를 선택하면 해당 프로젝트의 **Vulnerabilities** 탭이 그것으로 필터링되어 열립니다.

Components·CVEs 그룹은 입력하는 대로(두 글자부터, 디바운스) 질의하고, Projects·Pages는 즉시 매칭됩니다. 모두 키보드로 조작할 수 있습니다 — 화살표로 이동, Enter로 열기, Esc로 닫기.

## 빈 상태

프로젝트가 하나도 없는 새 배포에서는 0으로 채운 타일 대신 가운데 정렬된 CTA("No projects yet — register your first project to start scanning…")가 표시됩니다. **Register project** 버튼을 클릭하면 `/projects/new`로 이동합니다.

## 에러 상태

대시보드 엔드포인트가 비-2xx 응답을 반환하면 페이지는 타일 영역을 인라인 에러("Couldn't load the dashboard. Please try again.")와 재시도 컨트롤로 교체합니다. 최근 스캔 목록과 나머지 내비게이션은 그대로 동작합니다 — 에러는 요약 위젯에 한정됩니다.

## 정상 동작 확인

처음 로그인한 뒤:

<!-- docs-uat: id=dashboard-active-in-nav kind=ui harness=dashboardActiveInNav tier=nightly -->
1. 헤더 아바타에 이니셜이 표시되고 사이드바에 **Dashboard**가 강조됩니다.
<!-- docs-uat: id=dashboard-severity-tiles kind=ui harness=dashboardSeverityTiles tier=nightly -->
2. 심각도 타일 다섯 개에 값이 표시됩니다(0 도 정상).
<!-- docs-uat: id=dashboard-recent-scans kind=ui harness=dashboardRecentScans tier=nightly -->
3. 최근 스캔 목록에 최소 한 줄이 나오거나 비어 있을 때는 "No scans have run yet." 빈 상태 메시지가 표시됩니다.

## 함께 보기

- [프로젝트](./projects.md) — 최근 스캔 행에서 단일 프로젝트로 진입.
- [스캔](./scans.md) — 스캔 상태 타일이 미러링하는 글로벌 큐 화면.
- [승인](./approvals.md) — Pending-approvals 타일이 가리키는 큐.
