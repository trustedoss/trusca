"""
Source-tree viewer response schemas — G3.2.

Read-only Pydantic v2 models for the two source-tree-view endpoints backed by
the per-scan tarball preserved in G3.1
(``{workspace_root()}/scan-sources/{project_id}/{scan_id}.tar.gz``):

- GET /v1/projects/{project_id}/source-tree   → :class:`SourceTreePage`
- GET /v1/projects/{project_id}/source-file   → :class:`SourceFileResponse`

The tree endpoint lists the *immediate children* of a directory (lazy, per-dir)
so the UI can render a virtual-scrolled, expand-on-demand tree without ever
materialising the whole member list. The file endpoint returns one file's bytes
(capped) plus the per-LINE license matches projected from the folded scancode
JSON.

These are pure response shapes — no ORM ``from_attributes`` mapping — because
the data is assembled from tar members + ``license_findings`` rows, not a single
table. Every field carries an ``examples`` entry so the auto-generated OpenAPI
doc is self-describing.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Encoding marker for the file-content response. ``utf-8`` files carry decoded
# ``content``; ``binary`` files (NUL byte or undecodable bytes) carry no content.
FileEncoding = Literal["utf-8", "binary"]


class SourceTreeEntry(BaseModel):
    """One immediate child (file or directory) of a listed directory."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "main.py",
                    "path": "src/main.py",
                    "is_dir": False,
                    "byte_size": 1280,
                    "license_spdx_ids": ["MIT", "Apache-2.0"],
                }
            ]
        }
    )

    name: str = Field(description="Base name of the entry (no path separators).")
    path: str = Field(
        description="POSIX path of the entry relative to the source root.",
    )
    is_dir: bool = Field(description="True for a directory, False for a regular file.")
    byte_size: int = Field(
        description=(
            "Uncompressed size of the file in bytes. 0 for directories (their "
            "size is not meaningful in the tar)."
        ),
        ge=0,
    )
    license_spdx_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Cheap per-file license badge set: the distinct SPDX ids recorded "
            "in license_findings for this exact source path under the resolved "
            "scan. Empty for directories and unanalysed files."
        ),
    )


class SourceTreePage(BaseModel):
    """A page of immediate children for one directory."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scan_id": "5b6c0f2e-3a1d-4e8a-9b2c-7d4e1f0a9c33",
                    "path": "src",
                    "entries": [
                        {
                            "name": "main.py",
                            "path": "src/main.py",
                            "is_dir": False,
                            "byte_size": 1280,
                            "license_spdx_ids": ["MIT"],
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 50,
                }
            ]
        }
    )

    scan_id: uuid.UUID = Field(
        description="The scan whose preserved source this tree was read from.",
    )
    path: str = Field(
        description=(
            "The directory whose children are listed. Empty string is the "
            "source root."
        ),
    )
    entries: list[SourceTreeEntry] = Field(
        description="Immediate children of ``path`` on this page (dirs first).",
    )
    total: int = Field(
        description="Total number of immediate children in ``path`` (all pages).",
        ge=0,
    )
    page: int = Field(description="1-based page index for this response.", ge=1)
    size: int = Field(description="Page size used for this response.", ge=1)


class LicenseMatch(BaseModel):
    """A per-line license match projected from the folded scancode JSON."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "spdx_id": "MIT",
                    "start_line": 1,
                    "end_line": 21,
                    "score": 99.5,
                }
            ]
        }
    )

    spdx_id: str = Field(description="SPDX identifier of the matched license.")
    start_line: int = Field(
        description="1-based first line of the match (inclusive).", ge=1
    )
    end_line: int = Field(
        description="1-based last line of the match (inclusive).", ge=1
    )
    score: float | None = Field(
        default=None,
        description="scancode match score (0-100), or null when unreported.",
    )


class SourceFileResponse(BaseModel):
    """A single source file's bytes (capped) + its per-line license matches."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scan_id": "5b6c0f2e-3a1d-4e8a-9b2c-7d4e1f0a9c33",
                    "path": "LICENSE",
                    "byte_size": 1071,
                    "truncated": False,
                    "encoding": "utf-8",
                    "content": "MIT License\n\nCopyright (c) ...",
                    "license_matches": [
                        {
                            "spdx_id": "MIT",
                            "start_line": 1,
                            "end_line": 21,
                            "score": 99.5,
                        }
                    ],
                }
            ]
        }
    )

    scan_id: uuid.UUID = Field(
        description="The scan whose preserved source this file was read from.",
    )
    path: str = Field(description="POSIX path of the file relative to the source root.")
    byte_size: int = Field(
        description="Full uncompressed size of the file in bytes.", ge=0
    )
    truncated: bool = Field(
        description=(
            "True when ``content`` was capped at the viewer's per-file byte "
            "limit and does not contain the whole file."
        ),
    )
    encoding: FileEncoding = Field(
        description=(
            "'utf-8' when ``content`` is decoded text; 'binary' when the file is "
            "non-text (NUL byte or undecodable) and ``content`` is null."
        ),
    )
    content: str | None = Field(
        default=None,
        description=(
            "Decoded file content (possibly truncated). Null for binary files."
        ),
    )
    license_matches: list[LicenseMatch] = Field(
        default_factory=list,
        description=(
            "Per-line license matches for THIS path, projected from the folded "
            "scancode JSON. Empty when the file has no recorded matches."
        ),
    )


__all__ = [
    "FileEncoding",
    "LicenseMatch",
    "SourceFileResponse",
    "SourceTreeEntry",
    "SourceTreePage",
]
