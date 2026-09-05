# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The registry of encrypted columns matches the code, and stays put (E22b).

Why this file exists
--------------------
A column's encryption key can be changed by editing one call site. Nothing
fails: the service encrypts and decrypts with the same key, so its own tests
agree with themselves. What breaks is rows written before the change, and no
test writes those.

That happened on 2026-09-05. A change moved
``registry_credentials.password_encrypted`` from the shared key to a derived
subkey with no re-encryption. The module it edited already carried a docstring
warning against exactly that outcome, reached by a different route: the
sentence forbade changing what the shared key derives, and the change instead
moved a column out from under it. A sentence cannot fail. These can.

The snapshot below can be edited to make this pass. That is a real limit of
this kind of test and it is not closed here; what is closed is doing it
silently, and what the failure message does is tell somebody who does not yet
know what they broke.
"""

from __future__ import annotations

import ast
import pathlib

from core.encrypted_columns import ENCRYPTED_COLUMNS, columns_for_purpose, purposes

BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: What each column is encrypted under, as of the last deliberate decision.
#:
#: Changing an entry here is a data migration, not an edit. Rows already in
#: every deployment hold ciphertext under the old key; moving the column
#: without moving the rows makes them unreadable, and the symptom is a feature
#: quietly failing rather than an error at deploy time.
EXPECTED_PURPOSES: dict[str, str | None] = {
    "github_app_credentials.private_key_encrypted": None,
    "github_app_credentials.webhook_secret_encrypted": None,
    "projects.git_credential_encrypted": None,
    "registry_credentials.password_encrypted": None,
    "projects.webhook_secret_encrypted": None,
}


def test_the_registry_holds_exactly_the_columns_the_snapshot_names() -> None:
    """A column added without a snapshot entry, or removed without one.

    Adding is the common case and the message says what to do. Removing is
    rarer and needs the same thought: a column that leaves the registry is a
    column rotation stops visiting.
    """
    registered = {c.label for c in ENCRYPTED_COLUMNS}
    expected = set(EXPECTED_PURPOSES)

    added = sorted(registered - expected)
    removed = sorted(expected - registered)
    assert not added, (
        f"new encrypted column(s) {added} are in core.encrypted_columns but "
        "not in this snapshot. Add them here with the key they use, so that a "
        "later change of that key is visible as a change to this file."
    )
    assert not removed, (
        f"encrypted column(s) {removed} left core.encrypted_columns. If the "
        "column is gone, drop it here too. If it still holds ciphertext, put "
        "it back: rotation only visits what the registry lists, and a column "
        "it skips keeps rows on a key the operator is about to remove."
    )


def test_no_column_changed_which_key_opens_it() -> None:
    """The guard the registry-credential defect needed.

    Names both purposes in the message, because somebody who moved a column
    usually did it on purpose and does not yet know what it costs.
    """
    moved = {
        c.label: (EXPECTED_PURPOSES[c.label], c.purpose)
        for c in ENCRYPTED_COLUMNS
        if c.label in EXPECTED_PURPOSES and c.purpose != EXPECTED_PURPOSES[c.label]
    }
    assert not moved, (
        "these columns changed which key opens them: "
        + "; ".join(
            f"{label} was {before!r}, is now {after!r}"
            for label, (before, after) in sorted(moved.items())
        )
        + ". Ciphertext already stored in every deployment was written under "
        "the old key and will not open under the new one, so this needs a "
        "migration that re-encrypts those rows in the same change. Update "
        "this snapshot only once that migration exists."
    )


def test_every_encryption_call_site_passes_the_purpose_the_registry_records()  -> None:
    """The registry describes the code, rather than sitting beside it.

    Read structurally rather than by matching text: a call is found as a call,
    and its ``purpose`` keyword as a keyword. A comment naming a purpose, or a
    string in a docstring, is not a call and does not count.
    """
    services = BACKEND / "services"
    found: dict[str, set[str | None]] = {}

    # Rotation reads the registry and passes each column's purpose through as
    # a variable, so its calls describe every purpose rather than choosing
    # one. Including it would assert the registry against itself.
    generic = {"key_rotation_service.py"}

    for path in sorted(services.glob("*.py")):
        if path.name in generic:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in {"encrypt_secret", "decrypt_secret"}:
                continue
            purpose: str | None = None
            for keyword in node.keywords:
                if keyword.arg != "purpose":
                    continue
                if isinstance(keyword.value, ast.Constant):
                    purpose = keyword.value.value
                elif isinstance(keyword.value, ast.Name):
                    purpose = constants.get(keyword.value.id, "<unresolved>")
                else:
                    purpose = "<unresolved>"
            found.setdefault(path.name, set()).add(purpose)

    assert found, (
        "no encrypt_secret / decrypt_secret call was found in services/, so "
        "this test is asserting against an empty set and would pass whatever "
        "the registry said"
    )

    known = set(purposes())
    unknown = {
        module: sorted(str(p) for p in used - known)
        for module, used in found.items()
        if used - known
    }
    assert not unknown, (
        f"these modules encrypt under a key the registry does not list: "
        f"{unknown}. Add the column and its purpose to "
        "core.encrypted_columns, or the rotation command will not know the "
        "rows exist."
    )


def test_columns_for_purpose_covers_every_column() -> None:
    """Rotation walks the registry one purpose at a time.

    A column whose purpose is not in ``purposes()`` would be visited by no
    pass at all, which is the silent skip this whole file is about.
    """
    covered = [c for p in purposes() for c in columns_for_purpose(p)]
    assert sorted(c.label for c in covered) == sorted(
        c.label for c in ENCRYPTED_COLUMNS
    )
