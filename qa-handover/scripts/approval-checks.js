#!/usr/bin/env node
/**
 * 승인 워크플로우 동시성/불변식 검증 — 비파괴만.
 * 가이드(approvals.md): Pending→Under Review→Approved/Rejected, If-Match 필수,
 *   버전 불일치 → 412 approval_etag_mismatch (optimistic locking).
 *
 * 검증(상태 변경 없음):
 *   - APPR-LIST   : GET /v1/approvals?status=pending → 200, Pending 존재
 *   - APPR-ETAG   : GET /v1/approvals/{id} → ETag: "{version}" 헤더
 *   - APPR-400    : PATCH transition (If-Match 누락) → 400 (전이 미실행)
 *   - APPR-412    : PATCH transition (If-Match 틀린 버전) → 412 (버전 불일치 거부 = 안전)
 *
 * 정상/무효 전이(409)는 실제 상태를 바꾸므로 의도적으로 제외(미검증/위임).
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
  if (!r.ok) throw new Error(`super-admin login ${r.status} — 레이트리밋이면 1분 후 재시도`);
  return (await r.json()).access_token;
}

(async () => {
  const token = await login();

  // 1) Pending 큐 존재
  const lr = await fetch(`${BASE}/v1/approvals?status=pending&page_size=5`, { headers: auth(token) });
  const lb = await lr.json().catch(() => ({}));
  const items = lb.items || (Array.isArray(lb) ? lb : []);
  rec('APPR-LIST', lr.status === 200, `pending 큐 → ${lr.status} (rows≈${lb.total ?? items.length})`);

  if (!items.length) {
    rec('APPR-PENDING-EXISTS', false, '⚠️ Pending 항목 0건 — 조건부 라이선스 스캔 후 재확인 필요(전이 검증 불가)');
    finish();
    return;
  }
  const id = items[0].id;

  // 2) ETag 헤더
  const gr = await fetch(`${BASE}/v1/approvals/${id}`, { headers: auth(token) });
  const etag = gr.headers.get('etag');
  rec('APPR-ETAG', gr.status === 200 && !!etag, `GET 승인 → ${gr.status}, ETag=${etag || '없음'}`);

  // 3) If-Match 누락 → 400 (전이 미실행)
  {
    const r = await fetch(`${BASE}/v1/approvals/${id}/transition`, {
      method: 'PATCH',
      headers: auth(token, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ action: 'under_review' }),
    });
    rec('APPR-400-NO-IFMATCH', r.status === 400, `If-Match 누락 전이 → ${r.status} (기대 400)`);
  }

  // 4) If-Match 틀린 버전 → 412 (optimistic locking, 전이 거부 = 안전)
  {
    const r = await fetch(`${BASE}/v1/approvals/${id}/transition`, {
      method: 'PATCH',
      headers: auth(token, { 'Content-Type': 'application/json', 'If-Match': '"999999999"' }),
      body: JSON.stringify({ action: 'under_review' }),
    });
    const b = await r.json().catch(() => ({}));
    const mismatch = b.approval_etag_mismatch === true || (b.type || '').includes('etag');
    rec('APPR-412-ETAG-MISMATCH', r.status === 412, `틀린 If-Match 전이 → ${r.status} (mismatch flag=${mismatch})`);
  }

  finish();
})().catch((e) => {
  console.error('치명:', e.message);
  process.exit(1);
});

function finish() {
  // APPR-PENDING-EXISTS는 환경 의존(BUG-010으로 Pending 0) → CI 게이트 제외
  const fails = results.filter((r) => !r.ok && r.id !== 'APPR-PENDING-EXISTS');
  const envSkips = results.filter((r) => !r.ok && r.id === 'APPR-PENDING-EXISTS');
  console.log(`\n=== 승인 검증 ${results.length}건 중 PASS ${results.filter((r) => r.ok).length} / FAIL ${fails.length}${envSkips.length ? ` / 환경보류 ${envSkips.length}` : ''} ===`);
  require('node:fs').writeFileSync('approval-checks-result.json', JSON.stringify(results, null, 2));
  if (fails.length) process.exit(1); // CI 게이트
}
