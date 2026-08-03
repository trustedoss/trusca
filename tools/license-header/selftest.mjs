#!/usr/bin/env node
/**
 * license-header selftest — asserts the gate's own logic.
 *
 * Run: node tools/license-header/selftest.mjs
 *
 * Why a selftest for a 200-line linter: the glob translator shipped broken on
 * its first draft (an empty-regex replace that matched at every position), and
 * an exclusion rule that silently matches nothing is the one failure mode that
 * cannot be noticed from the outside — it stamps our copyright onto someone
 * else's file and reports success.
 */
import { strict as assert } from "node:assert";

import {
  hasHeader,
  headerFor,
  isExcluded,
  isTargeted,
  loadExcluded,
  withHeader,
} from "./lint.mjs";

const excluded = loadExcluded();
let passed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    console.error(`FAIL  ${name}\n      ${err.message}`);
    process.exitCode = 1;
  }
}

// --- exclusion globs actually match -----------------------------------------
// A glob that matches nothing is worse than no glob: the gate reports a clean
// run while stamping third-party files.
check("glob: alembic versions excluded", () => {
  assert.equal(
    isExcluded("apps/backend/alembic/versions/0042_add_thing.py", excluded),
    true,
  );
});

check("glob: alembic env.py NOT excluded (only versions/ is generated)", () => {
  assert.equal(isExcluded("apps/backend/alembic/env.py", excluded), false);
});

check("glob: license texts excluded", () => {
  assert.equal(
    isExcluded("apps/backend/services/license_texts/AGPL-3.0-only.txt", excluded),
    true,
  );
});

check("glob: verify-specs excluded at any depth", () => {
  assert.equal(isExcluded("tests/verify-specs/specs/m3-audit.json", excluded), true);
  assert.equal(isExcluded("tests/verify-specs/verify-runner.mjs", excluded), true);
});

check("glob: a sibling of an excluded dir is NOT excluded", () => {
  assert.equal(isExcluded("tests/verify-other/thing.json", excluded), false);
});

check("path: vendored data files excluded", () => {
  assert.equal(isExcluded("apps/backend/services/g7_registry.json", excluded), true);
  assert.equal(
    isExcluded("apps/backend/services/eol/eol_purl_map.json", excluded),
    true,
  );
  assert.equal(
    isExcluded("apps/backend/services/regulation_crosswalk.json", excluded),
    true,
  );
});

check("path: dual-copyright TS excluded from auto-stamping", () => {
  assert.equal(
    isExcluded("apps/frontend/src/features/scan/lib/g7Conformance.ts", excluded),
    true,
  );
  assert.equal(
    isExcluded("apps/frontend/src/features/scan/lib/g7Guidance.ts", excluded),
    true,
  );
});

check("path: the hand-written jq/shell ports are NOT excluded (ours)", () => {
  for (const rel of [
    "apps/backend/services/g7_conformance.py",
    "apps/backend/services/license_flags.py",
    "apps/backend/services/eol/eol_catalog.py",
  ]) {
    assert.equal(isExcluded(rel, excluded), false, rel);
  }
});

// --- scope ------------------------------------------------------------------
check("scope: tests are out of scope", () => {
  assert.equal(isTargeted("apps/backend/tests/unit/test_thing.py", excluded), false);
  assert.equal(isTargeted("apps/frontend/src/x/tests/a.ts", excluded), false);
});

check("scope: ungated extensions are out of scope", () => {
  assert.equal(isTargeted("apps/backend/services/data.json", excluded), false);
  assert.equal(isTargeted("apps/backend/requirements.txt", excluded), false);
});

check("scope: paths outside the scan roots are out of scope", () => {
  assert.equal(isTargeted("tools/ko-style/lint.mjs", excluded), false);
  assert.equal(isTargeted("deploy/hetzner/remote-deploy.sh", excluded), false);
  assert.equal(isTargeted("docs-site/docusaurus.config.ts", excluded), false);
  assert.equal(isTargeted(".github/workflows/ci.yml", excluded), false);
});

check("scope: operator-facing artifacts ARE in scope", () => {
  // These reach a user as individual files, not inside an image — the argument
  // for a per-file header is stronger here than for application code.
  for (const rel of [
    "scripts/install.sh",
    "scripts/restore.sh",
    "actions/scan/action.yml",
    "docker-compose.yml",
    "charts/trustedoss/values.yaml",
    "charts/trustedoss/templates/deployment-backend.yaml",
  ]) {
    assert.equal(isTargeted(rel, excluded), true, rel);
  }
});

check("scope: the chart's own license copies are not stamped", () => {
  // LICENSE / NOTICE carry no extension and THIRD_PARTY_NOTICES.md is .md, so
  // none is a gated extension. Stamping them would also break the byte-equality
  // contract in test_license_distribution.py.
  for (const rel of [
    "charts/trustedoss/LICENSE",
    "charts/trustedoss/NOTICE",
    "charts/trustedoss/THIRD_PARTY_NOTICES.md",
    "charts/trustedoss/templates/NOTES.txt",
  ]) {
    assert.equal(isTargeted(rel, excluded), false, rel);
  }
});

check("scope: cloud-init is excluded (first line is load-bearing)", () => {
  assert.equal(isTargeted("scripts/hetzner-cloud-init.yaml", excluded), false);
});

// --- Helm comment form -------------------------------------------------------
check("helm: templates get a Helm comment, not a YAML one", () => {
  const header = headerFor("charts/trustedoss/templates/deployment-backend.yaml");
  assert.match(header, /^\{\{- \/\*\n/);
  assert.match(header, /\*\/ -\}\}$/);
  assert.equal(header.includes("# SPDX"), false);
  assert.equal(hasHeader(`${header}\napiVersion: apps/v1\n`), true);
});

check("helm: _helpers.tpl also gets the Helm form", () => {
  assert.match(headerFor("charts/trustedoss/templates/_helpers.tpl"), /^\{\{- \/\*/);
});

check("helm: chart files OUTSIDE templates/ get a YAML comment", () => {
  assert.match(headerFor("charts/trustedoss/values.yaml"), /^# SPDX/);
  assert.match(headerFor("charts/trustedoss/Chart.yaml"), /^# SPDX/);
});

check("shell: .sh gets a # comment, below the shebang", () => {
  assert.match(headerFor("scripts/install.sh"), /^# SPDX/);
  const out = withHeader('#!/usr/bin/env bash\nset -euo pipefail\n', "scripts/install.sh");
  assert.equal(out.split("\n")[0], "#!/usr/bin/env bash");
  assert.match(out.split("\n")[1], /^# SPDX/);
});

// --- header detection -------------------------------------------------------
check("detect: a stamped python file is recognised", () => {
  const text = "# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 TRUSCA contributors\n\"\"\"Doc.\"\"\"\n";
  assert.equal(hasHeader(text), true);
});

check("detect: any four-digit year is accepted", () => {
  const text = "// SPDX-License-Identifier: Apache-2.0\n// Copyright 2031 TRUSCA contributors\n";
  assert.equal(hasHeader(text), true);
});

check("detect: a year range is accepted", () => {
  const text = "// SPDX-License-Identifier: Apache-2.0\n// Copyright 2026-2031 TRUSCA contributors\n";
  assert.equal(hasHeader(text), true);
});

check("detect: SPDX without the copyright line is NOT enough", () => {
  assert.equal(hasHeader("# SPDX-License-Identifier: Apache-2.0\n"), false);
});

check("detect: a different holder is NOT our header", () => {
  const text = "// SPDX-License-Identifier: Apache-2.0\n// Copyright 2026 Someone Else\n";
  assert.equal(hasHeader(text), false);
});

check("detect: a header buried past the scan window does not count", () => {
  const text = `${"\n".repeat(10)}# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 TRUSCA contributors\n`;
  assert.equal(hasHeader(text), false);
});

check("detect: the dual-copyright form still reads as stamped", () => {
  const text =
    "// SPDX-License-Identifier: Apache-2.0\n" +
    "// Copyright 2026 SK Telecom Co., Ltd.\n" +
    "// Copyright 2026 TRUSCA contributors\n";
  assert.equal(hasHeader(text), true);
});

// --- comment syntax ---------------------------------------------------------
check("syntax: python uses #, typescript uses //", () => {
  assert.match(headerFor("a/b.py"), /^# SPDX-License-Identifier: Apache-2\.0\n# Copyright/);
  assert.match(headerFor("a/b.ts"), /^\/\/ SPDX-License-Identifier: Apache-2\.0\n\/\/ Copyright/);
  assert.match(headerFor("a/b.tsx"), /^\/\/ SPDX/);
});

// --- insertion --------------------------------------------------------------
check("insert: goes above a module docstring", () => {
  const out = withHeader('"""Doc."""\n\nimport os\n', "a/b.py");
  assert.equal(
    out,
    '# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 TRUSCA contributors\n"""Doc."""\n\nimport os\n',
  );
});

check("insert: goes BELOW a shebang", () => {
  const out = withHeader('#!/usr/bin/env python3\n"""Doc."""\n', "a/b.py");
  assert.equal(out.split("\n")[0], "#!/usr/bin/env python3");
  assert.match(out.split("\n")[1], /^# SPDX-License-Identifier/);
});

check("insert: is idempotent", () => {
  const once = withHeader('"""Doc."""\n', "a/b.py");
  assert.equal(withHeader(once, "a/b.py"), once);
});

check("insert: preserves the original body verbatim", () => {
  const body = "import { x } from 'y';\n\nexport const z = 1;\n";
  assert.equal(withHeader(body, "a/b.ts").endsWith(body), true);
});

check("insert: leaves a dual-copyright file untouched", () => {
  const body =
    "// SPDX-License-Identifier: Apache-2.0\n" +
    "// Copyright 2026 SK Telecom Co., Ltd.\n" +
    "// Copyright 2026 TRUSCA contributors\n" +
    "export const a = 1;\n";
  assert.equal(withHeader(body, "a/b.ts"), body);
});

if (process.exitCode) {
  console.error("\nlicense-header selftest: FAILED");
} else {
  console.log(`license-header selftest: ${passed} check(s) passed.`);
}
