# Provenance of the container-alpine captures

`inprocess/container-alpine.json` and `bomlens/container-alpine.json` are
`run_golden.py --images` captures of one image,
`docker.io/library/alpine:3.19.0@sha256:51b67269f354137895d43f3b3d810bfacd3945438e94dc5ac55fdac340352f48`
(the index digest; both tools read its linux/arm64 image). No engine
comparison run has scanned an image yet, so they were not downloaded from a
run. They were built on 2026-10-13 from the output of the two tools each
engine runs, through the backend's own functions, as described below. The
tests add the scan id and the stage windows to a copy, as they do for
`run-38223776971`.

## Where each field comes from

| Field | In-process | BomLens |
| --- | --- | --- |
| Tool output | `trivy image --image-src remote --platform linux/arm64 --scanners vuln --list-all-pkgs --format json` with Trivy 0.71.2 and its database of 2026-10-12T01:14Z, the flags `integrations/trivy.py` passes | `image_1.0.0_bom.json` and `image_1.0.0_security.json` of the `image-registry-alpine` case in `tests/fixtures/bomlens/` on the branch `feature/bomlens-container-scan` (PR #534; see its `PROVENANCE.md`): BomLens 1.13.2 in `MODE=IMAGE`, database of 2026-10-12T13:18Z |
| `components.purls` | every package's `Identifier.PURL` | every SBOM component with a `purl` (the 15 libraries; the 80 file entries and the distribution have none) |
| `licenses` | `tasks.scan_container._license_ids` on each package | `tasks.scan_source._extract_spdx_ids` on each SBOM component, the reader `persist_sbom_components` uses |
| licence category | `tasks.scan_source._classify_license_category` | the same |
| `vulnerabilities_count`, `shadow.vulnerability_ids`, `shadow.vulnerability_findings` | the report's `Vulnerabilities` (ID, package, installed version) | the same, from the BomLens Trivy report |

Both sides found the same 15 packages and the same 48 findings of 21
vulnerability IDs. The licences differ, and the difference is the one the
two persistence paths make: Trivy lists `ca-certificates-bundle` as `MPL-2.0`
and `MIT`, which the in-process path keeps as two licences, while the BomLens
SBOM carries the expression `MPL-2.0 AND MIT`, which the SBOM reader keeps
whole (likewise `libc-utils` and `musl-utils`).

`scan_status`, `input` and the `shadow.scan` fields other than `kind` and
`engine` are what a succeeded scan records, with the times left empty. The
fields the report does not read (`notice`, `sbom`, `report_pdf_ok`,
`source_tree_root_entries`, `gate`) are left out rather than guessed; the
report shows `-` for the gate.

## When to replace

With the captures of the first engine comparison run that scans images
(`gh run download <run> -n golden-shadow-containers`), once PR #534 is
merged. Those come from the API after persistence, which these do not.
