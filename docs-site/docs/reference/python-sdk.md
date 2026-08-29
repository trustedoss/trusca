---
id: python-sdk
title: Python SDK
description: The official Python client for the TRUSCA REST API, generated from the OpenAPI spec at every release and attached to the GitHub Release as a wheel.
sidebar_label: Python SDK
sidebar_position: 5
---

# Python SDK

Every [release](https://github.com/trustedoss/trusca/releases) ships a Python client generated from that exact version's [OpenAPI spec](./api-overview.md), attached as a wheel and an sdist. There is no PyPI package yet (roadmap); download the wheel matching your deployment's version from its release page.

:::note Audience
Python engineers scripting against the API (CI glue, internal dashboards, bulk operations) who would rather call typed methods than build `requests` calls by hand.
:::

## Why it isn't in the `trusca` repository

[OpenAPI Generator](https://openapi-generator.tech/) produces roughly 900 files for this API's surface (one module per model, one per tag). Committing that would turn every API-shape change into a multi-thousand-line diff on top of the repository-size cost, for content nobody hand-edits. Instead the SDK is generated fresh at release-tag time and attached to the Release, the same way the release's [CycloneDX SBOM](https://github.com/trustedoss/trusca/releases) is: `tools/python-sdk/generate.sh`, run by `.github/workflows/release.yml` right after the OpenAPI spec is itself regenerated for that tag.

## Install

```bash
pip install trusca_client-2.2.0-py3-none-any.whl
```

Pin the wheel's version to your deployment's version. A newer server may have added fields the older client's models don't know about (harmless, extra response fields are ignored) or renamed an operation (which the older client would call under the old name and get a 404 for).

## Quickstart

The same four-step scenario as the [Postman collection](./postman-collection.md): log in, create a project, trigger a scan, then export its SBOM.

```python
import trusca_client
from trusca_client.models.login_request import LoginRequest
from trusca_client.models.project_create import ProjectCreate
from trusca_client.models.scan_create import ScanCreate

configuration = trusca_client.Configuration(host="https://trustedoss.example.com")

with trusca_client.ApiClient(configuration) as api_client:
    auth_api = trusca_client.AuthApi(api_client)
    projects_api = trusca_client.ProjectsApi(api_client)

    # admin@demo.trustedoss.dev only authenticates against a
    # seed_demo-seeded instance; use a real account otherwise.
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

An API key (`tos_<prefix>_<secret>`, see [API keys](../admin-guide/api-keys.md)) authenticates the same way: set `configuration.access_token` to the raw key instead of calling `login_auth_login_post`.

The full README bundled in the wheel (`pip show -f trusca_client`, or read it straight out of the `.whl`, which is a zip) carries the SBOM-export step and the same caveats documented there.

## Method names are the spec's operation ids, not hand-picked

FastAPI derives each operation id from the Python function name that implements it, so `trigger_scan_endpoint_v1_projects_project_id_scans_post` is what the generator saw for `POST /v1/projects/{project_id}/scans`, not a curated SDK method name. Every method's exact signature and the model class each parameter takes is documented per-endpoint under the wheel's own `docs/` directory, generated from the same spec.

## Regenerating it yourself

```bash
bash tools/python-sdk/generate.sh 2.2.0
```

Requires Node (the generator's npm wrapper, pinned in `tools/python-sdk/package.json`), a JVM (the generator itself runs on one; this is a build-time-only dependency and never runs in the deployed product), and `pip install build` for the final packaging step. Point it at any commit's `docs-site/static/openapi.json` (regenerate that first with `python scripts/dump_openapi.py` if you want the client to match your working tree rather than the last commit).

## See also

- [API overview](./api-overview.md): auth, pagination, error shape.
- [Postman collection](./postman-collection.md): the same four-step scenario without writing any code.
- [API keys](../admin-guide/api-keys.md): issuing the credential `configuration.access_token` holds.
