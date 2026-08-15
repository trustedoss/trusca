/**
 * NotFoundPage: unit tests (A4).
 *
 * Two things are worth holding down here. The page itself has to say which
 * address failed and offer a way out, and the route table has to actually
 * send unknown paths at it: for most of this product's life `*` redirected to
 * `/login`, so a typo looked like a session problem and the page that would
 * have explained it did not exist.
 *
 * The routing half is asserted against `router.tsx` as text rather than by
 * mounting `<AppRoutes />`, which would drag in the auth store, the shell and
 * every query the sidebar makes. The E2E spec walks the real thing; this is
 * the part that runs on every PR, since the E2E job does not.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { NotFoundPage } from "@/pages/NotFoundPage";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROUTER_PATH = path.join(
  __dirname,
  "..",
  "..",
  "..",
  "src",
  "router.tsx",
);

function renderAt(pathname: string) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <NotFoundPage />
    </MemoryRouter>,
  );
}

describe("NotFoundPage", () => {
  it("says the page does not exist and echoes the address that failed", () => {
    renderAt("/prjects/42?range=90d");

    const page = screen.getByTestId("not-found-page");
    expect(page.textContent).toContain("This page does not exist.");
    // The address is the whole point: "not found" without naming what was not
    // found leaves the user guessing at their own typo. The query string is
    // part of the address the user is looking at, so it is part of the echo.
    expect(screen.getByTestId("not-found-page-path").textContent).toContain(
      "/prjects/42?range=90d",
    );
  });

  it("gives the screen a heading", () => {
    // EmptyState's title is a <p>, and AppShell supplies no <h1>, so without
    // an explicit one this screen has no heading for a reader navigating by
    // headings: unlike AdminNotFound, the 404 it is modelled on.
    renderAt("/nope");

    expect(
      screen.getByRole("heading", { level: 1 }).textContent,
    ).toBe("Page not found");
  });

  it("offers a way out", () => {
    renderAt("/nope");

    expect(
      screen.getByTestId("not-found-page-home").getAttribute("href"),
    ).toBe("/");
  });
});

describe("the route table", () => {
  const source = fs.readFileSync(ROUTER_PATH, "utf8");

  it("sends unknown paths to NotFoundPage", () => {
    expect(source).toContain('<Route path="*" element={<NotFoundPage />} />');
  });

  it("no longer answers an unknown path with a redirect to /login", () => {
    // The exact shape that used to be there. A signed-in user who mistyped a
    // URL was bounced through the sign-in form and back to the dashboard,
    // with nothing anywhere saying the address was wrong.
    expect(source).not.toContain('path="*" element={<Navigate to="/login"');
  });

  it("keeps the catch-all behind the auth guard", () => {
    // NotFoundPage must be nested inside the `/` route that <RequireAuth />
    // wraps. If it ever moved out to the top level, an anonymous visitor
    // could tell which paths exist by which ones 404 instead of redirecting.
    const guardAt = source.indexOf("<RequireAuth>");
    const catchAllAt = source.indexOf(
      '<Route path="*" element={<NotFoundPage />} />',
    );
    const routesCloseAt = source.indexOf("</Routes>");
    expect(guardAt).toBeGreaterThan(-1);
    expect(catchAllAt).toBeGreaterThan(guardAt);
    // …and still inside the route table, not after it.
    expect(catchAllAt).toBeLessThan(routesCloseAt);
  });
});
