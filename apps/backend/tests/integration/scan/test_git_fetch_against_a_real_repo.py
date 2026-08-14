# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Real git, real refs: what ``build_git_fetch_commands`` chooses actually works.

The command *selection* has 25 unit cases. Running them did not: the loop was
``# pragma: no cover`` because it needs a live remote, and the SSRF guard
rejects ``file://`` so a fixture repository could not stand in for one. Two
behaviours therefore shipped verified only at the level of "these are the right
strings" (gap #40):

  - that git, handed those commands, checks out the tree the ref points at,
    including ``refs/pull/N/merge`` which is the case the whole change exists
    for;
  - that a ref which has vanished between trigger and worker pickup falls back
    to the default branch and records ``metadata.ref_fallback``.

git takes a filesystem path as a remote, and ``run_git_fetch_commands`` runs
AFTER the guard has passed the URL, so a bare repository on disk exercises the
same code with real git. No loopback daemon, no test-only hole in the guard.

Not covered here, and deliberately: authentication. The credential injection
and the stderr scrubbing on an auth failure need a remote that can reject you;
those keep their own unit tests (``build_authenticated_clone_url``,
``_scrub_clone_stderr``).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "TRUSCA test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "TRUSCA test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A bare repository with a default branch, a tag, and a merge ref.

    Shaped like what a webhook-triggered scan meets: ``main`` has moved on
    since the pull request was opened, so a scan that fetches the wrong ref
    reads different code and says so with a different file.
    """
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-b", "main", ".", cwd=work)
    (work / "which.txt").write_text("main\n", encoding="utf-8")
    _git("add", "which.txt", cwd=work)
    _git("commit", "-m", "main commit", cwd=work)

    # The pull request's merge ref, carrying a different tree.
    _git("checkout", "-b", "feature", cwd=work)
    (work / "which.txt").write_text("pull-request\n", encoding="utf-8")
    _git("commit", "-am", "pr commit", cwd=work)
    pr_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("checkout", "main", cwd=work)

    # Move main on, so "fetched the default branch instead" is visible.
    (work / "which.txt").write_text("main-moved-on\n", encoding="utf-8")
    _git("commit", "-am", "main moves on", cwd=work)
    _git("tag", "v1.0.0", cwd=work)

    bare = tmp_path / "remote.git"
    _git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
    # Bare clones do not copy refs/pull/*; write it explicitly, which is what
    # the Git host does on its side.
    _git("update-ref", "refs/pull/1/merge", pr_sha, cwd=bare)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    return bare


def _run(
    remote: Path,
    tmp_path: Path,
    ref: str | None,
    *,
    fallbacks: list[dict[str, Any]] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Path:
    from tasks import scan_source

    if monkeypatch is not None:
        recorded = fallbacks if fallbacks is not None else []

        def _capture(*, scan_uuid: uuid.UUID, ref: str, reason: str) -> None:
            recorded.append({"scan_uuid": scan_uuid, "ref": ref, "reason": reason})

        # The real one writes to the scan row; the DB is not what this test is
        # about, and the call itself is what matters.
        monkeypatch.setattr(scan_source, "_record_ref_fallback", _capture)

    source_dir = tmp_path / "source"
    source_dir.mkdir(exist_ok=True)
    target = source_dir / "repo"

    commands = scan_source.build_git_fetch_commands(
        clone_url=str(remote),
        target=target,
        ref=ref,
        resolve_option=None,
    )
    return scan_source.run_git_fetch_commands(
        commands,
        scan_uuid=uuid.uuid4(),
        clone_url=str(remote),
        target=target,
        ref=ref,
        resolve_option=None,
        credential=None,
        source_dir=source_dir,
    )


def _checked_out(source_dir: Path) -> str:
    return (source_dir / "repo" / "which.txt").read_text(encoding="utf-8").strip()


def test_default_branch_when_no_ref_was_named(remote: Path, tmp_path: Path) -> None:
    """The pre-ref behaviour, byte for byte: a shallow clone of HEAD."""
    source_dir = _run(remote, tmp_path, None)
    assert _checked_out(source_dir) == "main-moved-on"


def test_branch_ref_checks_out_that_branch(remote: Path, tmp_path: Path) -> None:
    source_dir = _run(remote, tmp_path, "refs/heads/feature")
    assert _checked_out(source_dir) == "pull-request"


def test_tag_ref_checks_out_the_tag(remote: Path, tmp_path: Path) -> None:
    source_dir = _run(remote, tmp_path, "refs/tags/v1.0.0")
    assert _checked_out(source_dir) == "main-moved-on"


def test_pull_request_merge_ref_checks_out_the_pull_request(
    remote: Path, tmp_path: Path
) -> None:
    """The case the whole change exists for.

    ``git clone --branch`` cannot take ``refs/pull/N/merge``, which is why the
    ref path is init + fetch + detached checkout rather than a clone. If that
    sequence were wrong, this would silently read ``main`` — the failure the
    original bug produced, where a pull request's scan graded the base branch.
    """
    source_dir = _run(remote, tmp_path, "refs/pull/1/merge")
    assert _checked_out(source_dir) == "pull-request"


def test_vanished_ref_falls_back_and_is_recorded(
    remote: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merged or force-pushed pull request, reproduced.

    The ref is deleted after the commands are built, which is the real race:
    the trigger names a ref and the worker picks the job up minutes later. The
    scan must not fail, must end up on the default branch, and must say that it
    did — a verdict from a fallback describes different code than the one
    requested.
    """
    fallbacks: list[dict[str, Any]] = []
    _git("update-ref", "-d", "refs/pull/1/merge", cwd=remote)

    source_dir = _run(
        remote,
        tmp_path,
        "refs/pull/1/merge",
        fallbacks=fallbacks,
        monkeypatch=monkeypatch,
    )

    assert _checked_out(source_dir) == "main-moved-on", "did not fall back"
    assert len(fallbacks) == 1, "the substitution was not recorded"
    assert fallbacks[0]["ref"] == "refs/pull/1/merge"
    assert fallbacks[0]["reason"], "the git error must reach the scan row"


def test_missing_remote_without_a_ref_is_terminal(tmp_path: Path) -> None:
    """No ref means no fallback: the clone failing is the whole answer."""
    from tasks.scan_source import _FetchAborted

    with pytest.raises(_FetchAborted):
        _run(tmp_path / "not-a-repository.git", tmp_path, None)
