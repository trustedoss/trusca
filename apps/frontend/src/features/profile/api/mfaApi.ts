import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

/**
 * Keep the response interceptor out of the step-up.
 *
 * It reads any 401 as a stale access token: it calls `/auth/refresh`, swaps
 * the token and replays the request. A step-up refusal is a different 401,
 * the session is fine and the proof was wrong, and letting it through there
 * does three unwanted things. The request is replayed, so one wrong password
 * is two attempts against the backend and the body carrying that password is
 * sent twice. A refresh rotation fires for nothing. And if the refresh fails,
 * its catch signs the person out, which would happen at the moment they were
 * trying to secure their account.
 */
const SKIP_REFRESH = { _skipAuthRefresh: true } as AxiosRequestConfig & {
  _skipAuthRefresh?: boolean;
};

/** What the setup screen needs to show a QR code and a typed fallback. */
export interface MfaEnrolStart {
  /** Base32, for somebody whose camera will not read the code. */
  secret: string;
  /** The `otpauth://` URI a QR code encodes. */
  provisioning_uri: string;
  /** Proves this enrolment belongs to this session when confirming it. */
  mfa_token: string;
}

export interface RecoveryCodes {
  codes: string[];
}

/**
 * Proof that the person is at the keyboard, not just that a session is open.
 *
 * Either field. The code where the account already has a factor, the password
 * otherwise or when the authenticator is not to hand. Both of these endpoints
 * hand back credentials that outlive a password change and are not touched by
 * revoking sessions, so a session on its own is the wrong thing to gate them
 * on: a stolen one is exactly the case the factor exists to survive.
 */
export interface MfaStepUp {
  password?: string;
  code?: string;
}

/** Store a secret and return it to be scanned. Does not turn the factor on. */
export async function startMfaEnrolment(
  proof: MfaStepUp,
): Promise<MfaEnrolStart> {
  const { data } = await api.post<MfaEnrolStart>(
    "/v1/users/me/mfa/enrol",
    proof,
    SKIP_REFRESH,
  );
  return data;
}

/** Turn the factor on, once a code proves the authenticator works. */
export async function confirmMfaEnrolment(payload: {
  mfa_token: string;
  code: string;
}): Promise<RecoveryCodes> {
  const { data } = await api.post<RecoveryCodes>(
    "/v1/users/me/mfa/enrol/confirm",
    payload,
  );
  return data;
}

/** Replace every unused recovery code with a fresh set. */
export async function regenerateRecoveryCodes(
  proof: MfaStepUp,
): Promise<RecoveryCodes> {
  const { data } = await api.post<RecoveryCodes>(
    "/v1/users/me/mfa/recovery-codes",
    proof,
    SKIP_REFRESH,
  );
  return data;
}
