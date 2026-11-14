import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

/**
 * Octicons that depict another company's trademark (logos, product mascots).
 * They are published in the same package as the neutral icons, so a plain
 * import would pass review unnoticed. The only exception is named below.
 */
const TRADEMARK_ICONS = [
  "MarkGithubIcon",
  "LogoGithubIcon",
  "LogoGistIcon",
  "CopilotIcon",
  "CopilotErrorIcon",
  "CopilotWarningIcon",
  "CodespacesIcon",
  "DependabotIcon",
  "HubotIcon",
  "AgentIcon",
];

const TRADEMARK_MESSAGE =
  "This Octicon shows a trademark. Do not import it; if the product needs the mark, add the single allowed file src/features/integrations/GitHubMark.tsx.";

export default tseslint.config(
  {
    ignores: ["dist", "coverage", "node_modules", "tests/e2e/**"],
  },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Deep paths and file forms are blocked as well, or the named list above
    // could be bypassed by importing the icon's own module.
    files: ["**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@primer/octicons-react",
              importNames: TRADEMARK_ICONS,
              message: TRADEMARK_MESSAGE,
            },
          ],
          patterns: [
            {
              group: [
                "@primer/octicons-react/dist/icons/*",
                "@primer/octicons-react/dist/*",
              ],
              message: TRADEMARK_MESSAGE,
            },
          ],
        },
      ],
    },
  },
  {
    // The one file allowed to draw the mark. It does not exist yet; naming the
    // path here keeps the exception to a single file.
    files: ["src/features/integrations/GitHubMark.tsx"],
    rules: { "no-restricted-imports": "off" },
  },
  {
    files: ["tests/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
);
