---
id: remediation-dry-run
title: Remediation dry-run (npm)
description: Preview the package.json dependency-bump edit for a project's vulnerable npm dependencies — no PR, no persistence.
sidebar_label: Remediation dry-run
sidebar_position: 5
---

# Remediation dry-run (npm)

The remediation dry-run turns a project's vulnerability findings into a concrete, reviewable **`package.json` edit**: it computes the minimum-safe upgrade for each vulnerable npm dependency and shows you the exact lines that would change. It does **not** open a pull request and does **not** persist anything — it is a preview you can inspect before automated PR creation (a later feature) acts on it.

:::note Audience
Developers and CI integrators previewing dependency remediation for an npm project.
:::

## Endpoint

```
POST /v1/projects/{project_id}/remediation/npm/dry-run
```

Authentication is required (JWT or API key). The caller must be a member of the project's team (role ≥ developer); a project you cannot see returns `404` (existence-hide).

### Request body (optional)

```json
{
  "manifest": "{\n  \"dependencies\": { \"lodash\": \"^4.17.20\" }\n}\n"
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `manifest` | string \| null | Raw `package.json` text to edit. When omitted, the endpoint best-effort reads the manifest from the project's latest **preserved scan source**. Supply it explicitly when no source was preserved (or it was swept). |

### Response

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

`manifest_source` is `override` (you supplied one), `preserved_source` (read from the latest scan), or `none` (none available — `manifest_found` is then `false`). `edited_manifest` is present only when `changed` is `true`.

## Range-rewrite policy

The dry-run **preserves your range-operator style** and rewrites only the version number:

| Existing range | Result (target `1.3.0`) |
| --- | --- |
| `^1.2.3` (caret) | `^1.3.0` |
| `~1.2.3` (tilde) | `~1.3.0` |
| `1.2.3` (pinned) | `1.3.0` |
| `>=1.2.3` (single relop) | `>=1.3.0` |
| `v1.2.3` | `v1.3.0` |
| `1.2.x` / `1.x` | `^1.3.0` (widened to caret) |
| `*` / `""` / `latest` | left unchanged (already permits the fix) |
| `npm:alias@…` / `file:` / `git+…` / compound (`>=1 <2`, `||`) | left unchanged + flagged |

A range that already satisfies the target (its lower bound is at or above the fix) is left untouched and flagged `already_satisfied`. Only packages that have a recommendation are touched; everything else is byte-for-byte preserved, so the eventual PR diff is minimal.

## Lockfile

The dry-run **never edits `package-lock.json`** — integrity hashes are not hand-written. When the manifest changes, the response always carries a `lockfile_regeneration_required` warning. Run `npm install` to regenerate the lockfile before merging.

## Warnings

| Code | Meaning |
| --- | --- |
| `lockfile_regeneration_required` | the manifest changed; regenerate the lockfile |
| `package_not_present` | a recommended package was not in any dependency section |
| `value_not_string` | a version value was not a string (array/number/null) — skipped |
| `unparseable_range` | a range was a wildcard/alias/compound/non-registry source — left unchanged |
| `already_satisfied` | the existing range already covers the fix — no bump |
| `target_unparseable` | the recommended version did not parse — skipped |
| `duplicate_keys_collapsed` | the manifest had duplicate keys (last-wins, per the JSON spec) |

## Errors

All errors are RFC 7807 `application/problem+json`:

- `401` — authentication required.
- `404` — project not found / not accessible.
- `422` — the supplied or fetched `package.json` could not be edited (invalid JSON, non-object root, no dependency section, oversized).
