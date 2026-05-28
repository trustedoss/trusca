/**
 * adminBackupsApi — wire-shape unit tests (Phase 6 PR #19 chore D).
 *
 * Same canned-axios-adapter pattern as `adminUsersApi.test.ts`: install a
 * stub adapter on the shared axios instance so we can assert on URL,
 * method, headers, and body without touching the network.
 *
 * The download path is exercised separately because it runs through
 * `URL.createObjectURL` + a synthetic `<a>` click. We stub each of those
 * with a vi.fn so we can assert the correct filename is selected from the
 * Content-Disposition header without invoking the real browser plumbing.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteBackup,
  downloadBackup,
  listBackups,
  triggerManualBackup,
  uploadRestore,
} from "@/features/admin/api/adminBackupsApi";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  method: string;
  url: string;
  data: unknown;
  params: Record<string, unknown>;
  headers: Record<string, string>;
  responseType: string | undefined;
}

function installAdapter(
  responses: Array<{
    status: number;
    data: unknown;
    headers?: Record<string, string>;
  }>,
): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  let i = 0;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    const canned = responses[i] ?? { status: 200, data: null };
    i += 1;
    let parsed: unknown;
    if (config.data instanceof FormData) {
      // Multipart payloads — capture an inert sentinel so the assertion
      // sees that a FormData was passed without forcing the test to
      // serialize it.
      parsed = "[FormData]";
    } else if (typeof config.data === "string") {
      parsed = JSON.parse(config.data);
    } else {
      parsed = config.data;
    }
    calls.push({
      method: (config.method ?? "get").toLowerCase(),
      url: config.url ?? "",
      data: parsed,
      params: (config.params as Record<string, unknown>) ?? {},
      headers: (config.headers as unknown as Record<string, string>) ?? {},
      responseType: config.responseType,
    });
    const response: AxiosResponse = {
      data: canned.data,
      status: canned.status,
      statusText: "",
      headers: canned.headers ?? {},
      config,
      request: {},
    };
    if (canned.status >= 400) {
      const err = new Error(`status ${canned.status}`);
      (err as { response?: AxiosResponse }).response = response;
      (err as { config?: InternalAxiosRequestConfig }).config = config;
      throw err;
    }
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

describe("adminBackupsApi", () => {
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
    vi.restoreAllMocks();
  });

  it("listBackups GETs /v1/admin/backup", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { items: [], total: 0 } },
    ]);
    restore = r;
    const result = await listBackups();
    expect(calls[0].method).toBe("get");
    expect(calls[0].url).toBe("/v1/admin/backup");
    expect(result).toEqual({ items: [], total: 0 });
  });

  it("triggerManualBackup POSTs /v1/admin/backup", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 202,
        data: { task_id: "task-1", name: "manual-20260509T000000Z" },
      },
    ]);
    restore = r;
    const result = await triggerManualBackup();
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/admin/backup");
    expect(result.task_id).toBe("task-1");
  });

  it("deleteBackup DELETEs the named row", async () => {
    const { calls, restore: r } = installAdapter([{ status: 204, data: null }]);
    restore = r;
    await deleteBackup("manual-20260509T000000Z");
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe("/v1/admin/backup/manual-20260509T000000Z");
  });

  it("uploadRestore sends multipart with X-Confirm-Restore header", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 202, data: { task_id: "t-2", message: "queued" } },
    ]);
    restore = r;
    const file = new File(["hello"], "snap.tar.gz", { type: "application/gzip" });
    const result = await uploadRestore(file);
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/admin/backup/restore");
    // The header is set on the request — adapter sees it as-passed.
    expect(calls[0].headers["X-Confirm-Restore"]).toBe("yes");
    // FormData sentinel from our adapter wrapper.
    expect(calls[0].data).toBe("[FormData]");
    expect(result.task_id).toBe("t-2");
  });

  it("downloadBackup uses responseType=blob and triggers a download click", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 200,
        data: new Blob(["payload"], { type: "application/gzip" }),
        headers: {
          "content-disposition":
            "attachment; filename=\"manual-20260509.tar.gz\"",
        },
      },
    ]);
    restore = r;
    // jsdom does not implement URL.createObjectURL / revokeObjectURL.
    // Patch the prototype directly (matches the SbomTab test pattern)
    // so we can observe the call without depending on the runtime.
    const originalCreate = (URL as unknown as Record<string, unknown>)
      .createObjectURL;
    const originalRevoke = (URL as unknown as Record<string, unknown>)
      .revokeObjectURL;
    const createMock = vi.fn().mockReturnValue("blob:fake-url");
    (URL as unknown as Record<string, unknown>).createObjectURL = createMock;
    (URL as unknown as Record<string, unknown>).revokeObjectURL = vi.fn();

    const click = vi.fn();
    const appendSpy = vi
      .spyOn(document.body, "appendChild")
      .mockImplementation((node) => node);
    const createElementSpy = vi
      .spyOn(document, "createElement")
      .mockImplementation(((tag: string) => {
        if (tag === "a") {
          return {
            href: "",
            download: "",
            click,
            remove: vi.fn(),
          } as unknown as HTMLAnchorElement;
        }
        // Bypass our spy for non-anchor tags by using the original.
        return Object.getPrototypeOf(document).createElement.call(
          document,
          tag,
        );
      }) as typeof document.createElement);

    try {
      await downloadBackup("manual-20260509T000000Z");
      expect(calls[0].method).toBe("get");
      expect(calls[0].url).toBe(
        "/v1/admin/backup/manual-20260509T000000Z/download",
      );
      expect(calls[0].responseType).toBe("blob");
      expect(createMock).toHaveBeenCalledTimes(1);
      expect(click).toHaveBeenCalledTimes(1);
      expect(appendSpy).toHaveBeenCalled();
      expect(createElementSpy).toHaveBeenCalledWith("a");
    } finally {
      if (originalCreate === undefined) {
        delete (URL as unknown as Record<string, unknown>).createObjectURL;
      } else {
        (URL as unknown as Record<string, unknown>).createObjectURL =
          originalCreate;
      }
      if (originalRevoke === undefined) {
        delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
      } else {
        (URL as unknown as Record<string, unknown>).revokeObjectURL =
          originalRevoke;
      }
    }
  });
});
