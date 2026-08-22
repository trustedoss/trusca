---
id: notice-templates
title: NOTICE templates
description: 프로젝트 NOTICE 문서의 라이선스·컴포넌트 내용은 건드리지 않고 머리말과 꼬리말만 덧붙입니다.
sidebar_label: NOTICE templates
sidebar_position: 11
---

# NOTICE 템플릿

조직은 모든 프로젝트의 [NOTICE 귀속 문서](../user-guide/sbom.md#notice-파일)에 순수 텍스트 상용구(배포 안내, 사내 레터헤드, 표준 법무 고지 등)를 덧붙일 수 있습니다. 템플릿을 설정하지 않으면 NOTICE는 지금까지와 똑같이 나옵니다.

:::note 대상 독자
`super_admin`이 템플릿을 설정하거나 제거합니다. `developer` 이상은 현재 템플릿을 조회하고, 그 템플릿이 포함된 NOTICE를 생성할 수 있습니다(NOTICE 생성 자체는 그 엔드포인트가 나온 이후 계속 `developer` 권한이었습니다).
:::

## 템플릿이 할 수 있는 것과 할 수 없는 것

템플릿은 형식(`text`, `markdown`, `html`)마다 조직 전체에 적용되는 머리말 하나, 꼬리말 하나입니다. 마크업이 아니라 순수 텍스트입니다.

- 라이선스·컴포넌트·의무사항 목록 앞뒤로 글을 덧붙일 뿐입니다. 그 목록의 어떤 줄도 제거하거나 순서를 바꾸거나 고칠 수 없습니다.
- 조건문이나 반복문이 없습니다. 특정 라이선스나 컴포넌트에 따라 의무사항을 생략하도록 템플릿을 짤 수 없습니다.
- 텍스트는 NOTICE 렌더러가 다른 모든 값에 이미 적용하는 것과 같은 방식으로 이스케이프됩니다. 마크다운에서는 인라인 활성 문자를 백슬래시로 이스케이프하고, 줄 시작에서 제목·목록·인용문을 열지 못하도록 감싸며, HTML 형식에서는 HTML 이스케이프를 거쳐 `<pre>` 블록 안에 출력됩니다.

## 템플릿 설정

<!-- docs-uat: id=notice-template-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/notice-templates/org/<organization-uuid>/text" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"preface": "Internal distribution only.", "footer": "© Example Corp. All rights reserved."}'
```

`preface`와 `footer`는 각각 선택 사항이지만 최소 하나는 있어야 합니다. 둘 다 없이 `PUT`하면 `422 Unprocessable Entity`로 거부됩니다. 필요한 쪽만 설정하면 되고, 나머지는 없는 채로 남습니다.

조직이 실제로 내려받는 형식에만 템플릿을 쓰세요. HTML NOTICE만 받아 가는 조직이라면 `text`나 `markdown` 템플릿은 쓸 필요가 없습니다.

## 템플릿 조회·제거

<!-- docs-uat: id=notice-template-get kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -H "Authorization: Bearer ${JWT}" \
  "https://trustedoss.example.com/v1/notice-templates/org/<organization-uuid>/text"
```

`404 Not Found`은 그 형식에 조직이 아직 템플릿을 쓰지 않았다는 뜻일 뿐, 오류가 아닙니다. 같은 경로에 `DELETE`를 보내면 제거되고, 다음 요청부터 NOTICE는 템플릿 없는 출력으로 돌아갑니다.

## 정상 동작 확인

<!-- docs-uat: id=notice-template-verify kind=manual tier=manual -->
1. 프로젝트의 NOTICE를 생성해(`GET /v1/projects/{project_id}/notice?format=text`) 머리말이 첫 번째 라이선스 절 앞에, 꼬리말이 마지막 절 뒤에 나오는지 확인합니다.

## API 엔드포인트

모든 경로는 `/v1/notice-templates/org/{organization_id}/{format}` 아래에 있으며, `format`은 `text`, `markdown`, `html` 중 하나입니다.

| 메서드 | 권한 | 설명 |
|---|---|---|
| `PUT` | `super_admin` | 조직의 형식별 템플릿을 생성하거나 교체합니다. |
| `GET` | `developer` 이상 | 조직의 형식별 템플릿을 조회합니다. 없으면 `404`입니다. |
| `DELETE` | `super_admin` | 조직의 형식별 템플릿을 제거합니다. 성공 시 `204`, 없었다면 `404`입니다. |

## 트러블슈팅

### `PUT` 시 `422 Unprocessable Entity`

`preface`와 `footer` 둘 다 설정하지 않았거나, 경로의 `format`이 `text`·`markdown`·`html` 중 하나가 아닙니다.

### `PUT`이나 `DELETE`에서 `403 Forbidden`

NOTICE 템플릿은 특정 팀이 아니라 모든 프로젝트의 NOTICE에 적용되는 상용구라, `super_admin`만 쓸 수 있습니다.

### 템플릿이 NOTICE에 나타나지 않음

NOTICE를 요청한 형식과 같은 형식으로 템플릿을 썼는지 확인하세요. `markdown` 템플릿은 `text`나 `html` 다운로드에는 적용되지 않습니다. 또한 프로젝트가 그 템플릿을 쓴 조직 소속인지 확인하세요. 템플릿은 조직 경계를 넘지 않습니다.

## 함께 보기

- [SBOM → NOTICE file](../user-guide/sbom.md#notice-파일): 이 상용구가 붙는 문서입니다.
