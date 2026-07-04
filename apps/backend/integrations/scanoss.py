"""
SCANOSS adapter — opt-in vendored-OSS identification (Phase J / P3-11).

What this does
--------------
cdxgen enumerates dependencies that carry a package manifest (``package.json`` /
``pom.xml`` / ``go.mod`` …). It cannot see open-source code that was COPIED into
the tree with no manifest — a file lifted from a GitHub project, a vendored
header, a snippet pasted from Stack Overflow. SCANOSS closes that gap: it
computes a Winnowing fingerprint of each first-party file and matches it against
an Open Source Knowledge Base to recover the originating component + license.

PRIVACY WARNING (read before enabling)
--------------------------------------
When ``SCANOSS_ENABLED`` is true this adapter sends file FINGERPRINTS (Winnowing
hashes) to the external API at ``SCANOSS_API_URL`` (``https://api.osskb.org`` by
default). It does NOT upload the source itself, and it does NOT upload the SBOM —
only the hash fingerprints leave the worker. Even so, fingerprints are derived
from the code, so this is EXTERNAL EGRESS and is therefore OFF BY DEFAULT.
TRUSCA is an on-prem persistent portal (unlike BomLens, a local CLI), so we do
not mirror BomLens's default-ON behaviour: an operator must consciously opt in,
and can point ``SCANOSS_API_URL`` at a self-hosted SCANOSS server to keep
fingerprints on-premises. When disabled, this adapter runs NO subprocess and
performs NO network I/O — it returns an empty result immediately.

Precision: full-file matches only
---------------------------------
SCANOSS reports two match kinds per file: ``"file"`` (the whole file matched a
known OSS file) and ``"snippet"`` (some lines matched). We promote ONLY
``id == "file"`` matches to components. Snippet matches — a few copied lines —
are noisy and low-confidence for a component inventory, so they are skipped.
This mirrors BomLens's ``docker/lib/identify-vendored.sh`` precision rule.

Best-effort, never fatal
------------------------
Consistent with scancode / cosign: a missing ``scanoss-py`` binary, the feature
being disabled, no network, a non-zero exit, a timeout, or an unparseable result
all degrade to an EMPTY result — never an exception that fails the scan. The
caller (``tasks.scan_source``) treats an empty result as "no vendored OSS found"
and moves on.

Output shape (SCANOSS "plain" JSON)
-----------------------------------
``scanoss-py scan`` (plain format) emits a dict keyed by scanned path, each
value a list of match dicts::

    {
      "src/vendored/parson.c": [
        {"id": "file", "purl": ["pkg:github/kgabis/parson"],
         "component": "parson", "version": "1.5.2",
         "licenses": [{"name": "MIT"}]}
      ],
      "src/util.c": [
        {"id": "snippet", "purl": ["pkg:github/someone/util"], ...}
      ]
    }

We parse ``id == "file"`` entries into :class:`VendoredComponent`.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, args are a fixed list
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import (
    scanoss_api_key,
    scanoss_api_url,
    scanoss_enabled,
    scanoss_timeout_seconds,
)
from integrations._line_streamer import LineCallback, run_with_line_streaming
from integrations._subprocess_env import scrubbed_env_for_scanoss

log = structlog.get_logger("integrations.scanoss")

# Binary name devops must ship in the worker image (Dockerfile.worker). Installed
# via ``pip install scanoss`` which provides the ``scanoss-py`` launcher.
SCANOSS_BINARY = "scanoss-py"

# Deterministic result filename inside the stage output dir. A module constant so
# unit tests can locate the JSON the (stubbed) subprocess is expected to write.
RESULT_FILENAME = "scanoss.json"

# Width of ``licenses.spdx_id`` (models/scan.py — ``String(64)``). A license name
# from the (external) SCANOSS API longer than the column would raise
# StringDataRightTruncation on INSERT; the adapter drops it here and the
# persistence layer re-checks defensively.
SPDX_ID_MAX_LENGTH = 64

# Widths of ``components.name`` (``String(512)``) and
# ``component_versions.version`` (``String(255)``). A name/version from the
# (external) SCANOSS API longer than its column would raise
# StringDataRightTruncation and roll back the WHOLE vendored batch (a
# hostile / MITM'd endpoint could thereby silently suppress ALL vendored
# findings for a scan — security-review Low-1). We truncate per-field HERE so
# one over-long field cannot sink the batch.
COMPONENT_NAME_MAX_LENGTH = 512
COMPONENT_VERSION_MAX_LENGTH = 255

# Cap on the number of vendored components returned from one scan so a
# pathological response cannot balloon ``scan_components``. Excess is dropped
# with a WARNING; the scan still succeeds.
MAX_VENDORED_COMPONENTS = 5000

# Result-size ceiling before ``json.load`` materialises the whole document — an
# unbounded API response is an OOM vector. 128 MiB is ample for a fingerprint
# match report (far denser than raw source).
MAX_RESULT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class VendoredComponent:
    """One full-file vendored-OSS match promoted to a component.

    ``purl`` / ``name`` / ``version`` identify the originating component;
    ``licenses`` is the list of SPDX-ish license names SCANOSS reported for it.
    """

    purl: str
    name: str
    version: str
    licenses: list[str]


@dataclass(frozen=True)
class ScanossResult:
    """Output of a SCANOSS run.

    ``vendored`` is the (possibly empty) list of full-file matches.
    ``result_path`` is the on-disk JSON artifact, or ``None`` when the stage was
    a no-op (disabled / binary missing) and no subprocess ran.
    """

    vendored: list[VendoredComponent]
    result_path: Path | None = None


def _empty() -> ScanossResult:
    return ScanossResult(vendored=[], result_path=None)


def run_scanoss(
    *,
    source_dir: Path,
    output_dir: Path,
    timeout_seconds: int | None = None,
    line_callback: LineCallback | None = None,
    verbose: bool = False,
) -> ScanossResult:
    """Run SCANOSS over ``source_dir`` and return full-file vendored matches.

    PRIVACY: when this actually runs (feature enabled + binary present) it sends
    file fingerprints to ``SCANOSS_API_URL``. See the module docstring.

    Contract (best-effort, never raises into the scan):
      - ``scanoss_enabled()`` is false  → immediate empty result. NO subprocess,
        NO network I/O, NO fingerprinting. This is the primary privacy gate; the
        caller ALSO gates on ``scanoss_enabled()`` for defence in depth.
      - ``scanoss-py`` not on ``$PATH`` → immediate empty result (degraded, the
        operator enabled the feature but the worker image lacks the binary).
      - subprocess failure / timeout / unparseable JSON → empty result, logged.

    Args:
        source_dir: First-party source root (the cloned / uploaded workspace).
        output_dir: Workspace subdirectory for the SCANOSS JSON artifact.
        timeout_seconds: Override ``SCANOSS_TIMEOUT_SECONDS``.
        line_callback: Optional stdout/stderr line sink for the scan-log drawer.
        verbose: Reserved for future debug flags; accepted for call-site parity
            with the other adapters.
    """
    # --- Privacy gate #1: feature disabled → no scanner, no egress. ----------
    if not scanoss_enabled():
        log.info("scanoss_disabled", note="SCANOSS_ENABLED is off; no scan, no egress")
        return _empty()

    # --- Degrade gate: binary absent → empty (do not fail the scan). ---------
    if shutil.which(SCANOSS_BINARY) is None:
        log.info(
            "scanoss_not_installed",
            binary=SCANOSS_BINARY,
            note="feature enabled but scanoss-py not on $PATH; skipping",
        )
        return _empty()

    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / RESULT_FILENAME
    timeout = timeout_seconds if timeout_seconds is not None else scanoss_timeout_seconds()
    cmd = _build_command(source_dir=source_dir, result_path=result_path)

    # Defence-in-depth (security-review Low-3): the configured API key rides on
    # the child's argv (``--key``); if any scanoss-py version were to echo the
    # invocation to stdout/stderr, the key would land in the team-readable scan
    # log (streamed) and the server log (stderr on non-zero exit). Redact the
    # key value out of both sinks so the non-leak does not depend on the
    # external binary's logging behaviour. No-op on the default (keyless) tier.
    api_key = scanoss_api_key()

    def _redact(text: str) -> str:
        return text.replace(api_key, "***") if api_key else text

    stream_cb: LineCallback | None = None
    if line_callback is not None:
        stream_cb = lambda line, stage: line_callback(_redact(line), stage)  # noqa: E731

    # We do NOT log the resolved command (it can carry ``--key``) nor the API
    # key. Log only that a run is starting and where the output lands.
    log.info(
        "scanoss_start",
        source_dir=str(source_dir),
        output=str(result_path),
        api_url=scanoss_api_url(),
        timeout_seconds=timeout,
        streaming=line_callback is not None,
    )

    try:
        completed = run_with_line_streaming(
            cmd,
            timeout_seconds=timeout,
            cwd=str(source_dir),
            env=scrubbed_env_for_scanoss(),
            line_callback=stream_cb,
            stage="scanoss",
        )
    except subprocess.TimeoutExpired:
        log.warning("scanoss_timeout", timeout_seconds=timeout, source_dir=str(source_dir))
        return _empty()
    except Exception as exc:  # noqa: BLE001 — best-effort: any spawn error → empty
        log.warning("scanoss_subprocess_error", error=str(exc)[:300])
        return _empty()

    if completed.returncode != 0:
        # scanoss-py exits non-zero on network/auth errors; degrade rather than
        # fail the scan. stderr may echo the endpoint; the key is redacted
        # defensively (security-review Low-3) so a key echoed by any scanoss-py
        # version cannot reach the server log.
        log.warning(
            "scanoss_nonzero_exit",
            returncode=completed.returncode,
            stderr=_redact(completed.stderr.decode("utf-8", errors="replace"))[:1000],
        )
        return _empty()

    vendored = _parse_vendored(result_path)
    log.info("scanoss_succeeded", vendored=len(vendored))
    return ScanossResult(vendored=vendored, result_path=result_path)


def _build_command(*, source_dir: Path, result_path: Path) -> list[str]:
    """Build the ``scanoss-py scan`` argv.

    ``scan <dir>`` fingerprints the tree and matches it against the API.
    ``--output`` writes the plain (raw) JSON we parse. ``--apiurl`` targets the
    configured endpoint (default osskb.org, or a self-hosted server). ``--key``
    is appended ONLY when a key is configured (the public endpoint needs none),
    and it is the ONLY place the key touches the command line — never logged.

    scanoss-py skips common package-manager / build directories by default
    (its built-in ignore set), so we rely on that rather than re-deriving an
    exclude list here.
    """
    cmd = [
        SCANOSS_BINARY,
        "scan",
        str(source_dir),
        "--output",
        str(result_path),
        "--apiurl",
        scanoss_api_url(),
    ]
    key = scanoss_api_key()
    if key:
        cmd.extend(["--key", key])
    return cmd


def _parse_vendored(result_path: Path) -> list[VendoredComponent]:
    """Extract full-file (``id == "file"``) matches from the SCANOSS JSON.

    Best-effort: a missing / oversized / unparseable result yields an empty
    list. Snippet matches are skipped (precision rule). De-duplicated on
    ``(purl, version)`` and capped at :data:`MAX_VENDORED_COMPONENTS`.
    """
    if not result_path.exists():
        log.warning("scanoss_result_missing", path=str(result_path))
        return []

    # OOM guard: stat before json.load. The result is keyed off the scanned tree
    # + API response; skip parsing (empty, non-fatal) when it is too large.
    try:
        size = result_path.stat().st_size
    except OSError as exc:
        log.warning("scanoss_result_unstattable", error=str(exc)[:300])
        return []
    if size > MAX_RESULT_BYTES:
        log.warning(
            "scanoss_result_too_large",
            size_bytes=size,
            limit_bytes=MAX_RESULT_BYTES,
            note="result exceeds cap; vendored parse skipped",
        )
        return []

    try:
        with result_path.open("r", encoding="utf-8") as fh:
            data: Any = json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        log.warning("scanoss_result_unparseable", error=str(exc)[:300])
        return []

    if not isinstance(data, dict):
        return []

    seen: set[tuple[str, str]] = set()
    out: list[VendoredComponent] = []
    snippet_skipped = 0
    capped = False

    for matches in data.values():
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, dict):
                continue
            # Precision rule: full-file matches only. Snippet matches (a few
            # copied lines) are noise for a component inventory.
            if match.get("id") != "file":
                if match.get("id") == "snippet":
                    snippet_skipped += 1
                continue
            component = _parse_match(match)
            if component is None:
                continue
            key = (component.purl, component.version)
            if key in seen:
                continue
            seen.add(key)
            out.append(component)
            if len(out) >= MAX_VENDORED_COMPONENTS:
                capped = True
                break
        if capped:
            break

    if snippet_skipped:
        log.info("scanoss_snippets_skipped", count=snippet_skipped)
    if capped:
        log.warning(
            "scanoss_vendored_capped",
            cap=MAX_VENDORED_COMPONENTS,
            note="excess vendored matches dropped; scan still succeeds",
        )
    return out


def _parse_match(match: dict[str, Any]) -> VendoredComponent | None:
    """Turn one full-file SCANOSS match dict into a :class:`VendoredComponent`.

    Returns ``None`` when the match carries no usable purl (nothing to anchor a
    component on). ``purl`` is SCANOSS's list field — we take the first entry.
    """
    purl = _first_purl(match.get("purl"))
    if not purl:
        return None
    # Truncate to the destination column widths so an over-long field from the
    # external API cannot raise StringDataRightTruncation and roll back the
    # whole vendored batch (security-review Low-1).
    name = (_as_text(match.get("component")) or _name_from_purl(purl))[
        :COMPONENT_NAME_MAX_LENGTH
    ]
    version = (_as_text(match.get("version")) or "unknown")[
        :COMPONENT_VERSION_MAX_LENGTH
    ]
    licenses = _parse_licenses(match.get("licenses"))
    return VendoredComponent(purl=purl, name=name, version=version, licenses=licenses)


def _first_purl(raw: Any) -> str:
    """SCANOSS emits ``purl`` as a list; take the first non-empty string."""
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ""
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _parse_licenses(raw: Any) -> list[str]:
    """Extract license names from SCANOSS's ``licenses: [{"name": ...}]`` array.

    De-duplicated, over-length names dropped (column-width guard), order
    preserved. Non-list / malformed entries yield an empty list.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        name: str | None = None
        if isinstance(entry, dict):
            candidate = entry.get("name")
            if isinstance(candidate, str):
                name = candidate.strip()
        elif isinstance(entry, str):
            name = entry.strip()
        if not name or len(name) > SPDX_ID_MAX_LENGTH or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _as_text(value: Any) -> str:
    """Coerce a SCANOSS field to a trimmed string, or ``""`` for non-strings."""
    return value.strip() if isinstance(value, str) else ""


def _name_from_purl(purl: str) -> str:
    """Derive a display name from a purl when SCANOSS omits ``component``."""
    tail = purl.rsplit("/", 1)[-1]
    at = tail.find("@")
    return tail[:at] if at > 0 else tail


__all__ = [
    "MAX_VENDORED_COMPONENTS",
    "RESULT_FILENAME",
    "SCANOSS_BINARY",
    "SPDX_ID_MAX_LENGTH",
    "LineCallback",
    "ScanossResult",
    "VendoredComponent",
    "run_scanoss",
]
