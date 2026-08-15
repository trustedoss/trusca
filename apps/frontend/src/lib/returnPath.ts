// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Where to send someone after they sign in.
 *
 * The guard is on `/login`, so an attacker's lever is a link that carries a
 * crafted return target: the victim signs in to the real product with real
 * credentials and is then handed to whatever the link said. That is the
 * open-redirect shape, and it is worth more than usual here because the
 * hand-off happens immediately after a successful password entry, which is
 * when a lookalike page is most convincing.
 *
 * So this accepts one thing: a path inside this application. Not a URL, not
 * a host, not a scheme. Everything else falls back to the dashboard,
 * silently, because there is nothing useful to say to a user about a return
 * target they did not choose.
 *
 * Rejected, each for a stated reason rather than a guess at what a browser
 * might do with it:
 *   - `//evil.example/x`      a browser reads a protocol-relative URL as
 *                             another origin
 *   - `/\evil.example`        and it normalises a backslash to a slash
 *   - `https://evil.example`  the obvious one
 *   - `javascript:alert(1)`   no scheme survives "must start with /", but it
 *                             is asserted so the rule cannot be loosened
 *                             later without a test noticing
 *   - control characters, which smuggle a line break past a naive logger or
 *     header writer downstream
 *   - `/login` and the other auth screens, which are not destinations: they
 *     would bounce the user back to where they just came from
 *
 * The vulnerability and component detail pages carry their own `state.from`
 * checks (see `backToListHref` in both). Those additionally require the path
 * to stay inside one project, so they are narrower than this rather than
 * copies of it.
 */

/** Screens that are part of signing in, and so cannot be signed-in targets. */
const AUTH_PATHS = [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
];

// Built from escapes rather than written as literals: these characters are
// invisible in an editor, and an edit can drop one without anyone seeing.
// U+0085, U+2028 and U+2029 are here for the same reason as the C0 range:
// something downstream may treat them as a line break even though a URL
// parser does not.
const CONTROL_CHARS = new RegExp(
  // eslint-disable-next-line no-control-regex -- finding them is the point
  "[\\u0000-\\u001f\\u007f-\\u009f\\u2028\\u2029]",
);

export const DEFAULT_RETURN_PATH = "/";

/**
 * Return `candidate` when it is a path inside this app, else the dashboard.
 */
export function safeReturnPath(candidate: unknown): string {
  if (typeof candidate !== "string") return DEFAULT_RETURN_PATH;

  const path = candidate.trim();
  if (path.length === 0 || path.length > 2000) return DEFAULT_RETURN_PATH;
  if (CONTROL_CHARS.test(path)) return DEFAULT_RETURN_PATH;

  // One leading slash, and the next character must not turn it into an
  // authority.
  if (!path.startsWith("/")) return DEFAULT_RETURN_PATH;
  if (path.startsWith("//") || path.startsWith("/\\")) {
    return DEFAULT_RETURN_PATH;
  }

  // Normalised before the comparison, because the router is not literal
  // about either: `matchPath` treats `/login/`, `/LOGIN` and `/login//` as
  // the login screen, so an exact-match exclusion let all three back in.
  // The one that mattered was `/reset-password/?token=<jwt>`, which would
  // then have been recorded and forwarded.
  const pathname = path.split(/[?#]/)[0].toLowerCase().replace(/\/+$/, "");
  if (AUTH_PATHS.includes(pathname || "/")) return DEFAULT_RETURN_PATH;

  return path;
}
