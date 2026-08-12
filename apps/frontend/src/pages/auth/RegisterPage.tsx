// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, Info } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { AuthLayout } from "@/pages/auth/AuthLayout";
import {
  DEMO_LOGIN_EMAIL,
  DEMO_LOGIN_PASSWORD,
} from "@/pages/auth/DemoCredentialsHint";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useDemoMode } from "@/hooks/useDemoMode";
import { fetchMe, postLogin, postRegister } from "@/lib/api";
import { isDemoReadOnlyError } from "@/lib/demoReadOnly";
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

/**
 * What `/register` shows on the public read-only demo, in place of the form.
 *
 * The demo middleware has no allow-list entry for `POST /auth/register`, so the
 * form could only ever end in a 403. Rendering it anyway made the page a dead
 * end: the visitor filled in three fields, submitted, and got an English
 * problem `detail` back. This says the same thing before the typing, and hands
 * over the seeded account so there is somewhere to go.
 *
 * The account values come from `DemoCredentialsHint` rather than being repeated
 * here, because that module is the one place kept in sync with `seed_demo.py`.
 */
function DemoSignupNotice() {
  const { t } = useTranslation("auth");

  return (
    <div className="space-y-4" data-testid="register-demo-notice">
      <Alert
        variant="default"
        className="border-status-info-border bg-status-info-subtle text-status-info-foreground"
      >
        <Info
          className="h-4 w-4 text-status-info-foreground"
          aria-hidden
        />
        <AlertDescription className="space-y-2">
          <p>{t("register.demo.detail")}</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs">
            <dt className="opacity-80">{t("register.demo.email_label")}</dt>
            <dd data-testid="register-demo-email">{DEMO_LOGIN_EMAIL}</dd>
            <dt className="opacity-80">
              {t("register.demo.password_label")}
            </dt>
            <dd data-testid="register-demo-password">{DEMO_LOGIN_PASSWORD}</dd>
          </dl>
          <p className="text-xs opacity-80">
            {t("register.demo.password_note")}
          </p>
        </AlertDescription>
      </Alert>
      <Button asChild className="w-full" data-testid="register-demo-signin">
        <Link to="/login">{t("register.demo.signin_button")}</Link>
      </Button>
    </div>
  );
}

export function RegisterPage() {
  const { t } = useTranslation("auth");
  const navigate = useNavigate();
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setUser = useAuthStore((s) => s.setUser);
  const setStatus = useAuthStore((s) => s.setStatus);
  const status = useAuthStore((s) => s.status);
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The public demo does not create accounts. We gate on `isResolving` as well
  // as the flag: the hook seeds `demoReadOnly` from the build hint, which the
  // demo image does not set, so without the gate the form would paint for one
  // frame and then be replaced by the notice.
  const { demoReadOnly, isResolving } = useDemoMode();

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
      if (isDemoReadOnlyError(err)) {
        // Reachable only in the sliver before /health resolves (or if the flag
        // flips under a loaded page). The backend `detail` is English-only, so
        // use the translated string every other write surface shows.
        setApiError(t("common:demo.write_disabled"));
      } else if (err instanceof ProblemError) {
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

  // Read-only demo: no form at all. While the flag is still the build hint we
  // show neither, and a skeleton holds the space, so the visitor never sees a
  // form appear and then be taken away.
  if (isResolving || demoReadOnly) {
    return (
      <AuthLayout
        testId="register-page"
        title={demoReadOnly ? t("register.demo.title") : t("register.title")}
        subtitle={demoReadOnly ? undefined : t("register.subtitle")}
      >
        {demoReadOnly ? (
          <DemoSignupNotice />
        ) : (
          <div className="space-y-4" data-testid="register-resolving">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        )}
      </AuthLayout>
    );
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
