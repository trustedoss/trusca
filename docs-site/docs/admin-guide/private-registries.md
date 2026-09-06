---
id: private-registries
title: Private registries for dependency resolution
description: Authenticate cdxgen against a private Maven, npm, or pip registry during a source scan.
sidebar_label: Private registries (dependency resolution)
sidebar_position: 7.5
---

# Private registries for dependency resolution

A **source** scan runs `cdxgen` against your cloned repository, and `cdxgen`
shells into `mvn`, `npm`, or `pip` to resolve the project's dependency tree -
the same way a build would. If any of those dependencies live in a private
registry (an internal Nexus/Artifactory mirror, a scoped npm registry, a
private PyPI index), the worker needs a credential for it, or resolution
fails partway through and the scan's SBOM is missing whatever it could not
reach.

This is a different feature from
[Private registries](../user-guide/scans.md#private-registries) in the Scans
page. That one (ER3) authenticates **Trivy pulling a container image** for a
**container** scan. This page is about authenticating **cdxgen resolving a
project's dependencies** for a **source** scan. The two credentials are
stored differently and used by different tools.

## The convention

Put whichever of these four files you need under one directory:

| File | Ecosystem |
|---|---|
| `settings.xml` | Maven |
| `.npmrc` | npm |
| `pip.conf` | pip |
| `.netrc` | Generic HTTP/Git basic-auth - for example a private Go-module VCS host `cdxgen`'s Go analyzer shells `git`/`go list` against |

You do not need all four - cdxgen tolerates the others being absent. The
worker mounts this directory read-only at the fixed path
`/etc/trusca/registry`. The path is fixed rather than configurable so it
cannot become an arbitrary-file-read: nothing about a project or a scan
request chooses what gets mounted there, only the operator's deployment
configuration does.

## Putting the files in

### With Docker Compose

Set `REGISTRY_CONFIG_HOST_PATH` in `.env` (default `./secrets/registry`) and
place your files there:

```bash
mkdir -p ./secrets/registry
cp /path/to/settings.xml ./secrets/registry/settings.xml
cp /path/to/.npmrc ./secrets/registry/.npmrc
```

`docker-compose.yml` already mounts this directory read-only on the
scan-pipeline worker (`worker-scan` in production,
`celery-worker` in dev) - an absent or empty directory is harmless, the same
as this repository's cosign key mount.

`.netrc` is the one file with no pointer variable (see below), so it needs a
second, explicit mount to land at the right place. `docker-compose.yml` and
`docker-compose.dev.yml` both carry this line commented out next to the
directory mount - uncomment it only after you have created the file:

```yaml
- ${REGISTRY_CONFIG_HOST_PATH:-./secrets/registry}/.netrc:/home/trustedoss/.netrc:ro
```

Uncommenting it while the host file does not exist is not a no-op: Docker
creates the missing bind-mount source as an empty **directory**, which then
shadows the path for anything expecting a file there. Create the file first.

### With Helm

Create the Secret yourself, then let the chart mount it on the scan worker
alone:

```bash
kubectl create secret generic trustedoss-registry-config \
  --from-file=settings.xml=./settings.xml \
  --from-file=.npmrc=./.npmrc
```

```yaml
worker:
  scan:
    extraVolumes:
 - name: registry-config
        secret:
          secretName: trustedoss-registry-config
    extraVolumeMounts:
 - name: registry-config
        mountPath: /etc/trusca/registry
        readOnly: true
    extraEnv:
      MVN_ARGS: "--settings /etc/trusca/registry/settings.xml"
      NPM_CONFIG_USERCONFIG: /etc/trusca/registry/.npmrc
      PIP_CONFIG_FILE: /etc/trusca/registry/pip.conf
```

`worker.scan.*` is scoped to the scan-pipeline worker alone - unlike the
chart-wide `env.extraVolumes`/`env.extraEnv` used for a
[private certificate authority](./private-ca.md), which land on backend,
beat and both workers. A registry credential a source scan needs has no
reason to be readable from a pod that never runs `cdxgen`.

For `.netrc`, mount the same Secret a second time with an explicit `subPath`
so it lands at `$HOME/.netrc` (this image's `HOME` is `/home/trustedoss`)
instead of under `/etc/trusca/registry`:

```yaml
    extraVolumeMounts:
 - name: registry-config
        mountPath: /home/trustedoss/.netrc
        subPath: .netrc
        readOnly: true
```

## Pointing the tools at it

Mounting the directory changes nothing by itself - nothing reads
`/etc/trusca/registry` on its own. `settings.xml`, `.npmrc`, and `pip.conf`
each need a matching variable that tells the relevant tool where to look:

| Variable | Read by | Effect |
|---|---|---|
| `MVN_ARGS` | `cdxgen`'s own Maven invocation | Extra arguments appended to the `mvn` command line. Set it to `--settings /etc/trusca/registry/settings.xml`. |
| `NPM_CONFIG_USERCONFIG` | `npm` (standard npm environment variable) | Alternate `.npmrc` path. |
| `PIP_CONFIG_FILE` | `pip` (standard pip environment variable) | Alternate `pip.conf` path. |

Add whichever you need to `.env`:

```bash
MVN_ARGS=--settings /etc/trusca/registry/settings.xml
NPM_CONFIG_USERCONFIG=/etc/trusca/registry/.npmrc
PIP_CONFIG_FILE=/etc/trusca/registry/pip.conf
```

**There is no `MAVEN_SETTINGS` environment variable**, and setting one does
nothing. Maven itself only ever reads `~/.m2/settings.xml` or a `-s` /
`--settings` command-line flag - there is no environment-variable form.
`MVN_ARGS` is `cdxgen`'s own mechanism for passing extra arguments to the
`mvn` invocations it runs on your behalf, and `--settings <path>` is exactly
the flag it needs. (`cdxgen` prints this same guidance itself, in its own
console output, when it finds a `settings.xml` next to a `pom.xml` it is
scanning.)

`NPM_CONFIG_USERCONFIG` and `PIP_CONFIG_FILE` are not `cdxgen` features -
they are npm's and pip's own documented environment variables for an
alternate config-file path. `cdxgen` does nothing special for them beyond
inheriting the worker's environment when it shells out to `npm install` /
`pip install`, the same way any subprocess would.

`.netrc` has no pointer variable of its own. Every tool that honours it -
`git`, `go`, and curl-based fetchers - reads `$HOME/.netrc` unconditionally,
which is why it needs the second, explicit mount described above rather than
an environment variable.

## Known limits

- **One shared credential set per worker, not per project or team.** Unlike
  the container-image registry credentials (ER3), which are scoped per
  organisation and matched against the image being pulled, everything under
  `/etc/trusca/registry` is visible to every source scan the scan-pipeline
  worker runs. If different teams need different private-registry
  credentials, they currently share one worker-wide set; per-project scoping
  is not implemented.
- **Read-only, and never derived from scan input.** The mount path is fixed
  at deployment time by the operator. No project setting, API field, or
  scan-trigger parameter can change what gets mounted or read.
- **Air-gapped installs still need this page.** An offline bundle of the
  worker images and the Trivy vulnerability database (tracked separately) is
  a different concern from authenticating to an internal mirror that is
  reachable but requires a credential - you may need both, or only this one,
  depending on your network.
