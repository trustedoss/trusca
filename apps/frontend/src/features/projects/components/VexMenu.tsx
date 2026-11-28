// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { ChevronDownIcon, ShieldCheckIcon } from "@primer/octicons-react";
import { ActionList, ActionMenu } from "@primer/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { IconButton } from "@/components/primer/IconButton";
import { useVexExport } from "@/features/projects/api/useVexExport";
import type { TeamScopedRole } from "@/features/projects/api/projectDetailApi";
import type { VexFormat } from "@/features/projects/api/vexApi";
import { VexImportDialog } from "@/features/projects/components/VexImportDialog";
import { problemMessage } from "@/lib/problemMessage";

/**
 * VexMenu: one "VEX" button that holds both directions, the two export
 * formats and the import dialog. It replaces a pair of labelled controls that
 * took a second toolbar row. Export is a read (any reader); import keeps its
 * role and read-only gates, shown as a disabled item with the reason.
 */

const FORMATS: VexFormat[] = ["openvex", "cyclonedx"];

export interface VexMenuProps {
  projectId: string;
  projectName?: string | null;
  projectRole?: TeamScopedRole;
  readOnly?: boolean;
  /** Icon-only trigger for toolbars short of width; "VEX" stays as the name. */
  compact?: boolean;
}

export function VexMenu({
  projectId,
  projectName,
  projectRole = "developer",
  readOnly = false,
  compact = false,
}: VexMenuProps) {
  const { t } = useTranslation("project_detail");
  const exporter = useVexExport(projectId, projectName);
  const [importOpen, setImportOpen] = useState(false);

  const canImport =
    (projectRole === "group_admin" || projectRole === "super_admin") &&
    !readOnly;
  const importReason = readOnly
    ? t("snapshot.readonly_tooltip")
    : canImport
      ? undefined
      : t("vulnerabilities.vex.import_role_gated");

  return (
    <div className="flex flex-col" data-testid="vex-export">
      <ActionMenu>
        {compact ? (
          <ActionMenu.Anchor>
            <IconButton
              icon={ShieldCheckIcon}
              aria-label={t("vulnerabilities.vex.filter_label")}
              data-testid="vex-menu-trigger"
            />
          </ActionMenu.Anchor>
        ) : (
          <ActionMenu.Button
            trailingAction={ChevronDownIcon}
            data-testid="vex-menu-trigger"
          >
            {t("vulnerabilities.vex.filter_label")}
          </ActionMenu.Button>
        )}
        <ActionMenu.Overlay width="small">
          <ActionList>
            <ActionList.Group>
              <ActionList.GroupHeading>
                {t("vulnerabilities.vex.export_label")}
              </ActionList.GroupHeading>
              {FORMATS.map((format) => (
                <ActionList.Item
                  key={format}
                  disabled={exporter.busyFormat !== null}
                  onSelect={() => {
                    // Errors surface through exporter.error below; swallow the
                    // rejection so it is not an unhandled promise rejection.
                    exporter.download(format).catch(() => {
                      /* surfaced inline via exporter.error */
                    });
                  }}
                  data-testid={`vex-export-${format}`}
                  data-format={format}
                >
                  {exporter.busyFormat === format
                    ? t("vulnerabilities.vex.export_downloading")
                    : t(`vulnerabilities.vex.export_${format}`)}
                </ActionList.Item>
              ))}
            </ActionList.Group>
            <ActionList.Divider />
            <ActionList.Item
              disabled={!canImport}
              onSelect={() => setImportOpen(true)}
              data-testid="vex-import-open"
              data-role-gated={!canImport ? "true" : undefined}
              data-readonly-gated={readOnly ? "true" : undefined}
              title={importReason}
            >
              {t("vulnerabilities.vex.import_button")}
            </ActionList.Item>
          </ActionList>
        </ActionMenu.Overlay>
      </ActionMenu>
      {exporter.error ? (
        <span
          className="mt-1 text-xs text-destructive"
          data-testid="vex-export-error"
          aria-live="polite"
        >
          {problemMessage(exporter.error, t, {
            action: "vulnerabilities.vex.export_error",
          })}
        </span>
      ) : null}
      <VexImportDialog
        projectId={projectId}
        projectRole={projectRole}
        readOnly={readOnly}
        open={importOpen}
        onOpenChange={setImportOpen}
        hideTrigger
      />
    </div>
  );
}
