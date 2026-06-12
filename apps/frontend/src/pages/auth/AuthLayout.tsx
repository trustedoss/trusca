import type { ReactNode } from "react";

import { BrandMark } from "@/components/BrandMark";
import { BrandWordmark } from "@/components/BrandWordmark";
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

/**
 * Centered single-card layout shared by Login / Register / ForgotPassword.
 *
 * Auth pages are full-page (no sidebar) per CLAUDE.md "디자인 시스템" — the
 * 224 px sidebar applies to the post-login app shell, not the gateway.
 */
export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
  testId,
}: AuthLayoutProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* B5 — surface read-only demo mode to unauthenticated visitors too, not
          just inside the post-login shell. Self-gated: renders nothing on a
          normal deploy. */}
      <DemoBanner />
      <header
        className="flex items-center justify-between border-b px-6"
        style={{ height: "var(--layout-header)" }}
      >
        <span className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <BrandMark size={20} />
          <BrandWordmark />
        </span>
        <LanguageToggle />
      </header>
      <main
        className="mx-auto flex w-full max-w-md flex-col gap-6 px-6 py-12"
        data-testid={testId}
      >
        <Card>
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
  );
}
