# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Registry allow-list (ER3).

An allow-list that can be walked around is worse than none, because it is
believed. Most of this file is bypass attempts rather than happy paths.
"""

from __future__ import annotations

import pytest

from services.registry_allowlist import (
    DEFAULT_REGISTRY,
    DOCKER_HUB_AUTH_KEY,
    is_registry_allowed,
    is_registry_host_allowed,
    parse_allowed_registries,
    registry_auth_key,
    split_registry_host,
)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        # No registry segment: Docker resolves these to the Hub, so the host is
        # docker.io even though the string never says it.
        ("alpine", DEFAULT_REGISTRY),
        ("alpine:3.19", DEFAULT_REGISTRY),
        # First segment is a Hub NAMESPACE, not a host: no dot, no colon.
        ("myorg/app:1.0", DEFAULT_REGISTRY),
        ("library/nginx:1.27", DEFAULT_REGISTRY),
        # First segment looks like a host.
        ("ghcr.io/org/app:1", "ghcr.io"),
        ("registry.example.com/app", "registry.example.com"),
        # A port makes it a host even without a dot, and the port must not be
        # mistaken for a tag.
        ("registry:5000/app:1.0", "registry:5000"),
        ("localhost:5000/app", "localhost:5000"),
        ("localhost/app", "localhost"),
        # Case is not significant for a host.
        ("GHCR.IO/org/app", "ghcr.io"),
        # Unparseable input must not raise; the allow-list decides its fate.
        ("", DEFAULT_REGISTRY),
        ("   ", DEFAULT_REGISTRY),
    ],
)
def test_registry_host_is_parsed_the_way_docker_resolves_it(ref: str, expected: str) -> None:
    assert split_registry_host(ref) == expected


def test_an_empty_allowlist_allows_everything() -> None:
    """The behaviour every deployment has today. Upgrading must change nothing."""
    assert is_registry_allowed("ghcr.io/anything/at-all:1", ()) is True
    assert is_registry_allowed("evil.example.com/x", ()) is True


@pytest.mark.parametrize(
    ("ref", "allowed"),
    [
        ("ghcr.io/org/app:1", ("ghcr.io",)),
        ("alpine:3.19", ("docker.io",)),
        ("myorg/app", ("docker.io",)),
        ("registry.example.com/team/app", ("ghcr.io", "registry.example.com")),
        ("registry:5000/app", ("registry:5000",)),
        # Path prefix, exact and with a child path.
        ("ghcr.io/trustedoss", ("ghcr.io/trustedoss",)),
        ("ghcr.io/trustedoss/app:1", ("ghcr.io/trustedoss",)),
        # A trailing slash in the entry must not change the meaning.
        ("ghcr.io/trustedoss/app:1", ("ghcr.io/trustedoss/",)),
    ],
)
def test_allowed_references_pass(ref: str, allowed: tuple[str, ...]) -> None:
    assert is_registry_allowed(ref, allowed) is True


@pytest.mark.parametrize(
    ("ref", "allowed", "why"),
    [
        (
            "evil.example.com/ghcr.io/app",
            ("ghcr.io",),
            "the allowed host appears in the PATH, not as the host; a "
            "'does the string contain it' check would pass this",
        ),
        (
            "ghcr.io.evil.example.com/app",
            ("ghcr.io",),
            "a startswith on the host would pass this",
        ),
        (
            "notghcr.io/app",
            ("ghcr.io",),
            "an endswith on the host would pass this",
        ),
        (
            "ghcr.io/trustedoss-evil/app",
            ("ghcr.io/trustedoss",),
            "a path prefix must end on a segment boundary",
        ),
        (
            "registry.example.com/app",
            ("ghcr.io",),
            "a host that is simply not on the list",
        ),
        (
            "alpine:3.19",
            ("ghcr.io",),
            "an implicit Docker Hub reference must not slip through a list "
            "that does not include docker.io",
        ),
        (
            "myorg/app",
            ("myorg",),
            "'myorg' is a Hub namespace, not a host, so an entry naming it as "
            "a host must not match",
        ),
    ],
)
def test_bypass_attempts_are_rejected(ref: str, allowed: tuple[str, ...], why: str) -> None:
    assert is_registry_allowed(ref, allowed) is False, why


def test_parsing_the_operator_list() -> None:
    assert parse_allowed_registries(None) == ()
    assert parse_allowed_registries("") == ()
    assert parse_allowed_registries("  ") == ()
    assert parse_allowed_registries("ghcr.io") == ("ghcr.io",)
    # Whitespace and empty entries from a trailing comma must not become an
    # entry that matches the empty host.
    assert parse_allowed_registries(" ghcr.io , docker.io ,") == ("ghcr.io", "docker.io")


def test_an_empty_entry_never_matches() -> None:
    """A malformed list must fail closed, not open."""
    assert is_registry_allowed("ghcr.io/app", ("",)) is False
    assert is_registry_allowed("ghcr.io/app", ("/",)) is False


# --- Credential-store keys -------------------------------------------------
#
# A credential stored under the wrong key is not consulted, and the pull then
# proceeds anonymously and fails with a permission error. Nothing distinguishes
# that from a wrong password, which is why these are pinned rather than left to
# read like an implementation detail.


@pytest.mark.parametrize("host", ["docker.io", "index.docker.io", "DOCKER.IO"])
def test_docker_hub_credentials_are_keyed_by_the_legacy_v1_url(host: str) -> None:
    """Measured against Trivy 0.71.2: keyed by host, the credential is never
    sent (anonymous token, then `UNAUTHORIZED: authentication required`). Keyed
    by this URL the registry answers `incorrect username or password`, which is
    a credential it actually received."""
    assert registry_auth_key(host) == DOCKER_HUB_AUTH_KEY


@pytest.mark.parametrize(
    "host", ["ghcr.io", "registry.example.com", "registry:5000", "localhost:5000"]
)
def test_every_other_registry_is_keyed_by_its_own_host(host: str) -> None:
    """Not a blanket rewrite. `ghcr.io` keyed by its own host IS consulted, so
    rewriting everything would break the common case."""
    assert registry_auth_key(host) == host


# --- Host-level allow-list -------------------------------------------------


def test_a_path_scoped_entry_still_admits_its_host() -> None:
    """The regression this predicate exists for. A credential has only a host,
    and asking the reference-level predicate with a synthetic path appended
    made `ghcr.io/trustedoss` reject a credential for `ghcr.io` while happily
    allowing scans of `ghcr.io/trustedoss/app`."""
    allowed = ("ghcr.io/trustedoss",)
    assert is_registry_host_allowed("ghcr.io", allowed) is True
    # The two predicates must agree that this host is usable.
    assert is_registry_allowed("ghcr.io/trustedoss/app", allowed) is True


def test_the_host_predicate_still_refuses_a_host_that_is_not_listed() -> None:
    assert is_registry_host_allowed("evil.com", ("ghcr.io/trustedoss",)) is False


def test_the_host_predicate_does_not_match_on_a_prefix() -> None:
    """Same bypass the reference predicate guards: equality, never startswith."""
    assert is_registry_host_allowed("ghcr.io.evil.com", ("ghcr.io",)) is False


def test_an_empty_allow_list_admits_any_host() -> None:
    """Opt-in, exactly as the reference predicate treats it."""
    assert is_registry_host_allowed("anything.example.com", ()) is True
