// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * react-hook-form glue for Primer inputs.
 *
 * Same shape as the shadcn form helpers this replaces (Form, FormField,
 * FormItem, FormLabel, FormControl, FormDescription, FormMessage), so screens
 * move over by changing the import. What changes inside: no Radix. The label is
 * a plain <label>, and FormControl hands the field's id and ARIA wiring to its
 * one child by cloning it. When the field is invalid and the child is a Primer
 * TextInput or Textarea, it also sets `validationStatus="error"` so the red
 * border shows; other children keep just aria-invalid.
 */
import { Textarea, TextInput } from "@primer/react";
import {
  Children,
  cloneElement,
  createContext,
  isValidElement,
  useContext,
  useId,
  type HTMLAttributes,
  type LabelHTMLAttributes,
  type ReactElement,
} from "react";
import {
  Controller,
  FormProvider,
  useFormContext,
  type ControllerProps,
  type FieldPath,
  type FieldValues,
} from "react-hook-form";

import { cn } from "@/lib/utils";

export const Form = FormProvider;

interface FormFieldContextValue {
  name: string;
}
const FormFieldContext = createContext<FormFieldContextValue | undefined>(undefined);

export function FormField<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>,
>(props: ControllerProps<TFieldValues, TName>) {
  return (
    <FormFieldContext.Provider value={{ name: props.name }}>
      <Controller {...props} />
    </FormFieldContext.Provider>
  );
}

const FormItemContext = createContext<{ id: string } | undefined>(undefined);

function useFormField() {
  const field = useContext(FormFieldContext);
  const item = useContext(FormItemContext);
  const { getFieldState, formState } = useFormContext();
  if (!field) throw new Error("useFormField must be used within <FormField>");
  if (!item) throw new Error("useFormField must be used within <FormItem>");
  const state = getFieldState(field.name, formState);
  return {
    name: field.name,
    formItemId: `${item.id}-form-item`,
    formDescriptionId: `${item.id}-form-item-description`,
    formMessageId: `${item.id}-form-item-message`,
    ...state,
  };
}

export function FormItem({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  const id = useId();
  return (
    <FormItemContext.Provider value={{ id }}>
      <div className={cn("space-y-2", className)} {...props} />
    </FormItemContext.Provider>
  );
}

export function FormLabel({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  const { error, formItemId } = useFormField();
  return (
    <label
      htmlFor={formItemId}
      className={cn(
        "block text-sm font-medium leading-none",
        error && "text-destructive",
        className,
      )}
      {...props}
    />
  );
}

export function FormControl({ children }: { children: ReactElement }) {
  const { error, formItemId, formDescriptionId, formMessageId } = useFormField();
  const only = Children.only(children);
  if (!isValidElement(only)) return only;
  const invalid = Boolean(error);
  const wiring: Record<string, unknown> = {
    id: formItemId,
    "aria-describedby": invalid ? `${formDescriptionId} ${formMessageId}` : formDescriptionId,
    "aria-invalid": invalid,
  };
  if (invalid && (only.type === TextInput || only.type === Textarea)) {
    wiring.validationStatus = "error";
  }
  return cloneElement(only as ReactElement<Record<string, unknown>>, wiring);
}

export function FormDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  const { formDescriptionId } = useFormField();
  return (
    <p
      id={formDescriptionId}
      className={cn("text-xs text-muted-foreground", className)}
      {...props}
    />
  );
}

export function FormMessage({ className, children, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  const { error, formMessageId } = useFormField();
  // problem-detail-lint-allow: a form field's validation message, never a ProblemError
  const body = error ? String(error.message ?? "") : children;
  if (!body) return null;
  return (
    <p
      id={formMessageId}
      aria-live="polite"
      className={cn("text-xs font-medium text-destructive", className)}
      {...props}
    >
      {body}
    </p>
  );
}
