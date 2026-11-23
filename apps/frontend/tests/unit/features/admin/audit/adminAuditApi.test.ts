// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * adminAuditApi: the query string sent for the Action filter.
 *
 * The API matches `action` as a substring unless `action_match=exact` is
 * sent, so `archive` would also return `unarchive` rows. The Action select
 * names one action, so both the search and the CSV export must ask for an
 * exact match whenever an action is set, and send nothing when it is not.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadAdminAuditCsv,
  searchAdminAudit,
} from "@/features/admin/audit/api/adminAuditApi";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  url: string;
  params: Record<string, unknown>;
}

function installAdapter(data: unknown): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    calls.push({
      url: config.url ?? "",
      params: (config.params as Record<string, unknown>) ?? {},
    });
    const response: AxiosResponse = {
      data,
      status: 200,
      statusText: "",
      headers: {},
      config,
      request: {},
    };
    return response;
  };
  api.defaults.adapter = adapter;
  return {
    calls,
    restore: () => {
      api.defaults.adapter = original;
    },
  };
}

const EMPTY_PAGE = { items: [], total: 0, page: 1, page_size: 50, has_more: false };

describe("adminAuditApi action_match", () => {
  let restore: () => void = () => {};
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: "tok-admin",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    restore();
    useAuthStore.getState().reset();
  });

  it("asks the search for an exact match when an action is chosen", async () => {
    const { calls, restore: r } = installAdapter(EMPTY_PAGE);
    restore = r;
    await searchAdminAudit({ action: "archive" });
    expect(calls[0].url).toBe("/v1/admin/audit");
    expect(calls[0].params.action).toBe("archive");
    expect(calls[0].params.action_match).toBe("exact");
  });

  it("sends no action_match without an action", async () => {
    const { calls, restore: r } = installAdapter(EMPTY_PAGE);
    restore = r;
    await searchAdminAudit({ action: null, target_table: "projects" });
    expect(calls[0].params.target_table).toBe("projects");
    expect(calls[0].params.action).toBeUndefined();
    expect(calls[0].params.action_match).toBeUndefined();
  });

  it("asks the CSV export for the same exact match", async () => {
    // jsdom exposes URL but not createObjectURL; patch it for this test only.
    const target = URL as unknown as Record<string, unknown>;
    const original = target.createObjectURL;
    target.createObjectURL = vi.fn(() => "blob:audit");
    const { calls, restore: r } = installAdapter(new Blob(["created_at\n"]));
    restore = r;
    try {
      await downloadAdminAuditCsv({ action: "archive" });
    } finally {
      if (original === undefined) delete target.createObjectURL;
      else target.createObjectURL = original;
    }
    expect(calls[0].url).toBe("/v1/admin/audit/export.csv");
    expect(calls[0].params.action).toBe("archive");
    expect(calls[0].params.action_match).toBe("exact");
  });
});
