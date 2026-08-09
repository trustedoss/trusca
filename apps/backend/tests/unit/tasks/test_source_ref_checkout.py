# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The source scan must materialise the ref that triggered it.

It did not. Every scan ran `git clone --depth 1 <url>`, which checks out the
remote's default branch, while the ref travelled alongside as a retention key
only. A pull request that added a vulnerable dependency was therefore scanned
as the base branch: the gate passed it, and a `main` that carried a critical
CVE blocked pull requests that had nothing to do with it. Branch-scoped
verdicts described the wrong code the entire time.

The command sequence is what regressed, and it is testable; the subprocess loop
that runs it needs a live remote and is not. So the choice of commands lives in
`build_git_fetch_commands` and is pinned here, along with the ref-acceptance
rules in `git_ref_to_fetch` — the ref reaches the git command line from a
webhook payload, so what counts as one is a security boundary as well as a
correctness one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tasks.scan_source import build_git_fetch_commands, git_ref_to_fetch

TARGET = Path("/w/source/repo")
PIN = "http.curloptResolve=github.com:443:140.82.121.4"


# ---------------------------------------------------------------------------
# Which ref, if any
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("refs/heads/main", "refs/heads/main"),
        ("refs/pull/12/merge", "refs/pull/12/merge"),
        ("refs/merge-requests/7/head", "refs/merge-requests/7/head"),
        ("main", "main"),
        ("feature/oauth", "feature/oauth"),
        ("  main  ", "main"),
    ],
)
def test_fetchable_refs_are_taken_verbatim(raw: str, expected: str) -> None:
    """The RAW ref is what git can fetch — not the normalized retention key.

    `Scan.ref` holds `pr-12`, which groups a pull request's scans together and
    is not a git ref at all: `git fetch origin pr-12` fails. The metadata keeps
    the original `refs/pull/12/merge`, which is what has to reach git.
    """
    assert git_ref_to_fetch({"ref": raw}) == expected


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"ref": None},
        {"ref": ""},
        {"ref": "   "},
        {"ref": 42},
        {"ref": ["refs/heads/main"]},
    ],
)
def test_absent_ref_means_default_branch(metadata: object) -> None:
    assert git_ref_to_fetch(metadata) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ref",
    [
        "--upload-pack=touch /tmp/pwned",
        "-x",
        "refs/heads/../../etc/passwd",
        "refs/heads/main\nrefs/heads/other",
        "refs/heads/ma in",
        "refs/heads/\x00main",
        "refs/heads/" + "x" * 300,
    ],
)
def test_refs_that_could_reach_git_as_something_else_are_dropped(ref: str) -> None:
    """A rejected ref falls back to the default branch rather than failing.

    The value arrives from a webhook payload, so it is remote-controlled. It is
    passed after `--` in the fetch command as well; this is the first of the
    two checks, not the only one.
    """
    assert git_ref_to_fetch({"ref": ref}) is None


# ---------------------------------------------------------------------------
# What git is asked to do
# ---------------------------------------------------------------------------


def test_without_a_ref_the_command_is_the_previous_clone() -> None:
    """No ref must behave exactly as before — same single shallow clone."""
    commands = build_git_fetch_commands(
        clone_url="https://github.com/acme/widgets.git",
        target=TARGET,
        ref=None,
        resolve_option=PIN,
    )

    assert commands == [
        [
            "git",
            "-c",
            PIN,
            "clone",
            "--depth",
            "1",
            "https://github.com/acme/widgets.git",
            str(TARGET),
        ]
    ]


def test_a_ref_is_fetched_and_checked_out() -> None:
    commands = build_git_fetch_commands(
        clone_url="https://github.com/acme/widgets.git",
        target=TARGET,
        ref="refs/pull/12/merge",
        resolve_option=PIN,
    )

    assert commands == [
        ["git", "init", "--quiet", str(TARGET)],
        [
            "git",
            "-C",
            str(TARGET),
            "remote",
            "add",
            "origin",
            "https://github.com/acme/widgets.git",
        ],
        [
            "git",
            "-C",
            str(TARGET),
            "-c",
            PIN,
            "fetch",
            "--depth",
            "1",
            "origin",
            "--",
            "refs/pull/12/merge",
        ],
        ["git", "-C", str(TARGET), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    ]


def test_the_ref_is_passed_after_a_double_dash() -> None:
    """`--` stops git reading a ref that begins with a dash as an option."""
    fetch = build_git_fetch_commands(
        clone_url="https://x/y.git", target=TARGET, ref="main", resolve_option=None
    )[2]

    assert fetch[-2:] == ["--", "main"]


def test_every_networked_command_carries_the_ssrf_pin() -> None:
    """The fetch transfers the repository, so it needs the pin as much as clone.

    `validate_git_url_with_ip` screens the host and pins the resolved IP at the
    libcurl layer so a DNS answer that rotates to an internal address between
    validation and fetch cannot be followed. Applying that only to the first
    command would leave the transfer unpinned.
    """
    commands = build_git_fetch_commands(
        clone_url="https://github.com/acme/widgets.git",
        target=TARGET,
        ref="refs/heads/main",
        resolve_option=PIN,
    )

    networked = [c for c in commands if "fetch" in c or "clone" in c]
    assert networked, "expected at least one command to touch the network"
    for cmd in networked:
        assert PIN in cmd, f"missing SSRF pin: {cmd}"


def test_ssh_remotes_get_no_curl_pin() -> None:
    """The curl option only applies to HTTP(S); ssh:// must not carry it."""
    commands = build_git_fetch_commands(
        clone_url="git@github.com:acme/widgets.git",
        target=TARGET,
        ref="refs/heads/main",
        resolve_option=None,
    )

    for cmd in commands:
        assert "-c" not in cmd
