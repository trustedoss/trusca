/**
 * VulnerabilitiesRemediationPanel — W4-C #22.
 *
 * Collapsible slot inside `VulnerabilitiesTab` that exposes the npm remediation
 * dry-run preview + automated PR creation that used to live on its own top-level
 * "Remediation" tab. The IA overhaul (W4-C #22) absorbs Remediation into
 * Vulnerabilities so a triager doesn't have to bounce between tabs to take a
 * remediation action.
 *
 * Separation rationale: kept in a standalone file rather than inlined into the
 * already-855-line `VulnerabilitiesTab.tsx` so the parent's diff stays small and
 * the surface remains easy to test in isolation.
 *
 * Visibility:
 *   - The button-row affordance ("Open remediation PR" / "Dry-run preview") is
 *     visible to everyone (developers can preview, team admins can submit).
 *   - The detailed preview + PR list expand-collapses behind a button so the
 *     vulnerabilities table above stays the primary surface.
 *
 * URL state: ``?vuln_section=remediation`` opens the panel on initial render so
 * the redirect from the old ``?tab=remediation`` deeplink lands the user on the
 * expanded surface. Closing the panel clears the param.
 *
 * Role gate: the same project-team-scoped role check as ``RemediationTab`` —
 * the actual `RemediationTab` component is re-used inside the expanded body so
 * we never duplicate the gate logic.
 */
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { RemediationTab } from "@/features/projects/components/RemediationTab";

export interface VulnerabilitiesRemediationPanelProps {
  projectId: string;
}

export function VulnerabilitiesRemediationPanel({
  projectId,
}: VulnerabilitiesRemediationPanelProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();
  const sectionRef = useRef<HTMLDivElement>(null);

  const expanded = searchParams.get("vuln_section") === "remediation";

  function toggleExpanded() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (expanded) {
          next.delete("vuln_section");
        } else {
          next.set("vuln_section", "remediation");
        }
        return next;
      },
      { replace: true },
    );
  }

  // On initial mount with `?vuln_section=remediation` (the redirect path from
  // the old `?tab=remediation` URL), scroll the section into view so the user
  // lands on the expanded panel rather than at the top of the table.
  useEffect(() => {
    if (expanded) {
      requestAnimationFrame(() => {
        sectionRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      });
    }
    // We deliberately only react to the *initial* state — every subsequent
    // toggle is driven by user click which already keeps the section in view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      ref={sectionRef}
      data-testid="vulnerabilities-remediation-panel"
      data-expanded={expanded ? "true" : "false"}
      className="border-t bg-muted/20 scroll-mt-16"
    >
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <Wrench
            className="h-4 w-4 text-muted-foreground"
            aria-hidden="true"
          />
          <div>
            <h3 className="text-sm font-semibold">
              {t("vulnerabilities.remediation.heading")}
            </h3>
            <p className="text-xs text-muted-foreground">
              {t("vulnerabilities.remediation.subheading")}
            </p>
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={toggleExpanded}
          data-testid="vulnerabilities-remediation-toggle"
          aria-expanded={expanded}
          aria-controls="vulnerabilities-remediation-body"
        >
          {expanded ? (
            <ChevronDown
              className="mr-1.5 h-3.5 w-3.5"
              aria-hidden="true"
            />
          ) : (
            <ChevronRight
              className="mr-1.5 h-3.5 w-3.5"
              aria-hidden="true"
            />
          )}
          {expanded
            ? t("vulnerabilities.remediation.collapse")
            : t("vulnerabilities.remediation.expand")}
        </Button>
      </div>

      {expanded ? (
        <div
          id="vulnerabilities-remediation-body"
          data-testid="vulnerabilities-remediation-body"
        >
          <RemediationTab projectId={projectId} />
        </div>
      ) : null}
    </div>
  );
}
