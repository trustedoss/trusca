# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The certificate variables the guide puts in ``.env`` reach the containers that
make outbound connections, and the proxy variables deliberately do not come
from there (#571).

The private-CA page told operators to set ``SSL_CERT_FILE`` and friends in
``.env``, and ``integrations._subprocess_env`` forwards all of them to the
scanners. None of that mattered: ``docker-compose.yml`` has no ``env_file``, so
a value in ``.env`` reaches compose interpolation and nothing else, and no
service listed these names. ``test_config_key_contract`` was green throughout,
because it checks that a key is documented, not that a container sees it.

Why the certificate values have to be absent, not empty
-------------------------------------------------------
The repo's usual ``KEY: ${KEY:-}`` would have been the wrong fix. It hands an
unset variable to the container as an empty string, and two of these names
treat an empty string as a setting. Measured on the host, not assumed:
``GIT_SSL_CAINFO=`` makes ``git ls-remote`` exit 128 with "error setting
certificate verify locations", and ``SSL_CERT_FILE=`` takes Python's default
SSL context from 197 trusted authorities to 0. The allowlist forwards an empty
value as readily as a real one. So the anchor lists each name with no value:
compose then copies the shell or ``.env`` value when there is one and sends a
bare name otherwise, which the Docker API treats as "remove from the
environment". That is why the assertions below demand ``None`` and not merely
presence.

Why the proxy names must stay out of the anchor
-----------------------------------------------
The same value-less form is a regression for the proxy names. docker-compose
1.29.2 (``Service._get_container_create_options``) builds the container
environment as ``merge_environment(self._parse_proxy_config(), service_env)``:
the ``proxies`` block of the Docker client's ``~/.docker/config.json``, which
is Docker's documented way to give containers a proxy, goes underneath the
service's own environment. A value-less ``HTTP_PROXY:`` that is unset in the
shell and ``.env`` resolves to ``None``, overrides the injected value, and is
sent as a bare name, which removes the proxy config.json supplied. Hosts that
configured their proxy that way would lose it on upgrade. So proxies for
compose are documented as config.json or an override file with explicit
values, and the guide must not tell operators to put them in ``.env``.

What is read rather than typed here
-----------------------------------
Both sets come from ``_BASE_ALLOWLIST``, and every non-basic name in it has to
fall into one of them, so a name the scanners start forwarding has to be
classified here. The names the guide tells operators to set come from the
guide's own shell blocks.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest
import yaml

from integrations._subprocess_env import _BASE_ALLOWLIST

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
COMPOSE_PROD = REPO_ROOT / "docker-compose.yml"
COMPOSE_DEV = REPO_ROOT / "docker-compose.dev.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GUIDE_EN = REPO_ROOT / "docs-site" / "docs" / "admin-guide" / "private-ca.md"
GUIDE_KO = (
    REPO_ROOT
    / "docs-site"
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "admin-guide"
    / "private-ca.md"
)
ANCHOR_KEY = "x-backend-env"

#: Every service that makes outbound connections, per compose file. The
#: frontend, Redis, Postgres, Traefik and the Docker API proxy are absent on
#: purpose: none of them runs the scanners or the portal's HTTP client.
OUTBOUND_SERVICES = {
    COMPOSE_PROD: ("backend", "worker-scan", "worker-default", "beat", "worker-engine"),
    COMPOSE_DEV: ("backend", "celery-worker", "celery-beat"),
}

#: Process basics in the allowlist that the image or runtime sets, not the
#: operator. Everything else in it is a trust or proxy setting.
_PROCESS_BASICS = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TZ"})

CA_VARIABLES = frozenset(
    name
    for name in _BASE_ALLOWLIST
    if "CERT" in name or "CA_BUNDLE" in name or "CAINFO" in name or "CAPATH" in name
)
PROXY_VARIABLES = frozenset(name for name in _BASE_ALLOWLIST if "proxy" in name.lower())

_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.M)


def _document(path: pathlib.Path) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def _services(path: pathlib.Path) -> dict[str, Any]:
    services: dict[str, Any] = _document(path)["services"]
    return services


def _environment(path: pathlib.Path, service: str) -> dict[str, Any]:
    env: dict[str, Any] = (_services(path)[service] or {}).get("environment") or {}
    return env


def _keys_the_guide_puts_in_env(page: pathlib.Path) -> set[str]:
    """Names assigned at the start of a line in the page's shell blocks.

    Those blocks are the ones an operator copies into ``.env``. The other
    shell blocks on the page (``cat``, ``kubectl``, ``docker-compose logs``)
    assign nothing at line start, so they contribute nothing.
    """
    keys: set[str] = set()
    for language, body in _FENCE.findall(page.read_text(encoding="utf-8")):
        if language == "bash":
            keys |= set(_ASSIGNMENT.findall(body))
    return keys


def test_the_variable_sets_are_the_ones_this_file_is_about() -> None:
    """A precondition, so the checks below cannot pass by finding nothing.

    Every operator-set name in the allowlist has to be either a certificate
    name or a proxy name, so a new one cannot slip past both rules.
    """
    assert CA_VARIABLES == {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "GIT_SSL_CAINFO",
        "GIT_SSL_CAPATH",
    }
    assert PROXY_VARIABLES == {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    assert CA_VARIABLES | PROXY_VARIABLES == _BASE_ALLOWLIST - _PROCESS_BASICS


@pytest.mark.parametrize(
    ("path", "service"),
    [(path, service) for path, names in OUTBOUND_SERVICES.items() for service in names],
    ids=lambda value: value.name if isinstance(value, pathlib.Path) else value,
)
def test_every_outbound_service_receives_the_certificate_variables_without_a_default(
    path: pathlib.Path, service: str
) -> None:
    assert service in _services(path), f"{path.name} no longer has {service}"
    env = _environment(path, service)
    missing = sorted(name for name in CA_VARIABLES if name not in env)
    assert not missing, (
        f"{path.name}: {service} does not receive {missing}. A value set in "
        ".env reaches compose interpolation only, so the container never sees "
        "it and the guide's instructions do nothing."
    )
    valued = sorted(name for name in CA_VARIABLES if env[name] is not None)
    assert not valued, (
        f"{path.name}: {service} gives {valued} a value. List them with no "
        "value (`NAME:`) so an unset one stays out of the container: an empty "
        "GIT_SSL_CAINFO breaks every clone and an empty SSL_CERT_FILE leaves "
        "the portal trusting no authority."
    )


@pytest.mark.parametrize("path", list(OUTBOUND_SERVICES), ids=lambda p: p.name)
def test_the_proxy_names_stay_out_of_the_compose_environment(path: pathlib.Path) -> None:
    """compose injects proxies from ~/.docker/config.json underneath this
    environment; any entry here, with or without a value, would decide over
    it, and a value-less one would remove it. See the module docstring."""
    anchor = _document(path)[ANCHOR_KEY]
    assert "SSL_CERT_FILE" in anchor, f"{path.name}: {ANCHOR_KEY} was not found"
    found = sorted(name for name in PROXY_VARIABLES if name in anchor)
    for service in OUTBOUND_SERVICES[path]:
        env = _environment(path, service)
        found += [f"{service}: {name}" for name in sorted(PROXY_VARIABLES) if name in env]
    assert not found, (
        f"{path.name} lists proxy variables {found}. Compose injects the proxy "
        "from the Docker client's config.json underneath the service "
        "environment, so an entry here overrides it, and a value-less one "
        "unset in .env removes it from the container."
    )


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO], ids=["en", "ko"])
def test_every_variable_the_guide_puts_in_env_reaches_the_containers(
    page: pathlib.Path,
) -> None:
    documented = _keys_the_guide_puts_in_env(page)
    assert {
        "SSL_CERT_FILE",
        "GIT_SSL_CAINFO",
    } <= documented, f"{page.name}: the .env block was not found; this oracle needs updating"
    unpassed = sorted(
        f"{service}: {name}"
        for service in OUTBOUND_SERVICES[COMPOSE_PROD]
        for name in documented
        if name not in _environment(COMPOSE_PROD, service)
    )
    assert not unpassed, (
        f"{page.name} tells operators to set these in .env, and "
        f"docker-compose.yml does not pass them: {unpassed}"
    )


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO], ids=["en", "ko"])
def test_the_guide_sends_proxies_to_config_json_not_env(page: pathlib.Path) -> None:
    text = page.read_text(encoding="utf-8")
    in_env = sorted(name for name in _keys_the_guide_puts_in_env(page) if name in PROXY_VARIABLES)
    assert not in_env, (
        f"{page.name} tells operators to put {in_env} in .env, which "
        "docker-compose.yml does not pass on"
    )
    configs = [
        json.loads(body)
        for language, body in _FENCE.findall(text)
        if language == "json" and '"proxies"' in body
    ]
    assert configs, f"{page.name} no longer shows the config.json proxy method"
    for config in configs:
        assert {"httpProxy", "httpsProxy", "noProxy"} <= set(config["proxies"]["default"])


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO], ids=["en", "ko"])
def test_the_guide_names_only_services_that_exist(page: pathlib.Path) -> None:
    """The override examples and the log command name compose services.

    The example named ``worker`` after the service had been split into
    ``worker-scan`` and ``worker-default``. An override for a service that
    does not exist defines a new one with neither an image nor a build
    context, which compose refuses, and either way the mount the page
    promised never reaches a worker.
    """
    text = page.read_text(encoding="utf-8")
    services = set(_services(COMPOSE_PROD))

    overrides = [
        yaml.safe_load(body)
        for language, body in _FENCE.findall(text)
        if language == "yaml" and "docker-compose.override.yml" in body
    ]
    assert len(overrides) >= 2, f"{page.name}: the override examples were not found"
    for override in overrides:
        named = set(override["services"])
        assert named <= services, f"{page.name}: override names {sorted(named - services)}"
        assert {
            "worker-scan",
            "worker-default",
        } <= named, f"{page.name}: an override example reaches no worker"
        for name, service in override["services"].items():
            env = (service or {}).get("environment")
            if env is not None:
                lacking = sorted(PROXY_VARIABLES - set(env))
                assert not lacking, f"{page.name}: proxy override for {name} lacks {lacking}"

    logged = re.findall(r"docker-compose logs ([a-z0-9 -]+?) \|", text)
    assert logged, f"{page.name}: the log command was not found"
    for command in logged:
        named = set(command.split())
        assert named <= services, f"{page.name}: logs names {sorted(named - services)}"


def test_the_template_offers_the_certificate_names_and_no_proxy_assignment() -> None:
    """Uncommenting an example in .env.example must not produce an empty value
    or a proxy setting that goes nowhere.

    The template showed ``# SSL_CERT_DIR=`` and ``# GIT_SSL_CAPATH=``. An
    operator uncommenting the block wholesale would have set both to empty,
    and an empty ``GIT_SSL_CAPATH`` is as fatal to cloning as an empty
    ``GIT_SSL_CAINFO``.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    seen: set[str] = set()
    empty: list[str] = []
    proxies: list[str] = []
    for name, value in re.findall(r"^#?\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", text, re.M):
        if name in CA_VARIABLES:
            seen.add(name)
            if not value.strip():
                empty.append(name)
        if name in PROXY_VARIABLES:
            proxies.append(name)
    assert seen == CA_VARIABLES, f".env.example does not show {sorted(CA_VARIABLES - seen)}"
    assert not empty, f".env.example shows {empty} with an empty value"
    assert not proxies, f".env.example offers {proxies}, which compose does not pass"
