// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Approved-but-unexecuted anonymisation requests (ER32).
 *
 *   GET /v1/user-anonymisation/awaiting-execution → AwaitingExecutionList
 *
 * Why this has a screen at all: approval happens in the product and the
 * erasure itself runs as an operator command on a server, because the
 * database role that can reach inside `audit_logs` is deliberately not the
 * one the application holds. Between the two there is a state where two
 * people have agreed, nothing is broken, and a person is still waiting.
 * Erasure requests usually carry a statutory deadline, so that gap needs to
 * be visible somewhere an operator already looks.
 *
 * No subject email or name crosses this boundary, only ids. The one request
 * whose purpose is to remove an address should not be putting it into API
 * logs and browser history on the way.
 */
import { api } from "@/lib/api";

export interface AwaitingExecutionItem {
  request_id: string;
  subject_user_id: string;
  /**
   * Who asked and who agreed. Shown because the operator is about to do
   * something irreversible on the strength of a database row, and a row is
   * only a row: anything that can write to that table can produce one saying
   * "approved". These are the two people they can go and ask.
   */
  requested_by_user_id: string;
  approved_by_user_id: string | null;
  approved_at: string;
  /** Computed server-side so the count does not shift with the viewer's timezone. */
  waiting_days: number;
}

export interface AwaitingExecutionList {
  items: AwaitingExecutionItem[];
  count: number;
}

export async function getAwaitingExecution(): Promise<AwaitingExecutionList> {
  const { data } = await api.get<AwaitingExecutionList>(
    "/v1/user-anonymisation/awaiting-execution",
  );
  return data;
}
