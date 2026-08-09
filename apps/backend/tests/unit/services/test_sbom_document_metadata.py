# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What an SBOM says about itself, on the route that actually ships it.

The export route got these statements first. The scan route is the one whose
document is signed with cosign and handed to a consumer, so the statements
being absent there was the gap that mattered — and nothing in either module's
own tests could see it, because neither imports the other.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services import sbom_document_metadata as meta
from services.sbom_conformance import evaluate


def _cdxgen_like() -> dict[str, Any]:
    """What cdxgen writes, in the shape the scan pipeline hands on.

    Its own tool entry carries no version, which is the case the guidance asks
    to be answered with "unknown" rather than left blank.
    """
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": "2026-08-09T00:00:00Z",
            "tools": {"components": [{"name": "cdxgen", "type": "application"}]},
            "component": {"type": "application", "name": "demo", "version": "1.0"},
        },
        "components": [],
        "dependencies": [],
    }


def test_the_scan_document_states_context_tool_and_unknowns() -> None:
    doc = meta.stamp_document_metadata(_cdxgen_like(), scan_kind="source")
    metadata = doc["metadata"]

    assert metadata["lifecycles"] == [{"phase": "pre-build"}]
    names = [t["name"] for t in metadata["tools"]["components"]]
    assert "cdxgen" in names and "TRUSCA" in names
    assert any(
        p["name"] == meta.UNDECLARED_FIELDS_PROPERTY and p["value"]
        for p in metadata["properties"]
    )


def test_a_generator_tool_with_no_version_says_unknown() -> None:
    """The element asks which version; a blank field does not answer it."""
    doc = meta.stamp_document_metadata(_cdxgen_like(), scan_kind="source")
    versions = {
        t["name"]: t["version"] for t in doc["metadata"]["tools"]["components"]
    }
    assert versions["cdxgen"] == meta.UNKNOWN_VERSION
    assert versions["TRUSCA"] == meta.tool_version()


def test_the_legacy_array_tools_shape_is_preserved() -> None:
    """Rewriting a generator's document into the other shape is a change we
    have no reason to make."""
    doc = _cdxgen_like()
    doc["metadata"]["tools"] = [{"name": "cdxgen", "version": "12.3.3"}]
    stamped = meta.stamp_document_metadata(doc, scan_kind="source")
    assert isinstance(stamped["metadata"]["tools"], list)
    assert {t["name"] for t in stamped["metadata"]["tools"]} == {"cdxgen", "TRUSCA"}


def test_stamping_twice_changes_nothing() -> None:
    """A re-run of a scan must produce the same bytes."""
    once = meta.stamp_document_metadata(_cdxgen_like(), scan_kind="source")
    twice = meta.stamp_document_metadata(json.loads(json.dumps(once)), scan_kind="source")
    assert twice == once


@pytest.mark.parametrize(
    ("kind", "phase"),
    [("source", "pre-build"), ("container", "post-build"), ("sbom", None)],
)
def test_the_phase_follows_the_scan_kind(kind: str, phase: str | None) -> None:
    doc = meta.stamp_document_metadata(_cdxgen_like(), scan_kind=kind)
    if phase is None:
        # An ingested supplier document is not ours to claim a phase for.
        assert "lifecycles" not in doc["metadata"]
    else:
        assert doc["metadata"]["lifecycles"] == [{"phase": phase}]


def test_amending_a_document_does_not_claim_authorship(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SBOM_AUTHOR", "Example Co.")
    doc = meta.stamp_document_metadata(
        _cdxgen_like(), scan_kind="source", claim_authorship=False
    )
    assert "authors" not in doc["metadata"]
    assert "lifecycles" not in doc["metadata"]
    # The tool and the unknowns statement are still true of an amended document.
    assert any(
        t["name"] == "TRUSCA" for t in doc["metadata"]["tools"]["components"]
    )


def test_a_hostile_metadata_shape_does_not_raise() -> None:
    doc: dict[str, Any] = {
        "metadata": "not an object",
        "components": [],
    }
    stamped = meta.stamp_document_metadata(doc, scan_kind="source")
    assert isinstance(stamped["metadata"], dict)


def test_the_stamped_document_satisfies_the_elements_it_answers() -> None:
    """The round trip that makes this answerable: stamp, then measure.

    Without it the module could write fields the baseline does not read, and
    both sides would pass their own tests.
    """
    doc = meta.stamp_document_metadata(_cdxgen_like(), scan_kind="source")
    rows = {c.id: c.status for c in evaluate(json.dumps(doc).encode()).checks}
    assert rows["cisa-sbom-generation-context"] == "pass"
    assert rows["cisa-sbom-tool-name"] == "pass"
    assert rows["cisa-sbom-tool-version"] == "pass"
    assert rows["cisa-explicit-unknowns"] == "pass"
