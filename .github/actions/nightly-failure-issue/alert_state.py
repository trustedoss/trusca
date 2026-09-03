#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Track a scheduled workflow's failure history inside its alert issue body.

Why the state lives in the body
-------------------------------
The action is stateless between runs and the issue is the only thing that
persists, so the issue body carries a machine-readable marker alongside the
human-readable text. Reading it back is what lets the action tell a one-off
failure from an intermittent one.

Why that distinction matters (ER40)
-----------------------------------
The previous rule closed the issue on the first green run. Issue #224 recorded
six consecutive nightly e2e failures and was auto-closed by a single green run
on 2026-08-30; nobody had diagnosed them, and closing the issue removed the
only trail back to them. Intermittent failures are the hardest class to
diagnose and that rule erased them fastest.

So an issue that has recorded more than one failure is never auto-closed. It
gets a comment saying the run recovered and stays open for a person to close
once the cause is understood. A genuine one-off still closes by itself, which
is what keeps the alert from becoming noise.

Commands
--------
  record-failure <body-file> <out-file>   append this run, print the new total
  read-state      <body-file>             print the current total
  render-recovery <body-file> <out-file>  mark a recovery without closing

`<body-file>` may be missing or empty, which is how a brand new issue starts.
The total prints as `total=<n>` for the shell to read. Only the total decides
anything; how many of those failures were consecutive is left to the history
table, which carries a row per run. A separate streak counter would have to be
reset on a green run the action does not always write back, so it could show a
number that was quietly wrong.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# The marker is an HTML comment so it does not render, and it is the single
# source of truth: the table below it is redrawn from this on every write.
_MARKER_RE = re.compile(r"<!-- nightly-alert-state (?P<json>\{.*?\}) -->", re.S)

# Enough history to show a pattern without an unreadable issue body.
_MAX_HISTORY = 20


def _load(body: str) -> dict:
    match = _MARKER_RE.search(body)
    if not match:
        return {"total": 0, "history": []}
    try:
        state = json.loads(match.group("json"))
    except json.JSONDecodeError:
        # A hand-edited or truncated marker must not crash the alert; losing
        # the count is better than losing the notification.
        return {"total": 0, "history": []}
    state.setdefault("total", 0)
    state.setdefault("history", [])
    if not isinstance(state["history"], list):
        state["history"] = []
    return state


def _read_body(path: str) -> str:
    file = Path(path)
    if not file.is_file():
        return ""
    return file.read_text(encoding="utf-8")


def _history_table(history: list[dict]) -> str:
    if not history:
        return "_No runs recorded yet._"
    lines = [
        "| # | Run | Commit | Workflow | When |",
        "|---|---|---|---|---|",
    ]
    # Newest first: the run being investigated is the one at the top.
    for entry in reversed(history):
        lines.append(
            "| {n} | [`{run}`]({url}) | `{sha}` | `{wf}` | {when} |".format(
                n=entry.get("n", "?"),
                run=entry.get("run", "?"),
                url=entry.get("url", ""),
                sha=(entry.get("sha") or "")[:8],
                wf=entry.get("workflow", "?"),
                when=entry.get("when", "?"),
            )
        )
    return "\n".join(lines)


def _render(state: dict, *, recovered: bool, details: str, workflow: str) -> str:
    total = state["total"]

    if recovered:
        headline = (
            f"The scheduled run of `{workflow}` is green again, but this issue "
            "is deliberately left open."
        )
        why = (
            f"It recorded **{total} failures** before recovering, so a single "
            "green run does not establish that the cause is gone. An "
            "intermittent failure looks exactly like this. Close it by hand "
            "once the cause is understood, and say what it was."
        )
    else:
        headline = f"The scheduled run of `{workflow}` failed."
        why = (
            "A scheduled gate that stays red stops being a gate. Either fix "
            "the regression it found or fix the gate, then let the next "
            "scheduled run close this issue."
        )

    count_line = f"**{total} failure(s) recorded on this issue.**"

    marker = json.dumps(
        {"total": total, "history": state["history"]},
        separators=(",", ":"),
    )

    return "\n".join(
        [
            headline,
            "",
            count_line,
            "",
            details.strip(),
            "",
            why,
            "",
            "## Failure history",
            "",
            _history_table(state["history"]),
            "",
            f"<!-- nightly-alert-state {marker} -->",
            "",
            "*Auto-managed by `.github/actions/nightly-failure-issue`. It is "
            "updated in place while the run keeps failing. A single isolated "
            "failure closes itself on the next green run; anything that failed "
            "more than once stays open until a person closes it. Do not edit "
            "the title or the state comment.*",
        ]
    )


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2

    command = argv[1]
    state = _load(_read_body(argv[2]))
    workflow = os.environ.get("GITHUB_WORKFLOW", "workflow")
    details = os.environ.get("DETAILS", "")

    if command == "read-state":
        print(f"total={state['total']}")
        return 0

    if len(argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[3])

    if command == "record-failure":
        state["total"] += 1
        state["history"].append(
            {
                "n": state["total"],
                "run": os.environ.get("GITHUB_RUN_ID", "?"),
                "url": os.environ.get("RUN_URL", ""),
                "sha": os.environ.get("GITHUB_SHA", ""),
                "workflow": workflow,
                "when": os.environ.get("RUN_STARTED_AT", "?"),
            }
        )
        state["history"] = state["history"][-_MAX_HISTORY:]
        body = _render(state, recovered=False, details=details, workflow=workflow)
        out.write_text(body, encoding="utf-8")
        print(f"total={state['total']}")
        return 0

    if command == "render-recovery":
        # The total is never reset: it is what decides whether this issue may
        # ever close itself again.
        body = _render(state, recovered=True, details=details, workflow=workflow)
        out.write_text(body, encoding="utf-8")
        print(f"total={state['total']}")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
