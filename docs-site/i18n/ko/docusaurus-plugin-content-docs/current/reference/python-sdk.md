---
id: python-sdk
title: Python SDK
description: TRUSCA REST API의 공식 Python 클라이언트. 릴리스마다 OpenAPI 스펙에서 새로 생성해 GitHub Release에 wheel로 첨부한다.
sidebar_label: Python SDK
sidebar_position: 5
---

# Python SDK

모든 [릴리스](https://github.com/trustedoss/trusca/releases)는 그 버전의 [OpenAPI 스펙](./api-overview.md)에서 생성한 Python 클라이언트를 wheel과 sdist로 첨부합니다. 아직 PyPI 패키지는 없으며(로드맵), 배포한 인스턴스의 버전에 맞는 wheel을 해당 릴리스 페이지에서 내려받으면 됩니다.

:::note 대상 독자
API를 손으로 `requests` 호출을 짜는 대신 타입이 있는 메서드로 부르고 싶은 Python 엔지니어(CI 연동, 사내 대시보드, 일괄 작업 등).
:::

## `trusca` 저장소에 들어 있지 않은 이유

[OpenAPI Generator](https://openapi-generator.tech/)는 이 API 표면 전체에 대해 약 900개 파일을 만들어냅니다(모델마다 모듈 하나, 태그마다 모듈 하나). 이걸 저장소에 커밋하면 API 모양이 바뀔 때마다 몇천 줄짜리 diff가 생기고, 그 내용은 아무도 손으로 고칠 일이 없는 코드입니다. 대신 릴리스 태그 시점에 새로 생성해 Release에 첨부합니다. 같은 릴리스에 함께 첨부되는 [CycloneDX SBOM](https://github.com/trustedoss/trusca/releases)과 같은 방식으로, `tools/python-sdk/generate.sh`를 `.github/workflows/release.yml`이 그 태그의 OpenAPI 스펙을 재생성한 직후 실행합니다.

## 설치

```bash
pip install trusca_client-2.2.0-py3-none-any.whl
```

wheel의 버전은 배포한 인스턴스의 버전과 맞추세요. 서버가 더 새 버전이면 이전 클라이언트의 모델이 모르는 필드가 추가됐을 수 있고(응답의 여분 필드는 무시되므로 무해합니다), 반대로 어떤 동작의 이름이 바뀌었다면 이전 클라이언트는 옛 이름으로 호출해 404를 받게 됩니다.

## 빠른 시작

[Postman 컬렉션](./postman-collection.md)과 같은 4단계 시나리오입니다. 로그인, 프로젝트 생성, 스캔 트리거, SBOM 다운로드 순서입니다.

```python
import trusca_client
from trusca_client.models.login_request import LoginRequest
from trusca_client.models.project_create import ProjectCreate
from trusca_client.models.scan_create import ScanCreate

configuration = trusca_client.Configuration(host="https://trustedoss.example.com")

with trusca_client.ApiClient(configuration) as api_client:
    auth_api = trusca_client.AuthApi(api_client)
    projects_api = trusca_client.ProjectsApi(api_client)

    # admin@demo.trustedoss.dev는 seed_demo로 시드된 인스턴스에서만 동작합니다.
    # 그 외에는 실제 계정을 쓰세요.
    token = auth_api.login_auth_login_post(
        LoginRequest(email="admin@demo.trustedoss.dev", password="DemoTest2026!")
    )
    configuration.access_token = token.access_token

    project = projects_api.create_project_endpoint_v1_projects_post(
        ProjectCreate(
            team_id="8f0c1e2a-...your team UUID...",
            name="checkout-service",
            slug="checkout-service",
            git_url="https://github.com/acme/checkout-service.git",
        )
    )

    scan = projects_api.trigger_scan_endpoint_v1_projects_project_id_scans_post(
        project_id=project.id, scan_create=ScanCreate(kind="source")
    )
    print(f"scan {scan.id} queued for project {project.id}")
```

API 키(`tos_<prefix>_<secret>` 형식, [API keys](../admin-guide/api-keys.md) 참고)도 같은 방식으로 인증합니다. `login_auth_login_post`를 부르는 대신 `configuration.access_token`에 키 원문을 그대로 넣으면 됩니다.

wheel에 함께 들어 있는 전체 README(`pip show -f trusca_client`로 확인하거나, `.whl`이 zip 파일이니 그대로 열어 봐도 됩니다)에는 SBOM 다운로드 단계와 그때 주의할 점까지 함께 적혀 있습니다.

## 메서드 이름은 스펙의 operation id이지, 사람이 고른 이름이 아닙니다

FastAPI는 각 operation id를 그 동작을 구현한 Python 함수 이름에서 그대로 뽑아냅니다. 그래서 `POST /v1/projects/{project_id}/scans`에 대해 생성기가 본 이름은 `trigger_scan_endpoint_v1_projects_project_id_scans_post`이며, 다듬어진 SDK 메서드 이름이 아닙니다. 각 메서드의 정확한 시그니처와 파라미터가 받는 모델 클래스는 wheel 안의 `docs/` 디렉터리에 엔드포인트별로 문서화돼 있고, 이 역시 같은 스펙에서 생성됩니다.

## 직접 다시 생성하기

```bash
bash tools/python-sdk/generate.sh 2.2.0
```

Node(생성기의 npm 래퍼, `tools/python-sdk/package.json`에 버전 고정), JVM(생성기 자체가 JVM 위에서 동작합니다. 빌드 시점에만 쓰는 도구이며 배포된 제품에는 전혀 들어가지 않습니다), 그리고 마지막 패키징 단계를 위한 `pip install build`가 필요합니다. 마지막 커밋이 아니라 지금 작업 중인 트리에 맞는 클라이언트를 만들고 싶다면 `python scripts/dump_openapi.py`로 `docs-site/static/openapi.json`을 먼저 재생성하세요.

## 함께 보기

- [API 개요](./api-overview.md): 인증, 페이지네이션, 오류 형식.
- [Postman 컬렉션](./postman-collection.md): 코드 한 줄 없이 같은 4단계 시나리오를 실행합니다.
- [API keys](../admin-guide/api-keys.md): `configuration.access_token`에 넣을 자격 증명을 발급하는 방법.
