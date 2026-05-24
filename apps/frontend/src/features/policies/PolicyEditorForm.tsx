/**
 * PolicyEditorForm — the editable body of a license policy (v2.2 c3).
 *
 * A controlled form: the parent owns the draft (a `LicensePolicyUpsertIn`) and
 * passes `onChange`. This keeps the component pure and trivially testable. It
 * renders every editable field of a policy:
 *
 *   - enabled            master toggle (Switch)
 *   - name               optional display label
 *   - unknown_license_category   posture select
 *   - compound_operator_strategy AND / OR / WITH selects
 *   - category_overrides         add / edit / remove rows (SPDX id → category)
 *   - license_exceptions         add / remove waivers (spdx_id, reason, …)
 *
 * Read-only mode (`readOnly`) disables every control so a non-team_admin can
 * view the effective policy without mutating it. Design follows the compact,
 * inline enterprise density (no modal dialogs; SPDX ids in mono font).
 *
 * No hardcoded color hex literals or English strings — Tailwind tokens + `t()`
 * only (CLAUDE.md design system + i18n rules).
 */
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type {
  CompoundOperator,
  CompoundStrategy,
  LicensePolicyUpsertIn,
  PolicyCategory,
} from "@/lib/licensePoliciesApi";

const CATEGORY_OPTIONS: PolicyCategory[] = [
  "allowed",
  "conditional",
  "forbidden",
];
const STRATEGY_OPTIONS: CompoundStrategy[] = [
  "most_restrictive",
  "least_restrictive",
];
const COMPOUND_OPERATORS: CompoundOperator[] = ["AND", "OR", "WITH"];

interface Props {
  draft: LicensePolicyUpsertIn;
  onChange: (next: LicensePolicyUpsertIn) => void;
  readOnly?: boolean;
}

/** Category dot — color paired with a label so color is never the only signal. */
function CategoryDot({ category }: { category: PolicyCategory }) {
  const dot: Record<PolicyCategory, string> = {
    allowed: "bg-emerald-500",
    conditional: "bg-amber-500",
    forbidden: "bg-destructive",
  };
  return (
    <span
      aria-hidden
      className={cn("inline-block h-2 w-2 rounded-full", dot[category])}
      data-category={category}
    />
  );
}

export function PolicyEditorForm({ draft, onChange, readOnly = false }: Props) {
  const { t } = useTranslation("policies");

  const overrideEntries = Object.entries(draft.category_overrides);

  function patch(partial: Partial<LicensePolicyUpsertIn>) {
    onChange({ ...draft, ...partial });
  }

  // --- category_overrides handlers ---
  function setOverrideKey(oldKey: string, newKey: string) {
    const next: Record<string, PolicyCategory> = {};
    for (const [k, v] of Object.entries(draft.category_overrides)) {
      next[k === oldKey ? newKey : k] = v;
    }
    patch({ category_overrides: next });
  }
  function setOverrideValue(key: string, value: PolicyCategory) {
    patch({
      category_overrides: { ...draft.category_overrides, [key]: value },
    });
  }
  function removeOverride(key: string) {
    const next = { ...draft.category_overrides };
    delete next[key];
    patch({ category_overrides: next });
  }
  function addOverride() {
    if (Object.prototype.hasOwnProperty.call(draft.category_overrides, "")) {
      return; // a blank row already exists — fill it first
    }
    patch({
      category_overrides: { ...draft.category_overrides, "": "conditional" },
    });
  }

  // --- license_exceptions handlers ---
  function setException(index: number, field: string, value: string | null) {
    const next = draft.license_exceptions.map((ex, i) =>
      i === index ? { ...ex, [field]: value } : ex,
    );
    patch({ license_exceptions: next });
  }
  function removeException(index: number) {
    patch({
      license_exceptions: draft.license_exceptions.filter((_, i) => i !== index),
    });
  }
  function addException() {
    patch({
      license_exceptions: [
        ...draft.license_exceptions,
        { spdx_id: "", reason: "", expires_at: null, component_purl: null },
      ],
    });
  }

  const selectClass =
    "h-8 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

  return (
    <div className="flex flex-col gap-6" data-testid="policy-editor-form">
      {/* Enabled toggle */}
      <div className="flex items-center justify-between rounded-md border bg-card px-4 py-3">
        <div className="flex flex-col">
          <Label htmlFor="policy-enabled" className="text-sm font-medium">
            {t("policies.fields.enabled.label")}
          </Label>
          <span className="text-xs text-muted-foreground">
            {t("policies.fields.enabled.help")}
          </span>
        </div>
        <Switch
          id="policy-enabled"
          data-testid="policy-enabled-toggle"
          checked={draft.enabled}
          disabled={readOnly}
          aria-label={t("policies.fields.enabled.label")}
          onCheckedChange={(checked) => patch({ enabled: checked })}
        />
      </div>

      {/* Name */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="policy-name" className="text-sm font-medium">
          {t("policies.fields.name.label")}
        </Label>
        <Input
          id="policy-name"
          data-testid="policy-name-input"
          className="h-8 max-w-sm text-sm"
          value={draft.name ?? ""}
          disabled={readOnly}
          placeholder={t("policies.fields.name.placeholder")}
          onChange={(e) => patch({ name: e.target.value || null })}
        />
      </div>

      {/* Unknown license posture */}
      <div className="flex flex-col gap-1">
        <Label htmlFor="policy-unknown" className="text-sm font-medium">
          {t("policies.fields.unknown.label")}
        </Label>
        <span className="text-xs text-muted-foreground">
          {t("policies.fields.unknown.help")}
        </span>
        <select
          id="policy-unknown"
          data-testid="policy-unknown-select"
          className={cn(selectClass, "max-w-xs")}
          value={draft.unknown_license_category}
          disabled={readOnly}
          onChange={(e) =>
            patch({
              unknown_license_category: e.target.value as PolicyCategory,
            })
          }
        >
          {CATEGORY_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`policies.category.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      {/* Compound operator strategy */}
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">
          {t("policies.fields.compound.label")}
        </legend>
        <span className="text-xs text-muted-foreground">
          {t("policies.fields.compound.help")}
        </span>
        <div className="flex flex-wrap gap-4" data-testid="policy-compound">
          {COMPOUND_OPERATORS.map((op) => (
            <div key={op} className="flex flex-col gap-1">
              <Label
                htmlFor={`policy-compound-${op}`}
                className="font-mono text-xs text-muted-foreground"
              >
                {op}
              </Label>
              <select
                id={`policy-compound-${op}`}
                data-testid={`policy-compound-${op}`}
                className={selectClass}
                value={draft.compound_operator_strategy[op]}
                disabled={readOnly}
                onChange={(e) =>
                  patch({
                    compound_operator_strategy: {
                      ...draft.compound_operator_strategy,
                      [op]: e.target.value as CompoundStrategy,
                    },
                  })
                }
              >
                {STRATEGY_OPTIONS.map((opt) => (
                  <option key={opt} value={opt}>
                    {t(`policies.strategy.${opt}`)}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </fieldset>

      {/* Category overrides */}
      <section className="flex flex-col gap-2" data-testid="policy-overrides">
        <div className="flex items-center justify-between">
          <div className="flex flex-col">
            <h3 className="text-sm font-medium">
              {t("policies.overrides.title")}
            </h3>
            <span className="text-xs text-muted-foreground">
              {t("policies.overrides.help")}
            </span>
          </div>
          {!readOnly ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={addOverride}
              data-testid="policy-add-override"
            >
              {t("policies.overrides.add")}
            </Button>
          ) : null}
        </div>

        {overrideEntries.length === 0 ? (
          <p
            className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground"
            data-testid="policy-overrides-empty"
          >
            {t("policies.overrides.empty")}
          </p>
        ) : (
          <ul className="flex flex-col divide-y rounded-md border">
            {overrideEntries.map(([key, value], idx) => (
              <li
                key={`${idx}-${key}`}
                className="flex items-center gap-2 px-3"
                style={{ minHeight: "var(--table-row)" }}
                data-testid="policy-override-row"
              >
                <CategoryDot category={value} />
                <Input
                  className="h-7 flex-1 font-mono text-xs"
                  value={key}
                  disabled={readOnly}
                  aria-label={t("policies.overrides.spdx_label")}
                  placeholder={t("policies.overrides.spdx_placeholder")}
                  data-testid="policy-override-key"
                  onChange={(e) => setOverrideKey(key, e.target.value)}
                />
                <select
                  className={selectClass}
                  value={value}
                  disabled={readOnly}
                  aria-label={t("policies.overrides.category_label")}
                  data-testid="policy-override-category"
                  onChange={(e) =>
                    setOverrideValue(key, e.target.value as PolicyCategory)
                  }
                >
                  {CATEGORY_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {t(`policies.category.${opt}`)}
                    </option>
                  ))}
                </select>
                {!readOnly ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => removeOverride(key)}
                    data-testid="policy-remove-override"
                    aria-label={t("policies.overrides.remove")}
                  >
                    {t("policies.overrides.remove")}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* License exceptions */}
      <section className="flex flex-col gap-2" data-testid="policy-exceptions">
        <div className="flex items-center justify-between">
          <div className="flex flex-col">
            <h3 className="text-sm font-medium">
              {t("policies.exceptions.title")}
            </h3>
            <span className="text-xs text-muted-foreground">
              {t("policies.exceptions.help")}
            </span>
          </div>
          {!readOnly ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={addException}
              data-testid="policy-add-exception"
            >
              {t("policies.exceptions.add")}
            </Button>
          ) : null}
        </div>

        {draft.license_exceptions.length === 0 ? (
          <p
            className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground"
            data-testid="policy-exceptions-empty"
          >
            {t("policies.exceptions.empty")}
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {draft.license_exceptions.map((ex, index) => (
              <li
                key={index}
                className="flex flex-col gap-2 rounded-md border bg-card p-3"
                data-testid="policy-exception-row"
              >
                <div className="flex flex-wrap gap-2">
                  <div className="flex flex-1 flex-col gap-1">
                    <Label
                      htmlFor={`exc-spdx-${index}`}
                      className="text-xs text-muted-foreground"
                    >
                      {t("policies.exceptions.spdx_label")}
                    </Label>
                    <Input
                      id={`exc-spdx-${index}`}
                      className="h-7 font-mono text-xs"
                      value={ex.spdx_id}
                      disabled={readOnly}
                      data-testid="policy-exception-spdx"
                      onChange={(e) =>
                        setException(index, "spdx_id", e.target.value)
                      }
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label
                      htmlFor={`exc-expiry-${index}`}
                      className="text-xs text-muted-foreground"
                    >
                      {t("policies.exceptions.expiry_label")}
                    </Label>
                    <Input
                      id={`exc-expiry-${index}`}
                      type="date"
                      className="h-7 w-40 text-xs"
                      value={(ex.expires_at ?? "").slice(0, 10)}
                      disabled={readOnly}
                      data-testid="policy-exception-expiry"
                      onChange={(e) =>
                        setException(
                          index,
                          "expires_at",
                          e.target.value
                            ? `${e.target.value}T00:00:00Z`
                            : null,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <Label
                    htmlFor={`exc-purl-${index}`}
                    className="text-xs text-muted-foreground"
                  >
                    {t("policies.exceptions.purl_label")}
                  </Label>
                  <Input
                    id={`exc-purl-${index}`}
                    className="h-7 font-mono text-xs"
                    value={ex.component_purl ?? ""}
                    disabled={readOnly}
                    placeholder={t("policies.exceptions.purl_placeholder")}
                    data-testid="policy-exception-purl"
                    onChange={(e) =>
                      setException(
                        index,
                        "component_purl",
                        e.target.value || null,
                      )
                    }
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label
                    htmlFor={`exc-reason-${index}`}
                    className="text-xs text-muted-foreground"
                  >
                    {t("policies.exceptions.reason_label")}
                  </Label>
                  <Textarea
                    id={`exc-reason-${index}`}
                    className="min-h-[3rem] text-xs"
                    value={ex.reason}
                    disabled={readOnly}
                    placeholder={t("policies.exceptions.reason_placeholder")}
                    data-testid="policy-exception-reason"
                    onChange={(e) =>
                      setException(index, "reason", e.target.value)
                    }
                  />
                </div>
                {!readOnly ? (
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => removeException(index)}
                      data-testid="policy-remove-exception"
                    >
                      {t("policies.exceptions.remove")}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
