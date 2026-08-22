---
id: report-format-templates
title: Report format templates
description: Set an organization header, label, and column selection for the vulnerability PDF/HTML report without changing its data.
sidebar_label: Report format templates
sidebar_position: 12
---

# Report format templates

An organization can set a header line, replace the default brand text, and narrow which columns the [vulnerability PDF report](../user-guide/vulnerabilities.md#download-a-report-pdf-or-excel) renders. With no row configured, the report renders exactly as it always has.

:::note Audience
`super_admin` sets or removes the row. `developer` and above can read the current row and download reports that reflect it (downloading a report has required `developer`/`viewer` since those endpoints shipped).
:::

## What it can and cannot do

One row per organization, covering the PDF/HTML report only — the separate Excel report keeps its own, wider column set and is not affected.

- `header_text` and `org_label` are plain text, not markup. `header_text` adds one line under the report header; `org_label` replaces the "TRUSCA" brand text. Both are HTML-escaped exactly like every other value the report already prints.
- `vulnerability_columns` and `component_columns` are each a non-empty **subset** of a fixed vocabulary. Selecting fewer columns never reorders, renames, or computes a column — it only hides ones you did not list, always rendered in the same order the full report already uses.
- Vulnerability columns: `cve`, `cvss`, `summary`, `status`.
- Component columns: `name`, `version`, `license`, `severity`, `vulns`.

## Request-time column override

The PDF endpoint also accepts `vulnerability_columns` / `component_columns` as repeated query parameters. When given, they override the organization's stored default **for that request only** — the priority is request-time selection, then the organization default, then every column:

<!-- docs-uat: id=report-format-request-override kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerability-report.pdf?vulnerability_columns=cve&vulnerability_columns=status"
```

A column name outside the fixed vocabulary answers `422 Unprocessable Entity`, whether it comes from the query string or from a `PUT` to this row.

## Set the organization defaults

<!-- docs-uat: id=report-format-template-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/report-format-templates/org/<organization-uuid>" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"org_label": "Acme Corp", "vulnerability_columns": ["cve", "cvss", "status"]}'
```

Every field is optional, but at least one is required; a `PUT` with none of `header_text`, `org_label`, `vulnerability_columns`, or `component_columns` is rejected with `422 Unprocessable Entity`. Set only what you need — the rest stays absent.

## Read or remove the row

<!-- docs-uat: id=report-format-template-get kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -H "Authorization: Bearer ${JWT}" \
  "https://trustedoss.example.com/v1/report-format-templates/org/<organization-uuid>"
```

`404 Not Found` means the organization has not written a row, not an error. `DELETE` the same path to remove it; reports revert to the unformatted output on the next request.

## Verify it worked

<!-- docs-uat: id=report-format-template-verify kind=manual tier=manual -->
1. Download the project's PDF report (`GET /v1/projects/{project_id}/vulnerability-report.pdf`) and confirm the organization label replaces "TRUSCA" and the selected columns are the only ones rendered.

## API endpoints

All paths are under `/v1/report-format-templates/org/{organization_id}`.

| Method | Permission | Description |
|---|---|---|
| `PUT` | `super_admin` | Create or replace the organization's report formatting row. |
| `GET` | `developer`+ | Read the organization's report formatting row. `404` if none. |
| `DELETE` | `super_admin` | Remove the organization's report formatting row. `204` on success, `404` if it had none. |

## Troubleshooting

### `422 Unprocessable Entity` on a `PUT`

Either none of the four fields were set, one of the column lists was empty (omit it — null — instead), or a column list named a column outside the fixed vocabulary.

### `422 Unprocessable Entity` on a report download

A `vulnerability_columns` or `component_columns` query parameter named a column outside the fixed vocabulary.

### `403 Forbidden` on a `PUT` or `DELETE`

Only `super_admin` may write report formatting; it covers every project's report in the deployment, not one team's.

### The formatting does not appear in a report

Confirm the project belongs to the organization the row was written for; a row never crosses organizations. Confirm you are looking at the PDF/HTML report — the Excel report has its own column set and is not affected by this row.

## See also

- [Vulnerabilities → PDF/Excel reports](../user-guide/vulnerabilities.md#download-a-report-pdf-or-excel): the document this formatting applies to.
