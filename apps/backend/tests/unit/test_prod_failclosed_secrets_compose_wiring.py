# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Guard: every prod fail-closed secret is actually wired into the containers
that need it, not just documented in ``.env.example``.

Why this exists: A5 (concurrency-scaling-plan-2026-08-22.md §3.3) added
``core.config.api_key_hmac_secret()``, a NEW accessor that raises
``RuntimeError`` outside ``dev`` if its env var is unset -- but nothing
checked that ``API_KEY_HMAC_SECRET`` was actually passed through to the
backend container in ``docker-compose.yml`` / ``docker-compose.dev.yml``.
``tests/unit/test_config_key_contract.py`` only checks that a key the
backend reads is DOCUMENTED somewhere (``.env.example`` or the reference
page); it does not check that a running container ever SEES the value an
operator sets. A key can pass that contract test, be perfectly documented,
and still never reach the process that calls ``os.getenv`` for it, if
whoever wires the compose file forgets the line. This is exactly that gap
(security-reviewer finding on the A5 PR), generalised so the same defect
class does not recur for the next prod fail-closed secret.

Scope is deliberately narrow (self-hosted Docker Compose, not Helm): a
Helm-chart equivalent is a natural follow-up but is out of scope for this
guard, which mirrors what security-reviewer asked for on this PR.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.dev.yml",
)

ANCHOR_KEY = "x-backend-env"

# Env vars whose core.config accessor RAISES RuntimeError outside `dev` when
# unset (fail-closed), rather than silently deriving a weaker fallback or
# using an empty/placeholder value. Each entry names the accessor that
# enforces it, so the next person who adds one knows where to look and where
# to register it.
#
# When you add a new fail-closed secret accessor, add its env var here too --
# that is the whole point of this guard: a fail-closed accessor with a
# forgotten compose wiring fails at container startup in production, which is
# a worse time to discover it than a failing PR test.
PROD_FAILCLOSED_SECRETS: dict[str, str] = {
    "SECRET_KEY": "core.config.secret_key",
    "GITHUB_APP_ENCRYPTION_KEY": "core.crypto._resolve_fernet / _derive_key_from_secret",
    "API_KEY_HMAC_SECRET": "core.config.api_key_hmac_secret (A5)",
}


def _extract_anchor_block(path: Path, anchor_key: str) -> str:
    """Return the indented body of a top-level YAML anchor key as raw text.

    Deliberately NOT a full YAML parse: these compose files use `${VAR:-default}`
    interpolation syntax that is only meaningful to docker-compose, and PyYAML
    would either choke on it or silently pass it through as an opaque string --
    either way a full parse buys nothing here. A plain indentation-scoped
    text extraction is enough to answer "does this line exist under this
    anchor", which is all this guard needs.
    """
    lines = path.read_text().splitlines()
    block: list[str] = []
    in_block = False
    block_indent: int | None = None
    for line in lines:
        if not in_block:
            if line.startswith(f"{anchor_key}:"):
                in_block = True
            continue
        if line.strip() == "":
            block.append(line)
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if block_indent is None:
            block_indent = current_indent
        if current_indent < block_indent:
            break
        block.append(line)
    return "\n".join(block)


def test_x_backend_env_anchor_exists_in_both_compose_files() -> None:
    """Sanity check the extractor itself found something, not an empty
    string that would make every other assertion in this file vacuously
    pass."""
    for path in COMPOSE_FILES:
        assert path.exists(), f"expected {path} to exist"
        block = _extract_anchor_block(path, ANCHOR_KEY)
        assert block.strip() != "", (
            f"{path.name}: found no `{ANCHOR_KEY}:` anchor block, or it was empty -- "
            "either the anchor was renamed/removed, or this extractor's indentation "
            "logic no longer matches the file's shape"
        )


def test_every_prod_failclosed_secret_is_wired_into_backend_env() -> None:
    """Every secret in PROD_FAILCLOSED_SECRETS must appear as a `KEY:` line
    inside the `x-backend-env` anchor of BOTH compose files.

    A container that never receives the env var sees `os.getenv(...)`
    return None regardless of what the operator set in `.env` -- the
    accessor's fail-closed RuntimeError then fires at request time (or, for
    accessors called during startup, at boot) even when the operator did
    everything right on their end. Declaring-but-not-wiring is a silent
    trap: the deployment LOOKS configured (the key is in `.env`, documented,
    and `test_config_key_contract.py` is green) right up until the first
    request that needs it.
    """
    missing: list[str] = []
    for path in COMPOSE_FILES:
        block = _extract_anchor_block(path, ANCHOR_KEY)
        for env_var in PROD_FAILCLOSED_SECRETS:
            # Matches e.g. `  SECRET_KEY: ${SECRET_KEY}` or
            # `  API_KEY_HMAC_SECRET: ${API_KEY_HMAC_SECRET:-}` -- any value
            # shape is accepted here; THIS guard only checks the line exists,
            # not that the interpolation defaults are sane (that is a
            # separate concern, covered by the accessor's own tests and the
            # runtime RuntimeError itself).
            pattern = re.compile(rf"^\s*{re.escape(env_var)}:\s", re.MULTILINE)
            if not pattern.search(block):
                missing.append(f"{path.name}: {ANCHOR_KEY} is missing `{env_var}:`")

    assert missing == [], (
        "prod fail-closed secret(s) declared in PROD_FAILCLOSED_SECRETS are not "
        "wired into the backend container's environment:\n  " + "\n  ".join(missing) + "\n\n"
        "Add a line like `KEY_NAME: ${KEY_NAME:-}` under `x-backend-env` in the "
        "listed file(s). This is devops-engineer's file surface, not "
        "backend-developer's -- see CLAUDE.md's agent boundaries."
    )
