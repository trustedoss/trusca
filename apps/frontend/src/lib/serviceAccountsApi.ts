// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Identities for automation, so a credential does not die with a person.
 *
 * An API key stops working when the person who issued it is deactivated. Right
 * for a person's key, wrong for a pipeline's: a nightly build that has run for
 * a year stops the day its author leaves. A service account is an issuer that
 * outlives people.
 *
 * What it has instead of a person is a steward: somebody answerable for it. The
 * steward is never part of authenticating. When they leave, existing keys keep
 * working, and no new key may be issued until somebody takes it over.
 */
import { api } from "@/lib/api";

export interface ServiceAccountOut {
  id: string;
  /** Synthetic and undeliverable. It exists because the audit log prints it. */
  email: string;
  full_name: string | null;
  is_active: boolean;
  /** Null when nobody is answerable: keys still work, new ones are refused. */
  managed_by_user_id: string | null;
  created_at: string;
}

export interface ServiceAccountListOut {
  items: ServiceAccountOut[];
  total: number;
}

export async function listServiceAccounts(
  teamId: string,
): Promise<ServiceAccountListOut> {
  const { data } = await api.get<ServiceAccountListOut>("/v1/service-accounts", {
    params: { team_id: teamId },
  });
  return data;
}

export async function createServiceAccount(payload: {
  team_id: string;
  slug: string;
  display_name: string;
  role?: string;
}): Promise<ServiceAccountOut> {
  const { data } = await api.post<ServiceAccountOut>(
    "/v1/service-accounts",
    payload,
  );
  return data;
}

export async function assignServiceAccountSteward(
  serviceAccountId: string,
  stewardUserId: string,
): Promise<ServiceAccountOut> {
  const { data } = await api.put<ServiceAccountOut>(
    `/v1/service-accounts/${serviceAccountId}/steward`,
    { steward_user_id: stewardUserId },
  );
  return data;
}

/**
 * Stop every key this account holds, in one act.
 *
 * The counterpart to keys no longer dying with a person: there has to be a
 * deliberate way to stop them, and it should not be a hunt through the key
 * list. The account row stays so the audit trail keeps its actor.
 */
export async function deactivateServiceAccount(
  serviceAccountId: string,
): Promise<ServiceAccountOut> {
  const { data } = await api.delete<ServiceAccountOut>(
    `/v1/service-accounts/${serviceAccountId}`,
  );
  return data;
}
