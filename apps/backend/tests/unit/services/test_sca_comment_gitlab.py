# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Posting the SCA report to a GitLab merge request (ER47).

The GitHub client next door is covered by ``test_sca_comment.py``. These cover
what is different, which is the whole reason there are two implementations: the
auth header, the verb an update uses, the path a note lives under, and the
field carrying the link.

On the fixtures
---------------
The note bodies below carry every field the GitLab REST documentation lists for
a merge request note, not the three this code reads. Hardening rule 3: a
minimal fixture only exercises the fields somebody remembered, and the ones it
leaves out are where a parser meets something it did not expect. `system`,
`resolvable` and the author object are all things a real thread contains and
this code has to walk past.

What is NOT verified here
-------------------------
These responses were written from the GitLab REST documentation for merge
request notes (docs.gitlab.com/api/notes/) and have not been checked against a
running instance. That is a different state from "confirmed", and the
difference matters: a field GitLab renamed, or an error shape it returns that
the documentation does not show, would pass here and fail on first contact.
When somebody runs this against a real GitLab, replace this paragraph with what
they saw rather than deleting it silently.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.sca_comment import (
    COMMENT_MARKER,
    SCACommentUnauthorized,
    post_pr_comment,
)

# The GitHub file next door already builds these correctly. Rebuilding them
# here would be a second definition of the same fixture, and the first attempt
# at one got the field names wrong: the type moved and only the real factory
# knew.
from tests.unit.services.test_sca_comment import _make_gate_result, _make_summary

PROJECT = "acme/platform/api"
ENCODED = "acme%2Fplatform%2Fapi"
MR_IID = 42
TOKEN = "glpat-not-a-real-token"


def _note(note_id: int, body: str) -> dict[str, Any]:
    """One note, shaped the way the API documents it rather than minimally."""
    return {
        "id": note_id,
        "type": None,
        "body": body,
        "attachment": None,
        "author": {
            "id": 7,
            "username": "trustedoss-bot",
            "name": "TRUSCA",
            "state": "active",
            "avatar_url": "https://gitlab.example.com/uploads/-/system/user/avatar/7/a.png",
            "web_url": "https://gitlab.example.com/trustedoss-bot",
        },
        "created_at": "2026-09-04T10:00:00.000Z",
        "updated_at": "2026-09-04T10:00:00.000Z",
        "system": False,
        "noteable_id": 300,
        "noteable_type": "MergeRequest",
        "project_id": 5,
        "noteable_iid": MR_IID,
        "resolvable": False,
        "confidential": False,
        "internal": False,
        "web_url": f"https://gitlab.example.com/{PROJECT}/-/merge_requests/{MR_IID}#note_{note_id}",
    }


class _Recorder:
    """Collects the requests made, and answers them from a script."""

    def __init__(self, script: list[tuple[int, Any]]) -> None:
        self.script = script
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        status, payload = self.script.pop(0)
        return httpx.Response(status, json=payload, request=request)


def _client(recorder: _Recorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(recorder))


async def _post(recorder: _Recorder, **kwargs: Any):  # noqa: ANN202
    async with _client(recorder) as client:
        return await post_pr_comment(
            repo_full_name=PROJECT,
            pr_number=MR_IID,
            gate_result=_make_gate_result(),
            summary=_make_summary(),
            github_token=TOKEN,
            provider="gitlab",
            http_client=client,
            **kwargs,
        )


async def test_a_first_comment_is_created_under_the_merge_request() -> None:
    recorder = _Recorder([(200, []), (201, _note(101, COMMENT_MARKER + "\nbody"))])

    result = await _post(recorder)

    assert result.status == "posted"
    assert result.comment_id == 101
    assert "#note_101" in (result.comment_url or ""), result.comment_url

    listing, creating = recorder.calls
    assert listing.method == "GET"
    assert f"/projects/{ENCODED}/merge_requests/{MR_IID}/notes" in str(listing.url), (
        f"the project path was not encoded into one segment: {listing.url}"
    )
    assert creating.method == "POST"


async def test_an_existing_comment_is_updated_with_put() -> None:
    """PUT, where the GitHub client PATCHes, and under the merge request.

    Both halves matter. A PATCH is refused by GitLab, and a note id on its own
    is not addressable: the path carries the merge request too.
    """
    existing = _note(202, COMMENT_MARKER + "\nold body")
    recorder = _Recorder([(200, [existing]), (200, _note(202, COMMENT_MARKER + "\nnew"))])

    result = await _post(recorder)

    assert result.status == "updated"
    assert result.comment_id == 202

    _listing, updating = recorder.calls
    assert updating.method == "PUT", (
        f"updated with {updating.method}; GitLab refuses PATCH on a note"
    )
    assert f"/merge_requests/{MR_IID}/notes/202" in str(updating.url), updating.url


async def test_a_note_without_our_marker_is_left_alone() -> None:
    """A thread full of human comments must not be edited.

    The fixture puts a real-looking discussion in front of ours, including a
    system note, because that is what a merge request actually contains.
    """
    others = [
        _note(1, "Looks good to me"),
        {**_note(2, "assigned to @someone"), "system": True},
        _note(3, "Can you rebase?"),
    ]
    recorder = _Recorder([(200, others), (201, _note(104, COMMENT_MARKER + "\nbody"))])

    result = await _post(recorder)

    assert result.status == "posted", "an unrelated note was mistaken for ours"
    assert recorder.calls[1].method == "POST"


async def test_the_token_travels_as_a_private_token_header() -> None:
    """GitLab reads PRIVATE-TOKEN; a bearer is silently unauthenticated."""
    recorder = _Recorder([(200, []), (201, _note(105, COMMENT_MARKER))])

    await _post(recorder)

    listing = recorder.calls[0]
    assert listing.headers.get("PRIVATE-TOKEN") == TOKEN
    assert "authorization" not in {k.lower() for k in listing.headers}, (
        "a bearer header was sent as well; GitLab ignores it and the presence "
        "of two schemes hides which one is being relied on"
    )


async def test_an_unauthorised_response_is_raised_not_swallowed() -> None:
    recorder = _Recorder([(401, {"message": "401 Unauthorized"})])

    with pytest.raises(SCACommentUnauthorized):
        await _post(recorder)


async def test_the_base_url_follows_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted instance is the ordinary case, not the exception."""
    monkeypatch.setenv("GITLAB_API_BASE", "https://gitlab.corp.example/api/v4/")
    recorder = _Recorder([(200, []), (201, _note(106, COMMENT_MARKER))])

    await _post(recorder)

    assert str(recorder.calls[0].url).startswith(
        "https://gitlab.corp.example/api/v4/projects/"
    ), (
        f"the request went to {recorder.calls[0].url}; a deployment that set "
        "GITLAB_API_BASE would be talking to gitlab.com instead of its own "
        "instance, and the trailing slash in the setting has to be absorbed"
    )


async def test_without_the_setting_it_talks_to_gitlab_com(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default half of the pair, so the test above is known to be moving
    something rather than describing the only behaviour there is."""
    monkeypatch.delenv("GITLAB_API_BASE", raising=False)
    recorder = _Recorder([(200, []), (201, _note(107, COMMENT_MARKER))])

    await _post(recorder)

    assert str(recorder.calls[0].url).startswith("https://gitlab.com/api/v4/projects/")
