/**
 * i18next-parser configuration — chore A1.
 *
 * Drives both `npm run i18n:extract` (writes the locale JSON files) and
 * `npm run i18n:check` (extracts to a temp dir and diffs against the
 * committed files; CI gate fails on drift).
 *
 * Conventions match `src/lib/i18n.ts`:
 *   - Locales: en, ko (mirror EN/KO simultaneously per CLAUDE.md i18n rule).
 *   - Default namespace: "common".
 *   - Namespace separator: ":". Key separator: ".".
 *   - keepRemoved: false — unused keys are pruned, so a deleted call site
 *     leaves no dead translations behind.
 *
 * Why .cjs: the frontend is type:module, but i18next-parser CLI loads the
 * config with require(); the .cjs extension forces CommonJS resolution.
 */
module.exports = {
  contextSeparator: "_",
  createOldCatalogs: false,
  defaultNamespace: "common",
  defaultValue: "",
  indentation: 2,
  keepRemoved: false,
  keySeparator: ".",
  lexers: {
    js: ["JsxLexer"],
    jsx: ["JsxLexer"],
    ts: ["JsxLexer"],
    tsx: ["JsxLexer"],
    default: ["JavascriptLexer"],
  },
  lineEnding: "auto",
  locales: ["en", "ko"],
  namespaceSeparator: ":",
  output: "src/locales/$LOCALE/$NAMESPACE.json",
  // Match the same source globs as Vite. We exclude the locale JSON, the
  // i18n bootstrap, and the test directory because they don't define new
  // user-facing keys.
  input: ["src/**/*.{ts,tsx}"],
  sort: true,
  verbose: false,
  failOnWarnings: false,
  failOnUpdate: false,
  customValueTemplate: null,
  resetDefaultValueLocale: null,
  // The codebase uses simple `{{count}}` interpolation, not i18next's
  // plural form (no `_one` / `_other` keys). Disable the plural suffix
  // emission so the parser stops asking for keys we don't ship.
  i18nextOptions: { compatibilityJSON: "v1" },
  pluralSeparator: false,
  yamlOptions: null,
};
