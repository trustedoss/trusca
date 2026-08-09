# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Every component a CycloneDX document declares, nesting included.

CycloneDX lets a component carry its own ``components`` array. It is not a
dependency edge — it means "this component contains these" — and generators do
emit it: an npm package whose bundled dependency is described inside it, a
container image describing its layers' packages, a firmware image describing
what a file contains.

TRUSCA read only the top-level array, in three places that each looked correct
on its own:

  * persistence dropped the nested entries, so they never appeared in the
    inventory;
  * conformance scored a denominator that excluded them, so a document could
    be reported as fully identified while carrying unidentified components;
  * worst, Trivy reads the file itself and CAN match a nested component, but
    ``persist_trivy_findings`` resolves each finding to a persisted
    ``ComponentVersion`` by PURL — and there was none, so the finding was
    dropped without a trace. A vulnerability that a scanner found and reported
    disappeared between two of our own stages.

Walking is depth-bounded and identity-guarded: the input is an
attacker-controllable document, and an unbounded walk over a self-referential
structure is a denial-of-service surface rather than a correctness question.
"""

from __future__ import annotations

from typing import Any

#: Matches the ingest service's nesting ceiling and the G7 evaluator's own
#: recursion guard. A document nested deeper than this is not a document any
#: generator produces.
MAX_NESTING_DEPTH = 64


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def iter_components(
    components: Any, *, max_depth: int = MAX_NESTING_DEPTH
) -> list[dict[str, Any]]:
    """Flatten ``components`` depth-first: each parent, then what it contains.

    Parents come before their children so that callers which assign an order
    (persistence writes rows in iteration order) keep the document's own
    reading order. Non-dict entries are skipped rather than raising — this runs
    on uploaded documents.

    The identity guard is against a structure that reaches itself, which JSON
    cannot express but a caller mutating a parsed document can create.
    """
    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(items: Any, depth: int) -> None:
        if depth > max_depth:
            return
        for item in _as_list(items):
            if not isinstance(item, dict):
                continue
            if id(item) in seen:
                continue
            seen.add(id(item))
            out.append(item)
            walk(item.get("components"), depth + 1)

    walk(components, 0)
    return out


def nested_count(components: Any) -> int:
    """How many of the flattened components came from nesting.

    Used where a caller wants to say so — a log line, a detail string — rather
    than leaving a reader to wonder why the count grew.
    """
    top = len([c for c in _as_list(components) if isinstance(c, dict)])
    return len(iter_components(components)) - top
