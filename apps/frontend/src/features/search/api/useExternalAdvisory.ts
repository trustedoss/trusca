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

export function useExternalAdvisory(kind: string, debouncedTerm: string) {
  const features = useDeploymentFeatures();
  const shaped = isCveOrGhsaShaped(debouncedTerm);

  return useQuery({
    queryKey: ["external-advisory", debouncedTerm.trim()],
    queryFn: () => lookupExternalAdvisory(debouncedTerm.trim()),
    enabled: kind === "vulnerabilities" && shaped && features.external_package_lookup === true,
    meta: { errorToast: false },
  });
}
