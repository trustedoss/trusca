// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * DemoCredentialsHint — login-page helper for the public read-only demo.
 *
 * Shown ONLY when `useDemoMode().demoReadOnly` is true (a normal deploy renders
 * nothing). It points first-time visitors at a seeded demo account so they can
 * sign in and browse without guessing credentials. A "fill" button drops the
 * email + password straight into the login form for one-click discovery.
 *
 * The account is `explore@demo.trustedoss.dev`, seeded into the Frontend team
 * because that team carries the richest CVE / license data.
 *
 * It is not one of the `<team>-admin@` accounts, and the reason is this screen.
 * A visitor sees one address and no other context, so `frontend-admin@` read as
 * "the account that shows you the frontend" — as though the portal had a
 * frontend view and a backend view and you chose one at sign-in. It does not.
 * The account decides which team's data you see, and the seeded teams are named
 * after disciplines. `explore@` promises nothing about what you will see.
 *
 * The shared password is the same across every seeded account (`seed_demo`).
 * The email domain is always `@demo.trustedoss.dev` regardless of the deploy
 * hostname, so it is safe to print verbatim.
 *
 * Design: a single, restrained info box built from the shared `Alert` primitive
 * (default variant + sky tones) so it reads as guidance, not an error. Color is
 * paired with an icon (a11y — color is never the only signal).
 */
import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/**
 * Seeded demo account offered to first-time visitors. Must match
 * `_DEMO_EXPLORE_EMAIL` in apps/backend/scripts/seed_demo.py — a visitor who
 * clicks "fill demo credentials" and cannot sign in has no way to tell which
 * side drifted.
 */
export const DEMO_LOGIN_EMAIL = "explore@demo.trustedoss.dev";
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
        <p className="text-xs text-sky-700">{t("login.demo.scan_note")}</p>
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
