#!/usr/bin/env node
/**
 * scan-result-*.json 들을 모아 {fixture: projectId} 매핑을 stdout으로 출력.
 * compare-bd.js의 FIXTURE_PROJECT_MAP 입력으로 사용.
 * 사용: FIXTURE_PROJECT_MAP=$(node scripts/collect-scan-map.js)
 */
const fs = require('node:fs');

const dir = process.argv[2] || '.';
const map = {};
for (const f of fs.readdirSync(dir).filter((f) => /^scan-result-.*\.json$/.test(f))) {
  try {
    const r = JSON.parse(fs.readFileSync(`${dir}/${f}`, 'utf8'));
    if (r.fixture && r.projectId) map[r.fixture] = r.projectId;
  } catch {
    /* skip 손상 파일 */
  }
}
process.stdout.write(JSON.stringify(map));
