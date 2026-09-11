---
id: offline-install
title: Offline / air-gapped install bundle
description: Build a single tar with every image, the Trivy DB, the Helm chart, and the Compose files, then install with no internet access at all.
sidebar_label: Offline install bundle
sidebar_position: 7.6
---

# Offline / air-gapped install bundle

`scripts/install.sh` normally pulls images from a registry and lets the
worker download the Trivy vulnerability DB from `ghcr.io` on first boot.
Neither is possible on a host with no outbound network path at all. This
page covers `scripts/bundle-offline.sh`, which packages everything that
install needs into one tar on a connected host, and
`scripts/install.sh --offline <bundle.tar>`, which consumes it on the
air-gapped target.

This is a different concern from [Private registries](./private-registries.md)
(authenticating cdxgen against a reachable-but-credentialed dependency
mirror) and from [Vulnerability data](./vulnerability-data.md)'s Path A/B
(mirroring or manually seeding *just* the Trivy DB, when everything else
about the deployment already has network access). Use this page when the
target host cannot reach anything at all - no registry, no Trivy DB
endpoint, nothing.

## What's in the bundle

| Component | Source | Always present? |
|---|---|---|
| The six images `docker-compose.yml` pulls | `docker save` | Yes |
| Trivy vulnerability DB snapshot | `trivy --download-db-only` | Best-effort - omitted if `trivy` is not installed on the build host, or the download fails |
| Trivy Java DB snapshot | `trivy --download-java-db-only` | Best-effort - a smaller nice-to-have; a missing one only affects jar-to-Maven-coordinate matching |
| `charts/trustedoss/` | copied | Yes |
| `docker-compose.yml`, `docker-compose.dev.yml`, `.env.example` | copied | Yes |

The six images are traefik, postgres, redis, `trusca-backend`,
`trusca-backend-worker`, and `trusca-frontend`. The originating issue for
this feature described "four images"; measured against `docker-compose.yml`
rather than assumed, the compose stack actually pulls six distinct image
references (the backend-worker image is reused by three services -
`worker-scan`, `worker-default`, and `beat` - but that is still one image to
save and load). An air-gapped `docker-compose up` needs the whole set or it
stalls on whichever image is missing, so the bundle carries all six.

## 1. Build the bundle (on a connected host)

Run this on a build server, a developer laptop, or a bastion with internet
egress - anywhere `docker` (and ideally `trivy`) is installed and can reach
`ghcr.io`:

```bash
bash scripts/bundle-offline.sh
```

`IMAGE_REGISTRY` / `IMAGE_TAG` default from `.env` when one is present in the
repo root (the same convention `install.sh` and `backup.sh` use), otherwise
they fall back to `docker-compose.yml`'s own defaults. Override either
explicitly:

```bash
bash scripts/bundle-offline.sh --tag 0.22.0 --registry ghcr.io/trustedoss
```

Pick a different output path with `-o`/`--output`:

```bash
bash scripts/bundle-offline.sh -o /mnt/usb/trusca-offline-bundle.tar
```

The script pulls any of the six images not already present locally, then
`docker save`s all of them into one tar, downloads the Trivy DB (and, best
effort, the Java DB) into a scratch cache directory, copies the Helm chart
and the three Compose/env files, and packs everything into a single output
tar. It prints each component's size as it goes, and the final tar's
contents and size at the end.

If a step fails partway through (disk full, `docker save` error, network
drop mid-download), the script cleans up its scratch directory and removes
any partial output - it never leaves a truncated tar at the final path for
a later run to mistake for a good one.

:::tip Rebuild per release
The bundle is tied to one `IMAGE_TAG`. Build a fresh one for every version
you plan to install or upgrade to across the air gap - there is no
"latest" bundle to reuse.
:::

## 2. Transfer it across the air gap

Copy **both** the tar and its `.sha256` sidecar across whatever approved
channel your air gap uses (USB, internal artifact store, a supervised
file-transfer process). The bundle has no dependency on the machine it was
built on beyond the image tags and architecture it was built for - build it
on a host with the same CPU architecture (amd64/arm64) as the target.

:::danger Only install a bundle you can trust
`install.sh --offline` `docker load`s whatever is inside the tar and runs it
with the same privileges as a normal install - there is no sandboxing. The
checksum in step 3 below catches transit corruption and tampering *after*
you already have the bundle, not a bundle from a source you should not have
trusted in the first place. Only run `--offline` against a bundle you built
yourself, or one whose provenance you can otherwise attest to (an internal
build pipeline you control, a colleague you trust and can ask directly) -
never one whose origin you cannot verify.
:::

## 3. Install with it (on the air-gapped target)

Clone or copy the repository onto the target host (the bundle carries the
Compose files as a convenience copy, but `scripts/install.sh` itself still
needs to exist there), then run:

```bash
bash scripts/install.sh --offline /path/to/trusca-offline-bundle-0.22.0.tar
```

Combine with `--no-prompt` for a fully unattended air-gapped install (see
[the UAT checklist](./../installation/uat-checklist.md) for the full list of
`INSTALL_*` non-interactive knobs `--no-prompt` reads):

```bash
bash scripts/install.sh --no-prompt --offline /path/to/trusca-offline-bundle-0.22.0.tar
```

With `--offline` set, `install.sh`:

1. Verifies the bundle against its `.sha256` sidecar (a checksum mismatch, or
   a missing sidecar, aborts immediately - nothing below runs against an
   unverified bundle) and checks every member path inside the tar for a
   path-traversal or absolute-path shape, before extracting anything.
2. Extracts the bundle to a scratch directory and `docker load`s the six
   images, then confirms every image the bundle's own `MANIFEST.txt` lists
   is actually present in the local Docker daemon afterward - this replaces
   the `docker-compose pull` step entirely, so nothing in the normal install
   flow reaches out to a registry.
3. Pins `.env`'s `IMAGE_REGISTRY` / `IMAGE_TAG` to whatever the bundle's
   `MANIFEST.txt` recorded - `.env.example`'s own default (or an
   `INSTALL_*` override) has no reason to match the tag the bundle was
   actually built with, and `docker-compose.yml` needs to resolve to the
   images that were just loaded, not pull a mismatched one from the network.
4. If the bundle carries a Trivy DB snapshot, seeds the `trivy-cache` named
   volume from it (via `docker-compose run` against the `worker-scan`
   service, so it lands on the correct volume regardless of this
   deployment's `COMPOSE_PROJECT_NAME`) before the stack starts, and sets
   `TRIVY_DB_BOOTSTRAP_ON_START=false` in `.env` so the worker does not
   repeatedly retry an unreachable `ghcr.io` on every restart.
5. Runs the rest of the wizard exactly as a normal install - the staged
   bring-up, the alembic migration, and the super-admin bootstrap are all
   unchanged.

If the bundle has no Trivy DB (the build host had no `trivy` installed, or
the download failed), `install.sh` leaves the worker's normal online
bootstrap enabled - vulnerability matching will simply be unavailable until
that host regains network access, or until you follow
[Vulnerability data's Path B](./vulnerability-data.md#air-gapped) to seed the
cache separately.

## Helm / Kubernetes

The bundle carries `charts/trustedoss/` for a Helm-based deploy, but
`docker load` only populates one host's local Docker daemon - it does not
help a Kubernetes cluster, whose kubelets pull images through their own
container runtime from a registry, not from `docker load` on your
workstation. For a Helm install across an air gap you additionally need an
internal registry reachable by every node, and to push the six images
there (`docker load` the bundle locally, re-tag each image for your internal
registry, `docker push`) before `helm install` - the chart's own
`image.repository` values then point at that internal registry the same way
[Private registries](./private-registries.md) documents for the container
scan credential path.

## Known limits

- **The `.sha256` sidecar is required, not optional.** `install.sh --offline`
  refuses to proceed without one matching the bundle - there is no flag to
  skip this. If you only have the tar (the sidecar was lost or never copied
  over), regenerate it on the target with
  `sha256sum bundle.tar > bundle.tar.sha256` yourself; that only re-proves
  the tar is internally consistent with a checksum you just made, not that
  it is the one `bundle-offline.sh` originally produced, so do this only for
  a bundle you already trust by the rule in step 2 above.
- **One host's Docker daemon.** `docker load` on the target populates the
  local Docker daemon only. On a multi-node deployment (Helm/Kubernetes,
  or a Compose host with any container runtime other than the one that ran
  `docker load`), you still need to get the images to every node - see
  Helm/Kubernetes above.
- **Architecture-specific.** Build the bundle on the same CPU architecture
  (amd64/arm64) you intend to install on. `docker save` captures whatever
  the local daemon pulled, not a multi-arch manifest.
- **Trivy DB is best-effort, not guaranteed.** A build host without `trivy`
  installed, or one that cannot reach `ghcr.io/aquasecurity/trivy-db`
  itself, still produces a valid bundle - just one without a Trivy DB
  snapshot. `MANIFEST.txt` inside the tar records whether one was included.
- **Not compressed.** Images and the Trivy DB are already compressed
  internally; the bundle is a plain `tar`, not a `.tar.gz`, to avoid burning
  CPU re-compressing multi-gigabyte content for little size benefit.
- **The repository itself is still needed.** The bundle carries a
  convenience copy of the three Compose/env files, but `scripts/install.sh`
  and `scripts/bundle-offline.sh` themselves are not inside the bundle - get
  the repository onto the target host by whatever means your air gap
  allows (a source tarball, an internal Git mirror, the same transfer
  channel as the bundle itself).
