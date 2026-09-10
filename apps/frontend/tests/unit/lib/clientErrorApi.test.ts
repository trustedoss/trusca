/**
 * clientErrorApi - unit tests (#423).
 *
 * Stubs the axios adapter on the shared `api` instance (mirrors
 * tests/unit/lib/apiKeysApi.test.ts) so this proves the actual request
 * shape `POST /v1/client-errors` receives, not just that some function was
 * called.
 */
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { reportClientError } from "@/lib/clientErrorApi";

interface Recorded {
  method: string;
  url: string;
  data: unknown;
}

function installAdapter(status: number): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    calls.push({
      method: (config.method ?? "get").toLowerCase(),
      url: config.url ?? "",
      data: config.data ? JSON.parse(config.data as string) : undefined,
    });
    const response: AxiosResponse = {
      data: null,
      status,
      statusText: "",
      headers: {},
      config,
      request: {},
    };
    if (status >= 400) {
      const err = new Error(`status ${status}`);
      (err as { response?: AxiosResponse }).response = response;
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

// axios in a browser-like adapter reads the request URL off the live
// document; jsdom gives every test the same default location unless a test
// overrides it, so this pins one explicitly rather than depending on that.
function setLocation(href: string): void {
  Object.defineProperty(window, "location", {
    value: new URL(href),
    writable: true,
  });
}

describe("reportClientError", () => {
  let restore: () => void = () => {};
  afterEach(() => {
    restore();
  });

  it("posts the report with the current URL", async () => {
    setLocation("https://portal.example/projects/1?reset_token=abc");
    const { calls, restore: r } = installAdapter(204);
    restore = r;

    reportClientError({ message: "boom", stack: "Error: boom\n at f", componentStack: "\n in F" });
    // Fire-and-forget: give the microtask queue a turn to let the request land.
    await vi.waitFor(() => expect(calls).toHaveLength(1));

    expect(calls[0]!.method).toBe("post");
    expect(calls[0]!.url).toBe("/v1/client-errors");
    expect(calls[0]!.data).toEqual({
      message: "boom",
      stack: "Error: boom\n at f",
      component_stack: "\n in F",
      url: "https://portal.example/projects/1?reset_token=abc",
    });
  });

  it("truncates fields that exceed the backend's caps rather than letting a 422 reach nobody", async () => {
    setLocation("https://portal.example/");
    const { calls, restore: r } = installAdapter(204);
    restore = r;

    reportClientError({
      message: "x".repeat(2500),
      stack: "y".repeat(9000),
      componentStack: "z".repeat(9000),
    });
    await vi.waitFor(() => expect(calls).toHaveLength(1));

    const body = calls[0]!.data as Record<string, string>;
    expect(body.message).toHaveLength(2000);
    expect(body.stack).toHaveLength(8000);
    expect(body.component_stack).toHaveLength(8000);
  });

  it("never throws when the backend call fails", async () => {
    setLocation("https://portal.example/");
    const { restore: r } = installAdapter(500);
    restore = r;

    expect(() => reportClientError({ message: "boom" })).not.toThrow();
    // Let the rejected request settle so it does not surface as an
    // unhandled rejection in a later test.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
});
