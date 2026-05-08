import { useTranslation } from "react-i18next";

export function ApprovalsPage() {
  const { t } = useTranslation("common");
  return (
    <div className="p-6" data-testid="approvals-coming-soon">
      <h1 className="text-lg font-semibold">{t("nav.approvals")}</h1>
      <p className="mt-2 text-muted-foreground">{t("approvals.coming_soon")}</p>
    </div>
  );
}
