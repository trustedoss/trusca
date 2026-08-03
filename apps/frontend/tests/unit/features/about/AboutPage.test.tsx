// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * AboutPage — unit tests for the in-product license-notice surface.
 *
 * Covers:
 *   - Identity fields render from the API (product, version, license, copyright).
 *   - A notice document loads on demand and renders VERBATIM inside <pre>.
 *   - Switching tabs fetches the newly selected document, not everything upfront.
 *   - A document missing from the deployment (size_bytes: null) shows the
 *     packaging-fault empty state and does NOT request the body.
 *   - Load failures surface instead of rendering a blank page.
 *   - Tab labels come from translations, not from the API's English strings.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AboutPage from "@/features/about/AboutPage";
import type { About, NoticeDocument } from "@/features/about/api/aboutApi";

vi.mock("@/features/about/api/aboutApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/about/api/aboutApi")
  >("@/features/about/api/aboutApi");
  return { ...actual, getAbout: vi.fn(), getNotice: vi.fn() };
});

import { getAbout, getNotice } from "@/features/about/api/aboutApi";

const mockedGetAbout = vi.mocked(getAbout);
const mockedGetNotice = vi.mocked(getNotice);

const APACHE_TEXT = `                                 Apache License
                           Version 2.0, January 2004

   Copyright 2026 TRUSCA contributors
`;

function doc(overrides: Partial<NoticeDocument> = {}): NoticeDocument {
  return {
    id: overrides.id ?? "license",
    title: overrides.title ?? "Apache License, Version 2.0",
    filename: overrides.filename ?? "LICENSE",
    description: overrides.description ?? "The license TRUSCA is under.",
    size_bytes: overrides.size_bytes === undefined ? 11291 : overrides.size_bytes,
  };
}

function about(overrides: Partial<About> = {}): About {
  return {
    product: "TRUSCA",
    version: "2.3.0-dev",
    license_spdx_id: "Apache-2.0",
    license_name: "Apache License, Version 2.0",
    license_url: "https://www.apache.org/licenses/LICENSE-2.0",
    copyright: "Copyright 2026 TRUSCA contributors",
    source_url: "https://github.com/trustedoss/trusca",
    documents: overrides.documents ?? [
      doc(),
      doc({ id: "notice", title: "Notice", filename: "NOTICE", size_bytes: 631 }),
      doc({
        id: "third-party-notices",
        title: "Third-party notices",
        filename: "THIRD_PARTY_NOTICES.md",
        size_bytes: 7586,
      }),
    ],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AboutPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AboutPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGetAbout.mockResolvedValue(about());
    mockedGetNotice.mockResolvedValue(APACHE_TEXT);
  });

  it("renders the deployment's identity", async () => {
    renderPage();

    expect(await screen.findByTestId("about-product")).toHaveTextContent("TRUSCA");
    expect(screen.getByTestId("about-version")).toHaveTextContent("2.3.0-dev");
    expect(screen.getByTestId("about-license")).toHaveTextContent("Apache-2.0");
    expect(screen.getByTestId("about-copyright")).toHaveTextContent(
      "Copyright 2026 TRUSCA contributors",
    );
    expect(screen.getByTestId("about-source-link")).toHaveAttribute(
      "href",
      "https://github.com/trustedoss/trusca",
    );
  });

  it("renders the first notice document verbatim", async () => {
    renderPage();

    const body = await screen.findByTestId("about-notice-body");
    // Byte-for-byte: a reflowed license text is not the license.
    expect(body).toHaveTextContent("Apache License", { normalizeWhitespace: true });
    expect(body.textContent).toBe(APACHE_TEXT);
    expect(mockedGetNotice).toHaveBeenCalledWith("license");
  });

  it("fetches only the selected document, not all three", async () => {
    renderPage();
    await screen.findByTestId("about-notice-body");

    expect(mockedGetNotice).toHaveBeenCalledTimes(1);
  });

  it("loads the newly selected document when a tab is clicked", async () => {
    mockedGetNotice.mockImplementation(async (id: string) =>
      id === "third-party-notices" ? "Copyright 2026 SK Telecom Co., Ltd." : APACHE_TEXT,
    );
    renderPage();
    await screen.findByTestId("about-notice-body");

    await userEvent.click(await screen.findByTestId("about-tab-third-party-notices"));

    await waitFor(() =>
      expect(mockedGetNotice).toHaveBeenCalledWith("third-party-notices"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("about-notice-body")).toHaveTextContent(
        "SK Telecom Co., Ltd.",
      ),
    );
  });

  it("shows a packaging fault for a document missing from the deployment", async () => {
    mockedGetAbout.mockResolvedValue(
      about({
        documents: [
          doc({ id: "license", size_bytes: 11291 }),
          doc({
            id: "third-party-notices",
            title: "Third-party notices",
            filename: "THIRD_PARTY_NOTICES.md",
            size_bytes: null,
          }),
        ],
      }),
    );
    renderPage();
    await screen.findByTestId("about-notice-body");

    await userEvent.click(await screen.findByTestId("about-tab-third-party-notices"));

    expect(await screen.findByTestId("about-notice-missing")).toBeInTheDocument();
    // The filename is named so an operator knows what to go look for.
    expect(screen.getByTestId("about-notice-missing")).toHaveTextContent(
      "THIRD_PARTY_NOTICES.md",
    );
    // No point requesting a body the API already said is absent.
    expect(mockedGetNotice).not.toHaveBeenCalledWith("third-party-notices");
  });

  it("surfaces an identity load failure instead of a blank page", async () => {
    mockedGetAbout.mockRejectedValue(new Error("boom"));
    renderPage();

    expect(await screen.findByTestId("about-error")).toBeInTheDocument();
  });

  it("surfaces a document load failure without losing the tabs", async () => {
    mockedGetNotice.mockRejectedValue(new Error("nope"));
    renderPage();

    expect(await screen.findByTestId("about-notice-error")).toBeInTheDocument();
    expect(screen.getByTestId("about-notice-tabs")).toBeInTheDocument();
  });

  it("labels tabs from translations rather than the API's English strings", async () => {
    mockedGetAbout.mockResolvedValue(
      about({
        documents: [
          doc({ id: "license", title: "SHOULD-NOT-RENDER" }),
        ],
      }),
    );
    renderPage();

    const tab = await screen.findByTestId("about-tab-license");
    expect(tab).not.toHaveTextContent("SHOULD-NOT-RENDER");
    expect(tab).toHaveTextContent("License");
  });

  it("falls back to the API title for a document the UI does not know", async () => {
    mockedGetAbout.mockResolvedValue(
      about({
        documents: [doc({ id: "future-doc", title: "Some New Notice" })],
      }),
    );
    renderPage();

    expect(await screen.findByTestId("about-tab-future-doc")).toHaveTextContent(
      "Some New Notice",
    );
  });
});
