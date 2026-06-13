/**
 * BrandLockup — the full TRUSCA logo: mark + "TRUSCA" wordmark + the
 * "TrustedOSS SCA" tagline (the SCA tool of the TrustedOSS initiative).
 *
 * Used where there is vertical room (the auth gateway, brand showcase).
 * Tight surfaces — the 48 px sidebar / header — use the reduced lockup
 * (BrandMark + BrandWordmark, no tagline). See docs/brand-trusca.md.
 *
 * The tagline is a brand string (not translated) and is NOT uppercased —
 * the umbrella name "TrustedOSS" keeps its camel casing. Its colour uses the
 * theme's muted-foreground token (passes WCAG AA), while the mark gradient
 * and the teal wordmark are fixed brand colours.
 */
import { BrandMark } from "@/components/BrandMark";
import { BrandWordmark } from "@/components/BrandWordmark";

export function BrandLockup() {
  return (
    <div className="flex items-center gap-3.5">
      <BrandMark size={48} />
      <div className="flex flex-col justify-center">
        <span className="text-3xl font-extrabold leading-none tracking-tight">
          <BrandWordmark />
        </span>
        <span className="mt-1.5 text-xs font-semibold leading-none tracking-wide text-muted-foreground">
          TrustedOSS SCA
        </span>
      </div>
    </div>
  );
}
