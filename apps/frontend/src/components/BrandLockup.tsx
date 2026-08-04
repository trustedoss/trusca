// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * BrandLockup — the full TRUSCA logo: mark + "TRUSCA" wordmark + the
 * "Trusted SCA" tagline, which is what the name expands to.
 *
 * Used where there is vertical room (the auth gateway, brand showcase).
 * Tight surfaces — the 48 px sidebar / header — use the reduced lockup
 * (BrandMark + BrandWordmark, no tagline).
 *
 * The tagline is a brand string (not translated) and is NOT uppercased. Its
 * colour uses the theme's muted-foreground token (passes WCAG AA), while the
 * mark gradient and the teal wordmark are fixed brand colours.
 */
import { BrandMark } from "@/components/BrandMark";
import { BrandWordmark } from "@/components/BrandWordmark";
import { cn } from "@/lib/utils";

export function BrandLockup({
  onInk = false,
}: {
  /**
   * Render for the ink surface: drop the mark's dark tile and take the
   * bar's own foreground scale. The page's `--foreground` is near-black and
   * would be invisible here, which is the same reason the global bar carries
   * its own `--topbar-*` family.
   */
  onInk?: boolean;
} = {}) {
  return (
    <div className="flex items-center gap-3.5">
      <BrandMark size={48} onInk={onInk} />
      <div className="flex flex-col justify-center">
        <span
          className={cn(
            "text-3xl font-extrabold leading-none tracking-tight",
            onInk && "text-topbar-foreground",
          )}
        >
          <BrandWordmark />
        </span>
        <span
          className={cn(
            "mt-1.5 text-xs font-semibold leading-none tracking-wide",
            onInk ? "text-topbar-muted-foreground" : "text-muted-foreground",
          )}
        >
          Trusted SCA
        </span>
      </div>
    </div>
  );
}
