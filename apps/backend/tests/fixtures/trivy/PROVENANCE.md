# Trivy fixture provenance

Where each report came from, and what question it can answer. A fixture
written to match the code's expectations agrees with the code by construction,
so anything that decides behaviour starts as real tool output.

Every file here is unmodified scanner output. `Trivy.Version` and `CreatedAt`
inside each document say which build produced it and when.

## Why this file exists

The synthetic report in `tests/fixtures/sbom_ingest/realistic-trivy-sbom.json`
carries eight fields per vulnerability. Real output carries about twenty. On
2026-09-05 that difference nearly produced the wrong answer to "does the source
scan path report where a CVE came from": the synthetic one has no `DataSource`,
and reading it alone says the field never arrives. It arrives on every finding.

Nothing inside that file says it is synthetic. A test that uses it does
(`test_first_detected_sla_db.py` calls it a "synthetic 1-CVE blob"), which is
one file away from anybody looking at the fixture itself.

## Container image scans

### `alpine-3.19-image-report.json`

`alpine:3.19`, recorded 2026-06-10. Ten vulnerabilities, all with `DataSource`
naming Alpine Secdb.

### `debian-python-image-report.json`

`python:3.12-slim`, recorded 2026-08-10. Twenty-one vulnerabilities, all with
`DataSource` naming the Debian Security Tracker. Three carry `FixedVersion`;
the rest have none, which is the ordinary state for a Debian advisory with no
fix released.

### `rocky-9-image-report.json`

`rockylinux:9-minimal`, recorded 2026-08-10. Eighteen vulnerabilities, all with
`DataSource` naming Rocky Linux updateinfo.

## SBOM scan

### `centos7-rpm-sbom-report.json`

A CycloneDX document scanned with `trivy sbom`, recorded 2026-08-09. Twelve
vulnerabilities, and **none of them carry `DataSource`**. They carry
`SeveritySource: "redhat"` instead.

Whether that is a property of this ecosystem or of this particular document is
not established. It is recorded here because it is the only observed case where
the field is absent, and anything reading `DataSource` has to handle it.

## Source scans (dependency manifests)

Recorded 2026-09-05 with Trivy 0.71.2, `trivy fs --scanners vuln`, against
manifests pinning known-vulnerable versions. `ArtifactName` is `.` in both,
because the scan target was a directory; what was in that directory is below.

### `npm-lockfile-source-report.json`

A `package.json` + `package-lock.json` pinning `lodash@4.17.15`,
`minimist@1.2.0` and `axios@0.21.0`. Thirty-four vulnerabilities, all with
`DataSource`: mostly `ghsa` ("GitHub Security Advisory npm"), some
`nodejs-security-wg`.

### `pip-requirements-source-report.json`

A `requirements.txt` pinning `requests==2.19.1`, `jinja2==2.10` and
`pyyaml==5.1`. Fourteen vulnerabilities, all with `DataSource` `ghsa`
("GitHub Security Advisory pip").

## What these do not contain

No report here carries an affected version range, under any field name. The
version information Trivy reports is `InstalledVersion` and, when a fix exists,
`FixedVersion`. Six real reports across four ecosystems were checked for this
(E5b in the enterprise-readiness plan): answering "which versions are affected"
needs a source other than Trivy's output.

## `db-metadata-real-download.json`

Not a scan. The metadata document from a real `trivy --download-db-only`, kept
for the DB freshness panel.
