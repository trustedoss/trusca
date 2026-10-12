#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Difference table for a shadow run of the golden fixtures.

A shadow run (``golden-nightly.yml`` dispatched with ``shadow=true``) scans
every golden fixture twice on one stack: once with the in-process engine and
once with the BomLens engine, both reading the same Trivy database. Each pass
writes one ``run_golden.py --shadow-out`` capture per fixture. The
``golden-containers`` job does the same for the container images in
``container_images.json``, into the same folders. This script reads the two
folders and writes a Markdown report: one summary row per fixture or image,
the time each scan spent per stage, then the items behind every difference.

It measures; it does not gate. The exit code is 1 only when there is nothing
to compare (no capture on either side), so a run that measured nothing does
not look like a run where both engines agreed.

stdlib only, like ``run_golden.py``.

Usage:
  python shadow_report.py --inprocess <dir> --bomlens <dir> \\
      [--trivy-db <dir>] [--engine-log <dir>] [--out report.md]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: Items listed per difference in the details section before it is cut.
LIST_CAP = 40


def load_captures(folder: Path | None) -> dict[str, dict]:
    """``{fixture: capture}`` for every ``<fixture>.json`` in ``folder``."""
    if folder is None or not folder.is_dir():
        return {}
    return {p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _shadow(capture: dict) -> dict:
    return _as_dict(capture.get("shadow"))


def _scan(capture: dict) -> dict:
    return _as_dict(_shadow(capture).get("scan"))


def purls(capture: dict) -> set[str] | None:
    comps = capture.get("components")
    if not isinstance(comps, dict):
        return None
    return {p or "(no purl)" for p in comps.get("purls") or []}


def component_count(capture: dict) -> int | None:
    comps = capture.get("components")
    if not isinstance(comps, dict):
        return None
    count = comps.get("count")
    return count if isinstance(count, int) else len(comps.get("purls") or [])


def licenses(capture: dict) -> set[str] | None:
    value = capture.get("licenses")
    return set(value) if isinstance(value, list) else None


def vulnerability_ids(capture: dict) -> set[str] | None:
    """The identifier set a shadow capture recorded; ``None`` on a baseline."""
    value = _shadow(capture).get("vulnerability_ids")
    return set(value) if isinstance(value, list) else None


def vulnerability_count(capture: dict) -> int | None:
    value = capture.get("vulnerabilities_count")
    return value if isinstance(value, int) else None


def status(capture: dict) -> str:
    if "error" in capture:
        return "error"
    return str(capture.get("scan_status") or "?")


def degraded(capture: dict) -> list[str]:
    """``stage:reason`` for every stage the scan recorded as degraded."""
    scan = _scan(capture)
    outcomes = _as_dict(scan.get("stage_outcomes"))
    out = [f"{k}:{v}" for k, v in sorted(outcomes.items())]
    skipped = scan.get("scancode_skipped")
    if skipped and "scancode" not in outcomes:
        out.append(f"scancode:{skipped}")
    return out


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def scan_seconds(capture: dict) -> float | None:
    """The scan row's own start-to-end time, else the harness's wall clock."""
    scan = _scan(capture)
    start, end = _parse_time(scan.get("started_at")), _parse_time(scan.get("completed_at"))
    if start and end:
        return round((end - start).total_seconds(), 1)
    wall = _shadow(capture).get("wall_seconds")
    return float(wall) if isinstance(wall, int | float) else None


def input_kind(capture: dict) -> str:
    """``source`` or ``container``: what the harness scanned.

    Recorded by ``run_golden.py`` with every capture, a failed one included.
    A capture from before the field is a source fixture: images were added
    to the run later.
    """
    kind = _as_dict(capture.get("input")).get("kind")
    return str(kind) if kind else "source"


def image_ref(capture: dict) -> str | None:
    ref = _as_dict(capture.get("input")).get("image_ref")
    return ref if isinstance(ref, str) and ref else None


def gate(capture: dict) -> str:
    value = capture.get("gate")
    if isinstance(value, dict) and value.get("gate"):
        return str(value["gate"])
    return "-"


def _only(a: set[str] | None, b: set[str] | None) -> list[str] | None:
    """Sorted items in ``a`` and not in ``b``; ``None`` when either is unknown."""
    if a is None or b is None:
        return None
    return sorted(a - b)


@dataclass
class Row:
    """One fixture's comparison. ``ip`` is in-process, ``bl`` is BomLens."""

    name: str
    ip: dict | None
    bl: dict | None
    only_bl_purls: list[str] | None = field(default=None)
    only_ip_purls: list[str] | None = field(default=None)
    only_bl_licenses: list[str] | None = field(default=None)
    only_ip_licenses: list[str] | None = field(default=None)
    only_bl_vulns: list[str] | None = field(default=None)
    only_ip_vulns: list[str] | None = field(default=None)

    @property
    def kind(self) -> str:
        """The input kind; both sides when they disagree (they never should)."""
        kinds = sorted({input_kind(c) for c in (self.ip, self.bl) if c is not None})
        return " / ".join(kinds) or "source"

    @property
    def image(self) -> str | None:
        refs = sorted({r for c in (self.ip, self.bl) if c is not None if (r := image_ref(c))})
        return " / ".join(refs) or None

    @property
    def differs(self) -> bool:
        if self.ip is None or self.bl is None:
            return True
        lists = (
            self.only_bl_purls, self.only_ip_purls,
            self.only_bl_licenses, self.only_ip_licenses,
            self.only_bl_vulns, self.only_ip_vulns,
        )
        if any(lst for lst in lists):
            return True
        return (
            component_count(self.ip) != component_count(self.bl)
            or vulnerability_count(self.ip) != vulnerability_count(self.bl)
            or status(self.ip) != status(self.bl)
            or degraded(self.ip) != degraded(self.bl)
            or gate(self.ip) != gate(self.bl)
        )


def compare(name: str, ip: dict | None, bl: dict | None) -> Row:
    row = Row(name=name, ip=ip, bl=bl)
    if ip is None or bl is None:
        return row
    row.only_bl_purls = _only(purls(bl), purls(ip))
    row.only_ip_purls = _only(purls(ip), purls(bl))
    row.only_bl_licenses = _only(licenses(bl), licenses(ip))
    row.only_ip_licenses = _only(licenses(ip), licenses(bl))
    row.only_bl_vulns = _only(vulnerability_ids(bl), vulnerability_ids(ip))
    row.only_ip_vulns = _only(vulnerability_ids(ip), vulnerability_ids(bl))
    return row


def compare_all(inprocess: dict[str, dict], bomlens: dict[str, dict]) -> list[Row]:
    """One row per name on either side: source fixtures first, then images."""
    names = sorted(set(inprocess) | set(bomlens))
    rows = [compare(n, inprocess.get(n), bomlens.get(n)) for n in names]
    return sorted(rows, key=lambda r: (r.kind != "source", r.name))


# ---------------------------------------------------------------------------
# Trivy database identity
# ---------------------------------------------------------------------------


def load_trivy(folder: Path | None) -> dict[str, dict[str, dict | None]]:
    """``{shard: {"before": metadata, "after": metadata}}`` from ``<shard>-<when>.json``."""
    out: dict[str, dict[str, dict | None]] = {}
    if folder is None or not folder.is_dir():
        return out
    for p in sorted(folder.glob("*.json")):
        shard, _, when = p.stem.rpartition("-")
        if not shard or when not in ("before", "after"):
            continue
        try:
            meta = json.loads(p.read_text())
        except (ValueError, OSError):
            meta = None
        out.setdefault(shard, {"before": None, "after": None})[when] = meta
    return out


def same_db(before: dict | None, after: dict | None) -> bool:
    """Whether the database did not move between the first and the second pass."""
    if not before or not after:
        return False
    keys = ("Version", "UpdatedAt")
    return all(before.get(k) == after.get(k) for k in keys) and before.get("UpdatedAt") is not None


# ---------------------------------------------------------------------------
# Stage timings
# ---------------------------------------------------------------------------

#: ``(started, ended)``; ``ended`` is ``None`` for a stage left open.
Window = tuple[datetime, datetime | None]


def stage_windows(capture: dict) -> dict[str, Window] | None:
    """The scan row's ``stage_timings``; ``None`` when the capture has none."""
    raw = _scan(capture).get("stage_timings")
    if not isinstance(raw, dict):
        return None
    out: dict[str, Window] = {}
    for stage, value in raw.items():
        entry = _as_dict(value)
        start = _parse_time(entry.get("started_at"))
        if start is not None:
            out[str(stage)] = (start, _parse_time(entry.get("ended_at")))
    return out


def scan_window(capture: dict) -> tuple[datetime, datetime] | None:
    scan = _scan(capture)
    start, end = _parse_time(scan.get("started_at")), _parse_time(scan.get("completed_at"))
    return (start, end) if start and end else None


def _seconds(start: datetime, end: datetime | None) -> float | None:
    return None if end is None else max((end - start).total_seconds(), 0.0)


def enclosing(windows: dict[str, Window]) -> dict[str, str]:
    """``{stage: the largest other stage whose window contains it}``.

    The BomLens engine reports some stages from its own step times, inside the
    window TRUSCA recorded around the container that ran them (and one engine
    window can hold another). Such a stage is shown, but its time is not added
    to the rest; the largest container names the stage whose time is.
    """
    out: dict[str, str] = {}
    for name, (start, end) in windows.items():
        if end is None:
            continue
        best: tuple[float, str] | None = None
        for other, (o_start, o_end) in windows.items():
            if other == name or o_end is None:
                continue
            length = (o_end - o_start).total_seconds()
            inside = o_start <= start and end <= o_end and start < o_end
            if inside and length > (end - start).total_seconds():
                if best is None or length > best[0]:
                    best = (length, other)
        if best is not None:
            out[name] = best[1]
    return out


def uncovered_seconds(windows: dict[str, Window], span: tuple[datetime, datetime]) -> float:
    """Seconds of ``span`` that no closed stage window covers."""
    lo, hi = span
    pieces = sorted(
        (max(start, lo), min(end, hi))
        for start, end in windows.values()
        if end is not None and min(end, hi) > max(start, lo)
    )
    covered = 0.0
    cursor = lo
    for start, end in pieces:
        start = max(start, cursor)
        if end > start:
            covered += (end - start).total_seconds()
            cursor = end
    return max((hi - lo).total_seconds() - covered, 0.0)


@dataclass
class Container:
    """One engine container, from its start and exit lines in the worker log."""

    label: str
    started: datetime
    ended: datetime | None = None
    exit_code: object = None


#: ``trusca-bomlens-<scan hex>-<n>-<label>``, the runner's container names.
_CONTAINER_LABEL = re.compile(r"-\d+-([A-Za-z0-9_]+)$")
_CONTAINER_EVENTS = ("bomlens_container_start", "bomlens_container_exit")


def _log_event(line: str) -> dict | None:
    brace = line.find("{")
    if brace < 0:
        return None
    try:
        event = json.loads(line[brace:])
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("event") not in _CONTAINER_EVENTS:
        return None
    return event


def load_engine_log(folder: Path | None) -> dict[str, list[Container]]:
    """``{scan id: containers in start order}`` from every ``*.log`` in ``folder``.

    The lines are the engine worker's ``bomlens_container_start`` and
    ``bomlens_container_exit`` events. The runner runs one container at a time
    per scan, so an exit closes the last container that scan started.
    """
    out: dict[str, list[Container]] = {}
    if folder is None or not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.log")):
        for line in path.read_text(errors="replace").splitlines():
            event = _log_event(line)
            if event is None:
                continue
            scan_id, moment = event.get("scan_id"), _parse_time(event.get("timestamp"))
            if not isinstance(scan_id, str) or moment is None:
                continue
            containers = out.setdefault(scan_id, [])
            if event["event"] == "bomlens_container_start":
                match = _CONTAINER_LABEL.search(str(event.get("container") or ""))
                label = match.group(1) if match else str(event.get("stage") or "?")
                containers.append(Container(label=label, started=moment))
            elif containers and containers[-1].ended is None:
                containers[-1].ended = moment
                containers[-1].exit_code = event.get("exit_code")
    return out


def containers_for(capture: dict | None, log: dict[str, list[Container]]) -> list[Container]:
    """The engine containers of the capture's scan, matched by scan id."""
    if capture is None:
        return []
    scan_id = _scan(capture).get("id")
    return log.get(scan_id, []) if isinstance(scan_id, str) else []


def _secs(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _share(value: float | None, total: float | None) -> str:
    if value is None or not total:
        return "-"
    return f"{round(100 * value / total)}%"


@dataclass
class _Side:
    capture: dict | None
    windows: dict[str, Window]
    parents: dict[str, str]
    total: float | None
    containers: list[Container]

    @classmethod
    def of(cls, capture: dict | None, log: dict[str, list[Container]]) -> _Side:
        windows = (stage_windows(capture) if capture is not None else None) or {}
        span = scan_window(capture) if capture is not None else None
        return cls(
            capture=capture,
            windows=windows,
            parents=enclosing(windows),
            total=(span[1] - span[0]).total_seconds() if span else None,
            containers=containers_for(capture, log),
        )

    def stage_cells(self, stage: str) -> list[str]:
        if stage not in self.windows:
            return ["-", "-"]
        seconds = _seconds(*self.windows[stage])
        cell = _secs(seconds)
        if stage in self.parents:
            cell += f" (in {_cell(self.parents[stage])})"
        return [cell, _share(seconds, self.total)]

    def other_cells(self) -> list[str]:
        span = scan_window(self.capture) if self.capture is not None and self.windows else None
        seconds = uncovered_seconds(self.windows, span) if span else None
        return [_secs(seconds), _share(seconds, self.total)]

    def ordered(self) -> list[str]:
        return [n for n, _ in sorted(self.windows.items(), key=lambda kv: (kv[1][0], kv[0]))]


def _container_table(side: _Side) -> list[str]:
    lines = [
        "| Container | Seconds | Share of the scan | Seconds since the previous container |",
        "| --- | --- | --- | --- |",
    ]
    prep = side.windows.get("prep")
    previous: datetime | None = prep[0] if prep else None
    for c in side.containers:
        gap = None if previous is None else max((c.started - previous).total_seconds(), 0.0)
        seconds = _seconds(c.started, c.ended)
        lines.append(
            f"| {_cell(c.label)} | {_secs(seconds)} | {_share(seconds, side.total)} | "
            f"{_secs(gap)} |"
        )
        previous = c.ended
    return lines


def _timing_block(row: Row, log: dict[str, list[Container]]) -> list[str]:
    ip, bl = _Side.of(row.ip, log), _Side.of(row.bl, log)
    if not (ip.windows or bl.windows or ip.containers or bl.containers):
        return []
    lines = [f"### {row.name}", ""]
    if ip.windows or bl.windows:
        stages = ip.ordered() + [s for s in bl.ordered() if s not in ip.windows]
        lines += [
            "| Stage | In-process seconds | In-process share | BomLens seconds | BomLens share |",
            "| --- | --- | --- | --- | --- |",
        ]
        for stage in stages:
            cells = ip.stage_cells(stage) + bl.stage_cells(stage)
            lines.append(f"| {_cell(stage)} | " + " | ".join(cells) + " |")
        lines.append("| other | " + " | ".join(ip.other_cells() + bl.other_cells()) + " |")
        lines.append(
            f"| scan | {_secs(ip.total)} | {_share(ip.total, ip.total)} | "
            f"{_secs(bl.total)} | {_share(bl.total, bl.total)} |"
        )
        lines.append("")
    for label, side in (("In-process", ip), ("BomLens", bl)):
        if side.containers:
            lines += [f"{label} engine containers (worker log):", ""]
            lines += [*_container_table(side), ""]
    return lines


def timing_section(rows: list[Row], log: dict[str, list[Container]]) -> list[str]:
    """Per-fixture stage times, a section of its own after the summary."""
    blocks = [block for row in rows if (block := _timing_block(row, log))]
    out = ["## Stage timings", ""]
    if not blocks:
        return [*out, "No capture recorded stage timings.", ""]
    out += [
        "Seconds per stage from each scan row's `stage_timings`, and the share "
        "of the scan's start-to-end time. A stage marked `(in X)` ran inside "
        "stage X's window: BomLens reports it from its own step times, within "
        "the window TRUSCA recorded around the engine container, so it is not "
        "added to the other stages. `other` is scan time no stage covers. "
        "On the BomLens side `prep`, `cdxgen` and `scancode` are the windows "
        "TRUSCA records around the plan, stage 1 and stage 2 containers, image "
        "checks and pulls included; `scancode` also covers storing the result.",
        "",
        "The container table comes from the engine worker's log. The seconds "
        "since the previous container (for the first one, since the `prep` "
        "stage began) include checking for the next image and pulling it when "
        "it is missing; the log does not say whether a pull happened.",
        "",
    ]
    if any(r.kind == "container" for r in rows):
        out += [
            "A container scan has the stages `bootstrap`, `trivy`, `persist` and "
            "`finalize` on both engines. On the BomLens side `trivy` is the window "
            "around the one engine container, which reads the image from its "
            "registry, generates the SBOM and matches it; the engine reports "
            "`cdxgen` and `postprocess` from its own step times inside it. The "
            "container is labelled `image` in the container table.",
            "",
        ]
    for block in blocks:
        out += block
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _cell(value: object) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _pair(a: object, b: object) -> str:
    return f"{_cell(a)} / {_cell(b)}"


def _count(items: list[str] | None) -> str:
    return "n/a" if items is None else str(len(items))


def _side(capture: dict | None, fn) -> object:  # type: ignore[no-untyped-def]
    return "missing" if capture is None else fn(capture)


def _summary_line(row: Row) -> list[str]:
    ip, bl = row.ip, row.bl
    return [
        _cell(row.name),
        _cell(row.kind),
        _pair(_side(ip, component_count), _side(bl, component_count)),
        _count(row.only_bl_purls),
        _count(row.only_ip_purls),
        f"{_count(row.only_bl_licenses)} / {_count(row.only_ip_licenses)}",
        _pair(_side(ip, vulnerability_count), _side(bl, vulnerability_count)),
        f"{_count(row.only_bl_vulns)} / {_count(row.only_ip_vulns)}",
        _pair(_side(ip, status), _side(bl, status)),
        _pair(
            _side(ip, lambda c: ", ".join(degraded(c)) or "none"),
            _side(bl, lambda c: ", ".join(degraded(c)) or "none"),
        ),
        _pair(_side(ip, scan_seconds), _side(bl, scan_seconds)),
        _pair(_side(ip, gate), _side(bl, gate)),
    ]


_HEADER = [
    "Fixture",
    "Input",
    "Components (in-process / BomLens)",
    "PURLs only in BomLens",
    "PURLs only in in-process",
    "Licences only in BomLens / only in in-process",
    "Vulnerabilities (in-process / BomLens)",
    "Vulnerability IDs only in BomLens / only in in-process",
    "Scan status (in-process / BomLens)",
    "Degraded stages (in-process / BomLens)",
    "Scan seconds (in-process / BomLens)",
    "Gate (in-process / BomLens)",
]


def _list(title: str, items: list[str] | None) -> list[str]:
    if not items:
        return []
    lines = [f"- {title} ({len(items)}):"]
    lines += [f"  - `{item}`" for item in items[:LIST_CAP]]
    if len(items) > LIST_CAP:
        lines.append(f"  - and {len(items) - LIST_CAP} more")
    return lines


def _details(row: Row) -> list[str]:
    lines: list[str] = []
    for label, capture in (("in-process", row.ip), ("BomLens", row.bl)):
        if capture is None:
            lines.append(f"- No {label} capture.")
        elif "error" in capture:
            lines.append(f"- {label} capture failed: `{capture['error']}`")
        else:
            message = _scan(capture).get("error_message")
            if message:
                lines.append(f"- {label} scan error: `{message}`")
    lines += _list("PURLs only in BomLens", row.only_bl_purls)
    lines += _list("PURLs only in in-process", row.only_ip_purls)
    lines += _list("Licences only in BomLens (spdx|category|kind)", row.only_bl_licenses)
    lines += _list("Licences only in in-process (spdx|category|kind)", row.only_ip_licenses)
    lines += _list("Vulnerability IDs only in BomLens", row.only_bl_vulns)
    lines += _list("Vulnerability IDs only in in-process", row.only_ip_vulns)
    return lines


def _image_section(rows: list[Row]) -> list[str]:
    images = [r for r in rows if r.kind == "container"]
    if not images:
        return []
    out = [
        "## Container images",
        "",
        "Both engines read each image from its registry: the in-process worker "
        "has no Docker socket, so `trivy image` reads the registry, and the "
        "BomLens engine reads it with syft. Nothing is pulled into the runner's "
        "Docker beforehand, because neither engine would read it there; each "
        "scan's time includes downloading the image. Every reference is pinned "
        "to a digest, so both passes read the same image.",
        "",
        "| Image | Reference |",
        "| --- | --- |",
    ]
    out += [f"| {_cell(r.name)} | `{_cell(r.image)}` |" for r in images]
    return [*out, ""]


def render(
    rows: list[Row],
    trivy: dict[str, dict[str, dict | None]],
    engine_log: dict[str, list[Container]] | None = None,
) -> str:
    same = sum(1 for r in rows if not r.differs)
    out = [
        "# Golden shadow run: in-process vs BomLens",
        "",
        f"{len(rows)} fixtures, {same} without any difference in the columns below.",
        "",
        "Both passes ran on one stack and the BomLens stage read the in-process "
        "worker's Trivy database volume. `n/a` means the capture has no such "
        "field (a failed capture, or one without the shadow fields).",
        "",
    ]
    if trivy:
        out += [
            "## Trivy database",
            "",
            "| Shard | Before the in-process pass | After the BomLens pass | Same database |",
            "| --- | --- | --- | --- |",
        ]
        for shard, pair in sorted(trivy.items()):
            before, after = pair.get("before"), pair.get("after")
            out.append(
                f"| {_cell(shard)} | {_cell((before or {}).get('UpdatedAt'))} | "
                f"{_cell((after or {}).get('UpdatedAt'))} | "
                f"{'yes' if same_db(before, after) else 'NO'} |"
            )
        out.append("")
    out += _image_section(rows)
    out += [
        "## Summary",
        "",
        "| " + " | ".join(_HEADER) + " |",
        "| " + " | ".join("---" for _ in _HEADER) + " |",
    ]
    out += ["| " + " | ".join(_summary_line(r)) + " |" for r in rows]
    out.append("")
    out += timing_section(rows, engine_log or {})
    detail_rows = [(r, _details(r)) for r in rows]
    detail_rows = [(r, d) for r, d in detail_rows if d]
    if detail_rows:
        out += ["## Differences", ""]
        for row, lines in detail_rows:
            out += [f"### {row.name}", "", *lines, ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inprocess", required=True, type=Path)
    ap.add_argument("--bomlens", required=True, type=Path)
    ap.add_argument("--trivy-db", type=Path)
    ap.add_argument(
        "--engine-log",
        type=Path,
        help="folder of engine worker log excerpts (*.log): container start and exit lines",
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    inprocess, bomlens = load_captures(args.inprocess), load_captures(args.bomlens)
    if not inprocess and not bomlens:
        print("no shadow captures found on either side", file=sys.stderr)
        return 1
    report = render(
        compare_all(inprocess, bomlens),
        load_trivy(args.trivy_db),
        load_engine_log(args.engine_log),
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
