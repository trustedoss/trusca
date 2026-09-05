# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Doc oracle for two-step sign-in (ER19-3).

Hardening rule 4: the guide is an oracle. Every number and every promise the
guides make about this feature is a decision somewhere in the code, and prose
drifts silently when the decision moves.

Three of these exist because the review found the code claiming something the
guide did not say. A comment in ``services/mfa_service.py`` asserted that the
user guide told people enrolling does not end their other sessions; it did
not, so the residual risk was undocumented and the code said otherwise. A
sentence cannot fail, which is the argument for this file rather than for a
more careful comment.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

USER_GUIDE_EN = REPO_ROOT / "docs-site/docs/user-guide/auth-and-profile.md"
USER_GUIDE_KO = (
    REPO_ROOT
    / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
    / "user-guide/auth-and-profile.md"
)
ADMIN_GUIDE_EN = REPO_ROOT / "docs-site/docs/admin-guide/users-and-teams.md"
ADMIN_GUIDE_KO = (
    REPO_ROOT
    / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
    / "admin-guide/users-and-teams.md"
)

ALL_GUIDES = (USER_GUIDE_EN, USER_GUIDE_KO, ADMIN_GUIDE_EN, ADMIN_GUIDE_KO)


@pytest.fixture(scope="module", autouse=True)
def _guides_exist() -> None:
    """A missing file would make every "not in" assertion below pass."""
    for path in ALL_GUIDES:
        assert path.is_file(), f"{path} is missing; this file guards nothing"


def test_the_recovery_code_count_the_guide_states_is_the_code_count() -> None:
    """Ten is a decision: each code is one sign-in, not one attempt."""
    from core.recovery_codes import CODE_COUNT

    english = USER_GUIDE_EN.read_text(encoding="utf-8")
    korean = USER_GUIDE_KO.read_text(encoding="utf-8")

    # The English guide writes the number as a word, which is right for prose
    # and means this cannot just interpolate the constant.
    words = {5: "five", 10: "ten", 12: "twelve", 16: "sixteen", 20: "twenty"}
    spelled = words.get(CODE_COUNT)
    assert spelled is not None, (
        f"CODE_COUNT is {CODE_COUNT} and this test does not know how the "
        "guide would spell it; add it to the map and check the prose"
    )
    assert f"**{spelled} recovery codes**" in english, (
        f"the user guide does not state {spelled} recovery codes; "
        "CODE_COUNT moved and the prose did not"
    )
    assert f"**복구 코드 {CODE_COUNT}개**" in korean, (
        f"the Korean user guide does not state {CODE_COUNT} recovery codes"
    )


def test_the_window_between_the_two_steps_is_the_one_the_guide_promises() -> None:
    """Five minutes is the pending token's lifetime, not a round number."""
    from core.security import MFA_PENDING_EXPIRE_MINUTES

    english = USER_GUIDE_EN.read_text(encoding="utf-8")

    stated = re.search(r"You have (\w+) minutes between the two steps", english)
    assert stated is not None, (
        "the user guide no longer states how long the two steps may be apart"
    )
    words = {"five": 5, "ten": 10, "fifteen": 15, "thirty": 30}
    minutes = words.get(stated.group(1))
    assert minutes == MFA_PENDING_EXPIRE_MINUTES, (
        f"the guide says {stated.group(1)} minutes and the token lives "
        f"{MFA_PENDING_EXPIRE_MINUTES}; somebody taking the guide at its word "
        "starts again from the password"
    )


def test_the_guide_says_enrolling_does_not_end_other_sessions() -> None:
    """The residual risk of the boundary decision, in the guide rather than a comment.

    ``complete_enrolment`` deliberately does not stamp ``mfa_changed_at``,
    because the request that turns the factor on carries a token minted before
    it and stamping would sign that person out on the recovery-code screen.
    The cost is that somebody who enables a factor because they suspect
    another session is theirs to worry about has not evicted it. That has to
    be written where they will read it.
    """
    english = USER_GUIDE_EN.read_text(encoding="utf-8")
    korean = USER_GUIDE_KO.read_text(encoding="utf-8")

    assert "does not end\nsessions that are already open" in english, (
        "the user guide does not tell somebody that turning on a second "
        "factor leaves their other sessions alive, which is what the code "
        "does and what a comment in mfa_service claims the guide says"
    )
    assert "reset your password" in english
    assert "이미 열려 있는 세션을 끊지는 않으므로" in korean, (
        "the Korean guide is missing the same sentence"
    )


def test_the_admin_guide_promises_only_what_clearing_actually_does() -> None:
    """Clearing ends sessions, and now it does; unlocking must not claim to."""
    english = ADMIN_GUIDE_EN.read_text(encoding="utf-8")

    assert "ends the sessions" in english, (
        "the admin guide no longer says clearing a factor ends open sessions, "
        "which is what clear_for_user does"
    )
    # The unlock action deliberately touches nothing else, and saying it does
    # would send an operator to the wrong control during an incident.
    unchanged = "the password, the sessions and the second\nfactor are all left as they were"
    assert unchanged in english, (
        "the admin guide no longer says unlocking sign-in leaves the password, "
        "the sessions and the factor alone"
    )


def test_the_guide_states_that_api_keys_are_not_covered() -> None:
    """Pinned because the test that proves it lives somewhere else.

    ``tests/integration/test_api_key_breadth.py`` asserts a key keeps working
    for an account with a factor. That is only defensible if somebody is told,
    because it means a key is worth a password rather than less.
    """
    english = USER_GUIDE_EN.read_text(encoding="utf-8")
    korean = USER_GUIDE_KO.read_text(encoding="utf-8")

    assert "**API keys keep working.**" in english
    assert "**API Key는 그대로 동작합니다.**" in korean
