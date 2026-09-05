# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The assignable-members payload must not carry an email address (ER65/ER28b).

The picker names colleagues; it has no use for addresses, and a screen that
never receives them cannot become a place they are read from. The schema
docstring says so, which binds nobody: this asserts it.

Written as a check over the model's fields rather than over one response, so
it holds for every caller and fails the moment a field is added, not only when
some particular test happens to look at one.
"""

from __future__ import annotations

import pytest

from schemas.vulnerability_detail import AssignableMember

# Names that would carry an address, spelled the ways this repo spells them.
_ADDRESS_ISH = ("email", "e_mail", "mail", "address", "contact")


def test_the_model_exposes_no_address_field() -> None:
    fields = set(AssignableMember.model_fields)
    offending = {f for f in fields if any(w in f.lower() for w in _ADDRESS_ISH)}
    assert not offending, (
        f"AssignableMember gained {sorted(offending)}. The picker needs a name "
        "and an id; an address here turns a colleague list into a place "
        "addresses are collected from."
    )


def test_the_model_still_carries_what_the_picker_needs() -> None:
    # Guards the guard: a model that lost both fields would pass the check
    # above for the wrong reason.
    assert {"user_id", "full_name"} <= set(AssignableMember.model_fields)


@pytest.mark.parametrize("name", ["email", "contact_email", "mail_address"])
def test_the_check_would_notice_an_address_field(name: str) -> None:
    """The assertion above is an absence, so prove absence is detectable.

    Building the same test over a model that HAS such a field is the cheapest
    way to show the check is doing work rather than passing because nothing
    could ever match.
    """
    pretend_fields = {"user_id", "full_name", name}
    offending = {f for f in pretend_fields if any(w in f.lower() for w in _ADDRESS_ISH)}
    assert offending == {name}
