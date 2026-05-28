"""
Recursive PII masking — `core.pii_mask.mask_pii`.

The helper walks dict / list trees, replacing values whose key matches a
sensitive token with `"***"`. We pin:

  - Top-level sensitive keys redact.
  - Nested dicts and lists recurse.
  - Case-insensitive key matching ("Password", "API_KEY", ...).
  - Substring matching ("user.password" / "X-Authorization-Token" both hit).
  - Excessive depth collapses to "***" (DoS guard).
  - Non-mapping types pass through unchanged (str / int / bool / None).
  - The function returns a deep copy — mutating the result must not change
    the input.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Top-level redaction
# ---------------------------------------------------------------------------


def test_top_level_password_redacted() -> None:
    from core.pii_mask import mask_pii

    out = mask_pii({"password": "secret", "username": "alice"})
    assert out == {"password": "***", "username": "alice"}


def test_multiple_sensitive_keys_redacted() -> None:
    from core.pii_mask import mask_pii

    src = {
        "password": "p",
        "api_key": "k",
        "access_token": "t",
        "refresh_token": "r",
        "secret": "s",
        "authorization": "a",
        "username": "alice",
    }
    out = mask_pii(src)
    for sensitive in (
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "secret",
        "authorization",
    ):
        assert out[sensitive] == "***"
    assert out["username"] == "alice"


def test_email_is_masked_as_pii() -> None:
    from core.pii_mask import mask_pii

    out = mask_pii({"email": "alice@example.com", "name": "Alice"})
    assert out["email"] == "***"
    assert out["name"] == "Alice"


# ---------------------------------------------------------------------------
# Case + substring matching
# ---------------------------------------------------------------------------


def test_case_insensitive_key_match() -> None:
    from core.pii_mask import mask_pii

    out = mask_pii({"Password": "p", "API_KEY": "k", "X-Auth-Token": "t"})
    assert out["Password"] == "***"
    assert out["API_KEY"] == "***"
    assert out["X-Auth-Token"] == "***"


def test_substring_match_in_compound_key() -> None:
    """`mask_pii` matches on token substring so `user.password` is redacted."""
    from core.pii_mask import mask_pii

    out = mask_pii({"user.password": "p", "user.name": "alice"})
    assert out["user.password"] == "***"
    assert out["user.name"] == "alice"


# ---------------------------------------------------------------------------
# Recursion — dicts and lists
# ---------------------------------------------------------------------------


def test_recurses_into_nested_dict() -> None:
    from core.pii_mask import mask_pii

    out = mask_pii(
        {
            "data": {
                "credentials": {"password": "p", "user": "alice"},
                "public": "ok",
            }
        }
    )
    assert out["data"]["credentials"]["password"] == "***"
    assert out["data"]["credentials"]["user"] == "alice"
    assert out["data"]["public"] == "ok"


def test_recurses_into_list_of_dicts() -> None:
    from core.pii_mask import mask_pii

    out = mask_pii(
        {
            "users": [
                {"name": "a", "password": "p1"},
                {"name": "b", "api_key": "k1"},
            ]
        }
    )
    assert out["users"][0] == {"name": "a", "password": "***"}
    assert out["users"][1] == {"name": "b", "api_key": "***"}


def test_tuple_input_normalized_to_list() -> None:
    """Tuples become lists — JSONB has no tuple type."""
    from core.pii_mask import mask_pii

    out = mask_pii({"items": ("a", {"password": "p"})})
    assert isinstance(out["items"], list)
    assert out["items"][1]["password"] == "***"


# ---------------------------------------------------------------------------
# DoS guard — excessive depth
# ---------------------------------------------------------------------------


def test_excessive_depth_collapses_to_redacted() -> None:
    """Beyond _MAX_DEPTH the helper short-circuits to `"***"` to avoid recursion blowups."""
    from core.pii_mask import mask_pii

    # Build a 60-deep nested dict (well past _MAX_DEPTH=32).
    payload: dict[str, object] = {"v": "leaf"}
    for _ in range(60):
        payload = {"v": payload}

    # The masker must not raise RecursionError; deep levels collapse.
    out = mask_pii(payload)

    # Walk down until we hit the redaction marker.
    cursor: object = out
    seen_redacted = False
    for _ in range(80):
        if cursor == "***":
            seen_redacted = True
            break
        if isinstance(cursor, dict) and "v" in cursor:
            cursor = cursor["v"]
            continue
        break
    assert seen_redacted


# ---------------------------------------------------------------------------
# Pass-through scalars
# ---------------------------------------------------------------------------


def test_scalar_inputs_returned_unchanged() -> None:
    from core.pii_mask import mask_pii

    assert mask_pii("hello") == "hello"
    assert mask_pii(42) == 42
    assert mask_pii(3.14) == 3.14
    assert mask_pii(True) is True
    assert mask_pii(None) is None


def test_input_not_mutated() -> None:
    """The helper must not mutate the original payload."""
    from core.pii_mask import mask_pii

    src = {"password": "p", "data": {"token": "t"}}
    snapshot = {"password": "p", "data": {"token": "t"}}
    _ = mask_pii(src)
    assert src == snapshot


def test_unknown_object_type_stringified() -> None:
    """Defensive — unknown types are stringified, not crashed."""
    from datetime import UTC, datetime

    from core.pii_mask import mask_pii

    dt = datetime(2026, 5, 6, tzinfo=UTC)
    out = mask_pii({"when": dt})
    assert isinstance(out["when"], str)


# ---------------------------------------------------------------------------
# redact_url_userinfo — M-1 fix (PR #9 security-reviewer)
# ---------------------------------------------------------------------------


def test_redact_url_userinfo_removes_pat_in_https_clone() -> None:
    from core.pii_mask import redact_url_userinfo

    raw = "https://oauth2:GH_PAT_xxxxxx@github.com/org/repo.git"
    out = redact_url_userinfo(raw)
    assert "GH_PAT_xxxxxx" not in out
    assert "oauth2" not in out
    assert "github.com" in out
    assert out.startswith("https://***@")


def test_redact_url_userinfo_preserves_path_and_query() -> None:
    from core.pii_mask import redact_url_userinfo

    raw = "https://user:pass@example.com:8443/path/seg?ref=abc#frag"
    out = redact_url_userinfo(raw)
    assert "/path/seg" in out
    assert "ref=abc" in out
    assert "8443" in out
    assert "user" not in out
    assert "pass" not in out


def test_redact_url_userinfo_passthrough_when_no_userinfo() -> None:
    from core.pii_mask import redact_url_userinfo

    raw = "https://github.com/org/repo.git"
    assert redact_url_userinfo(raw) == raw


def test_redact_url_userinfo_handles_invalid_input() -> None:
    from core.pii_mask import redact_url_userinfo

    # Non-string passthrough — `None` returned as the redaction sentinel
    # so structlog does not blow up on the kwarg type.
    assert redact_url_userinfo("") == ""
    # Defensive: wholly malformed inputs return a stable sentinel rather
    # than raising (callers are typically log kwargs).
    assert redact_url_userinfo("not a url") == "not a url"


def test_redact_url_userinfo_ssh_userinfo_only() -> None:
    from core.pii_mask import redact_url_userinfo

    # SCP-style git ssh URLs go through urlsplit too; userinfo (username
    # only, no password) should still redact.
    raw = "ssh://gituser@example.com/org/repo.git"
    out = redact_url_userinfo(raw)
    assert "gituser" not in out
    assert out.startswith("ssh://***@")
