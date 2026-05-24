#!/usr/bin/env node
/**
 * Black Duck ground truth(summary.csv) vs TrustedOSS 스캔 결과 정확성 비교.
 *
 * SCA 도구의 핵심 가치(컴포넌트 탐지 정확성)를 외부 기준(Black Duck)과 대조한다.
 * 실스캔은 건당 분 단위라 단일 세션엔 부적합 → CI matrix(fixture별 병렬 스캔) 후
 * 본 스크립트로 일괄 diff 하는 것이 올바른 실행 모델.
 *
 * 전제: 각 fixture를 TrustedOSS 프로젝트로 스캔 완료해 두고, fixture→projectId 매핑 제공.
 * 사용:
 *   API_BASE=http://localhost:8000 \
 *   TEST_USER_EMAIL=... TEST_USER_PASSWORD=... \
 *   BD_SUMMARY=~/.cache/bd-scan/e2e-matrix-20260520/summary.csv \
 *   FIXTURE_PROJECT_MAP='{"node":"<uuid>","maven":"<uuid>"}' \
 *   node scripts/compare-bd.js
 *
 * summary.csv 컬럼: fixture,image,detector_expected,detector_actual,bom_min,exit_code,status,note
 * 판정: TrustedOSS 컴포넌트 수 >= bom_min (bom_min<0 이면 SKIP)
 */
const fs = require('node:fs');

async function login(base, email, password) {
  const res = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`로그인 실패 ${res.status}`);
  return (await res.json()).access_token;
}

function parseSummary(csvPath) {
  const lines = fs.readFileSync(csvPath, 'utf8').trim().split('\n');
  const cols = lines[0].split(',');
  return lines.slice(1).map((line) => {
    const vals = line.split(',');
    return Object.fromEntries(cols.map((c, i) => [c, vals[i]]));
  });
}

async function componentCount(base, token, projectId) {
  const res = await fetch(`${base}/v1/projects/${projectId}/components?limit=1`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return null;
  const j = await res.json();
  return j.total ?? (Array.isArray(j.items) ? j.items.length : 0);
}

(async () => {
  const base = process.env.API_BASE || 'http://localhost:8000';
  const summaryPath = process.env.BD_SUMMARY;
  const map = JSON.parse(process.env.FIXTURE_PROJECT_MAP || '{}');
  if (!summaryPath) throw new Error('BD_SUMMARY 환경변수 필요');

  const token = await login(base, process.env.TEST_USER_EMAIL, process.env.TEST_USER_PASSWORD);
  const bd = parseSummary(summaryPath);
  const results = [];

  for (const row of bd) {
    const projectId = map[row.fixture];
    if (!projectId) {
      results.push({ fixture: row.fixture, verdict: 'NO_PROJECT(스캔 필요)' });
      continue;
    }
    const count = await componentCount(base, token, projectId);
    const bomMin = parseInt(row.bom_min, 10);
    const verdict =
      Number.isNaN(bomMin) || bomMin < 0
        ? 'SKIP(BOM 미검증)'
        : count == null
          ? 'ERROR(조회 실패)'
          : count >= bomMin
            ? 'PASS'
            : 'FAIL';
    results.push({
      fixture: row.fixture,
      bd_detector: row.detector_expected,
      bd_bom_min: row.bom_min,
      trustedoss_components: count,
      verdict,
    });
  }

  console.table(results);
  const fails = results.filter((r) => r.verdict === 'FAIL');
  if (fails.length) {
    console.error(`\n❌ Black Duck 대비 BOM 미달 ${fails.length}건 — false negative 의심`);
    process.exit(1);
  }
  console.log('\n✅ Black Duck 비교 완료 (미달 0)');
})();
