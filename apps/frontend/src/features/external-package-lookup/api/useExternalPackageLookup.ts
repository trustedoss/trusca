// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * A submit-triggered lookup, not a cached query -- one deps.dev round trip
 * per button click, same shape as `IntakeRequestsPage`'s `ask` mutation.
 * The result is not shared across the app, so it is not worth a query key;
 * the page keeps it in local state.
 */
import { useMutation } from "@tanstack/react-query";

import { lookupExternalPackage } from "@/features/external-package-lookup/api/externalPackagesApi";

export function useExternalPackageLookup() {
  return useMutation({
    mutationFn: ({ ecosystem, name }: { ecosystem: string; name: string }) =>
      lookupExternalPackage(ecosystem, name),
    meta: { errorToast: false },
  });
}
