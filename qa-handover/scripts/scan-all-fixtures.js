#!/usr/bin/env node
/**
 * 전 fixture 순차 스캔 → scan-result-<fixture>.json (BD 전수 비교용).
 * - 1회 로그인 토큰 재사용 → 로그인 레이트리밋(5/분) 회피
 * - 디스크 가드: 임계 초과 시 중단 (호스트 디스크 보호)
 *
 * 사용:
 *   API_BASE=... TEST_USER_EMAIL=... TEST_USER_PASSWORD=... \
 *   BD_SUMMARY=~/.cache/bd-scan/e2e-matrix-20260520/summary.csv \
 *   FIXTURES_DIR=~/projects/bd-scan/tests/fixtures/projects \
 *   SKIP=node DISK_GUARD=98 \
 *   node scripts/scan-all-fixtures.js
 */
const fs = require('node:fs');
const path = require('node:path');
const { execSync } = require('node:child_process');

const BASE = process.env.API_BASE || 'http://localhost:8000';
const EMAIL = process.env.TEST_USER_EMAIL;
const PW = process.env.TEST_USER_PASSWORD;
const FIXTURES_DIR =
  process.env.FIXTURES_DIR || path.join(process.env.HOME, 'projects/bd-scan/tests/fixtures/projects');
const BD_SUMMARY = process.env.BD_SUMMARY;
const SKIP = (process.env.SKIP || '').split(',').filter(Boolean);
const DISK_GUARD = parseInt(process.env.DISK_GUARD || '98', 10);

const auth = (t, e = {}) => ({ Authorization: `Bearer ${t}`, ...e });

function diskPct() {
  try {
    return parseInt(execSync("df -P / | tail -1 | awk '{print $5}'").toString().replace('%', '').trim(), 10);
  } catch {
    return 0;
  }
}

function fixtureList() {
  if (BD_SUMMARY && fs.existsSync(BD_SUMMARY)) {
    const rows = fs.readFileSync(BD_SUMMARY, 'utf8').trim().split('\n').slice(1);
    return [...new Set(rows.map((l) => l.split(',')[0]).filter(Boolean))];
  }
  return fs.readdirSync(FIXTURES_DIR).filter((f) => {
    try {
      return fs.statSync(path.join(FIXTURES_DIR, f)).isDirectory();
    } catch {
      return false;
    }
  });
}

async function login() {
  const r = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PW }),
  });
  if (!r.ok) throw new Error(`login ${r.status}`);
  return (await r.json()).access_token;
}

function makeZip(name) {
  const src = path.join(FIXTURES_DIR, name);
  if (!fs.existsSync(src)) throw new Error(`fixture 없음: ${src}`);
  const zip = path.join('/tmp', `fixture-${name}.zip`);
  fs.rmSync(zip, { force: true });
  execSync(`cd ${JSON.stringify(src)} && zip -rq ${JSON.stringify(zip)} . 2>/dev/null || true`);
  if (!fs.existsSync(zip)) throw new Error('zip 생성 실패(빈 디렉토리?)');
  return zip;
}

async function scanOne(token, teamId, fixture) {
  const slug = `qa-bd-${fixture}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, '-');
  const cr = await fetch(`${BASE}/v1/projects`, {
    method: 'POST',
    headers: auth(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ team_id: teamId, name: `[QA-BD] ${fixture}`, slug }),
  });
  if (!cr.ok) throw new Error(`create ${cr.status}`);
  const projectId = (await cr.json()).id;

  const zip = makeZip(fixture);
  const form = new FormData();
  form.append('upload', new Blob([fs.readFileSync(zip)]), `${fixture}.zip`);
  const up = await fetch(`${BASE}/v1/projects/${projectId}/source-archive`, { method: 'POST', headers: auth(token), body: form });
  if (!up.ok) throw new Error(`upload ${up.status}`);

  const sc = await fetch(`${BASE}/v1/projects/${projectId}/scans`, {
    method: 'POST',
    headers: auth(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ kind: 'source' }),
  });
  if (!sc.ok) throw new Error(`scan ${sc.status}`);
  const scanId = (await sc.json()).id;

  const TERMINAL = ['succeeded', 'failed', 'cancelled'];
  let status = 'queued';
  const deadline = Date.now() + 30 * 60 * 1000;
  while (!TERMINAL.includes(status) && Date.now() < deadline) {
    await new Promise((f) => setTimeout(f, 5000));
    const s = await (await fetch(`${BASE}/v1/scans/${scanId}`, { headers: auth(token) })).json();
    status = s.status;
  }

  let componentCount = null;
  if (status === 'succeeded') {
    const c = await (await fetch(`${BASE}/v1/projects/${projectId}/components?limit=1`, { headers: auth(token) })).json();
    componentCount = c.total ?? (Array.isArray(c.items) ? c.items.length : 0);
  }
  return { fixture, projectId, scanId, status, componentCount };
}

(async () => {
  const token = await login();
  const proj = await (await fetch(`${BASE}/v1/projects?page=1&size=1`, { headers: auth(token) })).json();
  const teamId = proj.items?.[0]?.team_id;
  if (!teamId) throw new Error('team_id 조회 실패');

  const list = fixtureList().filter((f) => !SKIP.includes(f));
  console.log(`전수 스캔 대상 ${list.length}개: ${list.join(', ')}`);

  for (const fx of list) {
    const disk = diskPct();
    if (disk >= DISK_GUARD) {
      console.error(`⚠️ 디스크 ${disk}% ≥ ${DISK_GUARD}% — 안전 중단 (${fx} 미실행)`);
      break;
    }
    try {
      const r = await scanOne(token, teamId, fx);
      fs.writeFileSync(`scan-result-${fx}.json`, JSON.stringify(r, null, 2));
      console.log(`[${fx}] ${r.status} components=${r.componentCount} (disk ${disk}%)`);
    } catch (e) {
      fs.writeFileSync(`scan-result-${fx}.json`, JSON.stringify({ fixture: fx, status: 'error', error: e.message }));
      console.error(`[${fx}] 오류: ${e.message}`);
    }
  }
  console.log('전수 스캔 완료');
})().catch((e) => {
  console.error('치명 오류:', e.message);
  process.exit(1);
});
