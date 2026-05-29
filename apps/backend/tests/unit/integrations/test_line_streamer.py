"""Unit tests for ``integrations._line_streamer`` — P2 #8c.

These tests drive the line-streaming subprocess helper end-to-end with real
``Popen`` invocations of well-known shells (``sh -c '<script>'``) so we can
pin the contract without inventing a mock subprocess. Every test uses ``sh
-c`` rather than ``echo`` directly so the same script works on macOS + Linux
test runners.

Coverage targets:
  - No callback → falls back to ``subprocess.run`` contract (return code +
    captured stdout/stderr) unchanged.
  - With callback → every emitted line lands on the callback for the right
    stream, in order.
  - Callback exceptions never break the drain (the next line still arrives).
  - Subprocess timeout maps to ``TimeoutExpired`` and the partial captured
    output is preserved on the exception.
  - Non-zero exit code is propagated faithfully.
  - Blank lines are dropped (a bare ``\\n`` is not useful telemetry).
"""

from __future__ import annotations

import subprocess
import threading

import pytest

from integrations._line_streamer import run_with_line_streaming

# ---------------------------------------------------------------------------
# Fallback path — no callback
# ---------------------------------------------------------------------------


def test_run_without_callback_falls_back_to_subprocess_run() -> None:
    """The no-callback path returns a CompletedProcess identical to subprocess.run."""
    completed = run_with_line_streaming(
        ["sh", "-c", "printf 'one\\ntwo\\n'; printf 'err\\n' >&2; exit 3"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=None,
        stage="unit",
    )
    assert completed.returncode == 3
    assert completed.stdout == b"one\ntwo\n"
    assert completed.stderr == b"err\n"


# ---------------------------------------------------------------------------
# Streaming path — callback receives lines as they arrive
# ---------------------------------------------------------------------------


def test_streaming_callback_receives_stdout_lines_in_order() -> None:
    received: list[tuple[str, str]] = []
    lock = threading.Lock()

    def cb(stream: str, line: str) -> None:
        with lock:
            received.append((stream, line))

    completed = run_with_line_streaming(
        ["sh", "-c", "printf 'alpha\\nbeta\\ngamma\\n'"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=cb,
        stage="unit",
    )
    assert completed.returncode == 0
    # All three lines arrived on stdout. Order across the two pipes is not
    # guaranteed (the two drain threads run concurrently), but within a
    # single stream the order is preserved by the OS.
    stdout_lines = [line for stream, line in received if stream == "stdout"]
    assert stdout_lines == ["alpha", "beta", "gamma"]
    # Captured bytes still reflect every line for downstream inspection.
    assert completed.stdout == b"alpha\nbeta\ngamma\n"


def test_streaming_callback_distinguishes_stdout_and_stderr() -> None:
    received: list[tuple[str, str]] = []
    lock = threading.Lock()

    def cb(stream: str, line: str) -> None:
        with lock:
            received.append((stream, line))

    completed = run_with_line_streaming(
        ["sh", "-c", "printf 'out1\\n'; printf 'err1\\n' >&2; printf 'out2\\n'"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=cb,
        stage="unit",
    )
    assert completed.returncode == 0
    stdout_lines = sorted(line for stream, line in received if stream == "stdout")
    stderr_lines = sorted(line for stream, line in received if stream == "stderr")
    assert stdout_lines == ["out1", "out2"]
    assert stderr_lines == ["err1"]


def test_streaming_drops_empty_lines() -> None:
    """A bare ``\\n`` produces no callback invocation (not useful telemetry)."""
    received: list[tuple[str, str]] = []

    def cb(stream: str, line: str) -> None:
        received.append((stream, line))

    completed = run_with_line_streaming(
        ["sh", "-c", "printf 'a\\n\\nb\\n\\n\\n'"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=cb,
        stage="unit",
    )
    assert completed.returncode == 0
    lines = [line for _, line in received]
    assert lines == ["a", "b"]


def test_streaming_callback_exception_does_not_stop_drain() -> None:
    """A callback that raises is swallowed; subsequent lines still arrive."""
    received: list[str] = []

    def cb(stream: str, line: str) -> None:
        if line == "boom":
            raise RuntimeError("simulated callback failure")
        received.append(line)

    completed = run_with_line_streaming(
        ["sh", "-c", "printf 'pre\\nboom\\npost\\n'"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=cb,
        stage="unit",
    )
    assert completed.returncode == 0
    assert received == ["pre", "post"]


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


def test_streaming_timeout_raises_with_partial_output() -> None:
    """Timeout kills the subprocess and re-raises with already-drained bytes."""
    received: list[str] = []

    def cb(stream: str, line: str) -> None:
        received.append(line)

    # Emit one line, then sleep past the timeout.
    cmd = ["sh", "-c", "printf 'first\\n'; sleep 5"]
    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_with_line_streaming(
            cmd,
            timeout_seconds=1,
            cwd=None,
            env=None,
            line_callback=cb,
            stage="unit",
        )
    # Partial captured output (the line that already arrived) is preserved on
    # the exception so the caller can include it in the error text.
    err = exc_info.value
    assert err.stdout is not None
    stdout_bytes: bytes
    if isinstance(err.stdout, bytes):
        stdout_bytes = err.stdout
    else:
        # TimeoutExpired.stdout is typed Union[bytes, str, None] in stubs;
        # our streaming path always returns bytes, but the fallback for a
        # text-mode caller would be a str. Normalise for the assertion.
        stdout_bytes = str(err.stdout).encode("utf-8")
    assert b"first" in stdout_bytes
    # The callback ran for the line that arrived before the timeout.
    assert "first" in received


def test_no_callback_timeout_still_raises() -> None:
    """The fallback path also honours the timeout — no callback, same contract."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_line_streaming(
            ["sh", "-c", "sleep 5"],
            timeout_seconds=1,
            cwd=None,
            env=None,
            line_callback=None,
            stage="unit",
        )


# ---------------------------------------------------------------------------
# Non-zero exit
# ---------------------------------------------------------------------------


def test_nonzero_exit_is_propagated_under_streaming() -> None:
    completed = run_with_line_streaming(
        ["sh", "-c", "printf 'late\\n' >&2; exit 7"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=lambda *_: None,
        stage="unit",
    )
    assert completed.returncode == 7
    assert completed.stderr == b"late\n"


# ---------------------------------------------------------------------------
# Utf-8 / invalid bytes
# ---------------------------------------------------------------------------


def test_streaming_handles_invalid_utf8_via_replacement() -> None:
    """A line with invalid UTF-8 is delivered with U+FFFD replacement, not
    a crash."""
    received: list[str] = []

    def cb(stream: str, line: str) -> None:
        received.append(line)

    # `printf '\\xff\\n'` emits a single 0xFF byte then a newline — invalid
    # UTF-8 on its own.
    completed = run_with_line_streaming(
        ["sh", "-c", "printf '\\xff\\n'; printf 'ok\\n'"],
        timeout_seconds=10,
        cwd=None,
        env=None,
        line_callback=cb,
        stage="unit",
    )
    assert completed.returncode == 0
    # Both lines were delivered. The replacement char (U+FFFD) is acceptable;
    # the asserting requirement is only that the second line still arrived.
    assert "ok" in received
    assert len(received) == 2


def test_streaming_caps_in_memory_capture_but_streams_every_line() -> None:
    """feat/scan-log-verbosity (security-reviewer MEDIUM): a verbose subprocess
    that emits more than _MAX_CAPTURED_BYTES_PER_STREAM must NOT accumulate
    unbounded worker memory in the returned CompletedProcess, yet every line
    must still reach the live callback (the per-scan publish budget caps that
    path separately)."""
    from integrations._line_streamer import _MAX_CAPTURED_BYTES_PER_STREAM

    count = 0
    lock = threading.Lock()

    def cb(_stream: str, _line: str) -> None:
        nonlocal count
        with lock:
            count += 1

    # Emit ~50k lines of ~37 bytes each ≈ 1.85 MiB > the 1 MiB cap.
    line = "a" * 35
    n = 50000
    completed = run_with_line_streaming(
        ["sh", "-c", f"yes '{line}' | head -n {n}"],
        timeout_seconds=30,
        cwd=None,
        env=None,
        line_callback=cb,
        stage="unit",
    )

    # Live stream is uncapped — the callback saw every line.
    assert count == n
    # Retained capture is bounded (cap + at most one trailing line).
    assert len(completed.stdout) <= _MAX_CAPTURED_BYTES_PER_STREAM + len(line) + 8
    # The head of the capture (what callers slice for error messages) survives.
    assert completed.stdout.startswith(line.encode())
