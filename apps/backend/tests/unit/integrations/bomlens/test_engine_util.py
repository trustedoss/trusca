# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The engine adapter's pure helpers (``integrations/bomlens/engine_util.py``).

The image reference the engine scans, the cleaned last lines of a container's
output, a proxy that names the worker itself and the age limit of the Trivy
database. They were part of TRUSCA's own runner, and so are their tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.bomlens.engine_util import (
    ERROR_TAIL_LINE_CHARS,
    ERROR_TAIL_LINES,
    OUTPUT_TAIL_LINE_CHARS,
    OUTPUT_TAIL_LINES,
    OUTPUT_TAIL_WINDOW_BYTES,
    TRIVY_DB_MAX_AGE_LIMIT_DAYS,
    BomLensEngineError,
    image_target,
    is_loopback_proxy,
    output_tail,
    trivy_db_max_age_days,
)


@pytest.mark.parametrize(
    "reference",
    [
        "alpine",
        "alpine:3.19.0",
        "library/alpine:3.19.0",
        "myorg/app",
        "docker.io/library/alpine:3.19.0",
        "ghcr.io/org/team/app:1.2.3",
        "registry.example:5000/app:v1_2-3",
        "localhost:5000/app",
        "localhost/app:1",
        "alpine@sha256:" + "a" * 64,
        "alpine:3.19.0@sha256:" + "0123456789abcdef" * 4,
        "my-org/my_app__x/sub.part:Tag_1.0-rc",
        # Valid image names that are also syft scheme words: the registry
        # scheme in front is what keeps syft from reading a folder, and makes
        # "registry:alpine" the image "registry" with the tag "alpine".
        "dir:x",
        "registry:alpine",
    ],
)
def test_a_registry_reference_is_handed_over_with_the_registry_scheme(reference: str) -> None:
    assert image_target(reference) == f"registry:{reference}"


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "   ",
        "docker-archive:/w/image.tar",
        "oci-archive:/w/image.tar",
        "oci-dir:/w/layout",
        "dir:/",
        "file:/etc/passwd",
        "docker://alpine",
        "https://registry.example/app:1",
        "user:token@registry.example/app:1",
        "registry.example/App:1",
        "MyOrg/app",
        "alpine:3.19 --privileged",
        "alpine\n:3.19",
        "-alpine",
        "alpine:-3",
        "alpine@sha256:short",
        "alpine@md5:" + "a" * 32,
        "../alpine",
        "alpine/",
        "a" * 600,
    ],
)
def test_anything_else_is_refused_without_echoing_it(reference: str) -> None:
    with pytest.raises(BomLensEngineError, match="not a registry image reference") as raised:
        image_target(reference)
    if reference.strip():
        assert reference not in str(raised.value)


@pytest.mark.parametrize(
    ("host", "internal"),
    [
        ("localhost", True),
        ("localhost:5000", True),
        ("LOCALHOST", True),
        ("registry.localhost", True),
        ("127.0.0.1", True),
        ("127.1:5000", True),
        ("0x7f000001", True),
        ("10.1.2.3:443", True),
        ("169.254.169.254", True),
        ("[::1]:5000", True),
        ("[fe80::1]", True),
        ("8.8.8.8", True),
        ("docker.io", False),
        ("ghcr.io", False),
        ("registry.example:5000", False),
        ("localhost.example.com", False),
        ("deadbeef.example", False),
        ("abc", False),
        ("", False),
    ],
)
def test_an_address_or_the_local_machine_is_an_internal_registry(
    host: str, internal: bool
) -> None:
    from tasks.scan_container_bomlens import is_internal_registry

    assert is_internal_registry(host) is internal


def test_surrounding_space_is_dropped_not_passed_on() -> None:
    assert image_target(" alpine:3.19 ") == "registry:alpine:3.19"


def test_the_tail_keeps_the_last_lines_and_cuts_each_one() -> None:
    output = b"".join(f"line {n}\n".encode() for n in range(50))
    assert output_tail(output, lines=3) == ["line 47", "line 48", "line 49"]
    # Under the pre-cut (4 x line_chars), cut to line_chars after cleaning.
    assert output_tail(b"a" * 30 + b" b\n", line_chars=10) == ["a" * 10]


def test_a_line_cut_before_cleaning_loses_the_word_the_cut_fell_in() -> None:
    """A credential split by the cut no longer matches a pattern, so the word
    goes with the cut instead of reaching the log half-redacted."""
    secret = "https://bot:" + "p" * 40 + "@registry.example"
    line = ("word " * 7 + secret).encode()
    # line_chars=10: the cut is at 40 characters, inside the credential.
    assert output_tail(line + b"\n", line_chars=10) == ["word word "]
    # One word longer than the cut: nothing of it is kept.
    assert output_tail(b"z" * 100 + b"\n", line_chars=10) == []


def test_the_first_line_of_a_longer_output_is_dropped_its_start_is_missing() -> None:
    window = OUTPUT_TAIL_WINDOW_BYTES
    head = b"bot:hidden-start-of-a-credential@registry.example\n"
    body = b"x" * (window - len(head) + 10)
    tail = output_tail(b"https://" + head + body + b"\nlast\n")
    assert tail == ["last"]


@pytest.mark.parametrize(
    ("assignment", "kept"),
    [
        ('err "password": "', 'err "password":'),
        # Gradle's credentials block: no "=" or ":", only the quotes.
        ('password "', "password"),
    ],
    ids=["json", "gradle-dsl"],
)
def test_a_quoted_value_the_cut_fell_in_is_dropped_from_its_opening_quote(
    assignment: str, kept: str
) -> None:
    """The default cut is at 1200 characters. Spaces before it fold away when
    the line is cleaned, so what was near the cut lands inside the stored 300."""
    limit = OUTPUT_TAIL_LINE_CHARS * 4
    pad = "pad" + " " * (limit - 40 - 3)
    secret = "hunter2-6f0d"
    line = pad + assignment + secret + " more " + "y" * 100 + '"'
    assert len(pad + assignment + secret) < limit < len(line)

    (cleaned,) = output_tail(line.encode() + b"\n")

    assert secret not in cleaned
    assert cleaned == f"pad {kept}"


def test_a_vertical_tab_does_not_part_a_key_from_its_value() -> None:
    """Lines are split on line feeds only, as the log readers split them."""
    tail = output_tail(b"password=\x0bsecret-vt-3a9e\nnext\x0cline\n")
    assert tail == ["password=***", "nextline"]


@pytest.mark.parametrize(
    ("hours", "days"),
    [
        (None, 16),
        ("168", 16),
        ("24", 4),
        ("25", 6),
        ("1", 4),
        ("0", 4),
        ("-48", 4),
        ("junk", 16),
        ("336", 30),
        ("337", 30),
        ("720", 30),
        # Past a float's range: integer arithmetic, no OverflowError.
        ("9" * 400, 30),
        # Past Python's int() digit limit: read as junk, the default.
        ("9" * 5000, 16),
    ],
)
def test_the_database_age_follows_the_refresh_interval(
    monkeypatch: pytest.MonkeyPatch, hours: str | None, days: int
) -> None:
    """ceil(TRIVY_DB_REFRESH_HOURS / 24) * 2 + 2, at most 30, read per call."""
    if hours is None:
        monkeypatch.delenv("TRIVY_DB_REFRESH_HOURS", raising=False)
    else:
        monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", hours)
    assert trivy_db_max_age_days() == days


@pytest.mark.parametrize(("hours", "capped"), [("336", False), ("337", True), ("9" * 400, True)])
def test_a_capped_database_age_is_logged(
    monkeypatch: pytest.MonkeyPatch, hours: str, capped: bool
) -> None:
    import structlog

    monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", hours)
    with structlog.testing.capture_logs() as captured:
        assert trivy_db_max_age_days() == TRIVY_DB_MAX_AGE_LIMIT_DAYS
    events = [e for e in captured if e["event"] == "bomlens_trivy_db_max_age_capped"]
    assert [e["log_level"] for e in events] == (["warning"] if capped else [])


@pytest.mark.parametrize(
    ("value", "loopback"),
    [
        ("http://127.0.0.1:3128", True),
        ("127.0.0.1:3128", True),
        ("http://127.8.9.10", True),
        ("http://user:secret@127.0.0.1:3128", True),
        ("http://localhost:3128", True),
        ("localhost", True),
        ("socks5://proxy.localhost:1080", True),
        ("http://[::1]:3128", True),
        (" http://LOCALHOST:3128 ", True),
        ("http://proxy.corp.example:8080", False),
        ("http://10.0.0.5:3128", False),
        ("http://localhost.corp.example:3128", False),
        ("http://127.0.0.1.nip.io:3128", False),
        ("http://[::ffff:10.0.0.1]:3128", False),
        ("http://[bad", False),
        ("", False),
        # A trailing dot, the legacy IPv4 spellings, the unspecified address.
        ("http://localhost.:3128", True),
        ("http://127.0.0.1.:3128", True),
        ("http://127.1:3128", True),
        ("http://2130706433:3128", True),
        ("http://0x7f000001:3128", True),
        ("http://0177.0.0.1:3128", True),
        ("http://0.0.0.0:3128", True),
        ("http://[::]:3128", True),
        ("http://[::ffff:127.0.0.1]:3128", True),
        ("http://0x0a000001:3128", False),
        ("http://167772161:3128", False),
        ("http://deadbeef:3128", False),
        ("http://proxy.corp.example.:3128", False),
    ],
)
def test_a_proxy_on_the_loopback_address_is_recognised(value: str, loopback: bool) -> None:
    assert is_loopback_proxy(value) is loopback


def test_an_ipv4_mapped_loopback_is_recognised_on_an_older_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before Python 3.13 (and some 3.12 patch releases), ``::ffff:127.0.0.1``
    did not read as loopback. The runner unwraps the IPv4 address itself, so
    the answer does not depend on the interpreter's patch level."""
    import ipaddress

    monkeypatch.setattr(
        ipaddress.IPv6Address,
        "is_loopback",
        property(lambda self: int(self) == 1),
    )
    assert not ipaddress.ip_address("::ffff:127.0.0.1").is_loopback
    assert is_loopback_proxy("http://[::ffff:127.0.0.1]:3128") is True
    assert is_loopback_proxy("http://[::ffff:10.0.0.1]:3128") is False


# ---------------------------------------------------------------------------
# The administrator guide says what the code does (hardening rule 4)
# ---------------------------------------------------------------------------

_DOCS = Path(__file__).resolve().parents[6] / "docs-site"
_GUIDES = (
    _DOCS / "docs" / "admin-guide" / "dynamic-scan-executor.md",
    _DOCS
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "admin-guide"
    / "dynamic-scan-executor.md",
)


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_quotes_the_private_registry_refusal_as_the_scan_reports_it(
    guide: Path,
) -> None:
    from tasks.scan_container_bomlens import credential_refusal, internal_registry_refusal

    text = guide.read_text(encoding="utf-8")
    assert credential_refusal("<host>") in text
    assert internal_registry_refusal("<host>") in text


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_gives_the_image_scan_settings_their_code_defaults(
    guide: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.config import bomlens_image_scan_timeout_seconds, scan_container_engine

    monkeypatch.delenv("SCAN_CONTAINER_ENGINE", raising=False)
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS", raising=False)
    rows = {
        line.split("|")[1].strip(): line.split("|")[2].strip()
        for line in guide.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `SCAN_")
    }
    assert rows["`SCAN_CONTAINER_ENGINE`"] == f"`{scan_container_engine()}`"
    assert rows["`SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS`"] == (
        f"`{bomlens_image_scan_timeout_seconds()}`"
    )


#: How each guide states the two output-tail limits.
_TAIL_LIMITS = {
    "en": (
        "up to {lines} lines of {chars} characters each",
        "up to {lines} lines of {chars} characters",
    ),
    "ko": ("최대 {lines}줄(줄마다 {chars}자)", "최대 {lines}줄(줄마다 {chars}자)"),
}


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_describes_a_run_without_a_result_as_the_scan_reports_it(guide: Path) -> None:
    from bomlens_runner import EngineWroteNoResult as PackageWroteNoResult

    from integrations.bomlens import engine

    output = engine._Output(None)
    output("trivy", "x")
    error = engine._failure(
        PackageWroteNoResult("MODE=IMAGE exited 1 without writing a result manifest"), output
    )
    # The scan's error as the task stores it, up to the output lines.
    prefix = "scan engine: " + str(error).removesuffix("x")

    text = guide.read_text(encoding="utf-8")
    assert f"`{prefix}... Permission denied`" in text
    assert "`bomlens_engine_no_result`" in text
    assert "`volume-nocopy`" in text
    short, full = _TAIL_LIMITS["ko" if "/i18n/ko/" in str(guide) else "en"]
    lines, chars = ERROR_TAIL_LINES, ERROR_TAIL_LINE_CHARS
    assert short.format(lines=lines, chars=chars) in text
    lines, chars = OUTPUT_TAIL_LINES, OUTPUT_TAIL_LINE_CHARS
    assert full.format(lines=lines, chars=chars) in text
