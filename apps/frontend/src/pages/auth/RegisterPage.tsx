import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { AuthLayout } from "@/pages/auth/AuthLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { fetchMe, postLogin, postRegister } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

function buildSchema(t: (key: string) => string) {
  return z.object({
    display_name: z.string().min(1, { message: t("errors.required") }),
    email: z.string().email({ message: t("errors.email_invalid") }),
    // Backend enforces ≥8 (NIST 800-63B minimum). We mirror the policy on the
    // client so users get inline feedback before round-tripping. Backend
    // remains the source of truth — its 422 flows through the alert.
    password: z
      .string()
      .min(8, { message: t("errors.password_too_short") }),
  });
}

type RegisterValues = z.infer<ReturnType<typeof buildSchema>>;

export function RegisterPage() {
  const { t } = useTranslation("auth");
  const navigate = useNavigate();
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setUser = useAuthStore((s) => s.setUser);
  const setStatus = useAuthStore((s) => s.setStatus);
  const status = useAuthStore((s) => s.status);
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (status === "authenticated") {
      navigate("/", { replace: true });
    }
  }, [status, navigate]);

  const form = useForm<RegisterValues>({
    resolver: zodResolver(buildSchema(t)),
    defaultValues: { display_name: "", email: "", password: "" },
  });

  async function onSubmit(values: RegisterValues) {
    setApiError(null);
    setSubmitting(true);
    // L-1 (PR #6 follow-up): split register vs auto-login error handling.
    // The /auth/login rate limiter (5/min/IP) can collide with a freshly-
    // created account — the user would see a confusing alert on the register
    // form even though the account exists. Treat any auto-login failure as
    // "account created, please sign in" and bounce to /login?registered=1.
    try {
      await postRegister({
        email: values.email,
        password: values.password,
        full_name: values.display_name,
      });
    } catch (err) {
      if (err instanceof ProblemError) {
        setApiError(err.detail || err.title || t("errors.unknown"));
      } else {
        setApiError(t("errors.network"));
      }
      setSubmitting(false);
      return;
    }

    try {
      const tokens = await postLogin({
        email: values.email,
        password: values.password,
      });
      setAccessToken(tokens.access_token);
      const me = await fetchMe();
      setUser(me);
      setStatus("authenticated");
      navigate("/", { replace: true });
    } catch {
      // Auto-login failed (rate limit, transient backend, /me hiccup). The
      // account is real — surface that on /login via ?registered=1 instead of
      // stranding the user on the register form.
      navigate("/login?registered=1", { replace: true });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      testId="register-page"
      title={t("register.title")}
      subtitle={t("register.subtitle")}
      footer={
        <>
          {t("register.have_account")}{" "}
          <Link
            to="/login"
            className="font-medium text-primary hover:underline"
            data-testid="register-signin-link"
          >
            {t("register.signin_link")}
          </Link>
        </>
      }
    >
      {apiError ? (
        <Alert variant="destructive" data-testid="register-error">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <AlertDescription>{apiError}</AlertDescription>
        </Alert>
      ) : null}

      <Form {...form}>
        <form
          noValidate
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-4"
          data-testid="register-form"
        >
          <FormField
            control={form.control}
            name="display_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("register.display_name_label")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="name"
                    data-testid="register-display-name"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("register.email_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="email"
                    data-testid="register-email"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("register.password_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    autoComplete="new-password"
                    data-testid="register-password"
                    {...field}
                  />
                </FormControl>
                <FormDescription>{t("register.password_help")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={submitting}
            data-testid="register-submit"
          >
            {t("register.submit")}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
