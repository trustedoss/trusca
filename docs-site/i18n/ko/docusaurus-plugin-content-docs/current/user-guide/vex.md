---
id: vex
title: VEX 문서 — 내보내기와 가져오기
description: 프로젝트의 트리아지를 독립 OpenVEX·CycloneDX VEX 문서로 내보내고, 외부 문서를 가져와 statement를 자동 적용하며, 두 방향 모두 UI에서 수행합니다.
sidebar_label: VEX 문서
sidebar_position: 5
---

# VEX 문서 — 내보내기와 가져오기

VEX(Vulnerability Exploitability eXchange) 문서는 *어떤 CVE가 실제로 제품에
영향을 주는지*를 기록합니다. 포털 안에서 그 기록은 각 결과의
[VEX 상태](./vulnerabilities.md#vex-상태-머신)로 존재합니다. 이 페이지는 그
기록을 **표준 문서**로 경계 밖과 주고받는 방법을 다룹니다 — 트리아지를
다운스트림 소비자를 위해 내보내고, 다른 사람의(또는 이전에 내보낸) 문서를
가져와 결과에 다시 적용합니다.

:::note 대상 독자
공급자·다운스트림과 트리아지를 교환하는 엔지니어. 내보내기는 `developer`
이상, 가져오기는 일괄 트리아지 동작이라 `team_admin` 이 필요합니다.
:::

## VEX 문서 내보내기

CycloneDX **SBOM**에 포함되는 VEX 상태와 별개로, 포털은 프로젝트의 현재 결과
분류(triage)만으로 구성한 **독립 VEX 문서**를 내보낼 수 있습니다. VEX(Vulnerability
Exploitability eXchange) 문서는 다운스트림 소비자에게 *어떤 CVE가 실제로 제품에
영향을 주는지*를 알려줍니다 — 따라서 소비자는 이미 `not_affected`나 `fixed`로
분석한 CVE의 노이즈를 억제할 수 있습니다.

두 가지 포맷을 지원합니다:

| 포맷 | 쿼리 값 (`format=`) | MIME | 용도 |
|---|---|---|---|
| **OpenVEX 0.2.0** | `openvex` | `application/json` | 최소한의 벤더 중립 OpenVEX 스키마. 기본값. |
| **CycloneDX 1.5 VEX** | `cyclonedx` | `application/json` | `vulnerabilities[]` + 분석만 담은 CycloneDX BOM — CycloneDX SBOM과 짝을 이룹니다. |

문서는 **가장 최근에 성공한(latest succeeded)** 스캔의 결과로부터 만들어집니다.
성공한 스캔이 없거나(또는 결과가 없는) 프로젝트라도 다운스트림 도구가 파싱할 수
있도록 유효한 빈 VEX 문서(HTTP 200)를 내보냅니다.

### 상태 매핑

각 내부 VEX 상태는 대상 포맷의 상태 어휘로 매핑됩니다. 분류 중 입력한 자유 텍스트
justification은 자유 텍스트 필드에 그대로 전달됩니다 — OpenVEX `justification`
enum(임의의 분석가 서술로는 추론할 수 없는 정확한 법적 의미를 가짐)에 억지로
끼워 맞추지 **않습니다**.

| 포털 상태 | OpenVEX `status` | CycloneDX `analysis.state` |
|---|---|---|
| **New** | `under_investigation` | `in_triage` |
| **Analyzing** | `under_investigation` | `in_triage` |
| **Exploitable** | `affected` | `exploitable` |
| **Not affected** | `not_affected` | `not_affected` |
| **False positive** | `not_affected` | `false_positive` |
| **Suppressed** | `not_affected` | `not_affected` |
| **Fixed** | `fixed` | `resolved` |

justification 텍스트는 OpenVEX `impact_statement`와 CycloneDX `analysis.detail`에
들어갑니다.

### 바이트 안정 출력

SBOM 내보내기와 마찬가지로 VEX 내보내기는 **바이트 안정(byte-stable)** 합니다.
동일한 스캔을 다시 내보내면 동일한 바이트가 생성되므로 문서를 서명·캐싱하고
릴리스 간에 diff할 수 있습니다. statement는 `(CVE id, purl)` 순으로 정렬되고,
문서 id는 스캔 id에서 결정적으로 파생되며, 타임스탬프는 내보낸 순간이 아니라
스캔의 영속화된 완료 시각을 반영합니다.

### API에서 다운로드

<!-- docs-uat: id=vulns-vex-export-api kind=api auth=admin url=/v1/projects/${PROJECT_ID}/vex?format=openvex expect=status:200 tier=nightly -->
VEX 문서를 API로 내보냅니다:

<!-- docs-uat: id=vulns-api-vex-export kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
# OpenVEX (기본값)
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vex?format=openvex"

# CycloneDX VEX
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vex?format=cyclonedx"
```

`format`은 `openvex` 또는 `cyclonedx`를 받습니다. 파일명은
`vex-<project-slug>.<ext>`.

| 상태 | 의미 |
|---|---|
| `200` | VEX 문서 다운로드. |
| `401` | 미인증 — 유효한 토큰을 제공하세요. |
| `404` | 프로젝트가 없거나, 호출자가 해당 팀의 멤버가 아님(existence-hide, SBOM 내보내기와 동일한 정책). |
| `422` | 알 수 없는 `format` — `openvex` 또는 `cyclonedx`를 사용하세요. |

:::note 접근 권한
VEX 문서 다운로드는 `developer` 이상이 필요합니다. 크로스팀 호출자는 `403`이 아닌
`404`를 받으므로 비멤버는 프로젝트의 존재 여부를 알 수 없습니다.
:::

## VEX 문서 import(소비)

포털은 외부 VEX 문서(OpenVEX 또는 CycloneDX VEX)를 **import**하여 그 statement를
결과에 자동 적용함으로써 분류 노이즈를 억제할 수 있습니다. 이는
[VEX 문서 내보내기](#vex-문서-내보내기)의 역방향입니다 — export는 분류 결과를
표준 문서로 내보내고, import는 (다른 사람의 또는 이전에 내보낸) 문서를 다시
결과에 적용합니다.

대표적 사용 사례:

- 벤더나 상위 메인테이너가 특정 CVE를 자신의 패키지에서 **해당 없음(not affected)**
  으로 선언한 VEX 문서를 발행 → 수동 재분류 대신 import.
- VEX 문서를 export해 다른 도구에서 편집한 뒤 결정을 포털로 되돌리기.
- CI 단계에서 생성한 VEX 문서를 다음 동기화 때 소비하기.

### 권한

VEX import는 **대량 분류** 행위로(업로드 한 번이 다수의 결과를 전이시킬 수 있음)
프로젝트 팀 내 **`team_admin`**이 필요합니다(결과를 `Suppressed`로 전이할 때와
동일한 기준). 팀 멤버인 `developer`는 `403`을, 비멤버는 `404`(존재 은닉, export와
동일한 태도)를 받습니다.

### 매칭 방식

각 VEX statement는 **취약점 id**(CVE/GHSA/OSV 이름) **+ 컴포넌트 purl**로
프로젝트의 **최신 성공 스캔**의 결과와 매칭됩니다. 매칭되는 결과가 없는
statement(해당 스캔에 CVE가 없거나 purl이 일치하지 않음)는 **사유와 함께 skip**되며,
전체 import를 실패시키지 않습니다.

### 상태 매핑 (VEX → 포털)

import는 각 VEX 상태를 하나의 정규 포털 상태로 역매핑합니다:

| OpenVEX `status` | CycloneDX `analysis.state` | 포털 상태 |
|---|---|---|
| `not_affected` | `not_affected` | **Not affected** |
| — | `false_positive` | **False positive** |
| `affected` | `exploitable` | **Exploitable** |
| `fixed` | `resolved` | **Fixed** |
| `under_investigation` | `in_triage` | **Analyzing** |

`under_investigation` / `in_triage`는 `New`가 아닌 **Analyzing**으로 매핑됩니다 —
`New`는 탐지 인박스 상태이며 어떤 것도 `New`로는 전이되지 않습니다.

### 합법 전이 보존

import는 수동 워크플로우와 동일한 [VEX 상태 머신](./vulnerabilities.md#vex-상태-머신)을 따릅니다.
모든 판정이 `Analyzing`을 거치므로, 아직 **New**인 결과에 `not_affected`를
import하면 **합법 2단계 경로** `New → Analyzing → Not affected`가 자동 적용되고,
감사 로그에 **두 단계 모두** 기록됩니다. VEX 문서의 사유(`impact_statement` /
`analysis.detail`)는 결과에 보존됩니다.

### 멱등성 & 왕복

같은 문서를 두 번 import해도 안전합니다: 이미 목표 상태인 결과는 다시 쓰지 않고
**skip**(`already_at_target`)합니다. 분류 결과를 export한 뒤 곧바로 다시 import하면
**no-op**입니다 — 포털의 export/import 왕복은 상태가 안정적입니다.

### API에서 import

<!-- docs-uat: id=vulns-api-vex-import kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -F "upload=@vex.openvex.json;type=application/json" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vex/import"
```

응답은 JSON 요약입니다:

```json
{
  "format": "openvex",
  "matched": 12,
  "applied": 9,
  "skipped": 3,
  "errors": [
    {
      "vulnerability": "CVE-2024-0001",
      "product": "pkg:npm/left-pad@1.0.0",
      "reason": "unknown_component",
      "detail": "CVE-2024-0001 has no finding on pkg:npm/left-pad@1.0.0 in the latest scan"
    }
  ]
}
```

- `matched` — statement가 매칭한 결과 수.
- `applied` — 상태가 실제로 변경된 결과 수.
- `skipped` — 의도적으로 적용하지 않은 결과/statement(no-op, 미지 vuln/purl 등).
- `errors[].reason` — `unknown_vulnerability`, `unknown_component`,
  `ambiguous_match`, `unmapped_status`, `illegal_transition`,
  `already_at_target`, `forbidden_transition`, `malformed_statement` 중 하나.

| 상태 | 의미 |
|---|---|
| `200` | import 실행됨 — 요약 참고(적용 0건이어도 200). |
| `401` | 미인증. |
| `403` | 인증됨, 팀 멤버이나 `team_admin`이 아님. |
| `404` | 프로젝트 없음 또는 호출자가 팀 멤버 아님(존재 은닉). |
| `413` | 업로드 문서가 크기 제한(`VEX_IMPORT_MAX_BYTES`, 기본 8 MiB)을 초과. |
| `422` | 문서가 유효한 JSON이 아니거나 OpenVEX/CycloneDX VEX가 아님. 본문은 `application/problem+json`. |

## UI에서의 VEX

위의 모든 작업은 API 없이도 **취약점 탭** 툴바에서 수행할 수 있습니다.

### 내보내기·가져오기 버튼

- **VEX 내보내기** — **OpenVEX**, **CycloneDX VEX** 두 개의 버튼이 있습니다. 둘 중
  하나를 클릭하면 프로젝트의 현재 트리아지를 독립 VEX 문서로 다운로드합니다.
  다운로드는 SBOM·PDF 보고서와 동일하게 인증 세션을 통해 이루어지며(토큰이 URL에
  노출되지 않음), 읽기 작업이므로 `developer` 이상이면 누구나 사용할 수 있습니다.
- **VEX 가져오기** — OpenVEX 또는 CycloneDX VEX JSON 파일을 선택해 업로드하는
  다이얼로그를 엽니다. 형식은 자동으로 감지됩니다. 가져오기가 끝나면 다이얼로그에
  **일치**(구문이 매칭한 취약점), **적용**(상태가 실제로 변경된 취약점),
  **건너뜀** 세 가지 카운트와, 적용되지 않은 항목에 대한 구문별 건너뜀 사유 목록
  (알 수 없는 CVE/컴포넌트, 허용되지 않는 전이, 이미 대상 상태 등)이 표시됩니다.
  가져오기는 대량 트리아지 작업이므로 버튼은 **`team_admin`(및 `super_admin`)에게만
  활성화**됩니다. `developer`에게는 권한 안내 툴팁과 함께 비활성화되어 표시됩니다.
  서버의 `403`·`413`·`422` 응답은 평이한 문장 메시지로 인라인 표시됩니다.

### 필터: VEX로 억제된 항목만

툴바에는 **VEX로 억제된 항목만** 체크박스가 있습니다. 켜면 현재 페이지에서 상태가
VEX 가져오기로 설정된(`analysis_source = vex_import`) 취약점만 남습니다 — 방금
가져온 문서가 무엇을 바꿨는지 확인할 때 유용합니다. 이 토글은 URL
(`?vex_suppressed=1`)에 반영되어 새로고침해도 유지되고 딥링크로 공유할 수 있습니다.
VEX 가져오기로 설정된 행에는 상태 옆에 작은 **VEX** 배지가 색상이 아닌 레이블과 함께
표시되어 출처를 한눈에 알 수 있습니다.

### 드로어의 출처 배지

가져오기로 상태가 설정된 취약점을 열면 드로어에 **VEX 출처** 패널이 표시됩니다:
소비된 문서의 작성자, ID(`@id` / `serialNumber`), 타임스탬프, 매칭된 구문이 담은 VEX
상태, 가져온 시각, 그리고 가져온 근거가 나옵니다. 이 필드들은 모두 업로드된 문서에서
오며 엄격히 **텍스트**로만 렌더링됩니다 — 포털은 이를 HTML로 해석하지 않으므로,
마크업이 포함된 근거나 작성자도 그대로 표시되며 동작하지 않습니다(inert).

