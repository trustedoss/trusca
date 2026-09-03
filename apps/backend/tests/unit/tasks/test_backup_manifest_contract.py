# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Two programs write manifest.json, and one program reads it.

`tasks/backup.py` writes one when the Celery task runs; `scripts/backup.sh`
writes one when an operator runs the shell path. The restore reads whichever it
finds. They drifted: the shell script recorded no checksums, so the restore's
verification skipped every artifact it produced, and tightening that check
would have made every script-written backup unrestorable. That was found by an
unrelated test failing, not by anyone comparing the two.

So the two are compared here, by running the shell script's own heredoc rather
than by reading it. A key list transcribed into a test drifts the same way the
manifests did.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parents[1]
SHELL_BACKUP = REPO_ROOT / "scripts" / "backup.sh"

#: What a restore, or an operator reading the file, relies on being there.
#: Each producer may add its own extras (the task records ``name``, the script
#: records ``db_size``); neither may drop one of these.
REQUIRED_KEYS = frozenset(
    {"timestamp", "alembic_head", "workspace_path", "has_workspace", "checksums"}
)


def _shell_manifest(*, has_workspace: bool) -> dict[str, object]:
    """Run the script's manifest heredoc with stand-in values, return the JSON.

    Executing it rather than parsing the source means a stray quote or a
    trailing comma fails here, which is the failure mode that matters: a
    manifest nothing can parse is a backup nothing will restore.
    """
    source = SHELL_BACKUP.read_text(encoding="utf-8")
    match = re.search(
        r'cat > "\$out_dir/manifest\.json" <<JSON\n(.*?)\nJSON\n', source, re.DOTALL
    )
    assert match, "the manifest heredoc was not found; this contract is checking nothing"

    # The script's own checksum block, not a stand-in for it. Substituting a
    # value here was the first version of this test, and it compared something
    # this file had written against the task -- so the script could stop
    # recording the workspace digest and nothing failed.
    building = re.search(
        r"(pg_sha=\$\(sha256_of.*?\nfi)\n", source, re.DOTALL
    )
    assert building, "the checksum block was not found; this contract is checking nothing"

    script = (
        "set -eu\n"
        'out_dir="/tmp/does-not-matter"\n'
        'stamp="20260904T000000Z"\n'
        'alembic_head="abc1234"\n'
        'db_size="1 MB"\n'
        'WORKSPACE_HOST_PATH="/opt/trustedoss/workspace"\n'
        f'has_workspace={"true" if has_workspace else "false"}\n'
        # Stubbed so the block runs without files on disk; what is under test
        # is which artifacts it names, not the digest itself, which
        # `test_the_shell_script_computes_a_real_digest` covers.
        'sha256_of() { echo "sha-of-$(basename "$1")"; }\n'
        f"{building.group(1)}\n"
        f"cat <<JSON\n{match.group(1)}\nJSON\n"
    )
    completed = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return dict(json.loads(completed.stdout))


def _task_manifest(tmp_path: Path, *, has_workspace: bool) -> dict[str, object]:
    from tasks.backup import _write_manifest

    return _write_manifest(
        tmp_path,
        name="auto-20260904T000000Z",
        has_workspace=has_workspace,
        pg_dump_sha256="aa",
        workspace_sha256="bb" if has_workspace else None,
    )


@pytest.mark.parametrize("has_workspace", [True, False])
def test_both_producers_write_every_key_the_restore_relies_on(
    tmp_path: Path, has_workspace: bool
) -> None:
    from_script = _shell_manifest(has_workspace=has_workspace)
    from_task = _task_manifest(tmp_path, has_workspace=has_workspace)

    missing_from_script = REQUIRED_KEYS - set(from_script)
    missing_from_task = REQUIRED_KEYS - set(from_task)

    assert not missing_from_script, (
        f"scripts/backup.sh omits {sorted(missing_from_script)}; a backup it "
        "writes will be read by the same restore that reads the task's"
    )
    assert not missing_from_task, f"tasks/backup.py omits {sorted(missing_from_task)}"


@pytest.mark.parametrize("has_workspace", [True, False])
def test_the_checksum_blocks_have_the_same_shape(tmp_path: Path, has_workspace: bool) -> None:
    """Same artifacts named, whether or not there is a workspace.

    The restore refuses an artifact the manifest does not account for, so a
    producer listing fewer entries than it wrote turns its own backups away,
    and one listing more makes the restore look for a file that is not there.
    """
    from_script = _shell_manifest(has_workspace=has_workspace)
    from_task = _task_manifest(tmp_path, has_workspace=has_workspace)

    script_checksums = from_script["checksums"]
    task_checksums = from_task["checksums"]
    assert isinstance(script_checksums, dict) and isinstance(task_checksums, dict)

    assert set(script_checksums) == set(task_checksums), (
        "the two producers disagree on which artifacts get a checksum: "
        f"script={sorted(script_checksums)} task={sorted(task_checksums)}"
    )
    assert "postgres.sql.gz" in script_checksums
    assert ("workspace.tar.gz" in script_checksums) is has_workspace


def test_the_shell_script_computes_a_real_digest() -> None:
    """The helper has to work on both names the tool ships under.

    coreutils installs it as sha256sum, macOS ships shasum. A backup taken on a
    host with only one of them must still record a usable digest, so the helper
    is executed rather than read.
    """
    import hashlib

    source = SHELL_BACKUP.read_text(encoding="utf-8")
    match = re.search(r"sha256_of\(\) \{\n(.*?)\n\}\n", source, re.DOTALL)
    assert match, "sha256_of was not found in scripts/backup.sh"

    payload = b"trusca backup contract\n"
    target = Path(subprocess.run(["mktemp"], capture_output=True, text=True).stdout.strip())
    target.write_bytes(payload)
    try:
        script = f"set -eu\nsha256_of() {{\n{match.group(1)}\n}}\nsha256_of {target}\n"
        completed = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == hashlib.sha256(payload).hexdigest()
    finally:
        target.unlink(missing_ok=True)
