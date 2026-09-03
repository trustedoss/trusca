// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { lookupExternalAdvisory } from "@/features/search/api/externalAdvisoryApi";
import { useDeploymentFeatures } from "@/features/about/api/useDeploymentFeatures";

// Not a security boundary -- just the trigger for an extra external call, so
// a false positive/negative here only costs (or skips) one round trip.
const CVE_SHAPE = /^CVE-\d{4}-\d{4,}$/i;
const GHSA_SHAPE = /^GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$/i;

export function isCveOrGhsaShaped(term: string): boolean {
  const trimmed = term.trim();
  return CVE_SHAPE.test(trimmed) || GHSA_SHAPE.test(trimmed);
}

/**
 * deps.dev's advisories endpoint is case-sensitive (verified live): a CVE
 * id must be all-uppercase, and a GHSA id must keep its four-char groups
 * lowercase -- either one typed in the "wrong" case 404s even though
 * `isCveOrGhsaShaped` (case-insensitive on purpose, see above) still
 * matches it. Without this, a search for "cve-2021-23337" would render no
 * card at all rather than the one it should.
 */
const GHSA_GROUPS = /^GHSA-([a-z0-9]{4})-([a-z0-9]{4})-([a-z0-9]{4})$/i;

function toCanonicalAdvisoryId(term: string): string {
  const trimmed = term.trim();
  if (CVE_SHAPE.test(trimmed)) return trimmed.toUpperCase();
  const ghsaMatch = GHSA_GROUPS.exec(trimmed);
  if (ghsaMatch) {
    const [, a, b, c] = ghsaMatch;
    return `GHSA-${a.toLowerCase()}-${b.toLowerCase()}-${c.toLowerCase()}`;
  }
  return trimmed;
}

export function useExternalAdvisory(kind: string, debouncedTerm: string) {
  const features = useDeploymentFeatures();
  const shaped = isCveOrGhsaShaped(debouncedTerm);
  const advisoryId = toCanonicalAdvisoryId(debouncedTerm);

  return useQuery({
    queryKey: ["external-advisory", advisoryId],
    queryFn: () => lookupExternalAdvisory(advisoryId),
    enabled: kind === "vulnerabilities" && shaped && features.external_package_lookup === true,
    meta: { errorToast: false },
  });
}
