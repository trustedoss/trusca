// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * BuiltInRulesPanel - the rules that apply when no team policy overrides them.
 *
 * Read-only. The classification and the build gate default are in force from
 * the first scan, but until now they were visible only after a team was picked
 * and its drawer opened. The category examples mirror the built-in catalog
 * (`_LICENSE_CATEGORY_DEFAULTS` in the backend scan task); the gate line
 * mirrors `services/policy_gate.py`. They are an illustrative summary, not an
 * exhaustive list, and the copy says so.
 */
import { useTranslation } from "react-i18next";

const CATEGORIES = ["allowed", "conditional", "forbidden"] as const;

export function BuiltInRulesPanel() {
  const { t } = useTranslation("policies");
  return (
    <section
      className="border-b bg-background px-6 py-4"
      data-testid="policies-builtin"
      aria-labelledby="policies-builtin-heading"
    >
      <h2
        id="policies-builtin-heading"
        className="text-base font-semibold tracking-tight"
      >
        {t("policies.builtin.heading")}
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">
        {t("policies.builtin.description")}
      </p>
      <dl className="mt-3 grid gap-3 md:grid-cols-3">
        {CATEGORIES.map((category) => (
          <div
            key={category}
            className="rounded-md border bg-card p-3"
            data-testid={`policies-builtin-${category}`}
          >
            <dt className="text-sm font-semibold">
              {t(`policies.category.${category}`)}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {t(`policies.builtin.${category}_effect`)}
              </span>
            </dt>
            <dd className="mt-1 font-mono text-xs text-muted-foreground">
              {t(`policies.builtin.${category}_examples`)}
            </dd>
          </div>
        ))}
      </dl>
      <p
        className="mt-3 text-sm"
        data-testid="policies-builtin-gate"
      >
        {t("policies.builtin.gate")}
      </p>
    </section>
  );
}
