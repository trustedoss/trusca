// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { cn } from "@/lib/utils";
import type { ScanLogMessage } from "@/hooks/useScanWebSocket";

/**
 * ToolLogLine — single row in the unified tool-log stream.
 *
 * Renders one stdout / stderr line from a scan stage (cdxgen, scancode,
 * trivy, …) with:
 *   - HH:MM:SS timestamp in mono.
 *   - Stage badge tinted per {@link STAGE_BADGE_CLASS}.
 *   - `err` badge when the line was emitted on stderr.
 *   - The line text itself, tinted for stderr.
 *
 * Extracted from `ScanProgress.tsx` so the dedicated `/scans/:scanId` log
 * panel can render the same rows without duplicating the styling logic. The
 * two surfaces stay byte-for-byte consistent because they share this file.
 */

// Per-stage color codes for the tool log panel. Aligned with the design
// system risk palette where possible (cdxgen=primary/blue, scancode uses the
// conditional license warm tone for visual distinction, trivy uses the
// risk-high token because it is the vulnerability scanner). Unknown stages
// fall back to the neutral foreground.
export const STAGE_BADGE_CLASS: Record<string, string> = {
  cdxgen: "border-blue-500/30 bg-blue-500/10 text-blue-700",
  scancode: "border-amber-500/30 bg-amber-500/10 text-amber-700",
  // W6-#43f: Trivy is the vulnerability scanner — colour the badge with the
  // risk-high token so it reads as "security tool" in the unified log stream.
  // G0-7: the tint behind this badge paints for the first time, and the
  // severity hex is not readable on it (3.05:1) — hence the foreground token.
  trivy: "border-risk-high/40 bg-risk-high/10 text-risk-high-foreground",
};

export interface ToolLogLineProps {
  msg: ScanLogMessage;
}

export function ToolLogLine({ msg }: ToolLogLineProps) {
  const isErr = msg.stream === "stderr";
  const badge =
    STAGE_BADGE_CLASS[msg.stage] ??
    "border-border bg-muted text-muted-foreground";
  return (
    <li
      className={cn(
        "flex items-baseline gap-2 border-b px-3 py-1 last:border-b-0",
        isErr && "bg-risk-critical/10",
      )}
      data-stage={msg.stage}
      data-stream={msg.stream}
    >
      <span
        // The muted grey clears AA on the panel's own ground with almost no
        // headroom (4.53:1), and an error row now tints that ground — which
        // takes it under. On a row already marked as an error the timestamp
        // does not need to recede, so it takes the full foreground there.
        className={cn(
          "shrink-0",
          isErr ? "text-foreground" : "text-muted-foreground",
        )}
        title={msg.ts}
      >
        {msg.ts.slice(11, 19)}
      </span>
      <span
        className={cn(
          "shrink-0 rounded border px-1 py-0.5 text-[9px] uppercase tracking-wide",
          badge,
        )}
      >
        {msg.stage}
      </span>
      {isErr ? (
        <span
          className="shrink-0 rounded border border-risk-critical/40 bg-risk-critical/10 px-1 py-0.5 text-[9px] uppercase tracking-wide text-risk-critical-foreground"
          aria-label="stderr"
        >
          err
        </span>
      ) : null}
      <span
        className={cn(
          "min-w-0 whitespace-pre-wrap break-words",
          isErr ? "text-risk-critical-foreground" : "text-foreground",
        )}
      >
        {msg.line}
      </span>
    </li>
  );
}
