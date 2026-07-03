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
    # Creative Commons (ShareAlike wins over plain Attribution; pinned to 4.0).
    ("Creative Commons Attribution 4.0 International", "CC-BY-4.0"),
    ("Creative Commons Attribution-ShareAlike 4.0 International", "CC-BY-SA-4.0"),
]

# Inputs that MUST return None: unrecognized, compound, or an unsupported version.
_UNRECOGNIZED: list[str | None] = [
    None,
    "",
    "   ",
    "Acme Proprietary EULA 2.0",
    "MIT OR Apache-2.0",            # compound — do not collapse to one id
    "GPL-2.0-only AND Classpath",  # compound
    "Creative Commons Attribution 3.0",  # CC-BY-3.0 not carried → not remapped to 4.0
    "Artistic License 1.0",        # only Artistic-2.0 is carried
]


@pytest.mark.parametrize("raw,expected", _RECOGNIZED)
def test_recognized_aliases_resolve(raw: str, expected: str) -> None:
    assert normalize_license_name(raw) == expected


@pytest.mark.parametrize("raw", _UNRECOGNIZED)
def test_unrecognized_or_compound_returns_none(raw: str | None) -> None:
    assert normalize_license_name(raw) is None


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
