"""Subprocess line-streaming helper — P2 #8c.

cdxgen / scancode (and friends) run for minutes; the scan WebSocket panel
wants to render their stdout / stderr as it happens instead of waiting for the
process to exit. ``subprocess.run(..., capture_output=True)`` materialises the
full output in one shot and is therefore unsuitable.

This helper wraps :class:`subprocess.Popen` with two drain threads (one per
stream) that decode line-by-line and forward each line to an optional
``line_callback``. The full output is also captured into ``stdout`` /
``stderr`` bytes on the returned :class:`subprocess.CompletedProcess` so the
caller's existing return-code / stderr handling continues to work unchanged.

Why threads (instead of ``selectors`` / ``asyncio``)
-----------------------------------------------------
Celery tasks run on a sync worker. ``Popen.communicate(timeout=...)`` is sync
already, and a pair of daemon threads draining ``proc.stdout`` /
``proc.stderr`` line-by-line is the most defensive (no buffering surprises),
portable (Windows + Linux), and well-understood pattern. ``selectors`` would
work too but adds non-trivial nonblocking I/O handling for marginal benefit.

Timeout handling
----------------
We use ``proc.wait(timeout=...)`` rather than ``communicate(timeout=...)`` so
the drain threads stay in charge of reading the pipes. On timeout we kill the
process, join the drain threads (with a short bound), and re-raise
:class:`subprocess.TimeoutExpired` — exactly what the caller already handles.

Callback safety
---------------
``line_callback`` is invoked from a background drain thread. A callback that
raises is caught and logged at WARNING; it does NOT stop the drain. A callback
that blocks for a long time would back-pressure the pipe and could in theory
stall the subprocess once the kernel pipe buffer fills (~64 KiB on Linux) —
the WebSocket publisher is fast (Redis publish), so this is a theoretical
concern, but documenting it here keeps future callbacks honest.
"""

from __future__ import annotations

import subprocess  # noqa: S404 — running vetted local binaries, args are fixed lists
import threading
from collections.abc import Callable
from typing import IO

import structlog

log = structlog.get_logger("integrations.line_streamer")

# Mirrors the public ``LineCallback`` type aliases in cdxgen / scancode; we
# keep an independent alias here so this module has no circular import.
LineCallback = Callable[[str, str], None]


def _drain(
    stream: IO[bytes],
    *,
    stream_name: str,
    captured: list[bytes],
    callback: LineCallback | None,
    stage: str,
) -> None:
    """Read ``stream`` line-by-line, fan out to ``captured`` + ``callback``.

    ``stream`` is opened in binary mode (Popen default with ``encoding=None``);
    we decode each line lazily so a corrupt byte sequence is replaced with
    U+FFFD rather than crashing the drain. The trailing newline is stripped
    before the callback runs — consumers want the text, not the line ending.
    """
    try:
        for raw in iter(stream.readline, b""):
            captured.append(raw)
            if callback is None:
                continue
            try:
                # Strip the trailing CR/LF before handing to the callback. We
                # decode with errors="replace" so a single bad byte (often
                # seen in JVM/Gradle output that mixes log encodings) never
                # crashes the drain.
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                # An empty line (the subprocess emitted a bare ``\n``) is
                # not useful telemetry; drop it instead of paying for a
                # publish round-trip.
                if text == "":
                    continue
                callback(stream_name, text)
            except Exception as exc:  # noqa: BLE001 — drain must keep running
                log.warning(
                    "scan_log_callback_failed",
                    stage=stage,
                    stream=stream_name,
                    error=str(exc)[:300],
                )
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001, S110 — best-effort close on teardown
            # We are inside a daemon drain thread that is about to exit; a
            # close() failure (already-closed pipe, EBADF on subprocess kill)
            # has no remediation other than letting the FD finalize naturally.
            # No logging here: a forced kill in the timeout path always trips
            # this branch and the structured log line would only be noise.
            pass


def run_with_line_streaming(
    cmd: list[str],
    *,
    timeout_seconds: int,
    cwd: str | None,
    env: dict[str, str] | None,
    line_callback: LineCallback | None,
    stage: str,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``cmd`` with optional line-by-line streaming of stdout + stderr.

    The returned :class:`subprocess.CompletedProcess` carries the FULL captured
    bytes on ``.stdout`` / ``.stderr`` so the caller's existing post-exit
    inspection (return code, stderr decode, etc.) keeps working unchanged.

    When ``line_callback`` is ``None`` we fall back to the simple
    ``subprocess.run`` path — same observable behaviour as before P2 #8c,
    same timeout semantics, no extra threads. This keeps the streaming
    overhead opt-in for callers that don't need it (e.g. tests that capture
    output for assertions).

    Raises:
        subprocess.TimeoutExpired: the process did not exit within
            ``timeout_seconds``. Already-drained bytes ARE preserved on the
            exception (via stdout/stderr attributes) so the caller's error
            message can include partial output.
    """
    if line_callback is None:
        # Fast path: no streaming, no extra threads — preserves the exact
        # behaviour every existing caller relied on before P2 #8c. The
        # capture_output kwarg sets stdout=PIPE, stderr=PIPE, which keeps
        # the existing error-text inspection working unchanged.
        return subprocess.run(  # noqa: S603 — args are a fixed list, no shell
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            cwd=cwd,
            env=env,
        )

    # Streaming path: Popen + two drain threads.
    #
    # bufsize=0 (unbuffered) — Python warns on bufsize=1 in binary mode (which
    # is what we get without text=True). The drain threads call ``readline``
    # which already handles line framing on its own; what we actually want is
    # for the OS pipe to deliver bytes as soon as possible. Unbuffered (0) is
    # the closest equivalent and avoids the RuntimeWarning.
    proc = subprocess.Popen(  # noqa: S603 — args are a fixed list, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        bufsize=0,
    )

    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []

    # The Popen stdout / stderr attributes are typed as Optional[IO[bytes]]
    # in stdlib stubs; with the kwargs above they are guaranteed non-None.
    assert proc.stdout is not None  # noqa: S101 — Popen kwargs guarantee non-None
    assert proc.stderr is not None  # noqa: S101

    stdout_thread = threading.Thread(
        target=_drain,
        kwargs={
            "stream": proc.stdout,
            "stream_name": "stdout",
            "captured": stdout_buf,
            "callback": line_callback,
            "stage": stage,
        },
        name=f"{stage}-stdout-drain",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain,
        kwargs={
            "stream": proc.stderr,
            "stream_name": "stderr",
            "captured": stderr_buf,
            "callback": line_callback,
            "stage": stage,
        },
        name=f"{stage}-stderr-drain",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        returncode = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        # Mirror subprocess.run's timeout semantics: terminate, drain, raise
        # with already-captured output attached. We use kill() (not
        # terminate()) because cdxgen / scancode are CPU-bound and rarely
        # honour SIGTERM cleanly — the subprocess has already blown its
        # budget so a hard kill is appropriate.
        proc.kill()
        # Bounded join so a wedged drain thread cannot keep the worker
        # hostage. Daemon=True means a still-stuck thread will die when the
        # process exits anyway.
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        captured_stdout = b"".join(stdout_buf)
        captured_stderr = b"".join(stderr_buf)
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=float(timeout_seconds),
            output=captured_stdout,
            stderr=captured_stderr,
        ) from None

    # Process exited normally — let the drain threads finish reading whatever
    # the kernel buffered after the process closed its pipes.
    stdout_thread.join()
    stderr_thread.join()

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout=b"".join(stdout_buf),
        stderr=b"".join(stderr_buf),
    )


__all__ = ["LineCallback", "run_with_line_streaming"]
