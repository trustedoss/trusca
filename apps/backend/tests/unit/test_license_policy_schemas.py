"""
Schema-level adversarial tests for ``schemas.license_policy`` — v2.2 Track C (c1).

These are PURE (no DB). They prove that untrusted upsert payloads either
validate cleanly or raise a Pydantic ``ValidationError`` (which the FastAPI
handler renders as a 422 RFC 7807 envelope) — NEVER an unhandled exception /
500. Required per MEMORY: feedback_adversarial_input_parametrize (untrusted-input
parsing must parametrize oversized / control-char / wrong-type cases).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from schemas.license_policy import LicenseException, LicensePolicyUpsertIn

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_empty_body_valid() -> None:
    p = LicensePolicyUpsertIn()
    assert p.category_overrides == {}
    assert p.license_exceptions == []
    assert p.unknown_license_category == "conditional"
    # Compound strategy filled with defaults.
    assert p.compound_operator_strategy == {
        "AND": "most_restrictive",
        "OR": "least_restrictive",
        "WITH": "most_restrictive",
    }
    assert p.enabled is True


def test_full_valid_body() -> None:
    p = LicensePolicyUpsertIn(
        name="Engineering",
        category_overrides={"MPL-2.0": "forbidden", "MIT": "allowed"},
        license_exceptions=[
            LicenseException(spdx_id="GPL-3.0-only", reason="legal waiver TICKET-1"),
            LicenseException(
                spdx_id="LGPL-3.0",
                reason="vendored",
                expires_at=datetime(2026, 12, 31, tzinfo=UTC),
                component_purl="pkg:pypi/x@1.2.3",
            ),
        ],
        unknown_license_category="forbidden",
        compound_operator_strategy={"OR": "most_restrictive"},
        enabled=False,
    )
    assert p.category_overrides["MPL-2.0"] == "forbidden"
    assert len(p.license_exceptions) == 2
    # Partial compound map is merged with defaults.
    assert p.compound_operator_strategy["OR"] == "most_restrictive"
    assert p.compound_operator_strategy["AND"] == "most_restrictive"


# ---------------------------------------------------------------------------
# Adversarial — must raise ValidationError (→ 422), never 500
# ---------------------------------------------------------------------------


def test_oversized_override_map_rejected() -> None:
    huge = {f"Lic-{i}": "allowed" for i in range(10_000)}
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(category_overrides=huge)


def test_oversized_exceptions_array_rejected() -> None:
    huge = [{"spdx_id": f"L{i}", "reason": "x"} for i in range(10_000)]
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(license_exceptions=huge)


@pytest.mark.parametrize(
    "label,bad_value",
    [
        ("invalid_category_string", "allow"),
        ("uppercase_category", "ALLOWED"),
        ("unknown_is_not_a_posture", "unknown"),
        ("empty_string", ""),
        ("arbitrary_string", "banana"),
        ("numeric", 1),
        ("none", None),
    ],
)
def test_invalid_override_category_value_rejected(label: str, bad_value: object) -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(category_overrides={"MIT": bad_value})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "label,bad_posture",
    [
        ("unknown_not_allowed", "unknown"),
        ("garbage", "nope"),
        ("uppercase", "FORBIDDEN"),
        ("empty", ""),
    ],
)
def test_invalid_unknown_license_category_rejected(label: str, bad_posture: str) -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(unknown_license_category=bad_posture)


@pytest.mark.parametrize(
    "label,bad_key",
    [
        ("empty_key", ""),
        ("whitespace_key", "   "),
        ("null_byte", "MIT\x00"),
        ("crlf", "MIT\r\nInjected: yes"),
        ("tab", "MIT\tx"),
        ("control_char", "MIT\x01"),
        ("oversized_key", "L" * 5000),
    ],
)
def test_malicious_override_keys_rejected(label: str, bad_key: str) -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(category_overrides={bad_key: "allowed"})


@pytest.mark.parametrize(
    "label,payload",
    [
        ("overrides_is_list", {"category_overrides": ["MIT"]}),
        ("overrides_is_string", {"category_overrides": "MIT"}),
        ("exceptions_is_dict", {"license_exceptions": {"spdx_id": "MIT"}}),
        ("exceptions_is_string", {"license_exceptions": "GPL"}),
        ("compound_is_list", {"compound_operator_strategy": ["AND"]}),
        ("enabled_is_string", {"enabled": "yes-please"}),
        ("name_is_int", {"name": 12345}),
    ],
)
def test_wrong_typed_fields_rejected(label: str, payload: dict) -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(**payload)


def test_unknown_extra_key_rejected() -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(secret_backdoor=True)  # type: ignore[call-arg]


def test_name_with_control_char_rejected() -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(name="bad\x00name")


def test_oversized_name_rejected() -> None:
    with pytest.raises(ValidationError):
        LicensePolicyUpsertIn(name="x" * 5000)


# ---------------------------------------------------------------------------
# LicenseException entry validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,payload",
    [
        ("missing_spdx", {"reason": "x"}),
        ("missing_reason", {"spdx_id": "MIT"}),
        ("empty_spdx", {"spdx_id": "", "reason": "x"}),
        ("empty_reason", {"spdx_id": "MIT", "reason": ""}),
        ("null_byte_spdx", {"spdx_id": "MIT\x00", "reason": "x"}),
        ("control_char_purl", {"spdx_id": "MIT", "reason": "x", "component_purl": "p\x00"}),
        ("extra_key", {"spdx_id": "MIT", "reason": "x", "evil": 1}),
        ("bad_expires_at", {"spdx_id": "MIT", "reason": "x", "expires_at": "not-a-date"}),
    ],
)
def test_license_exception_invalid_rejected(label: str, payload: dict) -> None:
    with pytest.raises(ValidationError):
        LicenseException(**payload)


def test_license_exception_valid_minimal() -> None:
    exc = LicenseException(spdx_id="MIT", reason="ok")
    assert exc.spdx_id == "MIT"
    assert exc.expires_at is None
    assert exc.component_purl is None


def test_license_exception_reason_allows_newlines() -> None:
    exc = LicenseException(spdx_id="MIT", reason="line1\nline2\tindented")
    assert "\n" in exc.reason
