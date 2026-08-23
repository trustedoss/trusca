---
id: search
title: Search
description: Search projects, packages, CVEs, and licenses across everything you can see — then save the searches you keep coming back to.
sidebar_label: Search
sidebar_position: 5
---

# Search

Press **⌘K** (macOS) / **Ctrl+K** (Windows, Linux) for a quick look. The same palette opens the full page: **Open full search** is the first row under **Pages**, and it carries whatever you have typed. They answer different questions on purpose: the palette shows a few of everything so you can jump somewhere, the page shows all of one thing so you can work through it.

Every group of results in the palette also ends with **See all results**, which lands you on the page with the same term applied and that group's tab already selected.

## Four tabs

| Tab | What it matches | Where a row leads |
|---|---|---|
| Projects | Name, slug, or clone URL. Archived projects appear, labelled. | The project's detail page. |
| Components | Package name or purl, across a project's whole scan history. | That project's Components tab, filtered to the package. |
| Vulnerabilities | CVE id or summary, in each project's latest successful scan. | The finding's detail page. |
| Licenses | SPDX id or license name, in each project's latest successful scan. | The project's Compliance tab. |

Components search history while vulnerabilities and licenses read only the current scan. That difference is deliberate: *"have we ever shipped this package"* is a fair question, but a triage list should not resurface a CVE that a later scan already cleared, and a license finding from a superseded scan would misstate today's obligations.

Each tab says which of the two it is, on the line above the results — that line is also why the Components tab here can return more rows than the [component inventory](./inventory.md), which reads only the latest scan.

You only ever see projects your team memberships reach.

## Filtering by facet

Below the tabs, each facet chip carries a count — **high 42** means the filter will leave 42 results, not 42 of the ones currently visible. Counts come from the whole matching set.

Which facets exist depends on the tab: severity and status for vulnerabilities, type for components, category for licenses. Switching tabs clears them rather than carrying a filter the new tab has no way to show you.

Everything — the term, the tab, the page, the facets — lives in the URL, so a search can be reloaded, bookmarked, and shared.

## Saving a search

**Save search** parks the current query under a name. Saved searches appear on your dashboard; clicking one restores exactly the filters that were applied when you saved it.

They belong to you, not your team. What gets saved is the filter, and the results are re-run through your own team scope every time you open it — so sharing the row would share the query text, not anyone's findings.

You can keep up to 20. The Save button goes disabled once you reach the limit rather than failing after you type a name.

## Verify it worked

<!-- docs-uat: id=search-results-api kind=api auth=admin url=/v1/search/results?kind=components&q=lod expect=status:200 tier=nightly -->
1. `GET /v1/search/results?kind=components&q=lod` returns 200 with `items_components`, `total`, `page`, `size`, and `facets`.
<!-- docs-uat: id=search-unknown-kind-422 kind=api auth=admin url=/v1/search/results?kind=bogus&q=lodash expect=status:422 tier=nightly -->
2. An unknown `kind` returns 422.
<!-- docs-uat: id=saved-searches-api kind=api auth=admin url=/v1/saved-searches expect=status:200 tier=nightly -->
3. `GET /v1/saved-searches` returns your saved searches with the per-user `limit`.

## See also

- [Component inventory](./inventory.md) — one row per package across the whole organization, rather than one row per match.
- [Dashboard](./dashboard.md) — where saved searches surface.
