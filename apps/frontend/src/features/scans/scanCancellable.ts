// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import type { ScanStatus } from "@/lib/projectsApi";

const CANCELLABLE: ReadonlySet<ScanStatus> = new Set(["queued", "running"]);

/** True when the scan can still be cancelled, so a cancel button has something to show. */
export function isScanCancellable(status: ScanStatus): boolean {
  return CANCELLABLE.has(status);
}
