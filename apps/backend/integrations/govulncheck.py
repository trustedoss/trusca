"""govulncheck adapter — Go call-graph reachability analyser (v2.3 r1).

``govulncheck`` (golang.org/x/vuln/cmd/govulncheck) does *static call-graph*
vulnerability analysis for Go modules: it cross-references the module's import
graph and call graph against the Go vulnerability database (OSV records, ids
like ``GO-2023-1234`` that carry CVE / GHSA *aliases*) and reports, per
vulnerability, whether the vulnerable symbol is actually **reachable** from the
analysed code — not merely present as a dependency.

We run it as ``govulncheck -json ./...`` from the module directory and parse the
streaming JSON to extract, for each OSV id (and its aliases), a single
reachability verdict:

    reachable=True   the OSV appears in a ``finding`` whose trace reaches a
                     concrete *function* frame — i.e. govulncheck found a call
                     path into the vulnerable symbol.
    reachable=False  the OSV is known to affect a module/package in the graph
                     (it shows up in an ``osv`` record and/or a module/package
                     level ``finding``) but NO call-level trace was reported —
                     present but, per the call graph, not reachable.

This is a **best-effort enrichment**, NOT a primary scan stage (CLAUDE.md core
rule #3: it still runs inside Celery, never inline). Every failure mode —
binary missing, not a Go module, non-zero exit, timeout, broken / hostile JSON —
degrades to an EMPTY result + a WARNING log. The caller
(``tasks.scan_reachability``) leaves the affected findings' ``reachable`` column
NULL ("not analysed") and the scan it enriches is unaffected.

Output format (govulncheck 1.x ``-json``)
-----------------------------------------
A *stream* of JSON objects (NOT a single document — older builds emit one object
per line, newer ones a concatenated/whitespace-separated stream). Each object
has exactly one top-level key:

    {"config":   {...}}            run metadata (Go version, db, scan level)
    {"progress": {...}}            human progress messages
    {"osv":      {<OSV entry>}}    a vulnerability definition; ``id`` is the GO-
                                   id, ``aliases`` lists CVE-*/GHSA-* ids.
    {"finding":  {"osv": "<id>",   one observed instance of an OSV in the graph;
                  "trace": [...]}} the trace frames go shallow→deep. A frame with
                                   a ``function`` key is a call-level frame; a
                                   trace whose frames are module/package-only
                                   means "present but not called".

We tolerate unknown keys, missing keys, non-dict objects, and trailing garbage:
adversarial / truncated output yields whatever clean verdicts we could parse
(possibly none), never an exception that escapes the adapter.

Security
--------
``govulncheck`` is a vetted local binary; the command is a hardcoded argv list
(no shell, no user-interpolated flags). ``cwd`` is the worker-created module
directory. The subprocess receives a scrubbed env (worker secrets removed) via
``integrations._subprocess_env``. Bandit S603/S404 are false positives for this
controlled invocation (documented inline).
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 — vetted local binary, hardcoded argv, no shell
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import structlog

from core.config import govulncheck_max_output_bytes, govulncheck_timeout_seconds
from integrations._subprocess_env import scrubbed_env_for_prep

log = structlog.get_logger("integrations.govulncheck")

# Stable label persisted in ``vulnerability_findings.reachability_source``. Kept
# well under the column width (VARCHAR(64)).
SOURCE_LABEL = "govulncheck"

# The exact, fixed argv. ``govulncheck`` is resolved via $PATH (the worker image
# symlinks the installed binary onto /usr/local/bin — mirrors how the scancode
# adapter shells to the bare ``scancode`` name). No user input is interpolated;
# ``-json`` selects the machine-readable stream and ``./...`` analyses every
# package in the module. Bandit S607 (partial executable path) is accepted for
# this controlled, $PATH-resolved invocation, same as scancode/Trivy.
_GOVULNCHECK_ARGV = ["govulncheck", "-json", "./..."]  # noqa: S607

# Cap on how many distinct OSV/alias ids we surface from one run, mirroring the
# scancode adapter's detection cap: a pathological / hostile output cannot
# balloon the verdict map the caller iterates and writes to the DB.
_MAX_VERDICTS = 5000

# Per-id cap on aliases we fan a verdict out to (a real OSV carries a handful of
# CVE/GHSA aliases; an adversarial record could list thousands).
_MAX_ALIASES_PER_OSV = 64

# An id token we are willing to key a verdict on. Real ids are GO-YYYY-NNNN /
# CVE-YYYY-NNNNN / GHSA-xxxx-xxxx-xxxx — all short, ASCII, no separators that
# could smuggle a traversal / injection downstream. We bound the length so a
# hostile giant "id" string cannot bloat memory or the DB lookup; the matcher in
# the task validates against the real ``vulnerabilities.external_id`` set anyway.
_MAX_ID_LEN = 128


# ---------------------------------------------------------------------------
# Errors (all caught internally → empty result; surfaced only for tests)
# ---------------------------------------------------------------------------


class GovulncheckError(RuntimeError):
    """Base class for govulncheck adapter errors."""


class GovulncheckNotInstalled(GovulncheckError):
    """The ``govulncheck`` binary is not on $PATH."""


class GovulncheckNotAModule(GovulncheckError):
    """The target directory has no ``go.mod`` — nothing to analyse."""


class GovulncheckTimeout(GovulncheckError):
    """govulncheck ran longer than the per-run wall-clock limit."""


class GovulncheckFailed(GovulncheckError):
    """govulncheck exited non-zero in a way that yielded no parseable output."""


@dataclass(frozen=True)
class ReachabilityResult:
    """Verdict map from one govulncheck run.

    ``verdicts`` maps an uppercased vulnerability id (the OSV GO-id AND each of
    its CVE/GHSA aliases, all pointing at the same verdict) to a bool:
    ``True`` = reachable (a call-level trace was found), ``False`` = present but
    not reachable. An id absent from the map was never seen by govulncheck and
    the caller must leave it NULL ("not analysed").

    ``analysed`` is ``True`` only when govulncheck actually ran to a usable
    result (even if it found zero vulnerabilities). It is ``False`` for every
    graceful-skip path (binary missing, not a module, timeout, unusable output)
    so the caller can distinguish "ran, nothing reachable" from "did not run".
    """

    verdicts: dict[str, bool]
    analysed: bool


# Sentinel for "we did not / could not analyse" — empty verdicts, analysed=False.
_EMPTY = ReachabilityResult(verdicts={}, analysed=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_govulncheck(
    *,
    module_dir: Path,
    timeout_seconds: int | None = None,
    max_output_bytes: int | None = None,
) -> ReachabilityResult:
    """Run ``govulncheck -json ./...`` in ``module_dir`` and return verdicts.

    Best-effort: ANY failure returns :data:`_EMPTY` (empty verdicts,
    ``analysed=False``) after a WARNING log — it NEVER raises into the caller.
    The typed exceptions exist for unit-test assertions; the public contract is
    "you always get a ReachabilityResult".

    Args:
        module_dir: Directory containing the Go module (``go.mod``).
        timeout_seconds: Override ``GOVULNCHECK_TIMEOUT_SECONDS``.
        max_output_bytes: Override ``GOVULNCHECK_MAX_OUTPUT_BYTES`` (the parsed
            stdout is size-capped before deserialisation so a hostile module
            cannot OOM the worker via a giant report).

    Returns:
        ReachabilityResult — empty + ``analysed=False`` on any skip / failure.
    """
    try:
        return _run(
            module_dir=module_dir,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
    except GovulncheckNotInstalled:
        log.warning("govulncheck_not_installed", module_dir=str(module_dir))
        return _EMPTY
    except GovulncheckNotAModule:
        log.warning("govulncheck_not_a_module", module_dir=str(module_dir))
        return _EMPTY
    except GovulncheckTimeout:
        log.warning("govulncheck_timeout", module_dir=str(module_dir))
        return _EMPTY
    except GovulncheckFailed as exc:
        log.warning("govulncheck_failed", module_dir=str(module_dir), error=str(exc)[:300])
        return _EMPTY
    except Exception as exc:  # noqa: BLE001 — best-effort: never fail the caller
        # An unexpected bug in our own parsing must not break the reachability
        # task (which is itself best-effort). Log and degrade to "not analysed".
        log.warning(
            "govulncheck_unexpected_error",
            module_dir=str(module_dir),
            error=str(exc)[:300],
        )
        return _EMPTY


def _run(
    *,
    module_dir: Path,
    timeout_seconds: int | None,
    max_output_bytes: int | None,
) -> ReachabilityResult:
    """Inner runner that raises typed errors; wrapped by :func:`run_govulncheck`."""
    if not (module_dir / "go.mod").is_file():
        raise GovulncheckNotAModule(f"no go.mod in {module_dir}")
    if shutil.which("govulncheck") is None:
        raise GovulncheckNotInstalled("govulncheck not on $PATH")

    timeout = timeout_seconds if timeout_seconds is not None else govulncheck_timeout_seconds()
    cap = max_output_bytes if max_output_bytes is not None else govulncheck_max_output_bytes()

    try:
        proc = subprocess.run(  # noqa: S603 — vetted binary, fixed argv, no shell
            _GOVULNCHECK_ARGV,
            cwd=str(module_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=scrubbed_env_for_prep(),
        )
    except subprocess.TimeoutExpired as exc:
        raise GovulncheckTimeout(str(exc)) from exc
    except OSError as exc:
        # FileNotFoundError despite the which() check (race / mount), or the
        # workspace mounted noexec — treat as "could not run".
        raise GovulncheckFailed(f"spawn failed: {exc}") from exc

    stdout = proc.stdout or ""
    # govulncheck exits non-zero (3) WHEN it finds vulnerabilities — that is a
    # SUCCESSFUL run with results, not a failure. A real failure (bad flags,
    # build error) also exits non-zero but emits little/no parseable JSON on
    # stdout. So we don't gate on returncode: we parse stdout and only treat it
    # as a failure when nothing parseable came back AND the exit was non-zero.
    if len(stdout.encode("utf-8", errors="ignore")) > cap:
        # Truncating the stream mid-object would corrupt the tail record; we
        # still parse what fits — the streaming parser tolerates a broken final
        # object. Bound the slice in characters generously above the byte cap.
        stdout = stdout[: cap]

    verdicts = _parse_stream(stdout)

    if not verdicts and proc.returncode not in (0, 3):
        raise GovulncheckFailed(
            f"exit={proc.returncode}, stderr={(proc.stderr or '')[:200]!r}"
        )

    log.info(
        "govulncheck_finished",
        module_dir=str(module_dir),
        returncode=proc.returncode,
        verdict_count=len(verdicts),
        reachable_count=sum(1 for v in verdicts.values() if v),
    )
    return ReachabilityResult(verdicts=verdicts, analysed=True)


# ---------------------------------------------------------------------------
# Streaming JSON parser
# ---------------------------------------------------------------------------


def _parse_stream(text: str) -> dict[str, bool]:
    """Parse a govulncheck ``-json`` stream into an id → reachable verdict map.

    Two passes over the decoded objects:

      1. Collect ``osv`` records to build GO-id → [aliases] so a verdict on a
         GO-id can fan out to its CVE/GHSA aliases (DT findings key on those).
      2. Walk ``finding`` records: a finding with a call-level (``function``)
         trace frame marks its OSV reachable=True; a finding without one
         contributes reachable=False *unless already True*. ``True`` always
         wins over ``False`` for the same id.

    Tolerant of: line-delimited OR concatenated objects, non-dict objects,
    missing keys, unknown keys, and trailing garbage (parses the valid prefix).
    """
    osv_aliases: dict[str, list[str]] = {}
    # Verdict keyed by GO-id first; aliases are merged in at the end so an
    # alias-only contradiction can't lose to ordering.
    go_verdict: dict[str, bool] = {}
    # An OSV that appeared at all (osv record or finding) but for which we never
    # saw a call-level trace → eligible for a False verdict.
    seen_osv: set[str] = set()

    for obj in _iter_json_objects(text):
        if not isinstance(obj, dict):
            continue
        if "osv" in obj and isinstance(obj["osv"], dict):
            entry = obj["osv"]
            osv_id = _clean_id(entry.get("id"))
            if osv_id is None:
                continue
            seen_osv.add(osv_id)
            aliases = entry.get("aliases")
            if isinstance(aliases, list):
                cleaned = [
                    a for a in (_clean_id(x) for x in aliases[:_MAX_ALIASES_PER_OSV])
                    if a is not None
                ]
                if cleaned:
                    osv_aliases.setdefault(osv_id, []).extend(cleaned)
        elif "finding" in obj and isinstance(obj["finding"], dict):
            finding = obj["finding"]
            osv_id = _clean_id(finding.get("osv"))
            if osv_id is None:
                continue
            seen_osv.add(osv_id)
            reachable = _trace_is_reachable(finding.get("trace"))
            # True wins; never downgrade a previously-True verdict.
            if reachable:
                go_verdict[osv_id] = True
            else:
                go_verdict.setdefault(osv_id, False)

    # Every OSV we saw at all but have no verdict for yet is "present, not
    # reachable" (False) — an osv record with no matching finding trace.
    for osv_id in seen_osv:
        go_verdict.setdefault(osv_id, False)

    return _fan_out_aliases(go_verdict, osv_aliases)


def _iter_json_objects(text: str) -> Iterator[object]:
    """Yield top-level JSON objects from a possibly-concatenated stream.

    ``json.JSONDecoder.raw_decode`` reads one value at a time and reports how far
    it got, so we can walk a stream of objects with no separators (the newer
    govulncheck format) OR one-per-line (older). On a decode error we skip a
    single character and retry — this drops trailing garbage / a truncated final
    object without aborting the whole parse.
    """
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    # Bound the number of objects so a hostile stream of millions of tiny
    # objects cannot pin the worker.
    emitted = 0
    while idx < n and emitted < _MAX_VERDICTS * 4:
        # Skip whitespace between objects.
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except (json.JSONDecodeError, ValueError):
            # Not a valid value at this position — skip one char and retry. This
            # is how we tolerate a corrupt/truncated tail or interleaved noise.
            idx += 1
            continue
        if end <= idx:  # pragma: no cover — defensive against a zero-width decode
            idx += 1
            continue
        idx = end
        emitted += 1
        yield obj


def _trace_is_reachable(trace: object) -> bool:
    """True iff any trace frame names a concrete ``function`` (call-level).

    govulncheck trace frames go shallow→deep; a frame carrying a non-empty
    ``function`` field means the analyser found a call path INTO the vulnerable
    symbol. A trace of only module/package frames means "present but not
    called" → not reachable. A missing / non-list trace is treated as not
    reachable (it cannot demonstrate a call path).
    """
    if not isinstance(trace, list):
        return False
    for frame in trace:
        if isinstance(frame, dict):
            fn = frame.get("function")
            if isinstance(fn, str) and fn.strip():
                return True
    return False


def _fan_out_aliases(
    go_verdict: dict[str, bool], osv_aliases: dict[str, list[str]]
) -> dict[str, bool]:
    """Merge each GO-id verdict onto its CVE/GHSA aliases.

    DT findings key on CVE/GHSA ids (``vulnerabilities.external_id``), but
    govulncheck reports GO-ids — so a verdict is only useful if it also lands on
    the aliases. ``True`` always wins on a collision. Capped at ``_MAX_VERDICTS``
    total entries so a hostile alias explosion cannot balloon the map.
    """
    out: dict[str, bool] = {}

    def _put(key: str, value: bool) -> None:
        if len(out) >= _MAX_VERDICTS and key not in out:
            return
        if out.get(key):  # already True — never downgrade
            return
        out[key] = value

    for go_id, verdict in go_verdict.items():
        _put(go_id, verdict)
        for alias in osv_aliases.get(go_id, ()):
            _put(alias, verdict)
    return out


def _clean_id(value: object) -> str | None:
    """Validate + normalise a vulnerability id token from untrusted JSON.

    Returns the uppercased, stripped id when it is a non-empty string within the
    length bound, else ``None``. Uppercasing matches how the task compares
    against ``vulnerabilities.external_id`` (GO-/CVE-/GHSA- ids are conventionally
    upper). We do NOT charset-filter here — the task only ever uses these as
    dict keys / equality probes against a known id set, so a weird token simply
    never matches; rejecting only oversized / non-string keeps the parser
    permissive without creating a downstream injection surface.
    """
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > _MAX_ID_LEN:
        return None
    return token.upper()


__all__ = [
    "GovulncheckError",
    "GovulncheckFailed",
    "GovulncheckNotAModule",
    "GovulncheckNotInstalled",
    "GovulncheckTimeout",
    "ReachabilityResult",
    "SOURCE_LABEL",
    "run_govulncheck",
]
