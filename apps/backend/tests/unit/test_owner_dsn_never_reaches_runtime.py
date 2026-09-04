# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Guard: the owner database credential never reaches a runtime container.

``test_prod_failclosed_secrets_compose_wiring`` checks that secrets a
container needs are wired in. This is its opposite, and the reasoning that
makes it worth having is in ``docker-compose.yml``: the runtime gets the
DML-only role so "a runtime RCE cannot DROP TRIGGER on audit_logs".

ER32 leans on that. The audit scrub is a ``SECURITY DEFINER`` function only
the table's owner may execute, and the application role is deliberately not
granted EXECUTE. All of which is decoration if the owner DSN is sitting in the
runtime container's environment, because anything that can read it can simply
connect as the owner and do whatever it likes, EXECUTE grant or not.

What actually holds the line is narrow and easy to undo by accident: no
compose file uses ``env_file``, so a variable in ``.env`` is visible to compose
interpolation and nothing else, and the ``x-backend-env`` anchor names its
keys one at a time. Add ``env_file: .env`` to a service for convenience, or
add the owner DSN to the anchor while debugging, and every value in the
operator's ``.env`` lands inside the container with no error anywhere.

These are static checks against the compose sources rather than a rendered
config, so they run in the PR gate without Docker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]

COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.dev.yml",
)

#: Credentials that must never appear in a container's environment. The owner
#: DSN is the one ER32 depends on; the raw postgres superuser password is here
#: because it reaches the same place by the same mistake.
FORBIDDEN_IN_RUNTIME = ("DATABASE_URL_OWNER", "POSTGRES_PASSWORD")

#: Services exempt from the check, and why. Everything else in the compose
#: file is checked, including services that do not exist yet.
#:
#: Listing the services to CHECK instead was the first version of this, and it
#: was close to inert: it named ``worker`` and ``beat`` while the real services
#: are ``worker-default``, ``worker-scan``, ``celery-worker`` and
#: ``celery-beat``, so the workers, the ones that run scanner subprocesses on
#: untrusted input, went unchecked. A list of things to check silently stops
#: covering whatever it does not name; a list of things to skip forces the next
#: service to be considered.
EXEMPT_SERVICES = {
    # The database server itself. POSTGRES_PASSWORD is what configures it, not
    # a credential it consumes, and it holds the data anyway.
    "postgres",
}


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    loaded: dict[str, Any] = yaml.safe_load(path.read_text())
    return loaded


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_no_service_pulls_the_whole_env_file(path: Path) -> None:
    """``env_file`` would hand every variable in ``.env`` to the container.

    Including ones nobody thought about when they added it. The allowlist in
    ``x-backend-env`` is what makes it possible to reason about what the
    runtime can see at all.
    """
    doc = _load(path)
    offenders = [
        name
        for name, service in (doc.get("services") or {}).items()
        if isinstance(service, dict) and "env_file" in service
    ]
    assert not offenders, (
        f"{path.name}: {offenders} use env_file, so every variable in the "
        "operator's .env now reaches those containers, including the owner "
        "database credential. Name the keys the service needs instead"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_no_service_receives_the_owner_credential(path: Path) -> None:
    """Every service the file declares, minus an explicit exemption.

    Read from the merged mapping rather than the anchor's text, so a service
    that adds the key beside ``<<: *backend-env`` is caught too.
    """
    doc = _load(path)
    services = doc.get("services") or {}
    checked = sorted(set(services) - EXEMPT_SERVICES)

    # A guard that checks nothing passes. Both compose files declare a backend,
    # so if it is not in the set, the derivation is broken rather than the
    # deployment being clean.
    assert "backend" in checked, (
        f"{path.name}: derived no backend service to check ({checked}); the "
        "guard is inspecting the wrong shape and would pass on anything"
    )

    for name in checked:
        service = services[name]
        if not isinstance(service, dict):
            continue
        env = service.get("environment") or {}
        keys = set(env) if isinstance(env, dict) else {
            str(entry).split("=", 1)[0] for entry in env
        }
        leaked = sorted(keys & set(FORBIDDEN_IN_RUNTIME))
        assert not leaked, (
            f"{path.name}: service '{name}' receives {leaked}. The runtime is "
            "supposed to hold the DML-only role; with the owner credential in "
            "its environment the append-only audit trail and the ER32 scrub "
            "function's EXECUTE restriction both stop meaning anything"
        )


def test_the_operator_command_reads_the_owner_dsn_not_a_new_variable() -> None:
    """One name for one credential.

    A second variable for the same secret is a second thing to keep out of the
    runtime, and the next person to wire compose only knows to exclude the
    ones they have heard of.
    """
    source = (
        REPO_ROOT / "apps" / "backend" / "scripts" / "anonymise_user.py"
    ).read_text()
    assert "DATABASE_URL_OWNER" in source
    assert "ANONYMISATION_DATABASE_URL" not in source, (
        "the anonymisation command invented its own name for the owner DSN; "
        "use DATABASE_URL_OWNER, which install.sh already writes and "
        "docker-compose already withholds from the runtime"
    )
