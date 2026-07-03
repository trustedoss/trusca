---
id: releasing
title: Releasing
description: How a TRUSCA release is cut — images publish first, the GitHub Release stays a draft until a smoke test proves the published images boot, then it is revealed.
sidebar_label: Releasing
sidebar_position: 5
---

# Releasing

A TRUSCA release is driven entirely by pushing a `vX.Y.Z` git tag. The
[`.github/workflows/release.yml`](https://github.com/trustedoss/trusca/blob/main/.github/workflows/release.yml)
workflow does the rest. The design goal is that **no release is ever announced
before it has been proven installable from the exact images users will pull.**

## The gate at a glance

The workflow runs four stages in order, each depending on the previous one:

1. **`build`** — builds each image (`trusca-backend`, `trusca-backend-worker`,
   `trusca-frontend`) on native amd64 + arm64 runners and pushes them to GitHub
   Container Registry **by digest**.
2. **`merge`** — assembles a multi-arch manifest list per image and applies the
   version tags (`X.Y.Z` immutable, `X.Y` movable — never `:latest`).
3. **`release`** — creates the GitHub Release as a **draft**. Notes come from
   `docs-site/docs/release-notes/X.Y.Z.md` when present, otherwise from GitHub's
   auto-generated notes.
4. **`release-gate`** — pulls the freshly published `X.Y.Z` images, boots the
   **production** `docker-compose.yml` (with the small
   [`docker-compose.smoke.yml`](https://github.com/trustedoss/trusca/blob/main/docker-compose.smoke.yml)
   overlay that publishes the backend + frontend ports so the smoke can run
   without Traefik/DNS/TLS), and runs the documented Quickstart first-scan smoke:
   health poll → `create_super_admin` → login → projects API. On success it runs
   `gh release edit <tag> --draft=false --latest` to reveal the Release.

```
build ──▶ merge ──▶ release (draft) ──▶ release-gate ──▶ reveal (draft=false)
 push       tag        GitHub Release      pull + boot        public + latest
 by         version    stays hidden        published images   only if smoke
 digest     tags                           + first-scan smoke passed
```

## Why images publish before the Release is revealed

The container images are published in `build` + `merge`, **before** the Release
exists. This is deliberate: the gate can only prove a release is installable by
pulling and running the *actual* published images the way an operator would. The
Release is the human-facing announcement, so it is held back — as a draft —
until that proof succeeds.

## Failure semantics

If any `release-gate` step fails, the reveal step is skipped (it has no
`if: always()` guard, so it only runs on the success path). The result is:

- **The image tags stay published and pullable.** `X.Y.Z` and `X.Y` were pushed
  in the `merge` stage and are not rolled back. An operator can still pull them,
  and a re-run of the workflow reuses them.
- **The GitHub Release stays a draft.** It is not visible on the Releases page,
  is not marked `latest`, and does not notify watchers. Nothing announces a
  release whose images failed to boot.

To recover, fix the underlying problem and re-run the workflow for the same tag
(or dispatch it manually with the `tag` input). The `release` job is idempotent:
it leaves an existing draft untouched, and `release-gate` re-pulls the same
published images and re-runs the smoke. Only when the smoke passes does the
draft flip to public.

:::note Manual reveal
If you have independently verified a release whose gate is failing for an
unrelated (e.g. infrastructure) reason, a maintainer can reveal it by hand with
`gh release edit vX.Y.Z --draft=false --latest`. Prefer fixing the gate.
:::

## Cutting a release

1. Land the release notes at `docs-site/docs/release-notes/X.Y.Z.md` and bump
   `IMAGE_TAG` in `.env.example` to `X.Y.Z`.
2. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. Watch the `release-gate` job. When it goes green the Release is public and
   marked `latest` automatically — no manual step is needed.

## See also

- [Getting started](./getting-started.md) — dev stack, first PR.
- [Install with Docker Compose](../installation/docker-compose.md) — the operator
  path the gate exercises.
- [Quickstart](../quickstart.md) — the first-scan scenario the gate smoke mirrors.
