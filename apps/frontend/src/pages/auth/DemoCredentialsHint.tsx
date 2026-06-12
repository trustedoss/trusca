/**
 * DemoCredentialsHint — login-page helper for the public read-only demo.
 *
 * Shown ONLY when `useDemoMode().demoReadOnly` is true (a normal deploy renders
 * nothing). It points first-time visitors at a seeded demo account so they can
 * sign in and browse without guessing credentials. A "fill" button drops the
 * email + password straight into the login form for one-click discovery.
 *
 * The representative account `frontend-admin@demo.trustedoss.dev` is used
 * because its team carries the richest CVE / license data. The shared password
 * is the same across every seeded account (`seed_demo`). The email domain is
 * always `@demo.trustedoss.dev` regardless of the deploy hostname, so it is
 * safe to print verbatim.
 *
 * Design: a single, restrained info box built from the shared `Alert` primitive
 * (default variant + sky tones) so it reads as guidance, not an error. Color is
 * paired with an icon (a11y — color is never the only signal).
 */
import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** Representative seeded demo account (richest CVE / license data). */
export const DEMO_LOGIN_EMAIL = "frontend-admin@demo.trustedoss.dev";
/** Shared password for every seeded demo account (apps/backend seed_demo). */
export const DEMO_LOGIN_PASSWORD = "DemoTest2026!";

interface DemoCredentialsHintProps {
  /** Drops the demo email + password into the login form fields. */
  onFill: (credentials: { email: string; password: string }) => void;
}

export function DemoCredentialsHint({ onFill }: DemoCredentialsHintProps) {
  const { t } = useTranslation("auth");

  return (
    <Alert
      variant="default"
      data-testid="login-demo-hint"
      className="border-sky-200 bg-sky-50 text-sky-900"
    >
      <Info className="h-4 w-4 text-sky-600" aria-hidden />
      <AlertDescription className="space-y-2">
        <p className="font-medium">{t("login.demo.title")}</p>
        <p className="text-sky-800">{t("login.demo.detail")}</p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs text-sky-900">
          <dt className="text-sky-700">{t("login.demo.email_label")}</dt>
          <dd data-testid="login-demo-email">{DEMO_LOGIN_EMAIL}</dd>
          <dt className="text-sky-700">{t("login.demo.password_label")}</dt>
          <dd data-testid="login-demo-password">{DEMO_LOGIN_PASSWORD}</dd>
        </dl>
        <p className="text-xs text-sky-700">{t("login.demo.password_note")}</p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="border-sky-300 bg-white/60 text-sky-900 hover:bg-white"
          data-testid="login-demo-fill"
          onClick={() =>
            onFill({ email: DEMO_LOGIN_EMAIL, password: DEMO_LOGIN_PASSWORD })
          }
        >
          {t("login.demo.fill_button")}
        </Button>
      </AlertDescription>
    </Alert>
  );
}
