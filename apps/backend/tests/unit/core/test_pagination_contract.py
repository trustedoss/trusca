# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``core.pagination``: the one shape new list endpoints use.

The API has three pagination spellings, counted from the published OpenAPI
schema: ``limit``/``offset`` on eight endpoints, ``page``/``page_size`` on
fifteen, and ``page``/``size`` on six, with defaults and maxima varying inside
each group. Nothing made a new endpoint match an existing one, so a client
needs a branch per shape.

These pin the shape that stops that count growing. They drive it through a
real FastAPI app rather than constructing the dataclass directly, because the
failure worth catching is the wiring: a dependency whose parameters do not
reach the query string, or bounds that do not reach the schema, still looks
correct when the class is instantiated by hand.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PAGE_MAX,
    Page,
    PageParams,
)


class _Thing(BaseModel):
    name: str


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()

    @app.get("/things", response_model=Page[_Thing])
    def list_things(params: PageParams = Depends()) -> Page[_Thing]:
        rows = [_Thing(name=f"t{i}") for i in range(params.offset, params.offset + 2)]
        return Page.of(rows, total=57, params=params)

    return TestClient(app)


# ---------------------------------------------------------------------------
# The wiring: parameters and bounds have to reach the HTTP surface
# ---------------------------------------------------------------------------


def test_the_dependency_publishes_exactly_page_and_page_size(
    client: TestClient,
) -> None:
    """The spelling is the contract, so it is asserted from the schema.

    `page` / `page_size`, not `page` / `size` and not `limit` / `offset`. A
    fourth spelling entering the API is the outcome this whole unit exists to
    prevent.
    """
    schema = client.get("/openapi.json").json()
    names = [q["name"] for q in schema["paths"]["/things"]["get"]["parameters"]]

    assert names == ["page", "page_size"]


def test_the_bounds_are_published_not_just_enforced(client: TestClient) -> None:
    """A generated client reads the schema, so the caps have to be in it."""
    schema = client.get("/openapi.json").json()
    params = {q["name"]: q["schema"] for q in schema["paths"]["/things"]["get"]["parameters"]}

    assert params["page"]["default"] == 1
    assert params["page"]["maximum"] == PAGE_MAX
    assert params["page_size"]["default"] == DEFAULT_PAGE_SIZE
    assert params["page_size"]["maximum"] == MAX_PAGE_SIZE


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_defaults_apply_when_the_caller_asks_for_nothing(
    client: TestClient,
) -> None:
    body = client.get("/things").json()

    assert body["page"] == 1
    assert body["page_size"] == DEFAULT_PAGE_SIZE
    assert body["total"] == 57


def test_page_is_one_based_and_offset_follows_from_it(
    client: TestClient,
) -> None:
    """Page 3 of 25 starts at row 50, not 75. Off-by-one here is silent.

    A caller sees plausible rows either way; only the rows between 50 and 75
    go missing, which no single response reveals.
    """
    body = client.get("/things?page=3&page_size=25").json()

    assert [item["name"] for item in body["items"]] == ["t50", "t51"]
    assert PageParams(page=3, page_size=25).offset == 50
    assert PageParams(page=1, page_size=25).offset == 0


def test_the_response_echoes_what_was_asked_rather_than_recomputing(
    client: TestClient,
) -> None:
    body = client.get("/things?page=2&page_size=10").json()

    assert body["page"] == 2
    assert body["page_size"] == 10


def test_total_is_the_unpaginated_count(client: TestClient) -> None:
    """`total` is what the query matched, not how many rows came back.

    A caller divides it by `page_size` to find the last page, so returning
    `len(items)` would make every response look like a single page.
    """
    body = client.get("/things?page_size=2").json()

    assert len(body["items"]) == 2
    assert body["total"] == 57


# ---------------------------------------------------------------------------
# Rejection, not silent clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "why"),
    [
        (f"page_size={MAX_PAGE_SIZE + 1}", "above the page-size cap"),
        ("page_size=0", "zero rows is not a page"),
        ("page=0", "pages are 1-based"),
        (f"page={PAGE_MAX + 1}", "above the page cap"),
        ("page=-1", "negative page"),
    ],
)
def test_out_of_range_is_a_422_naming_the_parameter(
    client: TestClient, query: str, why: str
) -> None:
    """Rejected, not clamped: a clamp gives a caller rows it did not ask for.

    The page cap is what keeps `(page - 1) * page_size` inside int64. Without
    it an oversized page number reached asyncpg and became a 500, which is the
    defect PAGE_MAX was introduced for.
    """
    response = client.get(f"/things?{query}")

    assert response.status_code == 422, why
    assert "page" in response.text


def test_the_page_cap_keeps_the_offset_inside_int64() -> None:
    """The arithmetic the cap exists to bound, asserted rather than assumed."""
    largest = PageParams(page=PAGE_MAX, page_size=MAX_PAGE_SIZE).offset

    assert largest < 2**63 - 1


# ---------------------------------------------------------------------------
# The counts the API overview publishes
# ---------------------------------------------------------------------------
#
# The overview names how many endpoints use each spelling. Those numbers are
# the reason a reader checks their endpoint instead of assuming one shape, so
# they have to stay true as endpoints are added. Derived from the committed
# OpenAPI snapshot, which is regenerated from the app and drift-gated
# separately, so this cannot pass against a stale spec.


def _shape_counts() -> dict[str, int]:
    import json
    from pathlib import Path

    for candidate in Path(__file__).resolve().parents:
        spec = candidate / "docs-site" / "static" / "openapi.json"
        if spec.is_file():
            break
    else:
        pytest.skip("openapi.json not found above this file")

    document = json.loads(spec.read_text(encoding="utf-8"))
    counts = {"limit+offset": 0, "page+page_size": 0, "page+size": 0}
    for operations in document["paths"].values():
        get = operations.get("get")
        if not get:
            continue
        names = {q["name"] for q in (get.get("parameters") or [])}
        if {"page", "size"} <= names:
            counts["page+size"] += 1
        elif {"page", "page_size"} <= names:
            counts["page+page_size"] += 1
        elif {"limit", "offset"} <= names:
            counts["limit+offset"] += 1
    return counts


def test_the_overview_states_the_real_endpoint_counts() -> None:
    """Update the overview's table when this fails; do not update the numbers here.

    A new endpoint on an old spelling is exactly what the overview warns
    readers about, so the page has to keep saying how many there are.
    """
    from pathlib import Path

    counts = _shape_counts()
    for candidate in Path(__file__).resolve().parents:
        overview = candidate / "docs-site" / "docs" / "reference" / "api-overview.md"
        if overview.is_file():
            break
    else:
        pytest.skip("api-overview.md not found above this file")

    text = overview.read_text(encoding="utf-8")
    for shape, label in (
        ("limit+offset", "Offset"),
        ("page+page_size", "Numbered (`page_size`)"),
        ("page+size", "Numbered (`size`)"),
    ):
        row = next(
            (line for line in text.splitlines() if line.startswith(f"| {label} |")),
            None,
        )
        assert row is not None, f"the pagination table has no {label!r} row"
        assert row.rstrip().endswith(f"| {counts[shape]} |"), (
            f"api-overview.md says {row.strip()!r} but the OpenAPI schema has "
            f"{counts[shape]} endpoints using {shape}"
        )


def test_new_endpoints_did_not_add_a_fourth_spelling() -> None:
    """Every paginated GET uses one of the three shapes the overview lists.

    A fourth would be invisible to the table above, which only counts the
    three it knows, so it is asserted separately.
    """
    import json
    from pathlib import Path

    for candidate in Path(__file__).resolve().parents:
        spec = candidate / "docs-site" / "static" / "openapi.json"
        if spec.is_file():
            break
    else:
        pytest.skip("openapi.json not found above this file")

    document = json.loads(spec.read_text(encoding="utf-8"))
    strays = []
    for path, operations in document["paths"].items():
        get = operations.get("get")
        if not get:
            continue
        names = {q["name"] for q in (get.get("parameters") or [])}
        paging = names & {"page", "page_size", "size", "limit", "offset"}
        if not paging:
            continue
        if paging in ({"page", "size"}, {"page", "page_size"}, {"limit", "offset"}):
            continue
        strays.append((path, sorted(paging)))

    assert not strays, (
        "these endpoints paginate with a spelling the API overview does not " f"describe: {strays}"
    )
