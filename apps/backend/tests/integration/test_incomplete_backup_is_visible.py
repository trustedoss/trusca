# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A backup that did not finish must say so before restore day.

The manifest is written last, after both artifacts and their checksums, so a
run killed partway leaves its directory without one. The restore refuses such a
backup, which is right. The listing showed it as an ordinary row, which is not:
an operator counting their restore points counted one that would not restore,
and found out at the only moment the count matters.

The second test here is about a defence that looked like two. The checksum
re-verify skipped any artifact the manifest did not mention, in the same branch
that skipped an artifact that was not there. Only the pre-flight's insistence
on a manifest kept an unaccounted-for file out, so a later relaxation of that
requirement would have taken the checksum check with it and nobody would have
seen it go.
"""

from __future__ import annotations

import gzip
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def backups_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setenv("BACKUPS_ROOT", str(root))
    return root


_counter = itertools.count()


def _stamp() -> str:
    """A distinct, format-valid stamp per call.

    The directory name is what `_validate_name` parses, so the two backups a
    test lays down have to differ by their timestamp rather than by a suffix:
    a name it cannot parse is skipped by the listing entirely, which would make
    the assertions below pass or fail for the wrong reason.
    """
    moment = datetime.now(tz=UTC) - timedelta(seconds=next(_counter))
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _make_backup(root: Path, kind: str, *, manifest: bool, workspace: bool = False) -> str:
    """Lay out a backup directory the way the task does, or stop short of it."""
    name = f"{kind}-{_stamp()}"
    directory = root / name
    directory.mkdir()

    dump = directory / "postgres.sql.gz"
    with gzip.open(dump, "wb") as handle:
        handle.write(b"SELECT 1;\n")

    checksums = {"postgres.sql.gz": _sha256(dump)}
    if workspace:
        archive = directory / "workspace.tar.gz"
        archive.write_bytes(b"not a real tar")
        checksums["workspace.tar.gz"] = _sha256(archive)

    if manifest:
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "alembic_head": "abc1234",
                    "has_workspace": workspace,
                    "checksums": checksums,
                }
            )
        )
    return name


def _sha256(path: Path) -> str:
    from tasks.backup import _sha256_file

    return _sha256_file(path)


def test_a_killed_backup_is_listed_as_incomplete(backups_dir: Path) -> None:
    """Both rows appear; only one claims to be usable.

    Hiding the unfinished one would be worse: the directory is on disk taking
    space, and an operator who cannot see it cannot delete it.
    """
    from services.backup_service import list_backups

    finished = _make_backup(backups_dir, "auto", manifest=True)
    killed = _make_backup(backups_dir, "auto", manifest=False)

    by_name = {item.name: item for item in list_backups()}

    assert set(by_name) == {finished, killed}, by_name
    assert by_name[finished].complete is True
    assert by_name[killed].complete is False, (
        "a backup with no manifest is listed as though it were restorable; the "
        "restore refuses it, so the listing and the restore disagree"
    )


def test_a_finished_backup_without_a_workspace_is_still_complete(backups_dir: Path) -> None:
    """Not every backup has a workspace, and those are not damaged.

    Worth its own test: a completeness check that counted the workspace would
    mark most deployments' backups broken, and the flag would be ignored within
    a week.
    """
    from services.backup_service import list_backups

    name = _make_backup(backups_dir, "manual", manifest=True, workspace=False)

    item = next(i for i in list_backups() if i.name == name)

    assert item.complete is True


@pytest.mark.parametrize("missing", ["postgres.sql.gz", "manifest.json"])
def test_the_listing_and_the_restore_agree_on_every_required_artifact(
    backups_dir: Path, missing: str
) -> None:
    """Both surfaces asked about the same backup, one artifact at a time.

    The listing says whether a backup is complete and the restore decides
    whether to proceed. Asserting that they share a constant only proves they
    share a constant; either could stop reading it. So each artifact is removed
    in turn and both are asked, which fails whichever side stopped agreeing.
    """
    from services.backup_service import list_backups
    from tasks.backup import RestoreTaskError, _require_backup_artifacts

    name = _make_backup(backups_dir, "manual", manifest=True)
    (backups_dir / name / missing).unlink()

    item = next(i for i in list_backups() if i.name == name)
    assert item.complete is False, f"the listing calls a backup missing {missing} complete"

    with pytest.raises(RestoreTaskError) as failure:
        _require_backup_artifacts(
            backup_path=backups_dir / name, name=name, actor_uuid=None
        )
    assert missing in str(failure.value), failure.value


def test_an_artifact_the_manifest_does_not_account_for_is_refused(backups_dir: Path) -> None:
    """A file present and unaccounted for is not something to restore.

    Reached by putting a workspace archive next to a manifest that records
    ``has_workspace: false``. Before this change the missing expected checksum
    sent it down the same branch as "there is no such file", and the archive was
    extracted unverified.
    """
    from tasks.backup import RestoreTaskError, _verify_backup_checksums

    name = _make_backup(backups_dir, "manual", manifest=True, workspace=False)
    (backups_dir / name / "workspace.tar.gz").write_bytes(b"unaccounted for")

    with pytest.raises(RestoreTaskError) as failure:
        _verify_backup_checksums(
            backup_path=backups_dir / name,
            manifest=json.loads((backups_dir / name / "manifest.json").read_text()),
            name=name,
            actor_uuid=None,
        )

    assert "no checksum recorded" in str(failure.value), (
        "the refusal must be about the artifact being unaccounted for. Comparing "
        f"a digest against None also raises, and that reads as a mismatch: {failure.value}"
    )


def test_a_manifest_from_before_checksums_still_restores(backups_dir: Path) -> None:
    """`scripts/backup.sh` writes a manifest with no checksums block at all.

    Those backups are real and an operator may have been keeping them as
    restore points. Refusing them to tighten this check would have stranded
    every one, which is the opposite of the goal. This is the distinction the
    rule turns on: accounting for no artifacts is an older format, accounting
    for some and not others is a backup that changed after it was written.
    """
    from structlog.testing import capture_logs

    from tasks.backup import _verify_backup_checksums

    name = _make_backup(backups_dir, "manual", manifest=True)
    path = backups_dir / name / "manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["checksums"]
    path.write_text(json.dumps(manifest))

    with capture_logs() as entries:
        _verify_backup_checksums(
            backup_path=backups_dir / name, manifest=manifest, name=name, actor_uuid=None
        )

    events = [entry.get("event") for entry in entries]
    assert "admin.backup.restore_unverified" in events, (
        "skipping verification has to be visible; it is the only guard against "
        f"on-disk corruption, and it just did not run: {events}"
    )
