"""
Unit tests for ``integrations._subprocess_env`` (chore PR #6 + PR-A2).

The helper module centralises subprocess env scrubbing for prep / cdxgen
/ scancode (the ORT variant was dropped in PR-A2). These tests pin three
properties:

* The shared base allowlist forwards PATH / proxy / CA-bundle hints but
  excludes worker secrets like ``DT_API_KEY`` / ``SECRET_KEY``.
* Each per-stage builder adds the right ecosystem-specific keys.
* The prefix-band forwarders (``npm_config_*``, ``CDXGEN_*``) drop
  credential-named keys via ``_looks_like_credential``, even when the prefix
  matches.
"""

from __future__ import annotations

import pytest

from integrations._subprocess_env import (
    _looks_like_credential,
    scrubbed_env_for_cdxgen,
    scrubbed_env_for_prep,
    scrubbed_env_for_scancode,
)

# ---------------------------------------------------------------------------
# Common: secrets are stripped, base hints are forwarded
# ---------------------------------------------------------------------------


_WORKER_SECRETS = {
    "DT_API_KEY": "super-secret-dt-key",
    "SECRET_KEY": "super-secret-jwt-signing-key",
    "DATABASE_URL": "postgresql+asyncpg://trustedoss:hunter2@postgres/trustedoss",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/secret",
    "TEAMS_WEBHOOK_URL": "https://outlook.office.com/webhook/secret",
    "GITHUB_CLIENT_SECRET": "gh-oauth-secret",
}

_BASE_HINTS = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/home/worker",
    "LANG": "en_US.UTF-8",
    "TZ": "UTC",
    "SSL_CERT_FILE": "/etc/ssl/corporate-ca.pem",
    "REQUESTS_CA_BUNDLE": "/etc/ssl/corporate-ca.pem",
    "NODE_EXTRA_CA_CERTS": "/etc/ssl/corporate-ca.pem",
    "HTTP_PROXY": "http://proxy.corp.example:8080",
    "HTTPS_PROXY": "http://proxy.corp.example:8080",
    "NO_PROXY": "localhost,127.0.0.1,.corp.example",
}


@pytest.fixture
def env_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed os.environ with the worker-secret + base-hint surface."""
    for k, v in {**_WORKER_SECRETS, **_BASE_HINTS}.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize(
    "builder",
    [scrubbed_env_for_prep, scrubbed_env_for_cdxgen, scrubbed_env_for_scancode],
)
def test_builder_strips_worker_secrets(env_seeded: None, builder) -> None:  # type: ignore[no-untyped-def]
    env = builder()
    for secret_key in _WORKER_SECRETS:
        assert secret_key not in env, (
            f"{builder.__name__} leaked {secret_key} into subprocess env"
        )


@pytest.mark.parametrize(
    "builder",
    [scrubbed_env_for_prep, scrubbed_env_for_cdxgen, scrubbed_env_for_scancode],
)
def test_builder_forwards_base_proxy_and_ca_hints(
    env_seeded: None, builder
) -> None:  # type: ignore[no-untyped-def]
    env = builder()
    for hint_key, hint_value in _BASE_HINTS.items():
        assert env.get(hint_key) == hint_value, (
            f"{builder.__name__} dropped base hint {hint_key}"
        )


# ---------------------------------------------------------------------------
# prep: ecosystem keys + .NET telemetry-opt-out defaults
# ---------------------------------------------------------------------------


def test_prep_forwards_ecosystem_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOPROXY", "https://proxy.golang.org,direct")
    monkeypatch.setenv("CARGO_HOME", "/work/cargo")
    monkeypatch.setenv("BUNDLE_PATH", "/work/bundle")
    monkeypatch.setenv("NUGET_PACKAGES", "/work/nuget")

    env = scrubbed_env_for_prep()

    assert env["GOPROXY"] == "https://proxy.golang.org,direct"
    assert env["CARGO_HOME"] == "/work/cargo"
    assert env["BUNDLE_PATH"] == "/work/bundle"
    assert env["NUGET_PACKAGES"] == "/work/nuget"


def test_prep_seeds_dotnet_telemetry_optout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOTNET_CLI_TELEMETRY_OPTOUT", raising=False)
    monkeypatch.delenv("DOTNET_NOLOGO", raising=False)

    env = scrubbed_env_for_prep()

    assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert env["DOTNET_NOLOGO"] == "1"


# ---------------------------------------------------------------------------
# cdxgen: npm_config_* + CDXGEN_* prefix bands, with credential deny
# ---------------------------------------------------------------------------


def test_cdxgen_forwards_npm_config_registry_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("npm_config_registry", "https://npm.corp.example")
    monkeypatch.setenv("npm_lifecycle_event", "install")
    monkeypatch.setenv("npm_package_name", "foo")

    env = scrubbed_env_for_cdxgen()

    assert env["npm_config_registry"] == "https://npm.corp.example"
    assert env["npm_lifecycle_event"] == "install"
    assert env["npm_package_name"] == "foo"


def test_cdxgen_drops_npm_credentials_inside_prefix_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even though ``npm_config_*`` is allow-listed, credential-named
    keys inside that band are stripped — npm stores ``_authToken`` /
    ``_password`` / ``_auth`` here.
    """
    monkeypatch.setenv("npm_config__authToken", "npm-secret-1")
    monkeypatch.setenv("npm_config__auth", "base64-creds")
    monkeypatch.setenv("npm_config__password", "hunter2")
    monkeypatch.setenv("npm_config_email", "test@example.com")  # PII via deny? no — email passes
    # Sanity: a benign key in the same band still passes.
    monkeypatch.setenv("npm_config_registry", "https://npm.corp.example")

    env = scrubbed_env_for_cdxgen()

    assert "npm_config__authToken" not in env
    assert "npm_config__auth" not in env
    assert "npm_config__password" not in env
    assert env["npm_config_registry"] == "https://npm.corp.example"


def test_cdxgen_forwards_cdxgen_operator_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDXGEN_GRADLE_ARGS", "--init-script /w/init.gradle")
    monkeypatch.setenv("CDXGEN_DEBUG", "1")

    env = scrubbed_env_for_cdxgen()

    assert env["CDXGEN_GRADLE_ARGS"] == "--init-script /w/init.gradle"
    assert env["CDXGEN_DEBUG"] == "1"


def test_cdxgen_drops_cdxgen_token_band(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDXGEN_GH_TOKEN", "gh-secret")
    monkeypatch.setenv("CDXGEN_AUTH", "auth-secret")

    env = scrubbed_env_for_cdxgen()

    assert "CDXGEN_GH_TOKEN" not in env
    assert "CDXGEN_AUTH" not in env


def test_cdxgen_forwards_jvm_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAVA_HOME", "/opt/java/temurin-21")
    monkeypatch.setenv("JAVA_OPTS", "-Xmx2g")
    monkeypatch.setenv("GRADLE_USER_HOME", "/work/gradle")

    env = scrubbed_env_for_cdxgen()

    assert env["JAVA_HOME"] == "/opt/java/temurin-21"
    assert env["JAVA_OPTS"] == "-Xmx2g"
    assert env["GRADLE_USER_HOME"] == "/work/gradle"


# ---------------------------------------------------------------------------
# scancode (PR-A2): base allowlist only — no toolchain keys, no prefix band
# ---------------------------------------------------------------------------


def test_scancode_forwards_base_hints_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """scancode is pure Python: it gets PATH / proxy / CA hints but no JVM /
    Node / ecosystem toolchain keys (it never shells into a language runtime)."""
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/corporate-ca.pem")
    # Toolchain keys that cdxgen forwards but scancode does NOT need.
    monkeypatch.setenv("JAVA_HOME", "/opt/java/temurin-21")
    monkeypatch.setenv("GOPROXY", "https://proxy.golang.org")

    env = scrubbed_env_for_scancode()

    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["HTTPS_PROXY"] == "http://proxy.corp.example:8080"
    assert env["REQUESTS_CA_BUNDLE"] == "/etc/ssl/corporate-ca.pem"
    # No toolchain leakage — scancode has no use for these.
    assert "JAVA_HOME" not in env
    assert "GOPROXY" not in env


def test_scancode_strips_worker_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """scancode reads attacker-controlled file contents — secrets must not
    inherit so an embedded payload / scancode CVE has nothing to exfiltrate."""
    monkeypatch.setenv("DT_API_KEY", "super-secret")
    monkeypatch.setenv("SECRET_KEY", "jwt-signing-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")

    env = scrubbed_env_for_scancode()

    assert "DT_API_KEY" not in env
    assert "SECRET_KEY" not in env
    assert "DATABASE_URL" not in env


def test_scancode_seeds_home_and_lang_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME / LANG defaults are seeded so scancode's caches/locale resolve."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("LANG", raising=False)

    env = scrubbed_env_for_scancode()

    assert env["HOME"] == "/tmp"  # noqa: S108 — matches the module default hint
    assert env["LANG"] == "C.UTF-8"


def test_scancode_forwards_native_lib_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image-set libarchive / libmagic path hints MUST reach scancode, or
    it crashes at import on arm64 (undefined symbol: archive_read_new)."""
    monkeypatch.setenv("EXTRACTCODE_LIBARCHIVE_PATH", "/usr/local/lib/scancode-libarchive.so")
    monkeypatch.setenv("TYPECODE_LIBMAGIC_PATH", "/usr/local/lib/scancode-libmagic.so")

    env = scrubbed_env_for_scancode()

    assert env["EXTRACTCODE_LIBARCHIVE_PATH"] == "/usr/local/lib/scancode-libarchive.so"
    assert env["TYPECODE_LIBMAGIC_PATH"] == "/usr/local/lib/scancode-libmagic.so"


# ---------------------------------------------------------------------------
# _looks_like_credential — keep the heuristic boundaries pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "NPM_TOKEN",
        "npm_config__authToken",
        "ORT_API_KEY",
        "CDXGEN_GH_TOKEN",
        "FOO_PASSWORD",
        "FOO_PASSWD",
        "FOO_PASSPHRASE",
        "FOO_SECRET",
        "FOO_CREDENTIALS",
        "FOO_PRIVATE_KEY",
        "FOO_PRIVATEKEY",
        # security-reviewer L1 (chore PR #6) — session-bearing keys.
        "ORT_SESSIONID",
        "CDXGEN_BEARER_TOKEN",
        "npm_config_cookie",
    ],
)
def test_credential_heuristic_flags_secret_keys(key: str) -> None:
    assert _looks_like_credential(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "PATH",
        "JAVA_HOME",
        "GOPROXY",
        "CDXGEN_GRADLE_ARGS",
        "ORT_DATA_DIR",
        "npm_config_registry",
        "npm_lifecycle_event",
    ],
)
def test_credential_heuristic_passes_benign_keys(key: str) -> None:
    assert _looks_like_credential(key) is False


# ---------------------------------------------------------------------------
# CLAUDE.md core rule #11 — env is read at call time, not import
# ---------------------------------------------------------------------------


def test_each_call_reflects_current_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper resolves env at call time so operator changes are honored.

    Two calls separated by ``monkeypatch.setenv`` see different values.
    """
    monkeypatch.setenv("GOPROXY", "https://proxy-a.example")
    first = scrubbed_env_for_prep()

    monkeypatch.setenv("GOPROXY", "https://proxy-b.example")
    second = scrubbed_env_for_prep()

    assert first["GOPROXY"] == "https://proxy-a.example"
    assert second["GOPROXY"] == "https://proxy-b.example"
