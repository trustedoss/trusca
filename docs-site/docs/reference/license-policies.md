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
The policy **data model + CRUD API**, the **dynamic build-gate evaluation**
(including hardened compound-SPDX resolution), and the **in-app editor** are now
wired: an effective, enabled policy changes the gate's forbidden-license verdict
for that team. See [Editing policies in the portal](#editing-policies-in-the-portal)
and [Dynamic gate evaluation](#dynamic-gate-evaluation) below.
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

## Editing policies in the portal

The **Policies** screen (sidebar → **Policies**, route `/policies`) is the
no-code way to author a policy — it drives the same REST API documented below.

1. **Pick a scope.** Use the **Team** picker in the toolbar and click **Edit team
   policy**, or click any row in the policies table to edit that scope. A
   `super_admin` additionally sees **Edit org default** for the organization-wide
   fallback. The selected scope is encoded in the URL
   (`/policies?policy=team:<id>` / `?policy=org:<id>`), so a bookmark or a hard
   reload reopens the same editor.
2. **Edit in the drawer.** The editor slides in from the right and exposes every
   field:
   - **Policy enabled** — master toggle. Off ⇒ the team falls back to the org
     default or the static catalog without losing the policy's contents.
   - **Name** — an optional display label.
   - **Uncatalogued license posture** — the category for a license in neither the
     catalog nor the overrides.
   - **Compound expression strategy** — the `AND` / `OR` / `WITH` resolution.
   - **Category overrides** — add / edit / remove rows mapping an SPDX id to a
     category.
   - **License exceptions** — add / remove waivers (SPDX id + reason required;
     optional expiry date and component PURL).
3. **Save or reset.** **Save policy** issues the upsert `PUT`; **Reset to default**
   (team scope only) deletes the team policy via `DELETE`, reverting to the org
   default / static catalog. Validation failures from the server (oversized maps,
   bad identifiers) surface as an error toast.

### Who can edit

| Role | Team policy | Org default |
| --- | --- | --- |
| `super_admin` | any team | yes |
| `team_admin` (of the team) | that team | no |
| team member (not admin) | **read-only** | no |

A team member who is not a `team_admin` can open the editor to view the
**effective** policy, but every control is disabled and a read-only notice is
shown — the underlying read returns `403`, and the UI degrades gracefully.

## Dynamic gate evaluation

The build-blocking gate (see [CI integration](../ci-integration/github-actions.md))
blocks a build when a project has at least one **forbidden-licensed** component.
With **no** effective policy, "forbidden" means the license category the scanner
persisted at scan time against the built-in catalog — behaviour is unchanged.

When the project's owning team has an **effective, enabled** policy, the gate
re-classifies each component's license expression **dynamically** before
counting:

1. Each component's stored SPDX expression is parsed by a hardened
   compound-SPDX evaluator (single id, `A AND B`, `A OR B`, `A WITH exc`,
   parentheses, nesting).
2. Each operand id is resolved through the policy in order: a matching,
   non-expired **exception** (forces `allowed`) → an explicit
   **override** → the built-in catalog → the **`unknown_license_category`**
   posture for anything uncatalogued.
3. Operands are folded with the per-operator
   **`compound_operator_strategy`** (`AND`/`WITH` most-restrictive, `OR`
   least-restrictive by default).
4. A component whose expression resolves to `forbidden` is counted; a positive
   count fails the gate.

So a team can, for example, **forbid** a normally-allowed license, **waive** a
normally-forbidden one for a single dependency, or read a dual-license `A OR B`
permissively — all without a redeploy. Disabling the policy (`enabled: false`)
or deleting it reverts the gate to the static catalog.

### Robustness

License expressions come from scanner output and dependency metadata — untrusted
input. The evaluator is bounded and fails safe: it never hangs and never errors
out the gate. An expression that is too long, nested too deeply, has too many
tokens, is unbalanced, or contains control characters is **not** parsed; the
component is treated with the policy's `unknown_license_category` posture and a
warning is logged. The bounds are: max **4096** characters, max **64**
parenthesis nesting levels, max **1024** tokens.

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
