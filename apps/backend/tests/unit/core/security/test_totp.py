# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""TOTP, proved against the RFC's own vectors rather than against itself.

The reason a hand-written implementation is defensible here is that RFC 6238
publishes the answers. A round trip through our own code would agree with
itself whatever it computed; these vectors were produced by somebody else, so
matching them is evidence rather than consistency.
"""

from __future__ import annotations

import ast
import base64
from pathlib import Path

import pytest

from core.totp import PERIOD_SECONDS, code_at, generate_secret, provisioning_uri, verify

#: RFC 6238 Appendix B. The published table covers SHA-1, SHA-256 and SHA-512
#: with *different* seeds -- 20, 32 and 64 bytes -- and applying the 20-byte
#: seed to all three produces mismatches that look like implementation bugs.
#: We implement SHA-1 only, so this is the 20-byte seed and the SHA-1 column.
_SEED = b"12345678901234567890"
_SECRET = base64.b32encode(_SEED).decode("ascii").rstrip("=")

_VECTORS = {
    59: "287082",
    1111111109: "081804",
    1111111111: "050471",
    1234567890: "005924",
    2000000000: "279037",
    20000000000: "353130",
}


@pytest.mark.parametrize(("unix_time", "expected"), sorted(_VECTORS.items()))
def test_the_rfc_vectors_match(unix_time: int, expected: str) -> None:
    assert code_at(_SECRET, counter=unix_time // PERIOD_SECONDS) == expected


def test_a_code_is_accepted_within_its_own_step() -> None:
    moment = 1111111111.0
    code = code_at(_SECRET, counter=int(moment) // PERIOD_SECONDS)

    assert verify(_SECRET, code, at=moment) is not None


def test_the_neighbouring_steps_are_accepted_and_the_next_one_is_not() -> None:
    """Drift tolerance is one step either way, and it stops there.

    A phone's clock is often a little off, which is what the window is for. It
    is also the guessing target: one step each side turns one code in a million
    into three. Widening it further trades that away for clocks that ought to
    be corrected instead, so the boundary is asserted rather than left to
    whatever the default happens to be.
    """
    moment = 1111111111.0
    centre = int(moment) // PERIOD_SECONDS

    for step in (-1, 0, 1):
        code = code_at(_SECRET, counter=centre + step)
        assert verify(_SECRET, code, at=moment) == centre + step, step

    for step in (-2, 2):
        code = code_at(_SECRET, counter=centre + step)
        assert verify(_SECRET, code, at=moment) is None, step


def test_verify_returns_which_step_matched() -> None:
    """The caller needs the counter, not a yes.

    A code stays valid for its whole step, so somebody who observes one can use
    it again until the step ends. Preventing that means remembering which step
    was spent, which the caller cannot do if all it gets back is a boolean.
    """
    moment = 1111111111.0
    centre = int(moment) // PERIOD_SECONDS

    assert verify(_SECRET, code_at(_SECRET, counter=centre), at=moment) == centre


@pytest.mark.parametrize("candidate", ["", "12345", "1234567", "abcdef", "12 456", None])
def test_a_malformed_candidate_is_refused_without_comparing(candidate) -> None:
    assert verify(_SECRET, candidate, at=1111111111.0) is None


def test_a_generated_secret_round_trips() -> None:
    secret = generate_secret()

    assert len(secret) == 32, secret
    assert verify(secret, code_at(secret, counter=1), at=float(PERIOD_SECONDS)) is not None


def test_the_provisioning_uri_names_the_parameters_it_was_built_with() -> None:
    """An app that assumes different parameters produces codes we refuse.

    The URI is the only place the server tells the app which algorithm, how
    many digits and what period to use. Leaving them out means relying on the
    app's defaults matching ours, which is true today and is not a guarantee.
    """
    uri = provisioning_uri("ABCDEFGH", account="alice@example.com", issuer="TRUSCA")

    assert uri.startswith("otpauth://totp/")
    assert "secret=ABCDEFGH" in uri
    assert "algorithm=SHA1" in uri
    assert f"digits={6}" in uri
    assert f"period={PERIOD_SECONDS}" in uri
    # The label carries a colon between issuer and account, which has to be
    # encoded or an app reads the account as a bare name.
    assert "alice%40example.com" in uri


def test_the_comparison_is_constant_time() -> None:
    """Asserted structurally, because getting it wrong is silent.

    A byte-by-byte comparison returns sooner the earlier it finds a difference,
    which lets somebody learn a code position by position. Nothing fails, no
    test goes red, and the only sign is timing. So the call is checked for by
    reading the tree: `==` here would pass every other test in this file.
    """
    source = Path(verify.__globals__["__file__"]).read_text(encoding="utf-8")
    tree = ast.parse(source)

    verify_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "verify"
    )
    called = {
        child.func.attr
        for child in ast.walk(verify_node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }

    assert "compare_digest" in called, (
        "verify() does not use hmac.compare_digest, so how long it takes "
        "depends on how much of the code was right"
    )
