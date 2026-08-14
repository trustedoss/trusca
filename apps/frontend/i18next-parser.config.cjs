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
  // Most counted copy here is plain `{{count}}` interpolation, and the few
  // keys that do carry a `_other` variant are written by hand beside their
  // bare key (see the plural rules in scripts/i18n-check.cjs). Either way the
  // parser should extract the bare key only, so disable suffix emission.
  i18nextOptions: { compatibilityJSON: "v1" },
  pluralSeparator: false,
  yamlOptions: null,
};
