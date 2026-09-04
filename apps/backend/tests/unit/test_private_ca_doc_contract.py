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
def test_the_page_records_the_limits_we_know_about(page: pathlib.Path) -> None:
    """Two things this release does not solve, said rather than left to be found.

    Both were established during the investigation: the Helm chart takes extra
    environment variables but no extra volumes, so the variables have nothing
    to point at there, and image pulls made by the Docker daemon are outside
    the portal's environment entirely.
    """
    text = page.read_text(encoding="utf-8")
    assert "Helm" in text
    assert "Docker" in text or "docker" in text
