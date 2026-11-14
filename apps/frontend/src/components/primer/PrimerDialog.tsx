// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * One modal for the whole app, on Primer's Dialog.
 *
 * Why a wrapper: Primer's own close button carries a hard-coded English label,
 * which a Korean reader would hear as the only English word on the screen. The
 * header is therefore ours (translated close label); everything else is the
 * stock Dialog, including the focus trap, Escape and the backdrop click.
 *
 * Primer's Dialog takes no `data-testid`, so `testId` goes on the body wrapper.
 */
import { XIcon } from "@primer/octicons-react";
import { Dialog, IconButton } from "@primer/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface PrimerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** Read out with the title; shown under it when `showDescription`. */
  description?: string;
  children?: ReactNode;
  width?: "small" | "medium" | "large" | "xlarge";
  position?: "center" | "left" | "right";
  testId?: string;
  /** Buttons for the footer, already rendered. */
  footer?: ReactNode;
}

export function PrimerDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  width = "medium",
  position = "center",
  testId,
  footer,
}: PrimerDialogProps) {
  const { t } = useTranslation("common");
  if (!open) return null;
  return (
    <Dialog
      title={title}
      width={width}
      position={position}
      onClose={() => onOpenChange(false)}
      renderHeader={({ dialogLabelId, dialogDescriptionId }) => (
        <Dialog.Header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id={dialogLabelId} className="text-base font-semibold">
              {title}
            </h2>
            {description ? (
              <p
                id={dialogDescriptionId}
                className="mt-1 text-sm text-muted-foreground"
              >
                {description}
              </p>
            ) : null}
          </div>
          <IconButton
            icon={XIcon}
            variant="invisible"
            aria-label={t("dialog.close")}
            data-testid="dialog-close"
            unsafeDisableTooltip
            onClick={() => onOpenChange(false)}
          />
        </Dialog.Header>
      )}
      renderFooter={footer ? () => <Dialog.Footer>{footer}</Dialog.Footer> : undefined}
    >
      <div data-testid={testId}>{children}</div>
    </Dialog>
  );
}
