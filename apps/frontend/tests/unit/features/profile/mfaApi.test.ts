/**
 * The step-up calls must not be replayed by the 401 interceptor.
 *
 * A 401 from the step-up means the proof was wrong, not that the access token
 * is stale, and the interceptor cannot tell those apart: it refreshes and
 * replays. Three things follow from letting it. One wrong password becomes
 * two attempts against the backend, and the body carrying that password is
 * sent twice. A refresh rotation fires for nothing. And if the refresh fails,
 * its catch signs the person out, at the moment they were trying to secure
 * their account.
 *
 * Asserted through the real interceptor rather than by reading the config
 * back, because what is being checked is the interaction between two pieces
 * of this repository, and a test that inspected the flag would pass against
 * an interceptor that had stopped honouring it.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  regenerateRecoveryCodes,
  startMfaEnrolment,
} from "@/features/profile/api/mfaApi";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

const originalAdapter = api.defaults.adapter;

/** Every request that reached the transport, in order. */
let seen: string[] = [];

function refuseEverythingWith401(): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig) => {
    seen.push(`${(config.method || "get").toUpperCase()} ${config.url}`);
    const response: AxiosResponse = {
      data: { title: "Confirm It Is You", status: 401 },
      status: 401,
      statusText: "Unauthorized",
      headers: {},
      config,
      request: {},
    };
    const err: Error & {
      response?: AxiosResponse;
      config?: InternalAxiosRequestConfig;
      isAxiosError?: boolean;
    } = new Error("HTTP 401");
    err.response = response;
    err.config = config;
    err.isAxiosError = true;
    throw err;
  };
}

beforeEach(() => {
  seen = [];
  useAuthStore.setState({
    user: null,
    accessToken: "an-access-token",
    status: "authenticated",
    isAuthenticated: true,
  });
  api.defaults.adapter = refuseEverythingWith401();
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

describe("the MFA step-up calls", () => {
  it("does not refresh or replay when enrolment is refused", async () => {
    await expect(startMfaEnrolment({ password: "wrong" })).rejects.toThrow();

    expect(seen).toEqual(["POST /v1/users/me/mfa/enrol"]);
    // Named explicitly: a refresh here rotates the token for nothing, and its
    // failure path signs the person out.
    expect(seen).not.toContain("POST /auth/refresh");
  });

  it("does not refresh or replay when a reissue is refused", async () => {
    await expect(regenerateRecoveryCodes({ code: "000000" })).rejects.toThrow();

    expect(seen).toEqual(["POST /v1/users/me/mfa/recovery-codes"]);
  });

  it("leaves the session alone", async () => {
    await expect(startMfaEnrolment({ password: "wrong" })).rejects.toThrow();

    // The refresh path's catch calls reset(). A wrong password at the step-up
    // must not end the session it is protecting.
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().accessToken).toBe("an-access-token");
  });
});
