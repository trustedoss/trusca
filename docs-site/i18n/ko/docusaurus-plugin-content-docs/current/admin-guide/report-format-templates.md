---
id: report-format-templates
title: Report format templates
description: 취약점 PDF/HTML 보고서의 내용은 그대로 두고 조직 머리말·표기·열 선택만 설정합니다.
sidebar_label: Report format templates
sidebar_position: 12
---

# 보고서 서식 템플릿

조직은 [취약점 PDF 보고서](../user-guide/vulnerabilities.md#보고서-다운로드-pdf-또는-excel)에 머리말 한 줄을 추가하고, 기본 브랜드 표기를 바꾸고, 어떤 열을 렌더링할지 좁힐 수 있습니다. 설정한 행이 없으면 보고서는 지금까지와 똑같이 나옵니다.

:::note 대상 독자
`super_admin`이 행을 설정하거나 제거합니다. `developer` 이상은 현재 행을 조회하고, 그 설정이 반영된 보고서를 내려받을 수 있습니다(보고서 다운로드 자체는 그 엔드포인트가 나온 이후 계속 `developer`·`viewer` 권한이었습니다).
:::

## 할 수 있는 것과 할 수 없는 것

조직당 행 하나이며, PDF/HTML 보고서에만 적용됩니다. 별도의 Excel 보고서는 자체적으로 더 넓은 열 구성을 유지하며 이 설정의 영향을 받지 않습니다.

- `header_text`와 `org_label`은 마크업이 아니라 순수 텍스트입니다. `header_text`는 보고서 머리글 아래에 한 줄을 더하고, `org_label`은 "TRUSCA" 브랜드 표기를 대체합니다. 둘 다 보고서가 다른 값에 이미 적용하는 것과 같은 방식으로 HTML 이스케이프됩니다.
- `vulnerability_columns`와 `component_columns`는 각각 고정된 어휘의 **부분집합**입니다. 열을 더 적게 고른다고 해서 열의 순서를 바꾸거나 이름을 바꾸거나 값을 계산하지 않습니다. 지정하지 않은 열을 숨길 뿐이고, 남은 열은 전체 보고서가 이미 쓰는 순서 그대로 나옵니다.
- 취약점 열: `cve`, `cvss`, `summary`, `status`.
- 컴포넌트 열: `name`, `version`, `license`, `severity`, `vulns`.

## 요청 시 열 지정

PDF 엔드포인트는 `vulnerability_columns`·`component_columns`를 반복 쿼리 매개변수로도 받습니다. 값을 주면 **그 요청에 한해서만** 조직의 저장된 기본값을 덮어씁니다. 우선순위는 요청 시 지정, 그다음 조직 기본값, 그다음 전체 열 순서입니다.

<!-- docs-uat: id=report-format-request-override kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerability-report.pdf?vulnerability_columns=cve&vulnerability_columns=status"
```

고정된 어휘 밖의 열 이름은 쿼리 문자열에서 왔든 이 행에 대한 `PUT`에서 왔든 `422 Unprocessable Entity`로 거부됩니다.

## 조직 기본값 설정

<!-- docs-uat: id=report-format-template-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/report-format-templates/org/<organization-uuid>" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"org_label": "Acme Corp", "vulnerability_columns": ["cve", "cvss", "status"]}'
```

모든 필드가 선택 사항이지만 최소 하나는 있어야 합니다. `header_text`·`org_label`·`vulnerability_columns`·`component_columns` 중 아무것도 설정하지 않고 `PUT`하면 `422 Unprocessable Entity`로 거부됩니다. 필요한 것만 설정하면 되고, 나머지는 없는 채로 남습니다.

## 행 조회·제거

<!-- docs-uat: id=report-format-template-get kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -H "Authorization: Bearer ${JWT}" \
  "https://trustedoss.example.com/v1/report-format-templates/org/<organization-uuid>"
```

`404 Not Found`은 조직이 아직 행을 쓰지 않았다는 뜻일 뿐, 오류가 아닙니다. 같은 경로에 `DELETE`를 보내면 제거되고, 다음 요청부터 보고서는 서식 없는 출력으로 돌아갑니다.

## 정상 동작 확인

<!-- docs-uat: id=report-format-template-verify kind=manual tier=manual -->
1. 프로젝트의 PDF 보고서를 내려받아(`GET /v1/projects/{project_id}/vulnerability-report.pdf`) "TRUSCA" 대신 조직 표기가 나오고 선택한 열만 렌더링되는지 확인합니다.

## API 엔드포인트

모든 경로는 `/v1/report-format-templates/org/{organization_id}` 아래에 있습니다.

| 메서드 | 권한 | 설명 |
|---|---|---|
| `PUT` | `super_admin` | 조직의 보고서 서식 행을 생성하거나 교체합니다. |
| `GET` | `developer` 이상 | 조직의 보고서 서식 행을 조회합니다. 없으면 `404`입니다. |
| `DELETE` | `super_admin` | 조직의 보고서 서식 행을 제거합니다. 성공 시 `204`, 없었다면 `404`입니다. |

## 트러블슈팅

### `PUT` 시 `422 Unprocessable Entity`

네 필드 중 아무것도 설정하지 않았거나, 열 목록 중 하나가 빈 배열이거나(설정하지 않고 null로 두세요), 열 목록이 고정된 어휘 밖의 열 이름을 담고 있습니다.

### 보고서 다운로드 시 `422 Unprocessable Entity`

`vulnerability_columns`나 `component_columns` 쿼리 매개변수가 고정된 어휘 밖의 열 이름을 담고 있습니다.

### `PUT`이나 `DELETE`에서 `403 Forbidden`

보고서 서식은 특정 팀이 아니라 배포 전체의 모든 프로젝트 보고서에 적용되는 설정이라, `super_admin`만 쓸 수 있습니다.

### 서식이 보고서에 나타나지 않음

프로젝트가 그 행을 쓴 조직 소속인지 확인하세요. 행은 조직 경계를 넘지 않습니다. PDF/HTML 보고서를 보고 있는지도 확인하세요. Excel 보고서는 자체 열 구성을 쓰며 이 행의 영향을 받지 않습니다.

## 함께 보기

- [Vulnerabilities → PDF/Excel 보고서](../user-guide/vulnerabilities.md#보고서-다운로드-pdf-또는-excel): 이 서식이 적용되는 문서입니다.
