"""
Schema-validation tests for ``schemas.github_app`` — v2.2-b1.

Pure (no DB). Adversarial parametrize on the untrusted registration / link
inputs per memory feedback_adversarial_input_parametrize: malformed PEM,
empty / oversized key (>16KB), control chars / NUL / CRLF in app_id / slug /
repo, junk schemes in account_login, non-numeric app_id, unicode, etc. Every
bad input must raise a clean ValidationError (→ 422 RFC 7807 at the HTTP edge),
NEVER pass through to the service.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

GOOD_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "-----END RSA PRIVATE KEY-----\n"
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_credential_create_accepts_valid() -> None:
    from schemas.github_app import GitHubAppCredentialCreateIn

    model = GitHubAppCredentialCreateIn(
        app_id="123456",
        app_slug="trustedoss-scanner",
        private_key=GOOD_PEM,
        webhook_secret="whsec",
    )
    assert model.app_id == "123456"
    assert model.private_key.startswith("-----BEGIN")


def test_credential_create_minimal() -> None:
    from schemas.github_app import GitHubAppCredentialCreateIn

    model = GitHubAppCredentialCreateIn(app_id="1", private_key=GOOD_PEM)
    assert model.app_slug is None
    assert model.webhook_secret is None


# ---------------------------------------------------------------------------
# Adversarial: private_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,private_key",
    [
        ("empty", ""),
        ("no_pem_header", "just some text, not a pem"),
        ("oversized_17kb", "-----BEGIN KEY-----\n" + ("A" * (17 * 1024))),
        ("nul_byte", "-----BEGIN RSA PRIVATE KEY-----\n\x00\n-----END-----"),
        ("only_whitespace", "    \n\t  "),
        ("base64_no_armor", "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX"),
    ],
)
def test_credential_create_rejects_bad_private_key(label: str, private_key: str) -> None:
    from schemas.github_app import GitHubAppCredentialCreateIn

    with pytest.raises(ValidationError):
        GitHubAppCredentialCreateIn(app_id="1", private_key=private_key)


# ---------------------------------------------------------------------------
# Adversarial: app_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,app_id",
    [
        ("empty", ""),
        ("non_numeric", "app-slug-not-id"),
        ("crlf", "123\r\n456"),
        ("nul", "12\x003"),
        ("unicode_digits", "１２３"),  # full-width digits — not ASCII [0-9]
        ("negative", "-5"),
        ("with_space", "12 3"),
        ("too_long", "1" * 25),
    ],
)
def test_credential_create_rejects_bad_app_id(label: str, app_id: str) -> None:
    from schemas.github_app import GitHubAppCredentialCreateIn

    with pytest.raises(ValidationError):
        GitHubAppCredentialCreateIn(app_id=app_id, private_key=GOOD_PEM)


# ---------------------------------------------------------------------------
# Adversarial: app_slug / webhook_secret (control chars)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,app_slug",
    [
        ("crlf", "good\r\nset-cookie:x"),
        ("nul", "good\x00slug"),
        ("tab_control", "good\tslug"),
    ],
)
def test_credential_create_rejects_control_chars_in_slug(label: str, app_slug: str) -> None:
    from schemas.github_app import GitHubAppCredentialCreateIn

    with pytest.raises(ValidationError):
        GitHubAppCredentialCreateIn(app_id="1", app_slug=app_slug, private_key=GOOD_PEM)


def test_credential_create_rejects_control_chars_in_webhook_secret() -> None:
    from schemas.github_app import GitHubAppCredentialCreateIn

    with pytest.raises(ValidationError):
        GitHubAppCredentialCreateIn(
            app_id="1", private_key=GOOD_PEM, webhook_secret="bad\x00secret"
        )


# ---------------------------------------------------------------------------
# Installation link — happy + adversarial
# ---------------------------------------------------------------------------


def test_installation_link_accepts_valid() -> None:
    from schemas.github_app import GitHubAppInstallationLinkIn

    model = GitHubAppInstallationLinkIn(
        installation_id="987654",
        account_login="acme-corp",
        repository_full_name="acme-corp/widgets",
    )
    assert model.installation_id == "987654"
    assert model.repository_full_name == "acme-corp/widgets"


def test_installation_link_account_wide_null_repo() -> None:
    from schemas.github_app import GitHubAppInstallationLinkIn

    model = GitHubAppInstallationLinkIn(installation_id="1")
    assert model.repository_full_name is None
    assert model.account_login is None


@pytest.mark.parametrize(
    "label,installation_id",
    [
        ("empty", ""),
        ("non_numeric", "inst-id"),
        ("crlf", "12\r\n3"),
        ("nul", "1\x002"),
    ],
)
def test_installation_link_rejects_bad_installation_id(label: str, installation_id: str) -> None:
    from schemas.github_app import GitHubAppInstallationLinkIn

    with pytest.raises(ValidationError):
        GitHubAppInstallationLinkIn(installation_id=installation_id)


@pytest.mark.parametrize(
    "label,repo",
    [
        ("no_slash", "justname"),
        ("two_slashes", "a/b/c"),
        ("scheme", "https://github.com/a/b"),
        ("crlf", "a/b\r\nx"),
        ("nul", "a/b\x00"),
        ("space", "a / b"),
    ],
)
def test_installation_link_rejects_bad_repo(label: str, repo: str) -> None:
    from schemas.github_app import GitHubAppInstallationLinkIn

    with pytest.raises(ValidationError):
        GitHubAppInstallationLinkIn(installation_id="1", repository_full_name=repo)


@pytest.mark.parametrize(
    "label,account_login",
    [
        ("scheme", "javascript:alert(1)"),
        ("path", "../../etc/passwd"),
        ("crlf", "acme\r\nx"),
        ("nul", "acme\x00"),
        ("leading_hyphen", "-acme"),
    ],
)
def test_installation_link_rejects_bad_account_login(label: str, account_login: str) -> None:
    from schemas.github_app import GitHubAppInstallationLinkIn

    with pytest.raises(ValidationError):
        GitHubAppInstallationLinkIn(installation_id="1", account_login=account_login)
