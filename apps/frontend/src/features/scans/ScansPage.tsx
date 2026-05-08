import { useTranslation } from "react-i18next";

export function ScansPage() {
  const { t } = useTranslation("common");
  return (
    <div className="p-6" data-testid="scans-coming-soon">
      <h1 className="text-lg font-semibold">{t("scans.title")}</h1>
      <p className="mt-2 text-muted-foreground">{t("scans.coming_soon")}</p>
    </div>
  );
}
