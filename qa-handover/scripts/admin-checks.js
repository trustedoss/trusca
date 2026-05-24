#!/usr/bin/env node
/**
 * 관리자 영역(super_admin) 검증 — 비파괴 read + 안전한 불변식 차단만.
 * 가이드(admin-guide) 기반:
 *  - /v1/admin/{users,teams,audit,disk,health} read 접근
 *  - 복원 412 게이트: X-Confirm-Restore 헤더 없이 restore → 412 (복원 미실행 = 안전)
 *  - last-super-admin 보호: super_admin이 정확히 1명(=본인)일 때만 강등 시도 → 차단 확인
 *    (차단되므로 실제 강등 안 됨. 2명 이상이면 위험하므로 SKIP)
 *
 * 사용:
 *   API_BASE=http://localhost:8000 \
 *   ADMIN_EMAIL=admin@demo.trustedoss.dev ADMIN_PASSWORD=... \
 *   node scripts/admin-checks.js
 */
const BASE = process.env.API_BASE || 'http://localhost:8000';
const EMAIL = process.env.ADMIN_EMAIL || 'admin@demo.trustedoss.dev';
const PW = process.env.ADMIN_PASSWORD || process.env.TEST_USER_PASSWORD;
const auth = (t, e = {}) => ({ Authorization: `Bearer ${t}`, ...e });

const results = [];
const rec = (id, ok, detail) => {
  results.push({ id, ok, detail });
  console.log(`${ok ? '✅ PASS' : '🐛 FAIL'} [${id}] ${detail}`);
};

async function login() {
  const r = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PW }),
  });
  if (!r.ok) throw new Error(`super-admin login ${r.status} (${EMAIL}) — 레이트리밋(5/분)이면 1분 후 재시도`);
  return (await r.json()).access_token;
}

async function getJson(token, path) {
  const r = await fetch(`${BASE}${path}`, { headers: auth(token) });
  let body = null;
  try {
    body = await r.json();
  } catch {}
  return { status: r.status, body };
}

(async () => {
  const token = await login();
  rec('ADMIN-LOGIN', true, `super_admin 로그인 성공 (${EMAIL})`);

  // 1) read 접근
  for (const [id, path] of [
    ['ADMIN-USERS', '/v1/admin/users'],
    ['ADMIN-TEAMS', '/v1/admin/teams'],
    ['ADMIN-AUDIT', '/v1/admin/audit'],
    ['ADMIN-DISK', '/v1/admin/disk'],
    ['ADMIN-HEALTH', '/v1/admin/health'],
  ]) {
    const { status, body } = await getJson(token, path);
    const n = body?.total ?? (Array.isArray(body?.items) ? body.items.length : Array.isArray(body) ? body.length : '?');
    rec(id, status === 200, `${path} → ${status}${status === 200 ? ` (rows≈${n})` : ''}`);
  }

  // 2) 감사 로그 append-only — 행 존재 확인(불변성 자체는 DB 트리거 영역, UI/DELETE 부재로 보강)
  const audit = await getJson(token, '/v1/admin/audit');
  const auditRows = audit.body?.total ?? (Array.isArray(audit.body?.items) ? audit.body.items.length : 0);
  rec('ADMIN-AUDIT-ROWS', auditRows > 0, `감사 로그 행 ${auditRows}건 (write 작업 기록 존재)`);

  // 3) 복원 412 게이트 (비파괴 — confirm!=yes면 파일 스트리밍 전 즉시 412 return → 복원 미실행)
  //    archive는 required(File(...))라 더미 파일을 동봉해야 함수 본문의 412 분기에 도달.
  {
    const form = new FormData();
    form.append('archive', new Blob([Buffer.from('dummy')]), 'noop.tar.gz');
    const r = await fetch(`${BASE}/v1/admin/backup/restore`, { method: 'POST', headers: auth(token), body: form });
    let body = null;
    try {
      body = await r.json();
    } catch {}
    const typeOk = (body?.type || '').includes('restore_confirmation_required');
    rec(
      'ADMIN-RESTORE-412',
      r.status === 412 && typeOk,
      `복원(파일O, X-Confirm-Restore 누락) → ${r.status} (type=${body?.type || '없음'})`
    );
  }

  // 4) last-super-admin 보호 (role 필터로 정확히 카운트, 단일일 때만 강등 차단 검증 — 안전)
  {
    const u = await getJson(token, '/v1/admin/users?role=super_admin&page_size=200');
    const items = u.body?.items || (Array.isArray(u.body) ? u.body : []);
    const total = u.body?.total ?? items.length;
    const supers = items.filter((x) => x.role === 'super_admin');
    const me = items.find((x) => x.email?.toLowerCase() === EMAIL.toLowerCase());
    if (total > 1) {
      rec('ADMIN-LAST-SUPERADMIN', true, `super_admin ${total}명 — 강등 위험으로 SKIP(불변식은 유일할 때만 안전 검증)`);
    } else if (supers.length === 1 && me && supers[0].id === me.id) {
      const r = await fetch(`${BASE}/v1/admin/users/${me.id}/role`, {
        method: 'PATCH',
        headers: auth(token, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({ role: 'developer' }),
      });
      let body = null;
      try {
        body = await r.json();
      } catch {}
      const blocked = r.status >= 400;
      rec(
        'ADMIN-LAST-SUPERADMIN',
        blocked,
        `유일 super_admin 자기강등 → ${r.status} ${blocked ? `(차단됨: ${body?.title || ''})` : '⚠️ 강등됨! 즉시 복구 필요'}`
      );
      // 만약 잘못 강등되었으면(blocked=false) 즉시 원복 시도
      if (!blocked) {
        await fetch(`${BASE}/v1/admin/users/${me.id}/role`, {
          method: 'PATCH',
          headers: auth(token, { 'Content-Type': 'application/json' }),
          body: JSON.stringify({ role: 'super_admin' }),
        }).catch(() => {});
      }
    } else {
      rec('ADMIN-LAST-SUPERADMIN', true, `super_admin ${supers.length}명 — 강등 위험으로 SKIP(불변식은 단일일 때만 안전 검증)`);
    }
  }

  const fails = results.filter((r) => !r.ok);
  console.log(`\n=== 관리자 검증 ${results.length}건 중 PASS ${results.length - fails.length} / FAIL ${fails.length} ===`);
  require('node:fs').writeFileSync('admin-checks-result.json', JSON.stringify(results, null, 2));
  if (fails.length) process.exit(1); // CI 게이트: 검증 실패 시 파이프라인 중단
})().catch((e) => {
  console.error('치명:', e.message);
  process.exit(1);
});
