"""
The grade a deployment has chosen for people nobody graded (N4).

Three states, and the third is the one worth a test: unset, set to a grade,
and set to something that is not a grade. The last is a typo in a `.env` file,
and the answer to an instruction nobody can read has to be the floor. Resolving
it to "no choice was made" sends both callers to their historical fallback,
which is a higher grade than whatever the operator was reaching for by writing
the setting at all, with nothing anywhere saying so.
"""

from __future__ import annotations

import pytest

from core.config import default_member_role


@pytest.mark.parametrize("value", ["viewer", "developer", "team_admin"])
def test_a_grade_is_taken_as_written(value: str, monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", value)

    assert default_member_role() == value


@pytest.mark.parametrize("value", ["", "   "])
def test_unset_means_no_choice_was_made(value: str, monkeypatch) -> None:
    """Not a grade: each caller keeps what it granted before this existed."""
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", value)

    assert default_member_role() is None


@pytest.mark.parametrize(
    "value",
    ["read-only", "readonly", "member", "user", "admin", "owner"],
)
def test_an_unreadable_instruction_resolves_to_the_floor(
    value: str, monkeypatch
) -> None:
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", value)

    assert default_member_role() == "viewer"


def test_the_deployment_wide_grade_is_refused_even_when_written(monkeypatch) -> None:
    """A setting that hands this out on arrival would make the first person
    through the door an administrator of everybody else."""
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "super_admin")

    assert default_member_role() == "viewer"


def test_spelling_is_forgiven_but_meaning_is_not(monkeypatch) -> None:
    """Case and surrounding space are a typo; a different word is not."""
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "  Viewer ")

    assert default_member_role() == "viewer"
