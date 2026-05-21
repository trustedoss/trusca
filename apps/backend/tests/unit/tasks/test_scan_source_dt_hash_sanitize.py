"""
Unit tests for ``_sanitize_sbom_hashes_for_dt`` (bug fix: DT 400 on npm BOMs).

Bug:
    cdxgen 12.3.3 copies an npm lockfile's ``integrity`` (base64 sha512, e.g.
    ``sha512-...==``) verbatim into ``components[].hashes[].content``. The
    CycloneDX schema requires hash content to be a *hex* digest of one of the
    supported widths (32/40/64/96/128). DT validates the BOM server-side and
    rejects the whole document with HTTP 400, sinking vulnerability matching for
    every npm-lockfile project. The sanitizer strips the offending hash entries
    (NOT the components) from the bytes we send to DT.

Pinned behaviour:

* base64 sha512 ``integrity`` content → removed; valid hex hashes preserved;
  the component itself is kept.
* a component whose hashes are *all* invalid → ``hashes`` key removed entirely
  (an empty ``hashes: []`` is itself a schema violation in some DT versions).
* a clean SBOM (only valid hex) → returned byte-for-byte unchanged.
* malformed / non-JSON input → returned unchanged (best-effort, no crash).
* ``metadata.component.hashes`` is sanitized too (the root component).
* adversarial content (separator tokens, near-miss widths, CRLF, null bytes,
  base64 padding, non-string content) is treated as invalid and removed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tasks.scan_source import (
    _is_valid_hash_content,
    _sanitize_hashes_array,
    _sanitize_sbom_hashes_for_dt,
)

# Real-world base64 sha512 integrity value as emitted by npm lockfiles.
_NPM_INTEGRITY = (
    "sha512-1nzZbpiprPMm6V1+v8b6q3hChC0VKZ7vWuLM7Q1u4N6Zk2pK0WpQy5Yk2nN8a=="
)
# Valid hex digests, one per supported algorithm width.
_HEX_MD5 = "d41d8cd98f00b204e9800998ecf8427e"  # 32
_HEX_SHA1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709"  # 40
_HEX_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # 64


# ---------------------------------------------------------------------------
# _is_valid_hash_content — pattern unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        _HEX_MD5,
        _HEX_SHA1,
        _HEX_SHA256,
        "A" * 96,  # SHA-384 width, upper-case hex
        "f" * 128,  # SHA-512 width, lower-case hex
    ],
)
def test_valid_hex_content_accepted(content: str) -> None:
    assert _is_valid_hash_content(content) is True


@pytest.mark.parametrize(
    "content",
    [
        _NPM_INTEGRITY,  # the actual bug trigger (base64 + sha512- prefix)
        "sha512-" + "A" * 88,  # base64 body, wrong charset/length
        "Zm9vYmFy",  # plain base64 ("foobar")
        "abc",  # too short
        "g" * 64,  # 64 chars but 'g' is not hex
        "a" * 31,  # one short of MD5 width
        "a" * 33,  # one over MD5 width
        "a" * 65,  # between SHA-256 and SHA-384 widths
        "d41d8cd9-8f00-b204-e980-0998ecf8427e",  # hyphen separators (UUID-ish)
        "d41d8cd9 8f00b204e9800998ecf8427e ",  # embedded space + trailing space
        f"{_HEX_SHA256}\n",  # trailing newline (anchors must reject)
        f"\r\n{_HEX_SHA256}",  # leading CRLF
        f"{_HEX_MD5}\x00",  # trailing null byte
        "",  # empty
        "::::::::::::::::::::::::::::::::",  # separator-only token
    ],
)
def test_invalid_content_rejected(content: str) -> None:
    assert _is_valid_hash_content(content) is False


@pytest.mark.parametrize(
    "content",
    [None, 123, ["a" * 64], {"x": "y"}, b"a" * 64],
)
def test_non_string_content_rejected(content: Any) -> None:
    assert _is_valid_hash_content(content) is False


# ---------------------------------------------------------------------------
# _sanitize_hashes_array — standalone helper contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hashes", [None, "oops", 123, {"a": 1}])
def test_sanitize_hashes_array_non_list_returns_none(hashes: Any) -> None:
    """A non-list ``hashes`` collapses to ``None`` (caller drops the key)."""
    assert _sanitize_hashes_array(hashes) is None


def test_sanitize_hashes_array_all_invalid_returns_none() -> None:
    assert (
        _sanitize_hashes_array([{"alg": "SHA-512", "content": _NPM_INTEGRITY}]) is None
    )


def test_sanitize_hashes_array_keeps_valid_only() -> None:
    assert _sanitize_hashes_array(
        [
            {"alg": "SHA-512", "content": _NPM_INTEGRITY},
            {"alg": "SHA-256", "content": _HEX_SHA256},
        ]
    ) == [{"alg": "SHA-256", "content": _HEX_SHA256}]


# ---------------------------------------------------------------------------
# _sanitize_sbom_hashes_for_dt — document level
# ---------------------------------------------------------------------------


def _roundtrip(doc: dict[str, Any]) -> dict[str, Any]:
    out = _sanitize_sbom_hashes_for_dt(json.dumps(doc).encode("utf-8"))
    result: dict[str, Any] = json.loads(out)
    return result


def test_base64_integrity_removed_valid_hex_preserved_component_kept() -> None:
    """The core bug: a component with both a base64 integrity and a valid hex
    hash keeps the hex one, drops the base64 one, and survives intact."""
    doc = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "name": "lodash",
                "purl": "pkg:npm/lodash@4.17.21",
                "hashes": [
                    {"alg": "SHA-512", "content": _NPM_INTEGRITY},
                    {"alg": "SHA-256", "content": _HEX_SHA256},
                ],
            }
        ],
    }

    result = _roundtrip(doc)

    comp = result["components"][0]
    assert comp["name"] == "lodash"
    assert comp["purl"] == "pkg:npm/lodash@4.17.21"
    assert comp["hashes"] == [{"alg": "SHA-256", "content": _HEX_SHA256}]


def test_all_invalid_hashes_drops_hashes_key_keeps_component() -> None:
    """A component whose every hash is invalid loses the ``hashes`` key but is
    otherwise preserved — DT then accepts the BOM and matches on purl."""
    doc = {
        "components": [
            {
                "name": "left-pad",
                "purl": "pkg:npm/left-pad@1.3.0",
                "version": "1.3.0",
                "hashes": [
                    {"alg": "SHA-512", "content": _NPM_INTEGRITY},
                    {"alg": "SHA-1", "content": "Zm9vYmFy"},
                ],
            }
        ],
    }

    result = _roundtrip(doc)

    comp = result["components"][0]
    assert "hashes" not in comp
    assert comp["name"] == "left-pad"
    assert comp["purl"] == "pkg:npm/left-pad@1.3.0"
    assert comp["version"] == "1.3.0"


def test_clean_sbom_returned_byte_for_byte_unchanged() -> None:
    """A BOM with only valid hex hashes is not re-serialized — returned as-is."""
    doc = {
        "bomFormat": "CycloneDX",
        "components": [
            {
                "name": "ok-pkg",
                "purl": "pkg:npm/ok-pkg@1.0.0",
                "hashes": [{"alg": "SHA-256", "content": _HEX_SHA256}],
            }
        ],
    }
    original = json.dumps(doc).encode("utf-8")

    assert _sanitize_sbom_hashes_for_dt(original) == original


def test_component_with_no_hashes_untouched() -> None:
    """No ``hashes`` key → no mutation, original bytes returned."""
    doc = {
        "components": [
            {"name": "no-hash", "purl": "pkg:npm/no-hash@2.0.0"},
        ],
    }
    original = json.dumps(doc).encode("utf-8")

    assert _sanitize_sbom_hashes_for_dt(original) == original


def test_metadata_component_hashes_sanitized() -> None:
    """The root ``metadata.component.hashes`` array is sanitized too."""
    doc = {
        "metadata": {
            "component": {
                "name": "my-app",
                "purl": "pkg:npm/my-app@0.1.0",
                "hashes": [
                    {"alg": "SHA-512", "content": _NPM_INTEGRITY},
                    {"alg": "MD5", "content": _HEX_MD5},
                ],
            }
        },
        "components": [],
    }

    result = _roundtrip(doc)

    root = result["metadata"]["component"]
    assert root["name"] == "my-app"
    assert root["hashes"] == [{"alg": "MD5", "content": _HEX_MD5}]


def test_metadata_component_all_invalid_drops_hashes_key() -> None:
    doc = {
        "metadata": {
            "component": {
                "name": "my-app",
                "hashes": [{"alg": "SHA-512", "content": _NPM_INTEGRITY}],
            }
        },
    }

    result = _roundtrip(doc)

    assert "hashes" not in result["metadata"]["component"]
    assert result["metadata"]["component"]["name"] == "my-app"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json at all",
        b"{ broken json",
        b'{"components": [}',
        b"",
        b"\x00\x01\x02",
        b"[1, 2, 3",  # truncated array
    ],
)
def test_malformed_json_returns_original_no_crash(raw: bytes) -> None:
    """Best-effort: unparseable input is returned verbatim (DT will reject it
    later with a clear message; we don't crash the worker here)."""
    assert _sanitize_sbom_hashes_for_dt(raw) == raw


def test_top_level_json_array_returned_unchanged() -> None:
    """A JSON document that isn't an object (e.g. a bare array) is returned
    unchanged — there is no ``components``/``metadata`` to walk."""
    raw = b"[1, 2, 3]"
    assert _sanitize_sbom_hashes_for_dt(raw) == raw


def test_hashes_not_a_list_drops_key() -> None:
    """A malformed ``hashes`` that is not a list is removed defensively."""
    doc = {
        "components": [
            {"name": "weird", "purl": "pkg:npm/weird@1.0.0", "hashes": "oops"},
        ],
    }

    result = _roundtrip(doc)

    assert "hashes" not in result["components"][0]
    assert result["components"][0]["name"] == "weird"


def test_non_dict_hash_entries_removed() -> None:
    """Hash entries that aren't dicts (or carry non-string content) are dropped
    while a valid sibling survives."""
    doc = {
        "components": [
            {
                "name": "mixed",
                "purl": "pkg:npm/mixed@1.0.0",
                "hashes": [
                    "not-a-dict",
                    {"alg": "SHA-256", "content": 12345},
                    {"alg": "SHA-256"},  # missing content
                    {"alg": "SHA-256", "content": _HEX_SHA256},
                ],
            }
        ],
    }

    result = _roundtrip(doc)

    assert result["components"][0]["hashes"] == [
        {"alg": "SHA-256", "content": _HEX_SHA256}
    ]


def test_non_dict_component_entries_skipped() -> None:
    """A non-dict entry in the components array is ignored, not a crash."""
    doc = {
        "components": [
            "garbage",
            123,
            {
                "name": "real",
                "purl": "pkg:npm/real@1.0.0",
                "hashes": [{"alg": "SHA-512", "content": _NPM_INTEGRITY}],
            },
        ],
    }

    result = _roundtrip(doc)

    assert result["components"][0] == "garbage"
    assert result["components"][1] == 123
    assert "hashes" not in result["components"][2]


def test_multiple_components_only_dirty_ones_changed() -> None:
    """Mixed BOM: clean component preserved, dirty component sanitized, and the
    returned document re-serializes to valid JSON."""
    doc = {
        "components": [
            {
                "name": "clean",
                "purl": "pkg:npm/clean@1.0.0",
                "hashes": [{"alg": "SHA-1", "content": _HEX_SHA1}],
            },
            {
                "name": "dirty",
                "purl": "pkg:npm/dirty@2.0.0",
                "hashes": [{"alg": "SHA-512", "content": _NPM_INTEGRITY}],
            },
        ],
    }

    result = _roundtrip(doc)

    assert result["components"][0]["hashes"] == [{"alg": "SHA-1", "content": _HEX_SHA1}]
    assert "hashes" not in result["components"][1]
