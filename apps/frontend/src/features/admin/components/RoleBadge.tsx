/**
 * RoleBadge — neutral-palette badge that pairs the role label with an icon
 * so color is never the sole signal (CLAUDE.md "디자인 시스템" §accessibility).
 *
 * The admin domain has no severity — palette is neutral / blue / slate
 * instead of the risk colors.
 */
import { ShieldCheck, Users as UsersIcon, User } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { UserRole } from "@/features/admin/api/adminUsersApi";
import { cn } from "@/lib/utils";

interface RoleBadgeProps {
  role: UserRole;
  className?: string;
}

export function RoleBadge({ role, className }: RoleBadgeProps) {
  const { t } = useTranslation("admin");
  const Icon = role === "super_admin" ? ShieldCheck : role === "team_admin" ? UsersIcon : User;
  const variantClass =
    role === "super_admin"
      ? "bg-primary/10 text-primary border-transparent"
      : role === "team_admin"
        ? "bg-risk-low/10 text-risk-low border-transparent"
        : "bg-muted text-muted-foreground border-transparent";

  return (
    <Badge
      variant="outline"
      className={cn(variantClass, className)}
      data-testid="role-badge"
      data-role={role}
    >
      <Icon className="h-3 w-3" aria-hidden />
      <span>{t(`admin.users.role.${role}`)}</span>
    </Badge>
  );
}
