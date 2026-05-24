---
id: license-policies
title: License policies
description: Per-team and per-organization dynamic license policy — override categories, exceptions, and gate posture, with the REST API.
sidebar_label: License policies
sidebar_position: 5
---

# License policies

A **license policy** lets a team (or the whole organization) customise how SPDX
license identifiers map to the risk categories `allowed`, `conditional`, and
`forbidden`. Without a policy the portal classifies licenses against a fixed,
built-in catalog. A policy makes that classification *data* you can edit at
runtime — no redeploy.

:::note Status
This release ships the policy **data model + CRUD API**. Wiring the build-gate
and scan classification to consult these policies (including compound-SPDX
resolution) lands in a follow-up. Until then a policy is stored and readable but
does not yet change gate verdicts.
:::

## Scopes

A policy exists at exactly one of two scopes:

| Scope | `team_id` | Applies to | Who can write |
| --- | --- | --- | --- |
| **Team** | set | that one team | `team_admin` of the team, or `super_admin` |
| **Org default** | `null` | every team in the org with no team policy | `super_admin` only |

At most one org-default policy exists per organization, and at most one policy
per team. Re-`PUT`ting a scope **updates** the existing row (idempotent upsert).

### Effective policy resolution

When a team is evaluated, the effective policy is resolved in order:

1. the team's own policy, **if present and enabled**, else
2. the org-default policy, **if present and enabled**, else
3. nothing — the team falls back to the built-in static catalog.

Setting `enabled: false` disables a policy without deleting it, so a team can
turn dynamic policy off and back on without re-authoring it.

## Policy fields

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string \| null | Display label for the UI. |
| `category_overrides` | object | SPDX id → `allowed` \| `conditional` \| `forbidden`. Replaces the catalog verdict for that exact id. |
| `license_exceptions` | array | Explicit waivers — each forces the matched license to `allowed`. |
| `unknown_license_category` | enum | Posture for licenses absent from the catalog and the override map. Default `conditional`. |
| `compound_operator_strategy` | object | How a compound SPDX expression (`A AND B`, `A OR B`, `A WITH exc`) is resolved. |
| `enabled` | bool | Master toggle. `false` → policy ignored during resolution. |

### `category_overrides`

```json
{
  "MPL-2.0": "forbidden",
  "EPL-2.0": "conditional",
  "MIT": "allowed"
}
```

### `license_exceptions`

Each entry needs `spdx_id` and `reason`. `expires_at` (RFC 3339, optional) lets
the gate treat a waiver as expired; `component_purl` (optional) scopes the waiver
to a single component instead of every component carrying the license.

```json
[
  {
    "spdx_id": "GPL-3.0-only",
    "reason": "legal-approved waiver TICKET-123",
    "expires_at": "2026-12-31T00:00:00Z",
    "component_purl": "pkg:pypi/somepkg@1.2.3"
  }
]
```

### `compound_operator_strategy`

```json
{
  "AND": "most_restrictive",
  "OR": "least_restrictive",
  "WITH": "most_restrictive"
}
```

Values are `most_restrictive` or `least_restrictive`. The default keeps the most
restrictive sub-license for `AND` / `WITH`, and the least restrictive for `OR`
(the usual reading of a dual-licensed dependency). A partial object is merged
with the defaults — you only send the operators you want to change.

## API

All endpoints are rooted at `/v1/license-policies`, require a JWT, and return RFC
7807 `application/problem+json` on any error.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `PUT` | `/v1/license-policies/teams/{team_id}` | `team_admin` | Create / update a team policy. |
| `GET` | `/v1/license-policies/teams/{team_id}` | team member | Read the **effective** policy for the team. |
| `DELETE` | `/v1/license-policies/teams/{team_id}` | `team_admin` | Reset (delete) the team policy. |
| `PUT` | `/v1/license-policies/org/{organization_id}` | `super_admin` | Create / update the org-default policy. |
| `GET` | `/v1/license-policies/org/{organization_id}` | `super_admin` | Read the org-default policy. |
| `GET` | `/v1/license-policies` | authenticated | Paginated list of visible policies. |

The team `GET` returns the **effective** policy (team override, else org default)
and `404`s when neither applies — that `404` means "no policy, falls back to the
static catalog", not an error. The org endpoints are super-admin only and
existence-hide (a non-super-admin sees `404`).

### Example

```bash
curl -X PUT https://<portal>/v1/license-policies/teams/$TEAM_ID \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Engineering policy",
        "category_overrides": {"MPL-2.0": "forbidden"},
        "license_exceptions": [
          {"spdx_id": "GPL-3.0-only", "reason": "legal waiver TICKET-123"}
        ],
        "unknown_license_category": "conditional",
        "enabled": true
      }'
```

The full request / response schemas (with examples) are in the live OpenAPI
document at `/api/docs`. See also the [license classification table](../comparison.md)
for the built-in catalog the policy overrides.
