---
id: postman-collection
title: Postman 컬렉션
description: OpenAPI 스펙에서 생성한, 바로 임포트해 쓸 수 있는 Postman 컬렉션. 로그인 → 프로젝트 생성 → 스캔 트리거 → SBOM 다운로드로 이어지는 실행 가능한 시나리오를 포함한다.
sidebar_label: Postman 컬렉션
sidebar_position: 4
---

# Postman 컬렉션

다운로드: [`trusca.postman_collection.json`](/postman/trusca.postman_collection.json)

Swagger UI, Redoc 레퍼런스와 같은 [OpenAPI 스펙](./api-overview.md)에서 생성하므로 API의 모든 경로가 여기에 들어 있습니다. 클라이언트 코드를 짜기 전에 호출을 한번 시험해 보기 위한 것이지, 스펙 자체를 대신하지는 않습니다.

:::note 대상 독자
API를 손으로 직접 눌러 보며 탐색하는 엔지니어, 또는 실제 통합 코드를 작성하기 전에 Postman이나 Newman 기반 스모크 테스트를 만들려는 엔지니어.
:::

## 임포트하기

**Postman**: File → Import → 위 다운로드 링크의 URL을 붙여넣거나(또는 내려받은 파일을 선택) → Import.

**Insomnia**: Insomnia는 Postman v2.1 컬렉션을 그대로 읽습니다. Application 메뉴 → Preferences → Data → Import Data → From File.

## 먼저 환경 변수부터 채우기

컬렉션에는 다른 무엇보다 먼저 채워야 하는 컬렉션 변수 두 개가 있습니다.

| 변수 | 채울 값 |
|---|---|
| `baseUrl` | 배포된 인스턴스의 URL, 끝의 슬래시는 뺍니다(예: `https://trustedoss.example.com`). 기본값은 이 자리표시자입니다. |
| `bearerToken` | JWT 액세스 토큰 또는 `tos_...` 형식의 API 키입니다. 발급 방법은 [API keys](../admin-guide/api-keys.md)를 참고하세요. 비워 두고 아래의 Login 요청을 먼저 실행하면 자동으로 채워집니다. |

Postman에서는 컬렉션 이름 → **Variables** 탭 → **Current value** 열에서 값을 넣습니다.

## 4단계 시나리오

모든 엔드포인트가 Redoc과 똑같이 태그별로 개별 폴더에 들어 있습니다. 그중 4개는 실행 가능한 흐름으로 서로 연결돼 있어서, 각 요청의 응답이 컬렉션 변수를 통해 다음 요청으로 이어집니다. 위에서 아래로 순서대로 실행하면 실제 배포 인스턴스에 대해 호출의 모양만 보여 주는 게 아니라 실제로 동작합니다.

1. **Login**(`auth` 폴더): 예시 본문은 이 프로젝트가 공개 문서에 이미 게시해 둔 데모 로그인(`admin@demo.trustedoss.dev`)이며, `seed_demo`로 시드된 인스턴스에서 동작합니다. 성공하면 테스트 스크립트가 응답의 `access_token`을 `bearerToken`에 저장하므로, 이후 요청들은 자동으로 인증됩니다.
2. **Create a project**(`projects` 폴더): 예시 본문은 `checkout-service`라는 프로젝트를 채워 둔 형태이고, `team_id`만은 실제 값으로 바꿔야 합니다([Projects](../user-guide/projects.md) 참고). 성공하면 응답의 `id`가 `projectId`에 저장됩니다.
3. **Trigger a scan for the project**(`scans` 폴더): 2단계의 `{{projectId}}`를 사용하고, 본문은 `{"kind": "source"}`입니다. 새로 생성된 스캔의 `id`를 `scanId`에 저장합니다(이 컬렉션 안에서 폴링까지 하지는 않습니다. 스캔은 몇 분 단위로 걸리므로, 직접 `GET /v1/scans/{{scanId}}`를 폴링하거나 포털 UI에서 지켜보세요).
4. **Export SBOM for the project's latest succeeded scan**(`sbom` 폴더): `{{projectId}}`와 `format=cyclonedx-json`을 사용합니다. 3단계의 스캔이 실제로 성공한 뒤에야 동작합니다.

컬렉션의 다른 모든 요청도 같은 방식으로 `{{bearerToken}}`을 채워야 인증되지만, 흐름에 연결된 예시 값은 없습니다. Redoc에서 스펙을 읽을 때와 마찬가지로 경로·쿼리 파라미터는 직접 채워야 합니다.

## 다시 생성하기

이 파일은 로컬 문서 빌드를 위해 저장소에 커밋돼 있고, 실제 배포되는 문서 사이트는 매 배포 시점마다 살아 있는 API에서 새로 생성합니다(`tools/postman/dump_postman_collection.mjs`를 `.github/workflows/docs.yml`이 OpenAPI 스펙 재생성 직후에 실행합니다). 직접 다시 생성하려면 다음과 같이 합니다.

```bash
cd tools/postman && npm ci
node tools/postman/dump_postman_collection.mjs
```

## 함께 보기

- [API 개요](./api-overview.md): 인증, 페이지네이션, 오류 형식.
- [API keys](../admin-guide/api-keys.md): `bearerToken`에 넣을 자격 증명을 발급하는 방법.
