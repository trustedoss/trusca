// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Obligation vocabulary that is not part of the wire surface.
 *
 * It sits here rather than in `obligationsApi` because that module is mocked
 * wholesale by every test that touches the obligations tab, and a mock only
 * returns what it declares. A value export added there is missing from a dozen
 * mocks at once, and the fix that spreads the real module back in un-stubs the
 * request functions, which passes locally and makes real calls in CI. A module
 * nobody mocks avoids the choice.
 */

/**
 * How far along one project is with one obligation.
 *
 * Mirror of `models/obligation_fulfilment.py::OBLIGATION_FULFILMENT_STATUSES`,
 * pinned in both directions by `tests/contracts/obligation-fulfilment-statuses.json`.
 * A status the API accepts but this list omits is a state the table draws as
 * unknown; one this list offers but the API refuses fails only on save, after
 * the user has done the work.
 *
 * Order is the order the control offers them, from untouched to finished, with
 * the deliberate "this does not bind us" answer last so it does not read as a
 * step along the way.
 */
export const OBLIGATION_FULFILMENT_STATUSES = [
  "not_started",
  "in_progress",
  "done",
  "not_applicable",
] as const;

export type ObligationFulfilmentStatus =
  (typeof OBLIGATION_FULFILMENT_STATUSES)[number];
