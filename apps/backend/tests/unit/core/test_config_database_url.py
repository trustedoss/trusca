"""
Unit tests for ``core.config.database_url`` resolution (Chore O — H2).

Three input modes:

1. ``DATABASE_URL`` set directly → returned verbatim (docker-compose path).
2. ``DATABASE_URL`` unset + four DB_* env vars set → composed asyncpg DSN
   (Cloud Run / Secret Manager path).
3. Neither set → :data:`DEFAULT_DATABASE_URL` (local bring-up).

Plus an error case: a partial DB_* set must fail fast with a clear message.

These tests use ``monkeypatch.delenv`` aggressively because the developer's
shell is very likely to have ``DATABASE_URL`` already set (docker-compose
``.env``). Without the explicit ``delenv`` the test would silently pass via
branch (1) regardless of what we asserted about (2) and (3).
"""

from __future__ import annotations

from urllib.parse import quote_plus

import pytest

DB_ENV_VARS = ("DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME", "DB_PORT")


@pytest.fixture
def clean_db_env(monkeypatch):
    """Strip every DB_* / DATABASE_URL env var so each test starts clean."""
    for var in DB_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Branch 1 — DATABASE_URL direct
# ---------------------------------------------------------------------------


def test_database_url_direct_value_returned_verbatim(clean_db_env):
    """A literal DATABASE_URL must be returned without modification."""
    from core import config

    clean_db_env.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://alice:s3cret@db.example.com:6543/trustedoss",
    )

    assert (
        config.database_url()
        == "postgresql+asyncpg://alice:s3cret@db.example.com:6543/trustedoss"
    )


def test_database_url_direct_wins_even_when_db_parts_set(clean_db_env):
    """If DATABASE_URL is set, the composed branch is ignored entirely.

    This protects existing docker-compose dev/prod stacks that set
    DATABASE_URL alongside POSTGRES_* env vars.
    """
    from core import config

    clean_db_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    clean_db_env.setenv("DB_USER", "should_be_ignored")
    clean_db_env.setenv("DB_PASSWORD", "should_be_ignored")
    clean_db_env.setenv("DB_HOST", "should_be_ignored")
    clean_db_env.setenv("DB_NAME", "should_be_ignored")

    assert config.database_url() == "postgresql+asyncpg://u:p@h:5432/d"


# ---------------------------------------------------------------------------
# Branch 2 — composed from DB_* parts
# ---------------------------------------------------------------------------


def test_database_url_composed_from_four_parts(clean_db_env):
    """All four DB_* set, no DATABASE_URL → asyncpg DSN composed at runtime."""
    from core import config

    clean_db_env.setenv("DB_USER", "trustedoss")
    clean_db_env.setenv("DB_PASSWORD", "simplepw")
    clean_db_env.setenv("DB_HOST", "10.0.0.5")
    clean_db_env.setenv("DB_NAME", "trustedoss")

    assert (
        config.database_url()
        == "postgresql+asyncpg://trustedoss:simplepw@10.0.0.5:5432/trustedoss"
    )


def test_database_url_composed_with_custom_port(clean_db_env):
    """DB_PORT overrides the 5432 default."""
    from core import config

    clean_db_env.setenv("DB_USER", "u")
    clean_db_env.setenv("DB_PASSWORD", "p")
    clean_db_env.setenv("DB_HOST", "h")
    clean_db_env.setenv("DB_NAME", "n")
    clean_db_env.setenv("DB_PORT", "6543")

    assert config.database_url() == "postgresql+asyncpg://u:p@h:6543/n"


def test_database_url_composed_with_cloud_sql_unix_socket_host(clean_db_env):
    """Cloud Run's DB_HOST is a unix socket path under /cloudsql/...

    asyncpg accepts a host segment that starts with '/' as a unix socket;
    we just need to make sure we don't url-encode it or otherwise mangle it.
    """
    from core import config

    clean_db_env.setenv("DB_USER", "trustedoss")
    clean_db_env.setenv("DB_PASSWORD", "abc123")
    clean_db_env.setenv("DB_HOST", "/cloudsql/proj:us-central1:demo")
    clean_db_env.setenv("DB_NAME", "trustedoss")

    expected = (
        "postgresql+asyncpg://trustedoss:abc123"
        "@/cloudsql/proj:us-central1:demo:5432/trustedoss"
    )
    assert config.database_url() == expected


def test_database_url_composed_url_encodes_password_special_chars(clean_db_env):
    """Password with @, :, /, # must be url-encoded so asyncpg can parse the DSN."""
    from core import config

    nasty = "p@ss:w/o#rd%"
    clean_db_env.setenv("DB_USER", "u")
    clean_db_env.setenv("DB_PASSWORD", nasty)
    clean_db_env.setenv("DB_HOST", "h")
    clean_db_env.setenv("DB_NAME", "n")

    encoded = quote_plus(nasty)
    # quote_plus encodes '@' → '%40', ':' → '%3A', '/' → '%2F', '#' → '%23',
    # '%' → '%25'. Sanity-check before we trust it in the DSN.
    assert "%40" in encoded
    assert "%3A" in encoded
    assert "%2F" in encoded
    assert "%23" in encoded
    assert "%25" in encoded

    assert config.database_url() == f"postgresql+asyncpg://u:{encoded}@h:5432/n"


# ---------------------------------------------------------------------------
# Branch 3 — neither set
# ---------------------------------------------------------------------------


def test_database_url_falls_back_to_default_when_nothing_set(clean_db_env):
    from core import config

    assert config.database_url() == config.DEFAULT_DATABASE_URL


# ---------------------------------------------------------------------------
# Error path — partial DB_* set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("missing_var", "match"),
    [
        ("DB_USER", "DB_USER"),
        ("DB_PASSWORD", "DB_PASSWORD"),
        ("DB_HOST", "DB_HOST"),
        ("DB_NAME", "DB_NAME"),
    ],
)
def test_database_url_partial_db_env_raises(clean_db_env, missing_var, match):
    """If the operator sets some but not all of the four parts we fail fast.

    A partial set is almost always a misconfiguration (typo in Terraform,
    forgotten Secret Manager binding, etc.). Falling through to
    DEFAULT_DATABASE_URL would mask it; raising surfaces it on first request.
    """
    from core import config

    parts = {
        "DB_USER": "u",
        "DB_PASSWORD": "p",
        "DB_HOST": "h",
        "DB_NAME": "n",
    }
    parts.pop(missing_var)
    for k, v in parts.items():
        clean_db_env.setenv(k, v)

    with pytest.raises(RuntimeError, match=match):
        config.database_url()


def test_database_url_empty_string_password_treated_as_missing(clean_db_env):
    """An empty-string DB_PASSWORD is still 'missing' for our purposes.

    Empty creds reaching asyncpg lead to a confusing
    InvalidPasswordError far from the source of truth; raising at config
    time gives a clearer error.
    """
    from core import config

    clean_db_env.setenv("DB_USER", "u")
    clean_db_env.setenv("DB_PASSWORD", "")
    clean_db_env.setenv("DB_HOST", "h")
    clean_db_env.setenv("DB_NAME", "n")

    with pytest.raises(RuntimeError, match="DB_PASSWORD"):
        config.database_url()


# ---------------------------------------------------------------------------
# Sync DSN derivation
# ---------------------------------------------------------------------------


def test_database_url_sync_strips_asyncpg_suffix_on_composed_dsn(clean_db_env):
    """Alembic uses psycopg2; the +asyncpg dialect must be stripped."""
    from core import config

    clean_db_env.setenv("DB_USER", "u")
    clean_db_env.setenv("DB_PASSWORD", "p")
    clean_db_env.setenv("DB_HOST", "h")
    clean_db_env.setenv("DB_NAME", "n")

    assert config.database_url_sync() == "postgresql://u:p@h:5432/n"


def test_database_url_sync_strips_asyncpg_suffix_on_direct_dsn(clean_db_env):
    from core import config

    clean_db_env.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/n")

    assert config.database_url_sync() == "postgresql://u:p@h:5432/n"
