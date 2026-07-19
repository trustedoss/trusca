---
id: documentation
title: Writing docs
description: The four topic types every docs page should pick one of, task-title conventions, when a screenshot earns its place (and when it does not), and the version-history convention.
sidebar_label: Writing docs
sidebar_position: 4
---

# Writing docs

TRUSCA documents ship with the code that they describe — a user-facing feature
is not done until its guide page is (see [Releasing](./releasing.md)). This
page is the house style for those pages: what kind of page you are writing,
how to title it, and when a screenshot or a version note belongs.

## Pick one topic type per page

Every page answers a single kind of question. Decide which before you start —
a page that mixes two of these is the most common reason a reader has to
scroll past half of it to find their answer. The four types follow the
[Diátaxis](https://diataxis.fr/) framework:

| Type | Answers | Shape | Example |
|---|---|---|---|
| **Tutorial** | "Can you walk me through it the first time?" | A single numbered path from nothing to a working result, with checkpoints. Friendly tone; one happy path, no branches. | [Quickstart](../quickstart.md) |
| **How-to** | "How do I do X?" | Numbered steps for one task the reader already knows they need. Prerequisites up front, no conceptual detours. | [GitHub Actions](../ci-integration/github-actions.md) |
| **Reference** | "What are the exact values / fields / states?" | Tables and lists, scannable, complete, no narrative. The reader looks something up and leaves. | [Environment variables](../reference/env-variables.md) |
| **Explanation** | "Why does it work this way?" | Prose that gives background and rationale. No steps to follow. | [Analysis types](../reference/analysis-types.md) |

When a page genuinely needs two modes — most user-guide pages carry both
how-to steps and a reference table — lead with the dominant one and put the
other in a clearly separated section, rather than interleaving them. If a
section grows into a page of its own (the VEX sections did, splitting out of
`vulnerabilities.md` into `vex.md`), split it.

### Title conventions

- **How-to titles are an active verb + noun**: "Scan a project", "Upload an
  SBOM", "Verify SBOM signatures" — not "Scanning" or "Project scans".
- **Tutorial titles** may start with the topic ("Quickstart") — they read as a
  destination, not a task.
- **Reference and explanation titles** are noun phrases ("Environment
  variables", "Analysis types").
- A section heading is a fragment anchor. Renaming one **breaks inbound
  links** — if you must rename, keep the old anchor with an explicit
  `{#old-anchor}` so existing links still resolve.

## Screenshots earn their place — they are not free

A screenshot is expensive: it goes stale the next time the UI shifts, it is
invisible to screen readers, and it doubles the localization surface (every
KO page mirrors the EN one). Include one only when it carries information the
prose cannot.

**Add a screenshot when** the page walks the reader through a **UI surface**
and the picture removes ambiguity — a populated data view (the Vulnerabilities
table, a distribution chart), a multi-field form, a drawer or dialog whose
layout matters. These live under user-guide and admin-guide.

**Do not add a screenshot for**:

- A single button click or a menu-navigation step — describe it in text
  ("click **Scan**"). A picture of a button is noise.
- Anything that is really a **command or a config file** — installation, CI
  integration, and most of the reference shelf are CLI-and-YAML, and a
  fenced code block is the accurate, copy-pasteable, diff-able artifact. A
  screenshot of a terminal is strictly worse.
- Message text, empty states, or error strings — quote them as text so they
  are searchable and translatable.

This is why `installation/`, `ci-integration/`, and `reference/` ship with no
screenshots and that is correct, not a gap. When you do add one, capture it
through the automated pipeline (`make screenshots-capture`, see
[Testing guide](./testing-guide.md)) so it is regenerated at a consistent
1440×900 and passes the size gate — never paste a hand-taken screenshot.

## Note when a feature landed

For a user-facing feature, add a one-line version note directly under its
section heading so a reader can tell what their deployment has:

```markdown
### Group by upgrade — the remediation worklist

*Introduced in v0.17.0.*

...
```

This is the lightweight alternative to per-version documentation snapshots
(which Docusaurus advises small sites against). Add it to **new** feature
sections as they ship — there is no need to backfill the whole existing tree.
The [release notes](../release-notes/v0.14.0.md) remain the full per-version
changelog; this note is just the in-place "since when" a reader needs while
reading the guide.

## Both languages, together

Every page under `docs-site/docs/**` has a Korean mirror under
`docs-site/i18n/ko/.../current/**`. Change them in the same PR — a KO page that
lags its EN source is drift. Korean pages additionally pass the translation-tone
linter (`node tools/ko-style/lint.mjs --changed --fail-on S2`); see the
[ko-style README](https://github.com/trustedoss/trusca/blob/main/tools/ko-style/README.md).

## See also

- [Releasing](./releasing.md) — the pre-tag documentation sweep that keeps docs and code in step
- [Testing guide](./testing-guide.md) — the screenshot-capture pipeline and docs-uat assertions
- [Coding standards](./coding-standards.md) — i18n key rules and the wider house style
