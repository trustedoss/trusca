"""
Configuration key contract: the settings the code reads, the template operators
copy, and the reference they read are held to the same list.

Why this file exists: an env key is declared in one place and consumed in
another, and nothing connected them. Auditing the three sides by hand found 35
keys the backend read that appeared in neither `.env.example` nor the reference
page, one key in the template that nothing had read since the script that used
it was deleted, and a section describing an overlay file that no longer exists.
None of that failed anything, because a key that is read but undeclared behaves
exactly like a key with a good default until an operator needs to change it.

The extraction is the part worth reviewing. Matching `os.getenv("K")` alone
undercounts badly: the settings module wraps its reads in typed helpers, so
`_int_env("SCAN_LOG_LINE_MAX_LEN", 4096)` looks like an ordinary call. This
walks the syntax tree instead and discovers the helpers rather than listing
them, by finding functions that pass their own first parameter into a direct
read. A helper added later is picked up without touching this file.

Two shapes cannot be read off the tree, so both are declared here and each
declaration names the code that justifies it. A key assembled at runtime
(`f"VULN_SLA_DAYS_{severity.upper()}"`) has no literal to find, and a key
forwarded to a child process is passed through rather than read. Anything
outside those two lists must be visible on all the sides that claim it, and a
guard that quietly skipped either shape would report a clean run over exactly
the drift it was built to catch.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_REFERENCE = REPO_ROOT / "docs-site" / "docs" / "reference" / "env-variables.md"

KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

# Keys whose names are assembled at runtime, so no literal exists to find.
# Each entry names the site that builds it.
DYNAMIC_KEY_PREFIXES = {
    # core/config.py: f"VULN_SLA_DAYS_{severity.upper()}" over the four
    # severities that carry an SLA. info / unknown deliberately have none.
    "VULN_SLA_DAYS_": ("CRITICAL", "HIGH", "MEDIUM", "LOW"),
}

# Declared, deliberately unread. The reference page marks each of these
# "Read by: (none)" and says why, which is the review this list defers to.
# Shipping them in the template keeps a deployment's .env valid when the
# feature lands, so they are declarations rather than drift.
DECLARED_UNREAD = {"JIRA_ENABLED", "JIRA_URL", "JIRA_TOKEN"}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _is_direct_env_read(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr == "getenv":
            return True
        is_environ = isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
        if func.attr == "get" and is_environ:
            return True
    return isinstance(func, ast.Name) and func.id == "getenv"


def _helper_names(tree: ast.AST) -> set[str]:
    """Functions that forward their own first parameter into a direct read."""
    helpers: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) or not fn.args.args:
            continue
        first_param = fn.args.args[0].arg
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and _is_direct_env_read(node)
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == first_param
            ):
                helpers.add(fn.name)
    return helpers


def _source_trees() -> dict[Path, ast.AST]:
    trees: dict[Path, ast.AST] = {}
    for path in BACKEND_ROOT.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        trees[path] = ast.parse(path.read_text())
    return trees


def _keys_read_by_backend() -> dict[str, set[str]]:
    trees = _source_trees()
    helpers: set[str] = set()
    for tree in trees.values():
        helpers |= _helper_names(tree)

    read: dict[str, set[str]] = {}
    for path, tree in trees.items():
        where = str(path.relative_to(BACKEND_ROOT))
        for node in ast.walk(tree):
            key: str | None = None
            if isinstance(node, ast.Subscript):
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "environ"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    key = node.slice.value
            elif isinstance(node, ast.Call) and node.args:
                callee = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", "")
                )
                if _is_direct_env_read(node) or callee in helpers:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        key = first.value
            if key and KEY_PATTERN.match(key):
                read.setdefault(key, set()).add(where)
    return read


def _keys_forwarded_to_subprocesses() -> set[str]:
    """Names in the subprocess passthrough allowlists: forwarded, never read."""
    forwarded: set[str] = set()
    for path, tree in _source_trees().items():
        if "_subprocess_env" not in path.name:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Set | ast.List | ast.Tuple):
                for element in node.elts:
                    if (
                        isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                        and KEY_PATTERN.match(element.value)
                    ):
                        forwarded.add(element.value)
    return forwarded


def _keys_in_env_example() -> set[str]:
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), re.M))


def _keys_in_reference() -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|", ENV_REFERENCE.read_text(), re.M)
    }


def _keys_used_by_infrastructure() -> str:
    """Compose files, install scripts and chart templates, as raw text."""
    chunks = [path.read_text() for path in REPO_ROOT.glob("docker-compose*.yml")]
    for base in (REPO_ROOT / "scripts", REPO_ROOT / "charts"):
        if base.exists():
            chunks += [
                path.read_text()
                for path in base.rglob("*")
                if path.is_file() and path.suffix in {".sh", ".yaml", ".yml", ".tpl"}
            ]
    return "\n".join(chunks)


def _dynamic_keys() -> set[str]:
    return {
        f"{prefix}{suffix}"
        for prefix, suffixes in DYNAMIC_KEY_PREFIXES.items()
        for suffix in suffixes
    }


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_every_key_the_backend_reads_is_declared_for_operators() -> None:
    """A key with no entry is a setting only its author knows exists."""
    declared = _keys_in_env_example() | _keys_in_reference()
    read = _keys_read_by_backend()
    undeclared = sorted(key for key in read if key not in declared)
    detail = [f"{key} ({sorted(read[key])[0]})" for key in undeclared]
    assert undeclared == [], (
        "the backend reads these keys but neither .env.example nor the "
        f"reference page mentions them: {detail}"
    )


def test_the_template_offers_nothing_that_disappeared() -> None:
    """The other direction: a key nothing consumes is a promise that expired.

    EVAL_URL survived here for exactly this reason. The script that read it was
    deleted, so setting it did nothing, and the section around it still
    described an overlay file that is no longer in the repository.
    """
    live = set(_keys_read_by_backend()) | _keys_forwarded_to_subprocesses() | _dynamic_keys()
    infrastructure = _keys_used_by_infrastructure()
    orphans = sorted(
        key
        for key in _keys_in_env_example()
        if key not in live and key not in DECLARED_UNREAD and key not in infrastructure
    )
    assert orphans == [], (
        ".env.example offers these keys but nothing reads them, and they are "
        f"not declared as reserved: {orphans}"
    )


def test_the_reference_documents_nothing_that_disappeared() -> None:
    """Same rule for the page operators are pointed at."""
    live = set(_keys_read_by_backend()) | _keys_forwarded_to_subprocesses() | _dynamic_keys()
    infrastructure = _keys_used_by_infrastructure()
    orphans = sorted(
        key
        for key in _keys_in_reference()
        if key not in live and key not in DECLARED_UNREAD and key not in infrastructure
    )
    assert orphans == [], (
        "the reference page documents these keys but nothing reads them: " f"{orphans}"
    )


def test_reserved_keys_stay_reserved() -> None:
    """A reserved key that gains a reader must lose its exemption.

    Otherwise the exemption outlives the reason for it, and the reference page
    keeps telling operators the setting does nothing after it starts working.
    """
    read = _keys_read_by_backend()
    now_live = sorted(key for key in DECLARED_UNREAD if key in read)
    assert now_live == [], (
        "these keys are declared reserved but the backend now reads them; "
        f"drop them from DECLARED_UNREAD and document the real behaviour: {now_live}"
    )


def test_dynamic_key_families_are_still_assembled_where_they_claim() -> None:
    """The declared escape hatch has to keep pointing at real code.

    A family listed here is invisible to the tree walk, so if the code that
    builds it is deleted or renamed, the keys become undetectable orphans that
    every other test in this file would wave through.
    """
    sources = "\n".join(
        path.read_text()
        for path in BACKEND_ROOT.rglob("*.py")
        if ".venv" not in path.parts and "tests" not in path.parts
    )
    missing = sorted(prefix for prefix in DYNAMIC_KEY_PREFIXES if f'f"{prefix}' not in sources)
    assert missing == [], (
        "no runtime-assembled read remains for these declared key families; "
        f"remove the entry or fix the site that built them: {missing}"
    )
