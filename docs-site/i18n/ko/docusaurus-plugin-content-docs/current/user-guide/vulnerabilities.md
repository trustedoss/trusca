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
- **EPSS** — EPSS 확률을 백분율로 표시(예: `97.3%`). EPSS 값이 없는 CVE는 `—`로 표시됩니다. [EPSS — 악용 확률](#epss--악용-확률) 참고.
- **제목 (Title)** — 권고문의 짧은 요약.
- **영향 (Affected)** — 영향 받는 컴포넌트(`name@version`).
- **상태 (Status)** — 현재 VEX 상태.
- **발견 시각 (Discovered)** — 결과가 처음 등장한 시점.

상단 인라인 필터 바: 심각도, 상태, **EPSS 임계** 필터(`min_epss`), 그리고 **검색** 박스(CVE ID / 제목 / 컴포넌트 자유 텍스트), 정렬·정렬 순서 컨트롤. 정렬 컨트롤에는 **EPSS**(`sort=epss`)가 포함되며, EPSS 값이 없는 행은 마지막으로 정렬됩니다.

## 드로어 — 결과 상세

행을 클릭하면 다음을 봅니다.

- **요약 (Summary)** — 제목, 설명, CWE, CVSS 벡터, 그리고 Dependency-Track이 제공할 때의 **EPSS score와 percentile**(미제공 시 `—`). [EPSS — 악용 확률](#epss--악용-확률) 참고.
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

## EPSS — 악용 확률

포털은 [EPSS(Exploit Prediction Scoring System)](https://www.first.org/epss/) score를 CVSS 옆에 노출해, *심각한* CVE와 *실제 공격받을 가능성이 높은* CVE를 구분할 수 있게 합니다.

### EPSS vs. CVSS — 각각 무엇을 답하나

- **CVSS**는 **심각도**를 측정합니다 — CVE가 악용되었을 때의 이론적 영향. 누군가 실제로 악용하는지, 할 것인지는 말하지 않습니다.
- **EPSS**는 향후 30일 내 **실제 악용 확률**을 `0`~`1` 사이의 숫자로 측정합니다.

둘은 보완 관계입니다. CVSS `9.8`(Critical)인데 EPSS는 `0.01`인 CVE — 문서상으로는 심각하지만 공격받을 예측 확률은 낮은 — 가 흔합니다. EPSS로 정렬·필터하면 *실제로* 위험한 소수의 결과에 집중하고 노이즈를 줄일 수 있습니다.

:::caution EPSS는 best-effort
EPSS 데이터는 Dependency-Track 동기화 중 수집되며 **DT가 EPSS 값을 제공하는 CVE에 한해서만** 존재합니다. EPSS 값이 없는 결과는 UI에서 `—`, API에서 `null`로 표시됩니다 — 누락된 EPSS는 "낮음"이 아니라 "알 수 없음"으로 다루세요. EPSS는 CVSS나 VEX 분류를 대체하지 않으며, 하나의 추가 신호입니다.
:::

### 포털의 EPSS 표시 방식

- **Score** — 백분율로 렌더링. EPSS `0.973`은 `97.3%`로 표시됩니다.
- **Percentile** — "상위 N%"로 렌더링. 99번째 백분위수의 결과는 대략 "상위 1%"로 표시되며, 그 점수가 전체 채점된 CVE의 약 99%보다 높음을 뜻합니다.
- **누락** — `—` (DT가 해당 CVE에 EPSS 값을 제공하지 않음).

score와 percentile은 결과 테이블의 **EPSS** 컬럼과 드로어의 **요약(Summary)** 섹션에 나타납니다.

### EPSS 정렬·필터

- **정렬** — 툴바 정렬 컨트롤에서 **EPSS**를 선택(내림차순이면 가장 악용 가능성 높은 결과가 위로). EPSS 값이 없는 결과는 정렬 순서와 무관하게 항상 마지막으로 정렬됩니다(`NULLS LAST`).
- **필터** — **EPSS 임계**(`min_epss`, `0`~`1` 값)를 설정하면 `epss_score >= min_epss`인 결과만 표시합니다. 예를 들어 `min_epss=0.5`는 모델이 악용 확률 50% 미만으로 예측한 모든 결과를 숨깁니다. EPSS 값이 없는 결과는 임계 필터에서 제외됩니다(누락된 score는 `>=`를 만족할 수 없음).

### API에서 EPSS 읽기

`GET /v1/projects/{id}/vulnerabilities`는 모든 결과에 `epss_score`와 `epss_percentile`을 반환합니다(DT가 값을 제공하지 않으면 둘 다 `null`). 동일한 필드가 결과 상세(`GET /v1/vulnerability_findings/{finding_id}`)와 중첩된 `VulnerabilityRef`에도 나타납니다.

EPSS로 정렬, 높은 순:

```bash
curl -sS \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerabilities?sort=epss&order=desc"
```

모델이 악용 확률 50% 이상으로 예측한 결과만 반환:

```bash
curl -sS \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerabilities?min_epss=0.5"
```

응답의 한 결과는 다음과 같습니다(그 외 필드 생략).

```json
{
  "cve_id": "CVE-2021-44228",
  "severity": "critical",
  "cvss_score": 10.0,
  "epss_score": 0.974,
  "epss_percentile": 0.999,
  "status": "new"
}
```

:::tip EPSS로 빌드 게이팅
EPSS는 CI 빌드 게이트도 구동할 수 있어, Critical이 아니어도 악용 확률이 높은 CVE가 빌드를 실패시킬 수 있습니다. [EPSS로 빌드 게이팅](../ci-integration/github-actions.md#epss로-빌드-게이팅-선택) 참고.
:::

## PDF 보고서 다운로드

포털은 가장 최근 성공 스캔으로부터 프로젝트 단위 **취약점 PDF 보고서**를 렌더링합니다 — 리스크 요약, 심각도·라이선스 분포, 심각도별로 그룹화한 취약점(CVE id와 CVSS 포함), 컴포넌트 목록. 요청 시점에 생성되며 별도로 예약할 배치 작업은 없습니다.

### UI에서 다운로드

1. 프로젝트 열기.
2. **Vulnerabilities** 탭 클릭.
3. 툴바(우측 상단)의 **PDF 보고서 다운로드**를 클릭. 문서가 렌더링되는 동안 버튼에 **생성 중…**이 표시되고, 이어서 다운로드가 시작됩니다.

파일명은 `vulnerability-report-<project>.pdf`. 직전 시도에서 오류가 있으면 버튼 옆에 인라인으로 표시됩니다.

### API에서 다운로드

```bash
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerability-report.pdf"
```

응답은 `application/pdf`이며 `Content-Disposition: attachment`가 설정됩니다(`-OJ` 플래그는 서버가 제공한 파일명으로 저장하도록 curl에 지시). 보고서는 항상 **가장 최근에 성공한 스캔(latest succeeded)** 을 반영합니다 — 특정 과거 스캔 ID로 고정하는 기능은 v2.0.0에서 지원되지 않습니다.

| 상태 | 의미 |
|---|---|
| `200` | PDF 다운로드. |
| `401` | 미인증 — 유효한 토큰을 제공하세요. |
| `404` | 프로젝트가 없거나, 호출자가 해당 팀의 멤버가 아님(existence-hide, SBOM 내보내기와 동일한 정책). |
| `500` | PDF 렌더러 실패; 본문은 `application/problem+json`. 재시도 후 워커 이미지를 확인하세요(트러블슈팅 참고). |

:::note 접근 권한
보고서 다운로드는 `developer` 이상이 필요합니다. 크로스팀 호출자는 `403`이 아닌 `404`를 받으므로 비멤버는 프로젝트의 존재 여부를 알 수 없습니다.
:::

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

### PDF 보고서 다운로드가 `500`을 반환

PDF는 요청 시점에 weasyprint로 렌더링됩니다. `500`(본문은 `application/problem+json`)은 렌더러를 사용할 수 없다는 뜻으로, 대개 백엔드 이미지가 weasyprint 의존성보다 오래된 경우입니다. 백엔드 이미지를 재빌드한 뒤 재시도하세요. 그래도 지속되면 프로젝트 ID와 요청 시각을 담아 이슈를 등록하세요.

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
