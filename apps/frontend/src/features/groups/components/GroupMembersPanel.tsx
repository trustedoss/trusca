// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useGroupMembers } from "@/features/groups/api/useGroupMembers";
import type {
  GroupInheritedMemberEntry,
  GroupMemberEntry,
} from "@/features/groups/api/groupsApi";

/**
 * GroupMembersPanel — group-hierarchy Phase 4 PR 4-B.
 *
 * Direct memberships and cascade-inherited ones are two visually separate
 * lists (harness: `group-members-direct-row` vs. `group-members-inherited-
 * row`), not one list with a "source" column — the harness's own docstring
 * on `GroupInheritedMemberEntry` puts it directly: the split is what lets a
 * reader tell WHERE access came from, not just that it exists. An inherited
 * row always carries `data-source-group-name` (which ancestor granted it).
 *
 * `active` gates the query itself (see `useGroupMembers`), so this panel is
 * safe to keep mounted (inside a `TabsContent` that Radix un-displays rather
 * than unmounts) without firing a developer-role request the reader never
 * asked to see.
 */
export function GroupMembersPanel({
  groupId,
  active,
}: {
  groupId: string;
  active: boolean;
}) {
  const { t } = useTranslation("groups");
  const membersQuery = useGroupMembers(groupId, active);
  const isBusy = membersQuery.isFetching;
  const direct = membersQuery.data?.direct ?? [];
  const inherited = membersQuery.data?.inherited ?? [];

  return (
    <div
      className="flex flex-col gap-6 px-6 py-4"
      data-testid="group-members-panel"
      aria-busy={isBusy}
    >
      {membersQuery.isError ? (
        <Alert variant="destructive" data-testid="group-members-error">
          <AlertDescription>{t("detail.members.errors.load_failed")}</AlertDescription>
        </Alert>
      ) : null}

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("detail.members.direct_heading")}
        </h3>
        {membersQuery.isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : direct.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="group-members-direct-empty">
            {t("detail.members.direct_empty")}
          </p>
        ) : (
          <ul className="divide-y rounded-md border">
            {direct.map((member) => (
              <DirectMemberRow key={member.user_id} member={member} />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("detail.members.inherited_heading")}
        </h3>
        {membersQuery.isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : inherited.length === 0 ? (
          <p
            className="text-sm text-muted-foreground"
            data-testid="group-members-inherited-empty-state"
          >
            {t("detail.members.inherited_empty")}
          </p>
        ) : (
          <ul className="divide-y rounded-md border">
            {inherited.map((member) => (
              <InheritedMemberRow key={`${member.user_id}-${member.source_group_id}`} member={member} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DirectMemberRow({ member }: { member: GroupMemberEntry }) {
  return (
    <li
      data-testid="group-members-direct-row"
      data-email={member.email}
      data-role={member.role}
      className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
    >
      <div className="min-w-0 truncate">
        <span className="font-medium">{member.full_name ?? member.email}</span>
        {member.full_name ? (
          <span className="ml-2 truncate text-xs text-muted-foreground">
            {member.email}
          </span>
        ) : null}
      </div>
      <Badge variant="outline" className="shrink-0 capitalize">
        {member.role}
      </Badge>
    </li>
  );
}

function InheritedMemberRow({ member }: { member: GroupInheritedMemberEntry }) {
  const { t } = useTranslation("groups");
  return (
    <li
      data-testid="group-members-inherited-row"
      data-email={member.email}
      data-role={member.role}
      data-source-group-id={member.source_group_id}
      data-source-group-name={member.source_group_name}
      className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
    >
      <div className="min-w-0 truncate">
        <span className="font-medium">{member.full_name ?? member.email}</span>
        {member.full_name ? (
          <span className="ml-2 truncate text-xs text-muted-foreground">
            {member.email}
          </span>
        ) : null}
        <span className="ml-2 truncate text-xs text-muted-foreground">
          {t("detail.members.inherited_source", { group: member.source_group_name })}
        </span>
      </div>
      <Badge variant="outline" className="shrink-0 capitalize">
        {member.role}
      </Badge>
    </li>
  );
}
