import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { AuthLayout } from "@/pages/auth/AuthLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ProviderIcon } from "@/components/ProviderIcon";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { fetchMe, postLogin } from "@/lib/api";
import { getApiBase } from "@/lib/apiBase";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

function buildSchema(t: (key: string) => string) {
  return z.object({
    email: z.string().email({ message: t("errors.email_invalid") }),
    // Mirror the backend's NIST 800-63B floor of 8. Backend remains the
    // source of truth — its 422 (or our shorter-than-8 error) flows to the
    // alert if the client gate is somehow bypassed.
    password: z
      .string()
      .min(8, { message: t("errors.password_too_short") }),
  });
}

type LoginValues = z.infer<ReturnType<typeof buildSchema>>;

// Closed allow-list of OAuth error codes the backend may forward via
// `?error=oauth_*` (see apps/backend/api/v1/oauth.py:178-224). Anything
// else falls through to the generic `unknown` key — the SPA never trusts
// the raw query string verbatim because it's reflected from the browser.
const OAUTH_ERROR_KEYS: Record<string, string> = {
  oauth_denied: "oauth.errors.denied",
  oauth_invalid_state: "oauth.errors.invalid_state",
  oauth_failed: "oauth.errors.failed",
  oauth_user_inactive: "oauth.errors.user_inactive",
  oauth_no_organization: "oauth.errors.no_organization",
  oauth_missing_params: "oauth.errors.missing_params",
};

export function LoginPage() {
  const { t } = useTranslation("auth");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setUser = useAuthStore((s) => s.setUser);
  const setStatus = useAuthStore((s) => s.setStatus);
  const status = useAuthStore((s) => s.status);
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // L-1 (PR #6 follow-up): RegisterPage redirects here with ?registered=1 when
  // auto-login after a successful POST /auth/register fails (e.g. /auth/login
  // rate-limited the same IP). We surface a non-destructive success alert so
  // the user knows the account exists and just needs to sign in.
  const justRegistered = searchParams.get("registered") === "1";

  // chore B — OAuth error codes are forwarded via ?error=oauth_*. We keep
  // the raw value local so a malicious URL like ?error=<script> stays
  // confined to a controlled mapping; only the matching i18n key is rendered.
  const rawError = searchParams.get("error");
  const oauthErrorKey = useMemo(() => {
    if (!rawError) return null;
    if (rawError.startsWith("oauth_")) {
      return OAUTH_ERROR_KEYS[rawError] ?? "oauth.errors.unknown";
    }
    return null;
  }, [rawError]);

  // The OAuth `redirect_after` propagates through the provider round-trip:
  // - SPA reads it from the URL query (or defaults to "/").
  // - We pass it to /auth/oauth/<provider>/authorize so the backend stamps
  //   it into the signed state JWT.
  // - The provider posts back to /auth/oauth/<provider>/callback, which 302s
  //   the user to that URL. Note we URI-encode but DO NOT validate the value
  //   here — the backend (services/oauth_service.py) is the source of truth
  //   for which redirect_after values are safe.
  const redirectAfter = searchParams.get("redirect_after") ?? "/";

  // If a refresh cookie is alive (e.g., user reloaded /login after auth) the
  // store will resolve to "authenticated" via bootstrap → bounce to /.
  useEffect(() => {
    if (status === "authenticated") {
      navigate("/", { replace: true });
    }
  }, [status, navigate]);

  const form = useForm<LoginValues>({
    resolver: zodResolver(buildSchema(t)),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginValues) {
    setApiError(null);
    setSubmitting(true);
    try {
      const tokens = await postLogin(values);
      setAccessToken(tokens.access_token);
      // Hydrate the canonical user record from /auth/me so the UI has the
      // real id / role / superuser flag — never trust client-side guesses.
      const me = await fetchMe();
      setUser(me);
      setStatus("authenticated");
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ProblemError) {
        setApiError(err.detail || err.title || t("errors.unknown"));
      } else {
        setApiError(t("errors.network"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  function startOAuth(provider: "github" | "google") {
    const apiBase = getApiBase();
    const url =
      `${apiBase}/auth/oauth/${provider}/authorize` +
      `?redirect_after=${encodeURIComponent(redirectAfter)}`;
    // Full-page navigation is required: the backend issues a 302 to the
    // provider's consent page, which an XHR cannot follow cross-origin.
    window.location.href = url;
  }

  return (
    <AuthLayout
      testId="login-page"
      /* BUGHUNTER-GOLDEN(GOLD-P6-VISION): 번역 대신 raw i18n 키를 노출 — 화면에 'login.title' 가 그대로 보임 */
      title={"login.title"}
      subtitle={t("login.subtitle")}
      footer={
        <>
          {t("login.no_account")}{" "}
          <Link
            to="/register"
            className="font-medium text-primary hover:underline"
            data-testid="login-signup-link"
          >
            {t("login.signup_link")}
          </Link>
        </>
      }
    >
      {oauthErrorKey ? (
        <Alert variant="destructive" data-testid="login-oauth-error">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <AlertDescription>{t(oauthErrorKey)}</AlertDescription>
        </Alert>
      ) : null}

      {justRegistered && !apiError ? (
        // shadcn Alert ships only `default` and `destructive` variants. We use
        // default + success-toned text/icon so the message reads as positive
        // confirmation, not an error. Color is paired with an icon (a11y).
        <Alert
          variant="default"
          className="border-emerald-500/50 text-emerald-700 dark:text-emerald-400 [&>svg]:text-emerald-600"
          data-testid="login-registered-success"
        >
          <CheckCircle2 className="h-4 w-4" aria-hidden />
          <AlertDescription>{t("login.registered_success")}</AlertDescription>
        </Alert>
      ) : null}

      {apiError ? (
        <Alert variant="destructive" data-testid="login-error">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <AlertDescription>{apiError}</AlertDescription>
        </Alert>
      ) : null}

      <Form {...form}>
        <form
          noValidate
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-4"
          data-testid="login-form"
        >
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("login.email_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="email"
                    data-testid="login-email"
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
                <FormLabel>{t("login.password_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    autoComplete="current-password"
                    data-testid="login-password"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <div className="flex items-center justify-end">
            <Link
              to="/forgot-password"
              className="text-xs font-medium text-primary hover:underline"
              data-testid="login-forgot-link"
            >
              {t("login.forgot_link")}
            </Link>
          </div>
          {/* BUGHUNTER-GOLDEN(p2-a11y-missing-alt): alt 속성 없는 이미지 → axe image-alt 위반 */}
          <img src="/vite.svg" width={20} height={20} />
          <Button
            type="submit"
            className="w-full"
            disabled={submitting}
            data-testid="login-submit"
          >
            {t("login.submit")}
          </Button>
        </form>
      </Form>

      <div
        className="relative flex items-center justify-center"
        data-testid="login-oauth-divider"
      >
        <span className="w-full border-t" />
        <span className="absolute bg-card px-2 text-xs uppercase tracking-wider text-muted-foreground">
          {t("login.or_continue_with")}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => startOAuth("github")}
          data-testid="login-oauth-github"
        >
          <ProviderIcon provider="github" />
          <span>{t("oauth.github")}</span>
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => startOAuth("google")}
          data-testid="login-oauth-google"
        >
          <ProviderIcon provider="google" />
          <span>{t("oauth.google")}</span>
        </Button>
      </div>
    </AuthLayout>
  );
}
