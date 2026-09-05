# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The drawer guide describes the fields the detail response actually has.

Hardening rule 4: the guide is an oracle. It said the Affected section shows
"the upstream-reported affected range" for as long as that section existed.
There is no such field on ``AffectedComponent``, and Trivy does not report a
version range under any name (checked against six recorded reports across four
ecosystems). A reader following the guide went looking for something that was
never built.

Nothing failed, because nothing compared the two. This does.

It is deliberately narrow. It does not try to check the whole page against the
whole schema, which would fail on every prose sentence that mentions a concept
rather than a field. It pins the specific claims that were wrong, and the one
this change adds.
"""

from __future__ import annotations

import pathlib

import pytest

from schemas.vulnerability_detail import (
    AffectedComponent,
    MatchingProvenance,
    VulnerabilityDetailResponse,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
GUIDE = REPO_ROOT / "docs-site/docs/user-guide/vulnerabilities.md"
GUIDE_KO = (
    REPO_ROOT
    / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/user-guide/vulnerabilities.md"
)


def test_the_guide_does_not_promise_an_affected_version_range() -> None:
    """The claim that was wrong, kept out by name.

    Rewording it back in would need somebody to write "affected range" again,
    and this says what to do instead: if the field arrives, add it to
    ``AffectedComponent`` first.
    """
    fields = set(AffectedComponent.model_fields)
    assert "affected_range" not in fields, (
        "AffectedComponent now has a range field; update this test and the "
        "guide together"
    )

    for page in (GUIDE, GUIDE_KO):
        text = page.read_text(encoding="utf-8").lower()
        assert "affected range" not in text, (
            f"{page.name} promises an affected range again. Trivy does not "
            "report one, so nothing can fill it; if that changed, the field "
            "goes on AffectedComponent before the sentence goes on the page."
        )
        assert "영향 범위" not in page.read_text(encoding="utf-8"), page.name


@pytest.mark.parametrize("page", [GUIDE, GUIDE_KO])
def test_both_pages_describe_the_provenance_section(page: pathlib.Path) -> None:
    """The section exists in the response, so both mirrors say so.

    A page that documents it in one language only leaves the other set of
    readers unable to tell the "no feed reported" state from a bug.
    """
    text = page.read_text(encoding="utf-8")
    assert "Why this match" in text, (
        f"{page.name} does not describe the provenance section that the "
        "drawer renders"
    )


def test_the_response_carries_what_the_pages_describe() -> None:
    """The other direction: the field the guide names is really there.

    Without this the pair could be satisfied by deleting the field and the
    sentence, which is a different decision than the one recorded here.
    """
    assert "matching_provenance" in VulnerabilityDetailResponse.model_fields
    assert set(MatchingProvenance.model_fields) == {"name", "id", "feed_url"}
