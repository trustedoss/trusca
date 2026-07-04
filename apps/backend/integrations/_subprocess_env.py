"""
Subprocess env scrubbing for the scan pipeline.

CLAUDE.md core rule #11 (no module-level env caching) + security-reviewer
Medium #1 (chore PR #4) + Medium #1 v2 (chore PR #6) — combined source of
truth.

Threat model
------------
``cdxgen`` is a Node binary that, on a hostile clone, may load
attacker-controlled ``package.json`` / ``pom.xml`` / ``cdxgen.config.json``
plugins; ``ort`` is a JVM tool whose ``--rules-file`` / ``--ort-file``
inputs partly originate from the cloned source; the lockfile-resolver
``prep`` subprocesses (``bundle lock`` / ``cargo generate-lockfile`` /
``go mod tidy`` / ``dotnet restore``) read attacker-controlled manifests
to fetch from the network. Each of these surfaces sees the worker
process's environment unless we strip it: any inherited ``SECRET_KEY`` /
``DATABASE_URL`` / ``*_WEBHOOK_URL`` then becomes a covert exfil channel
through resolver telemetry, crash reports, or DNS lookups in error paths.
The W6-removed ``DT_API_KEY`` is no longer present in the worker env, but
the scrub allowlist remains tight as defence in depth.

Public API
----------
* :func:`scrubbed_env_for_prep` — for the lockfile-resolver step.
* :func:`scrubbed_env_for_cdxgen` — for the cdxgen invocation.
* :func:`scrubbed_env_for_scancode` — for the scancode invocation (PR-A2).

All resolve ``os.environ`` *at call time* — never at import — so
``os.getenv(...)`` patches in tests, and operator changes that cycle the
worker, take effect on the next subprocess.

PR-A2 removed the ORT integration; :func:`scrubbed_env_for_ort` and the
``_ORT_*`` allowlists were dropped along with it. scancode is a pure-Python
tool that needs no JVM / Node toolchain, so its env is the shared base
allowlist (PATH / HOME / proxy / CA hints) plus the generic defaults — no
ecosystem-specific keys, and no prefix band (scancode has no operator-override
env surface that we forward).
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Base allowlist (shared)
# ---------------------------------------------------------------------------

# Variables every subprocess needs (resolver, Node, JVM alike) plus the
# corporate TLS-intercept proxy hints from chore PR #5 L1. The proxy
# variables are operator-set on the worker, never carried in via the
# clone, so they are not an exfil vector.
_BASE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        # Corporate CA / proxy hints (chore PR #5 L1).
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# ``/tmp`` is the HOME hint when the worker image leaves HOME unset.
# S108 collision/symlink-race scenarios do not apply: HOME is a config
# directory hint, not a tempfile path, and the workspace is wiped at the
# end of every scan. (See scan_source._scrubbed_env historical comment.)
_GENERIC_DEFAULTS: dict[str, str] = {
    "HOME": "/tmp",  # noqa: S108 — HOME hint, not a tempfile path
    "LANG": "C.UTF-8",
}

_PREP_DEFAULTS: dict[str, str] = {
    **_GENERIC_DEFAULTS,
    # Otherwise ``dotnet restore`` phones home on first invocation —
    # noisy and a covert exfil channel for any future env we ship.
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
}


# ---------------------------------------------------------------------------
# Per-stage extra allowlists
# ---------------------------------------------------------------------------

# Prep — Ruby / Cargo / Go / .NET / Java toolchain knobs the resolvers
# legitimately consult.
_PREP_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Go
        "GOFLAGS",
        "GOPROXY",
        "GOSUMDB",
        "GOMODCACHE",
        "GOCACHE",
        # Cargo / Rust
        "CARGO_HOME",
        "RUSTUP_HOME",
        # .NET
        "DOTNET_CLI_TELEMETRY_OPTOUT",
        "DOTNET_NOLOGO",
        "NUGET_PACKAGES",
        # Java / Maven / Gradle
        "JAVA_HOME",
        "MAVEN_OPTS",
        "GRADLE_USER_HOME",
        # Ruby / bundler
        "BUNDLE_PATH",
        "BUNDLE_USER_HOME",
        "GEM_HOME",
    }
)

# cdxgen — Node + every language toolchain it dispatches into. cdxgen
# itself is invoked once per scan but it shells out to ``go list`` /
# ``mvn`` / ``gradle`` etc., so the same per-ecosystem variables that
# prep needs apply here too. ``CDXGEN_*`` is the operator override band
# (see chore PR #5 Part C ``CDXGEN_GRADLE_ARGS``); credential-named keys
# in that band are still stripped via ``_looks_like_credential``.
_CDXGEN_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Node
        "NODE_PATH",
        "NODE_OPTIONS",
        # JVM (cdxgen invokes Maven/Gradle for Java SBOMs)
        "JAVA_HOME",
        "JAVA_OPTS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "MAVEN_OPTS",
        "MAVEN_HOME",
        "M2_HOME",
        "GRADLE_USER_HOME",
        "GRADLE_OPTS",
        # Go (cdxgen calls ``go list``)
        "GOFLAGS",
        "GOPROXY",
        "GOSUMDB",
        "GOMODCACHE",
        "GOCACHE",
        # Python (cdxgen consults pip configuration)
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
    }
)

# Prefix bands (case-insensitive) — any ``os.environ`` key whose name
# starts with one of these is forwarded *unless* the key looks
# credential-bearing per ``_looks_like_credential``.
_CDXGEN_PREFIX_ALLOWLIST: tuple[str, ...] = (
    "npm_config_",  # user / project npm config; can carry registry URLs.
    "npm_lifecycle_",  # npm script lifecycle context (event, etc.).
    "npm_package_",  # npm script package metadata.
    "cdxgen_",  # cdxgen operator overrides (CDXGEN_GRADLE_ARGS et al.).
)

# ---------------------------------------------------------------------------
# Credential heuristic
# ---------------------------------------------------------------------------

# Substrings (case-insensitive) that mark a key as credential-bearing.
# Applies to the prefix-band forwarders only — explicit allowlist entries
# are accepted verbatim because we hand-picked them. The substrings are
# deliberately broad: better to drop a benign ``CDXGEN_AUTHORIZED_HOSTS``
# than to forward an attacker-readable ``NPM_CONFIG__AUTHTOKEN``. (npm's
# auth keys live as ``_authToken`` / ``_auth`` / ``_password``;
# environment-mapped they become ``npm_config__authToken`` etc., which
# match ``"auth"`` / ``"password"`` here.)
_CREDENTIAL_DENY_SUBSTRINGS: tuple[str, ...] = (
    "auth",
    "token",
    "password",
    "passwd",
    "passphrase",
    "secret",
    "credential",
    "apikey",
    "api_key",
    "private_key",
    "privatekey",
    # Session-bearing identifiers. cdxgen / ORT plugins occasionally
    # accept ``*_SESSION`` / ``*_SESSIONID`` / ``*_BEARER`` / ``*_COOKIE``
    # variables for registry login. Drop them inside the prefix bands
    # for the same reason ``token`` is dropped — security-reviewer L1
    # (chore PR #6).
    "session",
    "bearer",
    "cookie",
)


def _looks_like_credential(key: str) -> bool:
    lk = key.lower()
    return any(deny in lk for deny in _CREDENTIAL_DENY_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _build_env(
    *,
    explicit: frozenset[str],
    prefix_allow: tuple[str, ...] = (),
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    """Construct a scrubbed env dict from ``os.environ``.

    Order of operations:
      1. Inherit each explicit allowlist key verbatim (``os.environ.get``).
      2. For each prefix in ``prefix_allow`` (case-insensitive),
         inherit prefix-matching keys *unless* the key matches the
         credential heuristic.
      3. Seed any ``defaults`` for keys still missing.

    Resolved at call time per CLAUDE.md core rule #11.
    """
    out: dict[str, str] = {}
    for key in explicit:
        value = os.environ.get(key)
        if value is not None:
            out[key] = value
    if prefix_allow:
        for env_key, env_value in os.environ.items():
            lk = env_key.lower()
            if not any(lk.startswith(p) for p in prefix_allow):
                continue
            if _looks_like_credential(env_key):
                continue
            out.setdefault(env_key, env_value)
    if defaults:
        for k, v in defaults.items():
            out.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def scrubbed_env_for_prep() -> dict[str, str]:
    """Env dict for the lockfile-resolver subprocesses (chore PR #4)."""
    return _build_env(
        explicit=_BASE_ALLOWLIST | _PREP_EXTRA_ALLOWLIST,
        defaults=_PREP_DEFAULTS,
    )


def scrubbed_env_for_cdxgen() -> dict[str, str]:
    """Env dict for the cdxgen invocation (chore PR #6)."""
    return _build_env(
        explicit=_BASE_ALLOWLIST | _CDXGEN_EXTRA_ALLOWLIST,
        prefix_allow=_CDXGEN_PREFIX_ALLOWLIST,
        defaults=_GENERIC_DEFAULTS,
    )


# scancode native-lib path hints. The worker image (Dockerfile.worker) sets
# these to point scancode's ctypes loader at a COMPLETE system libarchive /
# libmagic (the bundled aarch64 wheels are empty stubs). They are
# image-operator-set paths, never carried in via the clone, so they are not an
# exfil vector — but they MUST be forwarded or scancode crashes at import on
# arm64 with ``undefined symbol: archive_read_new``.
_SCANCODE_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        "EXTRACTCODE_LIBARCHIVE_PATH",
        "TYPECODE_LIBMAGIC_PATH",
        # scancode honours these for its index / temp dirs; harmless to forward.
        "SCANCODE_CACHE",
        "SCANCODE_LICENSE_INDEX_CACHE",
    }
)


def scrubbed_env_for_scancode() -> dict[str, str]:
    """Env dict for the scancode invocation (PR-A2).

    scancode is pure Python: it needs the shared base allowlist (PATH / HOME /
    proxy / CA hints) plus the generic defaults (HOME / LANG) plus the
    image-set native-lib path hints (``EXTRACTCODE_LIBARCHIVE_PATH`` /
    ``TYPECODE_LIBMAGIC_PATH`` — see :data:`_SCANCODE_EXTRA_ALLOWLIST`). It has
    no language-toolchain dependency and no operator-override prefix band we
    forward, so — unlike cdxgen — it gets a small env. Worker secrets
    (``SECRET_KEY`` / ``DATABASE_URL`` / ``*_WEBHOOK_URL``)
    are stripped: scancode reads attacker-controlled file contents from the
    clone, so an embedded-payload or scancode CVE must not have a credential
    to exfiltrate.
    """
    return _build_env(
        explicit=_BASE_ALLOWLIST | _SCANCODE_EXTRA_ALLOWLIST,
        defaults=_GENERIC_DEFAULTS,
    )


# ---------------------------------------------------------------------------
# SCANOSS (Phase J — opt-in vendored-OSS identification)
# ---------------------------------------------------------------------------

# scanoss-py is pure Python. It reads the API endpoint / key from CLI flags
# (see integrations.scanoss._build_command), not env, so no ``SCANOSS_*`` keys
# are forwarded here — the adapter passes ``--apiurl`` / ``--key`` explicitly.
# It DOES honour standard proxy / CA hints (in _BASE_ALLOWLIST) so an operator
# behind a TLS-intercepting proxy can still reach osskb.org. Worker secrets
# (``SECRET_KEY`` / ``DATABASE_URL`` / ``*_WEBHOOK_URL``) are stripped: scanoss
# reads attacker-controlled file contents while fingerprinting, so a scanoss CVE
# or a hostile payload must have no credential to exfiltrate.
_SCANOSS_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        # scanoss-py caches settings / the .scanoss dir under HOME; harmless.
        "SCANOSS_SETTINGS",
    }
)


def scrubbed_env_for_scanoss() -> dict[str, str]:
    """Env dict for the ``scanoss-py scan`` invocation (Phase J).

    The shared base allowlist (PATH / HOME / proxy / CA hints) plus the generic
    defaults — and nothing else. The API endpoint + key travel as CLI flags, not
    env, and worker secrets are stripped (scanoss fingerprints attacker-supplied
    source, so a compromise must find no credential in the environment).
    """
    return _build_env(
        explicit=_BASE_ALLOWLIST | _SCANOSS_EXTRA_ALLOWLIST,
        defaults=_GENERIC_DEFAULTS,
    )


# ---------------------------------------------------------------------------
# cosign (v2.3-s1 — SBOM signing)
# ---------------------------------------------------------------------------

# cosign's keyless flow needs the OIDC / Sigstore endpoints, which it reads from
# its own ``COSIGN_*`` / ``SIGSTORE_*`` env band. These are operator-set on the
# worker (or ambient CI identity), never carried in via the clone, so they are
# not an exfil vector — but they MUST be forwarded for keyless to work behind a
# private Fulcio/Rekor or with a non-default OIDC issuer. ``COSIGN_PASSWORD`` is
# DELIBERATELY EXCLUDED here: the cosign adapter sets it explicitly on the env it
# passes to the subprocess (decrypted at call time), so forwarding an inherited
# one would (a) be redundant and (b) risk leaking an operator's shell-exported
# password into a path we did not intend. ``COSIGN_KEY_PASSWORD_ENCRYPTED`` is
# config, not a cosign env, and is never forwarded.
_COSIGN_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Keyless OIDC / Sigstore endpoints + identity hints.
        "COSIGN_EXPERIMENTAL",
        "COSIGN_OIDC_ISSUER",
        "COSIGN_OIDC_CLIENT_ID",
        "SIGSTORE_OIDC_ISSUER",
        "SIGSTORE_REKOR_URL",
        "SIGSTORE_FULCIO_URL",
        "FULCIO_URL",
        "REKOR_URL",
        "TUF_ROOT",
        "SIGSTORE_ID_TOKEN",
    }
)


def scrubbed_env_for_cosign() -> dict[str, str]:
    """Env dict for the cosign invocation (v2.3-s1).

    The shared base allowlist (PATH / HOME / proxy / CA hints) plus the
    cosign / Sigstore endpoint band — and nothing else. Worker secrets
    (``SECRET_KEY`` / ``DATABASE_URL`` / ``*_WEBHOOK_URL``) are
    stripped: cosign signs an attacker-influenced SBOM blob, so a cosign CVE or
    a hostile plugin path must have no credential to exfiltrate. The cosign
    adapter sets ``COSIGN_PASSWORD`` on the returned dict itself (decrypted at
    call time) — it is intentionally NOT inherited here.
    """
    return _build_env(
        explicit=_BASE_ALLOWLIST | _COSIGN_EXTRA_ALLOWLIST,
        defaults=_GENERIC_DEFAULTS,
    )


# ---------------------------------------------------------------------------
# Trivy (W6-#40 / W6-#44 — SBOM matching + image scan + DB lifecycle)
# ---------------------------------------------------------------------------

# Trivy's env surface is small and well-documented: cache/DB location, mirror
# repos for air-gapped operators, timeout / quiet knobs, and an insecure-TLS
# escape hatch for self-hosted mirrors with private CAs. Registry credentials
# are loaded from ``~/.docker/config.json``, not env, so no ``TRIVY_*TOKEN``
# entries are forwarded. ``TRIVY_OFFLINE_SCAN`` is included so the W6-#44
# air-gapped lifecycle can disable network DB pulls without re-plumbing the
# adapter. (security-reviewer M1 on PR #196 — bring trivy to parity with
# cdxgen / scancode / cosign env scrubbing.)
_TRIVY_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        "TRIVY_CACHE_DIR",
        "TRIVY_DB_REPOSITORY",
        "TRIVY_JAVA_DB_REPOSITORY",
        "TRIVY_OFFLINE_SCAN",
        "TRIVY_TIMEOUT",
        "TRIVY_NO_PROGRESS",
        "TRIVY_QUIET",
        "TRIVY_INSECURE",
    }
)


def scrubbed_env_for_trivy() -> dict[str, str]:
    """Env dict for ``trivy image`` / ``trivy sbom`` invocations (W6-#40).

    The shared base allowlist (PATH / HOME / proxy / CA hints) plus the
    Trivy operator-knob band (cache / mirror / timeout) — and nothing else.
    Worker secrets (``DT_API_KEY`` / ``SECRET_KEY`` / ``DATABASE_URL`` /
    ``*_WEBHOOK_URL``) are stripped: trivy parses attacker-influenced SBOM
    JSON and container images, so a trivy CVE or a fault-injected SBOM with
    a parser-exploit payload must have no credential to exfiltrate via
    crash reports, DNS lookups in error paths, or future trivy telemetry.
    Registry credentials are read from ``~/.docker/config.json``, not env,
    so no ``TRIVY_*TOKEN`` entries are forwarded here either.
    """
    return _build_env(
        explicit=_BASE_ALLOWLIST | _TRIVY_EXTRA_ALLOWLIST,
        defaults=_GENERIC_DEFAULTS,
    )


__all__ = [
    "scrubbed_env_for_cdxgen",
    "scrubbed_env_for_cosign",
    "scrubbed_env_for_prep",
    "scrubbed_env_for_scancode",
    "scrubbed_env_for_scanoss",
    "scrubbed_env_for_trivy",
]
