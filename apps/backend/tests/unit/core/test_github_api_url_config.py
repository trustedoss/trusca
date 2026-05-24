"""
Unit tests for ``core.config.github_api_url`` SSRF / cleartext validation —
v2.2-b1 security-reviewer follow-up (Medium #2).

Pure (no DB / no network). The accessor reads ``os.getenv`` at call time
(CLAUDE.md core rule #11), so each test monkeypatches ``APP_ENV`` +
``GITHUB_API_URL`` and asserts the prod allow-list:

  - prod + internal/loopback/link-local/metadata/private host → raises.
  - prod + http:// scheme → raises (cleartext guard).
  - prod + valid public https host → ok.
  - non-prod + http:// / localhost / private → ok (dev + local GHES).
"""

from __future__ import annotations

import pytest


def _set(monkeypatch: pytest.MonkeyPatch, *, env: str, url: str) -> None:
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("GITHUB_API_URL", url)


# ---------------------------------------------------------------------------
# Prod — rejected values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com",  # cleartext public host
        "http://github.example.com/api/v3",  # cleartext GHES
    ],
)
def test_prod_http_scheme_raises(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    from core.config import GitHubAppConfigError, github_api_url

    _set(monkeypatch, env="prod", url=url)
    with pytest.raises(GitHubAppConfigError):
        github_api_url()


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/api/v3",
        "https://localhost:8443/api/v3",
        "https://127.0.0.1/api/v3",
        "https://[::1]/api/v3",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata IP
        "https://169.254.1.1/api/v3",  # link-local
        "https://10.0.0.5/api/v3",  # RFC-1918 10/8
        "https://172.16.0.9/api/v3",  # RFC-1918 172.16/12
        "https://192.168.1.10/api/v3",  # RFC-1918 192.168/16
        "https://vault/api/v3",  # bare single-label internal name
    ],
)
def test_prod_internal_host_raises(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    from core.config import GitHubAppConfigError, github_api_url

    _set(monkeypatch, env="prod", url=url)
    with pytest.raises(GitHubAppConfigError):
        github_api_url()


# ---------------------------------------------------------------------------
# Prod — accepted values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com",
        "https://github.example.com/api/v3",  # public GHES FQDN
        "https://8.8.8.8/api/v3",  # public IP literal (allowed)
    ],
)
def test_prod_valid_public_https_ok(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    from core.config import github_api_url

    _set(monkeypatch, env="prod", url=url)
    assert github_api_url() == url.rstrip("/")


def test_prod_default_is_public_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import github_api_url

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    assert github_api_url() == "https://api.github.com"


def test_prod_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import github_api_url

    _set(monkeypatch, env="prod", url="https://api.github.com/")
    assert github_api_url() == "https://api.github.com"


# ---------------------------------------------------------------------------
# Non-prod — permissive (dev / CI / local GHES)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "staging", "test"])
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/api/v3",
        "http://127.0.0.1:9000",
        "https://localhost/api/v3",
        "http://github.internal/api/v3",
        "https://10.0.0.5/api/v3",
    ],
)
def test_non_prod_allows_any_scheme_and_host(
    monkeypatch: pytest.MonkeyPatch, env: str, url: str
) -> None:
    from core.config import github_api_url

    _set(monkeypatch, env=env, url=url)
    assert github_api_url() == url.rstrip("/")
