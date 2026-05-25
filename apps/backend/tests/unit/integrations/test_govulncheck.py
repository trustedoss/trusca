"""govulncheck adapter unit tests (v2.3 r1).

govulncheck is a heavy Go binary; unit tests must NEVER spawn it. We cover:

  - Stream parsing: reachable (call-level trace) vs not-reachable (module/package
    trace only), GO-id → CVE/GHSA alias fan-out, "True wins over False".
  - Both stream shapes: newline-delimited AND concatenated objects.
  - Graceful skips (run_govulncheck returns empty + analysed=False, never raises):
    binary missing, not a Go module, timeout, real failure (non-zero + no JSON).
  - Successful run that found vulns (exit code 3) is treated as analysed.
  - Adversarial / malformed output (parametrized): broken JSON, trailing garbage,
    non-dict objects, huge output, null/odd id tokens, oversized id, alias bomb.
  - subprocess is always mocked; the real binary is never invoked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from integrations import govulncheck as gv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _osv(go_id: str, aliases: list[str] | None = None) -> dict[str, Any]:
    return {"osv": {"id": go_id, "aliases": aliases or []}}


def _finding(go_id: str, *, reachable: bool) -> dict[str, Any]:
    """A finding whose trace is call-level (reachable) or package-only."""
    if reachable:
        trace = [
            {"module": "example.com/app"},
            {"module": "github.com/vuln/pkg", "package": "pkg", "function": "Vulnerable"},
        ]
    else:
        trace = [
            {"module": "example.com/app"},
            {"module": "github.com/vuln/pkg", "package": "pkg"},
        ]
    return {"finding": {"osv": go_id, "trace": trace}}


def _stream(objs: list[dict[str, Any]], *, joiner: str = "\n") -> str:
    return joiner.join(json.dumps(o) for o in objs)


class _Proc:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    raises: BaseException | None = None,
) -> None:
    def _fake(*_a: Any, **_k: Any) -> _Proc:
        if raises is not None:
            raise raises
        return _Proc(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr("integrations.govulncheck.subprocess.run", _fake)


@pytest.fixture
def go_module(tmp_path: Path) -> Path:
    """A directory that looks like a Go module (has go.mod)."""
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _govulncheck_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend govulncheck is installed (overridden per-test where needed)."""
    monkeypatch.setattr(
        "integrations.govulncheck.shutil.which", lambda _: "/usr/local/bin/govulncheck"
    )


# ---------------------------------------------------------------------------
# Stream parsing — verdicts
# ---------------------------------------------------------------------------


def test_reachable_finding_marks_true(monkeypatch: pytest.MonkeyPatch, go_module: Path) -> None:
    stream = _stream(
        [
            _osv("GO-2023-1111", ["CVE-2023-1111", "GHSA-aaaa-bbbb-cccc"]),
            _finding("GO-2023-1111", reachable=True),
        ]
    )
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is True
    assert result.verdicts["GO-2023-1111"] is True
    # Verdict fanned out onto the aliases (DT findings key on CVE/GHSA).
    assert result.verdicts["CVE-2023-1111"] is True
    assert result.verdicts["GHSA-AAAA-BBBB-CCCC"] is True


def test_package_only_trace_marks_false(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    stream = _stream(
        [
            _osv("GO-2023-2222", ["CVE-2023-2222"]),
            _finding("GO-2023-2222", reachable=False),
        ]
    )
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is True
    assert result.verdicts["GO-2023-2222"] is False
    assert result.verdicts["CVE-2023-2222"] is False


def test_true_wins_over_false_for_same_osv(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    """Two findings for one OSV — a single reachable trace makes it reachable."""
    stream = _stream(
        [
            _osv("GO-2023-3333", ["CVE-2023-3333"]),
            _finding("GO-2023-3333", reachable=False),
            _finding("GO-2023-3333", reachable=True),
        ]
    )
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.verdicts["GO-2023-3333"] is True
    assert result.verdicts["CVE-2023-3333"] is True


def test_osv_record_without_finding_is_false(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    """An OSV present in the graph but with no finding trace → not reachable."""
    stream = _stream([_osv("GO-2023-4444", ["CVE-2023-4444"])])
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.verdicts["GO-2023-4444"] is False
    assert result.verdicts["CVE-2023-4444"] is False


def test_concatenated_stream_no_newlines(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    """Newer govulncheck emits objects with no separators — must still parse."""
    stream = _stream(
        [
            _osv("GO-2023-5555", ["CVE-2023-5555"]),
            _finding("GO-2023-5555", reachable=True),
        ],
        joiner="",
    )
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.verdicts["CVE-2023-5555"] is True


def test_clean_run_no_vulns_is_analysed_empty(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    """exit 0, only config/progress messages → analysed=True, empty verdicts."""
    stream = _stream(
        [{"config": {"go_version": "go1.22"}}, {"progress": {"message": "Scanning..."}}]
    )
    _patch_run(monkeypatch, stdout=stream, returncode=0)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is True
    assert result.verdicts == {}


# ---------------------------------------------------------------------------
# Graceful skips — never raises, returns analysed=False
# ---------------------------------------------------------------------------


def test_not_installed_returns_empty(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    monkeypatch.setattr("integrations.govulncheck.shutil.which", lambda _: None)

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is False
    assert result.verdicts == {}


def test_not_a_go_module_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No go.mod in tmp_path → skip before even checking the binary.
    result = gv.run_govulncheck(module_dir=tmp_path)

    assert result.analysed is False
    assert result.verdicts == {}


def test_timeout_returns_empty(monkeypatch: pytest.MonkeyPatch, go_module: Path) -> None:
    _patch_run(
        monkeypatch,
        raises=subprocess.TimeoutExpired(cmd="govulncheck", timeout=1),
    )

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is False
    assert result.verdicts == {}


def test_spawn_oserror_returns_empty(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    _patch_run(monkeypatch, raises=OSError("exec format error"))

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is False


def test_real_failure_nonzero_no_json_returns_empty(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    """A genuine failure (bad build) exits non-zero with no parseable stdout."""
    _patch_run(
        monkeypatch,
        stdout="",
        stderr="go: build failed",
        returncode=1,
    )

    result = gv.run_govulncheck(module_dir=go_module)

    assert result.analysed is False
    assert result.verdicts == {}


# ---------------------------------------------------------------------------
# Adversarial / malformed output (parametrized) — must never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("", id="empty"),
        pytest.param("not json at all", id="plain-text"),
        pytest.param("{ broken json", id="broken-open-brace"),
        pytest.param("[1, 2, 3]", id="json-array-not-objects"),
        pytest.param("null", id="bare-null"),
        pytest.param("12345", id="bare-number"),
        pytest.param('{"osv": "not-a-dict"}', id="osv-not-dict"),
        pytest.param('{"finding": []}', id="finding-not-dict"),
        pytest.param('{"unknown_key": {"x": 1}}', id="unknown-key"),
        pytest.param('{"osv": {"id": 999}}', id="osv-id-not-string"),
        pytest.param('{"finding": {"osv": null, "trace": []}}', id="finding-null-osv"),
        pytest.param('{"finding": {"osv": "GO-1", "trace": "bad"}}', id="trace-not-list"),
        pytest.param(
            '{"osv": {"id": "GO-1", "aliases": "not-a-list"}}', id="aliases-not-list"
        ),
        pytest.param('{"osv": {"id": "GO-1"}}\x00\x00trailing', id="null-bytes-tail"),
    ],
)
def test_adversarial_output_never_raises(
    monkeypatch: pytest.MonkeyPatch, go_module: Path, stdout: str
) -> None:
    _patch_run(monkeypatch, stdout=stdout, returncode=3)

    # The key contract: no exception escapes, we always get a ReachabilityResult.
    result = gv.run_govulncheck(module_dir=go_module)
    assert isinstance(result, gv.ReachabilityResult)
    # None of these adversarial blobs should yield a True verdict.
    assert not any(result.verdicts.values())


def test_oversized_id_token_is_dropped(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    huge_id = "GO-" + "9" * 5000
    stream = _stream(
        [
            {"osv": {"id": huge_id, "aliases": ["CVE-2023-9999"]}},
            {"finding": {"osv": huge_id, "trace": [{"function": "X"}]}},
        ]
    )
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    # The oversized id never becomes a key (length guard); no verdicts survive.
    assert huge_id.upper() not in result.verdicts
    assert "CVE-2023-9999" not in result.verdicts


def test_alias_bomb_is_capped(monkeypatch: pytest.MonkeyPatch, go_module: Path) -> None:
    aliases = [f"CVE-2023-{i:05d}" for i in range(5000)]
    stream = _stream(
        [
            {"osv": {"id": "GO-2023-BOMB", "aliases": aliases}},
            _finding("GO-2023-BOMB", reachable=True),
        ]
    )
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    # The GO-id verdict is still there; alias fan-out is bounded (per-OSV cap)
    # so the map cannot balloon from one record.
    assert result.verdicts["GO-2023-BOMB"] is True
    cve_keys = [k for k in result.verdicts if k.startswith("CVE-2023-")]
    assert len(cve_keys) <= gv._MAX_ALIASES_PER_OSV


def test_huge_output_is_truncated_not_oom(
    monkeypatch: pytest.MonkeyPatch, go_module: Path
) -> None:
    """A stdout over the byte cap is truncated before parsing (no OOM, no raise)."""
    monkeypatch.setenv("GOVULNCHECK_MAX_OUTPUT_BYTES", "200")
    valid = _stream([_osv("GO-2023-7777"), _finding("GO-2023-7777", reachable=True)])
    # Pad far beyond the 200-byte cap with junk so the slice triggers.
    stream = valid + ("x" * 10_000)
    _patch_run(monkeypatch, stdout=stream, returncode=3)

    result = gv.run_govulncheck(module_dir=go_module)

    assert isinstance(result, gv.ReachabilityResult)
    # We still parse the valid prefix that fits under the cap.
    assert result.analysed is True
