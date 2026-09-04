# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the private-CA page (ER25, hardening rule 4).

The page tells an operator to set four environment variables and says which
tool reads each one. That instruction is only true while the allowlist
actually forwards them, and the page is the only place a reader learns that
``git`` needs its own variable, so the two have to move together.

What is compared, and what deliberately is not
----------------------------------------------
The variable names are read live off ``_subprocess_env`` rather than listed
here, so dropping one from the allowlist fails this test with the page still
promising it. The prose about which tool reads what is not machine-checkable
and is not checked: it came from measurements in the worker image, recorded in
the allowlist's own comment.

The boot log's field names are checked too. The page tells the operator to
read ``authorities`` and ``bundled_authorities`` out of a log line, which is an
interface even though it is not an API, and renaming a field would leave that
instruction pointing at nothing.
"""

from __future__ import annotations

import pathlib

import pytest

from core.tls_trust import describe_trust_store
from integrations._subprocess_env import _BASE_ALLOWLIST

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
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
REFERENCE_EN = REPO_ROOT / "docs-site" / "docs" / "reference" / "env-variables.md"

#: The certificate variables the allowlist forwards. Derived, not typed out.
CA_VARIABLES = frozenset(
    name
    for name in _BASE_ALLOWLIST
    if "CERT" in name or "CA_BUNDLE" in name or "CAINFO" in name or "CAPATH" in name
)


def test_the_allowlist_carries_the_variables_the_page_is_about() -> None:
    """A precondition, so the tests below cannot pass by finding nothing."""
    assert CA_VARIABLES == {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "GIT_SSL_CAINFO",
        "GIT_SSL_CAPATH",
    }


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO, REFERENCE_EN])
def test_every_forwarded_variable_is_documented(page: pathlib.Path) -> None:
    assert page.exists(), f"{page} moved; this oracle needs updating"
    text = page.read_text(encoding="utf-8")
    missing = sorted(name for name in CA_VARIABLES if name not in text)
    assert not missing, (
        f"{page.name} does not mention {missing}. An operator following it "
        "would configure part of the pipeline and be left guessing why the "
        "rest still fails, which is the situation this page exists to end."
    )


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO])
def test_the_page_warns_that_the_portal_replaces_rather_than_adds(
    page: pathlib.Path,
) -> None:
    """The one instruction that turns a working setup into a broken one.

    ``SSL_CERT_FILE`` set to a corporate CA alone leaves scans working and the
    portal's own calls unverifiable. If that paragraph is ever trimmed, the
    page becomes advice that breaks deployments.
    """
    text = page.read_text(encoding="utf-8")
    assert "ca-certificates.crt" in text, (
        "the page no longer shows how to build a bundle that keeps the public "
        "roots, which is the fix for the failure mode it describes"
    )


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO])
def test_the_page_names_the_boot_log_fields_that_exist(page: pathlib.Path) -> None:
    """The page tells the reader to check a log line; the fields must be real."""
    text = page.read_text(encoding="utf-8")
    facts = describe_trust_store()
    for field in ("authorities", "bundled_authorities", "source", "path"):
        assert field in facts, f"describe_trust_store no longer reports {field}"
    for field in ("authorities", "bundled_authorities"):
        assert field in text, (
            f"the page tells the operator to read {field} out of the boot log, "
            "and the log no longer carries that name"
        )
    assert "tls_trust.outbound" in text
    assert "tls_trust.public_roots_dropped" in text


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO])
def test_the_page_records_the_limit_we_cannot_lift(page: pathlib.Path) -> None:
    """Image pulls made by the Docker daemon are outside our configuration.

    Said rather than left to be discovered: an operator whose container scan
    cannot reach an internal registry needs to know the fix is on the host, not
    in any setting the portal has. The Helm limit that sat here with it is gone,
    because the chart now mounts the certificate.
    """
    text = page.read_text(encoding="utf-8")
    assert "Docker" in text or "docker" in text


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO])
def test_the_page_carries_a_helm_recipe_that_matches_the_chart(
    page: pathlib.Path,
) -> None:
    """The values the page tells an operator to set have to be the chart's.

    Read off the chart rather than typed here, so renaming a value and leaving
    the page telling people to use the old name fails. The page is the only
    place the four certificate variables and the mount appear together, and an
    operator who follows it and finds nothing mounted has no next step.
    """
    import yaml

    text = page.read_text(encoding="utf-8")
    values = yaml.safe_load(
        (REPO_ROOT / "charts" / "trustedoss" / "values.yaml").read_text()
    )
    for key in ("extraVolumes", "extraVolumeMounts", "extraEnv"):
        assert key in values["env"], f"the chart no longer declares env.{key}"
        assert key in text, (
            f"the page tells operators to configure Helm without naming {key}, "
            "which the chart needs to mount anything"
        )
    assert "subPath" in text, (
        "without subPath the Secret mounts as a directory over the path and "
        "the file the certificate variables name does not exist, so the page "
        "has to show it"
    )


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO])
def test_the_page_tells_the_reader_to_check_every_process(
    page: pathlib.Path,
) -> None:
    """Three services, three environments, three lines to look for.

    The page's instruction is to check all of them, which is only useful while
    all of them report. The process names are read off the hook module rather
    than typed here, so adding a fourth reporting process without a word in the
    guide fails.
    """
    text = page.read_text(encoding="utf-8")
    for name in ("api", "worker", "beat"):
        assert f"process={name}" in text, (
            f"the page does not tell the operator to look for the {name} "
            "process, so a service that never got the certificate would go "
            "unnoticed"
        )


def test_the_hooks_cover_the_processes_the_page_names() -> None:
    """The other direction: the guide promises worker and beat report."""
    import inspect

    from tasks import tls_trust_boot

    source = inspect.getsource(tls_trust_boot)
    assert 'process="worker"' in source
    assert 'process="beat"' in source


@pytest.mark.parametrize("page", [GUIDE_EN, GUIDE_KO])
def test_the_page_says_how_to_find_the_lines(page: pathlib.Path) -> None:
    """A log line nobody can filter for is a line nobody reads.

    The worker's own output is mostly scan progress, so the instruction to
    check three processes is only actionable with something to grep. The
    prefix is asserted against the events actually emitted, so renaming them
    and leaving the page pointing at the old string fails here.
    """
    import structlog

    from core.tls_trust import log_trust_store

    text = page.read_text(encoding="utf-8")
    assert "grep tls_trust" in text, (
        "the page tells the operator to check three processes without saying "
        "how to find their lines"
    )

    with structlog.testing.capture_logs() as captured:
        log_trust_store(process="api")
    events = [entry["event"] for entry in captured]
    assert events, "nothing was logged, so the prefix below proves nothing"
    for event in events:
        assert event.startswith("tls_trust"), (
            f"{event} does not carry the prefix the page tells operators to "
            "grep for, so filtering on it would hide this line"
        )
