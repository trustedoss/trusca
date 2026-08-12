"""
Unit tests for the license-name alias normalizer — Phase E (P2-7).

``services.license_normalize.normalize_license_name`` maps a free-text license
NAME to a canonical SPDX id for well-known aliases (the cdxgen
``{"license": {"name": "..."}}`` shape). Two properties matter:

  - Recognized aliases resolve to the RIGHT id (incl. tie-breaks: MIT/X11 → MIT,
    zlib/libpng → Zlib, ShareAlike before plain Attribution, BSD-4 before -3/-2).
  - Everything else returns ``None`` — an unrecognized name, a compound
    expression, or a version we do not carry — so the caller keeps its skip
    behaviour rather than guessing (the jq's "return unchanged" safety valve).

The §2 vocabulary-contract test at the bottom guards the cross-module invariant:
every id the normalizer can emit must be a classifier-known SPDX id, or the
recovery would hand ``_classify_license_category`` an id it maps to ``unknown``.
"""

from __future__ import annotations

import pytest

from services.license_normalize import normalize_license_name

# (free-text input, expected SPDX id). Mirrors real cdxgen / scanner spellings.
_RECOGNIZED: list[tuple[str, str]] = [
    # Copyleft families (version + "or later" handling).
    ("GNU General Public License v2.0", "GPL-2.0-only"),
    ("GNU General Public License v3.0 or later", "GPL-3.0-or-later"),
    ("GNU Lesser General Public License v2.1 or later", "LGPL-2.1-or-later"),
    ("GNU Library General Public License v3.0", "LGPL-3.0-only"),
    ("GNU Affero General Public License v3.0", "AGPL-3.0-only"),
    # Apache / MIT / Eclipse (ported from spdx-normalize.jq).
    ("Apache License, Version 2.0", "Apache-2.0"),
    ("The Apache Software License, Version 1.1", "Apache-1.1"),
    ("The MIT License", "MIT"),
    ("Expat License", "MIT"),
    ("MIT No Attribution", "MIT-0"),
    ("MIT/X11", "MIT"),
    ("X11/MIT", "MIT"),
    ("X11 License", "X11"),
    ("Eclipse Public License 2.0", "EPL-2.0"),
    ("Eclipse Public License - v 1.0", "EPL-1.0"),
    ("Eclipse Distribution License 1.0", "BSD-3-Clause"),
    # BSD family (4 before 3 before 2; zlib/libpng before libpng).
    ("BSD 4-Clause", "BSD-4-Clause"),
    ("BSD 3-Clause License", "BSD-3-Clause"),
    ("BSD 2-Clause", "BSD-2-Clause"),
    ("zlib/libpng License", "Zlib"),
    ("libpng License", "Libpng"),
    # Phase E permissive additions.
    ("Boost Software License 1.0", "BSL-1.0"),
    ("Artistic License 2.0", "Artistic-2.0"),
    ("The PostgreSQL License", "PostgreSQL"),
    ("Academic Free License v3.0", "AFL-3.0"),
    ("Universal Permissive License", "UPL-1.0"),
    ("Blue Oak Model License 1.0.0", "BlueOak-1.0.0"),
    ("Microsoft Public License", "MS-PL"),
    ("Microsoft Reciprocal License", "MS-RL"),
    ("The PHP License, version 3.01", "PHP-3.01"),
    ("OpenSSL License", "OpenSSL"),
    ("curl License", "curl"),
    ("NTP License", "NTP"),
    ("Ruby License", "Ruby"),
    ("SIL Open Font License 1.1", "OFL-1.1"),
    # Creative Commons (most restricted wins; pinned to 4.0). The restriction
    # clause sits between "attribution" and the version, which is exactly where
    # a `.*` used to swallow it — see the regression test below.
    ("Creative Commons Attribution 4.0 International", "CC-BY-4.0"),
    ("Creative Commons Attribution-ShareAlike 4.0 International", "CC-BY-SA-4.0"),
    ("Creative Commons Attribution-NonCommercial 4.0 International", "CC-BY-NC-4.0"),
    (
        "Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International",
        "CC-BY-NC-SA-4.0",
    ),
    (
        "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International",
        "CC-BY-NC-ND-4.0",
    ),
    ("Creative Commons Attribution-NoDerivatives 4.0 International", "CC-BY-ND-4.0"),
    ("CC BY-NC 4.0", "CC-BY-NC-4.0"),
    ("CC-BY-NC-SA-4.0", "CC-BY-NC-SA-4.0"),
    ("CC-BY-NC-ND-4.0", "CC-BY-NC-ND-4.0"),
    ("CC BY-ND 4.0", "CC-BY-ND-4.0"),
    # S5-B — the OSORI alias list resolves names the hand-written rules were
    # never given. CC-BY-3.0 used to come back as None; the point of that entry
    # was that 3.0 must not be silently remapped to 4.0, and it still is not.
    # Landing on the correct id it does have is the improvement: the component
    # goes from "licence unidentified" to "CC-BY-3.0, which our catalogue does
    # not classify" — which the OSORI reference panel can then describe.
    ("Creative Commons Attribution 3.0", "CC-BY-3.0"),
    ("The MIT License (MIT)", "MIT"),
    ("Apache 2", "Apache-2.0"),
]

# Inputs that MUST return None: unrecognized, compound, or an unsupported version.
_UNRECOGNIZED: list[str | None] = [
    None,
    "",
    "   ",
    "Acme Proprietary EULA 2.0",
    "MIT OR Apache-2.0",            # compound — do not collapse to one id
    "GPL-2.0-only AND Classpath",  # compound
    "Artistic License 1.0",        # only Artistic-2.0 is carried
]


@pytest.mark.parametrize("raw,expected", _RECOGNIZED)
def test_recognized_aliases_resolve(raw: str, expected: str) -> None:
    assert normalize_license_name(raw) == expected


@pytest.mark.parametrize("raw", _UNRECOGNIZED)
def test_unrecognized_or_compound_returns_none(raw: str | None) -> None:
    assert normalize_license_name(raw) is None


# A restriction clause we did not anticipate. The ordering of ``_RULES`` only
# protects the spellings someone thought to write down; these check the
# lookahead, which is what makes an unknown variant fail closed.
_UNANTICIPATED_RESTRICTED: list[str] = [
    "Creative Commons Attribution-NonCommercial-NoDerivs 4.0",
    "Creative Commons Attribution NonCommercial 4.0",
    "Creative Commons Attribution, NoDerivatives, 4.0",
]


@pytest.mark.parametrize("raw", _UNANTICIPATED_RESTRICTED)
def test_restricted_cc_never_falls_through_to_plain_attribution(raw: str) -> None:
    """A restricted CC name resolves to a restricted id, or to nothing at all.

    What it must never do is resolve to ``CC-BY-4.0``. Landing there is not a
    near-miss: the catalogue calls that licence allowed, so a NonCommercial
    dependency would pass a build gate that exists to stop it, and the review
    flag would stay silent because the flag reads the id this function returned.
    ``None`` is the safe answer — the caller skips the entry and the component
    is reported with no licence rather than the wrong one.
    """
    result = normalize_license_name(raw)
    assert result != "CC-BY-4.0"
    assert result is None or result.startswith("CC-BY-N")


def test_non_commercial_reaches_the_gate_and_the_review_flag() -> None:
    """End-to-end over the three modules a licence name passes through.

    Each was correct on its own while the defect was live: the normalizer knew
    only unrestricted CC names, the catalogue knew only the ids it was given,
    and the flag classifier was never handed the original name — persistence
    stores ``name=spdx_id``, so the id the normalizer returns is the only text
    the classifier ever sees. Testing them separately is what let a
    NonCommercial licence be stored as allowed and unflagged.
    """
    from services.license_flags import classify_review_flag
    from tasks.scan_source import _classify_license_category

    spdx_id = normalize_license_name(
        "Creative Commons Attribution-NonCommercial 4.0 International"
    )
    assert spdx_id == "CC-BY-NC-4.0"
    assert _classify_license_category(spdx_id) == "forbidden"
    assert classify_review_flag(spdx_id, spdx_id) == "non_commercial"


def test_separator_class_is_normalized() -> None:
    """Spaces, dots, dashes, underscores and slashes all read as one gap."""
    for spelling in ("Apache-2.0", "Apache_2.0", "Apache.2.0", "Apache 2 0", "apache/2.0"):
        assert normalize_license_name(spelling) == "Apache-2.0"


def test_long_input_is_bounded_and_safe() -> None:
    """A pathological long name neither matches spuriously nor is slow."""
    assert normalize_license_name("x" * 100_000) is None
    # A recognized token beyond the scan bound is intentionally not found.
    assert normalize_license_name("y" * 500 + " MIT License") is None


def test_non_string_input_returns_none() -> None:
    assert normalize_license_name(123) is None  # type: ignore[arg-type]


def test_every_emitted_id_is_classifier_known() -> None:
    """§2 vocabulary contract: every id the normalizer can emit must be a
    classifier-known SPDX id.

    The normalizer exists to turn a free-text name into an id the classifier
    recognizes. If it emitted an id absent from ``_LICENSE_CATEGORY_DEFAULTS``,
    the recovery would classify as ``unknown`` anyway — a silent no-op. Parse the
    rule targets out of the module and assert the set is a subset of the
    classifier vocabulary.
    """
    from services import license_normalize
    from tasks.scan_source import _LICENSE_CATEGORY_DEFAULTS

    emitted = {
        target
        for _pat, target in license_normalize._RULES
        if isinstance(target, str)
    }
    assert emitted, "no emittable ids found — module layout changed?"
    unknown = emitted - set(_LICENSE_CATEGORY_DEFAULTS)
    assert unknown == set(), (
        f"normalizer emits ids the classifier does not know: {sorted(unknown)}"
    )


def test_osori_aliases_may_reach_outside_the_catalog_but_stay_classifiable() -> None:
    """The OSORI fall-through has a different contract from the rules above.

    The hand-written ``_RULES`` are held to the 52-license catalogue: they are
    ours, and an id the classifier does not know would be a mistake in our own
    table. The OSORI alias list is the opposite by design — its whole value is
    reaching the ~620 licences we do not classify, so most of what it resolves
    lands outside the catalogue on purpose.

    What must hold is that landing outside is *survivable*: the classifier
    answers with a real category instead of raising, so the component ends up
    labelled "CC-BY-3.0, unclassified" rather than "licence unidentified".
    That is the upgrade.

    Almost all of them come back ``unknown``, which is the honest answer for a
    licence we have not classified. A few do better: the classifier falls
    through to the expression evaluator, so a ``WITH``-form id inherits its
    left operand's category — ``GPL-3.0-only WITH GCC-exception-3.1`` resolves
    to ``forbidden`` off the GPL. Asserting "always unknown" would forbid that,
    so the contract asserted here is "a valid category, never a crash".
    """
    from services.license_normalize import _osori_canonical_aliases
    from tasks.scan_source import _LICENSE_CATEGORY_DEFAULTS, _classify_license_category

    aliases = _osori_canonical_aliases()
    assert len(aliases) > 100, "the vendored OSORI snapshot looks empty"

    emitted = set(aliases.values())
    outside = emitted - set(_LICENSE_CATEGORY_DEFAULTS)
    assert outside, "OSORI adds nothing beyond our own catalogue — why carry it?"

    valid = {"allowed", "conditional", "forbidden", "unknown"}
    categories = {spdx_id: _classify_license_category(spdx_id) for spdx_id in outside}
    assert set(categories.values()) <= valid

    # The shape of the answer, not just its validity: an unclassified licence
    # should read as unclassified, not be quietly assigned a posture.
    unknown_share = sum(1 for c in categories.values() if c == "unknown") / len(outside)
    assert unknown_share > 0.9

    # And an id the catalogue DOES know keeps its real category — the alias
    # list must not be able to downgrade a classified licence.
    for spdx_id in sorted(emitted & set(_LICENSE_CATEGORY_DEFAULTS))[:20]:
        assert _classify_license_category(spdx_id) == _LICENSE_CATEGORY_DEFAULTS[spdx_id]
