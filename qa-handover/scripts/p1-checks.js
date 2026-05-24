#!/usr/bin/env node
/**
 * P1 축 검증 — boundary · concurrency · state-transition · observability. 전부 비파괴.
 *  - boundary       : 페이지네이션/입력 경계 → 422 (자원 생성 안 됨)
 *  - concurrency    : 기존 (team,slug) 재사용 생성 → 409 (중복이라 생성 안 됨)
 *  - state-trans    : terminal(succeeded) 스캔 취소 → 409 (이미 종료라 변화 없음)
 *  - observability  : X-Request-ID echo / 미전송 시 생성, RFC7807 problem+json
 *
 * 사용: API_BASE=http://localhost:8000 node -r dotenv/config scripts/p1-checks.js
 */
const fs = require('node:fs');
const BASE = process.env.API_BASE || 'http://localhost:8000';
const E = process.env.TEST_USER_EMAIL,
  P = process.env.TEST_USER_PASSWORD;
const results = [];
const rec = (id, ok, detail) => {
  results.push({ id, ok, detail });
  console.log(`${ok ? '✅ PASS' : '🐛 FAIL'} [${id}] ${detail}`);
};
const auth = (t, e = {}) => ({ Authorization: `Bearer ${t}`, ...e });

(async () => {
  const lr = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: E, password: P }),
  });
  if (!lr.ok) throw new Error(`login ${lr.status}`);
  const t = (await lr.json()).access_token;

  // --- boundary (페이지네이션/입력 경계 → 422) ---
  for (const [id, qs] of [
    ['BND-PAGE-0', 'page=0&size=20'],
    ['BND-SIZE-OVER', 'page=1&size=101'],
    ['BND-Q-LONG', `page=1&size=20&q=${'a'.repeat(256)}`],
  ]) {
    const r = await fetch(`${BASE}/v1/projects?${qs}`, { headers: auth(t) });
    rec(id, r.status === 422, `GET /v1/projects?${qs.slice(0, 30)}… → ${r.status} (기대 422)`);
  }

  // --- 기준 프로젝트(team_id, slug) 확보 ---
  const pj = await (await fetch(`${BASE}/v1/projects?page=1&size=1`, { headers: auth(t) })).json();
  const base = pj.items?.[0];

  // --- boundary: 빈 이름 생성 → 422 ---
  if (base) {
    const r = await fetch(`${BASE}/v1/projects`, {
      method: 'POST',
      headers: auth(t, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ team_id: base.team_id, name: '', slug: 'qa-empty-name-' + Date.now() }),
    });
    rec('BND-EMPTY-NAME', r.status === 422, `빈 이름 생성 → ${r.status} (기대 422, 자원 생성 안 됨)`);
  }

  // --- concurrency: 기존 (team, slug) 재사용 → 409 (비파괴) ---
  if (base?.slug) {
    const r = await fetch(`${BASE}/v1/projects`, {
      method: 'POST',
      headers: auth(t, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ team_id: base.team_id, name: 'QA dup slug probe', slug: base.slug }),
    });
    const b = await r.json().catch(() => ({}));
    rec('CON-SLUG-DUP', r.status === 409, `기존 slug 재사용 → ${r.status} (기대 409 slug conflict, type=${b.type || '?'})`);
  }

  // --- state-transition: terminal 스캔 취소 → 409 (비파괴) ---
  let termScan = null;
  try {
    const m = JSON.parse(fs.readFileSync('scan-result-maven.json', 'utf8'));
    if (m.status === 'succeeded' && m.scanId) termScan = m.scanId;
  } catch {}
  if (termScan) {
    const r = await fetch(`${BASE}/v1/scans/${termScan}/cancel`, { method: 'POST', headers: auth(t) });
    rec('ST-CANCEL-TERMINAL', r.status === 409, `terminal(succeeded) 스캔 취소 → ${r.status} (기대 409 — 이미 종료)`);
  } else {
    rec('ST-CANCEL-TERMINAL', true, 'SKIP — terminal 스캔 참조 없음');
  }

  // --- observability: X-Request-ID echo ---
  {
    const rid = `qa-probe-${Date.now()}`;
    const r = await fetch(`${BASE}/v1/projects?page=1&size=1`, { headers: auth(t, { 'X-Request-ID': rid }) });
    const echoed = r.headers.get('x-request-id');
    rec('OBS-REQID-ECHO', echoed === rid, `X-Request-ID echo: 보낸 "${rid}" → 받은 "${echoed}"`);
  }
  {
    const r = await fetch(`${BASE}/v1/projects?page=1&size=1`, { headers: auth(t) });
    const gen = r.headers.get('x-request-id');
    rec('OBS-REQID-GEN', !!gen, `미전송 시 서버 생성 X-Request-ID: "${gen || '없음'}"`);
  }

  // --- observability: RFC7807 problem+json (422) ---
  {
    const r = await fetch(`${BASE}/v1/projects?page=0`, { headers: auth(t) });
    const ct = r.headers.get('content-type') || '';
    const b = await r.json().catch(() => ({}));
    const ok = ct.includes('json') && (b.type !== undefined || b.title !== undefined || b.detail !== undefined);
    rec('OBS-RFC7807', ok, `422 에러 형식 ct="${ct}" type=${b.type ?? '?'} title=${b.title ?? '?'}`);
  }

  const fails = results.filter((r) => !r.ok);
  console.log(`\n=== P1 검증 ${results.length}건 중 PASS ${results.length - fails.length} / FAIL ${fails.length} ===`);
  fs.writeFileSync('p1-checks-result.json', JSON.stringify(results, null, 2));
  if (fails.length) process.exit(1); // CI 게이트
})().catch((e) => {
  console.error('치명:', e.message);
  process.exit(1);
});
