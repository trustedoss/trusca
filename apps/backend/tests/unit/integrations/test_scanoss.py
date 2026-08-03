"""
SCANOSS adapter unit tests (Phase J / P3-11).

SCANOSS sends file fingerprints to an external API, so the two things these
tests pin hardest are:

  - PRIVACY: when ``SCANOSS_ENABLED`` is unset/false the adapter runs NO
    subprocess and performs NO egress — it returns an empty result immediately.
    We prove "no subprocess" by monkeypatching the subprocess seam with a
    sentinel that fails the test if it is ever invoked.
  - PRECISION: only full-file (``id == "file"``) matches are promoted; snippet
    matches are skipped. The fixture is a realistic dense SCANOSS response
    (multiple matches per path, snippet + file mixed, multi-CVE-style density)
    per the hardening rule against hand-made minimal JSON.

We NEVER spawn the real ``scanoss-py`` binary; the subprocess seam
(``run_with_line_streaming``) is stubbed in every "enabled" test.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Realistic fixture — a dense SCANOSS "plain" JSON response.
#
# Shape: {"<scanned path>": [ {match}, {match}, ... ]}. Real responses carry
# multiple matches per path and mix "file" (full-file) with "snippet" (partial)
# ids. This fixture has: two clean full-file matches, one path where a full-file
# match co-exists with a snippet match, and one path that is snippet-only. Only
# the three full-file matches (parson, zlib, inih) must be promoted.
# ---------------------------------------------------------------------------

_SCANOSS_FIXTURE: dict[str, Any] = {
    "src/vendor/parson.c": [
        {
            "id": "file",
            "status": "pending",
            "purl": ["pkg:github/kgabis/parson"],
            "component": "parson",
            "version": "1.5.2",
            "latest": "1.5.3",
            "url": "https://github.com/kgabis/parson",
            "licenses": [
                {"name": "MIT", "source": "component_declared"},
                {"name": "MIT", "source": "file_header"},
            ],
        }
    ],
    "src/vendor/zlib/inflate.c": [
        {
            "id": "file",
            "purl": ["pkg:github/madler/zlib"],
            "component": "zlib",
            "version": "1.3.1",
            "licenses": [{"name": "Zlib", "source": "component_declared"}],
        },
        {
            # A weaker snippet match on the SAME path — must be ignored; the
            # full-file match above wins and the snippet adds no component.
            "id": "snippet",
            "purl": ["pkg:github/someone/zlib-fork"],
            "component": "zlib-fork",
            "version": "0.0.1",
            "lines": "10-42",
            "licenses": [{"name": "GPL-2.0-only"}],
        },
    ],
    "src/config/ini.c": [
        {
            "id": "file",
            "purl": ["pkg:github/benhoyt/inih"],
            "component": "inih",
            "version": "r58",
            "licenses": [
                {"name": "BSD-3-Clause"},
                # Duplicate name at a different source — de-duped to one finding.
                {"name": "BSD-3-Clause"},
            ],
        }
    ],
    "src/util/strdup.c": [
        {
            # Snippet-only path: a few copied lines. NOTHING should be promoted.
            "id": "snippet",
            "purl": ["pkg:github/gnu/glibc"],
            "component": "glibc",
            "version": "2.39",
            "lines": "3-9",
            "licenses": [{"name": "LGPL-2.1-or-later"}],
        }
    ],
    "src/app/main.c": [
        {
            # No match at all — SCANOSS emits id="none" for scanned-but-unmatched
            # files. Must be ignored.
            "id": "none",
        }
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_stream_writing(payload: dict[str, Any], *, returncode: int = 0):
    """Build a ``run_with_line_streaming`` replacement that writes ``payload``
    to the ``--output`` path from the argv and returns a CompletedProcess."""

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output")
        out_path = Path(cmd[out_idx + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, b"", b"scanoss done\n")

    return _fake


def _enable_and_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the feature ON and pretend the binary is installed."""
    monkeypatch.setenv("SCANOSS_ENABLED", "true")
    monkeypatch.setattr(
        "integrations.scanoss.shutil.which",
        lambda _: "/usr/local/bin/scanoss-py",
    )


# ---------------------------------------------------------------------------
# PRIVACY — disabled means no subprocess, no egress
# ---------------------------------------------------------------------------


def test_disabled_by_default_returns_empty_and_never_spawns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SCANOSS_ENABLED unset → empty result, and the subprocess seam is never
    touched (no fingerprinting, no egress)."""
    from integrations import scanoss

    monkeypatch.delenv("SCANOSS_ENABLED", raising=False)
    # Pretend the binary IS installed, to prove the guard is the ENABLED flag
    # and not merely a missing binary.
    monkeypatch.setattr(
        "integrations.scanoss.shutil.which", lambda _: "/usr/local/bin/scanoss-py"
    )

    def _must_not_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("scanoss subprocess must not run when disabled")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _must_not_run)

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )

    assert result.vendored == []
    assert result.result_path is None
    # No output dir was even created (the disabled branch returns before mkdir).
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "enabled", "", "TrUe-ish"])
def test_non_truthy_values_stay_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    """Only true/1/yes enable it — everything else fails closed to no-egress."""
    from integrations import scanoss

    monkeypatch.setenv("SCANOSS_ENABLED", value)
    monkeypatch.setattr(
        "integrations.scanoss.run_with_line_streaming",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not run")
        ),  # pragma: no cover
    )

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    assert result.vendored == []


# ---------------------------------------------------------------------------
# DEGRADE — enabled but binary missing
# ---------------------------------------------------------------------------


def test_enabled_but_binary_missing_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    monkeypatch.setenv("SCANOSS_ENABLED", "true")
    monkeypatch.setattr("integrations.scanoss.shutil.which", lambda _: None)

    def _must_not_run(*_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("subprocess must not run when binary absent")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _must_not_run)

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    assert result.vendored == []
    assert result.result_path is None


# ---------------------------------------------------------------------------
# PRECISION — full-file matches promoted, snippets skipped
# ---------------------------------------------------------------------------


def test_full_file_matches_promoted_snippets_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.setattr(
        "integrations.scanoss.run_with_line_streaming",
        _fake_stream_writing(_SCANOSS_FIXTURE),
    )

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )

    purls = {vc.purl for vc in result.vendored}
    # Three full-file matches promoted.
    assert purls == {
        "pkg:github/kgabis/parson",
        "pkg:github/madler/zlib",
        "pkg:github/benhoyt/inih",
    }
    # Snippet-only (glibc) and the snippet co-match (zlib-fork) are absent.
    assert "pkg:github/gnu/glibc" not in purls
    assert "pkg:github/someone/zlib-fork" not in purls
    assert result.result_path is not None and result.result_path.exists()


def test_parsed_fields_and_license_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.setattr(
        "integrations.scanoss.run_with_line_streaming",
        _fake_stream_writing(_SCANOSS_FIXTURE),
    )

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    by_purl = {vc.purl: vc for vc in result.vendored}

    parson = by_purl["pkg:github/kgabis/parson"]
    assert parson.name == "parson"
    assert parson.version == "1.5.2"
    # Two "MIT" entries at different sources collapse to one.
    assert parson.licenses == ["MIT"]

    inih = by_purl["pkg:github/benhoyt/inih"]
    assert inih.version == "r58"
    assert inih.licenses == ["BSD-3-Clause"]  # duplicate name de-duped


def test_nonzero_exit_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.setattr(
        "integrations.scanoss.run_with_line_streaming",
        _fake_stream_writing(_SCANOSS_FIXTURE, returncode=2),
    )

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    # Non-zero exit → empty, non-fatal.
    assert result.vendored == []


def test_timeout_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)

    def _timeout(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=["scanoss-py"], timeout=1.0)

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _timeout)

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    assert result.vendored == []


def test_unparseable_json_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)

    def _write_garbage(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[bytes]:
        out_path = Path(cmd[cmd.index("--output") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("{not json", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _write_garbage)

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    assert result.vendored == []


def test_lenient_shapes_bare_purl_string_and_name_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SCANOSS variants: purl as a bare string, missing component (name derived
    from purl), string-form licenses, and a match with no purl (dropped)."""
    from integrations import scanoss

    payload: dict[str, Any] = {
        "a.c": [
            {
                # purl as a bare string (not a list), no "component" field.
                "id": "file",
                "purl": "pkg:github/acme/widget@2.0.0",
                "version": "2.0.0",
                # licenses as bare strings, not {"name": ...} dicts.
                "licenses": ["Apache-2.0", "Apache-2.0"],
            }
        ],
        "b.c": [
            {
                # No usable purl → dropped entirely.
                "id": "file",
                "purl": [],
                "component": "ghost",
                "version": "1.0.0",
            }
        ],
    }

    _enable_and_install(monkeypatch)
    monkeypatch.setattr(
        "integrations.scanoss.run_with_line_streaming",
        _fake_stream_writing(payload),
    )

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )

    assert len(result.vendored) == 1
    widget = result.vendored[0]
    assert widget.purl == "pkg:github/acme/widget@2.0.0"
    # component omitted → name derived from the purl tail (version stripped).
    assert widget.name == "widget"
    assert widget.licenses == ["Apache-2.0"]  # string-form + de-duped


def test_missing_result_file_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 0 but no output file written → empty (degraded, non-fatal)."""
    from integrations import scanoss

    _enable_and_install(monkeypatch)

    def _no_write(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _no_write)

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    assert result.vendored == []


def test_result_too_large_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A result over the size ceiling is not deserialized (OOM guard)."""
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.setattr("integrations.scanoss.MAX_RESULT_BYTES", 8)
    monkeypatch.setattr(
        "integrations.scanoss.run_with_line_streaming",
        _fake_stream_writing(_SCANOSS_FIXTURE),  # far bigger than 8 bytes
    )

    result = scanoss.run_scanoss(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out"
    )
    assert result.vendored == []


# ---------------------------------------------------------------------------
# Command construction — endpoint + optional key
# ---------------------------------------------------------------------------


def test_command_omits_key_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.delenv("SCANOSS_API_KEY", raising=False)
    monkeypatch.setenv("SCANOSS_API_URL", "https://api.osskb.org")

    captured: dict[str, Any] = {}

    def _capture(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("--output") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _capture)

    scanoss.run_scanoss(source_dir=tmp_path / "src", output_dir=tmp_path / "out")

    cmd = captured["cmd"]
    assert cmd[:2] == ["scanoss-py", "scan"]
    assert "--apiurl" in cmd
    assert cmd[cmd.index("--apiurl") + 1] == "https://api.osskb.org"
    assert "--key" not in cmd  # no key configured → flag absent


def test_command_includes_key_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.setenv("SCANOSS_API_KEY", "sk-secret-123")

    captured: dict[str, Any] = {}

    def _capture(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("--output") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _capture)

    scanoss.run_scanoss(source_dir=tmp_path / "src", output_dir=tmp_path / "out")

    cmd = captured["cmd"]
    assert "--key" in cmd
    assert cmd[cmd.index("--key") + 1] == "sk-secret-123"


# ---------------------------------------------------------------------------
# Trust-boundary hardening (security-review Low-1 / Low-3)
# ---------------------------------------------------------------------------


def test_oversized_name_and_version_are_truncated_to_column_widths() -> None:
    """A hostile / MITM'd endpoint returning an over-long component / version
    must not exceed the destination column widths — otherwise the INSERT raises
    StringDataRightTruncation and the whole vendored batch rolls back, silently
    dropping every vendored finding for the scan (security-review Low-1)."""
    from integrations import scanoss

    match = {
        "id": "file",
        "purl": ["pkg:generic/liblzma"],
        "component": "L" * 900,
        "version": "9" * 400,
        "licenses": [{"name": "MIT"}],
    }
    vc = scanoss._parse_match(match)
    assert vc is not None
    assert len(vc.name) == scanoss.COMPONENT_NAME_MAX_LENGTH
    assert len(vc.version) == scanoss.COMPONENT_VERSION_MAX_LENGTH


def test_api_key_redacted_from_streamed_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A configured key echoed on a streamed line (by any scanoss-py version)
    must be redacted before it reaches the team-readable scan log — the key
    non-leak must not depend on the external binary's logging behaviour
    (security-review Low-3)."""
    from integrations import scanoss

    _enable_and_install(monkeypatch)
    monkeypatch.setenv("SCANOSS_API_KEY", "sk-secret-123")

    seen: list[str] = []

    def _fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        cb = kwargs.get("line_callback")
        if cb is not None:
            cb("auth: Bearer sk-secret-123 -> api.osskb.org", "scanoss")
        # Non-zero exit whose stderr also echoes the key (server-log path).
        return subprocess.CompletedProcess(
            cmd, 1, b"", b"error: key sk-secret-123 rejected\n"
        )

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _fake)

    scanoss.run_scanoss(
        source_dir=tmp_path / "src",
        output_dir=tmp_path / "out",
        line_callback=lambda line, _stage: seen.append(line),
    )

    assert seen, "line callback was never invoked"
    for line in seen:
        assert "sk-secret-123" not in line
        assert "***" in line
