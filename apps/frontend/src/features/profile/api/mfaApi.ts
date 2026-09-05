import { api } from "@/lib/api";

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

/** Store a secret and return it to be scanned. Does not turn the factor on. */
export async function startMfaEnrolment(): Promise<MfaEnrolStart> {
  const { data } = await api.post<MfaEnrolStart>("/v1/users/me/mfa/enrol");
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
export async function regenerateRecoveryCodes(): Promise<RecoveryCodes> {
  const { data } = await api.post<RecoveryCodes>(
    "/v1/users/me/mfa/recovery-codes",
  );
  return data;
}
