/**
 * Auth API client — Phase 1 PR #6.
 *
 * 1.6 shipped a fetch-based stub here. 1.7 promotes the implementation to
 * `lib/api.ts` (axios + interceptor + refresh rotation). This module is now a
 * thin re-export so existing call sites (LoginPage, RegisterPage) keep their
 * imports — but every request now flows through the shared axios instance.
 *
 * New callers should import from `@/lib/api` directly.
 */

export { ProblemError } from "@/lib/problem";
export type { ProblemDetails } from "@/lib/problem";
export {
  postLogin as login,
  postRegister as register,
  fetchMe,
  postLogout,
} from "@/lib/api";
export type {
  LoginPayload,
  RegisterPayload,
  TokenResponse,
  UserPublicWire as UserPublic,
} from "@/lib/api";
