#!/usr/bin/env node
/**
 * ko-style catalog self-test. For every rule in patterns.json, asserts that
 * `example_bad` triggers the rule and `example_ok` does not. Also checks the
 * masking guards (code fence, inline code, URL, HTML comment) never fire.
 *
 *   node tools/ko-style/selftest.mjs   →  exit 0 if all pass, 1 otherwise.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { loadRules, lintText } from "./lint.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const raw = JSON.parse(fs.readFileSync(path.join(__dirname, "patterns.json"), "utf8"));
const rules = loadRules();

let failures = 0;
const fail = (msg) => {
  console.error(`  ✗ ${msg}`);
  failures++;
};

for (const rule of raw.rules) {
  const bad = lintText(rule.example_bad, "x.md", rules).some((f) => f.id === rule.id);
  const ok = lintText(rule.example_ok, "x.md", rules).some((f) => f.id === rule.id);
  if (!bad) fail(`${rule.id}: example_bad did not trigger the rule`);
  if (ok) fail(`${rule.id}: example_ok falsely triggered the rule`);
}

// Masking guards: a bad token inside code / URL / comment must NOT be flagged.
//
// The inline-code fixture reads `…`는 with the particle attached. It used to be
// written `…` 는, which is itself the error `latin-particle-space` catches — a
// detail that only surfaced when the mask started keeping the backticks, and
// which is a fair illustration of how easily that spacing slips in.
const guards = [
  ["fenced code", "```\n에 의해 결정\n```"],
  ["inline code", "`에 의해`는 코드 예시입니다."],
  ["url target", "[링크](https://example.com/에-의해)를 참고하세요."],
  ["html comment", "<!-- 에 의해 결정 -->"],
  ["front matter", "---\ntitle: 에 의해\n---\n본문입니다."],
];
for (const [label, text] of guards) {
  const hits = lintText(text, "x.md", rules);
  if (hits.length) fail(`masking guard '${label}' leaked ${hits.length} finding(s)`);
}

// Positive guards: things the masking must NOT hide.
//
// The inline-code mask blanks the span's content but keeps its delimiters,
// because the most common form of a mis-spaced particle in these guides sits
// immediately after a code span (357 occurrences when the rule was written).
// Blanking the backticks too made the linter report a clean run over every one
// of them. This asserts the boundary is still visible, so re-masking the
// delimiters fails here instead of silently costing the catalogue a rule.
const positives = [
  ["particle after a code span", "`SBOM` 은 두 포맷으로 제공합니다.", "latin-particle-space"],
  ["particle after emphasis", "**설정** 을 엽니다.", "latin-particle-space"],
  ["particle after a latin word", "SBOM 은 두 포맷으로 제공합니다.", "latin-particle-space"],
];
for (const [label, text, id] of positives) {
  const hits = lintText(text, "x.md", rules).filter((f) => f.id === id);
  if (!hits.length) fail(`positive guard '${label}' was not caught by ${id}`);
}

if (failures) {
  console.error(`\nko-style self-test FAILED with ${failures} failure(s).`);
  process.exit(1);
}
console.log(
  `ko-style self-test OK (${raw.rules.length} rules + ${guards.length} masking guards ` +
    `+ ${positives.length} positive guards).`,
);
