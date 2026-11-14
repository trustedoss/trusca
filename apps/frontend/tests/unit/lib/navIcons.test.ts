/**
 * Every `NAV_ICON["/path"]` lookup in the shell must hit a real entry.
 *
 * The map is indexed by string, so a misspelt path type-checks and then hands
 * `undefined` to a JSX element: the sidebar or the palette crashes at render
 * for the one role that sees that row. Reading the lookups from the two
 * consumers' source covers rows the unit tests never draw (admin-only,
 * feature-gated).
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { NAV_ICON } from "@/lib/navIcons";

const ROOT = path.join(__dirname, "..", "..", "..", "src", "components");
const lookups = (file: string): string[] =>
  [...fs.readFileSync(path.join(ROOT, file), "utf8").matchAll(/NAV_ICON\["([^"]+)"\]/g)].map(
    (m) => m[1],
  );

describe("NAV_ICON", () => {
  it.each(["AppShell.tsx", "CommandMenu.tsx"])("%s only asks for paths the map has", (file) => {
    const paths = lookups(file);
    expect(paths.length).toBeGreaterThan(5);
    for (const p of paths) {
      expect(NAV_ICON[p], `${file} asks for ${p}`).toBeDefined();
    }
  });
});
