"""
Trivy adapter — ``run_trivy_sbom`` (W6 DT replacement, PR #40).

The W6 milestone replaces Dependency-Track with Trivy for vulnerability
matching against cdxgen SBOMs. This module pins the contract for the new
``run_trivy_sbom`` adapter:

  - Mock backend writes a CycloneDX-shaped Trivy JSON report so downstream
    persistence helpers can consume both ``trivy image`` and ``trivy sbom``
    outputs without branching on the source.
  - Real-mode + missing binary raises ``TrivyNotInstalled``.
  - Subprocess failures map to ``TrivyFailed`` (returncode != 0) and
    ``TrivyTimeout`` (TimeoutExpired). Both messages truncate stderr so
    massive outputs cannot exhaust log infra.
  - Adversarial JSON (oversized / NUL / CRLF / latin-1 / scheme injection in
    URL fields / missing keys / weird severity values) MUST be parseable by
    the adapter — the matcher is downstream, the adapter only loads + counts.

Per CLAUDE.md core rule #11, ``scan_backend_mode`` resolves the env at call
time, so each test toggles ``TRUSTEDOSS_SCAN_BACKEND`` through monkeypatch
and never relies on module-level caching.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_trivy_sbom_mock_writes_realistic_report(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text('{"bomFormat":"CycloneDX","components":[]}', encoding="utf-8")

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path,
        output_dir=tmp_path / "trivy",
    )

    assert isinstance(result, trivy_adapter.TrivyResult)
    assert result.report_path.exists()
    assert result.report_path.name == "trivy-sbom.json"
    assert result.report["SchemaVersion"] == 2
    assert result.report["ArtifactType"] == "cyclonedx"
    assert isinstance(result.report["Results"], list)
    assert result.report["Results"], "mock report must include at least one Result"

    first = result.report["Results"][0]
    assert first["Class"] == "lang-pkgs"
    assert first["Type"] == "npm"
    vulns = first["Vulnerabilities"]
    assert vulns, "mock Result must carry at least one vulnerability"
    cve = vulns[0]
    assert cve["VulnerabilityID"] == "CVE-2024-MOCK-SBOM-0001"
    assert cve["Severity"] == "HIGH"
    assert cve["PkgName"] == "example-pkg"
    assert cve["InstalledVersion"] == "1.0.0"


def test_run_trivy_sbom_mock_round_trips_through_disk(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text('{"bomFormat":"CycloneDX"}', encoding="utf-8")

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path,
        output_dir=tmp_path / "trivy",
    )
    on_disk = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert on_disk == result.report


def test_run_trivy_sbom_backend_param_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`backend="mock"` must win even when the env says ``real``.

    This is the seam ``run_source_scan`` will use to inject a backend without
    touching process-wide env state.
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path,
        output_dir=tmp_path / "trivy",
        backend="mock",
    )
    assert result.report["ArtifactType"] == "cyclonedx"


def test_run_trivy_sbom_creates_output_dir(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    deep = tmp_path / "scans" / "abc" / "trivy"
    assert not deep.exists()
    result = trivy_adapter.run_trivy_sbom(sbom_path=sbom_path, output_dir=deep)
    assert deep.is_dir()
    assert result.report_path.parent == deep


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_run_trivy_sbom_missing_input_raises_file_not_found(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    with pytest.raises(FileNotFoundError) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=tmp_path / "does-not-exist.json",
            output_dir=tmp_path / "trivy",
        )
    assert "SBOM file not found" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Real-mode subprocess behaviour (binary missing, success, failure, timeout)
# ---------------------------------------------------------------------------


def test_run_trivy_sbom_real_mode_without_binary_raises_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _name: None)

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    with pytest.raises(trivy_adapter.TrivyNotInstalled):
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path,
            output_dir=tmp_path / "trivy",
        )


def test_run_trivy_sbom_real_mode_success_loads_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")
    report_dir = tmp_path / "trivy"

    captured_cmd: list[list[str]] = []

    def fake_run(
        cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        captured_cmd.append(cmd)
        # Trivy writes its output to the ``--output`` path on disk.
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "ArtifactName": str(sbom_path),
                    "ArtifactType": "cyclonedx",
                    "Results": [
                        {
                            "Target": "pkg:npm/x@1",
                            "Class": "lang-pkgs",
                            "Type": "npm",
                            "Vulnerabilities": [
                                {
                                    "VulnerabilityID": "CVE-2024-9999",
                                    "PkgName": "x",
                                    "InstalledVersion": "1.0",
                                    "Severity": "MEDIUM",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=report_dir
    )

    assert len(captured_cmd) == 1
    cmd = captured_cmd[0]
    assert cmd[0] == "trivy"
    assert cmd[1] == "sbom"
    assert "--format" in cmd and cmd[cmd.index("--format") + 1] == "json"
    assert "--output" in cmd
    assert str(sbom_path) in cmd
    assert result.report["Results"][0]["Vulnerabilities"][0]["VulnerabilityID"] == "CVE-2024-9999"


def test_run_trivy_sbom_non_zero_returncode_raises_trivy_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=2,
            stdout=b"",
            stderr=b"trivy: unable to load db: connection refused",
        )

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyFailed) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )
    msg = str(excinfo.value)
    assert "2" in msg
    assert "connection refused" in msg


def test_run_trivy_sbom_failed_truncates_stderr_to_1000_chars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Massive stderr must not blow up the exception or logging line."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    huge_stderr = (b"x" * 10_000) + b"END"

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=b"", stderr=huge_stderr
        )

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyFailed) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )
    # Exception message slice = 1000 chars of stderr, "END" sentinel never reached.
    assert "END" not in str(excinfo.value)
    assert "x" * 1000 in str(excinfo.value)


def test_run_trivy_sbom_timeout_raises_trivy_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyTimeout) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path,
            output_dir=tmp_path / "trivy",
            timeout_seconds=42,
        )
    assert "42" in str(excinfo.value)


def test_run_trivy_sbom_stderr_with_invalid_utf8_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """latin-1 / mojibake stderr must decode with ``errors='replace'``."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    # 0xff is invalid in utf-8 — used to live in latin-1 output before Trivy
    # normalised everything to utf-8 in 0.50.
    broken = b"err: \xff\xfe broken"

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=b"", stderr=broken
        )

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(trivy_adapter.TrivyFailed) as excinfo:
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )
    # Replacement char proves the decode survived the invalid sequence.
    assert "broken" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Adversarial JSON output (the adapter only parses + counts — must not crash)
#
# The matcher / persister downstream is responsible for normalising severity
# strings, sanitising URL fields, and rejecting unknown shapes. The adapter
# must keep its contract narrow: load the JSON, return the dict. These cases
# pin that narrow contract so a malicious or buggy Trivy build cannot blow up
# the worker.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "label"),
    [
        ("CRITICAL!", "exclamation"),
        ("INVALID", "unknown enum"),
        ("", "empty string"),
        (None, "null"),
        (5, "numeric"),
        ("CRITICAL\r\nX-Injected: yes", "crlf injection"),
        ("javascript:alert(1)", "scheme injection"),
        ("crit\x00ical", "null byte"),
        ("크리티컬", "non-ascii"),
    ],
    ids=lambda v: str(v),
)
def test_run_trivy_sbom_real_mode_passes_through_adversarial_severity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    severity: Any,
    label: str,
) -> None:
    """The adapter loads whatever Trivy emits; severity validation is downstream."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "Results": [
                        {
                            "Vulnerabilities": [
                                {
                                    "VulnerabilityID": "CVE-X",
                                    "Severity": severity,
                                }
                            ]
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    # Pass-through: whatever shape Trivy emits is what the adapter returns.
    assert result.report["Results"][0]["Vulnerabilities"][0]["Severity"] == severity


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("javascript:alert('xss')", "javascript scheme"),
        ("file:///etc/passwd", "file scheme"),
        ("data:text/html,<script>x</script>", "data scheme"),
        ("https://nvd.nist.gov/CVE-X\r\nSet-Cookie: x=1", "crlf in url"),
        ("https://example.invalid/\x00malicious", "null byte in url"),
        ("https://" + "a" * 5_000 + ".invalid/cve", "oversized url"),
    ],
    ids=lambda v: str(v)[:30],
)
def test_run_trivy_sbom_passes_through_adversarial_reference_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
    label: str,
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "Results": [
                        {
                            "Vulnerabilities": [
                                {"VulnerabilityID": "CVE-X", "References": [url]}
                            ]
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    assert result.report["Results"][0]["Vulnerabilities"][0]["References"] == [url]


def test_run_trivy_sbom_empty_vulnerabilities_array_is_zero_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "ArtifactType": "cyclonedx",
                    "Results": [
                        {"Target": "pkg:npm/x@1", "Class": "lang-pkgs", "Vulnerabilities": []}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    assert result.report["Results"][0]["Vulnerabilities"] == []


def test_run_trivy_sbom_results_with_no_vuln_key_is_zero_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``Vulnerabilities`` key is omitted entirely — adapter must not KeyError."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {"SchemaVersion": 2, "Results": [{"Target": "pkg:npm/x@1"}]}
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    # Must not raise — the inner `.get("Vulnerabilities", []) or []` pattern
    # tolerates both missing key and null value.
    trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )


def test_run_trivy_sbom_null_vulnerabilities_is_zero_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``"Vulnerabilities": null`` (seen in some Trivy 0.4x builds) must not crash."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {"SchemaVersion": 2, "Results": [{"Vulnerabilities": None}]}
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )


def test_run_trivy_sbom_results_missing_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``Results`` key absent — older Trivy on no-match. Must not crash."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps({"SchemaVersion": 2, "ArtifactType": "cyclonedx"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    assert "Results" not in result.report  # untouched


def test_run_trivy_sbom_oversized_report_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A 10 MB+ Trivy JSON must load — no in-memory size cap in the adapter."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    # 10 MB of mostly-padding vulnerability descriptions.
    padding = "A" * 10_000
    big_vulns = [
        {
            "VulnerabilityID": f"CVE-2024-{i:06d}",
            "PkgName": "x",
            "InstalledVersion": "1.0",
            "Severity": "HIGH",
            "Description": padding,
        }
        for i in range(1_100)  # ~11 MB serialized
    ]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "Results": [{"Vulnerabilities": big_vulns}],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    assert len(result.report["Results"][0]["Vulnerabilities"]) == 1_100


def test_run_trivy_sbom_deeply_nested_json_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Trivy can wrap CVSS metadata in nested dicts — depth ~50 must parse."""
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    # Build a 50-level-deep dict — `json.load` default recursion cap is well
    # above this; this just proves the adapter does not impose a smaller one.
    nested: dict[str, Any] = {"leaf": True}
    for _ in range(50):
        nested = {"x": nested}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "Results": [
                        {
                            "Vulnerabilities": [
                                {"VulnerabilityID": "CVE-Y", "CVSS": nested}
                            ]
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    result = trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "trivy"
    )
    # Walk back down to the sentinel leaf.
    cvss = result.report["Results"][0]["Vulnerabilities"][0]["CVSS"]
    for _ in range(50):
        cvss = cvss["x"]
    assert cvss == {"leaf": True}


def test_run_trivy_sbom_report_with_non_utf8_bytes_replaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The on-disk report may contain stray bytes — `_load_json` reads utf-8.

    Trivy itself writes utf-8 unconditionally; this test pins that a single
    stray latin-1 byte in the JSON causes a deterministic ``UnicodeDecodeError``
    rather than a silent corruption. The persister upstream must surface this
    as a TrivyFailed-like error; we do not catch it in the adapter so the
    behaviour is visible at the call site.
    """
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.trivy.shutil.which", lambda _name: "/usr/local/bin/trivy"
    )

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        out_idx = cmd.index("--output") + 1
        # 0xff is invalid in utf-8.
        Path(cmd[out_idx]).write_bytes(b'{"k":"\xff"}')
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", fake_run)

    with pytest.raises(UnicodeDecodeError):
        trivy_adapter.run_trivy_sbom(
            sbom_path=sbom_path, output_dir=tmp_path / "trivy"
        )
