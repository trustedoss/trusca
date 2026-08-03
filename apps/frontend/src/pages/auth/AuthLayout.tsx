// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { BrandLockup } from "@/components/BrandLockup";
import { DemoBanner } from "@/components/DemoBanner";
import { LanguageToggle } from "@/components/LanguageToggle";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface AuthLayoutProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  testId?: string;
}

/** The three facts the panel stands on. Each is checkable in the product,
 *  not a claim about it — what it runs on, where the data comes from, what
 *  it exports. A gateway that promises a feeling instead is the kind of
 *  page a reader learns to skip. */
const TRUST_KEYS = ["gateway.trust.hosting", "gateway.trust.sources", "gateway.trust.formats"];

/**
 * Split gateway shared by Login / Register / Forgot / Reset.
 *
 * The brand panel is the same ink surface as the post-login global bar, and
 * that is the point: the first screen and the shell the user lands in are made
 * of one material, so signing in reads as entering the same product rather
 * than crossing into a different one. It reuses `--topbar-*` for exactly that
 * reason — the tokens whose contrast on ink is already measured.
 *
 * Below `lg` the panel is not shown. A phone has no room for a brand column
 * beside a form, and stacking them would push the form under a screenful of
 * marketing — the compact lockup above the card already carries the identity.
 */
export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
  testId,
}: AuthLayoutProps) {
  const { t } = useTranslation("auth");

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* B5 — surface read-only demo mode to unauthenticated visitors too, not
          just inside the post-login shell. Self-gated: renders nothing on a
          normal deploy. */}
      <DemoBanner />
      <div className="flex min-h-screen">
        <aside
          className="hidden w-[42%] max-w-xl flex-col justify-between bg-topbar px-10 py-12 lg:flex"
          data-testid="auth-brand-panel"
        >
          <BrandLockup onInk />
          <div className="space-y-8">
            <p className="max-w-md text-2xl font-semibold leading-snug tracking-tight text-topbar-foreground">
              {t("gateway.pitch")}
            </p>
            <ul className="space-y-3">
              {TRUST_KEYS.map((key) => (
                <li key={key} className="flex items-start gap-3 text-sm">
                  <Check
                    aria-hidden
                    className="mt-0.5 h-4 w-4 shrink-0 text-brand-on-ink"
                  />
                  <span className="text-topbar-muted-foreground">{t(key)}</span>
                </li>
              ))}
            </ul>
          </div>
          <p className="text-xs text-topbar-muted-foreground">
            {t("gateway.licence")}
          </p>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header
            className="flex items-center justify-end border-b px-6"
            style={{ height: "var(--layout-header)" }}
          >
            <LanguageToggle />
          </header>
          <main
            className="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center gap-6 px-6 py-12"
            data-testid={testId}
          >
            {/* The lockup repeats here below `lg`, where the panel is absent —
                and only there. Two lockups on one screen is a brand shouting
                at someone already inside the door. */}
            <div className="lg:hidden">
              <BrandLockup />
            </div>
            <Card className="w-full">
              <CardHeader>
                <CardTitle>{title}</CardTitle>
                {subtitle ? <CardDescription>{subtitle}</CardDescription> : null}
              </CardHeader>
              <CardContent className="space-y-4">{children}</CardContent>
            </Card>
            {footer ? (
              <div className="text-center text-sm text-muted-foreground">
                {footer}
              </div>
            ) : null}
          </main>
        </div>
      </div>
    </div>
  );
}
