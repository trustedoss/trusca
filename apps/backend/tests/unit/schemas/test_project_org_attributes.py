"""
The two validators behind a project's organizational attributes.

Tested here rather than only through the API because the API redacts
validation messages, so the sentence naming the allowed values never reaches
an HTTP client and the rejection is indistinguishable from Pydantic's own.
A direct caller does see it, and the rejection itself is the property worth
holding: a typo that silently became "unspecified" would leave somebody
looking at a screen that says they narrowed nothing.
"""

from __future__ import annotations

import pytest

from schemas.scan import (
    DISTRIBUTION_MODELS,
    _validate_distribution_model,
    _validate_org_attribute,
)


@pytest.mark.parametrize("value", DISTRIBUTION_MODELS)
def test_every_declared_model_is_accepted(value: str) -> None:
    assert _validate_distribution_model(value) == value


@pytest.mark.parametrize("value", ["sass", "SaaS", "shipped", "internal-only"])
def test_anything_else_is_refused_by_name(value: str) -> None:
    with pytest.raises(ValueError, match="distribution_model must be one of"):
        _validate_distribution_model(value)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_nothing_said_stays_nothing_said(value: str | None) -> None:
    """Blank is not a sixth category: it means judged as though it ships every way."""
    assert _validate_distribution_model(value) is None


def test_a_free_text_attribute_is_trimmed() -> None:
    """So that "Platform" and "Platform " are one bucket, not two that look alike."""
    assert _validate_org_attribute("  Platform  ") == "Platform"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_blank_attribute_becomes_null(value: str | None) -> None:
    assert _validate_org_attribute(value) is None
