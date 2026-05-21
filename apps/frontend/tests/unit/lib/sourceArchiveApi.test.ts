/**
 * sourceArchiveApi — unit tests (feat/zip-upload).
 *
 * Stubs the axios adapter on the shared `api` instance so the wrapper hits the
 * real request/response interceptors (Bearer header + ProblemError mapping).
 * Covers the happy path, multipart field name, progress callback, and the
 * status → i18n-key / token mappers for every backend failure mode.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import {
  uploadErrorMessageKey,
  uploadErrorToken,
  uploadSourceArchive,
} from "@/lib/sourceArchiveApi";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  method: string;
  url: string;
  data: unknown;
}

function installAdapter(
  responses: Array<{ status: number; data: unknown }>,
): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  let i = 0;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    const canned = responses[i] ?? { status: 200, data: null };
    i += 1;
    calls.push({
      method: (config.method ?? "get").toLowerCase(),
      url: config.url ?? "",
      data: config.data,
    });
    // Drive the progress callback like a real upload would.
    config.onUploadProgress?.({
      loaded: 50,
      total: 100,
      bytes: 50,
      lengthComputable: true,
    } as never);
    const response: AxiosResponse = {
      data: canned.data,
      status: canned.status,
      statusText: "",
      headers: {},
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

function zipFile(name = "src.zip"): File {
  return new File([new Uint8Array(8)], name, { type: "application/zip" });
}

describe("uploadSourceArchive", () => {
  let restore: () => void = () => {};
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: "tok-upload",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    restore();
    useAuthStore.getState().reset();
  });

  it("POSTs multipart to the source-archive route and returns archive_id", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 201, data: { archive_id: "arch-1" } },
    ]);
    restore = r;
    const result = await uploadSourceArchive("proj-1", zipFile());
    expect(result.archive_id).toBe("arch-1");
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/projects/proj-1/source-archive");
    expect(calls[0].data).toBeInstanceOf(FormData);
    const form = calls[0].data as FormData;
    expect(form.get("upload")).toBeInstanceOf(File);
  });

  it("forwards upload progress as a 0–100 percent", async () => {
    const { restore: r } = installAdapter([
      { status: 201, data: { archive_id: "arch-2" } },
    ]);
    restore = r;
    const seen: number[] = [];
    await uploadSourceArchive("proj-1", zipFile(), {
      onProgress: (p) => seen.push(p),
    });
    expect(seen).toContain(50);
  });

  it("maps a 413 to a ProblemError the caller can key on", async () => {
    const { restore: r } = installAdapter([
      {
        status: 413,
        data: { type: "about:blank", title: "Too Large", status: 413, detail: "x" },
      },
    ]);
    restore = r;
    await expect(uploadSourceArchive("proj-1", zipFile())).rejects.toBeInstanceOf(
      ProblemError,
    );
  });
});

describe("uploadErrorMessageKey / uploadErrorToken", () => {
  function problem(status: number): ProblemError {
    return new ProblemError("x", {
      status,
      title: "t",
      detail: "d",
      problem: { type: "about:blank", title: "t", status, detail: "d" },
    });
  }

  it.each([
    [413, "upload.errors.too_large", "too_large"],
    [415, "upload.errors.not_a_zip", "not_a_zip"],
    [507, "upload.errors.quota_exceeded", "quota_exceeded"],
    [404, "upload.errors.not_found", "not_found"],
    [400, "upload.errors.bad_request", "bad_request"],
    [0, "upload.errors.network", "network"],
    [500, "upload.errors.unknown", "unknown"],
  ])("maps status %s to key/token", (status, key, token) => {
    expect(uploadErrorMessageKey(problem(status))).toBe(key);
    expect(uploadErrorToken(problem(status))).toBe(token);
  });

  it("falls back to unknown for non-ProblemError values", () => {
    expect(uploadErrorMessageKey(new Error("nope"))).toBe("upload.errors.unknown");
    expect(uploadErrorToken("string")).toBe("unknown");
  });
});
