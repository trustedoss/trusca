// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type {
  SearchFacetBucket,
  SearchKind,
} from "@/features/search/api/searchResultsApi";
import { cn } from "@/lib/utils";

/**
 * Facet chips for the active tab.
 *
 * Counts come from the server over the WHOLE match, not the visible page, so a
 * chip reading "high 42" promises 42 results rather than 42-of-what-you-can-see.
 *
 * Which facets exist is a property of the kind: severity and status only make
 * sense for vulnerabilities, package type for components, category for
 * licences, and projects have none. Rather than hard-code that mapping twice,
 * the bar renders whatever facets the response carried — the server already
 * decided, and a new facet appears here without a frontend change.
 */

const FACET_PARAM: Record<string, string> = {
  severity: "severity",
  status: "status",
  package_type: "package_type",
  license_category: "license_category",
};

export interface SearchFacetBarProps {
  kind: SearchKind;
  facets: Record<string, SearchFacetBucket[]>;
  severity: string[];
  status: string[];
  packageType: string[];
  licenseCategory: string[];
  onChange: (param: string, values: string[]) => void;
}

export function SearchFacetBar({
  kind,
  facets,
  severity,
  status,
  packageType,
  licenseCategory,
  onChange,
}: SearchFacetBarProps) {
  const { t } = useTranslation("search");

  const selectedFor: Record<string, string[]> = {
    severity,
    status,
    package_type: packageType,
    license_category: licenseCategory,
  };

  const groups = Object.entries(facets).filter(
    ([name, buckets]) => FACET_PARAM[name] && buckets.length > 0,
  );
  if (groups.length === 0) return null;

  return (
    <div
      className="flex flex-wrap items-start gap-x-6 gap-y-2 border-b px-6 py-3"
      data-testid="search-facets"
      data-kind={kind}
    >
      {groups.map(([name, buckets]) => {
        const selected = selectedFor[name] ?? [];
        return (
          <div key={name} className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">
              {t(`facet.${name}`)}
            </span>
            <div className="flex flex-wrap items-center gap-1.5">
              {buckets.map((bucket) => {
                const active = selected.includes(bucket.value);
                return (
                  <button
                    key={bucket.value}
                    type="button"
                    data-testid={`search-facet-${name}-${bucket.value}`}
                    data-active={active}
                    aria-pressed={active}
                    onClick={() =>
                      onChange(
                        FACET_PARAM[name],
                        active
                          ? selected.filter((value) => value !== bucket.value)
                          : [...selected, bucket.value],
                      )
                    }
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs",
                      "transition-colors duration-fast ease-out-soft",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                      active
                        ? "border-transparent bg-primary text-primary-foreground"
                        : "hover:bg-accent",
                    )}
                  >
                    <span>{bucket.value}</span>
                    <Badge className="px-1 py-0 text-[10px]">
                      {bucket.count}
                    </Badge>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
