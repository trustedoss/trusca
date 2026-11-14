// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * One icon per destination, shared by the sidebar and the ⌘K palette so a
 * place looks the same wherever it is listed.
 */
import {
  ArchiveIcon,
  BellIcon,
  ChecklistIcon,
  DatabaseIcon,
  FileDirectoryIcon,
  GraphIcon,
  InboxIcon,
  InfoIcon,
  KeyIcon,
  LawIcon,
  OrganizationIcon,
  PackageIcon,
  PeopleIcon,
  PulseIcon,
  TasklistIcon,
  WorkflowIcon,
  NoteIcon,
  type Icon,
} from "@primer/octicons-react";

export const NAV_ICON: Record<string, Icon> = {
  "/": GraphIcon,
  "/components": PackageIcon,
  "/projects": FileDirectoryIcon,
  "/groups": OrganizationIcon,
  "/scans": WorkflowIcon,
  "/approvals": ChecklistIcon,
  "/intake": InboxIcon,
  "/policies": LawIcon,
  "/integrations": KeyIcon,
  "/notifications": BellIcon,
  "/about": InfoIcon,
  "/admin/users": PeopleIcon,
  "/admin/teams": OrganizationIcon,
  "/admin/scans": TasklistIcon,
  "/admin/disk": DatabaseIcon,
  "/admin/audit": NoteIcon,
  "/admin/backup": ArchiveIcon,
  "/admin/health": PulseIcon,
};
