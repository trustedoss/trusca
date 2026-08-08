# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Vendored data files must not carry the upstream product name or its paths.

TRUSCA vendors several data files from the sibling project BomLens. Some of
their strings are served straight to TRUSCA users — the regulatory crosswalk's
disclaimer is rendered on the conformance panel — and one of them shipped
reading "BomLens does not certify...", so a TRUSCA user was shown a legal
notice in another product's name.

The guard is deliberately shaped the way it is:

* It walks EVERY string in EVERY vendored JSON, rather than checking a list of
  fields known to be user-facing today. A field list would have to be extended
  by whoever adds the next registry, which is exactly the person who will
  forget. Walking everything means a new file and a new field are covered on
  arrival.
* What it lets through is an explicit allowlist below, so each exemption is
  visible and has to be argued for, instead of being implied by the absence of
  a check.

Attribution is NOT what this guard removes. Crediting upstream is an
Apache-2.0 §4(d) obligation and lives in THIRD_PARTY_NOTICES.md and in module
docstrings — neither of which is served to users. What must not appear is
upstream's name in strings TRUSCA presents as its own, or upstream's internal
file paths in documentation of TRUSCA's own layout.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_SERVICES = Path(__file__).resolve().parents[3] / "services"

# Upstream property names TRUSCA matches on. These are upstream's wire format,
# not branding: a SBOM produced by BomLens carries `bomlens:generationContext`,
# and services/g7_conformance.py matches that name EXACTLY. Renaming it here
# would silently stop recognising those documents.
#
# Known limitation: this also exempts prose that happens to mention a property
# ("...writes bomlens:eol=unknown, never a guess"). That prose is legitimate —
# it documents the wire format — but the pattern cannot tell the two apart, so
# a sentence of the form "bomlens:<word>" would slip through. The product-name
# spelling that caused the incident this guard exists for ("BomLens does not
# certify...") has no colon and is caught.
_UPSTREAM_PROPERTY = re.compile(r"bomlens:[A-Za-z][A-Za-z0-9_]*")

# Files whose contract is to be byte-identical to upstream. We cannot rewrite
# their strings without breaking that contract, and their contents never reach
# a user — services/eol/eol_purl_map.json is matching data, and its `_comment`
# is read by nothing (the loader reads only the rules). Attribution for these
# lives in THIRD_PARTY_NOTICES.md under "vendored verbatim".
_VERBATIM_CONTRACT = frozenset({"eol/eol_purl_map.json"})

# Upstream's own file names. Naming one inside a TRUSCA data file describes
# TRUSCA's layout wrongly; the equivalent statement belongs in the attribution
# file, which is not walked here.
_UPSTREAM_PATHS = re.compile(
    r"docker/lib/|docker/build-|validate-sbom\.sh|enrich-[a-z-]+\.sh"
    r"|normalize-sbom\.sh|spdx-normalize\.jq|license-flags\.jq"
)

_PRODUCT_NAME = re.compile(r"bomlens|sbom-tools", re.IGNORECASE)


def _vendored_files() -> list[Path]:
    return sorted(_SERVICES.rglob("*.json"))


def _strings(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Yield (json path, string) for every string in the document, keys included."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key
            yield from _strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def test_guard_covers_every_vendored_json() -> None:
    """A new vendored file is in scope on arrival, not when someone remembers."""
    found = {p.relative_to(_SERVICES).as_posix() for p in _vendored_files()}
    # The four that existed when this guard was written. Extra files are fine —
    # they are already being walked. A MISSING one means the glob broke.
    assert {
        "g7_registry.json",
        "regulation_crosswalk.json",
        "eol/eol_purl_map.json",
        "malicious/malicious_snapshot.json",
    } <= found


@pytest.mark.parametrize("path", _vendored_files(), ids=lambda p: p.name)
def test_no_upstream_product_name(path: Path) -> None:
    """No vendored string names the upstream product, except a property name."""
    document = json.loads(path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for json_path, text in _strings(document):
        residue = _UPSTREAM_PROPERTY.sub("", text)
        if _PRODUCT_NAME.search(residue):
            offenders.append(f"{json_path}: {text[:120]}")

    assert not offenders, (
        f"{path.name} names the upstream product in strings TRUSCA serves as "
        f"its own. Rewrite them; attribution belongs in THIRD_PARTY_NOTICES.md, "
        f"not in the payload:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("path", _vendored_files(), ids=lambda p: p.name)
def test_no_upstream_file_paths(path: Path) -> None:
    """No vendored string points at an upstream path as if it were ours."""
    if path.relative_to(_SERVICES).as_posix() in _VERBATIM_CONTRACT:
        pytest.skip("byte-identical to upstream by contract; see _VERBATIM_CONTRACT")

    document = json.loads(path.read_text(encoding="utf-8"))
    offenders = [
        f"{json_path}: {text[:120]}"
        for json_path, text in _strings(document)
        if _UPSTREAM_PATHS.search(text)
    ]

    assert not offenders, (
        f"{path.name} documents TRUSCA's layout using upstream's paths:\n"
        + "\n".join(offenders)
    )


def test_verbatim_exemptions_are_actually_byte_pinned() -> None:
    """An exemption is only defensible while the byte contract really holds.

    If the byte-identical test for a file is ever dropped, the file becomes
    editable and its exemption here becomes an unguarded hole. This ties the
    two together so removing one surfaces the other.
    """
    contracts = (
        Path(__file__).with_name("test_eol_catalog_contracts.py")
    ).read_text(encoding="utf-8")
    assert "byte_identical" in contracts
    assert "eol_purl_map.json" in contracts


_REPO_ROOT = Path(__file__).resolve().parents[5]
_GUIDE_EN = _REPO_ROOT / "docs-site/docs/ci-integration/sbom-upload.md"
_GUIDE_KO = (
    _REPO_ROOT
    / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
    / "ci-integration/sbom-upload.md"
)


@pytest.mark.parametrize(
    ("field", "guide"),
    [("disclaimer", _GUIDE_EN), ("disclaimer_ko", _GUIDE_KO)],
)
def test_guide_quotes_the_served_disclaimer_verbatim(field: str, guide: Path) -> None:
    """The guide promises the payload carries this text — hold it to that.

    This is the check that would have caught the incident. The guides said
    "the payload carries this disclaimer verbatim" while the payload named a
    different product, and nothing compared the two. Documentation is the
    oracle here (CLAUDE.md hardening rule 4): if the served string and the
    quoted string drift apart, one of them is now lying to a reader.
    """
    assert guide.is_file(), f"{guide} is missing — the quote it holds is the oracle"

    served = json.loads(
        (_SERVICES / "regulation_crosswalk.json").read_text(encoding="utf-8")
    )[field]

    assert served.strip()
    assert served in guide.read_text(encoding="utf-8"), (
        f"{guide.name} no longer quotes the {field} the API serves. Either the "
        f"payload changed and the guide was not updated, or the guide was "
        f"reworded. Both are drift; make them identical again."
    )


def test_upstream_property_names_are_preserved() -> None:
    """The exemption is load-bearing: renaming the marker breaks ingest.

    services/g7_conformance.py matches this property name exactly. If the guard
    above is ever widened to rewrite it, this fails and says why.
    """
    registry = (_SERVICES / "g7_registry.json").read_text(encoding="utf-8")
    assert "bomlens:generationContext" in registry

    predicate = (_SERVICES / "g7_conformance.py").read_text(encoding="utf-8")
    assert "bomlens:generationContext" in predicate
