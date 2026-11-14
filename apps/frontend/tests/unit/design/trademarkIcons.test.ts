/**
 * Trademark Octicons are blocked by ESLint (eslint.config.js).
 *
 * The rule is checked by running ESLint itself over snippets, so a config edit
 * that weakens it (a dropped name, a pattern that stops matching) fails here
 * and not only when somebody next imports the icon.
 */
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");

const BLOCKED = [
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

const eslint = new ESLint({ cwd: FRONTEND_ROOT });

async function restrictedImports(code: string, file = "src/probe.tsx") {
  const [result] = await eslint.lintText(code, { filePath: path.join(FRONTEND_ROOT, file) });
  return result.messages.filter((m) => m.ruleId === "no-restricted-imports");
}

// The first lint call loads the whole config and the TypeScript parser.
describe("trademark Octicons", { timeout: 60_000 }, () => {
  it.each(BLOCKED)("blocks %s from the package root", async (name) => {
    const found = await restrictedImports(`import { ${name} } from "@primer/octicons-react";\nexport const x = ${name};\n`);
    expect(found).toHaveLength(1);
  });

  it("blocks a re-export and a namespace import", async () => {
    expect(await restrictedImports(`export { CopilotIcon } from "@primer/octicons-react";\n`)).toHaveLength(1);
    expect(await restrictedImports(`import * as O from "@primer/octicons-react";\nexport const x = O;\n`)).toHaveLength(1);
  });

  it.each([
    "@primer/octicons-react/dist/icons/MarkGithubIcon",
    "@primer/octicons-react/dist/icons/MarkGithubIcon.mjs",
    "@primer/octicons-react/dist/index.esm.mjs",
  ])("blocks the deep path %s", async (spec) => {
    const found = await restrictedImports(`import { MarkGithubIcon } from "${spec}";\nexport const x = MarkGithubIcon;\n`);
    expect(found.length).toBeGreaterThanOrEqual(1);
  });

  it("allows the neutral icons", async () => {
    expect(await restrictedImports(`import { SearchIcon, AlertIcon } from "@primer/octicons-react";\nexport const x = [SearchIcon, AlertIcon];\n`)).toHaveLength(0);
  });

  it("allows the blocked names only in src/features/integrations/GitHubMark.tsx", async () => {
    const code = `import { MarkGithubIcon } from "@primer/octicons-react";\nexport const x = MarkGithubIcon;\n`;
    expect(await restrictedImports(code, "src/features/integrations/GitHubMark.tsx")).toHaveLength(0);
    expect(await restrictedImports(code, "src/features/integrations/GitHubMark2.tsx")).toHaveLength(1);
    expect(await restrictedImports(code, "src/features/integrations/IntegrationsPage.tsx")).toHaveLength(1);
  });
});
