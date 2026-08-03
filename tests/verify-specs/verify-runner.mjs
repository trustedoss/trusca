// verify-runner — 모듈별 결정적 검증 스펙(JSON)을 실제 타깃에 실행해 결정적 pass/fail을 낸다.
//
// 이 개선이 닫는 약점: ① 판정 정확성 미검증, ② 독립 재현 부족, ③ LLM 작성·LLM 검증 순환.
//   에이전트가 매번 새로 판정하는 대신, 입력과 기대(상태코드/본문/DB행)를 코드에 박아
//   누구나 한 명령으로 재실행하면 같은 판정이 나오게 한다. 기대값은 케이스 명세(가이드)에서
//   도출하며(원 판정 복붙 금지), api 경로는 /openapi.json로 확정한 것만 쓴다.
//
// 스펙 위치: scripts/verify/specs/<module>.json
// 토큰은 scripts/verify/.tokens.json에 캐시(레이트리밋 5/분/IP 회피 + 결정성).
//
// 사용법:
//   node scripts/verify/verify-runner.mjs <module> [--json]
//   import { runSpec } from ".../verify-runner.mjs"  (recheck-gate가 in-process로 호출)
//
// exit code: 모든 체크 pass면 0, 하나라도 fail/error면 1.

import { readFileSync, existsSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { login, api, psql, ACCOUNTS } from "./testrun-helpers.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const SPEC_DIR = join(HERE, "specs");
const TOKEN_CACHE = join(HERE, ".tokens.json");
const TOKEN_TTL_MS = 25 * 60 * 1000; // access 토큰 30분 만료보다 보수적

// ---- 토큰 캐시: 재로그인을 피해 레이트리밋과 비결정을 차단 ----
function loadCache() {
  try { return JSON.parse(readFileSync(TOKEN_CACHE, "utf8")); } catch { return {}; }
}
function saveCache(c) {
  mkdirSync(HERE, { recursive: true });
  writeFileSync(TOKEN_CACHE, JSON.stringify(c, null, 2));
}
const _cache = loadCache();
async function tokenFor(accountKey) {
  const acc = ACCOUNTS[accountKey];
  if (!acc) throw new Error(`알 수 없는 계정 키: ${accountKey}`);
  const now = Date.now();
  const hit = _cache[acc.email];
  if (hit && hit.exp > now) return hit.token;
  const token = await login(acc.email, acc.pw);
  _cache[acc.email] = { token, exp: now + TOKEN_TTL_MS };
  saveCache(_cache);
  return token;
}

// ---- 값 추출/치환 ----
function getPath(obj, path) {
  // "a.b[0].c" 형태 단순 경로
  return path.split(/[.[\]]+/).filter(Boolean).reduce((o, k) => (o == null ? o : o[/^\d+$/.test(k) ? Number(k) : k]), obj);
}
function subst(str, vars) {
  if (typeof str !== "string") return str;
  return str.replace(/\$\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `\${${k}}`));
}
function substDeep(v, vars) {
  if (typeof v === "string") return subst(v, vars);
  if (Array.isArray(v)) return v.map((x) => substDeep(x, vars));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, substDeep(x, vars)]));
  return v;
}
function listOf(body) {
  if (!body) return [];
  return body.items || body.results || body.data || (Array.isArray(body) ? body : []);
}

// 일시적 네트워크 오류(연결 리셋 등)는 환경오류로 오분류되지 않게 재시도한다.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function apiRetry(method, path, opts, tries = 3) {
  let last;
  for (let i = 0; i < tries; i++) {
    try { return await api(method, path, opts); }
    catch (err) { last = err; await sleep(200 * (i + 1)); }
  }
  throw last;
}

// ---- vars 단계: 런타임 uuid(프로젝트/팀 id 등) 해석 ----
async function resolveVars(spec, vars) {
  for (const v of spec.vars || []) {
    const token = v.as ? await tokenFor(v.as) : undefined;
    const path = subst(v.path, vars);
    const { status, body } = await apiRetry(v.method || "GET", path, { token, body: substDeep(v.body, vars) });
    if (status >= 400) throw new Error(`vars '${v.name}' 해석 실패: ${v.method} ${path} → ${status}`);
    let src;
    if (v.list) {
      const arr = listOf(body);
      src = v.where ? arr.find((x) => Object.entries(v.where).every(([k, want]) => x[k] === want)) : arr[0];
      if (!src) throw new Error(`vars '${v.name || JSON.stringify(v.fields)}': where ${JSON.stringify(v.where)} 매칭 없음(목록 ${arr.length}건)`);
    } else {
      src = body;
    }
    // 한 레코드에서 여러 필드를 뽑을 때는 fields={NAME:path}, 단일이면 name+field.
    if (v.fields) for (const [name, path] of Object.entries(v.fields)) vars[name] = getPath(src, path);
    else vars[v.name] = v.field ? getPath(src, v.field) : src;
  }
}

// ---- 기대 평가 ----
function evalExpect(check, res, vars) {
  const e = check.expect || {};
  const fails = [];
  if (e.status !== undefined) {
    const want = Array.isArray(e.status) ? e.status : [e.status];
    if (!want.includes(res.status)) fails.push(`status ${res.status} ∉ ${JSON.stringify(want)}`);
  }
  if (e.jsonHas) for (const k of e.jsonHas) if (getPath(res.body, k) === undefined) fails.push(`본문에 ${k} 없음`);
  if (e.jsonMissing) for (const k of e.jsonMissing) if (getPath(res.body, k) !== undefined) fails.push(`본문에 ${k} 존재(없어야 함)`);
  if (e.jsonEquals) for (const [k, want] of Object.entries(e.jsonEquals)) {
    const got = getPath(res.body, k);
    if (got !== subst(String(want), vars) && got !== want) fails.push(`${k}=${JSON.stringify(got)} ≠ ${JSON.stringify(want)}`);
  }
  if (e.bodyIncludes) {
    const hay = JSON.stringify(res.body || "");
    for (const s of [].concat(e.bodyIncludes)) if (!hay.includes(subst(s, vars))) fails.push(`본문에 '${s}' 미포함`);
  }
  if (e.bodyMatches) {
    const hay = JSON.stringify(res.body || "");
    if (!new RegExp(e.bodyMatches).test(hay)) fails.push(`본문이 /${e.bodyMatches}/ 불일치`);
  }
  return fails;
}

async function runCheck(check, vars) {
  // shell 채널: sql 실행
  if (check.channel === "shell" || check.sql) {
    const sql = subst(check.sql, vars);
    let out;
    try { out = psql(sql).trim(); } catch (err) { return { tc: check.tc, verdict: "error", channel: "shell", detail: `psql 오류: ${err.message}` }; }
    const e = check.expect || {};
    const fails = [];
    if (e.rowEquals !== undefined && out !== String(subst(String(e.rowEquals), vars))) fails.push(`행값 '${out}' ≠ '${e.rowEquals}'`);
    if (e.rowCount !== undefined && out.split("\n").filter(Boolean).length !== e.rowCount) fails.push(`행수 ${out.split("\n").filter(Boolean).length} ≠ ${e.rowCount}`);
    if (e.rowMatches && !new RegExp(e.rowMatches).test(out)) fails.push(`행값 /${e.rowMatches}/ 불일치`);
    return { tc: check.tc, verdict: fails.length ? "fail" : "pass", channel: "shell", knownBug: check.knownBug, detail: fails.join("; ") || out.slice(0, 80) };
  }
  // api 채널
  const token = check.useKey ? undefined : check.as ? await tokenFor(check.as) : undefined;
  const headers = substDeep({ ...(check.headers || {}) }, vars);
  if (check.useKey) headers.Authorization = `Bearer ${subst(check.useKey, vars)}`;
  const path = subst(check.request.path, vars);
  let res;
  try {
    res = await apiRetry(check.request.method, path, { token, headers, body: substDeep(check.request.body, vars) });
  } catch (err) {
    return { tc: check.tc, verdict: "error", channel: "api", detail: `요청 오류: ${err.message}` };
  }
  // 후속 capture(예: 생성된 키 raw_key를 var로)
  if (check.capture) for (const [name, p] of Object.entries(check.capture)) vars[name] = getPath(res.body, p);
  const fails = evalExpect(check, res, vars);
  return { tc: check.tc, verdict: fails.length ? "fail" : "pass", channel: "api", knownBug: check.knownBug, detail: fails.join("; ") || `status ${res.status}` };
}

export async function runSpec(moduleName) {
  const specFile = join(SPEC_DIR, moduleName + ".json");
  if (!existsSync(specFile)) throw new Error(`스펙 없음: ${specFile}`);
  const spec = JSON.parse(readFileSync(specFile, "utf8"));
  const vars = {};
  await resolveVars(spec, vars);
  const results = [];
  for (const check of spec.checks) {
    // 체크별 로컬 vars 격리가 필요하면 spec에서 분리; 기본은 공유(capture 연계 위해)
    results.push(await runCheck(check, vars));
  }
  return { module: moduleName, results };
}

async function main() {
  const args = process.argv.slice(2);
  const jsonOut = args.includes("--json");
  const mod = args.find((a) => !a.startsWith("--"));
  if (!mod) { console.error("사용법: node scripts/verify/verify-runner.mjs <module> [--json]"); process.exit(2); }
  const out = await runSpec(mod);
  if (jsonOut) { console.log(JSON.stringify(out, null, 2)); }
  else {
    console.log(`\n[verify-runner] ${out.module}`);
    for (const r of out.results) {
      const mark = r.verdict === "pass" ? "PASS " : r.verdict === "fail" ? "FAIL " : "ERROR";
      console.log(`  ${mark} ${r.tc.padEnd(20)} ${r.channel}  ${r.detail}${r.knownBug ? `  [knownBug ${r.knownBug}]` : ""}`);
    }
    const bad = out.results.filter((r) => r.verdict !== "pass");
    console.log(`\n  ${out.results.length - bad.length}/${out.results.length} pass`);
  }
  process.exit(out.results.every((r) => r.verdict === "pass") ? 0 : 1);
}

// CLI로 직접 실행될 때만 main
if (process.argv[1] && process.argv[1].endsWith("verify-runner.mjs")) main();
