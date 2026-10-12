# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""What the guides promise about SCAN_BOMLENS_STAGE1_IMAGE_PREFIX.

The offline-install guide tells an operator how to list the cdxgen images a
BomLens release can choose, how to copy them, and what to set afterwards.
Each of those is a command or a value an operator types, so each is run or
checked here: the listing and copy commands are run (``skopeo`` replaced by a
recorder) on ``cdxgen-images.json`` as the pinned BomLens image ships it, the
copies must land under the prefix the guide then sets, and that value must be
one the runner accepts. The reference pages and the template must name the
key, and the "no mirror setting" warning it replaced must stay gone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from integrations.bomlens.runner import stage1_image_prefix

REPO_ROOT = Path(__file__).resolve().parents[4]
PLAN_FIXTURES = REPO_ROOT / "apps" / "backend" / "tests" / "fixtures" / "bomlens-plan"
KEY = "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX"
KO = Path("docs-site/i18n/ko/docusaurus-plugin-content-docs/current")
OFFLINE = (
    Path("docs-site/docs/admin-guide/offline-install.md"),
    KO / "admin-guide/offline-install.md",
)
REFERENCES = (
    Path("docs-site/docs/admin-guide/dynamic-scan-executor.md"),
    KO / "admin-guide/dynamic-scan-executor.md",
    Path("docs-site/docs/reference/env-variables.md"),
    KO / "reference/env-variables.md",
)
SECTION = "{#bomlens-stage1-images}"
PLACEHOLDER_REGISTRY = "<your-registry>"


def _read(path: Path) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _section(text: str) -> str:
    """From the stage 1 heading to the next heading, skipping code blocks
    (a shell comment such as ``# In the portal's .env`` is not a heading)."""
    lines = text[text.index(SECTION) :].splitlines(keepends=True)
    kept = [lines[0]]
    fenced = False
    for line in lines[1:]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,3} ", line):
            break
        kept.append(line)
    return "".join(kept)


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)


@pytest.mark.parametrize("path", [*OFFLINE, *REFERENCES, Path(".env.example")], ids=str)
def test_every_operator_document_names_the_setting(path: Path) -> None:
    assert KEY in _read(path)


@pytest.mark.parametrize("path", OFFLINE, ids=str)
def test_the_no_mirror_warning_is_gone(path: Path) -> None:
    text = _read(path)
    for stale in ("No mirror setting", "has no setting that points", "미러 설정이 없"):
        assert stale not in text, f"{path} still says {stale!r}"


@pytest.mark.parametrize("path", OFFLINE, ids=str)
def test_the_guide_sets_a_prefix_the_runner_accepts(path: Path) -> None:
    section = _section(_read(path))
    values = re.findall(rf"^{KEY}=(\S+)$", section, re.MULTILINE)
    assert len(values) == 1, values
    concrete = values[0].replace(PLACEHOLDER_REGISTRY, "registry.example")
    assert PLACEHOLDER_REGISTRY in values[0]
    assert stage1_image_prefix(concrete) == concrete


def _published_list() -> dict[str, object]:
    data: dict[str, object] = json.loads((PLAN_FIXTURES / "cdxgen-images.json").read_text())
    return data


def test_the_captured_plan_names_an_image_from_the_published_list() -> None:
    """The list the guide reads is the one the plan's digests come from."""
    listing = _published_list()
    plan = json.loads((PLAN_FIXTURES / "node-ok" / "bomlens-plan.json").read_text())
    generate = next(s for s in plan["stages"] if s["id"] == "generate")
    repo, tag = generate["image"].rsplit(":", 1)
    assert listing["tag"] == tag
    assert listing["digests"][repo] == generate["image_digest"]  # type: ignore[index]


def _need_tools() -> None:
    missing = [t for t in ("bash", "jq") if shutil.which(t) is None]
    if missing:
        assert os.getenv("CI") != "true", f"CI runner lacks {missing}"
        pytest.skip(f"needs {missing} on PATH")


@pytest.mark.parametrize("path", OFFLINE, ids=str)
def test_the_listing_command_prints_each_pinned_image(path: Path) -> None:
    _need_tools()
    blocks = _bash_blocks(_section(_read(path)))
    (listing,) = [b for b in blocks if b.startswith("docker run")]
    jq_line = next(line for line in listing.splitlines() if line.startswith("jq "))
    out = subprocess.run(
        ["bash", "-c", jq_line],
        cwd=PLAN_FIXTURES,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    listing_data = _published_list()
    digests: dict[str, str] = listing_data["digests"]  # type: ignore[assignment]
    expected = [f"{repo}:{listing_data['tag']}@{digest}" for repo, digest in digests.items()]
    assert out == expected
    assert "/usr/local/lib/sbom/cdxgen-images.json" in listing


@pytest.mark.parametrize("path", OFFLINE, ids=str)
def test_the_copy_loop_keeps_every_platform_and_digest_under_the_set_prefix(
    path: Path, tmp_path: Path
) -> None:
    """Run the guide's loop with ``skopeo`` recording its arguments."""
    _need_tools()
    section = _section(_read(path))
    (loop,) = [b for b in _bash_blocks(section) if "skopeo copy" in b]
    prefix = re.search(rf"^{KEY}=(\S+)$", section, re.MULTILINE)
    assert prefix is not None
    shutil.copyfile(PLAN_FIXTURES / "cdxgen-images.json", tmp_path / "cdxgen-images.json")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = bin_dir / "skopeo"
    recorder.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$RECORD"\n')
    recorder.chmod(0o755)
    record = tmp_path / "record.txt"
    subprocess.run(
        ["bash", "-c", loop],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RECORD": str(record)},
        check=True,
    )
    calls = record.read_text().splitlines()
    listing = _published_list()
    digests: dict[str, str] = listing["digests"]  # type: ignore[assignment]
    expected = [
        "copy --all --preserve-digests "
        f"docker://{repo}@{digest} "
        f"docker://{prefix.group(1)}{repo.removeprefix('ghcr.io/cdxgen/')}:{listing['tag']}"
        for repo, digest in digests.items()
    ]
    assert calls == expected


def test_the_mirror_capture_is_the_published_list_under_the_prefix() -> None:
    """What the guide says the plan does with the prefix, from the real image."""
    plan = json.loads((PLAN_FIXTURES / "node-mirror-prefix" / "bomlens-plan.json").read_text())
    generate = next(s for s in plan["stages"] if s["id"] == "generate")
    repo, tag = generate["image"].rsplit(":", 1)
    published = "ghcr.io/cdxgen/" + repo.removeprefix("registry.example/mirror/cdxgen/")
    listing = _published_list()
    assert repo.startswith("registry.example/mirror/cdxgen/")
    assert listing["digests"][published] == generate["image_digest"]  # type: ignore[index]
    assert listing["tag"] == tag
