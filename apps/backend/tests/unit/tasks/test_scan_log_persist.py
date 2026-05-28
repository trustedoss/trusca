"""
Unit tests for the per-scan on-disk log persistence side of `tasks._progress`.

Covers (in order of risk):

  1. ``_scrub_secrets`` — the publisher-side credential redactor. Every line
     written to the durable, downloadable ``scan.log`` flows through this; a
     miss elevates a transient log echo into a long-lived credential leak
     (the security-reviewer's MEDIUM elevation when disk persistence shipped).
     Parametrized aggressively per ``feedback_adversarial_input_parametrize`` —
     untrusted tool stderr is exactly the kind of input that has to survive
     separator-only / oversized / CRLF / null-byte payloads.

  2. ``_append_log_line_to_disk`` — the disk-write happy path + budget + cap
     enforcement + fault tolerance. The reference contract is the docstring
     in `tasks/_progress.py` (format ``{ts} [{stage}/{stream}] {line}\\n``,
     shared per-scan budget with the Redis side, fire-and-forget IO errors).

  3. ``close_log_file`` / ``reset_log_counter`` — handle lifecycle. The
     ``acks_late`` redelivery scenario (retried task lands in the same worker
     process) MUST close the cached handle so the new run does not append
     to a file the workspace cleaner already rmtree'd.

The Redis side is covered separately by
``tests/unit/tasks/test_progress_publisher.py`` and shares the
``patched_publisher`` fixture pattern. We deliberately keep the persist-on /
persist-off knob local here because every test in this file wants disk
writes ON (the inverse of the Redis-only suite).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point ``WORKSPACE_HOST_PATH`` at an empty per-test directory.

    The publisher resolves the path via ``core.config.workspace_root()`` —
    which reads ``os.getenv`` at call time per CLAUDE.md core rule #11 — so a
    plain monkeypatch.setenv flips the destination cleanly. We yield the path
    so tests can assert on the resulting ``<root>/<scan_id>/scan.log`` file.

    Also ensures disk persistence is ON (the global Redis-only fixture in the
    sibling suite turns it off; we want the opposite default here).
    """
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    monkeypatch.setenv("SCAN_LOG_PERSIST_ENABLED", "true")
    yield tmp_path


@pytest.fixture
def disk_publisher(
    monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path
) -> Iterator[Any]:
    """Yield the `tasks._progress` module wired to a MagicMock Redis client.

    Mirrors ``patched_publisher`` from ``test_progress_publisher.py`` but
    leaves disk persistence ON (the whole point of this suite). The
    MagicMock collects every ``publish(channel, message)`` call so a test can
    cross-check the "budget applies to BOTH sides" contract without standing
    up fakeredis — fakeredis adds nothing here because we do not subscribe.
    """
    from tasks import _progress

    mock_client = MagicMock()
    monkeypatch.setattr(_progress, "_get_client", lambda: mock_client)
    # The mock client surfaces .publish_calls for ergonomic assertions.
    mock_client.publish_calls = mock_client.publish.call_args_list
    yield _progress
    # Close any per-test scan handles so the next test starts clean.
    # ``_log_files`` is module-private; we drain it via the public close API.
    for key in list(_progress._log_files.keys()):
        _progress.close_log_file(key)
    _progress.reset_publisher_for_tests()


def _read_log_file(workspace: Path, scan_id: str) -> str:
    """Return the on-disk ``scan.log`` content as a single decoded string."""
    path = workspace / scan_id / "scan.log"
    return path.read_text(encoding="utf-8")


# ===========================================================================
# Test_scrub_secrets — credential redactor (publisher-side)
# ===========================================================================


class Test_scrub_secrets:
    """The publisher-side credential redactor runs on every line BEFORE both
    Redis publish and disk write. A miss is a durable leak now that the
    output lands in a downloadable file. Parametrize aggressively.
    """

    @pytest.mark.parametrize(
        ("raw", "must_contain", "must_not_contain"),
        [
            # ---- HTTP Bearer (RFC 6750 token charset) ----
            (
                "Authorization: Bearer abc.def-ghi_jkl/MN+op=",
                "Bearer ***",
                "abc.def",
            ),
            # ---- Lowercase variant + leading whitespace ----
            ("    bearer xyz123token", "***", "xyz123token"),
            # ---- npm auth tokens (cdxgen debug mode) ----
            (
                "cdxgen npm_config__authToken=npm_secret_xyz123",
                "_authToken=***",
                "npm_secret_xyz123",
            ),
            # ---- _authToken= form with trailing newline ----
            (
                "_authToken=abc\n",
                "_authToken=***",
                "abc\n",
            ),
            # ---- _auth (legacy short form, colon separator) ----
            (
                "_auth: aGVsbG8K",
                "_auth: ***",
                "aGVsbG8K",
            ),
            # ---- URL userinfo (git clone over https) ----
            (
                "https://octocat:s3cret@github.com/repo.git",
                "https://***@github.com/repo.git",
                "s3cret",
            ),
            # ---- URL userinfo (ssh-style scheme) ----
            (
                "git+ssh://user:hunter2@host.example.com/x.git",
                "***@host.example.com",
                "hunter2",
            ),
            # ---- X-API-Key header (case-insensitive) ----
            (
                "X-API-Key: aaa-bbb-ccc",
                "X-API-Key: ***",
                "aaa-bbb-ccc",
            ),
            # ---- api_key= form ----
            (
                "api_key=mysecret",
                "api_key=***",
                "mysecret",
            ),
            # ---- api-key with hyphen ----
            (
                "api-key=topsecret123",
                "api-key=***",
                "topsecret123",
            ),
        ],
    )
    def test_scrubs_known_credential_shapes(
        self,
        raw: str,
        must_contain: str,
        must_not_contain: str,
    ) -> None:
        from tasks._progress import _scrub_secrets

        out = _scrub_secrets(raw)
        assert must_contain in out, (
            f"redaction marker missing — input={raw!r}, output={out!r}"
        )
        assert must_not_contain not in out, (
            f"raw secret survived — input={raw!r}, output={out!r}"
        )

    def test_clean_line_passes_through_byte_identical(self) -> None:
        """A line carrying no credentials must come out byte-for-byte unchanged.

        Otherwise the redactor would mangle legitimate tool output (think
        version strings that happen to contain ``=``).
        """
        from tasks._progress import _scrub_secrets

        # Mix of identifiers, version pins, JSON-y content, unicode — none of
        # which match a credential pattern.
        clean = 'cdxgen: resolved express@4.18.2 -> "license": "MIT" — done ✓'
        assert _scrub_secrets(clean) == clean

    def test_empty_string_returns_empty_string(self) -> None:
        from tasks._progress import _scrub_secrets

        assert _scrub_secrets("") == ""

    def test_null_bytes_survive(self) -> None:
        """Null bytes in tool stderr must not crash the regex engine."""
        from tasks._progress import _scrub_secrets

        # The redactor should not touch a non-credential null-bearing line.
        line = "x\x00y\x00z"
        assert _scrub_secrets(line) == line

    def test_crlf_survives(self) -> None:
        """Windows-style line endings must round-trip cleanly."""
        from tasks._progress import _scrub_secrets

        # The Redis line transport keeps newlines verbatim; the disk side
        # strips a single trailing \r\n inside ``_append_log_line_to_disk``.
        # The scrubber itself must not eat or mutate the CRLF.
        line = "log content here\r\n"
        assert _scrub_secrets(line) == line

    def test_does_not_crash_on_degenerate_url_userinfo(self) -> None:
        """Pathological URL userinfo (``user:pass@@@host``) must not crash."""
        from tasks._progress import _scrub_secrets

        # The first @ in the userinfo is greedy under our regex; the trailing
        # @@host becomes opaque path. We only assert the call completes and
        # returns a string — the security-reviewer comment in the source
        # already documents this is the chosen trade-off.
        result = _scrub_secrets("https://user:pass@@@host")
        assert isinstance(result, str)
        assert result != ""

    def test_separator_only_input_does_not_crash(self) -> None:
        """An input made of separators only (``::==://``) must not crash."""
        from tasks._progress import _scrub_secrets

        result = _scrub_secrets("::==://")
        assert isinstance(result, str)

    def test_no_dos_on_200kb_bearer_input(self) -> None:
        """A 200 KB payload must scrub in well under 100 ms (no regex DoS).

        The publisher truncates BEFORE scrubbing (``_truncate_line`` then
        ``_scrub_secrets`` in ``publish_log``), but we still want a hard
        floor on the scrubber's worst-case behavior in case a future caller
        ever forgets to truncate first.
        """
        from tasks._progress import _scrub_secrets

        # 200 KB ≈ deliberately huge; the regex must not catastrophically
        # backtrack on this. The actual production cap is ~2000 chars.
        payload = "Bearer " + ("A" * 200_000)
        start = time.perf_counter()
        out = _scrub_secrets(payload)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 100, (
            f"regex took {elapsed_ms:.1f}ms on 200KB input — possible "
            f"catastrophic backtracking"
        )
        # The scrub still triggers on a 200 KB Bearer token.
        assert "Bearer ***" in out
        assert "A" * 100 not in out  # the raw secret body must be gone

    def test_multiple_credentials_on_one_line_all_scrubbed(self) -> None:
        """A single line carrying TWO credentials must scrub both."""
        from tasks._progress import _scrub_secrets

        raw = "Authorization: Bearer tok1 X-API-Key: sec2"
        out = _scrub_secrets(raw)
        assert "tok1" not in out
        assert "sec2" not in out
        # Both replacement markers are present.
        assert "Bearer ***" in out
        assert "X-API-Key: ***" in out


# ===========================================================================
# Test_append_log_line_to_disk — the disk write side of ``publish_log``
# ===========================================================================


class Test_append_log_line_to_disk:
    """Pinning the on-disk persistence contract of ``publish_log``.

    The format is::

        {ISO8601_ts} [{stage}/{stream}] {line}\\n

    The cap, truncation, and persist toggle are shared with the Redis side
    (one shared budget, one shared truncated line) so a misconfigured
    operator cannot get a downloaded log that disagrees with the live WS
    frames.
    """

    def test_happy_path_three_lines_format_and_order(
        self, disk_publisher: Any, isolated_workspace: Path
    ) -> None:
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="resolving packages"
        )
        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stderr", line="warning: peer dep"
        )
        disk_publisher.publish_log(
            scan_id, stage="trivy", stream="stdout", line="2 vulnerabilities found"
        )

        body = _read_log_file(isolated_workspace, str(scan_id))
        lines = body.splitlines()
        assert len(lines) == 3, body

        # Order preserved.
        assert "resolving packages" in lines[0]
        assert "peer dep" in lines[1]
        assert "vulnerabilities found" in lines[2]

        # Format: {ts} [{stage}/{stream}] {line}
        # The trailing \n is per line (splitlines drops the terminator).
        assert "[cdxgen/stdout] resolving packages" in lines[0]
        assert "[cdxgen/stderr] warning: peer dep" in lines[1]
        assert "[trivy/stdout] 2 vulnerabilities found" in lines[2]

        # Each line starts with an ISO8601 timestamp (sanity — has 'T' + '-').
        for line in lines:
            head = line.split(" [", 1)[0]
            assert "T" in head and "-" in head, (
                f"line missing iso timestamp prefix: {line!r}"
            )

    def test_scrubbed_before_write(
        self, disk_publisher: Any, isolated_workspace: Path
    ) -> None:
        """Credentials are redacted BEFORE the line touches disk.

        The disk file is the most durable place a secret can land in this
        system — the test pins the security-reviewer's MEDIUM finding into
        a regression gate.
        """
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        disk_publisher.publish_log(
            scan_id,
            stage="cdxgen",
            stream="stdout",
            line="Authorization: Bearer leak_me_please",
        )

        body = _read_log_file(isolated_workspace, str(scan_id))
        assert "Bearer ***" in body
        assert "leak_me_please" not in body

    def test_cap_shared_with_redis_publish(
        self,
        disk_publisher: Any,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The per-scan cap suppresses BOTH the file write AND the Redis publish.

        Set ``SCAN_LOG_MAX_LINES_PER_SCAN=2`` and fire 5 calls — the file ends
        up with exactly 2 lines AND the Redis MagicMock saw exactly 2
        publishes. Anything else means the downloaded log can disagree with
        the live WS frames a user actually saw, breaking the "one budget"
        contract documented in ``publish_log``.
        """
        monkeypatch.setenv("SCAN_LOG_MAX_LINES_PER_SCAN", "2")
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        for i in range(5):
            disk_publisher.publish_log(
                scan_id, stage="cdxgen", stream="stdout", line=f"line {i}"
            )

        body = _read_log_file(isolated_workspace, str(scan_id))
        on_disk = body.splitlines()
        assert len(on_disk) == 2, body

        # Mock client recorded exactly the same number of publishes.
        mock_client = disk_publisher._get_client()
        assert mock_client.publish.call_count == 2

    def test_truncation_marker_lands_on_disk_too(
        self,
        disk_publisher: Any,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A line over the per-line cap is truncated with the marker before write."""
        monkeypatch.setenv("SCAN_LOG_LINE_MAX_LEN", "80")
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="x" * 5_000
        )

        body = _read_log_file(isolated_workspace, str(scan_id))
        # The truncation suffix lives in the FILE, not just on the Redis
        # frame; otherwise the downloaded log misleads the user about which
        # lines were truncated.
        assert "…(truncated)" in body
        # The single written line (with prefix + marker) is bounded — the
        # raw 5000-char payload is gone.
        assert "x" * 200 not in body

    def test_persist_disabled_creates_no_file(
        self,
        disk_publisher: Any,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``SCAN_LOG_PERSIST_ENABLED=false`` skips ALL disk writes.

        The Redis publish path must still fire — the kill-switch is for
        durability, not for live streaming.
        """
        monkeypatch.setenv("SCAN_LOG_PERSIST_ENABLED", "false")
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        for i in range(10):
            disk_publisher.publish_log(
                scan_id, stage="cdxgen", stream="stdout", line=f"line {i}"
            )

        # No file (and no parent directory) materialized.
        scan_dir = isolated_workspace / str(scan_id)
        log_path = scan_dir / "scan.log"
        assert not log_path.exists()

        # Redis publish ran 10 times — the live wire is unaffected by the
        # disk-side kill switch.
        mock_client = disk_publisher._get_client()
        assert mock_client.publish.call_count == 10

    def test_crash_in_file_open_does_not_raise(
        self,
        disk_publisher: Any,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An OSError in ``Path.open`` MUST be swallowed.

        The scan pipeline running for 30+ minutes cannot crash over a disk
        hiccup. Fire-and-forget is the documented contract.
        """
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        # Patch the builtin ``open`` used by ``_get_or_open_log_file`` to
        # raise. The redis MagicMock continues to record the publish so we
        # can assert the wire path survived.
        import builtins

        real_open = builtins.open

        def boom(*args: Any, **kwargs: Any) -> Any:
            # Only fail for the scan.log target — let everything else
            # (pytest internals, encoding tables) succeed.
            if args and isinstance(args[0], (str, Path)):
                if "scan.log" in str(args[0]):
                    raise OSError("simulated disk failure")
            return real_open(*args, **kwargs)

        monkeypatch.setattr(builtins, "open", boom)

        # No exception escapes.
        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="hello"
        )

        # The Redis publish still fired — the wire path is independent.
        mock_client = disk_publisher._get_client()
        assert mock_client.publish.call_count == 1


# ===========================================================================
# Test_close_log_file — handle lifecycle
# ===========================================================================


class Test_close_log_file:
    """Lifecycle of the cached per-scan file handle.

    The handle is opened lazily on first ``publish_log`` and is meant to
    survive for the duration of the scan (~30-60 min). The scan task's
    ``finally`` block closes it; ``reset_log_counter`` ALSO closes it so a
    Celery ``acks_late`` redelivery into the same worker process opens a
    fresh handle rather than appending to a file the workspace cleaner may
    have already rmtree'd.
    """

    def test_open_then_close_evicts_from_cache(
        self, disk_publisher: Any, isolated_workspace: Path
    ) -> None:
        scan_id = uuid.uuid4()
        key = str(scan_id)
        disk_publisher.reset_log_counter(scan_id)

        # First publish opens the handle.
        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="first"
        )
        assert key in disk_publisher._log_files

        # Close evicts it.
        disk_publisher.close_log_file(scan_id)
        assert key not in disk_publisher._log_files

    def test_close_on_missing_key_is_noop(self, disk_publisher: Any) -> None:
        """Idempotent — calling close for a scan that never published is a no-op."""
        # No exception, no state change.
        disk_publisher.close_log_file(uuid.uuid4())
        disk_publisher.close_log_file("never-existed")

    def test_close_then_reopen_appends_does_not_truncate(
        self, disk_publisher: Any, isolated_workspace: Path
    ) -> None:
        """A close + subsequent publish APPENDS to the existing file."""
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="first"
        )
        disk_publisher.close_log_file(scan_id)

        # Reset just the counter (not the handle yet — we want to verify the
        # re-open path that happens transparently on next publish).
        # Note: ``reset_log_counter`` ALSO closes; here we just publish, which
        # exercises the lazy re-open in ``_get_or_open_log_file``.
        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="second"
        )

        body = _read_log_file(isolated_workspace, str(scan_id))
        lines = body.splitlines()
        assert len(lines) == 2
        assert "first" in lines[0]
        assert "second" in lines[1]

    def test_reset_log_counter_also_closes_handle(
        self, disk_publisher: Any, isolated_workspace: Path
    ) -> None:
        """Regression test for the acks_late redelivery scenario.

        Celery can redeliver a task to the same worker process. Without the
        ``close_log_file`` call inside ``reset_log_counter``, the second run
        would keep writing to a handle whose underlying file the workspace
        cleaner may have rmtree'd between runs (resulting in either silent
        writes-to-nowhere or a stale-FD EBADF cascade).
        """
        scan_id = uuid.uuid4()
        key = str(scan_id)
        disk_publisher.reset_log_counter(scan_id)

        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="first"
        )
        assert key in disk_publisher._log_files

        # Reset MUST close the handle, not just zero the counter.
        disk_publisher.reset_log_counter(scan_id)
        assert key not in disk_publisher._log_files

    def test_reset_clears_budget_so_more_lines_can_publish(
        self,
        disk_publisher: Any,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After a reset the per-scan cap counter is back to zero on disk too."""
        monkeypatch.setenv("SCAN_LOG_MAX_LINES_PER_SCAN", "1")
        scan_id = uuid.uuid4()
        disk_publisher.reset_log_counter(scan_id)

        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="run1"
        )
        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="run1-overflow"
        )
        # Only one line survived the cap.
        body = _read_log_file(isolated_workspace, str(scan_id))
        assert len(body.splitlines()) == 1

        # Reset frees the budget AND closes + reopens the handle on next write.
        disk_publisher.reset_log_counter(scan_id)
        disk_publisher.publish_log(
            scan_id, stage="cdxgen", stream="stdout", line="run2"
        )

        body = _read_log_file(isolated_workspace, str(scan_id))
        lines = body.splitlines()
        # run1 line is still there (we never truncated the file); run2
        # appended on top with its own budget.
        assert len(lines) == 2
        assert "run1" in lines[0]
        assert "run2" in lines[1]
