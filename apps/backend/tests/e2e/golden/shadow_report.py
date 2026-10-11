#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Difference table for a shadow run of the golden fixtures.

A shadow run (``golden-nightly.yml`` dispatched with ``shadow=true``) scans
every golden fixture twice on one stack: once with the in-process engine and
once with the BomLens engine, both reading the same Trivy database. Each pass
writes one ``run_golden.py --shadow-out`` capture per fixture. This script
reads the two folders and writes a Markdown report: one summary row per
fixture, then the items behind every difference.

It measures; it does not gate. The exit code is 1 only when there is nothing
to compare (no capture on either side), so a run that measured nothing does
not look like a run where both engines agreed.

stdlib only, like ``run_golden.py``.

Usage:
  python shadow_report.py --inprocess <dir> --bomlens <dir> \\
      [--trivy-db <dir>] [--out report.md]
"""

from __future__ import annotations

import argparse
import json
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
    names = sorted(set(inprocess) | set(bomlens))
    return [compare(n, inprocess.get(n), bomlens.get(n)) for n in names]


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


def render(rows: list[Row], trivy: dict[str, dict[str, dict | None]]) -> str:
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
    out += [
        "## Summary",
        "",
        "| " + " | ".join(_HEADER) + " |",
        "| " + " | ".join("---" for _ in _HEADER) + " |",
    ]
    out += ["| " + " | ".join(_summary_line(r)) + " |" for r in rows]
    out.append("")
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
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    inprocess, bomlens = load_captures(args.inprocess), load_captures(args.bomlens)
    if not inprocess and not bomlens:
        print("no shadow captures found on either side", file=sys.stderr)
        return 1
    report = render(compare_all(inprocess, bomlens), load_trivy(args.trivy_db))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
