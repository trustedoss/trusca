"""Static guard for the kill-without-wait subprocess pattern (D2).

Background (testing-hardening-plan-2026-08.md §1 Type D / §2 D2): a PoC
deployment surfaced a defect where ``integrations/_line_streamer.py``'s
streaming-path timeout branch called ``proc.kill()`` and then re-raised
``TimeoutExpired`` without ever calling ``proc.wait()`` / ``proc.poll()`` to
reap the killed child. That specific defect is fixed (the branch now calls
``proc.wait()`` right after ``proc.kill()``); ``test_line_streamer.py::
test_streaming_timeout_reaps_child_process`` pins the fixed behaviour. This
file guards the *source pattern* itself, so a future adapter that copies the
old shape (kill, then raise, no wait) gets caught by CI instead of by another
production incident.

Design
------
``_scan_file`` parses one module with :mod:`ast` and, per function scope
(nested ``def``/``async def``/``class`` bodies are their own scope and are
*not* descended into - they get analysed separately when the outer walk
reaches them), tracks:

  1. local names bound to a ``subprocess.Popen(...)`` (or bare ``Popen(...)``
     if imported by name) call.
  2. ``.kill()`` / ``.terminate()`` / ``.wait()`` / ``.poll()`` calls on those
     names, in source order.

For every ``.kill()`` / ``.terminate()`` call we look forward (by line
number, within the same function scope - this covers both "same except
block" and "later in the same function" per the plan) for a matching
``.wait()`` call on the same name. No match is a violation, unless the
call's line (or the line immediately above it) carries the escape-hatch
comment marker ``# subprocess-cleanup-guard: allow - <reason>`` - used for
cases where the process is genuinely fire-and-forget and reaping is not
required (e.g. a daemonised helper). There are no such cases in the
codebase today; the marker exists so a legitimate future exception does not
have to widen a file-level allowlist.

Scope: this repo's only production ``Popen`` users are
``apps/backend/integrations/`` and ``apps/backend/tasks/`` (verified below,
and independently confirmed by the plan's own grep). Restricting the scan to
those two directories keeps the AST analysis simple (no whole-repo alias /
control-flow tracking) without losing coverage - see the plan's D2 note
"AST 분석이 과한 정밀도를 요구하면 대상을 좁힌다".
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ["integrations", "tasks"]
ALLOW_MARKER = "subprocess-cleanup-guard: allow"
_KILL_ATTRS = {"kill", "terminate"}
_WAIT_ATTRS = {"wait", "poll"}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    var: str
    call: str

    def __str__(self) -> str:  # pragma: no cover - used only in failure text
        return f"{self.path}:{self.line} - {self.var}.{self.call}() has no following .wait()"


def _expr_key(node: ast.expr) -> str | None:
    """Return a stable dotted-name key for a ``Name`` or ``Attribute`` chain.

    ``proc`` -> ``"proc"``; ``self.proc`` -> ``"self.proc"``. Anything else
    (a call result, a subscript, ...) returns ``None`` and is ignored - we
    only track simple variable/attribute bindings, which is what every
    ``Popen`` call site in this codebase uses.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expr_key(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _is_popen_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "Popen"
    if isinstance(func, ast.Name):
        return func.id == "Popen"
    return False


def _own_scope_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield descendants of ``func`` without crossing into a nested scope.

    A nested ``def``/``async def``/``class``/``lambda`` is yielded (so the
    caller can see it exists) but not descended into - it is analysed on its
    own when the outer module walk reaches it independently.
    """
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _has_allow_marker(source_lines: list[str], lineno: int) -> bool:
    """Check the call's own line and the line above it for the escape hatch."""
    for candidate in (lineno, lineno - 1):
        if 1 <= candidate <= len(source_lines) and ALLOW_MARKER in source_lines[candidate - 1]:
            return True
    return False


def _scan_source(source: str, *, path_label: str) -> list[Violation]:
    """Run the kill-without-wait scan over one module's source text."""
    tree = ast.parse(source, filename=path_label)
    source_lines = source.splitlines()
    violations: list[Violation] = []

    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        popen_vars: set[str] = set()
        calls: list[tuple[int, str, str]] = []  # (lineno, var, attr)

        for node in _own_scope_nodes(func):
            if isinstance(node, ast.Assign) and _is_popen_call(node.value):
                for target in node.targets:
                    key = _expr_key(target)
                    if key is not None:
                        popen_vars.add(key)
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and _is_popen_call(node.value)
            ):
                key = _expr_key(node.target)
                if key is not None:
                    popen_vars.add(key)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                key = _expr_key(node.func.value)
                if key is not None and node.func.attr in (_KILL_ATTRS | _WAIT_ATTRS):
                    calls.append((node.lineno, key, node.func.attr))

        calls.sort(key=lambda c: c[0])

        for lineno, var, attr in calls:
            if attr not in _KILL_ATTRS or var not in popen_vars:
                continue
            if _has_allow_marker(source_lines, lineno):
                continue
            reaped = any(
                other_var == var and other_attr in _WAIT_ATTRS and other_line >= lineno
                for other_line, other_var, other_attr in calls
            )
            if not reaped:
                violations.append(
                    Violation(path=path_label, line=lineno, var=var, call=attr)
                )

    return violations


def _scan_file(path: Path) -> list[Violation]:
    return _scan_source(path.read_text(encoding="utf-8"), path_label=str(path))


def _scan_dirs(root: Path, dirnames: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for dirname in dirnames:
        directory = root / dirname
        for path in sorted(directory.rglob("*.py")):
            violations.extend(_scan_file(path))
    return violations


# ---------------------------------------------------------------------------
# Scanner correctness - synthetic snippets, independent of production state.
# These stay green regardless of when the real defect gets fixed; they pin
# the detector's behaviour, not the codebase's current contents.
# ---------------------------------------------------------------------------


def test_scanner_flags_kill_without_wait() -> None:
    src = textwrap.dedent(
        """
        import subprocess

        def run(cmd, timeout):
            proc = subprocess.Popen(cmd)
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise
        """
    )
    violations = _scan_source(src, path_label="synthetic.py")
    assert len(violations) == 1
    assert violations[0].var == "proc"
    assert violations[0].call == "kill"


def test_scanner_accepts_kill_followed_by_wait() -> None:
    src = textwrap.dedent(
        """
        import subprocess

        def run(cmd, timeout):
            proc = subprocess.Popen(cmd)
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
        """
    )
    assert _scan_source(src, path_label="synthetic.py") == []


def test_scanner_accepts_terminate_followed_by_wait() -> None:
    """``terminate()`` is also guarded; a later ``.wait()`` still satisfies it."""
    src = textwrap.dedent(
        """
        import subprocess

        def stop(proc):
            proc.terminate()
            proc.wait(timeout=5)
        """
    )
    assert _scan_source(src, path_label="synthetic.py") == []


def test_scanner_ignores_kill_on_non_popen_object() -> None:
    """``.kill()`` on something that was never assigned from ``Popen(...)``
    is out of scope - e.g. a domain object with an unrelated ``kill()``
    method. The scanner only tracks names it saw bound to ``Popen(...)``."""
    src = textwrap.dedent(
        """
        def stop(session):
            session.kill()
        """
    )
    assert _scan_source(src, path_label="synthetic.py") == []


def test_scanner_respects_allow_marker_on_call_line() -> None:
    src = textwrap.dedent(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(cmd)
            proc.kill()  # subprocess-cleanup-guard: allow - daemonised sidecar, no reap needed
        """
    )
    assert _scan_source(src, path_label="synthetic.py") == []


def test_scanner_respects_allow_marker_on_preceding_line() -> None:
    src = textwrap.dedent(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(cmd)
            # subprocess-cleanup-guard: allow - daemonised sidecar, no reap needed
            proc.kill()
        """
    )
    assert _scan_source(src, path_label="synthetic.py") == []


def test_scanner_does_not_cross_into_nested_function_scope() -> None:
    """A ``.wait()`` inside a nested function must not satisfy an outer
    ``.kill()`` - they are different call frames, so pairing them would hide
    a real leak (the outer ``kill()`` still runs without a matching wait in
    its own frame)."""
    src = textwrap.dedent(
        """
        import subprocess

        def run(cmd):
            proc = subprocess.Popen(cmd)

            def _cleanup_other_proc(other):
                other.wait()

            proc.kill()
        """
    )
    violations = _scan_source(src, path_label="synthetic.py")
    assert len(violations) == 1
    assert violations[0].var == "proc"


# ---------------------------------------------------------------------------
# Repo-wide gate - the actual D2 unit.
# ---------------------------------------------------------------------------


def test_scanned_dirs_exist_and_are_the_only_popen_users() -> None:
    """Guard the scan's own scope assumption.

    D2 restricts the AST scan to ``integrations/`` and ``tasks/`` (the plan's
    documented exception for keeping the analysis simple - §2 D2). This test
    keeps that restriction honest: if a third production directory starts
    using ``subprocess.Popen`` directly, this fails loudly instead of the
    scan silently missing it.
    """
    for dirname in SCANNED_DIRS:
        assert (BACKEND_ROOT / dirname).is_dir()

    excluded = {"tests", "alembic", ".venv", "__pycache__", "scripts"}
    production_dirs = [
        p
        for p in BACKEND_ROOT.iterdir()
        if p.is_dir() and p.name not in excluded and not p.name.startswith(".")
    ]
    popen_users = set()
    for directory in production_dirs:
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "subprocess.Popen(" in text or ("Popen(" in text and "import Popen" in text):
                popen_users.add(directory.name)
    assert popen_users <= set(SCANNED_DIRS), (
        f"a production directory outside {SCANNED_DIRS} now constructs "
        f"subprocess.Popen directly: {popen_users - set(SCANNED_DIRS)} - "
        "add it to SCANNED_DIRS in this file"
    )


def test_no_kill_without_wait_violations_in_scanned_dirs() -> None:
    violations = _scan_dirs(BACKEND_ROOT, SCANNED_DIRS)
    assert violations == [], (
        "kill-without-wait pattern found (add a "
        f"'# {ALLOW_MARKER} -- <reason>' comment if this is intentional): "
        + "; ".join(str(v) for v in violations)
    )
