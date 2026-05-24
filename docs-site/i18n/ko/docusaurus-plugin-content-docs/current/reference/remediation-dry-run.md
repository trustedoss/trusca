---
id: remediation-dry-run
title: 리메디에이션 드라이런 (npm)
description: 프로젝트의 취약한 npm 의존성에 대한 package.json 버전 상향 편집을 미리보기 — PR 없음, 저장 없음.
sidebar_label: 리메디에이션 드라이런
sidebar_position: 5
---

# 리메디에이션 드라이런 (npm)

리메디에이션 드라이런은 프로젝트의 취약점 결과를 구체적이고 검토 가능한 **`package.json` 편집**으로 변환합니다. 취약한 npm 의존성마다 최소 안전 업그레이드 버전을 계산하고 변경될 정확한 라인을 보여줍니다. 풀 리퀘스트를 열지 **않으며** 아무것도 저장하지 **않습니다** — 자동 PR 생성(추후 기능)이 동작하기 전에 검토할 수 있는 미리보기입니다.

:::note 대상 독자
npm 프로젝트의 의존성 리메디에이션을 미리 보려는 개발자 및 CI 통합 담당자.
:::

## 엔드포인트

```
POST /v1/projects/{project_id}/remediation/npm/dry-run
```

인증이 필요합니다(JWT 또는 API 키). 호출자는 프로젝트 팀의 멤버(역할 ≥ developer)여야 합니다. 볼 수 없는 프로젝트는 `404`를 반환합니다(존재 은닉).

### 요청 본문 (선택)

```json
{
  "manifest": "{\n  \"dependencies\": { \"lodash\": \"^4.17.20\" }\n}\n"
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `manifest` | string \| null | 편집할 `package.json` 원문. 생략하면 프로젝트의 최신 **보존된 스캔 소스**에서 매니페스트를 최선 노력으로 읽습니다. 소스가 보존되지 않았거나 정리된 경우 직접 제공하세요. |

### 응답

```json
{
  "project_id": "…",
  "scan_id": "…",
  "ecosystem": "npm",
  "manifest_source": "preserved_source",
  "manifest_found": true,
  "changed": true,
  "edited_manifest": "{\n  \"dependencies\": { \"lodash\": \"^4.17.21\" }\n}\n",
  "recommendations": [
    { "package": "lodash", "current_version": "4.17.20", "recommended_version": "4.17.21" }
  ],
  "changes": [
    { "package": "lodash", "section": "dependencies", "before": "^4.17.20", "after": "^4.17.21", "changed": true }
  ],
  "warnings": [
    { "code": "lockfile_regeneration_required", "package": null, "detail": "run `npm install` to regenerate package-lock.json" }
  ],
  "notes": []
}
```

`manifest_source`는 `override`(직접 제공), `preserved_source`(최신 스캔에서 읽음), 또는 `none`(사용 불가 — 이때 `manifest_found`는 `false`)입니다. `edited_manifest`는 `changed`가 `true`일 때만 존재합니다.

## 범위 재작성 정책

드라이런은 **범위 연산자 스타일을 보존**하고 버전 번호만 다시 씁니다:

| 기존 범위 | 결과 (타깃 `1.3.0`) |
| --- | --- |
| `^1.2.3` (캐럿) | `^1.3.0` |
| `~1.2.3` (틸드) | `~1.3.0` |
| `1.2.3` (고정) | `1.3.0` |
| `>=1.2.3` (단일 비교) | `>=1.3.0` |
| `v1.2.3` | `v1.3.0` |
| `1.2.x` / `1.x` | `^1.3.0` (캐럿으로 확장) |
| `*` / `""` / `latest` | 변경 없음 (이미 수정본 허용) |
| `npm:alias@…` / `file:` / `git+…` / 복합 (`>=1 <2`, `||`) | 변경 없음 + 플래그 |

타깃을 이미 충족하는 범위(하한이 수정본 이상)는 그대로 두고 `already_satisfied`로 플래그합니다. 추천이 있는 패키지만 편집되고 나머지는 바이트 단위로 보존되어, 추후 PR diff가 최소화됩니다.

## 락파일

드라이런은 **`package-lock.json`을 절대 편집하지 않습니다** — 무결성 해시를 수동으로 쓰지 않습니다. 매니페스트가 변경되면 응답에 항상 `lockfile_regeneration_required` 경고가 포함됩니다. 병합 전에 `npm install`로 락파일을 재생성하세요.

## 경고

| 코드 | 의미 |
| --- | --- |
| `lockfile_regeneration_required` | 매니페스트가 변경됨; 락파일 재생성 필요 |
| `package_not_present` | 추천된 패키지가 어떤 의존성 섹션에도 없음 |
| `value_not_string` | 버전 값이 문자열이 아님(배열/숫자/null) — 건너뜀 |
| `unparseable_range` | 범위가 와일드카드/별칭/복합/비레지스트리 소스 — 변경 안 함 |
| `already_satisfied` | 기존 범위가 이미 수정본을 충족 — 상향 없음 |
| `target_unparseable` | 추천 버전이 파싱되지 않음 — 건너뜀 |
| `duplicate_keys_collapsed` | 매니페스트에 중복 키 존재(JSON 사양에 따라 마지막 값 사용) |

## 오류

모든 오류는 RFC 7807 `application/problem+json`입니다:

- `401` — 인증 필요.
- `404` — 프로젝트를 찾을 수 없거나 접근 불가.
- `422` — 제공/조회한 `package.json`을 편집할 수 없음(잘못된 JSON, 객체가 아닌 루트, 의존성 섹션 없음, 크기 초과).
