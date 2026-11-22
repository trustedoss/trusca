// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * A single-choice button group on Primer's SegmentedControl.
 *
 * Screens used to draw this four different ways (filled navy pills, a ghost
 * pair, a bordered strip, fake tabs), so a filter could look like the page's
 * primary button. Every single-choice group now goes through here.
 *
 * The API is value based: pass the options, the selected value and an
 * `onChange` that receives the value, not Primer's index. Each option renders
 * a `<button aria-pressed>`; the option's `testId` and a `data-active`
 * attribute ("true" or "false") land on that button, so tests and the
 * Playwright harness can read the selection the way they read the old
 * hand-rolled groups. Keyboard behavior is Primer's: every option is a Tab
 * stop and Space or Enter selects it.
 *
 * Primer renders no fixed English text here, but it logs a warning when the
 * group has no accessible name. The props type requires `aria-label` or
 * `aria-labelledby`, so callers pass a translated name.
 */
import { SegmentedControl as PrimerSegmentedControl } from "@primer/react";
import type { ReactElement } from "react";

export interface SegmentedControlOption<V> {
  value: V;
  /** Translated visible label. */
  label: string;
  /** `data-testid` for this option's button. */
  testId?: string;
  disabled?: boolean;
}

type AccessibleName =
  | { "aria-label": string; "aria-labelledby"?: string }
  | { "aria-label"?: string; "aria-labelledby": string };

interface BaseProps<V> {
  options: readonly SegmentedControlOption<V>[];
  value: V;
  onChange: (value: V) => void;
  /** Primer's sizes: small is 28 px, medium (default) is 32 px like other controls. */
  size?: "small" | "medium";
  fullWidth?: boolean;
  className?: string;
  "data-testid"?: string;
  "aria-describedby"?: string;
}

export type SegmentedControlProps<V> = BaseProps<V> & AccessibleName;

export function SegmentedControl<V>({
  options,
  value,
  onChange,
  size = "medium",
  fullWidth,
  className,
  ...rest
}: SegmentedControlProps<V>): ReactElement {
  return (
    <PrimerSegmentedControl
      {...rest}
      size={size}
      fullWidth={fullWidth}
      className={className}
      onChange={(index) => {
        const next = options[index];
        if (next && !next.disabled) onChange(next.value);
      }}
    >
      {options.map((option) => {
        const selected = Object.is(option.value, value);
        return (
          <PrimerSegmentedControl.Button
            key={String(option.value)}
            selected={selected}
            disabled={option.disabled}
            data-testid={option.testId}
            data-active={selected ? "true" : "false"}
          >
            {option.label}
          </PrimerSegmentedControl.Button>
        );
      })}
    </PrimerSegmentedControl>
  );
}
