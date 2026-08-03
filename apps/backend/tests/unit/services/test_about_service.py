# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Unit tests for the About surface's notice-file reads.

The interesting failures here are not "does it read a file" but:

  * the notice directory resolving to the WRONG place (an image ships them at
    /licenses/, a checkout has them at the repo root),
  * a missing file being silently dropped from the list, which would make a
    broken deployment look complete,
  * the document catalogue drifting from the files that are actually shipped —
    the same class of drift ``test_license_distribution.py`` guards for the
    Dockerfiles.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from services import about_service
from services.about_service import (
    NOTICE_DOCUMENTS,
    NoticeNotFoundError,
    NoticeTooLargeError,
    available_documents,
    notice_dir,
    notice_dir_candidates,
    product_identity,
    read_document,
)


def _shipped(directory: Path, *filenames: str) -> Path:
    """Write the named notice files into ``directory`` and return it.

    ``notice_dir`` requires LICENSE *and* NOTICE, so a fixture that ships only
    one of them is not a valid notice directory — pass both unless the case is
    specifically about an incomplete one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (directory / name).write_text(f"{name} contents\n", encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def _clear_notice_dir_cache() -> Iterator[None]:
    """``notice_dir`` is lru_cached; each test resolves it fresh."""
    notice_dir.cache_clear()
    yield
    notice_dir.cache_clear()


def test_notice_dir_resolves_to_the_repo_root_in_a_checkout() -> None:
    """Outside a container the repo root is the fallback, and it has LICENSE."""
    resolved = notice_dir()
    assert resolved is not None
    assert (resolved / "LICENSE").is_file()
    assert (resolved / "NOTICE").is_file()
    assert (resolved / "THIRD_PARTY_NOTICES.md").is_file()


def test_image_notice_dir_wins_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/licenses/`` takes precedence — that is where every image puts them."""
    _shipped(tmp_path, "LICENSE", "NOTICE")
    monkeypatch.setattr(about_service, "_IMAGE_NOTICE_DIR", tmp_path)
    notice_dir.cache_clear()

    assert notice_dir() == tmp_path


def test_notice_dir_is_none_when_nothing_is_shipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment with no notices reports absence rather than guessing."""
    monkeypatch.setattr(
        about_service,
        "notice_dir_candidates",
        lambda module_file=None: (tmp_path / "nope", tmp_path / "also-nope"),
    )
    notice_dir.cache_clear()

    assert notice_dir() is None


def test_candidates_survive_the_container_layout() -> None:
    """Regression: a fixed ancestor index crashed the app at import time.

    In the image this module lives at ``/app/services/about_service.py``, which
    has exactly three ancestors. The first version of this code took
    ``parents[3]`` as the repo root — fine in a checkout, ``IndexError`` in the
    container. Because it ran at module scope it did not degrade this one
    endpoint; the application never started and the backend never went healthy.

    Walking ancestors has no index to get wrong, and this pins that.
    """
    candidates = notice_dir_candidates(Path("/app/services/about_service.py"))

    assert candidates[0] == Path("/licenses")
    assert Path("/app") in candidates
    # No exception, and the walk terminates at the filesystem root.
    assert candidates[-1] == Path("/")


def test_candidates_still_reach_the_repo_root_in_a_checkout(tmp_path: Path) -> None:
    """The deep-checkout case the container layout must not break.

    Built under ``tmp_path`` rather than a literal path: ``resolve()`` follows
    symlinks, and on macOS a hardcoded ``/home/...`` comes back as
    ``/System/Volumes/Data/home/...``, which would make this assert about the
    host rather than about the walk.
    """
    repo_root = tmp_path / "work" / "trusca"
    module = repo_root / "apps" / "backend" / "services" / "about_service.py"

    candidates = notice_dir_candidates(module)

    assert repo_root.resolve() in candidates
    assert candidates.index(repo_root.resolve()) > candidates.index(
        module.parent.resolve()
    ), "ancestors must be ordered nearest-first"


def test_notice_dir_requires_both_license_and_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lone LICENSE somewhere above the checkout must not be mistaken for ours.

    The walk goes all the way to ``/``, so matching on LICENSE alone could latch
    onto an unrelated file. Requiring NOTICE beside it makes a false match far
    less likely.
    """
    lone_license = _shipped(tmp_path / "lone", "LICENSE")
    complete = _shipped(tmp_path / "complete", "LICENSE", "NOTICE")
    monkeypatch.setattr(
        about_service,
        "notice_dir_candidates",
        lambda module_file=None: (lone_license, complete),
    )
    notice_dir.cache_clear()

    assert notice_dir() == complete


def test_available_documents_lists_every_catalogue_entry() -> None:
    """All three documents are listed, each with a real size."""
    listed = available_documents()

    assert [doc.id for doc, _ in listed] == [doc.id for doc in NOTICE_DOCUMENTS]
    for doc, size in listed:
        assert size is not None and size > 0, doc.id


def test_missing_files_are_listed_with_a_null_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packaging fault stays visible instead of shortening the list.

    LICENSE and NOTICE are present (that pair is what makes this a notice
    directory at all); THIRD_PARTY_NOTICES.md is not. It must still appear, with
    a ``None`` size, so the UI can say "missing" rather than quietly showing two
    tabs and looking complete.
    """
    _shipped(tmp_path, "LICENSE", "NOTICE")
    monkeypatch.setattr(about_service, "_IMAGE_NOTICE_DIR", tmp_path)
    notice_dir.cache_clear()

    listed = dict((doc.id, size) for doc, size in available_documents())

    assert len(listed) == len(NOTICE_DOCUMENTS)
    assert listed["license"] is not None
    assert listed["notice"] is not None
    assert listed["third-party-notices"] is None


@pytest.mark.parametrize("document_id", [doc.id for doc in NOTICE_DOCUMENTS])
def test_read_document_returns_the_real_text(document_id: str) -> None:
    doc, text = read_document(document_id)

    assert doc.id == document_id
    assert text.strip(), "a notice document must not be empty"


def test_read_document_is_verbatim() -> None:
    """The bytes served match the file on disk — no reflow, no truncation."""
    base = notice_dir()
    assert base is not None
    _doc, text = read_document("license")

    assert text == (base / "LICENSE").read_text(encoding="utf-8")


def test_license_text_is_apache_2() -> None:
    """Guards against serving some other license under our SPDX id."""
    _doc, text = read_document("license")

    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "Copyright 2026 TRUSCA contributors" in text


def test_third_party_notices_credit_the_upstream_holder() -> None:
    """The §4(d) attribution has to be in what we actually serve."""
    _doc, text = read_document("third-party-notices")

    assert "SK Telecom Co., Ltd." in text
    assert "github.com/sktelecom/bomlens" in text


def test_unknown_document_id_raises() -> None:
    with pytest.raises(NoticeNotFoundError):
        read_document("no-such-document")


def test_absent_file_raises_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known id whose file is missing is a 404, not a crash."""
    _shipped(tmp_path, "LICENSE", "NOTICE")
    monkeypatch.setattr(about_service, "_IMAGE_NOTICE_DIR", tmp_path)
    notice_dir.cache_clear()

    with pytest.raises(NoticeNotFoundError):
        read_document("third-party-notices")


def test_oversized_file_raises_rather_than_streaming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size ceiling bounds the response even with a hostile mount."""
    _shipped(tmp_path, "NOTICE")
    (tmp_path / "LICENSE").write_text("A" * 128, encoding="utf-8")
    monkeypatch.setattr(about_service, "_IMAGE_NOTICE_DIR", tmp_path)
    monkeypatch.setattr(about_service, "MAX_DOCUMENT_BYTES", 64)
    notice_dir.cache_clear()

    with pytest.raises(NoticeTooLargeError):
        read_document("license")


def test_document_ids_are_unique_and_url_safe() -> None:
    """Ids are path segments in the notice route."""
    ids = [doc.id for doc in NOTICE_DOCUMENTS]

    assert len(ids) == len(set(ids))
    for document_id in ids:
        assert document_id == document_id.lower()
        assert " " not in document_id
        assert "/" not in document_id


def test_product_identity_matches_the_repository_notices() -> None:
    """Identity strings agree with LICENSE / NOTICE rather than drifting.

    This is the CLAUDE.md hardening rule 2 case: the copyright line now lives in
    LICENSE, NOTICE, the SPDX headers, and here. A mismatch would show a
    different holder in the UI than in the files beside it.
    """
    identity = product_identity()
    base = notice_dir()
    assert base is not None

    assert identity["product"] == "TRUSCA"
    assert identity["license_spdx_id"] == "Apache-2.0"
    assert identity["copyright"] in (base / "NOTICE").read_text(encoding="utf-8")
    assert identity["copyright"] in (base / "LICENSE").read_text(encoding="utf-8")
    assert identity["source_url"] in (base / "NOTICE").read_text(encoding="utf-8")
    assert identity["version"]
