#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""soak-metrics store, a SQLite time-series log of /metrics scrapes.

self-resource-validation-plan-2026-08-30.md §6-6: the portal's /metrics
endpoint is a snapshot (services/metrics_service.py's 7 always-on gauges
plus 2 behind QUEUE_BACKLOG_METRICS_ENABLED), off by default and with
nothing scraping it even when turned on. S4's soak instance is meant to run
for 90 days, and "observe" means having a trend to look at afterward, not
just an endpoint that could in principle answer a probe during that window.

Raw scrape text is stored as-is, one row per attempt, rather than parsed
into per-metric columns at write time: this collector has to survive
running unattended for 90 days, and re-parsing the Prometheus text format
into a schema every time a new series gets added (metrics_service.py's own
module docstring says series get added one at a time) would mean this
store's schema drifts out of sync with that module's contract file. Parsing
happens at read time instead, in report.py, against whatever series are
actually present in a given scrape.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrapes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT NOT NULL,
    status_code INTEGER,
    body TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_scrapes_scraped_at ON scrapes(scraped_at);
"""

# `metric_name{label="value",...} 12.34` or `metric_name 12.34`, skipping
# comment (#) lines. Prometheus text exposition format, the subset this
# module's own render_metrics() ever writes (no exponent notation, no NaN --
# but the value group accepts both since a future series could).
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>-?[0-9.]+(?:[eE][+-]?[0-9]+)?)\s*$"
)
_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


class Sample(NamedTuple):
    name: str
    labels: dict[str, str]
    value: float


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def record_scrape(
    conn: sqlite3.Connection,
    *,
    status_code: int | None,
    body: str | None,
    error: str | None,
) -> int:
    scraped_at = datetime.now(timezone.utc).isoformat()
    with closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO scrapes (scraped_at, status_code, body, error) VALUES (?, ?, ?, ?)",
            (scraped_at, status_code, body, error),
        )
        conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid


def parse_samples(body: str) -> list[Sample]:
    """Every gauge sample in one scrape's raw text, HELP/TYPE lines skipped."""
    samples: list[Sample] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if not match:
            continue
        labels = dict(_LABEL_RE.findall(match.group("labels") or ""))
        samples.append(Sample(match.group("name"), labels, float(match.group("value"))))
    return samples


def iter_scrapes(
    conn: sqlite3.Connection, *, since: str | None = None, until: str | None = None
) -> Iterator[sqlite3.Row]:
    query = "SELECT * FROM scrapes WHERE 1=1"
    params: list[str] = []
    if since:
        query += " AND scraped_at >= ?"
        params.append(since)
    if until:
        query += " AND scraped_at <= ?"
        params.append(until)
    query += " ORDER BY scraped_at ASC"
    yield from conn.execute(query, params)


def known_metric_names(conn: sqlite3.Connection) -> list[str]:
    names: set[str] = set()
    for row in conn.execute("SELECT body FROM scrapes WHERE body IS NOT NULL"):
        for sample in parse_samples(row["body"]):
            names.add(sample.name)
    return sorted(names)
