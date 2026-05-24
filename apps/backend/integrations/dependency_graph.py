"""
Dependency-graph parsing + depth computation (v2.2 2.2-a2).

cdxgen's CycloneDX SBOM carries a top-level ``dependencies`` array describing
the resolved dependency *graph*::

    "dependencies": [
        {"ref": "pkg:npm/app@1.0.0", "dependsOn": ["pkg:npm/a@1", "pkg:npm/b@2"]},
        {"ref": "pkg:npm/a@1", "dependsOn": ["pkg:npm/c@3"]},
        ...
    ]

This module turns that array into:

  * a normalized adjacency map (``parent ref → [child refs]``), and
  * a per-ref **depth** = shortest distance from a graph *root*, where a root is
    any node that no other node ``dependsOn`` (a direct dependency of the
    scanned project / a top-level). Direct deps get depth ``1``; their children
    ``2``; and so on. The scanned project's own metadata.component (which
    usually sits at the top of ``dependencies``) is treated as depth ``0`` so
    its immediate ``dependsOn`` children come out at depth ``1`` ("direct").

Why a separate, DB-free module
------------------------------
The graph is **untrusted input** (it is generated from attacker-controllable
source / lockfiles). Cycles, self-references, dangling refs, pathological depth
and giant fan-out must never hang or OOM the worker. Keeping the algorithm pure
(``dict``/``list`` in, ``dict`` out) lets us exercise every adversarial shape in
fast unit tests without a database, and lets ``tasks.scan_source`` consume the
result with a trivial ``ref → depth`` lookup.

Safety properties (all covered by adversarial unit tests):
  * **Cycle-safe** — BFS marks nodes ``visited`` on dequeue; a cycle
    (``A→B→A``) or self-ref (``A→A``) can never re-enqueue a settled node, so
    the traversal always terminates in O(V+E).
  * **Dangling-safe** — a ``dependsOn`` ref with no matching node (or no
    matching component in the SBOM) is recorded as an edge target but never
    invents a node; depth is only assigned to refs that actually appear.
  * **Bounded** — a hard ``MAX_DEPTH`` ceiling stops a multi-thousand-deep
    chain from producing an unbounded ``SmallInteger`` (and from spending
    unbounded time on a degenerate path); deeper nodes are clamped.
  * **Dedup** — duplicate edges and duplicate ``dependsOn`` entries collapse;
    the adjacency map stores each child once per parent.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

import structlog

log = structlog.get_logger("integrations.dependency_graph")

# Hard ceiling on computed depth. PostgreSQL stores ``depth`` in a SmallInteger
# (max 32767); a hostile / generated graph with a several-thousand-deep chain
# is clamped here so a single pathological path cannot blow the column or spend
# unbounded settle time. 64 is far beyond any real transitive tree (npm's
# deepest real-world graphs sit in the low double digits) while staying a tiny,
# safe integer. Nodes whose shortest path exceeds this are clamped to the cap.
MAX_DEPTH = 64

# Defensive cap on the number of distinct nodes we will BFS over. cdxgen emits
# one ``dependencies`` entry per resolved component; a real SBOM tops out in the
# low thousands. A hostile SBOM could declare an enormous synthetic graph; we
# bound the working set so depth computation stays O(V+E) with a known V. Beyond
# the cap we stop assigning depths (the extra nodes simply default to NULL/“not
# computed”), which degrades gracefully rather than letting the worker churn.
MAX_NODES = 100_000


def parse_dependency_graph(
    dependencies: Any,
) -> dict[str, list[str]]:
    """Normalize a CycloneDX ``dependencies`` array into an adjacency map.

    Args:
        dependencies: the raw ``sbom["dependencies"]`` value. Anything that is
            not a list of ``{"ref": str, "dependsOn": [str, ...]}`` dicts is
            skipped element-by-element — a non-list, a non-dict element, a
            missing / non-string ``ref``, or a non-list ``dependsOn`` all
            degrade to "no edges for that entry" rather than raising.

    Returns:
        ``{parent_ref: [child_ref, ...]}`` with:
          * every ``ref`` that appears (even with an empty ``dependsOn``) present
            as a key, so callers can distinguish "declared leaf" from "absent",
          * child refs de-duplicated per parent, order-preserved,
          * ``ref == child`` self-edges dropped (a node never depends on itself
            for depth purposes — keeping it would only add a no-op cycle).

    The function never raises on malformed input; it is the trust boundary
    between cdxgen's JSON and the depth algorithm.
    """
    adjacency: dict[str, list[str]] = {}
    if not isinstance(dependencies, list):
        return adjacency

    for entry in dependencies:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref:
            continue

        # Ensure the parent is a key even when it has no (or only invalid)
        # children — a declared root/leaf must still be discoverable.
        bucket = adjacency.setdefault(ref, [])
        if len(adjacency) > MAX_NODES:
            log.warning("dependency_graph_node_cap_exceeded", nodes=len(adjacency))
            break

        depends_on = entry.get("dependsOn")
        if not isinstance(depends_on, list):
            continue

        seen: set[str] = set(bucket)
        for child in depends_on:
            if not isinstance(child, str) or not child:
                continue
            if child == ref:
                # Self-reference (A→A): a no-op self-cycle. Drop it.
                continue
            if child in seen:
                continue  # Duplicate edge — collapse.
            seen.add(child)
            bucket.append(child)

    return adjacency


def compute_depths(
    adjacency: dict[str, list[str]],
    *,
    root_refs: Iterable[str] | None = None,
) -> dict[str, int]:
    """Compute the shortest-path depth of every node from a graph root.

    Definitions:
      * A **root** is a node that no other node depends on (in-degree 0). Roots
        model the scanned project's top-level / direct entry points. When
        ``root_refs`` is given (e.g. the SBOM ``metadata.component`` bom-ref),
        those nodes are forced to be roots; their *children* become the direct
        dependencies.
      * **depth** is the number of edges on the shortest path from any root.
        - An explicitly-supplied root (``root_refs``) is depth ``0``; its
          direct dependencies are depth ``1`` (matching ``ScanComponent.direct``
          := ``depth == 1``), transitive deps ``2+``.
        - When no ``root_refs`` is given, the in-degree-0 nodes are the roots
          and are themselves depth ``1`` (they ARE the direct dependencies —
          there is no synthetic project node above them).

    The traversal is a multi-source BFS that:
      * marks each node visited on *dequeue* (cycle/self-ref safe — a node
        already settled is never re-enqueued, so ``A→B→A`` terminates),
      * clamps any depth at :data:`MAX_DEPTH`,
      * stops enqueuing children once a node is at the cap (no point going
        deeper than we will record),
      * never assigns a depth to a *dangling* child ref that is not itself a
        node key (the edge target does not exist as a component) — such refs
        simply do not appear in the result.

    Returns:
        ``{ref: depth}`` for every reachable node, depth in ``[0, MAX_DEPTH]``.
        Unreachable nodes (orphan cycles with no in-degree-0 entry point — e.g.
        a pure ``A→B→A`` island) are seeded from an arbitrary deterministic
        member so they still receive a (non-NULL) depth instead of being
        silently dropped.
    """
    if not adjacency:
        return {}

    forced_roots = {r for r in (root_refs or ()) if isinstance(r, str) and r in adjacency}

    depths: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque()

    if forced_roots:
        for r in sorted(forced_roots):
            queue.append((r, 0))
    else:
        # Roots = in-degree-0 nodes. Compute the set of refs that are some
        # other node's child; the complement (among declared nodes) are roots.
        has_parent: set[str] = set()
        for children in adjacency.values():
            has_parent.update(children)
        roots = sorted(ref for ref in adjacency if ref not in has_parent)
        # Implicit roots are themselves the direct dependencies → depth 1.
        for r in roots:
            queue.append((r, 1))

    while queue:
        ref, depth = queue.popleft()
        if ref in depths:
            # Already settled at an equal-or-shorter depth (BFS guarantees the
            # first dequeue is the shortest). Cycle/diamond re-entry stops here.
            continue
        depths[ref] = depth
        if depth >= MAX_DEPTH:
            # At the cap — do not descend further (bounded traversal).
            continue
        for child in adjacency.get(ref, ()):  # dangling child → no node → skip
            if child in adjacency and child not in depths:
                queue.append((child, depth + 1))

    # Orphan islands: nodes only reachable through a cycle that has no
    # in-degree-0 entry (pure A→B→A with no external parent). Seed each
    # remaining unsettled node deterministically so it still gets a depth
    # rather than NULL. We treat such a seed as depth 1 (best-effort "direct"),
    # which is conservative — we never claim a node is deeper than we can prove.
    if len(depths) < len(adjacency):
        for ref in sorted(adjacency):
            if ref in depths:
                continue
            queue.append((ref, 1))
            while queue:
                node, depth = queue.popleft()
                if node in depths:
                    continue
                depths[node] = depth
                if depth >= MAX_DEPTH:
                    continue
                for child in adjacency.get(node, ()):
                    if child in adjacency and child not in depths:
                        queue.append((child, depth + 1))

    return depths


def graph_depths_from_sbom(sbom: dict[str, Any]) -> dict[str, int]:
    """Convenience: parse ``sbom["dependencies"]`` and compute per-ref depths.

    The SBOM ``metadata.component.bom-ref`` (the scanned project itself), when
    present and a node in the graph, is used as the forced root (depth 0) so its
    immediate children come out as depth-1 *direct* dependencies. When it is
    absent / not a node, we fall back to in-degree-0 root detection.

    Returns ``{bom_ref: depth}``; an empty dict when there is no usable graph.
    """
    adjacency = parse_dependency_graph(sbom.get("dependencies"))
    if not adjacency:
        return {}

    root_refs: list[str] = []
    metadata = sbom.get("metadata")
    if isinstance(metadata, dict):
        component = metadata.get("component")
        if isinstance(component, dict):
            root_ref = component.get("bom-ref") or component.get("purl")
            if isinstance(root_ref, str) and root_ref:
                root_refs.append(root_ref)

    return compute_depths(adjacency, root_refs=root_refs or None)


__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "compute_depths",
    "graph_depths_from_sbom",
    "parse_dependency_graph",
]
