/**
 * Resolve the backend API base URL.
 *
 * In dev the Vite container reads `VITE_API_BASE_URL`; in browser builds the
 * value is statically inlined. Trailing slashes are stripped so concatenated
 * paths never produce `//`.
 */
export function getApiBase(): string {
  const raw = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
  return raw.replace(/\/+$/, "");
}
