/**
 * ResetPasswordPage — chore A1.
 *
 * Public page reached from the email link `/reset-password?token=<jwt>`.
 * Renders a new-password + confirm-password form, POSTs to
 * `/auth/reset-password` (PR #22 backend), and on 204 navigates to /login
 * with a success flag the LoginPage already knows how to render
 * (`?registered=1` semantics — same "account ready, sign in" affordance).
 *
 * Errors:
 *   - Missing `?token=` query → render an "invalid link" error block with
 *     a back-to-forgot link. No form is shown.
 *   - 422 from the backend (invalid / expired / reused) → inline alert
 *     using `auth.reset.errors.<title>` keys with a generic fallback so
 *     the user can request a fresh link.
 */
import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { AuthLayout } from "@/pages/auth/AuthLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { postResetPassword } from "@/lib/api";
import { ProblemError } from "@/lib/problem";

function buildSchema(t: (key: string) => string) {
  return z
    .object({
      // Mirror the backend NIST 800-63B floor (8 chars). The backend
      // remains the source of truth: a 422 from the server still flows
      // into the alert.
      password: z
        .string()
        .min(8, { message: t("errors.password_too_short") }),
      confirmPassword: z.string(),
    })
    .refine((v) => v.password === v.confirmPassword, {
      path: ["confirmPassword"],
      message: t("reset.errors.mismatch"),
    });
}

type ResetValues = z.infer<ReturnType<typeof buildSchema>>;

/**
 * Map a ProblemError back to an i18n key under `auth.reset.errors.*`.
 * The backend titles for the 422 are `invalid_reset_token` (used or
 * malformed) and `expired_reset_token`.
 */
function problemKey(err: ProblemError): string {
  const t = (err.problem?.title ?? err.title ?? "").toLowerCase();
  if (t.includes("expired")) return "reset.errors.expired";
  if (t.includes("invalid") || t.includes("reset_token")) {
    return "reset.errors.invalid";
  }
  if (err.status === 0) return "errors.network";
  return "errors.unknown";
}

export function ResetPasswordPage() {
  const { t } = useTranslation("auth");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const [apiErrorKey, setApiErrorKey] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<ResetValues>({
    resolver: zodResolver(buildSchema(t)),
    defaultValues: { password: "", confirmPassword: "" },
  });

  async function onSubmit(values: ResetValues) {
    if (!token) return;
    setApiErrorKey(null);
    setSubmitting(true);
    try {
      await postResetPassword(token, values.password);
      // Success — the backend has revoked every refresh token for the
      // user, so we cannot silently sign them in. Redirect to /login
      // with the existing "?registered=1" success affordance so the
      // LoginPage shows a positive confirmation banner.
      navigate("/login?registered=1", { replace: true });
    } catch (err) {
      if (err instanceof ProblemError) {
        setApiErrorKey(problemKey(err));
      } else {
        setApiErrorKey("errors.unknown");
      }
    } finally {
      setSubmitting(false);
    }
  }

  // Missing token → render an error state with a way back.
  if (!token) {
    return (
      <AuthLayout
        testId="reset-page"
        title={t("reset.title")}
        subtitle={t("reset.subtitle")}
        footer={
          <Link
            to="/forgot-password"
            className="font-medium text-primary hover:underline"
            data-testid="reset-forgot-link"
          >
            {t("reset.request_new")}
          </Link>
        }
      >
        <Alert variant="destructive" data-testid="reset-invalid-link">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <AlertDescription>{t("reset.errors.missing_token")}</AlertDescription>
        </Alert>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      testId="reset-page"
      title={t("reset.title")}
      subtitle={t("reset.subtitle")}
      footer={
        <Link
          to="/login"
          className="font-medium text-primary hover:underline"
          data-testid="reset-back-link"
        >
          {t("reset.back_to_login")}
        </Link>
      }
    >
      {apiErrorKey ? (
        <Alert variant="destructive" data-testid="reset-error">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <AlertDescription>{t(apiErrorKey)}</AlertDescription>
        </Alert>
      ) : (
        <Alert data-testid="reset-info">
          <CheckCircle2 className="h-4 w-4" aria-hidden />
          <AlertDescription>{t("reset.info")}</AlertDescription>
        </Alert>
      )}

      <Form {...form}>
        <form
          noValidate
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-4"
          data-testid="reset-form"
        >
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("reset.password_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    autoComplete="new-password"
                    data-testid="reset-password"
                    disabled={submitting}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="confirmPassword"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("reset.confirm_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    autoComplete="new-password"
                    data-testid="reset-confirm"
                    disabled={submitting}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={submitting}
            data-testid="reset-submit"
          >
            {submitting ? t("reset.submitting") : t("reset.submit")}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
