// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Primer's Spinner with a translated screen reader label. Without `srText`
 * Primer announces a fixed English "Loading", which a Korean reader would hear
 * as the only English word on the screen. Import this, never Primer's own.
 */
import { Spinner as PrimerSpinner, type SpinnerProps } from "@primer/react";
import { useTranslation } from "react-i18next";

export function Spinner(props: Omit<SpinnerProps, "srText">) {
  const { t } = useTranslation("common");
  return <PrimerSpinner srText={t("spinner.loading")} {...props} />;
}
