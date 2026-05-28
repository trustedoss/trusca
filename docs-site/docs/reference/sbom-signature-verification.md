---
id: sbom-signature-verification
title: Verify SBOM signatures (cosign)
description: Download a TrustedOSS Portal SBOM signature bundle and verify it externally with cosign verify-blob, plus operator key setup for key-based and keyless signing.
sidebar_label: Verify SBOM signatures
sidebar_position: 10
---

# Verify SBOM signatures (cosign)

Every source scan signs its CycloneDX SBOM with [cosign](https://docs.sigstore.dev/cosign/overview/) and builds an in-toto / SLSA provenance attestation. This page shows a consumer how to download the signing artifacts and verify them **outside** the portal, and shows an operator how to configure the signing key.

:::note Audience
Two readers:

- **Verifiers** (sections 1–4) — anyone who consumes a TrustedOSS SBOM and wants to prove it is intact and was signed by a known deployment. Assumes a shell and the ability to install a CLI binary.
- **Operators** (section 5) — the person who deploys the portal and owns the signing key. Assumes Linux + Docker Compose proficiency and familiarity with [environment variables](./env-variables.md).
:::

## Prerequisites

For verification:

1. A TrustedOSS account with at least the **Developer** [role](./glossary.md#rbac-roles) on the project's team (the signature endpoints reuse the same access control as the SBOM export — an outsider sees `404`).
2. The project has at least one **succeeded** scan, and signing was configured on the deployment that ran it (see [section 5](#5-operator-key-setup)). A scan that was never signed has no signature artifacts.
3. [cosign](https://docs.sigstore.dev/cosign/installation/) installed on the machine that verifies (see [section 2](#2-install-cosign)).

## 1. Why sign an SBOM?

An [SBOM](./glossary.md#sca-core) tells a consumer *what is inside* a release. A **signature** answers two further questions the SBOM alone cannot:

- **Integrity** — were the SBOM bytes altered after the deployment produced them? A signature over the exact bytes detects any tampering.
- **Provenance** — *how* was the SBOM produced, and by whom? The [in-toto](https://in-toto.io/) / [SLSA](https://slsa.dev/) provenance attestation records the build platform identity and version.

This is the supply-chain-security expectation set by [Executive Order 14028](https://www.cisa.gov/topics/cyber-threats-and-advisories/cybersecurity-best-practices/secure-by-design/sbom), the [CISA 2025 SBOM minimum elements](https://www.cisa.gov/sbom), and the [NTIA minimum elements](https://www.ntia.gov/page/software-bill-materials): a consumer should be able to verify an artifact's authenticity without trusting the channel it arrived over.

### Key-based vs keyless

cosign supports two trust models. TrustedOSS supports both; **key-based is the default** for self-hosted, on-prem, and air-gapped deployments.

| Model | How the deployment signs | What the verifier needs | When |
|---|---|---|---|
| **Key-based** (default) | A cosign key pair; the private key signs, the public key is published | `cosign.pub` (the public key) | Self-hosted / on-prem / air-gapped — no internet dependency |
| **Keyless** (opt-in) | A short-lived [Fulcio](https://docs.sigstore.dev/certificate_authority/overview/) certificate bound to an OIDC identity; the signature is logged in [Rekor](https://docs.sigstore.dev/logging/overview/) | the Fulcio **certificate** plus the expected **identity** and **OIDC issuer** | CI-driven deployments with an OIDC provider and outbound access to a Sigstore instance |

The verification command differs between the two — both are covered in [section 4](#4-verify).

:::info Best-effort signing
Signing is best-effort. If the worker has no cosign binary, no key is configured, or cosign fails, the scan still **succeeds** but the SBOM is left unsigned (a structured warning is logged). An unsigned scan has no signature artifacts, so the download endpoints return `404` — see [Troubleshooting](#troubleshooting).
:::

## 2. Install cosign

Install cosign on the machine that performs verification. cosign **v2.x** is recommended (the commands below assume the v2 CLI).

```bash
# macOS (Homebrew)
brew install cosign

# Linux (binary)
curl -sSfL -o cosign \
  "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64"
chmod +x cosign && sudo mv cosign /usr/local/bin/

# Verify the install
cosign version
```

See the [cosign installation guide](https://docs.sigstore.dev/cosign/installation/) for other platforms.

## 3. Download the signing artifacts

The signature surface always describes the project's **latest succeeded scan** — the same scan the [SBOM export](../user-guide/sbom.md) serves, so the signed bytes and the exported SBOM match exactly.

### The bundle (recommended)

The **signature bundle** is a single zip containing everything needed to verify offline, so prefer it over the individual files. The zip contains:

| File | Always present? | Purpose |
|---|---|---|
| `sbom-<slug>.cdx.json` | yes | the CycloneDX SBOM — the signed bytes |
| `sbom-<slug>.cdx.json.sig` | yes | the detached cosign signature over the SBOM |
| `cosign.pub` | key-based only | the cosign public key (key-based verification) |
| `sbom-<slug>.cdx.json.cert.pem` | keyless only | the Fulcio signing certificate (keyless verification) |
| `sbom-<slug>.intoto.jsonl` | when attestation succeeded | the in-toto / SLSA provenance attestation |
| `sbom-<slug>.attest.cert.pem` | keyless + attestation | the Fulcio certificate for the attestation |
| `VERIFY.md` | yes | verification instructions tailored to what the bundle contains |

`<slug>` is the project's URL slug; the zip itself is named `sbom-signature-<slug>.zip`.

Download it from the API (replace the placeholders):

```bash
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://<your-domain>/v1/projects/${PROJECT_ID}/sbom/signature-bundle"
```

`-OJ` saves the file under the server-supplied name (`sbom-signature-<slug>.zip`). Substitute `<your-domain>` with your deployment's host and `${PROJECT_ID}` / `${TRUSTEDOSS_API_KEY}` with your values.

:::tip Read VERIFY.md first
Each bundle ships a `VERIFY.md` whose commands name the **exact** files in *that* bundle (key-based vs keyless, attestation present or not). When the bundle's contents and this page disagree, trust `VERIFY.md` — it is generated from the artifacts you actually received.
:::

### Individual artifacts

If you only need one file, each is also a discrete endpoint. All require the same Developer access and return the latest succeeded scan's artifact (or `404` if it does not exist).

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /v1/projects/{project_id}/sbom/signature` | `sbom-<slug>.cdx.json.sig` | the detached signature |
| `GET /v1/projects/{project_id}/sbom/public-key` | `cosign.pub` | key-based deployments only; `404` when keyless |
| `GET /v1/projects/{project_id}/sbom/certificate` | `sbom-<slug>.cdx.json.cert.pem` | keyless deployments only; `404` when key-based |
| `GET /v1/projects/{project_id}/sbom/attestation` | `sbom-<slug>.intoto.jsonl` | the SLSA provenance attestation |
| `GET /v1/projects/{project_id}/sbom/attestation-certificate` | `sbom-<slug>.attest.cert.pem` | keyless attestation only |

The SBOM itself comes from the existing [SBOM export endpoint](../user-guide/sbom.md#download-from-the-api) (`GET /v1/projects/{project_id}/sbom?format=cyclonedx-json`).

:::note Public material only
These endpoints serve **only** public artifacts — the SBOM, the signature, the Fulcio certificate, the attestation, and the cosign **public** key. The private signing key and its password are never read, returned, or logged by the portal. The public-key endpoint additionally refuses to serve anything that looks like a private key.
:::

## 4. Verify

Unzip the bundle, then run the command that matches your deployment's signing model.

```bash
unzip sbom-signature-<slug>.zip -d sbom-verify
cd sbom-verify
```

### Key-based (default)

```bash
cosign verify-blob \
  --key cosign.pub \
  --signature sbom-<slug>.cdx.json.sig \
  sbom-<slug>.cdx.json
```

A successful run prints:

```
Verified OK
```

### Keyless (Fulcio)

Keyless verification additionally pins the signer's **identity** and **OIDC issuer** — substitute the values your operator published (for example, a CI workflow identity and its issuer URL).

```bash
cosign verify-blob \
  --certificate sbom-<slug>.cdx.json.cert.pem \
  --certificate-identity <expected-identity> \
  --certificate-oidc-issuer <expected-issuer> \
  --signature sbom-<slug>.cdx.json.sig \
  sbom-<slug>.cdx.json
```

A successful run prints `Verified OK`. A mismatch — altered SBOM bytes, the wrong key/certificate, or an identity that does not match — exits non-zero with an error such as:

```
Error: verifying blob: invalid signature when validating ASN.1 encoded signature
```

`Verified OK` means the SBOM bytes are intact **and** were signed by this deployment's signing identity.

### Inspect the provenance attestation

When the bundle contains `sbom-<slug>.intoto.jsonl`, decode its payload to read how the SBOM was produced — the in-toto Statement carries the build platform `builder.id` / `builder.version` and the SBOM-generation context:

```bash
jq -r '.payload' sbom-<slug>.intoto.jsonl | base64 -d | jq .
```

To cryptographically verify the attestation (not just decode it):

```bash
# Key-based
cosign verify-blob-attestation \
  --key cosign.pub \
  --bundle sbom-<slug>.intoto.jsonl \
  sbom-<slug>.cdx.json

# Keyless
cosign verify-blob-attestation \
  --certificate sbom-<slug>.attest.cert.pem \
  --certificate-identity <expected-identity> \
  --certificate-oidc-issuer <expected-issuer> \
  --bundle sbom-<slug>.intoto.jsonl \
  sbom-<slug>.cdx.json
```

### Verify it worked

- `cosign verify-blob` prints `Verified OK` and exits `0`.
- `jq` decodes the attestation payload and the `predicate.builder.id` matches the builder your operator configured (`SLSA_BUILDER_ID`).
- Re-downloading the SBOM produces a byte-identical file (the export is byte-stable), so the same signature verifies it again:

  ```bash
  sha256sum sbom-<slug>.cdx.json
  # → matches the digest cosign reported during verify-blob
  ```

## 5. Operator key setup

This section is for the person deploying the portal. Pick one model.

### Key-based (default)

1. Generate a cosign key pair with the bundled helper. It writes `cosign.key` (encrypted private key) and `cosign.pub` (public key), and prints the `.env` wiring:

   ```bash
   bash scripts/cosign-keygen.sh --out ./secrets/cosign
   ```

   cosign prompts for a password to encrypt the private key at rest (or reads `COSIGN_PASSWORD` if exported). If cosign is not on your PATH, run the helper inside the worker container, which ships cosign:

   ```bash
   docker-compose run --rm worker bash scripts/cosign-keygen.sh
   ```

2. Encrypt the private-key password with the app's Fernet key so it can live in `.env` as ciphertext (never plaintext). Run it **inside** the worker so it uses the same key the app decrypts with at signing time:

   ```bash
   docker-compose run --rm worker \
     python -c "import sys;from core.crypto import encrypt_secret;print(encrypt_secret(sys.argv[1]))" 'YOUR_KEY_PASSWORD'
   ```

   A passwordless key is allowed — leave `COSIGN_KEY_PASSWORD_ENCRYPTED` unset.

3. Wire the keys into `.env` (the worker mounts `COSIGN_KEYS_HOST_PATH` read-only at `/cosign`):

   ```bash
   COSIGN_KEYLESS=false
   COSIGN_KEY_PATH=/cosign/cosign.key
   COSIGN_KEY_PASSWORD_ENCRYPTED=<paste the ciphertext from step 2>
   COSIGN_KEYS_HOST_PATH=./secrets/cosign
   ```

4. **Distribute `cosign.pub` to your verifiers** — publish it next to your releases or hand it out via a trusted channel. Verifiers can also pull it from the [public-key endpoint](#individual-artifacts), but an out-of-band copy lets them verify without portal access.

:::warning Protect the private key
`cosign.key` is the deployment's signing authority. Keep it out of version control (the bundled `.gitignore` excludes `secrets/`), mount it **read-only** into the worker, and back it up securely. The portal never reads, returns, or logs the private key or its password — and neither should your operational tooling.
:::

### Keyless (opt-in)

Keyless signing needs **no key pair**. cosign drives its own OIDC identity (an ambient CI token or a configured provider) and logs the signature to Rekor. Set:

```bash
COSIGN_KEYLESS=true
```

For a **private** Sigstore deployment, also set `COSIGN_OIDC_ISSUER`, `SIGSTORE_FULCIO_URL`, and `SIGSTORE_REKOR_URL` in the worker environment. Publish the **expected identity** and **OIDC issuer** to your verifiers so they can pass `--certificate-identity` / `--certificate-oidc-issuer` to `cosign verify-blob`.

### Provenance builder identity

The attestation stamps a builder identity into the provenance. Set these so a verifier can pin provenance to a known builder:

| Key | Default | Purpose |
|---|---|---|
| `SLSA_BUILDER_ID` | a vendor-neutral URI | URI naming this build platform in the provenance `builder.id` |
| `TRUSTEDOSS_VERSION` | bundled portal version | stamped into `builder.version` and the SBOM-generation context |

See [Environment variables → cosign signing](./env-variables.md) for the full key list and runtime semantics.

## Troubleshooting

### `404` on a signature endpoint — the scan was not signed

The signature endpoints return `404` when the project's latest succeeded scan has no artifact of that kind. The usual causes:

- **Signing was never configured** — the deployment ran with no cosign key (`COSIGN_KEY_PATH` unset) and `COSIGN_KEYLESS=false`. Configure a key ([section 5](#5-operator-key-setup)) and re-run the scan.
- **The worker has no cosign binary** — only the worker image ships cosign; a custom worker may have dropped it. Check the worker logs for a `cosign_not_found` / signing warning.
- **No succeeded scan yet** — run a scan and wait for it to reach `Completed`.

The `404` body is an RFC 7807 `application/problem+json` envelope whose `detail` names the actionable reason.

### `404` on `/public-key` but `/certificate` works (or vice versa)

This is expected and tells you which model the deployment uses:

- **Key-based** → `/sbom/public-key` returns `cosign.pub`; `/sbom/certificate` returns `404`. Verify with `--key cosign.pub`.
- **Keyless** → `/sbom/certificate` returns the Fulcio cert; `/sbom/public-key` returns `404`. Verify with `--certificate …`.

The bundle picks the right file automatically, which is another reason to prefer it.

### `cosign verify-blob` fails with "invalid signature"

The SBOM bytes do not match the signature. Re-download both from the **same** bundle (do not mix a freshly exported SBOM with an older signature), and confirm you did not re-format or re-save the SBOM (the export is byte-stable; any edit breaks the signature). For keyless, also confirm `--certificate-identity` / `--certificate-oidc-issuer` match the values your operator published.

### `413` on a download

The artifact or bundle exceeds the deployment's configured download size cap. This is a server-side guard, not a client-fixable error — contact the operator.

## See also

- [SBOM](../user-guide/sbom.md) — export the SBOM the signature is over
- [Glossary](./glossary.md) — SBOM, SCA, VEX, and RBAC role definitions
- [Environment variables](./env-variables.md) — the `COSIGN_*` and `SLSA_*` keys
- [API reference (Redoc)](pathname:///reference/api) — the generated endpoint contract
- [Report an issue](https://github.com/trustedoss/trustedoss-portal/issues/new/choose) — if verification fails unexpectedly
