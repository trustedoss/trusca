---
id: release-verification
title: Verify a release image
description: Prove that a published TRUSCA container image was built by this repository, and read the SLSA provenance and SBOM that ship inside it.
sidebar_label: Verify a release image
sidebar_position: 11
---

# Verify a release image

Every TRUSCA release publishes three multi-architecture images to GitHub Container Registry. Each one carries two things a consumer can check without trusting the registry listing:

- **SLSA provenance and an SBOM**, attached to the image itself as attestation manifests. They record how the image was built and what went into it, per architecture.
- **A signed GitHub build provenance attestation**, which binds the image digest to the repository, workflow, and commit that produced it. It is signed under GitHub's OIDC identity through [Sigstore](https://www.sigstore.dev/), so a third party can check it without any key from us.

This page is for anyone who self-hosts TRUSCA and wants to prove that what they pulled is what we published.

:::note Scope
This covers **container images**. The SBOM that TRUSCA generates for *your* projects is a different artifact with its own signature; see [Verify SBOM signatures](./sbom-signature-verification.md). For the git tag a release was cut from, see [Verify the release tag](#verify-the-release-tag).
:::

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Buildx (bundled with Docker Desktop and recent Docker Engine).
- The [GitHub CLI](https://cli.github.com/) `gh`, version 2.49 or newer, for `gh attestation verify`.
- Outbound access to `ghcr.io`. Verifying the GitHub attestation additionally reaches Sigstore's transparency log; see [Air-gapped deployments](#air-gapped-deployments).

## 1. Pin the image by digest

Tags can be repointed. A digest cannot, so resolve the tag once and use the digest everywhere after that.

```bash
IMAGE=ghcr.io/trustedoss/trusca-backend
VERSION=0.22.5   # the release you are verifying

DIGEST=$(docker buildx imagetools inspect "${IMAGE}:${VERSION}" \
  --format '{{ json .Manifest }}' | jq -r '.digest')
echo "${IMAGE}@${DIGEST}"
```

Pull by that digest, and put the digest into your compose file or Helm values rather than the tag if you want the deployment pinned.

## 2. Check who built it

`gh attestation verify` fetches the signed provenance for the digest and checks it against the repository you expect.

```bash
gh attestation verify "oci://${IMAGE}@${DIGEST}" --repo trustedoss/trusca
```

A successful run reports the verified predicate and the workflow that produced the image. If the digest was not built by that repository's release workflow, the command exits non-zero.

To be stricter, pin the workflow as well, so an attestation produced by some other workflow in the same repository does not satisfy the check:

```bash
gh attestation verify "oci://${IMAGE}@${DIGEST}" \
  --repo trustedoss/trusca \
  --signer-workflow trustedoss/trusca/.github/workflows/release.yml
```

## 3. Read the provenance and the SBOM

These come from inside the image index, so they need no network beyond the registry itself.

```bash
# How each architecture was built: build type, source commit, parameters.
docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --format '{{ json .Provenance }}'

# What is inside each architecture.
docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --format '{{ json .SBOM }}'
```

Both return an object keyed by platform (`linux/amd64`, `linux/arm64`), so pick the platform you run:

```bash
docker buildx imagetools inspect "${IMAGE}@${DIGEST}" --format '{{ json .Provenance }}' \
  | jq '.["linux/amd64"].SLSA.buildDefinition'
```

The SBOM here describes the built image, including its base layers. It is not the same document as the source-tree CycloneDX SBOM attached to the GitHub Release, which covers the repository's own dependency manifest. Both are published; they answer different questions.

## 4. What each check proves

| Check | Answers | Does not answer |
|---|---|---|
| Digest pinning (§1) | You are running exactly these bytes | Who produced them |
| `gh attestation verify` (§2) | This digest was built by this repository's workflow at a known commit | Whether that commit is one you trust |
| Provenance (§3) | How the image was assembled, per architecture | Whether the build inputs were themselves verified |
| Image SBOM (§3) | What packages the image contains | Whether they have known vulnerabilities. Scan the image for that |

None of these replace reviewing what changed in a release. They establish that the artifact you pulled is the artifact we built.

## Air-gapped deployments

The attestations in §3 travel inside the image index, so they work anywhere the image itself can be pulled, including from an internal mirror.

`gh attestation verify` is different. By default it fetches the attestation from GitHub and checks it against Sigstore's public trust material, so a host with no outbound access needs both of those brought in first.

Because the release pushes the attestation to the registry alongside the image, an internal mirror that copied the image referrers can serve it:

```bash
gh attestation verify "oci://${IMAGE}@${DIGEST}" --repo trustedoss/trusca --bundle-from-oci
```

If the host cannot reach Sigstore either, export the trust material on a connected machine and carry both files across:

```bash
# On a connected machine
gh attestation trusted-root > trusted_root.jsonl
gh attestation download "oci://${IMAGE}@${DIGEST}" --repo trustedoss/trusca

# On the air-gapped host, against the files you carried over
gh attestation verify "oci://${IMAGE}@${DIGEST}" \
  --bundle sha256:<digest>.jsonl \
  --custom-trusted-root trusted_root.jsonl \
  --repo trustedoss/trusca
```

The simplest option remains verifying on a connected machine and mirroring the **digest** internally. A digest carries the guarantee with it.

## Verify the release tag

The images above tell you a workflow built them. The tag tells you a person
cut that release. They answer different questions, so check both if the second
one matters to you.

:::note Not signed on every release yet
Tag signing is being rolled out: the release workflow already checks each tag
and reports an unsigned one, but does not yet refuse the release. Until a
release's tag carries a signature the commands below report that, which is the
honest answer rather than a pass.
:::

Release tags are signed with SSH. Verification needs the repository's
allowed-signers file, which says **which** key counts, so clone the repo rather
than working from a tarball:

```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca
git config gpg.ssh.allowedSignersFile .github/allowed_signers
git verify-tag v0.22.5
```

A good result names the signer. What you are looking for is a principal, not
just a good signature:

```
Good "git" signature for release@trusca with ED25519 key SHA256:...
```

If you see `Good "git" signature` **without** a signer, followed by
`No principal matched`, the tag was signed by a key that is not the release
manager's. Treat that as a failure. That distinction is the entire value of the
allowed-signers file: a bare `git verify-tag` accepts any valid key, including
one an attacker made a minute ago.

## The `unknown/unknown` entry in the package listing

The GHCR package page lists an extra `unknown/unknown` architecture beside `linux/amd64` and `linux/arm64`. That entry is the attestation manifest. Attestations are stored as manifests inside the image index with their platform deliberately set to `unknown/unknown` so no runtime mistakes them for something to execute.

It is a display artifact of the registry UI, not an extra image. Pulling by tag or digest resolves to the right architecture as normal. Releases published before attestations were enabled do not show the entry, so a listing that lacks it is simply an older image.
