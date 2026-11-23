// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * AdminAuditDrawer — right-slide Sheet detail view for an audit log row.
 *
 * Shows the meta header (actor / table / action / target_id / request_id)
 * and the full diff JSON. PII columns are stored as sha256 dicts (chore
 * PR #8 F4) — the display path detects `{ "sha256": "<hex>" }` and
 * renders a truncated `sha256:abcd1234…` pill so the operator sees the
 * provenance without staring at a 64-char string.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import RelativeTime from "@/components/RelativeTime";
import { Badge } from "@/components/primer/badge";
import { PrimerDialog } from "@/components/primer/PrimerDialog";
import type { AuditLogItem } from "@/features/admin/audit/api/adminAuditApi";

interface AdminAuditDrawerProps {
  open: boolean;
  entry: AuditLogItem | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Best-effort detector for sha256-fingerprinted PII payloads. The backend
 * stores them as ``{"sha256": "<64-char-hex>"}`` so the audit reader can
 * tell "this row had an email column once" without leaking the address.
 */
function isSha256Dict(value: unknown): value is { sha256: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    "sha256" in value &&
    typeof (value as { sha256: unknown }).sha256 === "string" &&
    /^[a-f0-9]{32,}$/i.test((value as { sha256: string }).sha256)
  );
}

function Sha256Pill({ hex }: { hex: string }) {
  const { t } = useTranslation("admin");
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md border border-status-warning-border bg-status-warning-subtle px-2 py-0.5 font-mono text-xs text-status-warning-foreground"
      data-testid="admin-audit-sha256-pill"
      data-prefix={hex.slice(0, 8)}
      title={hex}
    >
      {t("admin.audit.drawer.sha256_pill", { prefix: hex.slice(0, 16) })}
    </span>
  );
}

function renderDiffValue(value: unknown): ReactNode {
  if (isSha256Dict(value)) {
    return <Sha256Pill hex={value.sha256} />;
  }
  if (value === null)
    return <span className="text-muted-foreground">null</span>;
  if (typeof value === "string") return <span>{value}</span>;
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="font-mono text-xs">{String(value)}</span>;
  }
  return (
    <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 font-mono text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function AdminAuditDrawer({
  open,
  entry,
  onOpenChange,
}: AdminAuditDrawerProps) {
  const { t } = useTranslation("admin");

  return (
    <PrimerDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("admin.audit.drawer.title")}
      description={<span className="font-mono text-xs">{entry?.id ?? ""}</span>}
      position="right"
      width="large"
      testId="admin-audit-drawer"
    >
      <div className="flex flex-col gap-4">
        {entry ? (
          <>
            <section className="grid grid-cols-2 gap-3 text-xs">
              <Meta
                label={t("admin.audit.column.actor")}
                value={entry.actor_email ?? entry.actor_user_id ?? "—"}
                testId="admin-audit-drawer-actor"
              />
              <Meta
                label={t("admin.audit.column.target_table")}
                value={entry.target_table}
                testId="admin-audit-drawer-target-table"
              />
              <Meta
                label={t("admin.audit.column.action")}
                value={entry.action}
                testId="admin-audit-drawer-action"
              />
              <Meta
                label={t("admin.audit.column.target_id")}
                value={entry.target_id ?? "—"}
                testId="admin-audit-drawer-target-id"
              />
              <Meta
                label={t("admin.audit.drawer.request_label")}
                value={entry.request_id ?? "—"}
                testId="admin-audit-drawer-request-id"
              />
              {/*
                B3: the same absolute form the table now uses, so a row and
                the drawer it opens do not disagree about what time it is.
              */}
              <div>
                <div className="text-xs font-semibold text-muted-foreground">
                  {t("admin.audit.column.created_at")}
                </div>
                <RelativeTime
                  value={entry.created_at}
                  display="absolute"
                  className="block break-all font-mono text-xs"
                  data-testid="admin-audit-drawer-created-at"
                />
              </div>
            </section>

            <section data-testid="admin-audit-drawer-diff">
              <h3 className="mb-2 text-sm font-semibold">
                {t("admin.audit.drawer.diff_label")}
              </h3>
              {entry.diff && Object.keys(entry.diff).length > 0 ? (
                <ul className="space-y-2 rounded-md border bg-muted/20 p-3">
                  {Object.entries(entry.diff).map(([key, value]) => (
                    <li
                      key={key}
                      className="flex flex-col gap-1"
                      data-testid="admin-audit-drawer-diff-row"
                      data-key={key}
                    >
                      <span className="font-mono text-xs text-muted-foreground">
                        {key}
                      </span>
                      <div>{renderDiffValue(value)}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("admin.audit.drawer.no_diff")}
                </p>
              )}
            </section>

            <Badge
              variant="outline"
              className="self-start bg-muted text-xs text-muted-foreground"
            >
              {entry.team_id ?? "—"}
            </Badge>
          </>
        ) : null}
      </div>
    </PrimerDialog>
  );
}

function Meta({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div>
      <div className="text-xs font-semibold text-muted-foreground">
        {label}
      </div>
      <div className="break-all font-mono text-xs" data-testid={testId}>
        {value}
      </div>
    </div>
  );
}
