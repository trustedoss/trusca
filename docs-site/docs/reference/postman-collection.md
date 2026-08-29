---
id: postman-collection
title: Postman collection
description: A ready-to-import Postman collection covering every endpoint, generated from the OpenAPI spec, with a runnable login → project → scan → SBOM scenario.
sidebar_label: Postman collection
sidebar_position: 4
---

# Postman collection

Download: [`trusca.postman_collection.json`](/postman/trusca.postman_collection.json)

Generated from the same [OpenAPI spec](./api-overview.md) as the Swagger UI and Redoc reference, so every one of the API's paths is in here. This is for trying a call without writing a client first, not a replacement for the spec.

:::note Audience
Engineers exploring the API by hand, or building a Postman/Newman-based smoke test before writing real integration code.
:::

## Import it

**Postman**: File → Import → paste the URL above (or the downloaded file) → Import.

**Insomnia**: Insomnia reads Postman v2.1 collections directly. Application menu → Preferences → Data → Import Data → From File.

## Set your environment first

The collection ships two collection variables you fill in before anything else works:

| Variable | What to put there |
|---|---|
| `baseUrl` | Your deployment's URL, no trailing slash (e.g. `https://trustedoss.example.com`). Defaults to that placeholder. |
| `bearerToken` | A JWT access token or a `tos_...` API key, see [API keys](../admin-guide/api-keys.md). Leave empty and run **Login** first (below) to fill it in automatically. |

In Postman: collection name → **Variables** tab → **Current value** column.

## The four-step scenario

Every endpoint is here individually, grouped by tag exactly like Redoc. Four of them are additionally wired into a runnable chain: each one's response feeds the next request via collection variables, so running them top to bottom against a real deployment does something rather than just showing you the shape of a call.

1. **Login** (`auth` folder): the example body is the project's published demo login (`admin@demo.trustedoss.dev`, works against a `seed_demo`-seeded instance). On success, a test script captures `access_token` into `bearerToken`, so every later request authenticates automatically.
2. **Create a project** (`projects` folder): example body is a filled-in `checkout-service` project; you still need to replace `team_id` with a real one (see [Projects](../user-guide/projects.md)). On success, its `id` is captured into `projectId`.
3. **Trigger a scan for the project** (`scans` folder): uses `{{projectId}}` from step 2, body `{"kind": "source"}`. Captures the new scan's `id` into `scanId` (not polled here, a scan runs for minutes; poll `GET /v1/scans/{{scanId}}` yourself, or watch the portal UI).
4. **Export SBOM for the project's latest succeeded scan** (`sbom` folder): uses `{{projectId}}`, `format=cyclonedx-json`. Works once step 3's scan has actually succeeded.

Every other request in the collection needs `{{bearerToken}}` set the same way but has no example values wired to a chain. Fill in its path/query parameters yourself, same as reading the endpoint straight off Redoc.

## Regenerating it

This file is committed for local doc builds; the published docs site regenerates it fresh from the live API on every deploy (`tools/postman/dump_postman_collection.mjs`, run by `.github/workflows/docs.yml` right after the OpenAPI spec). To regenerate it yourself:

```bash
cd tools/postman && npm ci
node tools/postman/dump_postman_collection.mjs
```

## See also

- [API overview](./api-overview.md): auth, pagination, error shape.
- [API keys](../admin-guide/api-keys.md): issuing the credential `bearerToken` holds.
