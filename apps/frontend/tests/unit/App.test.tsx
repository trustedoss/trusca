import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";

function renderApp() {
  return render(
    <AppProviders>
      <App />
    </AppProviders>,
  );
}

describe("App smoke", () => {
  it("mounts the home page with the bootstrap title (EN by default)", () => {
    renderApp();

    expect(screen.getByTestId("home-main")).toBeInTheDocument();
    expect(screen.getByTestId("home-title")).toHaveTextContent(
      /Welcome to TrustedOSS Portal/i,
    );
  });

  it("renders the 5 risk severity tokens once each", () => {
    renderApp();

    const legend = screen.getByTestId("risk-legend");
    const items = legend.querySelectorAll("[data-risk]");
    expect(items).toHaveLength(5);

    const severities = Array.from(items).map((node) =>
      node.getAttribute("data-risk"),
    );
    expect(severities).toEqual(["critical", "high", "medium", "low", "info"]);
  });

  it("toggles the active language between en and ko", async () => {
    const user = userEvent.setup();
    renderApp();

    const toggle = screen.getByTestId("language-toggle");
    expect(toggle).toHaveAttribute("data-current-language", "en");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "ko");
    expect(screen.getByTestId("home-title")).toHaveTextContent(
      /TrustedOSS Portal에 오신 것을 환영합니다/,
    );

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "en");
  });
});
