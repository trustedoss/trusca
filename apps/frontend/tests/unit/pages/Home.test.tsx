import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Home } from "@/pages/Home";

describe("Home", () => {
  it("redirects to the dashboard root", () => {
    // The "/" index now renders the Dashboard directly; the legacy Home
    // safety-net redirect points at "/". We mount it under a distinct path so
    // the redirect target ("/") resolves to a sentinel we can assert on.
    const { container } = render(
      <MemoryRouter initialEntries={["/legacy"]}>
        <Routes>
          <Route path="/legacy" element={<Home />} />
          <Route
            path="/"
            element={<div data-testid="dashboard-page">dashboard</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      container.querySelector('[data-testid="dashboard-page"]'),
    ).not.toBeNull();
  });
});
