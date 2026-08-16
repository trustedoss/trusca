/**
 * useUrlState: the rules five list screens now share (B1).
 *
 * Each of these was a decision the three hand-written copies had already
 * converged on. Pinning them here is what stops the fourth and fifth copies
 * from quietly differing.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  PAGE_MAX,
  usePageParam,
  useUrlEnum,
  useUrlFlag,
  useUrlText,
} from "@/hooks/useUrlState";

const STATUSES = ["open", "all", "closed"] as const;

function Probe() {
  const [status, setStatus] = useUrlEnum("status", STATUSES, "open");
  const [page, setPage] = usePageParam();
  const [search, setSearch] = useUrlText("q");
  const [unread, setUnread] = useUrlFlag("unread");
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="page">{page}</span>
      <span data-testid="search">{search}</span>
      <span data-testid="unread">{unread ? "yes" : "no"}</span>
      <span data-testid="search-string">{location.search}</span>
      <button onClick={() => setStatus("closed")} data-testid="set-closed" />
      <button onClick={() => setStatus("open")} data-testid="set-open" />
      <button onClick={() => setPage(3)} data-testid="set-page-3" />
      <button onClick={() => setPage(1)} data-testid="set-page-1" />
      <button onClick={() => setSearch("  alpha  ")} data-testid="set-search" />
      <button
        onClick={() => setSearch("b".repeat(600))}
        data-testid="set-long-search"
      />
      <button onClick={() => setSearch("   ")} data-testid="clear-search" />
      <button
        onClick={() => setPage(PAGE_MAX * 10)}
        data-testid="set-page-huge"
      />
      <button onClick={() => setUnread(true)} data-testid="set-unread" />
      <button onClick={() => setUnread(false)} data-testid="clear-unread" />
      <button
        onClick={() => {
          setPage((p) => p + 1);
          setPage((p) => p + 1);
        }}
        data-testid="advance-twice"
      />
      <button onClick={() => navigate(-1)} data-testid="back" />
    </div>
  );
}

function renderProbe(url = "/list") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Probe />
    </MemoryRouter>,
  );
}

describe("useUrlState", () => {
  it("reads the current URL rather than a copy taken once", async () => {
    // The half ScansPage was missing: it seeded state from the URL and wrote
    // back on change, so Back moved the address bar and left the screen.
    const user = userEvent.setup();
    renderProbe("/list?status=closed");
    expect(screen.getByTestId("status").textContent).toBe("closed");

    await user.click(screen.getByTestId("set-open"));
    expect(screen.getByTestId("status").textContent).toBe("open");
  });

  it("keeps the default out of the URL", async () => {
    const user = userEvent.setup();
    renderProbe("/list?status=closed");

    await user.click(screen.getByTestId("set-open"));
    expect(screen.getByTestId("search-string").textContent).not.toContain(
      "status",
    );
  });

  it("ignores a value outside the vocabulary", () => {
    renderProbe("/list?status=nonsense");
    expect(screen.getByTestId("status").textContent).toBe("open");
  });

  it("sends the reader back to page one when a filter changes", async () => {
    // Narrowing while on page 4 otherwise lands on a page that no longer
    // exists, which reads as an empty result rather than a moved one.
    const user = userEvent.setup();
    renderProbe("/list?page=4&status=closed");
    expect(screen.getByTestId("page").textContent).toBe("4");

    await user.click(screen.getByTestId("set-search"));
    expect(screen.getByTestId("page").textContent).toBe("1");
  });

  it("does not reset the page when the page itself changes", async () => {
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("set-page-3"));
    expect(screen.getByTestId("page").textContent).toBe("3");
  });

  it("keeps page one out of the URL", async () => {
    const user = userEvent.setup();
    renderProbe("/list?page=3");

    await user.click(screen.getByTestId("set-page-1"));
    expect(screen.getByTestId("search-string").textContent).not.toContain(
      "page",
    );
  });

  it.each([
    ["zero", "/list?page=0"],
    ["negative", "/list?page=-2"],
    ["text", "/list?page=abc"],
    ["empty", "/list?page="],
  ])("treats a %s page as page one", (_label, url) => {
    // A page of 0 reaching the backend is a 422, not a filter it ignores.
    renderProbe(url);
    expect(screen.getByTestId("page").textContent).toBe("1");
  });

  it("trims free text and drops it when only whitespace is left", async () => {
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("set-search"));
    expect(screen.getByTestId("search").textContent).toBe("alpha");

    await user.click(screen.getByTestId("clear-search"));
    expect(screen.getByTestId("search-string").textContent).not.toContain("q=");
  });

  it("bounds free text so a URL cannot carry a payload", () => {
    renderProbe(`/list?q=${"a".repeat(500)}`);
    expect(screen.getByTestId("search").textContent).toHaveLength(200);
  });

  it("bounds free text on the way out too", async () => {
    // Bounding only the read leaves the address bar showing a filter the
    // list was never narrowed by: the request carries 200 characters while
    // the URL carries 600.
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("set-long-search"));

    const params = new URLSearchParams(
      screen.getByTestId("search-string").textContent ?? "",
    );
    expect(params.get("q")).toHaveLength(200);
    expect(screen.getByTestId("search").textContent).toHaveLength(200);
  });

  it("clamps a page above the backend's range", () => {
    // The backend answers 422 above PAGE_MAX, and a page number is now
    // something a link or a bookmark can carry.
    renderProbe(`/list?page=${"9".repeat(20)}`);
    expect(screen.getByTestId("page").textContent).toBe(String(PAGE_MAX));
  });

  it("clamps a page above the range on the way out too", async () => {
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("set-page-huge"));

    expect(screen.getByTestId("page").textContent).toBe(String(PAGE_MAX));
  });

  it("puts a flag in the URL only when it is on", async () => {
    const user = userEvent.setup();
    renderProbe("/list");
    expect(screen.getByTestId("unread").textContent).toBe("no");

    await user.click(screen.getByTestId("set-unread"));
    expect(screen.getByTestId("search-string").textContent).toContain("unread=1");

    await user.click(screen.getByTestId("clear-unread"));
    expect(screen.getByTestId("search-string").textContent).not.toContain(
      "unread",
    );
  });

  it("leaves the other parameters alone when one changes", async () => {
    // A screen has several of these, and a setter that rebuilt the whole
    // query string would drop its neighbours.
    const user = userEvent.setup();
    renderProbe("/list?status=closed&unread=1");

    await user.click(screen.getByTestId("set-search"));

    const search = screen.getByTestId("search-string").textContent ?? "";
    expect(search).toContain("status=closed");
    expect(search).toContain("unread=1");
    expect(search).toContain("q=alpha");
  });

  it("does not add a history entry for a value that is already set", async () => {
    // A debounced search writes on a timer. Restoring an earlier term with
    // Back re-arms that timer, and without this the same value would be
    // pushed again, so Back would appear to do nothing at all.
    //
    // Asserted as history depth, not as the URL after one Back: two entries
    // holding the same value are indistinguishable from one by the URL
    // alone, which is how an earlier version of this guard passed while
    // still pushing the duplicate.
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("set-search"));
    await user.click(screen.getByTestId("set-search"));
    expect(screen.getByTestId("search-string").textContent).toBe("?q=alpha");

    await user.click(screen.getByTestId("back"));

    // One Back reaches the URL we started from. A duplicate entry would
    // leave us on ?q=alpha and need a second press.
    expect(screen.getByTestId("search-string").textContent).toBe("");
  });

  it("still clears the page when a filter is re-set to its current value", async () => {
    // The value did not move, but the reader asked for it again, and page 4
    // of the old result set is not where they mean to be.
    const user = userEvent.setup();
    renderProbe("/list?q=alpha&page=4");

    await user.click(screen.getByTestId("set-search"));

    expect(screen.getByTestId("page").textContent).toBe("1");
  });

  it("composes two writes issued in the same batch", async () => {
    // react-router hands the updater the parameters that were current when
    // the setter was built, not live ones, so two Next clicks fast enough to
    // land in one batch would otherwise both start from page 1 and the
    // second would be the only one to take.
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("advance-twice"));

    expect(screen.getByTestId("page").textContent).toBe("3");
  });

  it("makes each change its own history entry, so Back undoes one", async () => {
    const user = userEvent.setup();
    renderProbe("/list");

    await user.click(screen.getByTestId("set-closed"));
    await user.click(screen.getByTestId("set-unread"));
    expect(screen.getByTestId("unread").textContent).toBe("yes");

    // The router owns the history here, not window.history: MemoryRouter
    // keeps its own stack, which is the whole point of using it.
    await user.click(screen.getByTestId("back"));

    expect(screen.getByTestId("unread").textContent).toBe("no");
    expect(screen.getByTestId("status").textContent).toBe("closed");
  });
});
