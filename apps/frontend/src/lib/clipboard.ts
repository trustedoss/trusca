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

  const scratch = document.createElement("textarea");
  try {
    scratch.value = value;
    // Off-screen rather than hidden: an element with `display: none` cannot
    // be selected, and the copy then silently does nothing.
    //
    // Off-screen still means reachable, so it is taken out of the tab order
    // and hidden from the accessibility tree for the moment it exists.
    scratch.setAttribute("readonly", "");
    scratch.setAttribute("tabindex", "-1");
    scratch.setAttribute("aria-hidden", "true");
    scratch.style.position = "fixed";
    scratch.style.top = "-9999px";
    document.body.appendChild(scratch);
    scratch.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    // In `finally`, because the removal used to sit on the line after the
    // copy: `document.execCommand` is deprecated, so the day a browser drops
    // it the call throws, and every attempt left another focusable textarea
    // in the document. The browsers likeliest to drop it are the ones this
    // fallback exists for.
    scratch.remove();
  }
}
