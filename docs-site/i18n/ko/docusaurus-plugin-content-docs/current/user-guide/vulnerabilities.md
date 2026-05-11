---
id: vulnerabilities
title: 취약점
description: TrustedOSS Portal에서 CVE 분류 — VEX 상태 머신, 심각도 모델, 억제 흐름, 재탐지.
sidebar_label: 취약점
sidebar_position: 4
---

# 취약점

**Vulnerabilities** 탭은 스캔 파이프라인이 프로젝트 컴포넌트와 상관시킨 모든 미해결 CVE(Common Vulnerabilities and Exposures)를 나열합니다. 결과는 스캔을 거쳐 영속화됩니다 — CVE가 한번 발견되면 근본 컴포넌트가 제거·업그레이드될 때까지 상태와 분류 노트와 함께 프로젝트 이력에 남습니다.

![프로젝트 상세 — 심각도 필터와 행별 CVE 링크가 있는 Vulnerabilities 탭](/img/screenshots/user-vulns-list.png)

:::note 대상 독자
개별 결과를 분류하는 엔지니어; SLA를 추적하는 보안 리드. VEX 상태 변경은 `developer` 이상; 일괄 억제는 `team_admin`.
:::

## 심각도 모델

| 심각도 | 색상 토큰 | CVSS v3 (일반) | 빌드 게이트 |
|---|---|---|---|
| **Critical** | `#dc2626` | 9.0–10.0 | 종료 코드 1(기본) |
| **High** | `#ea580c` | 7.0–8.9 | 프로젝트별 설정 |
| **Medium** | `#ca8a04` | 4.0–6.9 | 영향 없음 |
| **Low** | `#2563eb` | 0.1–3.9 | 영향 없음 |
| **Info** | `#71717a` | — | 영향 없음 |

기본 정책은 `Critical`에서만 빌드를 실패시킵니다. 프로젝트 소유자는 임계치를 `High`로 낮출 수 있습니다.

## VEX 상태 머신

결과는 [CycloneDX VEX(Vulnerability Exploitability eXchange)](https://cyclonedx.org/capabilities/vex/) 7-state 모델을 따릅니다. 각 결과는 **신규**에서 시작하며 분석가가 분류함에 따라 전환됩니다.

| 상태 | 정의 | 빌드 게이트 |
|---|---|---|
| **신규 (New)** | 막 발견됨; 분류되지 않음. | 카운트. |
| **분석 중 (Analyzing)** | 분류 진행 중. | 카운트. |
| **악용 가능 (Exploitable)** | 이 프로젝트 맥락에서 악용 가능 확인. | 카운트. |
| **해당 없음 (Not affected)** | 컴포넌트는 있으나 취약 코드 경로에 도달 불가. | 제외. |
| **오탐 (False positive)** | 탐지 자체가 잘못됨(예: 잘못된 purl). | 제외. |
| **억제됨 (Suppressed)** | 운영자가 명시적으로 침묵 처리(`not_affected` + 명시적 억제). | 제외. |
| **수정됨 (Fixed)** | 해결됨(컴포넌트 업그레이드 또는 패치 적용). | 제외. |

전환은 행위자, `previous_status`, `new_status`, 필수 사유 메시지와 함께 감사 로그에 기록됩니다.

### 필수 사유

`New` / `Analyzing` 외 상태로 전환할 때마다 자유 텍스트 사유(10자 이상)가 필요합니다. 포털은 사유를 그대로 저장합니다 — 사실 기반으로 작성하세요("lodash를 4.17.21로 업그레이드", "취약 코드 경로는 `dev_only` 모듈에 있음"). 본 텍스트는 CycloneDX VEX 출력에 그대로 노출됩니다.

## 결과 테이블

컬럼:

- **CVE** — CVE-YYYY-NNNN 식별자 (평문 표시; NVD 클릭 이동은 로드맵 항목).
- **심각도 (Severity)** — 색상 배지.
- **CVSS** — 상위 피드의 CVSS v3 숫자 점수.
- **제목 (Title)** — 권고문의 짧은 요약.
- **영향 (Affected)** — 영향 받는 컴포넌트(`name@version`).
- **상태 (Status)** — 현재 VEX 상태.
- **발견 시각 (Discovered)** — 결과가 처음 등장한 시점.

상단 인라인 필터 바: 심각도, 상태, 그리고 **검색** 박스(CVE ID / 제목 / 컴포넌트 자유 텍스트), 정렬·정렬 순서 컨트롤.

## 드로어 — 결과 상세

행을 클릭하면 다음을 봅니다.

- **요약 (Summary)** — 제목, 설명, CWE, CVSS 벡터.
- **참고 자료 (References)** — 벤더 권고, 수정 커밋, 익스플로잇 데이터베이스.
- **영향 (Affected)** — 상위에서 보고한 영향 범위와 본 프로젝트 컴포넌트 버전 강조, 그리고 `fixed_version`(수정이 포함된 상위 버전, 가용 시).
- **분석 (Analysis)** — VEX 상태 전환별 액션 버튼, 현재 상태에서 허용된 전환마다 한 개씩. 대상 상태는 `VulnFindingStatus` (`apps/backend/schemas/vulnerability_detail.py`) 의 초기 상태 `new` 를 제외한 6개입니다: `analyzing` ("Mark in triage"), `exploitable` ("Mark exploitable"), `not_affected` ("Mark not affected"), `false_positive` ("Mark false positive"), `suppressed` ("Mark suppressed"), `fixed` ("Mark fixed"). 초기 상태 `new` 로 진입하는 버튼은 없습니다. 버튼을 클릭하면 사유 입력 다이얼로그가 열리며 제출합니다. `developer` 이상만.
- **이력 (History)** — VEX 상태 전환 타임라인(누가, 언제, 어떤 사유로 상태를 변경했는지).

![취약점 드로어 — VEX 액션 버튼과 사유 입력 텍스트 영역이 있는 Analysis 섹션](/img/screenshots/user-vulns-drawer-vex.png)

### 워크스루 — Vulnerabilities 탭 진입 + 드로어 열기

아래 워크스루는 프로젝트를 열고 **Vulnerabilities** 탭으로 전환한 뒤 첫 번째 행을 클릭해 트리아지 준비가 된 Analysis 섹션이 있는 드로어를 표시합니다.

<video controls width="100%" preload="metadata" poster="/img/walkthroughs/walkthrough-cve-triage.gif">
  <source src="/img/walkthroughs/walkthrough-cve-triage.mp4" type="video/mp4" />
  ![애니메이션 워크스루 — Vulnerabilities 탭 진입 후 finding 드로어 열기](/img/walkthroughs/walkthrough-cve-triage.gif)
</video>

## 재탐지

Dependency-Track이 상위 피드(NVD, OSV, GitHub Advisory)에서 새 CVE를 수신하면 주기 동기화가 모든 프로젝트의 최신 스캔에 대해 재상관을 수행합니다. 새 결과는 자동으로 등장합니다 — 수동 작업 불필요.

CVE 재탐지는 DT가 새 권고문을 미러링할 때 자동으로 일어납니다 — Celery beat의 `dt_findings_resync` 태스크가 다음 번 실행될 때(기본 매시간) 영향을 받는 프로젝트에 새로운 `vulnerability_findings` 행이 생성됩니다. v2.0.0에서는 인앱 배너가 없습니다. 운영자는 `/admin/scans`와 프로젝트별 Vulnerabilities 탭으로 모니터링합니다.

**신규 CVE 알림** 트리거가 활성화되어 있으면([admin 알림](../admin-guide/dt-connector.md#알림) 참고) 담당 팀 또는 워처에게 이메일·Slack·Teams 메시지가 발송됩니다.

## 억제 vs. 해당 없음 vs. 수정됨

자주 혼동되는 부분:

- **해당 없음** — 취약 코드 경로가 실행되지 않음을 확신할 때. 분석가가 파일이나 모듈을 짚을 수 있을 때만 사용. 절제하여 사용.
- **억제됨** — 다른 상태에 맞지 않는 사유로 명시적으로 침묵(예: "내부 보상 통제 적용"). 더 절제하여 사용; 사유에 만료일을 명시하는 것을 권장.
- **수정됨** — 컴포넌트 업그레이드·패치 적용; 다음 스캔에서 (아마도) 확인. 다음 스캔이 결과를 더 이상 보고하지 않으면 포털이 `Fixed`를 자동으로 closed로 승격합니다.

## 정상 동작 확인

분류 후:

1. status 배지가 테이블에서 즉시 갱신.
2. 감사 로그에 `target_table=vulnerability_findings&action=update`가 `previous_status`, `new_status`, `justification`을 diff에 담아 기록.
3. 제외된 결과는 프로젝트 리스크 점수에서 카운트되지 않음.
4. 다음 스캔의 빌드 게이트에서 제외된 결과는 제외.

## 트러블슈팅

### 억제 후 결과가 다시 나타남

다음 스캔 후 `New`로 돌아오는 결과는 보통 **프로젝트** 수준이 아닌 **스캔** 수준에서 억제된 경우입니다. 포털은 억제를 프로젝트·컴포넌트·CVE 트리플에 고정합니다 — 억제 메타데이터 일치 여부를 다시 확인하세요.

### 스캔 간 심각도 변경

상위 피드는 가끔 CVE를 재점수화합니다(NVD 분석가 검토, 벤더 권고). 포털은 스캔 시점 심각도를 저장하고 다음 동기화에서 갱신합니다. 두 값이 다르면 드로어가 둘 다 보여줍니다.

### 보고서에서 CVE 누락

가능 원인:

- 컴포넌트의 `purl`이 Dependency-Track의 정규화와 일치하지 않음(드물지만 Maven `groupId:artifactId` 스타일이 가장 흔한 원인). 스캔 보고서와 함께 이슈를 등록.
- 스캔 실행 시 DT가 사용 불가했고 캐시에 해당 CVE 항목이 아직 없음. DT가 healthy해진 후 새 스캔 실행.
- DT가 아직 ingest하지 않는 생태계의 CVE. **/admin/dt → Vulnerability sources** 확인.

## 로드맵 (v2.x)

매뉴얼이 이전에 약속했으나 v2.0.0에 포함되지 않은 항목.

- 결과 테이블의 "마지막 확인 (Last seen)" 컬럼(결과를 마지막으로 확인한 스캔) — v2.1 예정.
- 결과 툴바의 컴포넌트 단위 필터와 발견 일자 범위 필터 — v2.1 예정. 현재는 검색 박스가 컴포넌트 조회를 대체합니다.
- 별도의 **수정 가용성** 드로어 섹션 — v2.0.0에서는 수정 버전이 **영향** 섹션 안의 `fixed_version`으로 노출됩니다.

## 함께 보기

- [컴포넌트·라이선스](./components-and-licenses.md)
- [승인](./approvals.md)
- [DT 커넥터](../admin-guide/dt-connector.md)
- [GitHub Actions — CVE 게이팅](../ci-integration/github-actions.md)
