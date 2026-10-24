# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the audit export's run budget (hardening rule 4, #587).

The audit-log guide and the environment-variable reference both tell an
operator how long one export run keeps posting. That figure is
``tasks.audit_export._RUN_BUDGET_SECONDS``, and nothing tied the two together:
changing the budget left every guide quoting the old one.

The expected wording is derived from the constant, so the check fails when
either side moves alone. Each guide is searched only where it describes the
export (the continuous-export section, the ``AUDIT_EXPORT_URL`` row), because
both pages mention other durations and a match elsewhere would say nothing
about this one.
"""

from __future__ import annotations

import pathlib

import pytest

from tasks import audit_export

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
DOCS = REPO_ROOT / "docs-site" / "docs"
KO_DOCS = REPO_ROOT / "docs-site" / "i18n" / "ko" / "docusaurus-plugin-content-docs" / "current"

_ENGLISH_NUMBERS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _budget_minutes() -> int:
    seconds = audit_export._RUN_BUDGET_SECONDS
    assert seconds % 60 == 0, "the guides quote whole minutes; say it differently there too"
    return int(seconds // 60)


def _english() -> str:
    minutes = _budget_minutes()
    return f"{_ENGLISH_NUMBERS[minutes]} minute{'s' if minutes != 1 else ''}"


def _korean() -> str:
    return f"{_budget_minutes()}분"


def _section(path: pathlib.Path, anchor: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.find(anchor)
    assert start != -1, f"{path} lost the {anchor} section this oracle reads"
    end = text.find("\n## ", start)
    return text[start : end if end != -1 else len(text)]


def _row(path: pathlib.Path, key: str) -> str:
    rows = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"| `{key}` |")
    ]
    assert len(rows) == 1, f"{path} should document {key} in exactly one row"
    return rows[0]


@pytest.mark.parametrize(
    ("path", "phrase"),
    [
        (DOCS / "admin-guide" / "audit-log.md", _english),
        (KO_DOCS / "admin-guide" / "audit-log.md", _korean),
    ],
    ids=["en", "ko"],
)
def test_the_audit_log_guide_quotes_the_run_budget(path, phrase) -> None:
    assert phrase() in _section(path, "{#continuous-export}")


@pytest.mark.parametrize(
    ("path", "phrase"),
    [
        (DOCS / "reference" / "env-variables.md", _english),
        (KO_DOCS / "reference" / "env-variables.md", _korean),
    ],
    ids=["en", "ko"],
)
def test_the_env_variables_reference_quotes_the_run_budget(path, phrase) -> None:
    assert phrase() in _row(path, "AUDIT_EXPORT_URL")


def test_the_task_reads_the_budget_it_is_documented_by() -> None:
    """The guides are tied to the constant; this ties the constant to the run."""
    assert audit_export._run_budget_seconds() == audit_export._RUN_BUDGET_SECONDS
