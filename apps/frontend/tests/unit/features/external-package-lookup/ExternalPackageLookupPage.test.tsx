/**
 * External catalog package lookup.
 *
 * Mirrors IntakeRequestsPage.test.tsx's structure: the important early case
 * is the disabled deployment, told rather than shown an empty result. The
 * rest exercises the submit-triggered lookup and the internal-usage section
 * that answers "have we already got this" before a pre-adoption request is
 * filed.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExternalPackageLookupPage } from "@/features/external-package-lookup/ExternalPackageLookupPage";

const lookupExternalPackage = vi.fn();
const useDeploymentFeatures = vi.fn();

vi.mock("@/features/external-package-lookup/api/externalPackagesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/external-package-lookup/api/externalPackagesApi")
  >("@/features/external-package-lookup/api/externalPackagesApi");
  return {
    ...actual,
    lookupExternalPackage: (...args: unknown[]) => lookupExternalPackage(...args),
  };
});

vi.mock("@/features/about/api/useDeploymentFeatures", () => ({
  useDeploymentFeatures: () => useDeploymentFeatures(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && "count" in opts ? `${key}:${opts.count}` : key,
  }),
}));

function found(overrides: Record<string, unknown> = {}) {
  return {
    ecosystem: "npm",
    name: "lodash",
    found: true,
    version: "4.17.21",
    purl: "pkg:npm/lodash",
    licenses: ["MIT"],
    advisory_count: 0,
    advisory_ids: [],
    homepage_url: null,
    source_repo_url: null,
    internal_projects: [],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ExternalPackageLookupPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ExternalPackageLookupPage", () => {
  beforeEach(() => {
    lookupExternalPackage.mockReset();
    useDeploymentFeatures.mockReset();
  });

  it("says the deployment does not look packages up externally, rather than an empty form", () => {
    useDeploymentFeatures.mockReturnValue({});

    renderPage();

    expect(screen.getByTestId("external-package-lookup-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("external-package-lookup-form")).not.toBeInTheDocument();
  });

  it("looks up what was typed in", async () => {
    useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
    lookupExternalPackage.mockResolvedValue(found());

    renderPage();

    await userEvent.type(screen.getByTestId("external-package-name"), "lodash");
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    await waitFor(() => expect(lookupExternalPackage).toHaveBeenCalledWith("npm", "lodash"));
    expect(await screen.findByTestId("external-package-lookup-result")).toBeInTheDocument();
  });

  it("shows not-found rather than an empty result card", async () => {
    useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
    lookupExternalPackage.mockResolvedValue({
      ecosystem: "npm",
      name: "this-does-not-exist",
      found: false,
      version: null,
      purl: null,
      licenses: [],
      advisory_count: 0,
      advisory_ids: [],
      homepage_url: null,
      source_repo_url: null,
      internal_projects: [],
    });

    renderPage();
    await userEvent.type(
      screen.getByTestId("external-package-name"),
      "this-does-not-exist",
    );
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    expect(await screen.findByTestId("external-package-lookup-not-found")).toBeInTheDocument();
    expect(screen.queryByTestId("external-package-lookup-result")).not.toBeInTheDocument();
  });

  it("says a package is not yet used internally, so a request will not be a duplicate", async () => {
    useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
    lookupExternalPackage.mockResolvedValue(found({ internal_projects: [] }));

    renderPage();
    await userEvent.type(screen.getByTestId("external-package-name"), "lodash");
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    expect(
      await screen.findByTestId("external-package-internal-usage-empty"),
    ).toBeInTheDocument();
  });

  it("links each internal project already using the package", async () => {
    useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
    lookupExternalPackage.mockResolvedValue(
      found({
        internal_projects: [
          {
            project_id: "p-1",
            project_name: "Checkout Service",
            project_slug: "checkout-service",
            version: "4.17.20",
          },
        ],
      }),
    );

    renderPage();
    await userEvent.type(screen.getByTestId("external-package-name"), "lodash");
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    const list = await screen.findByTestId("external-package-internal-usage-list");
    const link = screen.getByRole("link", { name: "Checkout Service" });
    expect(list).toContainElement(link);
    expect(link).toHaveAttribute("href", "/projects/checkout-service");
  });

  it("offers the intake CTA only where the deployment takes pre-adoption requests", async () => {
    useDeploymentFeatures.mockReturnValue({
      external_package_lookup: true,
      intake_requests: true,
    });
    lookupExternalPackage.mockResolvedValue(found());

    renderPage();
    await userEvent.type(screen.getByTestId("external-package-name"), "lodash");
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    const cta = await screen.findByTestId("external-package-lookup-intake-cta");
    expect(cta).toHaveAttribute("href", "/intake?purl=pkg%3Anpm%2Flodash");
  });

  it("hides the intake CTA where the deployment does not take pre-adoption requests", async () => {
    useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
    lookupExternalPackage.mockResolvedValue(found());

    renderPage();
    await userEvent.type(screen.getByTestId("external-package-name"), "lodash");
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    await screen.findByTestId("external-package-lookup-result");
    expect(
      screen.queryByTestId("external-package-lookup-intake-cta"),
    ).not.toBeInTheDocument();
  });

  it("renders a classified error rather than the raw failure", async () => {
    useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
    lookupExternalPackage.mockRejectedValue(new Error("boom"));

    renderPage();
    await userEvent.type(screen.getByTestId("external-package-name"), "lodash");
    await userEvent.click(screen.getByTestId("external-package-lookup-submit"));

    expect(await screen.findByTestId("external-package-lookup-error")).toBeInTheDocument();
  });
});
