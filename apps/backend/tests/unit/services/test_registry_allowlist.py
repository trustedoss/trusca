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
    is_registry_allowed,
    parse_allowed_registries,
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
