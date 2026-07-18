import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";

/**
 * OsEolPanel — image base-OS end-of-service-life signal (K-f1).
 *
 * A container scan's Trivy report carries the base image's OS release and an
 * end-of-service-life (EOSL) flag from Trivy's bundled vulnerability DB. When
 * that release is past EOL it no longer gets upstream security fixes, so newly
 * disclosed CVEs never reach it — a scan-level risk distinct from the
 * per-component EOL that {@link EolBadge} shows on source scans.
 *
 * Renders ONLY when `eosl` is true (a supported release reads as no signal).
 * The badge mirrors {@link EolBadge}: a colored dot paired with a literal
 * label so color is never the only signal, on the High hue family (a
 * maintenance-risk signal, one notch below confirmed exploitation). The panel
 * spells out the consequence and notes the Trivy-DB-freshness caveat.
 */

interface OsBlock {
  family: string;
  name?: string;
  eosl: boolean;
}

/** Narrow the untyped `scan.metadata.os` JSONB into a typed OS block. */
export function readOsBlock(metadata: Record<string, unknown>): OsBlock | null {
  const os = metadata?.os;
  if (typeof os !== "object" || os === null) return null;
  const rec = os as Record<string, unknown>;
  if (typeof rec.family !== "string" || !rec.family) return null;
  return {
    family: rec.family,
    name: typeof rec.name === "string" ? rec.name : undefined,
    eosl: rec.eosl === true,
  };
}

export interface OsEolPanelProps {
  metadata: Record<string, unknown>;
}

export function OsEolPanel({ metadata }: OsEolPanelProps) {
  const { t } = useTranslation("scans");
  const os = readOsBlock(metadata);
  if (!os || !os.eosl) return null;

  const osLabel = t("container.os_eol.os", {
    family: os.family,
    name: os.name ?? "",
  }).trim();

  return (
    <section
      className="rounded-md border bg-card p-4 shadow-sm"
      data-testid="scan-detail-os-eol"
      data-os-eosl="true"
      data-os-family={os.family}
      data-os-name={os.name}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold tracking-tight">
          {t("container.os_eol.heading")}
        </h2>
        <Badge
          tone="high"
          title={osLabel}
          className="gap-1.5 font-semibold"
        >
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full bg-risk-high"
          />
          <span>{t("container.os_eol.label")}</span>
        </Badge>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
          {osLabel}
        </span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">
        {t("container.os_eol.detail", { os: osLabel })}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {t("container.os_eol.note")}
      </p>
    </section>
  );
}
