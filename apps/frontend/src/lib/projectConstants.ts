// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Project vocabularies, kept apart from the module that talks to the server.
 *
 * They live here rather than beside the request functions because tests mock
 * `projectsApi` wholesale. A constant declared there disappears from every one
 * of those tests the moment it is added, and the failure arrives as an
 * unrelated crash in a component that merely reads it. Spreading the real
 * module back in fixes the constant and un-stubs the request functions at the
 * same time, so the suite starts making real calls. Separating the two removes
 * the choice.
 */

/**
 * How a project's software reaches the people who use it.
 *
 * A closed set, unlike the free-text attributes beside it, because this is not
 * an organizational label: it decides which licence obligations bind, and
 * offering a network service is not the same as shipping a binary. The backend
 * holds the same tuple and a contract test asserts the two agree.
 *
 * Ordered from the narrowest reach to the widest; the form and the filter
 * render in this order.
 */
export const DISTRIBUTION_MODELS = [
  "internal",
  "saas",
  "binary",
  "source",
  "embedded",
] as const;

export type DistributionModel = (typeof DISTRIBUTION_MODELS)[number];

/**
 * The filter value meaning "has not said how it ships".
 *
 * A sentinel rather than an empty parameter, because empty already means "do
 * not filter" and the two questions are opposites: one asks for everything,
 * the other asks for the projects still to be filled in. Deliberately not a
 * member of DISTRIBUTION_MODELS, so it can never be stored.
 */
export const UNSET_DISTRIBUTION_MODEL = "unset";
