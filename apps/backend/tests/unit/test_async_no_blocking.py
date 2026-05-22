"""Static audit (Tier 3): no blocking call in an async coroutine's DIRECT body.

The fixtures e2e found the vulnerability-report PDF endpoint calling the
CPU-bound weasyprint render inline in an ``async def`` — which serialises every
concurrent request on that worker's event loop. This audit makes that whole bug
class a fast PR-gate failure instead of a load-test surprise.

Rule: inside an ``async def``, a call to a known-blocking callable is a
violation UNLESS it lives in a nested ``def`` / ``lambda`` (those run wherever
they're invoked — e.g. handed to ``run_in_threadpool`` / ``anyio.to_thread``,
which is exactly how the safe form is written: the blocking callable is passed
as a *name*, never *called*, in the coroutine body).

Scope: ``apps/backend/api`` + ``apps/backend/services`` (the request-path code).
Extend ``ALLOWLIST`` only with a written justification.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
TARGET_DIRS = ("api", "services")

# (receiver_name, attr) qualified calls that block the event loop.
QUALIFIED = {
    ("time", "sleep"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("tarfile", "open"),
    ("requests", "get"),
    ("requests", "post"),
    ("requests", "put"),
    ("requests", "delete"),
    ("requests", "patch"),
}
# Distinctive blocking method names (tarfile member I/O) — low false-positive.
METHOD_NAMES = {"getmembers", "extractfile", "extractall"}
# Heavy first-party callables that must be offloaded, never called inline.
BARE_NAMES = {"render_report_pdf", "_open_tarball", "run_cdxgen", "run_scancode"}

# file:func:callable that are knowingly acceptable (must carry a reason here).
ALLOWLIST: set[str] = set()


def _violation(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name) and (f.value.id, f.attr) in QUALIFIED:
            return f"{f.value.id}.{f.attr}"
        if f.attr in METHOD_NAMES:
            return f".{f.attr}"
    elif isinstance(f, ast.Name) and f.id in BARE_NAMES:
        return f.id
    return None


def _scan_coroutine_body(node: ast.AST, hits: list[tuple[int, str]]) -> None:
    """Walk an async function's body, collecting blocking calls, but NOT
    descending into nested function/lambda scopes (offload boundaries)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue  # separate scope (e.g. the sync fn handed to run_in_threadpool)
        if isinstance(child, ast.Call):
            v = _violation(child)
            if v:
                hits.append((child.lineno, v))
        _scan_coroutine_body(child, hits)


def _audit_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    rel = path.relative_to(BACKEND).as_posix()
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef):
            hits: list[tuple[int, str]] = []
            _scan_coroutine_body(n, hits)
            for lineno, callee in hits:
                key = f"{rel}:{n.name}:{callee}"
                if key not in ALLOWLIST:
                    out.append(f"{rel}:{lineno} async {n.name}() calls blocking {callee}")
    return out


def test_no_blocking_calls_in_async_bodies() -> None:
    violations: list[str] = []
    for d in TARGET_DIRS:
        for py in sorted((BACKEND / d).rglob("*.py")):
            violations.extend(_audit_file(py))
    assert not violations, (
        "Blocking call in an async coroutine body (offload via run_in_threadpool / "
        "anyio.to_thread, or move into a nested sync fn):\n  " + "\n  ".join(violations)
    )
