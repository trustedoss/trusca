# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the finding-assignment promise (ER68, hardening rule 4).

The user guide says a finding carries who is fixing it, by when, and where the
work is tracked, and says it without qualification. For most of ER28a's life
that was false: findings are per-scan rows, and neither a rescan nor the
six-hourly rematch beat carried the four columns, so an assignment lasted an
afternoon.

What makes the sentence true is the carry-forward, and
``tests/integration/scan/test_assignment_carry_forward_db.py`` drives the four
sequences that prove it. This file is the other half of rule 4: it binds the
sentence to the mechanism, so removing the carry-forward while the guide still
promises it fails here, and softening the sentence has to be a deliberate edit
to this file rather than a quiet one to the docs.

The reverse direction is what stops this from being a string check that
protects nothing: the vocabulary is read live off
``services.vulnerability_matching``, so a column dropped from the carried set
fails the last test whatever the prose says.
"""

from __future__ import annotations

import pathlib

from services.vulnerability_matching import _ASSIGNMENT_FIELDS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
GUIDE_EN = (
    REPO_ROOT / "docs-site" / "docs" / "user-guide" / "vulnerabilities.md"
)
GUIDE_KO = (
    REPO_ROOT
    / "docs-site"
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "user-guide"
    / "vulnerabilities.md"
)

#: The sentence the carry-forward exists to make true, in both mirrors.
PROMISE = {
    GUIDE_EN: (
        "A finding can carry who is fixing it, by when, and where the work is "
        "tracked"
    ),
    GUIDE_KO: "finding 에는 누가 고치는지, 언제까지인지, 어디서 추적하는지를 담을 수 있습니다.",
}


def test_both_guides_still_make_the_promise() -> None:
    for path, sentence in PROMISE.items():
        assert path.exists(), f"{path} moved; this oracle needs updating"
        text = path.read_text(encoding="utf-8")
        assert sentence in text, (
            f"{path.name} no longer carries the sentence this contract is "
            "about. If the promise was deliberately weakened, say so here; if "
            "it was only reworded, update PROMISE. Either way the sequences in "
            "tests/integration/scan/test_assignment_carry_forward_db.py are "
            "what make it true."
        )


def test_the_guide_names_every_column_the_promise_covers() -> None:
    """Three things are promised, and four columns deliver them.

    Read from the code rather than listed here: adding a fifth assignment
    column without a word in the guide about what it does fails this, which is
    the drift rule 4 is for.
    """
    text = GUIDE_EN.read_text(encoding="utf-8")
    documented = {
        "assignee_user_id": "**Assignee**",
        "due_on": "`due_on`",
        "ticket_url": "**Ticket**",
        "ticket_key": "**Ticket**",
    }
    undeclared = set(_ASSIGNMENT_FIELDS) - set(documented)
    assert not undeclared, (
        f"{sorted(undeclared)} joined the assignment vocabulary with nothing "
        "here saying where the guide describes it"
    )
    for column, marker in documented.items():
        assert marker in text, (
            f"the guide no longer describes {column} (looked for {marker!r}), "
            "so the promise covers a column the reader is not told about"
        )
