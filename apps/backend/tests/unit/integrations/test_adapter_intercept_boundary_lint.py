"""
Lint: adapter unit tests must intercept the shared streaming helper boundary,
not ``subprocess.run`` directly (testing-hardening-plan-2026-08.md, Wave 2, F1).

Why this exists
----------------
``run_with_line_streaming`` falls back to a bare ``subprocess.run`` only when
``line_callback`` is ``None`` (see ``_line_streamer.py``); every production
caller of the adapters below always passes a callback (``tasks/scan_source.py``,
``tasks/scan_container.py``, ``tasks/ingest_sbom.py``), so production NEVER
takes that fast path. A unit test that patches
``integrations.<module>.subprocess.run`` and drives the adapter through that
fast path exercises a branch production never runs, and that gap is exactly
where the F1 defects (a missing ``--skip-db-update`` argv flag, a
kill-without-wait resource leak) survived every existing test before this
file existed. See ``test_external_tool_argv_contract.py``'s module docstring
for the full write-up of the same principle applied to the argv-contract
suite.

Scope: module-mixed adapters
-----------------------------
This test is stricter than "ban the string
``integrations.<module>.subprocess.run`` anywhere in this directory" because
some adapter modules mix both call shapes. ``integrations/trivy.py`` has
``run_trivy_image`` / ``run_trivy_sbom`` (routed through the streaming
helper) alongside ``download_db_only`` (a direct ``subprocess.run`` call;
there is no line-by-line progress consumer for a DB download). Patching
``integrations.trivy.subprocess.run`` to test ``download_db_only``, the way
``test_external_tool_argv_contract.py`` does, is the CORRECT interception
point for that function and must not be flagged.

So detection here is at (test/fixture function, adapter call) granularity:
build a census of which top-level ``integrations/*.py`` functions call
``run_with_line_streaming`` in production (AST walk, mirrors the census in
``test_external_tool_argv_contract.py``), then walk every ``test_*.py`` file
in this directory and flag a function that BOTH patches
``integrations.<module>.subprocess.run`` AND calls one of that module's
helper-routed functions. A module that never calls
``run_with_line_streaming`` at all (``cosign.py``, ``govulncheck.py``) never
enters the census, so their tests are free to patch ``subprocess.run``, the
approved (and only) seam for those two.

This granularity is a deliberate trade-off: it will not catch a test that
patches the boundary in one helper function (e.g. a pytest fixture) and
calls the adapter in a different function that consumes the fixture, without
either function containing both halves. Every violation this file was
written to catch, and every one found while landing F1, had the patch and
the adapter call in the same function body, so this is the right amount of
analysis for the cost.
"""

from __future__ import annotations

import ast
from pathlib import Path

_INTEGRATIONS_DIR = Path(__file__).resolve().parents[3] / "integrations"
_THIS_DIR = Path(__file__).resolve().parent
_THIS_FILE_NAME = Path(__file__).name


def _helper_using_functions_by_module() -> dict[str, set[str]]:
    """AST-walk ``integrations/*.py`` (top level only, mirroring the census
    scope in ``test_external_tool_argv_contract.py``, which excludes
    ``scan_executor/`` and ``_line_streamer.py`` itself).

    Returns ``{"integrations.<module>": {function_name, ...}}`` for every
    top-level function whose body contains a direct call to
    ``run_with_line_streaming(``. A module with no such function (cosign,
    govulncheck) is simply absent from the result.
    """
    result: dict[str, set[str]] = {}
    for path in sorted(_INTEGRATIONS_DIR.glob("*.py")):
        if path.name == "_line_streamer.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        funcs: set[str] = set()
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "run_with_line_streaming"
                ):
                    funcs.add(node.name)
                    break
        if funcs:
            result[f"integrations.{path.stem}"] = funcs
    return result


def _patched_subprocess_run_modules(node: ast.AST) -> set[str]:
    """Every ``integrations.<module>`` targeted by a
    ``monkeypatch.setattr("integrations.<module>.subprocess.run", ...)``
    string literal inside ``node``."""
    targets: set[str] = set()
    for inner in ast.walk(node):
        if not (isinstance(inner, ast.Call) and inner.args):
            continue
        func = inner.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
            continue
        first_arg = inner.args[0]
        if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
            continue
        target = first_arg.value
        if target.startswith("integrations.") and target.endswith(".subprocess.run"):
            targets.add(target[: -len(".subprocess.run")])
    return targets


def _called_function_names(node: ast.AST) -> set[str]:
    """Every bare/attribute call name invoked inside ``node``; we only need
    the tail identifier to correlate against the helper-function census."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _violations_in_file(path: Path, helper_funcs: dict[str, set[str]]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        patched_modules = _patched_subprocess_run_modules(node)
        if not patched_modules:
            continue
        called = _called_function_names(node)
        for module_path in patched_modules:
            wanted = helper_funcs.get(module_path)
            if not wanted:
                continue  # module never routes through the helper at all
            hit = called & wanted
            if hit:
                found.append(
                    f"{path.name}::{node.name} patches {module_path}.subprocess.run "
                    f"directly but also calls {sorted(hit)}, which routes through "
                    f"run_with_line_streaming in production; patch "
                    f"{module_path}.run_with_line_streaming instead"
                )
    return found


def test_adapter_tests_intercept_the_streaming_helper_not_subprocess_run() -> None:
    """Guards the F1 interception-boundary fix against regressing.

    A new adapter unit test (or a new function added to an existing one)
    that patches ``integrations.<module>.subprocess.run`` while calling a
    function this module's own production code routes through
    ``run_with_line_streaming`` fails here, before it can hide a bug in a
    branch production never executes.
    """
    helper_funcs = _helper_using_functions_by_module()
    assert helper_funcs, "sanity: expected at least one helper-using adapter module"

    violations: list[str] = []
    for path in sorted(_THIS_DIR.glob("test_*.py")):
        if path.name == _THIS_FILE_NAME:
            continue
        violations.extend(_violations_in_file(path, helper_funcs))

    assert not violations, (
        "adapter unit tests must patch integrations.<module>.run_with_line_streaming, "
        "not subprocess.run, for any function that routes through the shared "
        "streaming helper in production (testing-hardening-plan-2026-08.md F1):\n"
        + "\n".join(violations)
    )


def test_helper_using_module_census_excludes_cosign_and_govulncheck() -> None:
    """Pins the specific carve-out this file's docstring documents: cosign
    and govulncheck never route through ``run_with_line_streaming``, so
    their tests must remain free to patch ``subprocess.run`` directly."""
    helper_funcs = _helper_using_functions_by_module()
    assert "integrations.cosign" not in helper_funcs
    assert "integrations.govulncheck" not in helper_funcs
    # And the modules this unit actually protects ARE present.
    assert "integrations.trivy" in helper_funcs
    assert "integrations.cdxgen" in helper_funcs
    assert "integrations.scancode" in helper_funcs
    assert "integrations.scanoss" in helper_funcs
