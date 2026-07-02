"""
Unit tests for the synchronous SBOM-ingest validator + media-type guard.

These exercise the PURE front-half of the SBOM-ingest feature
(``services.sbom_ingest_service``) — no DB, no Redis, no Celery. The functions
under test are:

  - ``validate_cyclonedx_document(raw: bytes) -> dict`` — parse + structural
    whitelist (top-level keys + ``len(components)`` only; NEVER deep traversal).
  - ``_validate_content_type(...)`` — Content-Type / filename allow-list (415).
  - ``_read_bounded(...)`` — chunked, capped inbound read (413).

Adversarial-input contract (CLAUDE.md memory: untrusted-input parsers must be
parametrized over hostile inputs). The validator must:
  * accept well-formed CycloneDX 1.2–1.6 documents with or without components,
  * reject non-JSON / non-object / wrong bomFormat / unsupported specVersion /
    non-list components / over-cap component count with ``SbomIngestInvalid`` (422),
  * NOT recurse into component elements — it counts only ``len(components)`` at
    the top level, deferring the deep parse to the Celery worker — yet still
    bound structural nesting via a cheap O(n) byte-depth pre-check, so a
    pathologically deep document is a clean 422, never a RecursionError → 500.

This file is import-pure: it does not import ``main`` / ``core.ratelimit`` /
``core.db`` (so the autouse redis-backed rate-limiter fixture in conftest is
never exercised), which lets the validator cases run standalone with plain
``python`` as well as under pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.sbom_ingest_service import (
    _META_TEXT_MAX_LEN,
    SbomIngestInvalid,
    SbomIngestTooLarge,
    SbomIngestUnsupportedType,
    _clean_meta_text,
    _read_bounded,
    _validate_content_type,
    validate_cyclonedx_document,
    validate_uploaded_sbom,
)

_SBOM_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sbom"


def _doc(spec_version: str = "1.5", **extra: object) -> bytes:
    base: dict[str, object] = {"bomFormat": "CycloneDX", "specVersion": spec_version}
    base.update(extra)
    return json.dumps(base).encode("utf-8")


# ---------------------------------------------------------------------------
# Happy path — supported versions, components present / absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec_version", ["1.2", "1.3", "1.4", "1.5", "1.6"])
def test_accepts_supported_spec_versions_without_components(spec_version: str) -> None:
    parsed = validate_cyclonedx_document(_doc(spec_version))
    assert parsed["bomFormat"] == "CycloneDX"
    assert parsed["specVersion"] == spec_version
    # No 'components' key is valid (a metadata-only BOM).
    assert "components" not in parsed


@pytest.mark.parametrize("spec_version", ["1.4", "1.5", "1.6"])
def test_accepts_documents_with_components(spec_version: str) -> None:
    components = [
        {
            "type": "library",
            "name": "lodash",
            "version": "4.17.19",
            "purl": "pkg:npm/lodash@4.17.19",
        },
        {
            "type": "library",
            "name": "jinja2",
            "version": "2.11.2",
            "purl": "pkg:pypi/jinja2@2.11.2",
        },
    ]
    parsed = validate_cyclonedx_document(_doc(spec_version, components=components))
    assert isinstance(parsed["components"], list)
    assert len(parsed["components"]) == 2


def test_accepts_empty_components_list() -> None:
    parsed = validate_cyclonedx_document(_doc(components=[]))
    assert parsed["components"] == []


# ---------------------------------------------------------------------------
# Rejection matrix — every structural failure → SbomIngestInvalid (422)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"this is not json", "non-JSON garbage"),
        (b"", "empty body"),
        (b'{"bomFormat": "CycloneDX", "specVersion": "1.5"', "truncated JSON object"),
        (b"[]", "top-level JSON array"),
        (b'["CycloneDX"]', "top-level array with a string"),
        (b'"CycloneDX"', "top-level JSON scalar (string)"),
        (b"42", "top-level JSON scalar (number)"),
        (b"true", "top-level JSON scalar (bool)"),
        (b"null", "top-level JSON null"),
    ],
)
def test_rejects_non_object_or_non_json(raw: bytes, why: str) -> None:
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(raw)


@pytest.mark.parametrize(
    "bom_format",
    ["SPDX", "cyclonedx", "CYCLONEDX", "", "CycloneDX ", None, 1, ["CycloneDX"]],
)
def test_rejects_wrong_bom_format(bom_format: object) -> None:
    raw = json.dumps({"bomFormat": bom_format, "specVersion": "1.5"}).encode("utf-8")
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(raw)


def test_rejects_missing_bom_format() -> None:
    raw = json.dumps({"specVersion": "1.5"}).encode("utf-8")
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(raw)


@pytest.mark.parametrize(
    "spec_version",
    # 1.7 moved to the accepted set (G7 ML-BOM ingest); 1.8 takes its place
    # as the next-unreleased rejection probe.
    ["1.1", "2.0", "1.0", "1.8", "v1.5", "1.5.0", "", "latest"],
)
def test_rejects_unsupported_spec_version_strings(spec_version: str) -> None:
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(_doc(spec_version))


@pytest.mark.parametrize("spec_version", ["1.6", "1.7"])
def test_accepts_supported_upper_spec_versions(spec_version: str) -> None:
    # 1.7 is the ML-BOM (G7) ingest path — must pass the same gate as 1.6.
    validate_cyclonedx_document(_doc(spec_version))


@pytest.mark.parametrize("spec_version", [1.5, 15, None, True, ["1.5"], {"v": "1.5"}])
def test_rejects_non_string_spec_version(spec_version: object) -> None:
    raw = json.dumps(
        {"bomFormat": "CycloneDX", "specVersion": spec_version}
    ).encode("utf-8")
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(raw)


def test_rejects_missing_spec_version() -> None:
    raw = json.dumps({"bomFormat": "CycloneDX"}).encode("utf-8")
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(raw)


@pytest.mark.parametrize(
    "components",
    [
        {"not": "a list"},
        "pkg:npm/lodash@4.17.19",
        42,
        True,
    ],
)
def test_rejects_components_that_are_not_a_list(components: object) -> None:
    raw = json.dumps(
        {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components}
    ).encode("utf-8")
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(raw)


def test_rejects_too_many_components(monkeypatch: pytest.MonkeyPatch) -> None:
    """len(components) > cap → SbomIngestInvalid. The cap is read at call time
    (CLAUDE.md rule #11), so a tiny env override drives the rejection cheaply
    without building 50k elements."""
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "3")
    components = [{"type": "library", "name": f"c{i}"} for i in range(4)]
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(_doc(components=components))


def test_accepts_components_exactly_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boundary: len == cap passes; only len > cap is rejected."""
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "3")
    components = [{"type": "library", "name": f"c{i}"} for i in range(3)]
    parsed = validate_cyclonedx_document(_doc(components=components))
    assert len(parsed["components"]) == 3


# ---------------------------------------------------------------------------
# Deep nesting — within caps must PASS (no recursion / CPU blow-up)
# ---------------------------------------------------------------------------


def _nested_components_doc(depth: int) -> bytes:
    """Build {components:[{components:[{components:[...]}]}]} ``depth`` deep as a
    raw JSON STRING by concatenation.

    We deliberately do NOT round-trip a ``depth``-deep Python object through
    ``json.dumps``: CPython's json *encoder* is itself recursive and would
    overflow while building the fixture (a different limit from the one under
    test). The assembled string is still valid JSON.
    """
    opening = '{"type":"library","name":"n","components":['
    leaf = '{"type":"library","name":"leaf"}'
    closing = "]}"
    return (
        '{"bomFormat":"CycloneDX","specVersion":"1.5","components":['
        + opening * depth
        + leaf
        + closing * depth
        + "]}"
    ).encode("utf-8")


def test_moderately_nested_document_within_caps_passes() -> None:
    """A legitimately nested-component chain (well under the depth cap) with only
    ONE top-level component validates successfully.

    Two properties at once: (a) the validator inspects ONLY the top-level keys
    and ``len(components)`` — it never recurses into the element graph to count
    or sanitise nested components (it sees ``len == 1`` regardless of the chain
    below); (b) a normal SBOM that nests a handful of assembly levels stays well
    under ``_MAX_NESTING_DEPTH`` and is not falsely rejected. ``_nested_components_doc(d)``
    produces a structural byte-depth of roughly ``2 + 2*d``, so depth 20 (~42)
    sits comfortably under the 64 cap. The authoritative deep parse is deferred
    to the Celery worker; the abuse boundary is pinned in
    ``test_extremely_deep_document_is_rejected_422``.
    """
    parsed = validate_cyclonedx_document(_nested_components_doc(20))
    # Exactly one TOP-LEVEL component — the validator counted len() == 1 and did
    # not descend into the deep chain.
    assert len(parsed["components"]) == 1


def test_extremely_deep_document_is_rejected_422() -> None:
    """A pathologically deep document is rejected as a clean 422 by the O(n)
    byte-depth pre-check, BEFORE the recursive ``json.loads`` decoder runs.

    Regression guard for the RecursionError → unhandled 500 bug: ``json.loads``
    recurses one frame per nesting level, so a ~10k-deep CycloneDX document would
    overflow CPython's recursion limit and raise ``RecursionError`` (a
    ``RuntimeError`` subclass the old ``except ValueError`` did not catch),
    escaping as a 500. ``validate_cyclonedx_document`` now rejects it as
    ``SbomIngestInvalid`` via ``_max_nesting_depth`` (and defensively catches
    ``RecursionError`` as belt-and-braces).
    """
    with pytest.raises(SbomIngestInvalid):
        validate_cyclonedx_document(_nested_components_doc(10_000))


# ---------------------------------------------------------------------------
# Pass-through inputs — validator does NOT reject these (deferred to persist)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purl", "label"),
    [
        ("pkg:npm/evil@1.0.0\x00injected", "null byte in purl"),
        ("pkg:npm/evil@1.0.0\r\nX-Injected: 1", "CRLF in purl"),
        ("pkg:npm/" + "a" * 100_000 + "@1.0.0", "oversized purl"),
        ("javascript:alert(1)", "scheme-injection purl"),
    ],
)
def test_hostile_component_fields_pass_validation(purl: str, label: str) -> None:
    """Null bytes / CRLF / oversized / scheme-injection INSIDE a component are
    NOT rejected by the synchronous validator — it never traverses element
    fields. The Celery persist stage owns sanitising these (covered by the
    vulnerability_matching scrubber tests). Here we pin that the structural
    validator passes such a document through, so the contract boundary is
    explicit."""
    components = [{"type": "library", "name": "evil", "purl": purl}]
    parsed = validate_cyclonedx_document(_doc(components=components))
    assert len(parsed["components"]) == 1


# ---------------------------------------------------------------------------
# Content-Type / filename allow-list (415)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "filename"),
    [
        ("application/json", "sbom.json"),
        ("application/vnd.cyclonedx+json", "sbom.cdx.json"),
        ("application/octet-stream", "bom.json"),
        ("", "sbom.cdx.json"),  # CLI omits the part content-type → filename saves it
        ("application/json; charset=utf-8", "weird.bin"),  # param stripped, ct ok
        ("text/plain", "good.json"),  # bad ct but .json filename rescues it
        ("APPLICATION/JSON", "x"),  # case-insensitive content-type
        (None, "bom.cdx.json"),  # no ct header at all but valid filename
    ],
)
def test_content_type_guard_accepts(content_type: str | None, filename: str) -> None:
    # The guard returns None on success and raises SbomIngestUnsupportedType
    # otherwise; "accepts" == "does not raise".
    _validate_content_type(content_type=content_type, filename=filename)


@pytest.mark.parametrize(
    ("content_type", "filename"),
    [
        ("text/html", "index.html"),
        ("application/zip", "source.zip"),
        ("application/xml", "sbom.xml"),  # CycloneDX XML not accepted by THIS endpoint
        ("text/plain", "notes.txt"),
        ("image/png", "logo.png"),
    ],
)
def test_content_type_guard_rejects_on_both_axes(
    content_type: str, filename: str
) -> None:
    """415 only when BOTH the media type AND the filename are wrong — either one
    in the allow-list is enough to pass."""
    with pytest.raises(SbomIngestUnsupportedType):
        _validate_content_type(content_type=content_type, filename=filename)


# ---------------------------------------------------------------------------
# Bounded read (413) — the chunked cap guard
# ---------------------------------------------------------------------------


class _FakeUpload:
    """Minimal UploadFile stand-in: serves ``data`` in ``chunk`` byte slices."""

    def __init__(self, data: bytes, *, chunk: int) -> None:
        self._data = data
        self._chunk = chunk
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        n = self._chunk if size < 0 else min(size, self._chunk)
        out = self._data[self._pos : self._pos + n]
        self._pos += len(out)
        return out


@pytest.mark.asyncio
async def test_read_bounded_returns_full_body_under_cap() -> None:
    payload = b"A" * 4096
    upload = _FakeUpload(payload, chunk=512)
    out = await _read_bounded(upload, max_bytes=8192)  # type: ignore[arg-type]
    assert out == payload


@pytest.mark.asyncio
async def test_read_bounded_raises_too_large_over_cap() -> None:
    payload = b"B" * 9000
    upload = _FakeUpload(payload, chunk=512)
    with pytest.raises(SbomIngestTooLarge):
        await _read_bounded(upload, max_bytes=4096)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_bounded_aborts_before_buffering_whole_body() -> None:
    """The cap fires on the running total, not after materialising everything —
    a 64 MiB stream against a 1 MiB cap must abort having read only a bounded
    prefix (a couple of chunks past the cap), never the whole body."""
    big = b"C" * (64 * 1024 * 1024)
    upload = _FakeUpload(big, chunk=1024 * 1024)
    with pytest.raises(SbomIngestTooLarge):
        await _read_bounded(upload, max_bytes=1024 * 1024)  # type: ignore[arg-type]
    # The loop stopped right after the running total crossed the cap.
    assert upload._pos <= 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Metadata text cleaning — release / original_filename defense-in-depth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("v1.2.3", "v1.2.3"),
        ("  v1.2.3  ", "v1.2.3"),
        # Control bytes (NUL, CR, LF, tab) are stripped — they would corrupt
        # audit/log lines and have no place in a release label or filename.
        ("v1\x00.2\n.3\r", "v1.2.3"),
        ("a\tb", "ab"),
        # A value that is only control bytes collapses to None.
        ("\x00\n\r", None),
    ],
)
def test_clean_meta_text(raw: str | None, expected: str | None) -> None:
    assert _clean_meta_text(raw) == expected


def test_clean_meta_text_caps_length() -> None:
    """An oversized release/filename is truncated to the cap (no unbounded JSONB)."""
    cleaned = _clean_meta_text("x" * (_META_TEXT_MAX_LEN + 500))
    assert cleaned is not None
    assert len(cleaned) == _META_TEXT_MAX_LEN


# ---------------------------------------------------------------------------
# validate_uploaded_sbom — format dispatch (CycloneDX-JSON + SPDX JSON/TV)
#
# Pure (no DB / app). Real syft SPDX fixtures cover density; crafted inputs
# cover the adversarial / unsupported edges. The byte-depth pre-check must fire
# BEFORE any json.loads (incl. detect_format's) so a deep document is a clean
# 422, never a RecursionError → 500.
# ---------------------------------------------------------------------------


def test_validate_accepts_cyclonedx_returns_format() -> None:
    assert validate_uploaded_sbom(_doc()) == "cyclonedx"


def test_validate_accepts_real_spdx_json() -> None:
    raw = (_SBOM_FIXTURES / "real_spdx.json").read_bytes()
    assert validate_uploaded_sbom(raw) == "spdx-json"


def test_validate_accepts_real_spdx_tag_value() -> None:
    raw = (_SBOM_FIXTURES / "real_spdx.tag").read_bytes()
    assert validate_uploaded_sbom(raw) == "spdx-tv"


def test_validate_accepts_minimal_spdx_json() -> None:
    raw = json.dumps(
        {
            "spdxVersion": "SPDX-2.3",
            "name": "doc",
            "creationInfo": {"created": "2026-01-01T00:00:00Z", "creators": ["Tool: x"]},
            "packages": [{"SPDXID": "SPDXRef-a", "name": "a", "versionInfo": "1"}],
        }
    ).encode()
    assert validate_uploaded_sbom(raw) == "spdx-json"


def test_validate_accepts_minimal_spdx_tag_value() -> None:
    raw = b"SPDXVersion: SPDX-2.3\nPackageName: a\nPackageVersion: 1\n"
    assert validate_uploaded_sbom(raw) == "spdx-tv"


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"this is not an sbom", "plain-text"),
        (b"<rdf:RDF>spdx</rdf:RDF>", "spdx-rdf-unsupported"),
        (b'<?xml version="1.0"?><bom/>', "cyclonedx-xml-unsupported"),
        (b"[]", "json-array"),
        (b'{"foo": 1}', "json-without-sbom-markers"),
        # A pseudo-SPDX with bomFormat:"SPDX" (not real SPDX — real uses
        # spdxVersion) detects as unknown → 422 (unchanged from #406).
        (json.dumps({"bomFormat": "SPDX", "specVersion": "1.5"}).encode(), "fake-spdx"),
    ],
)
def test_validate_rejects_unsupported_formats(raw: bytes, why: str) -> None:
    with pytest.raises(SbomIngestInvalid):
        validate_uploaded_sbom(raw)


def test_validate_spdx_json_packages_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "2")
    raw = json.dumps(
        {
            "spdxVersion": "SPDX-2.3",
            "packages": [{"SPDXID": f"SPDXRef-{i}", "name": str(i)} for i in range(3)],
        }
    ).encode()
    with pytest.raises(SbomIngestInvalid):
        validate_uploaded_sbom(raw)


def test_validate_spdx_tag_value_packages_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tag-Value enforces the same component ceiling as the JSON paths (security
    # review: a byte-capped .tag could otherwise smuggle millions of packages).
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "2")
    raw = b"SPDXVersion: SPDX-2.3\n" + b"PackageName: a\n" * 3
    with pytest.raises(SbomIngestInvalid):
        validate_uploaded_sbom(raw)


def test_validate_spdx_tag_value_at_cap_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBOM_INGEST_MAX_COMPONENTS", "3")
    raw = b"SPDXVersion: SPDX-2.3\n" + b"PackageName: a\n" * 3
    assert validate_uploaded_sbom(raw) == "spdx-tv"


def test_validate_depth_guard_fires_before_json_parse() -> None:
    # A pathologically deep JSON document must be rejected by the O(n) byte
    # depth pre-check as a clean 422 — never reaching json.loads (which would
    # raise RecursionError → unhandled 500). 4000 levels is far past the cap.
    deep = b'{"a":' * 4000 + b"1" + b"}" * 4000
    with pytest.raises(SbomIngestInvalid):
        validate_uploaded_sbom(deep)


def test_validate_content_type_accepts_spdx_extensions() -> None:
    # Filename allow-list now covers SPDX extensions (advisory gate).
    for name in ("bom.spdx", "bom.spdx.json", "bom.tag", "sbom.spdx.tag"):
        _validate_content_type(content_type="application/octet-stream", filename=name)
    _validate_content_type(content_type="application/spdx+json", filename="x.unknown")
    _validate_content_type(content_type="text/spdx", filename="x.unknown")
