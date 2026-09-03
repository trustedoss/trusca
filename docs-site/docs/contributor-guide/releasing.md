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
   auto-generated notes. The job also generates a CycloneDX SBOM of the
   release's own source tree (syft) and attaches it as a Release asset
   (`trusca-X.Y.Z.cdx.json`) — an SCA product ships its own SBOM.
4. **`release-gate`** — pulls the freshly published `X.Y.Z` images, boots the
   **production** `docker-compose.yml` (with the small
   [`docker-compose.smoke.yml`](https://github.com/trustedoss/trusca/blob/main/docker-compose.smoke.yml)
   overlay that publishes the backend + frontend ports so the smoke can run
   without Traefik/DNS/TLS), and runs the documented Quickstart first-scan smoke:
   health poll → `create_super_admin` → login → projects API. On success it runs
   `gh release edit <tag> --draft=false --latest` to reveal the Release, then
   asks [`deploy-hetzner.yml`](https://github.com/trustedoss/trusca/blob/main/.github/workflows/deploy-hetzner.yml)
   to roll the public demo forward to that tag. That deploy waits on the `demo`
   Environment's required reviewer, so a release never reaches the demo host
   unattended.

```
build ──▶ merge ──▶ release (draft) ──▶ release-gate ──▶ reveal (draft=false)
 push       tag        GitHub Release      pull + boot        public + latest
 by         version    stays hidden        published images   only if smoke
 digest     tags                           + first-scan smoke passed
```

:::note The `latest` tag left in the registry
Releases up to 0.21.0 pushed a `latest` image tag as well: `docker/metadata-action`
adds one unless you opt out (`flavor: latest=auto`), and the workflow did not.
It does now, so no new one appears. The tags already in GHCR cannot simply be
removed — GHCR deletes package *versions*, not tags, and `latest` shares its
version with `0.21.0` and `0.21`. Untag it before the next release: push a
throwaway manifest to `latest`, then delete that version. Left alone, `latest`
keeps pointing at 0.21.0 forever while claiming to be current.
:::

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

1. Refresh the vendored endoflife.date snapshot so the release ships current
   lifecycle data (EOL verdicts are stamped offline from this file):
   `python3 scripts/refresh_eol_snapshot.py` from `apps/backend`, and commit
   the updated snapshot with the release-prep changes.
2. **Documentation sweep** — the release ships its docs, so before tagging:
   - Write the release notes at `docs-site/docs/release-notes/X.Y.Z.md`
     (EN + KO mirror, wired into `sidebars.ts`), sourced from the
     `[Unreleased]` section of `CHANGELOG.md` — then move those entries under
     a new `[X.Y.Z]` heading.
   - Walk the `[Unreleased]` entries once more and confirm every
     **user-facing** feature also landed in the relevant guide page
     (user-guide / admin-guide / ci-integration), not only in the release
     notes. A feature without a guide section is a release blocker — this
     is the "docs accompany features" rule enforced at the moment it is
     cheapest to fix.
   - If a new UI surface shipped, capture its screenshot via
     `make screenshots-capture` and reference it from the guide section.
3. Bump `IMAGE_TAG` in `.env.example` to `X.Y.Z`.
4. Bump the chart with it: `version` and `appVersion` in
   `charts/trustedoss/Chart.yaml`, and `image.tag` in
   `charts/trustedoss/values.yaml`. All three, in the same commit. The chart
   claims to release in lock-step with the portal and drifted nine minor
   versions once, which left a default `helm install` running an old portal
   unless the operator overrode `image.tag`.
5. Tag and push: `git tag -s vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
   `-s` signs it and implies `-a`. A plain `git tag` makes a *lightweight*
   tag, which is a ref pointing straight at a commit with no tag object, so it
   cannot carry a signature at all. See [Signing release tags](#signing-release-tags).
6. Watch the `release-gate` job. When it goes green the Release is public and
   marked `latest` automatically — no manual step is needed.
7. Approve the demo deploy. Revealing the Release queues a `deploy-hetzner.yml`
   run against the `demo` Environment; it waits for a reviewer. Leave it
   unapproved if the demo should stay on the previous release. The queued run
   never reseeds — when the release changes `scripts/seed_demo.py`, dispatch the
   workflow by hand with `reseed: true` instead.

## Signing release tags

Release tags are signed with SSH by the release manager. The `tag-signature`
job checks every release and is the first thing that runs, so once it is
enforced an unsigned tag stops the release before anything is published.

:::note Not enforced yet
`REQUIRE_SIGNED_TAGS` is `"false"` in `release.yml`, so today the job reports
an unsigned tag and lets the release continue. That is deliberate: until the
signing key is registered every tag is unsigned, and a hard failure would
block releasing rather than improve it. Flip it to `"true"` once the setup
below is done and `.github/allowed_signers` lists the key. That flip is the
only remaining step.
:::

### One-time setup, release manager

1. Create a signing key, separate from any key used for server access:

   ```bash
   ssh-keygen -t ed25519 -C "release@trusca" -f ~/.ssh/trusca_release
   ```

2. Tell git to sign tags with it:

   ```bash
   git config --global gpg.format ssh
   git config --global user.signingkey ~/.ssh/trusca_release.pub
   ```

3. Add the **public** key to GitHub twice, under Settings → SSH and GPG keys.
   Once as an *Authentication* key if you want it to work for git, and once as
   a **Signing key**, which is what makes GitHub show tags as `Verified`. They
   are separate entries for the same key and it is easy to add only the first.

4. Add it to `.github/allowed_signers`, which is what tells a verifier *which*
   key counts, and open a PR for that file:

   ```
   release@trusca ssh-ed25519 AAAA... release@trusca
   ```

5. Flip `REQUIRE_SIGNED_TAGS` to `"true"` in `release.yml`.

The private key never leaves the release manager's machine. CI does not sign
and needs no secret; it only verifies.

### Why the allowed-signers file matters

A signature that verifies cryptographically only says the tag was signed by
somebody. `git verify-tag` reports `Good "git" signature` for *any* valid key,
including one an attacker generated. What makes it mean anything is
`.github/allowed_signers`: without a matching entry git adds `No principal
matched` and the check fails. Verifying without that file is theatre.

## See also

- [Getting started](./getting-started.md) — dev stack, first PR.
- [Install with Docker Compose](../installation/docker-compose.md) — the operator
  path the gate exercises.
- [Quickstart](../quickstart.md) — the first-scan scenario the gate smoke mirrors.
