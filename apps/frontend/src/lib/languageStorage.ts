// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Where the chosen UI language is remembered.
 *
 * Its own module, holding one constant, because two places need it and they
 * cannot share `lib/i18n.ts`: that module calls `i18n.init()` at import time
 * and imports every locale JSON, so a Playwright helper running under Node
 * cannot pull it in (`needs an import attribute of type "json"`, and the
 * browser language detector wants a `window`).
 *
 * G0-5 — before this existed, `tests/screenshots/_helpers.ts` restated the key
 * by hand and got it wrong: it wrote `i18nextLng`, i18next's own default,
 * while `lib/i18n.ts` configures `detection.order: ["localStorage"]` against
 * the key below and nothing else. Setting a language in a spec therefore did
 * nothing at all, and the capture stayed English while claiming to be Korean.
 * Nothing called the helper yet, so no mislabelled screenshot shipped — that
 * was luck, and this module is what replaces the luck.
 */
export const LANGUAGE_STORAGE_KEY = "trustedoss.lang";
