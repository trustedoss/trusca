"""
Unit tests for ``services.scan_service.normalize_ref`` — scan-retention.

``normalize_ref`` turns an untrusted git ref (from a webhook payload or a CI
action env var) into the stable retention key that groups a project's scans.
Two concerns are tested:

  1. Convergence — webhook full refs (``refs/heads/main``) and CI bare refs
     (``main``) must produce the SAME key so a branch's scans supersede one
     another regardless of which path triggered them.
  2. Adversarial input — the result is a DB key AND a log field, so control
     bytes / oversized / separator-only input must collapse to None rather than
     mint a phantom key (which would let a scan evade ref-keyed retire).
"""

from __future__ import annotations

import pytest

from services.scan_service import normalize_ref


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # --- branches / tags (webhook full refs) ---
        ("refs/heads/main", "main"),
        ("refs/heads/feature/login", "feature/login"),
        ("refs/tags/v1.2.3", "v1.2.3"),
        # --- pull / merge requests ---
        ("refs/pull/12/merge", "pr-12"),
        ("refs/pull/3/head", "pr-3"),
        ("refs/merge-requests/7/head", "mr-7"),
        # --- already-bare (CI action / GitLab CI_COMMIT_REF_NAME) ---
        ("main", "main"),
        ("release/2025.1", "release/2025.1"),
        ("  feature/x  ", "feature/x"),  # surrounding whitespace trimmed
        # --- convergence: full ref and bare ref land on the same key ---
        ("refs/heads/develop", "develop"),
    ],
)
def test_normalize_ref_happy_path(raw: str, expected: str) -> None:
    assert normalize_ref(raw) == expected


def test_webhook_and_ci_refs_converge() -> None:
    """A webhook (full ref) and a CI action (bare) for the same branch must
    produce one retention key — otherwise retire never supersedes."""
    assert normalize_ref("refs/heads/main") == normalize_ref("main") == "main"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "/",
        "///",
        "refs/heads/",  # heads prefix with nothing after → no key
        "refs/tags/",
        "\x00",  # NUL byte
        "main\x00injected",  # embedded NUL
        "main\nlog-injection",  # newline (log-forging)
        "main\twith-tab",  # C0 control (tab)
        "ma\x7fin",  # DEL (0x7F) interior — missed by a naive `ord < 0x20` check
        "ma\x85in",  # C1 control (NEL) interior (trailing would be strip()'d)
        "feature/../../etc",  # path traversal — `..` rejected
        "a..b",  # interior `..` (git check-ref-format forbidden)
        "feature*",  # wildcard
        "ref~1",  # `~` (git revision syntax)
        "a:b",  # colon
        "branch with space",  # space
        "a" * 256,  # oversized (> 255)
        123,  # non-str
        ["refs/heads/main"],  # non-str container
    ],
)
def test_normalize_ref_rejects_junk(raw: object) -> None:
    """Missing / blank / oversized / control-char / non-str input → None so a
    junk ref never mints a phantom retention key."""
    assert normalize_ref(raw) is None  # type: ignore[arg-type]


def test_normalize_ref_at_length_boundary() -> None:
    """A 255-char bare ref is accepted; 256 is rejected."""
    assert normalize_ref("a" * 255) == "a" * 255
    assert normalize_ref("a" * 256) is None
