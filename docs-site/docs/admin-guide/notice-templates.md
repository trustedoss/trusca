---
id: notice-templates
title: NOTICE templates
description: Add a preface and footer to every project's NOTICE document without touching its license or component content.
sidebar_label: NOTICE templates
sidebar_position: 11
---

# NOTICE templates

An organization can add plain-text boilerplate, a distribution notice, an internal letterhead, a standard legal disclaimer, to every project's [NOTICE attribution document](../user-guide/sbom.md#notice-file). With no template configured, a NOTICE renders exactly as it always has.

:::note Audience
`super_admin` sets or removes a template. `developer` and above can read the current template and generate NOTICEs that include it (generating a NOTICE has required `developer` since that endpoint shipped).
:::

## What a template can and cannot do

A template is one preface and one footer, per format (`text`, `markdown`, `html`), for the whole organization. It is plain text, not markup:

- It can only add text before and after the license/component/obligation list. It cannot remove, reorder, or alter a single line of that list.
- There is no conditional or loop syntax. A template cannot be written to omit an obligation depending on which license or component triggered it.
- The text is escaped the same way every other value the NOTICE renderer prints already is: backslash-escaped for markdown's inline-active punctuation and wrapped so it can never open a heading, list, or blockquote at the start of a line; HTML-escaped and printed inside a `<pre>` block for the HTML format.

## Set a template

<!-- docs-uat: id=notice-template-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/notice-templates/org/<organization-uuid>/text" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"preface": "Internal distribution only.", "footer": "© Example Corp. All rights reserved."}'
```

`preface` and `footer` are each optional, but at least one is required, a `PUT` with neither is rejected with `422 Unprocessable Entity`. Set only the one you need; the other stays absent.

Write one template per format your organization actually downloads. A team that only ever pulls the HTML NOTICE never needs a `text` or `markdown` template written.

## Read or remove a template

<!-- docs-uat: id=notice-template-get kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -H "Authorization: Bearer ${JWT}" \
  "https://trustedoss.example.com/v1/notice-templates/org/<organization-uuid>/text"
```

`404 Not Found` means the organization has not written a template for that format, not an error. `DELETE` the same path to remove one; the NOTICE reverts to its untemplated output on the next request.

## Verify it worked

<!-- docs-uat: id=notice-template-verify kind=manual tier=manual -->
1. Generate the project's NOTICE (`GET /v1/projects/{project_id}/notice?format=text`) and confirm the preface appears before the first license section and the footer after the last.

## API endpoints

All paths are under `/v1/notice-templates/org/{organization_id}/{format}`, where `format` is `text`, `markdown`, or `html`.

| Method | Permission | Description |
|---|---|---|
| `PUT` | `super_admin` | Create or replace the organization's template for one format. |
| `GET` | `developer`+ | Read the organization's template for one format. `404` if none. |
| `DELETE` | `super_admin` | Remove the organization's template for one format. `204` on success, `404` if it had none. |

## Troubleshooting

### `422 Unprocessable Entity` on a `PUT`

Either neither `preface` nor `footer` was set, or `format` in the path is not one of `text`, `markdown`, `html`.

### `403 Forbidden` on a `PUT` or `DELETE`

Only `super_admin` may write NOTICE templates; this boilerplate covers every project's NOTICE, not one team's.

### The template does not appear in a NOTICE

Confirm you wrote it for the same `format` the NOTICE was requested with; a `markdown` template does not apply to a `text` or `html` download. Also confirm the project belongs to the organization the template was written for; a template never crosses organizations.

## See also

- [SBOM → NOTICE file](../user-guide/sbom.md#notice-file): the document this boilerplate attaches to.
