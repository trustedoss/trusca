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
process's environment unless we strip it: any inherited ``DT_API_KEY`` /
``SECRET_KEY`` / ``DATABASE_URL`` / ``*_WEBHOOK_URL`` then becomes a
covert exfil channel through resolver telemetry, crash reports, or DNS
lookups in error paths.

Public API
----------
* :func:`scrubbed_env_for_prep` — for the lockfile-resolver step.
* :func:`scrubbed_env_for_cdxgen` — for the cdxgen invocation.
* :func:`scrubbed_env_for_ort` — for the ORT invocation.

All three resolve ``os.environ`` *at call time* — never at import — so
``os.getenv(...)`` patches in tests, and operator changes that cycle the
worker, take effect on the next subprocess.
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

# ORT — JVM toolchain plus the ``ORT_*`` operator-override band.
_ORT_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        "JAVA_HOME",
        "JAVA_OPTS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "GRADLE_USER_HOME",
        "GRADLE_OPTS",
        "MAVEN_OPTS",
        "MAVEN_HOME",
        "M2_HOME",
    }
)

_ORT_PREFIX_ALLOWLIST: tuple[str, ...] = ("ort_",)


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


def scrubbed_env_for_ort() -> dict[str, str]:
    """Env dict for the ORT invocation (chore PR #6)."""
    return _build_env(
        explicit=_BASE_ALLOWLIST | _ORT_EXTRA_ALLOWLIST,
        prefix_allow=_ORT_PREFIX_ALLOWLIST,
        defaults=_GENERIC_DEFAULTS,
    )


__all__ = [
    "scrubbed_env_for_cdxgen",
    "scrubbed_env_for_ort",
    "scrubbed_env_for_prep",
]
