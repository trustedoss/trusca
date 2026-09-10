# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Jira ticket-status adapter (#385).

Driven with ``httpx.MockTransport``, the same technique
``tests/unit/integrations/license_fetcher`` uses: no real network call, no
env-gated skip.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from integrations.ticket_status.base import TicketStatusError, TicketStatusResult
from integrations.ticket_status.jira import JiraTicketStatusAdapter


def _fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    host: str = "example.atlassian.net",
    ticket_key: str = "PROJ-1",
    username: str | None = "bot@example.com",
    api_token: str = "tok",
) -> TicketStatusResult:
    adapter = JiraTicketStatusAdapter(transport=httpx.MockTransport(handler))
    return adapter.fetch(host=host, ticket_key=ticket_key, username=username, api_token=api_token)


def _issue_response(*, status_name: str, category_key: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "fields": {
                "status": {
                    "name": status_name,
                    "statusCategory": {"key": category_key},
                }
            }
        },
    )


def test_done_category_reads_as_resolved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "example.atlassian.net"
        assert request.url.path == "/rest/api/3/issue/PROJ-123"
        assert request.headers["Authorization"].startswith("Basic ")
        return _issue_response(status_name="Done", category_key="done")

    result = _fetch(handler, ticket_key="proj-123")
    assert result.status_name == "Done"
    assert result.resolved is True


def test_indeterminate_category_reads_as_not_resolved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _issue_response(status_name="In Progress", category_key="indeterminate")

    result = _fetch(handler)
    assert result.status_name == "In Progress"
    assert result.resolved is False


def test_renamed_done_column_still_reads_as_resolved() -> None:
    """A customer's custom workflow can rename 'Done' to anything; the
    category, not the name, decides `resolved`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _issue_response(status_name="Shipped to Production", category_key="done")

    result = _fetch(handler)
    assert result.status_name == "Shipped to Production"
    assert result.resolved is True


def test_401_raises_with_no_token_in_the_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorMessages": ["secret-shaped-detail"]})

    with pytest.raises(TicketStatusError) as exc_info:
        _fetch(handler, api_token="super-secret-token")  # noqa: S106
    message = str(exc_info.value)
    assert "super-secret-token" not in message
    assert "secret-shaped-detail" not in message


def test_404_raises_issue_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    with pytest.raises(TicketStatusError, match="no issue"):
        _fetch(handler)


def test_unexpected_redirect_is_refused_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    with pytest.raises(TicketStatusError, match="redirect"):
        _fetch(handler)


def test_oversized_response_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))

    with pytest.raises(TicketStatusError, match="large"):
        _fetch(handler)


def test_malformed_json_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(TicketStatusError, match="not valid JSON"):
        _fetch(handler)


def test_missing_status_field_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"fields": {}})

    with pytest.raises(TicketStatusError, match="no status name"):
        _fetch(handler)


@pytest.mark.parametrize("bad_key", ["", "not a key", "PROJ", "-123", "proj -123"])
def test_malformed_ticket_key_is_rejected_before_any_request(bad_key: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make a request for a malformed key")

    with pytest.raises(TicketStatusError, match="does not look like"):
        _fetch(handler, ticket_key=bad_key)


def test_missing_username_is_rejected_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make a request with no username")

    with pytest.raises(TicketStatusError, match="no account email"):
        _fetch(handler, username=None)
