/**
 * The severity palette lives in six places, and they must agree.
 *
 * `index.css` is the source, but the same five hexes are restated where the
 * stylesheet cannot reach: the backend paints them into PDF and Excel
 * reports, the design-system reference documents them in two languages, the
 * user guide shows them beside the CVSS bands, and the docs site's theme
 * header records them. When Low moved from blue to cyan in 2026-11, each of
 * those was a separate edit that nothing tied together; a report printed in
 * the old blue would have been green everywhere (CLAUDE.md hardening rule 2).
 *
 * Every copy is read from its named definition, not found by searching for a
 * hex: the `--risk-X` declaration in the `:root` block, the `_SEVERITY_COLOR`
 * dict in `report_service.py`, the `--risk-X` rows of the design-system
 * tables, the severity rows of the user-guide table, and the `risk:` lines of
 * the `custom.css` header. Searching for a hex would pass whenever the value
 * appears anywhere, which is the failure this exists to catch.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..");
const read = (rel: string) => fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");

const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
type Palette = Partial<Record<(typeof SEVERITIES)[number], string>>;

const HEX = "#[0-9a-fA-F]{6}";

/** `--risk-X` in the `:root` block of index.css, comments stripped. */
function fromStylesheet(): Palette {
  const css = read("apps/frontend/src/index.css");
  const start = css.indexOf(":root {");
  const end = css.indexOf(".dark {", start);
  const block = css.slice(start, end).replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Palette = {};
  for (const name of SEVERITIES) {
    const m = new RegExp(`--risk-${name}:\\s*(${HEX});`).exec(block);
    if (m) out[name] = m[1].toLowerCase();
  }
  return out;
}

/** The `_SEVERITY_COLOR` dict literal in the report service. */
function fromReportService(): Palette {
  const py = read("apps/backend/services/report_service.py");
  const m = /^_SEVERITY_COLOR\b[^=]*=\s*\{([\s\S]*?)^\}/m.exec(py);
  if (!m) throw new Error("report_service.py no longer defines _SEVERITY_COLOR");
  const out: Palette = {};
  for (const entry of m[1].matchAll(new RegExp(`"([a-z]+)":\\s*"(${HEX})"`, "g"))) {
    if ((SEVERITIES as readonly string[]).includes(entry[1])) {
      out[entry[1] as keyof Palette] = entry[2].toLowerCase();
    }
  }
  return out;
}

/** `| `--risk-X` | `#hex` |` rows, before the dark-theme section. */
function fromDesignSystem(rel: string): Palette {
  const doc = read(rel);
  const dark = /^## .*\bW18\b/m.exec(doc);
  const light = dark ? doc.slice(0, dark.index) : doc;
  const out: Palette = {};
  for (const name of SEVERITIES) {
    const m = new RegExp(
      `^\\|\\s*\`--risk-${name}\`\\s*\\|\\s*\`(${HEX})\``,
      "m",
    ).exec(light);
    if (m) out[name] = m[1].toLowerCase();
  }
  return out;
}

/** `| **Low** | `#hex` |` rows of the user-guide severity table. */
function fromUserGuide(rel: string): Palette {
  const doc = read(rel);
  const out: Palette = {};
  for (const name of SEVERITIES) {
    const label = name[0].toUpperCase() + name.slice(1);
    const m = new RegExp(
      `^\\|\\s*\\*\\*${label}\\*\\*\\s*\\|\\s*\`(${HEX})\``,
      "m",
    ).exec(doc);
    if (m) out[name] = m[1].toLowerCase();
  }
  return out;
}

/** The `risk:` lines of the docs-site theme header comment. */
function fromDocsTheme(): Palette {
  const css = read("docs-site/src/css/custom.css");
  const header = /^\/\*\*([\s\S]*?)\*\//.exec(css);
  if (!header) throw new Error("custom.css lost its header comment");
  const risk = header[1].slice(header[1].indexOf("risk:"));
  const out: Palette = {};
  for (const name of SEVERITIES) {
    const m = new RegExp(`\\b${name}\\s+(${HEX})`).exec(risk);
    if (m) out[name] = m[1].toLowerCase();
  }
  return out;
}

const SOURCES: Array<[string, () => Palette]> = [
  ["report_service.py _SEVERITY_COLOR", fromReportService],
  [
    "design-system.md (EN)",
    () => fromDesignSystem("docs-site/docs/reference/design-system.md"),
  ],
  [
    "design-system.md (KO)",
    () =>
      fromDesignSystem(
        "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/reference/design-system.md",
      ),
  ],
  [
    "user-guide/vulnerabilities.md (EN)",
    () => fromUserGuide("docs-site/docs/user-guide/vulnerabilities.md"),
  ],
  [
    "user-guide/vulnerabilities.md (KO)",
    () =>
      fromUserGuide(
        "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/user-guide/vulnerabilities.md",
      ),
  ],
  ["docs-site custom.css header", fromDocsTheme],
];

describe("severity palette parity", () => {
  const source = fromStylesheet();

  it("reads all five severities from the stylesheet", () => {
    // Vacuous-pass guard: an empty source would make every copy "agree".
    expect(Object.keys(source).sort()).toEqual([...SEVERITIES].sort());
  });

  it.each(SOURCES)("%s carries every severity", (_label, load) => {
    // A copy that lost a row would otherwise compare as a subset and pass.
    expect(Object.keys(load()).sort()).toEqual([...SEVERITIES].sort());
  });

  it.each(SOURCES)("%s matches index.css", (_label, load) => {
    expect(load()).toEqual(source);
  });

  it("states Low as the 2026-11 cyan in every copy", () => {
    // Named on its own because Low is the value that moved. A test that only
    // compared the copies with each other would pass if all six reverted.
    expect(source.low).toBe("#0891b2");
    for (const [, load] of SOURCES) expect(load().low).toBe("#0891b2");
  });
});
