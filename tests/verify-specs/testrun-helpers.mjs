// testrun-helpers — 테스트 케이스 실행 에이전트가 공유하는 헬퍼.
// 로그인 토큰, api 호출과 판정, shell(psql, docker-compose exec)을 한 곳에 모은다.
// 에이전트는 이 파일을 import해서 채널 실행을 일관되게 한다.
//
//   import { login, api, psql, ACCOUNTS, API, WEB } from '/Users/1112821/projects/bug-hunter/scripts/testrun-helpers.mjs';
//   const t = await login('admin@demo.trustedoss.dev');
//   const { status, body } = await api('GET', '/v1/projects', { token: t });
//   const rows = psql("SELECT count(*) FROM audit_logs;");

import { execSync } from "node:child_process";

export const API = process.env.VERIFY_API_URL || "http://localhost:8000";
export const WEB = "http://localhost:5173";
export const COMPOSE =
  process.env.VERIFY_COMPOSE_FILE ||
  new URL("../../docker-compose.dev.yml", import.meta.url).pathname;
export const DEFAULT_PW = "DemoTest2026!";

// seed_demo 고정 계정. 팀 id는 런타임에 /auth/me 또는 /v1/projects로 확인할 것(시드마다 uuid가 다름).
export const ACCOUNTS = {
  super_admin: { email: "admin@demo.trustedoss.dev", pw: DEFAULT_PW },
  team_admin_frontend: { email: "frontend-admin@demo.trustedoss.dev", pw: DEFAULT_PW },
  team_admin_backend: { email: "backend-admin@demo.trustedoss.dev", pw: DEFAULT_PW },
  team_admin_security: { email: "security-admin@demo.trustedoss.dev", pw: DEFAULT_PW },
  developer: { email: "dev@demo.trustedoss.dev", pw: DEFAULT_PW },
};

// 로그인하고 access_token 반환. 실패 시 throw.
export async function login(email, pw = DEFAULT_PW) {
  const r = await fetch(API + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password: pw }),
  });
  if (!r.ok) throw new Error(`login 실패 ${email}: ${r.status}`);
  const j = await r.json();
  return j.access_token;
}

// api 호출. opts.token(Bearer), opts.body(JSON), opts.headers(추가, 예 if-match).
// 반환 { status, body, headers }. body 파싱 실패 시 body=null.
export async function api(method, path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (opts.token) headers.Authorization = "Bearer " + opts.token;
  const init = { method, headers };
  if (opts.body !== undefined) init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
  const r = await fetch(API + path, init);
  let body = null;
  try { body = await r.json(); } catch { /* 비 JSON 응답 */ }
  return { status: r.status, body, headers: Object.fromEntries(r.headers.entries()) };
}

// 목록 응답에서 배열 추출(items/results/data/배열 자체).
export function listOf(body) {
  if (!body) return [];
  return body.items || body.results || body.data || (Array.isArray(body) ? body : []);
}

// psql 쿼리 실행(읽기 권장). 출력 문자열 반환.
export function psql(sql) {
  const cmd = `docker-compose -f ${COMPOSE} exec -T postgres psql -U trustedoss -d trustedoss -At -c ${JSON.stringify(sql)}`;
  return execSync(cmd, { encoding: "utf8" });
}

// backend 컨테이너에서 셸 명령 실행(스크립트, 로그 grep 등).
export function backendExec(shellCmd) {
  const cmd = `docker-compose -f ${COMPOSE} exec -T backend sh -c ${JSON.stringify(shellCmd)}`;
  return execSync(cmd, { encoding: "utf8" });
}

// 판정 헬퍼: 기대 상태코드와 실측 비교.
export const expectStatus = (got, want) => ({ ok: got === want, note: `status ${got} (기대 ${want})` });
