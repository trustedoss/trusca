# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
How the private-CA and proxy variables reach compose containers (#571), and
why the compose files never read them from compose's own environment.

The first fix listed the certificate names in the shared ``x-backend-env``
anchor with no value (``SSL_CERT_FILE:``), so compose would copy them from
``.env`` or the shell. That broke ``docker-compose up -d`` run twice. The
docker-compose 1.29.2 release binary is a PyInstaller one-file build whose
certifi runtime hook (``pyi_rth_certifi``) sets ``SSL_CERT_FILE`` in the
compose process itself, to ``<sys._MEIPASS>/certifi/cacert.pem``, whenever
OpenSSL reports no default CA file. ``_MEIPASS`` is a fresh
``/tmp/_MEI<random>`` directory on every invocation. A value-less key, or a
``${SSL_CERT_FILE...}`` interpolation, therefore resolved to a different path
on every run: the containers got a CA file that does not exist inside them,
and the service config hash changed, so the second ``up`` recreated the
backend (and on current Docker engines died with ``KeyError:
'ContainerConfig'``). The upgrade UAT caught it.

What the compose files do instead
---------------------------------
Neither the certificate names nor the proxy names appear in any service's
environment. Operators set the certificate variables with written-out values
in ``docker-compose.override.yml``, next to the mount the bundle needs anyway,
and proxies in ``~/.docker/config.json`` or the same override file. Proxy
names also stay out because compose injects config.json proxies underneath the
service environment, and any entry there would override them.

How hash stability is checked without compose
---------------------------------------------
compose 1.29.2 cannot be installed beside this backend (it pins PyYAML<6), so
the test models the only two places compose reads its own process environment
when it builds a service config: a value-less ``environment`` key and
``$VAR`` / ``${VAR...}`` interpolation. Each service is resolved under two
process environments that differ only in what the hook injects, and the
results must hash the same. A self-check proves the model sees both forms.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any

import pytest
import yaml

from integrations._subprocess_env import _BASE_ALLOWLIST

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
COMPOSE_FILES = sorted(REPO_ROOT.glob("docker-compose*.yml"))
COMPOSE_PROD = REPO_ROOT / "docker-compose.yml"
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
CA_MOUNT = "/etc/ssl/corp-ca.pem"

#: What the binary's certifi hook puts in compose's environment, on two runs.
HOOK_RUNS = (
    {"SSL_CERT_FILE": "/tmp/_MEIa1b2c3/certifi/cacert.pem"},
    {"SSL_CERT_FILE": "/tmp/_MEIz9y8x7/certifi/cacert.pem"},
)

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
_INTERPOLATION = re.compile(
    r"\$(?:\$|\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?])?(?P<arg>[^}]*)\}"
    r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)


# ---------------------------------------------------------------------------
# A model of where compose 1.29.2 reads its own environment
# ---------------------------------------------------------------------------


def _interpolate(value: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group("braced") or match.group("bare")
        op = match.group("op") or ""
        current = env.get(name)
        if op.startswith(":") and not current:
            current = None
        if current is None and op.endswith("-"):
            return _interpolate(match.group("arg") or "", env)
        return current or ""

    return _INTERPOLATION.sub(replace, value)


def _resolve(node: Any, env: dict[str, str], *, environment: bool = False) -> Any:
    if isinstance(node, str):
        return _interpolate(node, env)
    if isinstance(node, list):
        return [_resolve(item, env) for item in node]
    if isinstance(node, dict):
        resolved = {}
        for key, value in node.items():
            if environment and value is None:
                # A value-less key: compose copies it from its own environment.
                resolved[key] = env.get(key)
            else:
                resolved[key] = _resolve(value, env, environment=key == "environment")
        return resolved
    return node


def _service_hashes(document: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    hashes = {}
    for name, service in (document.get("services") or {}).items():
        resolved = _resolve(service or {}, env)
        dump = json.dumps(resolved, sort_keys=True, default=str)
        hashes[name] = hashlib.sha256(dump.encode()).hexdigest()
    return hashes


def _load(path: pathlib.Path) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def _keys_the_guide_puts_in_env(page: pathlib.Path) -> set[str]:
    """Names assigned at the start of a line in the page's shell blocks."""
    keys: set[str] = set()
    for language, body in _FENCE.findall(page.read_text(encoding="utf-8")):
        if language == "bash":
            keys |= set(_ASSIGNMENT.findall(body))
    return keys


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def test_the_variable_sets_are_the_ones_this_file_is_about() -> None:
    """Every operator-set name in the allowlist is classified, so a new one
    cannot slip past both rules."""
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
    assert len(COMPOSE_FILES) >= 2 and COMPOSE_PROD in COMPOSE_FILES


@pytest.mark.parametrize(
    "service",
    [
        {"environment": {"SSL_CERT_FILE": None}},
        {"environment": {"SSL_CERT_FILE": "${SSL_CERT_FILE:-}"}},
        {"environment": {"X": "$SSL_CERT_FILE"}},
        {"volumes": ["${SSL_CERT_FILE}:/etc/ssl/ca.pem:ro"]},
    ],
    ids=["value-less", "braced-default", "bare", "outside-environment"],
)
def test_the_model_sees_every_form_that_reads_the_hook_variable(
    service: dict[str, Any],
) -> None:
    """Without this, a model that ignored one form would call the real files
    stable for the wrong reason."""
    document = {"services": {"backend": service}}
    first, second = (_service_hashes(document, env) for env in HOOK_RUNS)
    assert first != second


# ---------------------------------------------------------------------------
# The compose files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_config_hash_does_not_depend_on_what_the_binary_sets(path: pathlib.Path) -> None:
    document = _load(path)
    first, second = (_service_hashes(document, env) for env in HOOK_RUNS)
    assert first, f"{path.name}: no services found"
    changed = sorted(name for name in first if first[name] != second[name])
    assert not changed, (
        f"{path.name}: {changed} read SSL_CERT_FILE from compose's own "
        "environment. The docker-compose 1.29.2 binary sets it to a new "
        "/tmp/_MEI* path on every run, so these services get a CA file that "
        "does not exist and are recreated by every `up -d`."
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_no_service_environment_names_a_ca_or_proxy_variable(path: pathlib.Path) -> None:
    """The certificate names belong in the override file with explicit values;
    the proxy names would also override what compose injects from
    config.json."""
    document = _load(path)
    named = sorted(
        f"{service}: {key}"
        for service, body in (document.get("services") or {}).items()
        for key in ((body or {}).get("environment") or {})
        if key in CA_VARIABLES | PROXY_VARIABLES
    )
    assert not named, f"{path.name} lists {named}"


# ---------------------------------------------------------------------------
# The guide and the template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO], ids=["en", "ko"])
def test_the_guide_does_not_put_these_variables_in_env(page: pathlib.Path) -> None:
    in_env = sorted(
        name for name in _keys_the_guide_puts_in_env(page) if name in CA_VARIABLES | PROXY_VARIABLES
    )
    assert not in_env, (
        f"{page.name} tells operators to put {in_env} in .env, which "
        "docker-compose.yml does not pass on"
    )


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO], ids=["en", "ko"])
def test_the_guide_overrides_set_what_they_promise_on_services_that_exist(
    page: pathlib.Path,
) -> None:
    text = page.read_text(encoding="utf-8")
    services = set(_load(COMPOSE_PROD)["services"])
    overrides = [
        yaml.safe_load(body)
        for language, body in _FENCE.findall(text)
        if language == "yaml" and "docker-compose.override.yml" in body
    ]
    assert len(overrides) >= 2, f"{page.name}: the override examples were not found"

    saw_ca = saw_proxy = False
    for override in overrides:
        named = set(override["services"])
        assert named <= services, f"{page.name}: override names {sorted(named - services)}"
        assert {
            "worker-scan",
            "worker-default",
        } <= named, f"{page.name}: an override example reaches no worker"
        for name, service in override["services"].items():
            env = (service or {}).get("environment") or {}
            mounts = [v for v in (service or {}).get("volumes") or [] if f":{CA_MOUNT}" in v]
            if mounts:
                saw_ca = True
                pointed = {k: env.get(k) for k in CA_VARIABLES if k in env}
                assert {"SSL_CERT_FILE", "GIT_SSL_CAINFO"} <= set(pointed), (
                    f"{page.name}: {name} mounts the bundle but sets no variable " "pointing at it"
                )
                assert set(pointed.values()) == {
                    CA_MOUNT
                }, f"{page.name}: {name} points the tools somewhere other than the mount"
            if PROXY_VARIABLES & set(env):
                saw_proxy = True
                lacking = sorted(PROXY_VARIABLES - set(env))
                assert not lacking, f"{page.name}: proxy override for {name} lacks {lacking}"
    assert saw_ca and saw_proxy, f"{page.name}: an override example lost its purpose"

    logged = re.findall(r"docker-compose logs ([a-z0-9 -]+?) \|", text)
    assert logged, f"{page.name}: the log command was not found"
    for command in logged:
        named = set(command.split())
        assert named <= services, f"{page.name}: logs names {sorted(named - services)}"


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO], ids=["en", "ko"])
def test_the_guide_shows_the_config_json_proxy_method(page: pathlib.Path) -> None:
    configs = [
        json.loads(body)
        for language, body in _FENCE.findall(page.read_text(encoding="utf-8"))
        if language == "json" and '"proxies"' in body
    ]
    assert configs, f"{page.name} no longer shows the config.json proxy method"
    for config in configs:
        assert {"httpProxy", "httpsProxy", "noProxy"} <= set(config["proxies"]["default"])


def test_the_template_offers_no_ca_or_proxy_assignment() -> None:
    """Uncommenting .env.example must not suggest these work from .env."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    offered = sorted(
        name
        for name in re.findall(r"^#?\s*([A-Za-z_][A-Za-z0-9_]*)=", text, re.M)
        if name in CA_VARIABLES | PROXY_VARIABLES
    )
    assert not offered, f".env.example offers {offered}, which compose does not pass"
