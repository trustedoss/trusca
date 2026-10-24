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

The second half of the file is about the step after declaration (#583). A key
in `.env.example` reaches a container only if `docker-compose.yml` names it in
that service's `environment`; compose does not read `env_file`. About forty
keys the backend reads were offered in the template and never passed, so
setting them did nothing. Every template key is now either passed or listed in
`NOT_PASSED_TO_CONTAINERS` with the reason it stays out, and each reason is
checked against the files it points at. Passing a key brings a second hazard:
`${KEY:-}` gives the container an empty string, not an unset variable, and
`os.getenv(KEY, default)` returns that empty string instead of the default.
So the value after `:-` must be the code's default, and a key with no default
in the code must be shown to treat an empty value as unset.
"""

from __future__ import annotations

import ast
import functools
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_REFERENCE = REPO_ROOT / "docs-site" / "docs" / "reference" / "env-variables.md"
COMPOSE_PROD = REPO_ROOT / "docker-compose.yml"
COMPOSE_DEV = REPO_ROOT / "docker-compose.dev.yml"

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
#
# JIRA_ENABLED / JIRA_URL / JIRA_TOKEN lived here as Phase B placeholders
# until #385 shipped ticket-status reads through a different mechanism
# (per-organization credentials stored via the admin API, not env vars), so
# they were removed from .env.example and the reference page rather than
# kept reserved for a shape the feature never took.
DECLARED_UNREAD: set[str] = set()


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


# Cached: parsing and walking the whole backend takes seconds, and several
# tests below ask the same question once per key. Nothing mutates the results.
@functools.cache
def _source_trees() -> dict[Path, ast.AST]:
    trees: dict[Path, ast.AST] = {}
    for path in BACKEND_ROOT.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        trees[path] = ast.parse(path.read_text())
    return trees


@functools.cache
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


# ---------------------------------------------------------------------------
# Which template keys reach the containers (#583)
# ---------------------------------------------------------------------------

# Template keys docker-compose.yml deliberately keeps out of every service's
# environment, by reason. Each reason is checked below against the files it
# names, so an entry cannot outlive the fact that justified it.
#
#   compose   - read by compose itself (`${KEY}` substitution for an image tag,
#               a replica count, a host path), never by the backend.
#   image     - set in the image at build time (Dockerfile `ENV` / `ARG`). A
#               compose entry would shadow the image's value: `${KEY:-}` with
#               the key unset hands the process an empty string.
#   host      - read by a script under scripts/ that runs on the host and
#               reads .env itself, not by any container.
#   dsn-part  - an alternative to DATABASE_URL for deployments that cannot
#               template a DSN (the Cloud Run module). Compose passes
#               DATABASE_URL, which the code prefers when both are set.
#   owner-dsn - the schema owner's DSN. install.sh and upgrade.sh hand it to
#               the one-off migration container; a running service never sees
#               it, so a compromise of one cannot rewrite the audit trail.
#   dev-only  - passed by docker-compose.dev.yml and not by the production
#               file, on purpose.
NOT_PASSED_TO_CONTAINERS: dict[str, str] = {
    "CELERY_CONCURRENCY": "compose",
    "CELERY_DEFAULT_CONCURRENCY": "compose",
    "COSIGN_KEYS_HOST_PATH": "compose",
    "DOMAIN": "compose",
    "IMAGE_REGISTRY": "compose",
    "IMAGE_TAG": "compose",
    "LOG_MAX_FILE": "compose",
    "LOG_MAX_SIZE": "compose",
    "REGISTRY_CONFIG_HOST_PATH": "compose",
    "TLS_EMAIL": "compose",
    "TRAEFIK_LOG_LEVEL": "compose",
    "WORKER_CPU_LIMIT": "compose",
    "WORKER_DEFAULT_CPU_LIMIT": "compose",
    "WORKER_DEFAULT_QUEUES": "compose",
    "WORKER_DEFAULT_REPLICAS": "compose",
    "WORKER_REPLICAS": "compose",
    "WORKER_SCAN_QUEUES": "compose",
    "CDXGEN_VERSION": "image",
    "EXTRACTCODE_LIBARCHIVE_PATH": "image",
    "TYPECODE_LIBMAGIC_PATH": "image",
    "TRUSTEDOSS_VERSION": "image",
    "TRUSTEDOSS_COMMIT": "image",
    "TRUSTEDOSS_BUILT_AT": "image",
    "BACKUP_RETENTION_DAYS": "host",
    "BACKUP_OFFSITE_REMOTE": "host",
    "BACKUP_OFFSITE_MAX_AGE": "host",
    "DB_USER": "dsn-part",
    "DB_PASSWORD": "dsn-part",
    "DB_HOST": "dsn-part",
    "DB_PORT": "dsn-part",
    "DB_NAME": "dsn-part",
    "DATABASE_URL_OWNER": "owner-dsn",
    # The limiter's kill switch. The template says to leave it unset in
    # production; not passing it is what makes that hold for a stack whose
    # .env is a copy of the whole template.
    "RATELIMIT_DISABLED": "dev-only",
}

_NOT_PASSED_REASONS = frozenset({"compose", "image", "host", "dsn-part", "owner-dsn", "dev-only"})

# Anchor keys whose compose default differs from the code's on purpose.
DELIBERATE_COMPOSE_DEFAULTS: dict[str, str] = {
    "APP_ENV": "the production file runs as prod; the code defaults to dev",
    "CORS_ALLOWED_ORIGINS": "required, no fallback (CLAUDE.md rule 13); install.sh writes it",
    "SCAN_BOMLENS_NETWORK": "names the egress network this compose file declares",
    "SCAN_WORKSPACE_MOUNT": "the workspace volume is mounted at /workspace here",
    "TRUSCA_INSTANCE_ID": "the code reads an empty value as 'default', the value given here",
    # Not settled: .env.example and core.config call the stage off by default,
    # and this file turns it on. Recorded so the guard holds for every other
    # key while that default is decided.
    "SCANCODE_ENABLED": "compose turns scancode on; the code and template default it off",
}


def _toolchain_cache_roots_default() -> str:
    from core import config

    return ",".join(config._DEFAULT_TOOLCHAIN_CACHE_ROOTS)


# Anchor keys whose code default is a constant rather than a literal at the
# read site, with the value the compose default must equal. An empty
# TOOLCHAIN_CACHE_ROOTS is not unset: it is an empty list, and the cache
# cleaner then reclaims nothing.
COMPOSE_DEFAULTS_FROM_CODE: dict[str, Callable[[], str]] = {
    "TOOLCHAIN_CACHE_ROOTS": _toolchain_cache_roots_default,
}


def _config(accessor: str) -> Callable[[], Any]:
    def read() -> Any:
        from core import config

        return getattr(config, accessor)()

    return read


def _gate(accessor: str, *args: str) -> Callable[[], Any]:
    def read() -> Any:
        from services import policy_gate

        return getattr(policy_gate, accessor)(*args)

    return read


def _comment_token(provider: str) -> Callable[[], Any]:
    def read() -> Any:
        from api.v1 import policy_gate

        # post_pr_comment tests `if not github_token`, so "" and None take the
        # same path there; the resolver returns whichever it read.
        return policy_gate._resolve_comment_token(provider) or None

    return read


def _portal_url() -> Any:
    import uuid

    from api.v1 import policy_gate

    return policy_gate._project_view_url(uuid.UUID(int=0))


def _gitlab_api_base() -> Any:
    from services import sca_comment

    return sca_comment.gitlab_api_base()


def _registries() -> Any:
    from services import registry_allowlist

    return registry_allowlist.allowed_registries()


def _osori_path() -> Any:
    from services.license_osori import osori_catalog

    return osori_catalog.osori_snapshot_path()


def _demo_password() -> Any:
    from scripts import seed_demo

    return seed_demo._resolve_demo_password()


# Anchor keys passed as `${KEY:-}` whose read site has no default to compare
# with: the code that reads each, and a value it visibly reacts to. The test
# below shows an empty value gives the same outcome as an unset one.
EMPTY_READS_AS_UNSET: dict[str, tuple[Callable[[], Any], str]] = {
    "API_KEY_HMAC_SECRET": (_config("api_key_hmac_secret"), "h" * 40),
    "COSIGN_KEY_PATH": (_config("cosign_key_path"), "/keys/cosign.key"),
    "COSIGN_KEY_PASSWORD_ENCRYPTED": (_config("cosign_key_password_encrypted"), "ciphertext"),
    "COSIGN_PUBLIC_KEY_PATH": (_config("cosign_public_key_path"), "/keys/cosign.pub"),
    "DEMO_SUPER_ADMIN_PASSWORD": (_demo_password, "a-demo-password-long-enough"),
    "GATE_EPSS_THRESHOLD": (_gate("_resolve_epss_threshold"), "0.5"),
    "GATE_REACHABLE_CRITICAL_ONLY": (_gate("_resolve_reachable_critical_only"), "true"),
    "GATE_EPSS_ON_MISSING_DATA": (_gate("_resolve_epss_on_missing_data"), "block"),
    "GATE_KEV_ENABLED": (_gate("_resolve_axis_enabled", "GATE_KEV_ENABLED"), "true"),
    "GATE_KEV_ON_MISSING_DATA": (
        _gate("_resolve_axis_on_missing_data", "GATE_KEV_ON_MISSING_DATA"),
        "block",
    ),
    "GATE_EOL_ENABLED": (_gate("_resolve_axis_enabled", "GATE_EOL_ENABLED"), "true"),
    "GATE_EOL_ON_MISSING_DATA": (
        _gate("_resolve_axis_on_missing_data", "GATE_EOL_ON_MISSING_DATA"),
        "block",
    ),
    "GATE_INCOMPLETE_SCAN_ENABLED": (
        _gate("_resolve_axis_enabled", "GATE_INCOMPLETE_SCAN_ENABLED"),
        "true",
    ),
    "GATE_INCOMPLETE_SCAN_ON_UNKNOWN": (
        _gate("_resolve_axis_on_missing_data", "GATE_INCOMPLETE_SCAN_ON_UNKNOWN"),
        "block",
    ),
    "GITHUB_TOKEN": (_comment_token("github"), "ghp_token"),
    "TRUSTEDOSS_GITHUB_TOKEN": (_comment_token("github"), "ghp_token"),
    "GITLAB_TOKEN": (_comment_token("gitlab"), "glpat_token"),
    "TRUSTEDOSS_GITLAB_TOKEN": (_comment_token("gitlab"), "glpat_token"),
    "GITLAB_API_BASE": (_gitlab_api_base, "https://gitlab.example/api/v4"),
    "PORTAL_PUBLIC_URL": (_portal_url, "https://portal.example"),
    "CONTAINER_SCAN_ALLOWED_REGISTRIES": (_registries, "registry.example"),
    "OSORI_SNAPSHOT_PATH": (_osori_path, "/data/osori.json"),
}

_COMPOSE_DEFAULT = re.compile(r"^\$\{(?P<key>[A-Z][A-Z0-9_]*)(?P<op>:-)?(?P<default>[^${}]*)\}$")
_DOCKERFILE_SETS = r"^(?:ENV|ARG)?\s*{key}="
_COMPUTED = "<computed>"


def _compose(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(path.read_text())
    return document


def _keys_passed_to_containers(path: Path) -> set[str]:
    return {
        key
        for service in (_compose(path).get("services") or {}).values()
        for key in ((service or {}).get("environment") or {})
    }


@functools.cache
def _code_defaults() -> dict[str, set[str | None]]:
    """For each key read by a direct call or a helper, the defaults it is read
    with: a literal as the string an env value would carry, None for no
    default, and ``_COMPUTED`` for an expression."""
    trees = _source_trees()
    helpers: set[str] = set()
    for tree in trees.values():
        helpers |= _helper_names(tree)
    defaults: dict[str, set[str | None]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            callee = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            first = node.args[0]
            if not (_is_direct_env_read(node) or callee in helpers):
                continue
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            given: ast.expr | None = node.args[1] if len(node.args) > 1 else None
            for keyword in node.keywords:
                if keyword.arg == "default":
                    given = keyword.value
            value: str | None
            if given is None:
                value = None
            elif isinstance(given, ast.Constant) and isinstance(given.value, bool):
                value = str(given.value).lower()
            elif isinstance(given, ast.Constant) and isinstance(given.value, str | int | float):
                value = str(given.value)
            else:
                value = _COMPUTED
            defaults.setdefault(first.value, set()).add(value)
    return defaults


def _literal_defaults(values: set[str | None]) -> set[str]:
    return {value for value in values if value is not None and value != _COMPUTED}


def _anchor_defaults() -> dict[str, str | None]:
    """Anchor keys written `${KEY:-default}` (the default) or `${KEY}` (None)."""
    found: dict[str, str | None] = {}
    for key, raw in (_compose(COMPOSE_PROD).get("x-backend-env") or {}).items():
        match = _COMPOSE_DEFAULT.match(str(raw))
        if match and match.group("key") == key:
            found[key] = match.group("default") if match.group("op") else None
    return found


def test_every_template_key_is_passed_or_declared_not_passed() -> None:
    template = _keys_in_env_example()
    passed = _keys_passed_to_containers(COMPOSE_PROD)
    declared = set(NOT_PASSED_TO_CONTAINERS)
    unclassified = sorted(template - passed - declared)
    assert not unclassified, (
        ".env.example offers these keys but docker-compose.yml passes them to no "
        "container, so setting them changes nothing. Add them to the service "
        "environment (with the code's default after `:-`), or to "
        f"NOT_PASSED_TO_CONTAINERS with the reason: {unclassified}"
    )
    both = sorted(passed & declared)
    assert not both, f"declared not passed, but docker-compose.yml passes them: {both}"
    gone = sorted(declared - template)
    assert not gone, f"NOT_PASSED_TO_CONTAINERS lists keys .env.example no longer offers: {gone}"


def _reason_problems(key: str, reason: str) -> list[str]:
    read = _keys_read_by_backend()
    host_scripts = "\n".join(p.read_text() for p in (REPO_ROOT / "scripts").glob("*.sh"))
    problems: list[str] = []
    if reason not in _NOT_PASSED_REASONS:
        problems.append(f"{key}: unknown reason {reason!r}")
    elif reason == "compose":
        if "${" + key not in COMPOSE_PROD.read_text():
            problems.append(f"{key}: docker-compose.yml does not substitute it")
        if key in read:
            problems.append(f"{key}: the backend reads it, so compose must pass it")
    elif reason == "image":
        dockerfiles = "\n".join(p.read_text() for p in BACKEND_ROOT.glob("Dockerfile*"))
        if not re.search(_DOCKERFILE_SETS.format(key=key), dockerfiles, re.M):
            problems.append(f"{key}: no Dockerfile sets it")
    elif reason == "host":
        if key not in host_scripts:
            problems.append(f"{key}: no host script reads it")
        if key in read:
            problems.append(f"{key}: the backend reads it, so compose must pass it")
    elif reason == "dsn-part":
        if key not in read or "DATABASE_URL" not in _keys_passed_to_containers(COMPOSE_PROD):
            problems.append(f"{key}: not a DSN part the code reads beside DATABASE_URL")
    elif reason == "owner-dsn":
        # The code reads it (core.config, for migrations), and a host script
        # takes it from .env to hand to the one-off migration container.
        if key not in read or f'"^{key}=' not in host_scripts:
            problems.append(f"{key}: not the owner DSN a host script hands to a migration")
    elif reason == "dev-only" and key not in _keys_passed_to_containers(COMPOSE_DEV):
        problems.append(f"{key}: docker-compose.dev.yml does not pass it either")
    return problems


def test_each_reason_for_not_passing_still_holds() -> None:
    problems = [
        problem
        for key, reason in sorted(NOT_PASSED_TO_CONTAINERS.items())
        for problem in _reason_problems(key, reason)
    ]
    assert not problems, problems


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("STALE_QUEUED_SCAN_GRACE_SECONDS", "compose"),
        ("GATE_KEV_ENABLED", "image"),
        ("LOGIN_THROTTLE_FAILURES", "host"),
        ("IMAGE_TAG", "dsn-part"),
        ("IMAGE_TAG", "owner-dsn"),
        ("GATE_KEV_ENABLED", "dev-only"),
        ("IMAGE_TAG", "forgotten"),
    ],
)
def test_a_wrong_reason_is_caught(key: str, reason: str) -> None:
    """Each reason check rejects a key it does not describe, so a reason that
    no longer holds cannot pass for the wrong cause."""
    assert _reason_problems(key, reason)


def test_compose_defaults_are_the_code_defaults() -> None:
    """`${KEY:-x}` is what the container gets when .env leaves KEY out, and the
    code's own default never applies once the variable exists."""
    code = _code_defaults()
    mismatched: list[str] = []
    for key, given in sorted(_anchor_defaults().items()):
        if key in DELIBERATE_COMPOSE_DEFAULTS:
            continue
        if key in COMPOSE_DEFAULTS_FROM_CODE:
            expected = COMPOSE_DEFAULTS_FROM_CODE[key]()
            if given != expected:
                mismatched.append(f"{key}: compose {given!r}, code {expected!r}")
            continue
        literals = _literal_defaults(code.get(key, set()))
        if literals and literals != {given}:
            mismatched.append(f"{key}: compose {given!r}, code {sorted(literals)}")
    assert not mismatched, (
        "these anchor defaults differ from the code's, so an operator who leaves "
        "the key out of .env gets something other than the documented default. "
        f"Use the code's default, or record why in DELIBERATE_COMPOSE_DEFAULTS: {mismatched}"
    )


def test_the_default_comparison_sees_known_defaults() -> None:
    """Without this, an extraction that found nothing would pass the test above."""
    code = _code_defaults()
    assert code["STALE_QUEUED_SCAN_GRACE_SECONDS"] == {"900"}
    assert code["LOGIN_THROTTLE_FAILURES"] == {"10"}
    assert code["GROUP_CASCADE_ENABLED"] == {"true"}
    assert code["GATE_KEV_ENABLED"] == {None}
    anchor = _anchor_defaults()
    assert anchor["STALE_QUEUED_SCAN_GRACE_SECONDS"] == "900"
    assert anchor["GATE_KEV_ENABLED"] == ""
    assert anchor["CORS_ALLOWED_ORIGINS"] is None


def test_deliberate_defaults_still_differ() -> None:
    """An exemption whose difference is gone would hide the next one."""
    code = _code_defaults()
    anchor = _anchor_defaults()
    stale = sorted(
        key
        for key in DELIBERATE_COMPOSE_DEFAULTS
        if key not in anchor or _literal_defaults(code.get(key, set())) == {anchor[key]}
    )
    assert not stale, f"no longer differ from the code, drop the exemption: {stale}"


def test_every_empty_default_without_a_code_default_is_checked() -> None:
    """Set equality between the anchor keys that hand the code an empty string
    where it has no default of its own, and the keys checked below."""
    code = _code_defaults()
    needing = {
        key
        for key, given in _anchor_defaults().items()
        if given == ""
        and key in code
        and not _literal_defaults(code[key])
        and key not in DELIBERATE_COMPOSE_DEFAULTS
    }
    assert set(EMPTY_READS_AS_UNSET) == needing, (
        f"unchecked: {sorted(needing - set(EMPTY_READS_AS_UNSET))}, "
        f"no longer passed empty: {sorted(set(EMPTY_READS_AS_UNSET) - needing)}"
    )


def _outcome(read: Callable[[], Any]) -> tuple[str, Any]:
    try:
        return ("value", read())
    except Exception as exc:  # noqa: BLE001 - the outcome is what is compared
        return ("raises", (type(exc).__name__, str(exc)))


@pytest.mark.parametrize("key", sorted(EMPTY_READS_AS_UNSET))
def test_an_empty_value_reads_as_unset(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    for other in EMPTY_READS_AS_UNSET:
        monkeypatch.delenv(other, raising=False)
    read, reacts_to = EMPTY_READS_AS_UNSET[key]
    unset = _outcome(read)
    monkeypatch.setenv(key, "")
    empty = _outcome(read)
    assert empty == unset, f"{key}: unset gives {unset}, empty gives {empty}"
    # The comparison means nothing unless this accessor reads this key.
    monkeypatch.setenv(key, reacts_to)
    assert _outcome(read) != unset, f"{key}: the accessor does not react to the key"
