---
id: private-ca
title: Private certificate authorities
description: Make scans and feeds work when your network uses an internal certificate authority or a TLS-intercepting proxy.
sidebar_label: Private certificate authorities
sidebar_position: 7
---

# Private certificate authorities

If your organisation runs its own certificate authority, or a proxy that
terminates TLS and re-signs it, the portal's outbound connections will not
verify until it trusts that authority. The symptom is usually a scan that fails
while cloning, or vulnerability feeds that stop refreshing.

This page is about that case. It is not about
[air-gapped operation](./vulnerability-data.md#air-gapped), where there is no
outbound connection to secure at all.

## Two steps, in this order

1. Put the certificate inside the containers.
2. Point the tools at it.

Neither works alone, and the second one is where a correct-looking setup can
still be wrong, because the tools do not all read the same variable and the
same variable does not mean the same thing to all of them.

## Putting the certificate in

Mount it read-only into the backend, worker and beat containers. With Docker
Compose, add an override file next to `docker-compose.yml`:

```yaml
# docker-compose.override.yml
services:
  backend:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
  worker:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
  beat:
    volumes:
      - /etc/ssl/corp/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
```

Compose reads `docker-compose.override.yml` automatically, so upgrades that
replace `docker-compose.yml` leave your mount alone.

The file must hold your authority **and** the public roots, concatenated. The
next section says why.

```bash
cat /path/to/corp-root-ca.pem /etc/ssl/certs/ca-certificates.crt \
  > /etc/ssl/corp/ca-bundle.pem
```

### With Helm

Create the Secret yourself, then let the chart mount it and set the variables:

```bash
kubectl create secret generic corp-ca --from-file=ca-bundle.pem=/etc/ssl/corp/ca-bundle.pem
```

```yaml
env:
  extraVolumes:
    - name: corp-ca
      secret:
        secretName: corp-ca
  extraVolumeMounts:
    - name: corp-ca
      mountPath: /etc/ssl/corp-ca.pem
      subPath: ca-bundle.pem
      readOnly: true
  extraEnv:
    SSL_CERT_FILE: /etc/ssl/corp-ca.pem
    NODE_EXTRA_CA_CERTS: /etc/ssl/corp-ca.pem
    REQUESTS_CA_BUNDLE: /etc/ssl/corp-ca.pem
    GIT_SSL_CAINFO: /etc/ssl/corp-ca.pem
```

`subPath` is what makes the certificate a file rather than a directory over
that path, and without it the variables above name something that is not
there. The mount goes to the backend, the scheduler and both workers, and not
to the frontend or Redis, which make no outbound calls.

The same bundle rule applies: your authority and the public roots in one file,
for the reason in the next section.

## Pointing the tools at it

Add these to your `.env`:

```bash
SSL_CERT_FILE=/etc/ssl/corp-ca.pem
NODE_EXTRA_CA_CERTS=/etc/ssl/corp-ca.pem
REQUESTS_CA_BUNDLE=/etc/ssl/corp-ca.pem
GIT_SSL_CAINFO=/etc/ssl/corp-ca.pem
```

Four names for one file, because the tools disagree. Setting all four is the
short answer; the table says which one each of them needs, for when only part
of the pipeline is failing.

| Variable | Read by | Effect |
|---|---|---|
| `SSL_CERT_FILE` | Trivy, cosign, govulncheck, and the portal's own HTTPS calls | **Adds** for the Go tools, which keep reading the system directory. **Replaces** for the portal itself. |
| `SSL_CERT_DIR` | The same set | A directory of hashed certificates, an alternative to the file. |
| `NODE_EXTRA_CA_CERTS` | cdxgen | Adds. The built-in roots stay. |
| `REQUESTS_CA_BUNDLE` | scancode, scanoss | Adds for those two. The portal's own calls **ignore** it. |
| `GIT_SSL_CAINFO` | `git clone` | Adds. git reads neither `SSL_CERT_FILE` nor `CURL_CA_BUNDLE`. |
| `GIT_SSL_CAPATH` | `git clone` | The directory form of the above. |

Two of those rows are worth reading twice.

**`SSL_CERT_FILE` replaces the trust set for the portal's own connections.**
The vulnerability feeds, licence lookups, Slack and Teams notifications, the
GitHub and GitLab calls and the ticket webhook all go out through one HTTP
client, and that client builds its trust from this file alone. Point it at a
file holding only your authority and every public endpoint becomes
unverifiable, while Trivy and cdxgen keep working because they still consult
the system store. The result reads as a feed outage rather than a certificate
problem. Concatenating the public roots into the same file, as above, avoids
it.

**`git clone` reads only its own two variables.** If cloning is the step that
fails and everything else works, `GIT_SSL_CAINFO` is the one that was missing.

## Checking it worked

Each of the three processes states its own trust set at boot, and names
itself. Every line this page is about carries the prefix `tls_trust`, which is
what to filter on. A worker's own output is mostly scan progress, so the line
is there and unfindable without it:

```bash
docker-compose logs backend worker beat | grep tls_trust
```

```
tls_trust.outbound  process=api     authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
tls_trust.outbound  process=worker  authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
tls_trust.outbound  process=beat    authorities=140 bundled_authorities=120 source=SSL_CERT_FILE path=/etc/ssl/corp-ca.pem
```

Look for all three. Compose gives each service its own environment, so a
certificate configured for one and missed on another is an ordinary mistake,
and the worker is the one that matters most: the scanners reach the network
from there. A `process=worker` line that still reads `source=bundled` means the
worker never got the setting.

`authorities` is how many certificate authorities the portal's own HTTPS calls
will accept and `bundled_authorities` is how many it ships with. A number
larger than the bundled one means your authority was added to the public roots,
which is what you want in most deployments.

If the file replaced them instead, a warning follows:

```
tls_trust.public_roots_dropped  authorities=1 bundled_authorities=120 ...
```

That is the mistake described above. Concatenate the public roots into your
bundle, or ignore the warning if trusting only your own authority is
deliberate, which it is on a network that reaches nothing else.

When `SSL_CERT_DIR` is used instead of a file, `authorities` reads `null`. A
trust store built from a directory loads certificates as it needs them, so
there is no count to give, and printing zero would be worse than printing
nothing.

## Known limits

Container image pulls performed by the Docker daemon are outside the portal's
configuration. If a container scan fails to pull an image from an internal
registry behind a private authority, the certificate has to be installed for
the daemon on that host; nothing in the portal's environment reaches it.

