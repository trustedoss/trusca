"""
Group claim to grade, for the generic identity provider.

The rules worth pinning are the refusals and the defaults. A group means
nothing until a deployment says what it means, so the interesting cases are
the ones where the answer is "less than you might expect": an unmapped group,
a deployment that maps nothing, and a mapping that names the grade this path
will not hand out.
"""

from __future__ import annotations

import pytest

from integrations.oauth.base import OAuthUserInfo
from services.oauth_service import _grade_for


def _info(*groups: str) -> OAuthUserInfo:
    return OAuthUserInfo(
        provider="oidc",
        provider_user_id="sub-1",
        email="person@example.test",
        full_name=None,
        avatar_url=None,
        groups=tuple(groups),
    )


def test_a_deployment_that_maps_nothing_keeps_the_historical_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing changes for the flow this path was written for."""
    monkeypatch.delenv("OIDC_GROUP_ROLE_MAP", raising=False)

    assert _grade_for(_info("anything")) == "team_admin"


def test_an_unmatched_person_gets_the_floor_once_mapping_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a deployment that mapped its groups, carrying none of them is an answer."""
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "platform:team_admin")

    assert _grade_for(_info("finance")) == "viewer"
    assert _grade_for(_info()) == "viewer"


def test_the_highest_mapped_grade_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OIDC_GROUP_ROLE_MAP", "readers:viewer,engineers:developer,platform:team_admin"
    )

    assert _grade_for(_info("readers", "platform", "engineers")) == "team_admin"
    assert _grade_for(_info("readers", "engineers")) == "developer"
    assert _grade_for(_info("readers")) == "viewer"


def test_a_group_cannot_name_the_deployment_wide_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise whoever can create a group can mint an administrator.

    The pair is dropped rather than rejected loudly, so the rest of a mapping
    still applies; the person lands on the floor instead of the grade the
    operator wrote.
    """
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "admins:super_admin,engineers:developer")

    assert _grade_for(_info("admins")) == "viewer"
    assert _grade_for(_info("admins", "engineers")) == "developer"


def test_a_malformed_pair_does_not_take_the_rest_of_the_mapping_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "broken,:developer,engineers:developer, :viewer")

    assert _grade_for(_info("engineers")) == "developer"
    assert _grade_for(_info("broken")) == "viewer"
