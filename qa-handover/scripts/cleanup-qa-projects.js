#!/usr/bin/env node
/**
 * QA 전수 스캔이 만든 [QA-BD] 테스트 프로젝트 정리 — 안전 가드 포함.
 * - scan-result-*.json의 projectId만 대상
 * - 삭제 전 GET으로 name이 "[QA-BD] "로 시작하는지 확인 → 아니면 SKIP
 *   (stale projectId·타인 프로젝트 오삭제 방지)
 *
 * 사용: API_BASE=http://localhost:8000 node -r dotenv/config scripts/cleanup-qa-projects.js
 */
const fs = require('node:fs');
const BASE = process.env.API_BASE || 'http://localhost:8000';
const E = process.env.TEST_USER_EMAIL;
const P = process.env.TEST_USER_PASSWORD;
const PREFIX = '[QA-BD]';

(async () => {
  const lr = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: E, password: P }),
  });
  if (!lr.ok) throw new Error(`login ${lr.status}`);
  const t = (await lr.json()).access_token;
  const H = { Authorization: `Bearer ${t}` };

  const ids = [];
  for (const f of fs.readdirSync('.').filter((f) => /^scan-result-.*\.json$/.test(f))) {
    try {
      const j = JSON.parse(fs.readFileSync(f, 'utf8'));
      if (j.projectId) ids.push({ fx: j.fixture, id: j.projectId });
    } catch {}
  }
  console.log(`scan-result 대상 후보: ${ids.length}개`);

  let del = 0,
    skip = 0,
    fail = 0;
  for (const { fx, id } of ids) {
    // 안전 가드: 이름 확인
    const g = await fetch(`${BASE}/v1/projects/${id}`, { headers: H });
    if (g.status === 404) {
      skip++;
      continue;
    } // 이미 없음
    const name = g.ok ? (await g.json()).name || '' : '';
    if (!name.startsWith(PREFIX)) {
      console.log(`  SKIP [${fx}] name="${name}" (${PREFIX} 아님 — 보호)`);
      skip++;
      continue;
    }
    const r = await fetch(`${BASE}/v1/projects/${id}`, { method: 'DELETE', headers: H });
    if ([200, 202, 204].includes(r.status)) del++;
    else {
      fail++;
      console.log(`  FAIL [${fx}] DELETE ${r.status}`);
    }
  }

  // p2-checks가 만든 예약 slug leftover (slug=organization, name=p2)만 정확히 정리
  try {
    const q = await fetch(`${BASE}/v1/projects?page=1&size=50&q=organization`, { headers: H });
    const items = (await q.json()).items || [];
    for (const p of items.filter((p) => p.slug === 'organization' && p.name === 'p2')) {
      const r = await fetch(`${BASE}/v1/projects/${p.id}`, { method: 'DELETE', headers: H });
      if ([200, 202, 204].includes(r.status)) {
        del++;
        console.log(`  정리 [p2-organization] id=${p.id}`);
      } else fail++;
    }
  } catch {}

  console.log(`정리: 삭제 ${del} / 건너뜀 ${skip} / 실패 ${fail}`);
})().catch((e) => {
  console.error('치명:', e.message);
  process.exit(1);
});
