---
id: inventory
title: Component inventory
description: Find every project that uses a package, across your whole organization — and see which projects a CVE reaches.
sidebar_label: Component inventory
sidebar_position: 4
---

# Component inventory

The project **Components** tab answers "what is in this project". The **Components** entry in the sidebar answers the other direction: *where in the organization is this package, and what would a fix have to touch*.

That question comes up the moment a CVE lands in the news. Without this page, answering it means opening every project in turn.

## Reading the list

Each row is one **package**, not one package-version and not one project. A library used at three versions across nine projects is a single row that reports both spreads.

| Column | What it means |
|---|---|
| Package | Name and full purl. |
| Type | Ecosystem the package came from (`npm`, `pypi`, `maven`, …). |
| Projects | How many projects currently use it. This is the number the page exists for. |
| Versions in use | The distinct versions in use, up to five. When there are more, the row says how many are hidden — the count above it is always the true total. |
| Severity | Worst CVE severity on any in-use version. |
| CVEs | Distinct CVEs across every in-use version. A CVE affecting two versions of the same package counts once. |
| License | Most restrictive license category observed, plus end-of-life and outdated badges when either applies to any in-use version. |

## What "in use" means

A package is in use when it appears in a project's **latest successful scan**.

Two consequences worth knowing:

- **A failed scan does not empty the inventory.** If a project's newest scan attempt failed, the project still contributes the packages from its last successful scan — the same rule the project Overview and the build gate follow.
- **Removing a dependency removes it here.** Once a newer successful scan no longer declares a package, it drops out. The inventory reflects the current portfolio, not everything ever scanned.

Archived projects are excluded, and you only ever see projects your team memberships reach.

## Finding a package

The search box matches the package **name** or its **purl**, so you can paste a coordinate straight in. Type filters narrow by ecosystem. **Add filter** reveals the risk axes (severity, license category) and the lifecycle flags (end-of-life only, outdated only) when you need them — they stay out of the way otherwise.

Every filter is mirrored into the URL, so a filtered view can be reloaded, bookmarked, and shared.

If a term finds nothing, the empty state offers **Search every scan**. This page reads only each project's latest successful scan, so a package that was removed a few releases ago is absent here but still present in the scan history — the [search page](./search.md) reaches back through all of it, and the link carries your term across.

## Which projects use it

Click any row to open the **Used by** panel: one entry per project and version, marked **Direct** or **Transitive**. Each entry links into that project's Components tab, already filtered — so going from "we use this somewhere" to "here is the manifest line" is two clicks.

## Which projects a CVE reaches

The same question in the vulnerability direction is answered on the vulnerability detail page. Open any finding and, when the CVE also affects other projects you can see, an **Also affected** section lists them with a link to each project's own finding. When the CVE reaches only the project you are already looking at, the section does not appear — there would be nothing to say.

## Verify it worked

<!-- docs-uat: id=inventory-page-mounts kind=ui harness=inventoryPageMounts tier=nightly -->
1. **Components** in the sidebar opens `/components` and the summary line reports a package count.
<!-- docs-uat: id=inventory-api-ok kind=api auth=admin url=/v1/inventory/components?limit=1 expect=status:200 tier=nightly -->
2. `GET /v1/inventory/components` returns 200 with `items`, `total`, `limit`, and `offset`.
<!-- docs-uat: id=inventory-unknown-component-404 kind=api auth=admin url=/v1/inventory/components/00000000-0000-0000-0000-000000000000/projects expect=status:404 tier=nightly -->
3. Asking for a component id that does not exist — or one only another team uses — returns 404, never 403. The two cases are deliberately indistinguishable so that probing an id teaches nothing.

## See also

- [Components & licenses](./components-and-licenses.md) — the per-project view of the same data.
- [Vulnerabilities](./vulnerabilities.md) — triaging a finding in one project.
