/**
 * Dev-only toggle chip for the AIS theme prototype.
 *
 * Mounted in the AppShell header and at the top of /dev/design-preview,
 * always behind `import.meta.env.DEV` so production builds drop it.
 * Copy is intentionally static English (designer-facing artifact, same
 * convention as DesignSystemPreview — no t() keys).
 */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { isAisThemeEnabled, setAisTheme } from "@/lib/devTheme";

export function AisThemeToggle() {
  const [enabled, setEnabled] = useState(isAisThemeEnabled);

  return (
    <Button
      variant={enabled ? "secondary" : "outline"}
      size="sm"
      aria-pressed={enabled}
      data-testid="ais-theme-toggle"
      title="Prototype: Google AI Studio light theme (dev only)"
      onClick={() => {
        const next = !enabled;
        setAisTheme(next);
        setEnabled(next);
      }}
    >
      AIS {enabled ? "on" : "off"}
    </Button>
  );
}
