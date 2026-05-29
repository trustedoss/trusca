"""
Scan-progress Redis publisher — Phase 2 PR #9.

The DB row on ``scans`` is the authoritative store for scan progress
(``current_step`` / ``progress_percent``). This module is a *secondary*
pub/sub channel that lets WebSocket-connected clients see progress as it
happens without polling. The WebSocket gateway in :mod:`api.v1.ws`
subscribes to ``scan:<scan_id>:progress`` (see :func:`core.config.scan_progress_channel`)
and forwards every payload to the connected user.

Design notes:

  - **Fire-and-forget.** Publish failures must NEVER break a scan. The DB
    is the single source of truth — a missed publish degrades to "client
    polls and the next stage commit publishes again". We swallow Redis
    exceptions and emit a ``log.warning`` instead.
  - **Sync API.** Celery tasks run in sync Python, so we use the sync
    redis-py client.
  - **Lazy singleton client.** A worker handles many scans; reusing a
    single connection avoids per-publish TCP handshake. The singleton is
    keyed on the resolved ``REDIS_URL`` so a runtime env change still
    forces a fresh client (CLAUDE.md core rule #11 — no module-level env
    caching).
  - **Test hook.** ``reset_publisher_for_tests`` clears the singleton so
    fakeredis-backed unit tests get a fresh client each case.

Message schemas (canonical):

    Progress frame (existing):
        {
            "type":    "progress",          # P2 #8c addition (backward-compat:
                                            #   absence is interpreted as "progress")
            "percent": <int 0-100>,
            "step":    <str>,
            "ts":      <ISO 8601 UTC, e.g. "2026-05-06T11:24:31.123456+00:00">
        }

    Log frame (P2 #8c):
        {
            "type":   "log",
            "stage":  "cdxgen" | "scancode" | ...,   # which tool produced the line
            "stream": "stdout" | "stderr",
            "line":   <str>,                          # capped at SCAN_LOG_LINE_MAX_LEN
            "ts":     <ISO 8601 UTC>
        }

Both are encoded as UTF-8 JSON bytes; the WS gateway decodes with strict UTF-8.

Step value vocabulary (do not free-text):

    Source pipeline (in-progress):
        bootstrap, fetch, prep, cdxgen, sign, scancode, approvals, trivy, finalize
    Container pipeline (in-progress):
        bootstrap, trivy, persist, finalize
    Terminal (both):
        succeeded, failed

On-disk log persistence (this PR — scan log download):

    Every successful ``publish_log`` call ALSO appends one line to a per-scan
    plain-text file at ``{WORKSPACE_HOST_PATH}/{scan_id}/scan.log``. Line
    format:

        {ISO8601_ts} [{stage}/{stream}] {line}\\n

    The file shares the per-scan budget cap with the Redis publish, uses the
    same truncated line, and is fire-and-forget on the same philosophy as the
    publish — a disk-IO error logs WARNING and the scan keeps running. We hold
    one long-lived file handle per scan_id (line-buffered) for the lifetime of
    the scan; the handle is closed by ``close_log_file(scan_id)`` from the
    scan task's ``finally`` block, and the file itself is reclaimed by the
    existing ``workspace_cleaner`` Celery beat (it ``rmtree``-s the parent
    ``<root>/<scan_id>/`` once the scan is terminal, so ``scan.log`` rides
    along — no new cleanup logic is added here).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import redis
import structlog

from core.config import (
    redis_url,
    scan_log_line_max_len,
    scan_log_max_lines_per_scan,
    scan_log_persist_enabled,
    scan_progress_channel,
    workspace_root,
)

log = structlog.get_logger("tasks.progress")


# ---------------------------------------------------------------------------
# Per-scan log line counter (P2 #8c)
# ---------------------------------------------------------------------------
#
# ``scan_log_max_lines_per_scan()`` caps the number of log lines we will publish
# for a single scan. The counter has to live in process memory because the
# cdxgen / scancode subprocesses publish from worker threads inside a Celery
# task, and we want every line drain thread for the SAME scan to share one
# counter (so cdxgen + scancode together cannot evade the cap). It is keyed on
# the scan id string so concurrent scans inside the same worker (rare, but
# Celery supports it) each get their own budget.
#
# We do NOT use Redis here — the cap is a publish-time safety net, not a
# distributed coordination problem, and Redis round-trips on every line would
# be more expensive than the publish itself. A scan that briefly crosses the
# cap on a different worker (after a worker restart) is acceptable: the lines
# the user actually saw came from the original worker.
# ---------------------------------------------------------------------------

_log_counts: dict[str, int] = {}
_log_counts_lock = threading.Lock()


def reset_log_counter(scan_id: uuid.UUID | str) -> None:
    """Forget the published-line count for ``scan_id`` and close any disk log.

    Called by the task entry point on every (re-)run so a retried scan gets a
    fresh budget. Also closes the cached per-scan file handle so a re-execution
    re-opens ``scan.log`` cleanly — without this, a worker that re-enters the
    same scan id (Celery ``acks_late`` redelivery, retried task) would keep
    appending to a handle whose underlying file may have been rmtree'd by the
    workspace cleaner between runs. Idempotent: missing keys are silently
    ignored.
    """
    key = str(scan_id)
    with _log_counts_lock:
        _log_counts.pop(key, None)
    close_log_file(scan_id)


def _bump_log_counter(scan_id_str: str, *, limit: int) -> bool:
    """Try to consume one slot from the per-scan log budget.

    Returns True when the publish may proceed, False when the budget is
    exhausted (the publisher then silently drops the line). A non-positive
    limit acts as a kill switch — the function always returns False.
    """
    if limit <= 0:
        return False
    with _log_counts_lock:
        current = _log_counts.get(scan_id_str, 0)
        if current >= limit:
            return False
        _log_counts[scan_id_str] = current + 1
        return True


# ---------------------------------------------------------------------------
# Per-scan disk log file cache.
#
# We hold ONE long-lived file handle per scan_id for the lifetime of the scan
# so back-to-back log lines (cdxgen can emit hundreds in a second) do not pay
# an open/close round trip each. The handle is opened in append mode with
# ``buffering=1`` (line-buffered) so every line hits the OS the moment its
# terminating newline is written — important so a ``GET /scans/{id}/log``
# called against a running scan sees the latest lines without a flush race.
#
# ``close_log_file(scan_id)`` is the deterministic teardown — called from the
# scan task's ``finally`` block. If the worker dies hard (SIGKILL) without
# running ``finally``, the OS reclaims the FD on process exit; the file
# itself is reclaimed by ``workspace_cleaner`` when the parent scan reaches
# a terminal status (or by the next ``reset_log_counter`` call on retry).
# ---------------------------------------------------------------------------

_log_files: dict[str, TextIO] = {}
_log_files_lock = threading.Lock()


def _log_file_path_for(scan_id_str: str) -> Path:
    """Resolved on-disk path for a scan's persisted log file."""
    return Path(workspace_root()) / scan_id_str / "scan.log"


def _get_or_open_log_file(scan_id_str: str) -> TextIO | None:
    """Return a cached append-mode handle for ``<workspace>/<scan_id>/scan.log``.

    Creates the parent directory if missing (very early stage — bootstrap may
    publish a line before the workspace dir is created). Returns ``None`` on
    any IO error so the caller can degrade to "WS only" without crashing the
    scan. Thread-safe across the cdxgen + scancode drain threads of a single
    scan.
    """
    with _log_files_lock:
        cached = _log_files.get(scan_id_str)
        if cached is not None and not cached.closed:
            return cached

        try:
            path = _log_file_path_for(scan_id_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            # buffering=1 — line-buffered. encoding=utf-8 — we control the
            # bytes; cdxgen / scancode emit utf-8 by default and any non-utf8
            # sequence we have already collapsed to ``str(line)`` upstream.
            handle = open(  # noqa: SIM115 — long-lived; closed by close_log_file
                path,
                mode="a",
                buffering=1,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            log.warning(
                "scan_log_file_open_failed",
                scan_id=scan_id_str,
                error=str(exc),
            )
            return None

        _log_files[scan_id_str] = handle
        return handle


def close_log_file(scan_id: uuid.UUID | str) -> None:
    """Close + evict the cached per-scan log handle (idempotent).

    Called from the scan task's ``finally`` so the FD is released even on a
    scan crash. Also called from :func:`reset_log_counter` so a Celery
    re-execution opens a fresh handle. Safe to call multiple times; safe to
    call for a scan that never opened a handle.
    """
    key = str(scan_id)
    with _log_files_lock:
        handle = _log_files.pop(key, None)
    if handle is None:
        return
    try:
        handle.close()
    except OSError as exc:  # pragma: no cover — close errors are best-effort
        log.warning(
            "scan_log_file_close_failed",
            scan_id=key,
            error=str(exc),
        )


def _append_log_line_to_disk(
    scan_id_str: str, *, stage: str, stream: str, line: str, ts: str
) -> None:
    """Append one formatted line to the per-scan disk log (best-effort).

    Format mirrors the docstring contract::

        {ISO8601_ts} [{stage}/{stream}] {line}\\n

    Never raises: any IO error is swallowed + logged at WARNING. The Redis
    publish side has already succeeded by the time we get here, so the user
    still sees the live frame on the WebSocket — only the post-hoc download
    misses this single line.

    Known limitation (LOW #9, security-reviewer follow-up): a single
    ``handle.write(...)`` call exceeding the kernel's atomic-write boundary
    (commonly 4 KiB on Linux for ``O_APPEND``) can in principle tear if two
    drain threads on the SAME scan write concurrently. Today this is
    bounded by ``_truncate_line`` (default ``SCAN_LOG_LINE_MAX_LEN`` is well
    under the page size) and by the per-line scrubbed payload. If we ever
    raise the per-line cap above ~4 KiB we should add an explicit lock
    around the ``handle.write`` so tearing cannot happen.
    """
    if not scan_log_persist_enabled():
        return
    handle = _get_or_open_log_file(scan_id_str)
    if handle is None:
        return
    try:
        # The line value is already truncated and is a Python str (utf-8 on
        # disk). We pre-strip a single trailing newline so a tool that emits
        # "...line\n" does not become "...line\n\n" in the file; lines that
        # carry no trailing newline still serialize cleanly.
        clean = line.rstrip("\r\n")
        handle.write(f"{ts} [{stage}/{stream}] {clean}\n")
    except OSError as exc:
        log.warning(
            "scan_log_file_write_failed",
            scan_id=scan_id_str,
            stage=stage,
            stream=stream,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Lazy singleton — see module docstring for rationale.
# ---------------------------------------------------------------------------

_client: redis.Redis | None = None
_client_url: str | None = None


def _get_client() -> redis.Redis:
    """Return a process-wide redis-py client.

    Resolved url is captured alongside the client so a test (or operator
    rotating REDIS_URL) gets a fresh connection rather than a stale one
    pointing at the previous broker. This keeps us honest with CLAUDE.md
    core rule #11 while still amortising connect cost across publishes.
    """
    global _client, _client_url
    url = redis_url()
    if _client is None or _client_url != url:
        # decode_responses=False — we publish bytes payloads (JSON-encoded
        # UTF-8) and let the subscriber side decode. This matches the WS
        # gateway's expectations and avoids accidental string round-trips.
        _client = redis.Redis.from_url(url, decode_responses=False)
        _client_url = url
    return _client


def reset_publisher_for_tests() -> None:
    """Drop the cached client so the next call rebuilds it.

    Unit tests inject a fakeredis instance via monkeypatch on this module's
    ``_get_client`` (or by monkeypatching ``redis.Redis.from_url``); calling
    this in a fixture teardown keeps state from leaking between test cases.
    """
    global _client, _client_url
    if _client is not None:
        try:
            _client.close()  # type: ignore[no-untyped-call]
        except Exception as exc:  # pragma: no cover — close errors are best-effort
            log.debug("scan_progress_client_close_failed", error=str(exc))
    _client = None
    _client_url = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """ISO-8601 UTC timestamp, microsecond precision."""
    return datetime.now(UTC).isoformat()


def publish_progress(
    scan_id: uuid.UUID | str,
    *,
    step: str,
    percent: int,
) -> None:
    """Publish a single progress event to ``scan:<scan_id>:progress``.

    Fire-and-forget: any Redis-side error is swallowed and logged at
    ``WARNING`` so the scan pipeline keeps running. The DB row is the
    single source of truth; pub/sub is best-effort.

    Args:
        scan_id: UUID (object or stringified) — must match the value the
            WebSocket gateway uses to subscribe.
        step:   Stage identifier (``bootstrap``, ``cdxgen``, ``succeeded``,
            etc.). See module docstring for the vocabulary.
        percent: 0-100 progress integer. Clamped to that range so a
            misconfigured caller cannot poison the UI.
    """
    # All serialization happens inside the try so a misbehaving __str__ on
    # the scan_id (or a JSON encoder hiccup) cannot crash the scan. The DB
    # row remains the single source of truth — a missed publish is recovered
    # from on the next stage commit.
    try:
        scan_id_str = str(scan_id)
        clamped = max(0, min(100, int(percent)))
        payload = {
            # P2 #8c — explicit type discriminator. Older clients that ignore
            # the field still see {percent, step, ts} as before; new clients
            # use it to fan out progress vs log frames on the wire. The
            # default-on-absence interpretation is "progress" so a frame
            # forwarded from an older worker remains compatible.
            "type": "progress",
            "percent": clamped,
            "step": step,
            "ts": _now_iso(),
        }
        channel = scan_progress_channel(scan_id_str)
        body = json.dumps(payload).encode("utf-8")
        client = _get_client()
        client.publish(channel, body)
    except Exception as exc:  # broad: redis errors, network, serialization
        # CLAUDE.md core rule #4 doesn't apply (Redis broker, not DT) — but
        # the same "best-effort, never crash the scan" principle holds.
        log.warning(
            "scan_progress_publish_failed",
            scan_id=repr(scan_id),
            step=step,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# P2 #8c — tool log line publisher
# ---------------------------------------------------------------------------


_TRUNC_SUFFIX = "…(truncated)"


def _truncate_line(line: str, limit: int) -> str:
    """Bound a single tool log line at ``limit`` chars.

    A hostile / runaway subprocess might emit a pathological multi-MB single
    line (no newline) — we never want the worker to materialise that as a
    single Redis publish. We slice to ``limit - len(_TRUNC_SUFFIX)`` and tag
    the result so the consumer sees a truncation happened. ``limit`` < 1
    falls back to a hard ``""`` (kill-switch friendly).
    """
    if limit <= 0:
        return ""
    if len(line) <= limit:
        return line
    keep = max(0, limit - len(_TRUNC_SUFFIX))
    return line[:keep] + _TRUNC_SUFFIX


# ---------------------------------------------------------------------------
# Credential scrubbing (MEDIUM, security-reviewer follow-up on ea75d1f)
# ---------------------------------------------------------------------------
#
# Before scan-log disk persistence shipped, ``publish_log`` only fanned out
# to Redis Pub/Sub — ephemeral, channel-scoped, seconds of exposure. Now the
# same lines append to ``scan.log`` on disk (hours-to-days lifetime, downloaded
# via GET /v1/scans/{id}/log by any team member).
#
# ``scan_source._scrub_clone_stderr`` already covers ``git clone`` URLs, but
# other credential-bearing outputs can reach ``publish_log``:
#
#   - cdxgen in verbose / ``CDXGEN_DEBUG_MODE=true`` may echo its resolved
#     npm config including ``npm_config__authToken=<bearer>``.
#   - Verbose HTTP logs in any tool can emit ``Authorization: Bearer <token>``.
#   - Any tool stderr can contain ``https://user:password@host/...`` URLs.
#
# We add a publisher-side scrubber that runs on every line AFTER truncation
# (so the regex cannot be DoS'd by an unbounded input — see
# ``feedback_adversarial_input_parametrize``) but BEFORE both Redis publish
# AND disk write. Pattern matches are intentionally conservative: we'd rather
# false-positive a few innocuous tokens than miss a credential.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # HTTP Authorization header — ANY scheme (Basic / Bearer / token / Digest).
    # Verbose Trivy / cdxgen registry round-trips (``--debug``,
    # ``CDXGEN_DEBUG_MODE=debug``) emit ``Authorization: Basic <b64(user:pass)>``
    # and ``Authorization: token <ghp_...>`` — neither is a Bearer token, so the
    # RFC 6750 pattern below would miss them. We redact everything after the
    # header name to end-of-line. ``.*$`` is line-bounded (lines are rstripped
    # and we never compile re.MULTILINE / re.DOTALL) so there is no catastrophic
    # backtracking on a truncated input. (security-reviewer HIGH on the
    # scan-log-verbosity widening — see feedback_durable_log_persist_requires_scrubber.)
    (re.compile(r"(?i)(authorization\s*:\s*)\S.*$"), r"\1***"),
    # HTTP Bearer tokens anywhere (not only in an Authorization header) —
    # RFC 6750 syntax (token charset).
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-+/=]+"), r"\1***"),
    # Registry / VCS / cloud auth headers Trivy + cdxgen emit in debug mode:
    # ``X-Registry-Auth`` (Docker), ``PRIVATE-TOKEN`` (GitLab), ``X-Amz-Security-Token``
    # (AWS ECR), ``X-Auth-Token`` (generic). Line-bounded ``\S+`` value.
    (
        re.compile(
            r"(?i)((?:x-registry-auth|private-token|x-amz-security-token|x-auth-token)\s*[:=]\s*)\S+"
        ),
        r"\1***",
    ),
    # Set-Cookie / Cookie — session material. Redact to end-of-line so a
    # multi-attribute cookie (``session=abc; Path=/``) is fully covered.
    (re.compile(r"(?i)((?:set-)?cookie\s*:\s*)\S.*$"), r"\1***"),
    # npm-style auth tokens: ``npm_config__authToken=``, ``_authToken:``,
    # ``_auth =``. The trailing ``\S+`` is line-bounded (no re.MULTILINE) so
    # it cannot run away across lines.
    (re.compile(r"(?i)(_auth(?:Token)?\s*[:=]\s*)\S+"), r"\1***"),
    # Generic ``password`` / ``secret`` / ``credential`` / ``access[_-]key`` /
    # ``token`` assignments (``KEY=value`` or ``KEY: value``) that resolved
    # config / env dumps surface in verbose mode (e.g. ``npm_config__password=``,
    # ``GITHUB_TOKEN=ghp_...``, ``AWS_SECRET_ACCESS_KEY=...``). The leading
    # alternation is unanchored so ``AWS_SECRET_ACCESS_KEY`` matches via its
    # ``secret`` substring; over-redaction here is intentional (we'd rather mask
    # a benign ``token=`` than leak a credential).
    (
        re.compile(
            r"(?i)((?:password|passwd|passphrase|secret|credential|access[_-]?key|token)\w*\s*[:=]\s*)\S+"
        ),
        r"\1***",
    ),
    # URLs with userinfo: ``scheme://user:pass@host`` -> ``scheme://***@host``.
    # The userinfo charset excludes ``/`` ``\s`` ``@`` so pathological inputs
    # like ``://user:pass@@@host`` match the first ``user:pass@`` only — the
    # trailing ``@@host`` becomes opaque path, not a userinfo passthrough.
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s@]+:[^/\s@]+@"), r"\1***@"),
    # Generic API key headers: ``X-API-Key:``, ``api-key=``, ``api_key:``
    (re.compile(r"(?i)(x-api-key\s*[:=]\s*)\S+"), r"\1***"),
    (re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)\S+"), r"\1***"),
)


def _scrub_secrets(line: str) -> str:
    """Best-effort credential redaction on a log line.

    Runs on every line BEFORE Redis publish and disk write. Pre-PR this was
    Redis-only (ephemeral); the new disk persistence + download endpoint
    elevates a transient leak into a durable, downloadable one. The caller
    MUST truncate first (cf. ``_truncate_line``) so this function never scans
    unbounded input — that ordering also satisfies
    ``feedback_adversarial_input_parametrize`` for separator-only / oversized
    inputs.

    Fails CLOSED + observable: if any pattern raises (a future regression or a
    pathological input that trips the engine), we drop the line to a hard
    sentinel and emit a distinct ``scan_log_scrub_failed`` event (no line
    content) so a redaction regression is alertable instead of silently
    leaking the un-scrubbed line downstream (security-reviewer LOW).
    """
    try:
        for pattern, replacement in _SECRET_PATTERNS:
            line = pattern.sub(replacement, line)
        return line
    except Exception as exc:  # noqa: BLE001 — fail closed, never leak the raw line
        log.warning("scan_log_scrub_failed", error=str(exc))
        return "***(redaction failed)***"


_VALID_STREAMS: frozenset[str] = frozenset({"stdout", "stderr"})


def publish_log(
    scan_id: uuid.UUID | str,
    *,
    stage: str,
    stream: str,
    line: str,
) -> None:
    """Publish a single tool log line to ``scan:<scan_id>:progress``.

    Fire-and-forget, same contract as :func:`publish_progress`: any Redis-side
    error is swallowed and logged at WARNING so the scan pipeline keeps
    running. The structlog ``log.warning`` text never contains the raw line
    (the line could carry secrets if a misbehaving tool ever echoed them).

    Args:
        scan_id: UUID (object or stringified) — must match the value the
            WebSocket gateway uses to subscribe.
        stage:   Pipeline stage that produced the line. Caller is expected to
            use the canonical step vocabulary (``cdxgen``, ``scancode``, …)
            so the FE can color-code consistently.
        stream:  ``"stdout"`` or ``"stderr"``. Any other value is normalised
            to ``"stdout"`` (defensive — never block a publish over a typo).
        line:    The raw line text. Truncated to ``SCAN_LOG_LINE_MAX_LEN``
            chars and bounded per-scan by ``SCAN_LOG_MAX_LINES_PER_SCAN``.

    The per-scan publish cap is enforced inside this helper, BEFORE
    serialization, so an over-cap line never touches Redis. The cap is shared
    across all stages of a single scan (cdxgen + scancode together) so a
    runaway subprocess cannot evade the limit by racing another stage.
    """
    try:
        scan_id_str = str(scan_id)
        line_limit = scan_log_line_max_len()
        scan_limit = scan_log_max_lines_per_scan()

        # Per-scan budget check FIRST (cheapest), so an over-cap publish never
        # hits Redis OR the on-disk scan.log. The disk write and the WS frame
        # share the same budget so the downloaded log matches what the user
        # saw on the wire — neither can leak past the cap.
        if not _bump_log_counter(scan_id_str, limit=scan_limit):
            return

        safe_stream = stream if stream in _VALID_STREAMS else "stdout"
        safe_stage = str(stage)
        # Order matters: truncate FIRST (bounds the regex input — adversarial
        # multi-MB single lines cannot DoS the scrubber), then scrub. The
        # scrubbed line is what reaches BOTH Redis (live WS) AND the on-disk
        # ``scan.log`` (durable, downloadable via GET /scans/{id}/log).
        safe_line = _scrub_secrets(_truncate_line(str(line), line_limit))
        ts = _now_iso()

        # Persist to disk FIRST. The WS publish is the live view (a missed
        # publish only matters for an open browser at the time); the disk file
        # is the historical record we serve from GET /scans/{id}/log days
        # later. Both share the same budget + truncation contract above.
        # _append_log_line_to_disk swallows its own IO errors and never
        # raises.
        _append_log_line_to_disk(
            scan_id_str,
            stage=safe_stage,
            stream=safe_stream,
            line=safe_line,
            ts=ts,
        )

        payload = {
            "type": "log",
            "stage": safe_stage,
            "stream": safe_stream,
            "line": safe_line,
            "ts": ts,
        }
        channel = scan_progress_channel(scan_id_str)
        body = json.dumps(payload).encode("utf-8")
        client = _get_client()
        client.publish(channel, body)
    except Exception as exc:  # broad: redis errors, network, serialization
        # Same philosophy as publish_progress — best-effort, never crash a
        # scan over a log-streaming hiccup. We DELIBERATELY do not log the
        # `line` content: it can carry attacker-controlled bytes and we
        # already have a length-bounded payload escape valve above.
        log.warning(
            "scan_log_publish_failed",
            scan_id=repr(scan_id),
            stage=stage,
            stream=stream,
            error=str(exc),
        )


def make_line_callback(
    scan_uuid: uuid.UUID, *, stage: str
) -> Callable[[str, str], None]:
    """Build a line-callback that forwards subprocess output to the scan log.

    P2 #8c — used by the cdxgen / scancode / Trivy call sites in the scan
    tasks. The callback runs inside a background drain thread spawned by
    ``_line_streamer``; it MUST NOT raise. ``publish_log`` is itself
    fire-and-forget (Redis errors are swallowed + logged), but we still wrap
    the call in a try/except as belt-and-suspenders: a publisher bug must
    never break a scan over a log-streaming side-channel.

    The per-scan line budget is enforced inside ``publish_log`` so the drain
    thread keeps reading the subprocess pipes even after the budget is
    exhausted (closing the pipe early could deadlock the subprocess once its
    kernel pipe buffer fills) — over-budget lines are silently dropped on the
    publisher side.
    """

    def _cb(stream: str, line: str) -> None:
        try:
            publish_log(scan_uuid, stage=stage, stream=stream, line=line)
        except Exception as exc:  # noqa: BLE001 — never break the drain
            log.warning(
                "scan_log_callback_unexpected",
                stage=stage,
                stream=stream,
                error=str(exc)[:300],
            )

    return _cb


__all__ = [
    "close_log_file",
    "make_line_callback",
    "publish_log",
    "publish_progress",
    "reset_log_counter",
    "reset_publisher_for_tests",
]
