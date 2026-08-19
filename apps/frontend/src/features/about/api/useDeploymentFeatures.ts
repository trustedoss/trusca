// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Which optional surfaces this deployment turned on.
 *
 * Rides on the `about` query the shell already makes, so asking costs nothing
 * extra and the answer is cached with everything else on that response.
 *
 * Everything is off until the answer arrives. A menu that drew an optional row
 * and then removed it would flicker, and the flicker would be worst on a slow
 * connection, where somebody is most likely to click the thing that vanishes.
 */
import { useAbout } from "@/features/about/api/useAbout";

export function useDeploymentFeatures(): Record<string, boolean> {
  const { data } = useAbout();
  return data?.features ?? {};
}
