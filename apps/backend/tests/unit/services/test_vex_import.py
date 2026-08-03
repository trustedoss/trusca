"""
Pure-unit tests for ``services/vex_import.py`` — v2.1 Track A (A2).

No DB: these exercise the parser, the reverse status maps, the legal-transition
pathfinder, and the adversarial-input handling. The DB-backed end-to-end
matching / transition / round-trip / RBAC tests live in
``tests/integration/test_vex_import_api.py`` (they need the real Postgres).

Coverage focus:
- reverse maps are correct + total over each VEX dialect's vocabulary;
- format detection (OpenVEX vs CycloneDX) + unsupported / malformed rejection;
- adversarial inputs are rejected/skipped safely (broken JSON, missing fields,
  out-of-enum status, conflicting/duplicate statements, oversized doc,
  justification injection: XSS / CRLF / null byte / SQL, deep nesting);
- the legal-transition pathfinder produces the multi-step path the matrix
  requires (new → analyzing → not_affected) and refuses impossible jumps.
"""

from __future__ import annotations

import json
import uuid

import pytest

from core.security import CurrentUser
from services.vex_import import (
    _CYCLONEDX_REVERSE_MAP,
    _OPENVEX_REVERSE_MAP,
    VEXImportMalformed,
    VEXImportTooLarge,
    VEXImportUnsupportedFormat,
    _apply_to_finding,
    _clean_justification,
    _clean_provenance,
    _decode_json,
    _detect_and_parse,
    _legal_path,
    _Result,
    _Statement,
)
from services.vulnerability_service import STATUS_TRANSITIONS

# ===========================================================================
# Reverse status maps
# ===========================================================================


@pytest.mark.parametrize(
    ("vex_status", "internal"),
    [
        ("not_affected", "not_affected"),
        ("affected", "exploitable"),
        ("fixed", "fixed"),
        ("under_investigation", "analyzing"),
    ],
)
def test_openvex_reverse_map(vex_status: str, internal: str) -> None:
    assert _OPENVEX_REVERSE_MAP[vex_status] == internal


@pytest.mark.parametrize(
    ("vex_state", "internal"),
    [
        ("not_affected", "not_affected"),
        ("false_positive", "false_positive"),
        ("exploitable", "exploitable"),
        ("resolved", "fixed"),
        ("in_triage", "analyzing"),
    ],
)
def test_cyclonedx_reverse_map(vex_state: str, internal: str) -> None:
    assert _CYCLONEDX_REVERSE_MAP[vex_state] == internal


def test_reverse_map_targets_are_valid_internal_statuses() -> None:
    """Every reverse-map target must be a real status in the transition graph."""
    valid = set(STATUS_TRANSITIONS.keys())
    assert set(_OPENVEX_REVERSE_MAP.values()) <= valid
    assert set(_CYCLONEDX_REVERSE_MAP.values()) <= valid


def test_under_investigation_maps_to_analyzing_not_new() -> None:
    """`new` is the discovery inbox — nothing transitions *into* it. A VEX
    'still investigating' must land on `analyzing`, never `new`."""
    assert _OPENVEX_REVERSE_MAP["under_investigation"] == "analyzing"
    assert _CYCLONEDX_REVERSE_MAP["in_triage"] == "analyzing"


# ===========================================================================
# Legal-transition pathfinder
# ===========================================================================


def test_legal_path_already_at_target_is_empty() -> None:
    assert _legal_path("not_affected", "not_affected") == []


def test_legal_path_new_to_not_affected_is_multistep() -> None:
    """The matrix forbids new→not_affected directly; the legal path routes
    through analyzing."""
    path = _legal_path("new", "not_affected")
    assert path == ["analyzing", "not_affected"]


def test_legal_path_new_to_analyzing_is_single_hop() -> None:
    assert _legal_path("new", "analyzing") == ["analyzing"]


def test_legal_path_new_to_fixed_routes_through_analyzing() -> None:
    assert _legal_path("new", "fixed") == ["analyzing", "fixed"]


def test_legal_path_terminal_to_terminal_routes_through_analyzing() -> None:
    # fixed → analyzing → not_affected is the only legal way.
    assert _legal_path("fixed", "not_affected") == ["analyzing", "not_affected"]


def test_legal_path_every_pair_is_reachable_or_none() -> None:
    """Sanity: pathfinder never raises and returns list|None for all pairs."""
    states = set(STATUS_TRANSITIONS.keys()) | {
        s for tos in STATUS_TRANSITIONS.values() for s in tos
    }
    for a in states:
        for b in states:
            result = _legal_path(a, b)
            assert result is None or isinstance(result, list)


def test_legal_path_into_new_is_unreachable() -> None:
    """`new` is the discovery inbox: no transition leads *into* it, so the only
    genuinely illegal target is `new` itself. (No VEX status reverse-maps to
    `new`, so a real import never hits this — it is the importer's
    illegal_transition guard's only live trigger.)"""
    for src in ("analyzing", "exploitable", "not_affected", "fixed", "suppressed"):
        assert _legal_path(src, "new") is None


def test_legal_path_all_non_new_targets_reachable_from_any_state() -> None:
    """Every reverse-map target (analyzing/exploitable/not_affected/
    false_positive/fixed) is reachable from every state — guaranteeing the
    importer's multi-step path always succeeds for a well-mapped statement."""
    reverse_targets = set(_OPENVEX_REVERSE_MAP.values()) | set(
        _CYCLONEDX_REVERSE_MAP.values()
    )
    states = set(STATUS_TRANSITIONS.keys())
    for src in states:
        for tgt in reverse_targets:
            path = _legal_path(src, tgt)
            assert path is not None, f"{src} → {tgt} should be reachable"


# ===========================================================================
# Format detection + parsing — happy path
# ===========================================================================


def _openvex_doc(statements: list[dict]) -> dict:
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": "https://trustedoss.io/vex/p/abc",
        "author": "TrustedOSS Portal",
        "timestamp": "2025-01-02T03:04:05.000Z",
        "version": 1,
        "statements": statements,
    }


def _cyclonedx_doc(vulns: list[dict]) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:11111111-1111-1111-1111-111111111111",
        "version": 1,
        "metadata": {
            "timestamp": "2025-01-02T03:04:05.000Z",
            "tools": [{"name": "TrustedOSS Portal"}],
        },
        "vulnerabilities": vulns,
    }


def test_detect_openvex() -> None:
    doc = _openvex_doc(
        [
            {
                "vulnerability": {"name": "CVE-2099-0001"},
                "products": [{"@id": "pkg:npm/left-pad@1.0.0"}],
                "status": "not_affected",
                "impact_statement": "not reachable",
            }
        ]
    )
    fmt, statements, origin = _detect_and_parse(doc)
    assert fmt == "openvex"
    assert len(statements) == 1
    st = statements[0]
    assert st.vuln == "CVE-2099-0001"
    assert st.products == ["pkg:npm/left-pad@1.0.0"]
    assert st.target == "not_affected"
    assert st.justification == "not reachable"
    assert origin["id"] == "https://trustedoss.io/vex/p/abc"
    assert origin["author"] == "TrustedOSS Portal"


def test_detect_cyclonedx() -> None:
    doc = _cyclonedx_doc(
        [
            {
                "id": "CVE-2099-0002",
                "source": {"name": "NVD"},
                "analysis": {"state": "resolved", "detail": "patched in 1.0.1"},
                "affects": [{"ref": "pkg:npm/left-pad@1.0.0"}],
            }
        ]
    )
    fmt, statements, origin = _detect_and_parse(doc)
    assert fmt == "cyclonedx"
    st = statements[0]
    assert st.vuln == "CVE-2099-0002"
    assert st.products == ["pkg:npm/left-pad@1.0.0"]
    assert st.target == "fixed"  # resolved → fixed
    assert st.justification == "patched in 1.0.1"
    assert origin["id"].startswith("urn:uuid:")


def test_openvex_without_context_still_detected_by_statements_array() -> None:
    doc = {"statements": [{"vulnerability": {"name": "CVE-1"}, "status": "fixed"}]}
    fmt, statements, _ = _detect_and_parse(doc)
    assert fmt == "openvex"


def test_openvex_vulnerability_as_bare_string() -> None:
    doc = _openvex_doc(
        [{"vulnerability": "CVE-2099-0003", "products": ["pkg:npm/x@1"], "status": "fixed"}]
    )
    _, statements, _ = _detect_and_parse(doc)
    assert statements[0].vuln == "CVE-2099-0003"
    assert statements[0].products == ["pkg:npm/x@1"]


# ===========================================================================
# Format detection — rejection
# ===========================================================================


def test_unsupported_format_rejected() -> None:
    with pytest.raises(VEXImportUnsupportedFormat):
        _detect_and_parse({"foo": "bar", "not": "a vex doc"})


def test_non_object_root_rejected() -> None:
    with pytest.raises(VEXImportMalformed):
        _detect_and_parse(["not", "an", "object"])


def test_openvex_statements_not_array_rejected() -> None:
    with pytest.raises(VEXImportMalformed):
        _detect_and_parse(
            {"@context": "https://openvex.dev/ns", "statements": "not-a-list"}
        )


def test_cyclonedx_vulnerabilities_not_array_rejected() -> None:
    with pytest.raises(VEXImportMalformed):
        _detect_and_parse(
            {"bomFormat": "CycloneDX", "vulnerabilities": {"not": "a list"}}
        )


# ===========================================================================
# Decode + size limits + adversarial JSON
# ===========================================================================


def test_decode_valid_json() -> None:
    assert _decode_json(b'{"a": 1}') == {"a": 1}


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json",  # broken
        b'{"a": ',  # truncated
        b"\xff\xfe\x00bad",  # non-UTF-8 / null bytes
        b"",  # empty
        b"   ",  # whitespace only
        b'{"a": 1',  # missing close brace
    ],
)
def test_decode_malformed_raises(raw: bytes) -> None:
    with pytest.raises(VEXImportMalformed):
        _decode_json(raw)


def test_decode_oversized_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEX_IMPORT_MAX_BYTES", "10")
    with pytest.raises(VEXImportTooLarge):
        _decode_json(b'{"this": "is definitely longer than ten bytes"}')


def test_decode_deeply_nested_does_not_crash() -> None:
    """A pathologically deep document must be rejected cleanly, not blow the
    stack with an uncaught error."""
    deep = "[" * 2000 + "]" * 2000
    raw = json.dumps({"statements": []}).encode()  # baseline still parses
    assert _decode_json(raw) == {"statements": []}
    # The deep array on its own: either parses (small) or raises Malformed.
    try:
        _decode_json(deep.encode())
    except VEXImportMalformed:
        pass  # acceptable — surfaced as clean 422


def test_too_many_statements_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEX_IMPORT_MAX_STATEMENTS", "2")
    doc = _openvex_doc(
        [{"vulnerability": {"name": f"CVE-{i}"}, "status": "fixed"} for i in range(5)]
    )
    with pytest.raises(VEXImportTooLarge):
        _detect_and_parse(doc)


# ===========================================================================
# Adversarial statement contents — parsed safely into skip-able shapes
# ===========================================================================


def test_statement_missing_vulnerability_yields_none_vuln() -> None:
    doc = _openvex_doc([{"products": [{"@id": "pkg:npm/x@1"}], "status": "fixed"}])
    _, statements, _ = _detect_and_parse(doc)
    assert statements[0].vuln is None


def test_statement_out_of_enum_status_has_no_target() -> None:
    doc = _openvex_doc(
        [
            {
                "vulnerability": {"name": "CVE-1"},
                "products": [{"@id": "pkg:npm/x@1"}],
                "status": "totally-bogus-status",
            }
        ]
    )
    _, statements, _ = _detect_and_parse(doc)
    assert statements[0].vex_status == "totally-bogus-status"
    assert statements[0].target is None  # unmapped → will be skipped


def test_statement_numeric_fields_coerced_to_none() -> None:
    """A number where a string is expected must become None, never crash."""
    doc = _openvex_doc(
        [
            {
                "vulnerability": {"name": 12345},  # not a string
                "products": [{"@id": 999}],  # not a string
                "status": ["array"],  # not a string
            }
        ]
    )
    _, statements, _ = _detect_and_parse(doc)
    st = statements[0]
    assert st.vuln is None
    assert st.products == []
    assert st.vex_status is None
    assert st.target is None


def test_non_dict_statement_in_array_is_tolerated() -> None:
    doc = _openvex_doc(["i am not a dict", 42, None])  # type: ignore[list-item]
    _, statements, _ = _detect_and_parse(doc)
    assert len(statements) == 3
    assert all(s.vuln is None for s in statements)


def test_duplicate_statements_both_parsed() -> None:
    """Conflicting/duplicate statements are both parsed; resolution happens at
    apply time (idempotency / last-writer per finding)."""
    doc = _openvex_doc(
        [
            {
                "vulnerability": {"name": "CVE-DUP"},
                "products": [{"@id": "pkg:npm/x@1"}],
                "status": "fixed",
            },
            {
                "vulnerability": {"name": "CVE-DUP"},
                "products": [{"@id": "pkg:npm/x@1"}],
                "status": "not_affected",
            },
        ]
    )
    _, statements, _ = _detect_and_parse(doc)
    assert len(statements) == 2
    assert statements[0].target == "fixed"
    assert statements[1].target == "not_affected"


# ===========================================================================
# Justification sanitization — injection-resistant persistence
# ===========================================================================


def test_justification_strips_crlf_and_null() -> None:
    raw = "line1\r\nline2\x00line3"
    cleaned = _clean_justification(raw)
    assert cleaned is not None
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert "\x00" not in cleaned
    assert "line1" in cleaned and "line2" in cleaned and "line3" in cleaned


def test_justification_keeps_tab_and_unicode() -> None:
    cleaned = _clean_justification("col1\tcol2 — 한국어 ✓")
    assert cleaned == "col1\tcol2 — 한국어 ✓"


def test_justification_xss_stored_verbatim_for_ui_escaping() -> None:
    """We do NOT HTML-escape at storage; the UI escapes on render. The payload
    is plain printable text so it survives intact (rendered inert by React)."""
    payload = "<script>alert(1)</script>"
    assert _clean_justification(payload) == payload


def test_justification_sql_payload_stored_as_text() -> None:
    """Parameterised queries make SQL payloads inert; we store the literal."""
    payload = "'; DROP TABLE vulnerability_findings; --"
    assert _clean_justification(payload) == payload


def test_justification_truncated_to_4000_chars() -> None:
    cleaned = _clean_justification("a" * 9000)
    assert cleaned is not None
    assert len(cleaned) == 4000


@pytest.mark.parametrize("value", [None, 123, [], {}, "", "   ", "\x00\r\n"])
def test_justification_non_text_or_empty_is_none(value: object) -> None:
    assert _clean_justification(value) is None


# ===========================================================================
# Provenance sanitization — control-char / NUL stripping for vex_origin (JSONB)
# ===========================================================================


def test_provenance_strips_crlf_null_and_del() -> None:
    """Embedded NUL / C0 / DEL would fail the JSONB commit; they are stripped."""
    cleaned = _clean_provenance("urn:uuid\x00:ab\r\ncd\x7fef")
    assert cleaned is not None
    assert "\x00" not in cleaned
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert "\x7f" not in cleaned
    assert cleaned == "urn:uuid:abcdef"


def test_provenance_keeps_tab_and_unicode() -> None:
    assert _clean_provenance("Tool\tName — 한국어 ✓") == "Tool\tName — 한국어 ✓"


def test_provenance_has_no_length_cap() -> None:
    """Unlike justification (4000-char cap), provenance is not truncated."""
    long_id = "u" * 9000
    assert _clean_provenance(long_id) == long_id


@pytest.mark.parametrize("value", [None, 123, [], {}, "", "   ", "\x00\r\n"])
def test_provenance_non_text_or_empty_is_none(value: object) -> None:
    assert _clean_provenance(value) is None


@pytest.mark.parametrize(
    "field",
    ["@id", "author", "timestamp"],
)
def test_openvex_origin_provenance_is_sanitized(field: str) -> None:
    """A NUL / CRLF / DEL in an OpenVEX top-level provenance field is stripped
    before it reaches ``vex_origin`` (would otherwise break the JSONB commit)."""
    doc = _openvex_doc(
        [{"vulnerability": {"name": "CVE-1"}, "products": ["pkg:npm/x@1"], "status": "fixed"}]
    )
    doc[field] = "ev\x00il\r\nval\x7fue"
    _, _, origin = _detect_and_parse(doc)
    key = "id" if field == "@id" else field
    assert origin[key] == "evilvalue"
    assert "\x00" not in origin[key]
    assert "\r" not in origin[key] and "\n" not in origin[key]
    assert "\x7f" not in origin[key]


def test_cyclonedx_origin_serial_number_is_sanitized() -> None:
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid\x00:abc\r\n",
        "metadata": {
            "timestamp": "2025-01-01T00:00:00Z\x00",
            "tools": [{"name": "Tool\x7fA\r\n"}],
        },
        "vulnerabilities": [],
    }
    _, _, origin = _detect_and_parse(doc)
    assert origin["id"] == "urn:uuid:abc"
    assert origin["timestamp"] == "2025-01-01T00:00:00Z"
    assert origin["author"] == "ToolA"
    for key in ("id", "timestamp", "author"):
        assert "\x00" not in origin[key]
        assert "\r" not in origin[key] and "\n" not in origin[key]
        assert "\x7f" not in origin[key]


# ===========================================================================
# CycloneDX parser — adversarial edges
# ===========================================================================


def test_cyclonedx_non_dict_vuln_in_array_tolerated() -> None:
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "vulnerabilities": ["not-a-dict", 7, None],
    }
    fmt, statements, _ = _detect_and_parse(doc)
    assert fmt == "cyclonedx"
    assert len(statements) == 3
    assert all(s.vuln is None for s in statements)


def test_cyclonedx_bare_string_affects_ref() -> None:
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "vulnerabilities": [
            {
                "id": "CVE-1",
                "analysis": {"state": "resolved"},
                "affects": ["pkg:npm/x@1", {"ref": "pkg:npm/y@2"}],
            }
        ],
    }
    _, statements, _ = _detect_and_parse(doc)
    assert statements[0].products == ["pkg:npm/x@1", "pkg:npm/y@2"]
    assert statements[0].target == "fixed"  # resolved → fixed


def test_cyclonedx_out_of_enum_state_has_no_target() -> None:
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "vulnerabilities": [
            {"id": "CVE-1", "analysis": {"state": "made-up-state"}, "affects": []}
        ],
    }
    _, statements, _ = _detect_and_parse(doc)
    assert statements[0].vex_status == "made-up-state"
    assert statements[0].target is None


def test_cyclonedx_too_many_vulns_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEX_IMPORT_MAX_STATEMENTS", "1")
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "vulnerabilities": [{"id": f"CVE-{i}"} for i in range(3)],
    }
    with pytest.raises(VEXImportTooLarge):
        _detect_and_parse(doc)


def test_cyclonedx_metadata_author_from_first_tool() -> None:
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:abc",
        "metadata": {
            "timestamp": "2025-01-01T00:00:00Z",
            "tools": [{"name": "ToolA"}, {"name": "ToolB"}],
        },
        "vulnerabilities": [],
    }
    _, _, origin = _detect_and_parse(doc)
    assert origin["author"] == "ToolA"
    assert origin["timestamp"] == "2025-01-01T00:00:00Z"
    assert origin["id"] == "urn:uuid:abc"


# ===========================================================================
# _apply_to_finding — defensive transition branches (no DB; fake objects)
# ===========================================================================


class _FakeFinding:
    """Minimal stand-in for a VulnerabilityFinding row (no ORM/DB)."""

    def __init__(self, status: str) -> None:
        self.status = status
        self.analysis_state = status
        self.analysis_justification: str | None = None
        self.analyst_user_id: uuid.UUID | None = None
        self.analyzed_at = None
        self.updated_at = None
        self.analysis_source: str | None = None
        self.vex_origin: dict | None = None


class _FakeSession:
    """No-op async session: ``flush`` does nothing (we assert on the object)."""

    async def flush(self) -> None:  # noqa: D401
        return None


def _developer(team_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="dev@example.com",
        role="developer",
        team_ids=[team_id],
        team_roles={team_id: "developer"},
        is_active=True,
        is_superuser=False,
    )


def _statement(target: str | None, *, vex_status: str = "x") -> _Statement:
    return _Statement(
        vuln="CVE-1",
        products=["pkg:npm/x@1"],
        vex_status=vex_status,
        target=target,
        justification="note",
    )


async def test_apply_already_at_target_is_skip() -> None:
    from datetime import UTC, datetime

    team_id = uuid.uuid4()
    finding = _FakeFinding("fixed")
    result = _Result()
    await _apply_to_finding(
        _FakeSession(),  # type: ignore[arg-type]
        finding,  # type: ignore[arg-type]
        statement=_statement("fixed"),
        actor=_developer(team_id),
        team_id=team_id,
        origin={"format": "openvex"},
        result=result,
        now=datetime.now(tz=UTC),
    )
    assert result.matched == 1
    assert result.applied == 0
    assert result.skipped == 1
    assert result.errors[0]["reason"] == "already_at_target"
    assert finding.status == "fixed"


async def test_apply_into_new_is_illegal_transition() -> None:
    """No legal path leads into `new` → illegal_transition, finding untouched."""
    from datetime import UTC, datetime

    team_id = uuid.uuid4()
    finding = _FakeFinding("analyzing")
    result = _Result()
    await _apply_to_finding(
        _FakeSession(),  # type: ignore[arg-type]
        finding,  # type: ignore[arg-type]
        statement=_statement("new"),
        actor=_developer(team_id),
        team_id=team_id,
        origin={"format": "openvex"},
        result=result,
        now=datetime.now(tz=UTC),
    )
    assert result.applied == 0
    assert result.skipped == 1
    assert result.errors[0]["reason"] == "illegal_transition"
    assert finding.status == "analyzing"


async def test_apply_suppressed_by_developer_is_forbidden() -> None:
    """A developer landing in `suppressed` is forbidden (defense-in-depth: the
    endpoint already gates team_admin, but the service re-checks per hop)."""
    from datetime import UTC, datetime

    team_id = uuid.uuid4()
    finding = _FakeFinding("analyzing")
    result = _Result()
    await _apply_to_finding(
        _FakeSession(),  # type: ignore[arg-type]
        finding,  # type: ignore[arg-type]
        statement=_statement("suppressed"),
        actor=_developer(team_id),
        team_id=team_id,
        origin={"format": "openvex"},
        result=result,
        now=datetime.now(tz=UTC),
    )
    assert result.applied == 0
    assert result.skipped == 1
    assert result.errors[0]["reason"] == "forbidden_transition"
    assert finding.status == "analyzing"  # untouched


async def test_apply_none_target_fails_closed_as_skip() -> None:
    """Fail-closed guard: a None target (would be stripped ``assert`` under -O)
    yields a structured skip, not a crash, and leaves the finding untouched."""
    from datetime import UTC, datetime

    team_id = uuid.uuid4()
    finding = _FakeFinding("analyzing")
    result = _Result()
    await _apply_to_finding(
        _FakeSession(),  # type: ignore[arg-type]
        finding,  # type: ignore[arg-type]
        statement=_statement(None, vex_status="made-up"),
        actor=_developer(team_id),
        team_id=team_id,
        origin={"format": "openvex"},
        result=result,
        now=datetime.now(tz=UTC),
    )
    assert result.matched == 1
    assert result.applied == 0
    assert result.skipped == 1
    assert result.errors[0]["reason"] == "unmapped_status"
    assert finding.status == "analyzing"  # untouched
    assert finding.vex_origin is None


async def test_apply_sanitizes_vex_status_into_origin() -> None:
    """A control-char / NUL in vex_status is stripped before it lands in the
    JSONB ``vex_origin`` (an embedded NUL would fail the commit)."""
    from datetime import UTC, datetime

    team_id = uuid.uuid4()
    finding = _FakeFinding("analyzing")
    result = _Result()
    await _apply_to_finding(
        _FakeSession(),  # type: ignore[arg-type]
        finding,  # type: ignore[arg-type]
        statement=_statement("fixed", vex_status="fi\x00xe\r\nd"),
        actor=_developer(team_id),
        team_id=team_id,
        origin={"format": "openvex", "id": "doc-1"},
        result=result,
        now=datetime.now(tz=UTC),
    )
    assert result.applied == 1
    assert finding.vex_origin is not None
    assert finding.vex_origin["vex_status"] == "fixed"
    assert "\x00" not in finding.vex_origin["vex_status"]
