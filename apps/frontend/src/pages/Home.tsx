import { Trans, useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";

import { LanguageToggle } from "@/components/LanguageToggle";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/authStore";

const RISK_LEVELS = ["critical", "high", "medium", "low", "info"] as const;

export function Home() {
  const { t } = useTranslation();
  const apiBase = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);
  const isSuperAdmin = useAuthStore(
    (s) => s.user?.isSuperuser === true || s.user?.role === "super_admin",
  );

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header
        className="flex items-center justify-between border-b px-6"
        style={{ height: "var(--layout-header)" }}
      >
        <div className="flex items-baseline gap-3">
          <span className="text-sm font-semibold tracking-tight">
            {t("app.name")}
          </span>
          <span className="text-xs text-muted-foreground">
            {t("app.version")}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {isSuperAdmin ? (
            <Button
              asChild
              variant="outline"
              size="sm"
              data-testid="home-admin-link"
            >
              <Link to="/admin">
                {t("nav.admin.entry", { ns: "admin" })}
              </Link>
            </Button>
          ) : null}
          <LanguageToggle />
          {/* Stub trigger so e2e (1.9 scenario) can exercise expectLoggedOut.
              Phase 2 replaces this with the real header/sidebar. */}
          <Button
            variant="outline"
            size="sm"
            onClick={handleLogout}
            data-testid="logout-button"
          >
            {t("auth.logout", { defaultValue: "Sign out" })}
          </Button>
        </div>
      </header>

      <main
        className="mx-auto grid max-w-3xl gap-6 px-6 py-12"
        data-testid="home-main"
      >
        <Card>
          <CardHeader>
            <CardTitle data-testid="home-title">{t("home.title")}</CardTitle>
            <CardDescription>{t("home.subtitle")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <p className="text-muted-foreground">{t("home.stack")}</p>
            <p>
              <span className="text-muted-foreground">
                {t("home.api_label")}:{" "}
              </span>
              <code className="font-mono text-xs">{apiBase}/health</code>
            </p>
            <Button data-testid="home-cta">{t("home.primary_cta")}</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              <Trans i18nKey="app.tagline" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul
              className="grid grid-cols-5 gap-2 text-xs"
              data-testid="risk-legend"
            >
              {RISK_LEVELS.map((level) => (
                <li
                  key={level}
                  className="flex items-center gap-2 rounded-md border px-2 py-1"
                  data-risk={level}
                >
                  <span
                    aria-hidden
                    className="inline-block h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: `var(--risk-${level})` }}
                  />
                  <span>{t(`risk.${level}`)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
