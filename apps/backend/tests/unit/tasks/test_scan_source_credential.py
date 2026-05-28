"""
Unit tests for the #18 Part B private-repo credential clone-URL injection.

These are PURE-function tests against
``tasks.scan_source.build_authenticated_clone_url`` — we never run a real git
clone. The contract under test:

  - an https URL + a token → the token is injected as userinfo
    (``https://x-access-token:<token>@host/path``), URL-encoded so a token with
    reserved chars cannot break out of the userinfo segment;
  - ssh:// / git@host: / git:// URLs → NO injection (SSH needs key material);
  - no credential / blank credential → URL unchanged;
  - an URL that already carries userinfo → not overwritten;
  - the injected-credential URL is masked by ``redact_url_userinfo`` so it can
    never reach a log line in plaintext.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest

from core.pii_mask import redact_url_userinfo
from tasks.scan_source import build_authenticated_clone_url

_TOKEN = "ghp_TopSecretToken1234567890"


# ---------------------------------------------------------------------------
# Fake sync session so the decrypt helper can be unit-tested without Postgres
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    def __init__(self, ciphertext: Any) -> None:
        self._ciphertext = ciphertext

    def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._ciphertext)


def _patch_session(monkeypatch: pytest.MonkeyPatch, ciphertext: Any) -> None:
    @contextmanager
    def _scope() -> Any:
        yield _FakeSession(ciphertext)

    monkeypatch.setattr("tasks.scan_source.sync_session_scope", _scope)


# ---------------------------------------------------------------------------
# https injection
# ---------------------------------------------------------------------------


def test_https_url_injects_credential_as_userinfo() -> None:
    url = "https://github.com/acme/private-repo.git"
    out = build_authenticated_clone_url(url, _TOKEN)
    assert out == f"https://x-access-token:{_TOKEN}@github.com/acme/private-repo.git"
    # The token is present in the produced URL (it is fed only to the subprocess).
    assert _TOKEN in out


def test_https_url_preserves_port() -> None:
    url = "https://gitlab.example.com:8443/team/repo.git"
    out = build_authenticated_clone_url(url, _TOKEN)
    assert out == (
        f"https://x-access-token:{_TOKEN}@gitlab.example.com:8443/team/repo.git"
    )


def test_https_url_url_encodes_token_with_reserved_chars() -> None:
    """A token with @ : / ? must be percent-encoded so it cannot smuggle a host."""
    nasty = "tok@evil.com:1234/path?x=y"
    out = build_authenticated_clone_url("https://github.com/a/b.git", nasty)
    # The raw '@host' breakout must NOT appear; the encoded form must.
    assert "@evil.com" not in out
    assert out.startswith("https://x-access-token:tok%40evil.com%3A1234%2Fpath%3Fx%3Dy@")
    assert out.endswith("@github.com/a/b.git")


# ---------------------------------------------------------------------------
# non-https schemes: NO injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@github.com/acme/repo.git",
        "git@github.com:acme/repo.git",
        "git://example.com/repo.git",
        "git+ssh://git@host.example.com/x/y",
    ],
)
def test_non_https_url_is_not_injected(url: str) -> None:
    assert build_authenticated_clone_url(url, _TOKEN) == url
    # And the token never appears in the returned value.
    assert _TOKEN not in build_authenticated_clone_url(url, _TOKEN)


def test_http_url_is_not_injected() -> None:
    """Only https gets token injection (http is intranet-only, no token over it)."""
    url = "http://gitlab.internal/team/repo.git"
    assert build_authenticated_clone_url(url, _TOKEN) == url


# ---------------------------------------------------------------------------
# no-op cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cred", [None, "", "   "])
def test_no_credential_leaves_url_unchanged(cred: str | None) -> None:
    url = "https://github.com/acme/repo.git"
    assert build_authenticated_clone_url(url, cred) == url


def test_url_with_existing_userinfo_is_not_overwritten() -> None:
    url = "https://existing-user:existing-pass@github.com/acme/repo.git"
    assert build_authenticated_clone_url(url, _TOKEN) == url


# ---------------------------------------------------------------------------
# redaction: the injected URL must mask for logging
# ---------------------------------------------------------------------------


def test_injected_url_is_redacted_for_logging() -> None:
    url = "https://github.com/acme/repo.git"
    injected = build_authenticated_clone_url(url, _TOKEN)
    masked = redact_url_userinfo(injected)
    assert _TOKEN not in masked
    assert "x-access-token" not in masked
    assert masked == "https://***@github.com/acme/repo.git"


# ---------------------------------------------------------------------------
# _decrypt_project_credential
# ---------------------------------------------------------------------------


def test_decrypt_credential_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored ciphertext decrypts back to the plaintext (#18 Part B)."""
    from core.crypto import encrypt_secret
    from tasks.scan_source import _decrypt_project_credential

    ciphertext = encrypt_secret(_TOKEN)
    _patch_session(monkeypatch, ciphertext)

    plaintext = _decrypt_project_credential(
        scan_uuid=uuid.uuid4(), project_id=uuid.uuid4()
    )
    assert plaintext == _TOKEN


def test_decrypt_credential_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from tasks.scan_source import _decrypt_project_credential

    _patch_session(monkeypatch, None)
    assert (
        _decrypt_project_credential(scan_uuid=uuid.uuid4(), project_id=uuid.uuid4())
        is None
    )


def test_decrypt_credential_none_when_no_project_id() -> None:
    from tasks.scan_source import _decrypt_project_credential

    # No DB access at all when there is no project id.
    assert (
        _decrypt_project_credential(scan_uuid=uuid.uuid4(), project_id=None) is None
    )


def test_decrypt_credential_failure_aborts_credential_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupted/rotated ciphertext raises _FetchAborted with NO credential bytes."""
    from tasks.scan_source import _decrypt_project_credential, _FetchAborted

    # A token that is not a valid Fernet ciphertext → decrypt_secret raises.
    bogus_ciphertext = "not-a-valid-fernet-token"
    _patch_session(monkeypatch, bogus_ciphertext)

    with pytest.raises(_FetchAborted) as excinfo:
        _decrypt_project_credential(scan_uuid=uuid.uuid4(), project_id=uuid.uuid4())

    message = str(excinfo.value)
    # The message must be credential-free AND ciphertext-free.
    assert _TOKEN not in message
    assert bogus_ciphertext not in message
    assert "decrypt" in message.lower()


# ---------------------------------------------------------------------------
# _scrub_clone_stderr — git-clone stderr is a credential sink (#18 Part B,
# Producer-Reviewer Medium fix). The credential must never survive into the
# returned string regardless of git's quoting / trailing punctuation.
# ---------------------------------------------------------------------------


def test_scrub_clone_stderr_quoted_userinfo_form() -> None:
    """git's quoted `... for 'https://user:TOKEN@host/repo.git':` form is redacted.

    This is the regression: the leading single quote defeats a per-token
    urlsplit, so the old token-wise scrubber leaked the credential.
    """
    from tasks.scan_source import _scrub_clone_stderr

    stderr = (
        "fatal: could not read Username for "
        "'https://x-access-token:ghp_SECRET@github.com/o/r.git':"
    )
    out = _scrub_clone_stderr(stderr, "ghp_SECRET")
    assert "ghp_SECRET" not in out
    assert "***" in out
    # The host/scheme context is preserved so the message stays useful.
    assert "github.com/o/r.git" in out


def test_scrub_clone_stderr_trailing_colon_no_quote_form() -> None:
    """The trailing-colon / no-quote variant is also redacted."""
    from tasks.scan_source import _scrub_clone_stderr

    stderr = "fatal: could not read Username for https://x-access-token:ghp_SECRET@host:"
    out = _scrub_clone_stderr(stderr, "ghp_SECRET")
    assert "ghp_SECRET" not in out
    assert "***" in out


def test_scrub_clone_stderr_bare_token_in_prose() -> None:
    """A bare credential echoed in prose (no URL wrapping) is still redacted.

    The regex (a) won't match prose, so the belt-and-suspenders raw replace (b)
    must catch it.
    """
    from tasks.scan_source import _scrub_clone_stderr

    stderr = "unexpected ghp_SECRET in output"
    out = _scrub_clone_stderr(stderr, "ghp_SECRET")
    assert "ghp_SECRET" not in out
    assert out == "unexpected *** in output"


def test_scrub_clone_stderr_url_encoded_token_with_reserved_chars() -> None:
    """A credential with reserved chars: neither raw nor %-encoded form survives.

    The injected userinfo is percent-encoded (build_authenticated_clone_url uses
    quote(..., safe="")), so the encoded form is what actually appears in the URL
    git echoes back. Both forms must be redacted.
    """
    from urllib.parse import quote

    from tasks.scan_source import _scrub_clone_stderr

    nasty = "tok@evil.com/secret"
    encoded = quote(nasty, safe="")
    stderr = (
        "fatal: could not read Username for "
        f"'https://x-access-token:{encoded}@github.com/a/b.git':"
    )
    out = _scrub_clone_stderr(stderr, nasty)
    # Neither the raw credential nor its URL-encoded form may survive.
    assert nasty not in out
    assert encoded not in out
    assert "***" in out


def test_scrub_clone_stderr_credential_none_still_redacts_via_regex() -> None:
    """credential=None (public repo): the regex still redacts userinfo; no crash."""
    from tasks.scan_source import _scrub_clone_stderr

    stderr = (
        "fatal: could not read Username for "
        "'https://x-access-token:ghp_SECRET@github.com/o/r.git':"
    )
    out = _scrub_clone_stderr(stderr, None)
    assert "ghp_SECRET" not in out
    assert "***@github.com/o/r.git" in out


def test_scrub_clone_stderr_truncates_to_500_chars() -> None:
    """The existing 500-char bound on the surfaced message is preserved."""
    from tasks.scan_source import _scrub_clone_stderr

    out = _scrub_clone_stderr("x" * 5000, None)
    assert len(out) == 500


def test_scrub_clone_stderr_no_credential_no_url_is_passthrough() -> None:
    """Credential-free, URL-free stderr is returned unchanged (sans truncation)."""
    from tasks.scan_source import _scrub_clone_stderr

    stderr = "fatal: repository not found"
    assert _scrub_clone_stderr(stderr, None) == stderr
