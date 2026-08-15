// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Put text on the clipboard, in the two ways browsers allow.
 *
 * The fallback is not defensive padding. `navigator.clipboard` is absent on
 * any origin the browser considers insecure, and this product is installed
 * on internal networks where plain http is ordinary. Without it every copy
 * on such a deployment fails, and the failure looks like a product bug
 * rather than a browser rule.
 *
 * Lives beside the other lib helpers rather than in the button that uses it:
 * `IntegrationsPage` needs the behaviour with its own labelled button, and a
 * module that exports a component and a function trips the fast-refresh
 * lint besides.
 */

/** Returns whether the text reached the clipboard. */
export async function writeToClipboard(value: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Permission denied, or a browser that exposes the API and declines to
      // use it outside a gesture it recognises. Fall through.
    }
  }

  try {
    const scratch = document.createElement("textarea");
    scratch.value = value;
    // Off-screen rather than hidden: an element with `display: none` cannot
    // be selected, and the copy then silently does nothing.
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.top = "-9999px";
    document.body.appendChild(scratch);
    scratch.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(scratch);
    return ok;
  } catch {
    return false;
  }
}
