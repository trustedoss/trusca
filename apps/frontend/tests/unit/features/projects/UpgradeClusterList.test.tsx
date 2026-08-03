/**
 * UpgradeClusterList — unit tests (W9-#53 "Group by upgrade").
 *
 * Validates the grouped-view rendering the flat list swaps to in "By upgrade"
 * mode: the two cluster header shapes (`ok` → concrete upgrade vs
 * `no_fix_version` → "No upgrade available"), the fixes-count, the direct /
 * KEV / severity signals, and that expanding a cluster and clicking a finding
 * invokes the shared drawer-open handler keyed by `finding_id`.
 *
 * The real i18n instance is loaded by `tests/setup.ts`, so assertions read the
 * rendered English copy (locale-agnostic anchors via `data-testid` where the
 * text would be brittle).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  UpgradeCluster,
  UpgradeClusterFinding,
} from "@/features/projects/api/vulnerabilitiesApi";
import { UpgradeClusterList } from "@/features/projects/components/UpgradeClusterList";

function finding(
  overrides: Partial<UpgradeClusterFinding> = {},
): UpgradeClusterFinding {
  return {
    finding_id: overrides.finding_id ?? "f-1",
    cve_id: overrides.cve_id ?? "CVE-2024-0001",
    severity: overrides.severity ?? "high",
    status: overrides.status ?? "new",
    epss_score: overrides.epss_score ?? 0.42,
    kev: overrides.kev ?? false,
    fixed_version: overrides.fixed_version ?? "4.17.21",
    ...overrides,
  };
}

function cluster(overrides: Partial<UpgradeCluster> = {}): UpgradeCluster {
  return {
    component_version_id: overrides.component_version_id ?? "cv-1",
    component_name: overrides.component_name ?? "lodash",
    component_purl: overrides.component_purl ?? "pkg:npm/lodash",
    current_version: overrides.current_version ?? "4.17.20",
    recommended_version: overrides.recommended_version ?? "4.17.21",
    reason: overrides.reason ?? "ok",
    direct: overrides.direct ?? true,
    max_severity: overrides.max_severity ?? "high",
    max_epss: overrides.max_epss ?? 0.42,
    finding_count: overrides.finding_count ?? 2,
    findings: overrides.findings ?? [
      finding({ finding_id: "f-1", cve_id: "CVE-2024-0001" }),
      finding({ finding_id: "f-2", cve_id: "CVE-2024-0002", severity: "medium" }),
    ],
  };
}

describe("UpgradeClusterList", () => {
  it("exposes the cluster count on the container", () => {
    render(
      <UpgradeClusterList
        clusters={[cluster(), cluster({ component_version_id: "cv-2" })]}
        onOpenFinding={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("vulnerabilities-upgrade-list"),
    ).toHaveAttribute("data-cluster-count", "2");
    expect(screen.getAllByTestId("vulnerability-upgrade-cluster")).toHaveLength(
      2,
    );
  });

  it("renders an 'ok' cluster header with the recommended version and fixes count", () => {
    render(
      <UpgradeClusterList
        clusters={[
          cluster({ recommended_version: "4.17.21", finding_count: 3 }),
        ]}
        onOpenFinding={vi.fn()}
      />,
    );
    const card = screen.getByTestId("vulnerability-upgrade-cluster");
    expect(card).toHaveAttribute("data-reason", "ok");
    // Header names the target version (color is not the only signal).
    const recommended = within(card).getByTestId(
      "vulnerability-upgrade-cluster-recommended",
    );
    expect(recommended).toHaveTextContent("lodash");
    expect(recommended).toHaveTextContent("4.17.20");
    expect(recommended).toHaveTextContent("4.17.21");
    // Fixes count reflects finding_count.
    expect(
      within(card).getByTestId("vulnerability-upgrade-cluster-fixes"),
    ).toHaveTextContent("3");
    // A direct dependency carries the Direct signal chip.
    expect(
      within(card).getByTestId("vulnerability-upgrade-cluster-direct"),
    ).toBeInTheDocument();
  });

  it("renders a 'no_fix_version' cluster as 'No upgrade available' with a reason hint", () => {
    render(
      <UpgradeClusterList
        clusters={[
          cluster({
            reason: "no_fix_version",
            recommended_version: null,
            finding_count: 1,
          }),
        ]}
        onOpenFinding={vi.fn()}
      />,
    );
    const card = screen.getByTestId("vulnerability-upgrade-cluster");
    expect(card).toHaveAttribute("data-reason", "no_fix_version");
    // No concrete-upgrade header; instead the "no upgrade" label + reason hint.
    expect(
      within(card).queryByTestId("vulnerability-upgrade-cluster-recommended"),
    ).not.toBeInTheDocument();
    expect(card).toHaveTextContent("No upgrade available");
    expect(card).toHaveTextContent("Some CVEs have no fix version yet");
  });

  it("distinguishes the 'unparseable_version' reason hint", () => {
    render(
      <UpgradeClusterList
        clusters={[
          cluster({ reason: "unparseable_version", recommended_version: null }),
        ]}
        onOpenFinding={vi.fn()}
      />,
    );
    const card = screen.getByTestId("vulnerability-upgrade-cluster");
    expect(card).toHaveTextContent("Fix version could not be parsed");
  });

  it("collapses findings by default and reveals them on expand", async () => {
    const user = userEvent.setup();
    render(
      <UpgradeClusterList clusters={[cluster()]} onOpenFinding={vi.fn()} />,
    );
    // Collapsed: no finding rows mounted.
    expect(
      screen.queryByTestId("vulnerability-upgrade-finding"),
    ).not.toBeInTheDocument();
    const header = screen.getByTestId("vulnerability-upgrade-cluster-header");
    expect(header).toHaveAttribute("aria-expanded", "false");

    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("vulnerability-upgrade-finding")).toHaveLength(
      2,
    );
  });

  it("invokes onOpenFinding with the finding id when a finding row is clicked", async () => {
    const user = userEvent.setup();
    const onOpenFinding = vi.fn();
    render(
      <UpgradeClusterList
        clusters={[
          cluster({
            findings: [
              finding({ finding_id: "finding-abc", cve_id: "CVE-2024-9999" }),
            ],
            finding_count: 1,
          }),
        ]}
        onOpenFinding={onOpenFinding}
      />,
    );
    await user.click(
      screen.getByTestId("vulnerability-upgrade-cluster-header"),
    );
    await user.click(screen.getByTestId("vulnerability-upgrade-finding"));
    expect(onOpenFinding).toHaveBeenCalledExactlyOnceWith("finding-abc");
  });

  it("surfaces the KEV badge on the header when any finding is KEV-listed", async () => {
    render(
      <UpgradeClusterList
        clusters={[
          cluster({
            findings: [
              finding({ finding_id: "f-kev", kev: true }),
              finding({ finding_id: "f-plain", kev: false }),
            ],
          }),
        ]}
        onOpenFinding={vi.fn()}
      />,
    );
    const header = screen.getByTestId("vulnerability-upgrade-cluster-header");
    expect(within(header).getByTestId("kev-badge")).toBeInTheDocument();
  });
});
