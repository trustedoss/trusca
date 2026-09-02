---
id: package-lookup
title: Package lookup
description: Check a package's license and known advisories before it is pulled in, and see whether it is already used internally.
sidebar_label: Package lookup
sidebar_position: 6
---

# Package lookup

[Search](./search.md) only covers what has already been scanned. Package lookup answers the question that comes before that: *should we pull this in at all*. It queries the external package catalog ([deps.dev](https://deps.dev)) directly, so a package that has never touched a build here still shows its license and known advisories.

Reach it from the command palette (**⌘K** / **Ctrl+K**, "Look up a package externally") or at `/packages/lookup`.

An administrator can turn this off for an air-gapped deployment by setting `EXTERNAL_PACKAGE_LOOKUP_ENABLED=false`; the entry point disappears rather than showing a lookup that always times out.

## Looking a package up

Pick an ecosystem and type the exact package name, then submit. The catalog only answers exact matches, not partial or fuzzy search, so a misspelled name or the wrong ecosystem comes back not found rather than close guesses.

Six ecosystems are supported: npm, PyPI, Maven, Go, crates.io, and NuGet.

The result shows the package's current version, its license(s), and a count of known advisories with their ids. Not finding a package here does not mean it is safe: it means the catalog has no record of it under that exact name and ecosystem.

## Internal usage

Every result also reports whether the package is already used somewhere internally: which projects, and at which version each one has it. An empty list means no project has pulled it in yet, so a pre-adoption request for it will not be a duplicate.

This match is exact, on the package's identity (its purl without a version), not a text search. A package with a similar name is never shown here by mistake.

## Asking to use it

Where [Intake](./approvals.md#intake-requests) is turned on, a result carries a button that opens one, with the package pre-filled.

## Verify it worked

<!-- docs-uat: id=external-package-lookup-api kind=api auth=viewer url=/v1/external-packages?ecosystem=npm&name=lodash expect=status:200 tier=nightly -->
1. `GET /v1/external-packages?ecosystem=npm&name=lodash` returns 200 with `found`, `licenses`, `advisory_count`, and `internal_projects`.
2. A package the catalog has never heard of returns 200 with `found: false`, not an error.
3. Rate limits: 10 package lookups per minute, 20 advisory lookups per minute (both per authenticated user).

## See also

- [Search](./search.md): for CVE and GHSA advisory details on results already found in a scan.
- [Approvals](./approvals.md): where a pre-adoption request goes next.
