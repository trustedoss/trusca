"""Unit tests — persist-boundary sanitisation in ``persist_sbom_components``
and ``_extract_spdx_ids`` (F-1 follow-up + #443 F-2 root fix).

Two reviewer-flagged defects:

  1. Component purl / name / version (TEXT columns) and the ``raw_data`` JSONB
     were persisted unsanitised — an SBOM with an embedded NUL (reachable via
     external ingest) aborted the whole persist with a Postgres DataError and
     leaked the raw psycopg message into the user-visible
     ``scan.error_message``.
  2. ``_extract_spdx_ids`` stored control-character-bearing spdx ids, which
     the NOTICE plain-text header replays verbatim (forgeable lines).

These tests pin:

  * the NUL probe (``_sanitize_jsonb_payload``): clean payloads pass through
    as the SAME object (zero-cost path), poisoned payloads are deep-cleaned
    at every depth (keys included), literal ``\\u0000`` text false-positives
    the probe but round-trips unchanged;
  * ``_extract_spdx_ids`` strips NUL/ESC from ids, expressions and reference
    URLs, and skips entries that sanitise to nothing;
  * the ``persist_sbom_components`` loop feeds only CLEANED strings to the
    component / version / scan-component rows while keeping the RAW refs as
    graph-map keys (a poisoned bom-ref still resolves its depth).

Second-round review (same attack class — hostile SBOM → whole-persist abort +
raw driver message in ``scan.error_message``):

  * F-1: lone surrogates (``"x\\ud800"`` in JSON) bypass a NUL-only probe and
    die later in UTF-8 encoding — probe extended, ``sanitize_jsonb_text``
    drops the surrogate range;
  * F-2: sanitisation-converged duplicates (``a@1.0.0`` vs ``a@1.0.0\\x00``)
    collide on ``uq_scan_components_scan_version_path`` — deduped in-loop;
  * F-3: non-string name/version/bom-ref reach psycopg as raw objects —
    coerced (numbers) or defaulted (containers), bom-ref → NULL path;
  * F-4: the sanitisation warning is aggregated to ONE line per scan.

Fake-session unit tests (no DB, no subprocess) — same pattern as
``test_scan_source_scope_enrichment.py``. The DB-backed counterpart lives in
``tests/integration/scan/test_ingest_sbom_pipeline.py`` (hostile AIBOM case).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from models import ScanComponent
from tasks.scan_source import (
    _extract_spdx_ids,
    _sanitize_jsonb_payload,
    persist_sbom_components,
)

# ---------------------------------------------------------------------------
# _sanitize_jsonb_payload — NUL-gated deep clean
# ---------------------------------------------------------------------------


def test_clean_payload_passes_through_as_same_object() -> None:
    """The zero-cost path: a clean payload is returned untouched — the SAME
    dict object, proving no walk / no copy happened."""
    payload = {
        "purl": "pkg:npm/lodash@4.17.19",
        "name": "lodash",
        "nested": {"licenses": [{"license": {"id": "MIT"}}]},
        "count": 3,
        "flag": True,
        "nothing": None,
    }
    assert _sanitize_jsonb_payload(payload) is payload


def test_nul_anywhere_triggers_deep_clean_at_every_depth() -> None:
    """One embedded NUL fires the probe; the walk then cleans EVERY string —
    values, list elements and dict keys, at any nesting depth — and strips
    co-riding ESC sequences in the same pass."""
    payload = {
        "purl": "pkg:npm/evil@1.0.0\x00",
        "desc\x00ription": "safe",  # poisoned KEY
        "properties": [
            {"name": "cdx:npm:integrity", "value": "sha512-\x00abc"},
            "tail\x1b[31m",  # ESC rides along with the NUL elsewhere
        ],
        "nested": {"deep": {"deeper": "x\x00y"}},
        "count": 7,
    }
    out = _sanitize_jsonb_payload(payload)
    assert out is not payload
    blob = json.dumps(out)
    assert "\\u0000" not in blob
    assert "\\u001b" not in blob
    assert out["purl"] == "pkg:npm/evil@1.0.0"
    assert out["description"] == "safe"  # key cleaned
    assert out["properties"][0]["value"] == "sha512-abc"
    assert out["properties"][1] == "tail[31m"
    assert out["nested"]["deep"]["deeper"] == "xy"
    assert out["count"] == 7  # non-string scalars untouched


def test_literal_backslash_u0000_text_false_positives_but_roundtrips() -> None:
    """A string containing the literal six characters ``\\u0000`` (no real
    NUL) false-positives the probe — that only costs the walk, which never
    alters printable text, so the content round-trips equal."""
    payload = {"note": "the escape sequence \\u0000 is rejected by jsonb"}
    out = _sanitize_jsonb_payload(payload)
    assert out == payload


# ---------------------------------------------------------------------------
# _extract_spdx_ids — control chars cleaned at extraction (#443 F-2 root fix)
# ---------------------------------------------------------------------------


def test_spdx_id_and_url_are_sanitised() -> None:
    component = {
        "licenses": [
            {
                "license": {
                    "id": "Apache-2.0\x00\x1b[31m",
                    "url": "https://example.test/license\x00",
                }
            }
        ]
    }
    assert _extract_spdx_ids(component) == [
        ("Apache-2.0[31m", "https://example.test/license")
    ]


def test_spdx_expression_is_sanitised() -> None:
    component = {
        "licenses": [{"expression": "MIT\x00 OR \r\nApache-2.0"}]
    }
    # NUL / CR / LF dropped (not replaced) — CR/LF removal blocks NOTICE
    # header line forgery.
    assert _extract_spdx_ids(component) == [("MIT OR Apache-2.0", None)]


def test_spdx_id_that_sanitises_to_nothing_is_skipped() -> None:
    component = {
        "licenses": [
            {"license": {"id": "\x00\x1b\x07"}},
            {"license": {"id": "MIT"}},
        ]
    }
    assert _extract_spdx_ids(component) == [("MIT", None)]


def test_multi_license_or_join_survives_poisoned_members() -> None:
    component = {
        "licenses": [
            {"license": {"id": "GPL-2.0-or-later\x00"}},
            {"license": {"id": "MPL-1.1\x1b"}},
        ]
    }
    assert _extract_spdx_ids(component) == [("GPL-2.0-or-later OR MPL-1.1", None)]


def test_clean_spdx_extraction_is_unchanged() -> None:
    component = {
        "licenses": [{"license": {"id": "MIT", "url": "https://spdx.org/licenses/MIT"}}]
    }
    assert _extract_spdx_ids(component) == [("MIT", "https://spdx.org/licenses/MIT")]


# ---------------------------------------------------------------------------
# persist_sbom_components — cleaned values persisted, raw refs key the graph
# ---------------------------------------------------------------------------


class _FakeComponent:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeComponentVersion:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def _scan_components(session: _FakeSession) -> list[ScanComponent]:
    return [r for r in session.added if isinstance(r, ScanComponent)]


@pytest.fixture
def capture_helpers(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Stub the row helpers, capturing the kwargs each receives, so the test
    can assert only CLEANED strings reach the TEXT-column persist calls.

    The stubs memoize on their natural key (purl / purl_with_version), like
    the real get-or-create helpers, so two raw components whose identities
    CONVERGE after sanitisation resolve to the same fake row — the F-2 dedup
    tests depend on that."""
    captured: dict[str, list[Any]] = {"component": [], "version": [], "licenses": []}
    components_by_purl: dict[str, _FakeComponent] = {}
    versions_by_purl: dict[str, _FakeComponentVersion] = {}

    def _component(session: Any, *, purl: str, name: str, package_type: str) -> Any:
        captured["component"].append({"purl": purl, "name": name, "package_type": package_type})
        return components_by_purl.setdefault(purl, _FakeComponent())

    def _version(session: Any, *, component: Any, version: str, purl_with_version: str) -> Any:
        captured["version"].append({"version": version, "purl_with_version": purl_with_version})
        return versions_by_purl.setdefault(purl_with_version, _FakeComponentVersion())

    def _licenses(
        session: Any,
        *,
        scan_uuid: uuid.UUID,
        component_version_id: uuid.UUID,
        cdxgen_component: dict[str, Any],
        purl: str,
    ) -> None:
        captured["licenses"].append({"purl": purl})

    monkeypatch.setattr("tasks.scan_source._get_or_create_component", _component)
    monkeypatch.setattr("tasks.scan_source._get_or_create_component_version", _version)
    monkeypatch.setattr("tasks.scan_source._persist_component_licenses", _licenses)
    return captured


def test_hostile_component_persists_cleaned_strings_and_raw_data(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """NUL/ESC in purl / name / version / scope / bom-ref / raw_data inner
    fields: every persisted surface is clean, the scan-fatal DataError vector
    (NUL) is gone."""
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/evil@1.0.0\x00\x1b[31m",
                "bom-ref": "pkg:npm/evil@1.0.0\x00\x1b[31m",
                "name": "evil\x00",
                "version": "1.0.0\x1b",
                "scope": "required\x00",
                "description": "innocent looking\x00\x1b[2J",
            }
        ]
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)

    assert capture_helpers["component"] == [
        {"purl": "pkg:npm/evil", "name": "evil", "package_type": "npm"}
    ]
    assert capture_helpers["version"] == [
        {"version": "1.0.0", "purl_with_version": "pkg:npm/evil@1.0.0[31m"}
    ]
    assert capture_helpers["licenses"] == [{"purl": "pkg:npm/evil@1.0.0[31m"}]

    scs = _scan_components(session)
    assert len(scs) == 1
    sc = scs[0]
    assert sc.dependency_scope == "required"
    assert sc.dependency_path == "pkg:npm/evil@1.0.0[31m"
    blob = json.dumps(sc.raw_data)
    assert "\\u0000" not in blob
    assert "\\u001b" not in blob
    assert sc.raw_data["description"] == "innocent looking[2J"


def test_component_ref_that_sanitises_to_nothing_is_skipped(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """A purl/bom-ref that is ALL control characters leaves no usable
    identifier — the component is skipped, not persisted as an empty purl."""
    session = _FakeSession()
    sbom = {"components": [{"purl": "\x00\x1b\x07", "name": "x", "version": "1"}]}
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    assert capture_helpers["component"] == []
    assert _scan_components(session) == []


def test_clean_component_raw_data_is_persisted_as_same_object(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """Zero-cost pass-through, end to end: a clean in-limit component dict
    reaches ``ScanComponent.raw_data`` as the SAME object — neither the NUL
    probe nor the size guard copied it."""
    session = _FakeSession()
    raw = {
        "purl": "pkg:npm/lodash@4.17.19",
        "bom-ref": "pkg:npm/lodash@4.17.19",
        "name": "lodash",
        "version": "4.17.19",
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom={"components": [raw]})
    scs = _scan_components(session)
    assert len(scs) == 1
    assert scs[0].raw_data is raw
    assert capture_helpers["component"] == [
        {"purl": "pkg:npm/lodash", "name": "lodash", "package_type": "npm"}
    ]


def test_poisoned_bom_ref_still_resolves_its_graph_depth(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """The graph maps are keyed on the RAW (unsanitised) refs on purpose: a
    poisoned bom-ref must still match its equally poisoned entry in
    ``sbom["dependencies"]`` so depth/direct stamping survives sanitisation.
    (The maps resolve to UUIDs / in-memory rows — nothing raw is persisted.)
    Uses the real ``_persist_dependency_graph``."""
    session = _FakeSession()
    poisoned_ref = "pkg:npm/evil@1.0.0\x00"
    sbom = {
        "metadata": {"component": {"bom-ref": "root"}},
        "components": [
            {
                "purl": poisoned_ref,
                "bom-ref": poisoned_ref,
                "name": "evil",
                "version": "1.0.0",
            }
        ],
        "dependencies": [
            {"ref": "root", "dependsOn": [poisoned_ref]},
            {"ref": poisoned_ref, "dependsOn": []},
        ],
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    scs = _scan_components(session)
    assert len(scs) == 1
    assert scs[0].depth == 1
    assert scs[0].direct is True
    # And the persisted text surface is still clean.
    assert scs[0].dependency_path == "pkg:npm/evil@1.0.0"


# ---------------------------------------------------------------------------
# F-1 (round 2) — lone surrogates: sanitiser drop + probe coverage
# ---------------------------------------------------------------------------


def test_sanitize_jsonb_text_drops_lone_surrogates_keeps_real_unicode() -> None:
    """The shared cleaner drops the whole surrogate range (0xD800-0xDFFF):
    a lone surrogate decoded by ``json.loads`` is not UTF-8-encodable and
    sinks any downstream ``.encode("utf-8")``. Legitimate non-BMP text (which
    ``json.loads`` produces by COMBINING valid pairs) passes through."""
    from services.sbom_conformance import sanitize_jsonb_text

    high, low, last = chr(0xD800), chr(0xDC00), chr(0xDFFF)
    assert sanitize_jsonb_text(f"x{high}y{low}z{last}") == "xyz"
    # A real astral-plane character (U+1F600) survives — it is NOT a surrogate
    # in a decoded Python str.
    astral = chr(0x1F600)
    cleaned = sanitize_jsonb_text(f"name {astral}")
    assert cleaned == f"name {astral}"
    cleaned.encode("utf-8")  # must not raise


def test_lone_surrogate_triggers_deep_clean_and_output_is_utf8_encodable() -> None:
    """A lone surrogate carries no NUL, so a NUL-only probe would pass it
    through to the size guard's ``.encode("utf-8")`` → UnicodeEncodeError →
    whole-scan abort. The extended probe fires and the cleaned payload is
    UTF-8-encodable (the exact operation that crashed pre-fix)."""
    payload = {"name": "x" + chr(0xD800), "nested": ["ok", "y" + chr(0xDFFF)]}
    out = _sanitize_jsonb_payload(payload)
    assert out is not payload
    assert out["name"] == "x"
    assert out["nested"] == ["ok", "y"]
    # ensure_ascii=False + encode is what integrations._size_guard does.
    json.dumps(out, ensure_ascii=False).encode("utf-8")  # must not raise


def test_low_surrogate_alone_also_fires_the_probe() -> None:
    """The probe range covers d800-dfff completely — a lone LOW surrogate
    (dc00+) must not slip through a high-surrogates-only pattern."""
    payload = {"name": "x" + chr(0xDC00)}
    out = _sanitize_jsonb_payload(payload)
    assert out is not payload and out["name"] == "x"


# ---------------------------------------------------------------------------
# F-2 (round 2) — sanitisation-converged duplicates collapse to one row
# ---------------------------------------------------------------------------


def test_converged_duplicate_components_persist_once(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """``a@1.0.0`` and ``a@1.0.0\\x00`` sanitise to the SAME
    (component_version, dependency_path) — two ScanComponent rows would
    violate ``uq_scan_components_scan_version_path`` at commit and sink the
    whole persist (with SBOM content echoed in the IntegrityError DETAIL).
    The first row wins; the duplicate is skipped, licenses included."""
    session = _FakeSession()
    clean_ref = "pkg:npm/a@1.0.0"
    poisoned_ref = "pkg:npm/a@1.0.0\x00"
    sbom = {
        "components": [
            {"purl": clean_ref, "bom-ref": clean_ref, "name": "a", "version": "1.0.0"},
            {"purl": poisoned_ref, "bom-ref": poisoned_ref, "name": "a", "version": "1.0.0"},
        ]
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    scs = _scan_components(session)
    assert len(scs) == 1, "converged duplicates must collapse to ONE row"
    assert scs[0].dependency_path == clean_ref
    # License persist ran once — a second run would violate the
    # license_findings unique key the same way.
    assert capture_helpers["licenses"] == [{"purl": clean_ref}]


def test_duplicate_raw_refs_still_resolve_graph_depth_to_first_row(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """The skipped duplicate's RAW refs are still registered in the graph
    maps, pointing at the FIRST row — a dependencies entry spelled with the
    poisoned ref resolves depth onto the surviving ScanComponent."""
    session = _FakeSession()
    clean_ref = "pkg:npm/a@1.0.0"
    poisoned_ref = "pkg:npm/a@1.0.0\x00"
    sbom = {
        "metadata": {"component": {"bom-ref": "root"}},
        "components": [
            {"purl": clean_ref, "bom-ref": clean_ref, "name": "a", "version": "1.0.0"},
            {"purl": poisoned_ref, "bom-ref": poisoned_ref, "name": "a", "version": "1.0.0"},
        ],
        # The graph references ONLY the poisoned spelling.
        "dependencies": [
            {"ref": "root", "dependsOn": [poisoned_ref]},
            {"ref": poisoned_ref, "dependsOn": []},
        ],
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    scs = _scan_components(session)
    assert len(scs) == 1
    assert scs[0].depth == 1
    assert scs[0].direct is True


def test_literal_duplicate_entries_also_collapse(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """A raw SBOM listing the SAME component twice (no poisoning involved)
    hits the same unique constraint — the dedup covers it too."""
    session = _FakeSession()
    entry = {
        "purl": "pkg:npm/b@2.0.0",
        "bom-ref": "pkg:npm/b@2.0.0",
        "name": "b",
        "version": "2.0.0",
    }
    sbom = {"components": [entry, dict(entry)]}
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    assert len(_scan_components(session)) == 1


# ---------------------------------------------------------------------------
# F-3 (round 2) — non-string name / version / bom-ref
# ---------------------------------------------------------------------------


def test_numeric_name_and_version_are_stringified(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """Lenient producers emit ``"version": 1.0`` — JSON numbers are coerced to
    their string form instead of reaching psycopg as raw objects."""
    session = _FakeSession()
    sbom = {
        "components": [
            {"purl": "pkg:npm/x@1", "bom-ref": "pkg:npm/x@1", "name": 123, "version": 4.5}
        ]
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    assert capture_helpers["component"] == [
        {"purl": "pkg:npm/x", "name": "123", "package_type": "npm"}
    ]
    assert capture_helpers["version"][0]["version"] == "4.5"


def test_container_or_bool_name_and_version_fall_back_to_defaults(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """Objects / arrays / bools have no meaningful text form — fall back to
    the established defaults rather than persisting ``str(obj)`` repr garbage
    of unbounded length."""
    session = _FakeSession()
    sbom = {
        "components": [
            {
                "purl": "pkg:npm/y@1",
                "bom-ref": "pkg:npm/y@1",
                "name": {"nested": "object"},
                "version": [True],
            }
        ]
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    assert capture_helpers["component"][0]["name"] == "unknown"
    assert capture_helpers["version"][0]["version"] == "0.0.0"


def test_non_string_bom_ref_persists_null_dependency_path(
    capture_helpers: dict[str, list[Any]],
) -> None:
    """A non-string bom-ref carries no path information: dependency_path is
    NULL, never a raw non-string object headed for a TEXT column."""
    session = _FakeSession()
    sbom = {
        "components": [
            {"purl": "pkg:npm/z@1", "bom-ref": 42, "name": "z", "version": "1"}
        ]
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    scs = _scan_components(session)
    assert len(scs) == 1
    assert scs[0].dependency_path is None


# ---------------------------------------------------------------------------
# F-4 (round 2) — one aggregated warning per scan
# ---------------------------------------------------------------------------


class _RecordingLog:
    """Minimal structlog stand-in recording warning events."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:  # noqa: ARG002 — signature parity
        return None

    def warning(self, event: str, **kw: Any) -> None:
        self.warnings.append((event, kw))


def test_sanitisation_warning_is_aggregated_once_per_scan(
    capture_helpers: dict[str, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two poisoned components + one converged duplicate → exactly ONE
    ``jsonb_nul_sanitized`` line carrying the aggregate counters. Per-component
    lines would let a hostile 10k-component SBOM write 10k warnings."""
    fake_log = _RecordingLog()
    monkeypatch.setattr("tasks.scan_source.log", fake_log)
    session = _FakeSession()
    sbom = {
        "components": [
            {"purl": "pkg:npm/p1@1", "bom-ref": "pkg:npm/p1@1", "name": "p1",
             "version": "1", "description": "x\x00"},
            {"purl": "pkg:npm/p2@1", "bom-ref": "pkg:npm/p2@1", "name": "p2",
             "version": "1", "description": "y\x00"},
            {"purl": "pkg:npm/c@1", "bom-ref": "pkg:npm/c@1", "name": "c", "version": "1"},
            {"purl": "pkg:npm/c@1", "bom-ref": "pkg:npm/c@1", "name": "c", "version": "1"},
        ]
    }
    scan_uuid = uuid.uuid4()
    persist_sbom_components(session, scan_uuid=scan_uuid, sbom=sbom)

    sanitize_events = [w for w in fake_log.warnings if w[0] == "jsonb_nul_sanitized"]
    assert len(sanitize_events) == 1, "one aggregated line per scan, not per component"
    _, fields = sanitize_events[0]
    assert fields["sanitized_components"] == 2
    assert fields["deduplicated_components"] == 1
    assert fields["scan_id"] == str(scan_uuid)


def test_no_warning_when_nothing_was_sanitised(
    capture_helpers: dict[str, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_log = _RecordingLog()
    monkeypatch.setattr("tasks.scan_source.log", fake_log)
    session = _FakeSession()
    sbom = {
        "components": [
            {"purl": "pkg:npm/ok@1", "bom-ref": "pkg:npm/ok@1", "name": "ok", "version": "1"}
        ]
    }
    persist_sbom_components(session, scan_uuid=uuid.uuid4(), sbom=sbom)
    assert [w for w in fake_log.warnings if w[0] == "jsonb_nul_sanitized"] == []
