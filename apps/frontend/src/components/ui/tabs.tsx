import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useId,
  useMemo,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Tabs — minimal accessible primitive (PR #10).
 *
 * Stand-in for shadcn's `@radix-ui/react-tabs` based component. The portal
 * already locks dependencies, so we hand-rolled the smallest API surface we
 * need: `<Tabs>`, `<TabsList>`, `<TabsTrigger>`, `<TabsContent>`.
 *
 *   - Controlled or uncontrolled (`defaultValue` for uncontrolled).
 *   - Roving keyboard (`Arrow Left/Right/Home/End`) per WAI-ARIA pattern.
 *   - `aria-selected`, `aria-controls`, `role="tab|tablist|tabpanel"`
 *     wired up so a screen reader announces the tablist correctly.
 *   - `disabled` triggers are skipped by keyboard navigation and cannot
 *     receive `onValueChange`.
 *
 * If a future PR adopts radix-tabs we can drop this in place — the API
 * matches the shadcn/radix shape one-for-one.
 */

interface TabsContextValue {
  value: string;
  setValue: (next: string) => void;
  baseId: string;
  registerTrigger: (value: string, disabled: boolean | undefined) => void;
  triggers: Array<{ value: string; disabled: boolean | undefined }>;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabsContext(component: string): TabsContextValue {
  const ctx = useContext(TabsContext);
  if (!ctx) {
    throw new Error(`<${component}> must be used inside <Tabs>`);
  }
  return ctx;
}

export interface TabsProps {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  children: ReactNode;
  className?: string;
}

export function Tabs({
  value,
  defaultValue,
  onValueChange,
  children,
  className,
}: TabsProps) {
  const [internal, setInternal] = useState(defaultValue ?? "");
  const isControlled = value !== undefined;
  const current = isControlled ? value : internal;
  const baseId = useId();

  const triggers = useMemo<Array<{ value: string; disabled: boolean | undefined }>>(
    () => [],
    [],
  );

  const registerTrigger = useCallback(
    (v: string, disabled: boolean | undefined) => {
      const existing = triggers.find((t) => t.value === v);
      if (existing) {
        existing.disabled = disabled;
      } else {
        triggers.push({ value: v, disabled });
      }
    },
    [triggers],
  );

  const setValue = useCallback(
    (next: string) => {
      if (!isControlled) setInternal(next);
      onValueChange?.(next);
    },
    [isControlled, onValueChange],
  );

  const ctx = useMemo<TabsContextValue>(
    () => ({ value: current, setValue, baseId, registerTrigger, triggers }),
    [current, setValue, baseId, registerTrigger, triggers],
  );

  return (
    <TabsContext.Provider value={ctx}>
      <div className={cn("flex flex-col", className)}>{children}</div>
    </TabsContext.Provider>
  );
}

export const TabsList = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      role="tablist"
      className={cn(
        "inline-flex items-center gap-1 border-b bg-background px-2",
        className,
      )}
      {...props}
    />
  ),
);
TabsList.displayName = "TabsList";

export interface TabsTriggerProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  value: string;
}

export const TabsTrigger = forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ className, value, disabled, onKeyDown, ...props }, ref) => {
    const ctx = useTabsContext("TabsTrigger");
    // Register so keyboard navigation knows about us.
    ctx.registerTrigger(value, disabled);

    const isSelected = ctx.value === value;

    function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
      if (
        event.key === "ArrowRight" ||
        event.key === "ArrowLeft" ||
        event.key === "Home" ||
        event.key === "End"
      ) {
        event.preventDefault();
        const enabled = ctx.triggers.filter((t) => !t.disabled);
        if (enabled.length === 0) return;
        const idx = enabled.findIndex((t) => t.value === ctx.value);
        let next = idx;
        if (event.key === "ArrowRight") next = (idx + 1) % enabled.length;
        else if (event.key === "ArrowLeft")
          next = (idx - 1 + enabled.length) % enabled.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = enabled.length - 1;
        ctx.setValue(enabled[next].value);
      }
      onKeyDown?.(event);
    }

    return (
      <button
        ref={ref}
        type="button"
        role="tab"
        aria-selected={isSelected}
        aria-controls={`${ctx.baseId}-panel-${value}`}
        id={`${ctx.baseId}-trigger-${value}`}
        tabIndex={isSelected ? 0 : -1}
        data-state={isSelected ? "active" : "inactive"}
        data-value={value}
        disabled={disabled}
        onClick={() => {
          if (!disabled) ctx.setValue(value);
        }}
        onKeyDown={handleKeyDown}
        className={cn(
          "inline-flex h-9 items-center whitespace-nowrap rounded-t-md border-b-2 border-transparent px-3 text-sm font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          "disabled:pointer-events-none disabled:opacity-50",
          isSelected
            ? "border-primary text-foreground"
            : "text-muted-foreground hover:text-foreground",
          className,
        )}
        {...props}
      />
    );
  },
);
TabsTrigger.displayName = "TabsTrigger";

export interface TabsContentProps extends HTMLAttributes<HTMLDivElement> {
  value: string;
}

export const TabsContent = forwardRef<HTMLDivElement, TabsContentProps>(
  ({ className, value, ...props }, ref) => {
    const ctx = useTabsContext("TabsContent");
    const isSelected = ctx.value === value;
    if (!isSelected) return null;
    return (
      <div
        ref={ref}
        role="tabpanel"
        id={`${ctx.baseId}-panel-${value}`}
        aria-labelledby={`${ctx.baseId}-trigger-${value}`}
        data-state="active"
        data-value={value}
        className={cn("flex flex-col", className)}
        {...props}
      />
    );
  },
);
TabsContent.displayName = "TabsContent";
