/**
 * deriveInitials — M-17 header avatar.
 *
 * Derives a 1–2 character monogram from a user's display name. The auth
 * store's `displayName` is already `full_name ?? email` (see lib/api.ts
 * fetchMe mapping), so this helper has to cope with both shapes:
 *
 *   - "Haksung Jang"  → "HJ"  (first + last word)
 *   - "Haksung"       → "H"   (single word)
 *   - "dev@x.com"     → "D"   (email — local part, first character)
 *
 * Emails are detected by the "@" and reduced to the local part *before*
 * word-splitting so "first.last@x" still yields a single deterministic
 * character rather than punctuation soup. Returns "" for blank input so
 * callers can fall back to the generic icon instead of rendering an empty
 * circle (no placeholder per the M-17 brief).
 */
export function deriveInitials(displayName: string): string {
  const source = displayName.includes("@")
    ? displayName.slice(0, displayName.indexOf("@"))
    : displayName;
  const words = source.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "";
  if (words.length === 1) return words[0].charAt(0).toUpperCase();
  return (
    words[0].charAt(0) + words[words.length - 1].charAt(0)
  ).toUpperCase();
}
