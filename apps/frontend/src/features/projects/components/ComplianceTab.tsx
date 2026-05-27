/**
 * ComplianceTab — W4-C #20.
 *
 * Unified container for the previously-split "Licenses" and "Obligations"
 * surfaces. The Information Architecture overhaul collapses both into a
 * single top-level "Compliance" tab so the user has one place to answer the
 * BD-flavoured question "what licenses am I shipping and what do they
 * require?".
 *
 * Implementation choice: rather than rewrite the two virtualized tables into
 * one merged grid (which would require a BE-side join endpoint that doesn't
 * exist yet), this tab presents the two existing surfaces as sub-views,
 * URL-encoded under ``?cview=licenses|obligations``. The default sub-view is
 * ``licenses`` because the license inventory is the upstream of the
 * obligations surface — you can't have an obligation without the license
 * that produced it.
 *
 * URL state:
 *   - ``?cview=`` selects the sub-view (default = ``licenses``).
 *   - All existing per-tab filter params (``license_category``, ``kind``,
 *     ``search``, ``sort``, ``order``, ``page``) flow through to the active
 *     sub-view unchanged so a deep-link from W4-B still lands correctly.
 *   - Drawer keys (``?license=``, ``?obligation=``) stay distinct so they
 *     do not collide.
 *
 * Backwards-compatibility (handled in ProjectDetailPage::setTab):
 *   - ``?tab=licenses`` → ``?tab=compliance&cview=licenses``
 *   - ``?tab=obligations`` → ``?tab=compliance&cview=obligations``
 *
 * BE comment: this composition fetches both list endpoints when the user
 * toggles between sub-views. A future PR could collapse them into a single
 * BE endpoint that joins license_findings with their obligations, but that
 * is out of scope here.
 */
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LicensesTab } from "@/features/projects/components/LicensesTab";
import { ObligationsTab } from "@/features/projects/components/ObligationsTab";

export type ComplianceSubview = "licenses" | "obligations";

const VALID_SUBVIEW = new Set<ComplianceSubview>(["licenses", "obligations"]);

function parseSubview(raw: string | null): ComplianceSubview {
  if (raw && VALID_SUBVIEW.has(raw as ComplianceSubview)) {
    return raw as ComplianceSubview;
  }
  return "licenses";
}

export interface ComplianceTabProps {
  projectId: string;
  /**
   * Project name — threaded into the obligations sub-view so the NOTICE file
   * download names the artefact after the project.
   */
  projectName?: string | null;
  /**
   * Pinned snapshot scan id (feature #28). When set, both sub-views reflect
   * that historical scan instead of the latest succeeded one.
   */
  scanId?: string;
}

export function ComplianceTab({
  projectId,
  projectName,
  scanId,
}: ComplianceTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  const subview = parseSubview(searchParams.get("cview"));

  function setSubview(next: ComplianceSubview) {
    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        if (next === "licenses") {
          // Default sub-view → drop the param so the canonical URL stays
          // short (mirrors how `?tab=overview` is omitted by ProjectDetail).
          merged.delete("cview");
        } else {
          merged.set("cview", next);
        }
        // Drop drawer state from the other sub-view so a switch does not
        // leave a "ghost" drawer flagged in the URL.
        if (next === "licenses") {
          merged.delete("obligation");
        } else {
          merged.delete("license");
        }
        return merged;
      },
      { replace: true },
    );
  }

  return (
    <div data-testid="compliance-tab" className="flex flex-1 flex-col">
      <Tabs
        value={subview}
        onValueChange={(next) => setSubview(next as ComplianceSubview)}
      >
        <TabsList
          data-testid="compliance-subtabs"
          className="border-b bg-background"
        >
          <TabsTrigger
            value="licenses"
            data-testid="compliance-subtab-licenses"
          >
            {t("compliance.subtab.licenses")}
          </TabsTrigger>
          <TabsTrigger
            value="obligations"
            data-testid="compliance-subtab-obligations"
          >
            {t("compliance.subtab.obligations")}
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {subview === "licenses" ? (
        <LicensesTab projectId={projectId} scanId={scanId} />
      ) : (
        <ObligationsTab
          projectId={projectId}
          projectName={projectName}
          scanId={scanId}
        />
      )}
    </div>
  );
}
