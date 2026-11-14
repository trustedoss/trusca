/**
 * scripts/collect-licenses.mjs: the third-party notices written into the build.
 *
 * The pure parts are driven with a fake file tree. One test runs the collector
 * over the real lockfile and node_modules, which is what stops a new dependency
 * without a licence file from reaching a build.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  collect,
  fallbackCandidates,
  formatEntry,
  normaliseLicense,
  packageNameFromKey,
  pickLicenseFile,
  productionPackages,
  render,
  type FsApi,
} from "../../../scripts/collect-licenses.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");

/** A fake disk: path -> file text, with directories implied by the paths. */
function fakeFs(files: Record<string, string>): FsApi {
  const norm = (p: string) => p.split(path.sep).join("/");
  return {
    existsSync: (p) => {
      const n = norm(p);
      return Object.keys(files).some((f) => f === n || f.startsWith(`${n}/`));
    },
    readFileSync: (p) => {
      const n = norm(p);
      if (!(n in files)) throw new Error(`ENOENT ${n}`);
      return files[n];
    },
    readdirSync: (p) => {
      const n = `${norm(p)}/`;
      return [...new Set(Object.keys(files).filter((f) => f.startsWith(n)).map((f) => f.slice(n.length).split("/")[0]))];
    },
  };
}

const lock = {
  packages: {
    "": { name: "app" },
    "node_modules/zeta": { version: "1.0.0", license: "MIT" },
    "node_modules/alpha": { version: "2.0.0", license: "ISC" },
    "node_modules/devtool": { version: "3.0.0", license: "MIT", dev: true },
    "node_modules/alpha/node_modules/@s/inner": { version: "1.1.0", license: "MIT" },
    "node_modules/self": { link: true, resolved: "../x" },
  },
};

describe("package selection", () => {
  it("names nested and scoped packages", () => {
    expect(packageNameFromKey("node_modules/a/node_modules/@s/b")).toBe("@s/b");
    expect(packageNameFromKey("node_modules/plain")).toBe("plain");
  });

  it("keeps production packages only, sorted, without the root or links", () => {
    expect(productionPackages(lock).map((p) => `${p.name}@${p.version}`)).toEqual([
      "@s/inner@1.1.0",
      "alpha@2.0.0",
      "zeta@1.0.0",
    ]);
  });

  it("reads the three spellings of a licence field", () => {
    expect(normaliseLicense("MIT")).toBe("MIT");
    expect(normaliseLicense({ type: "ISC" })).toBe("ISC");
    expect(normaliseLicense([{ type: "MIT" }, { type: "Apache-2.0" }])).toBe("MIT OR Apache-2.0");
    expect(normaliseLicense(undefined)).toBeUndefined();
  });
});

describe("licence file lookup", () => {
  it("accepts the usual names and prefers LICENSE over COPYING", () => {
    expect(pickLicenseFile(["README.md", "COPYING", "LICENSE.md"])).toBe("LICENSE.md");
    expect(pickLicenseFile(["LICENCE"])).toBe("LICENCE");
    expect(pickLicenseFile(["README.md", "license-checker.js", "index.js"])).toBeUndefined();
  });

  it("looks for a package file, then a scope file", () => {
    expect(fallbackCandidates("@primer/react")).toEqual(["@primer-react.txt", "@primer.txt"]);
    expect(fallbackCandidates("dlv")).toEqual(["dlv.txt"]);
  });
});

describe("collect", () => {
  const root = "/app";
  it("uses the package's own file, then the committed fallback, then the scope file", () => {
    const { entries, problems } = collect(lock, {
      root,
      fsApi: fakeFs({
        "/app/node_modules/zeta/LICENSE": "ZETA TEXT",
        "/app/node_modules/alpha/package.json": "{}",
        "/app/licenses/alpha.txt": "ALPHA FALLBACK",
        "/app/node_modules/alpha/node_modules/@s/inner/package.json": "{}",
        "/app/licenses/@s.txt": "SCOPE FALLBACK",
      }),
    });
    expect(problems).toEqual([]);
    const by = Object.fromEntries(entries.map((e) => [e.name, e]));
    expect(by.zeta.text).toBe("ZETA TEXT");
    expect(by.zeta.source).toBe("LICENSE");
    expect(by.alpha.text).toBe("ALPHA FALLBACK");
    expect(by.alpha.source).toBe("licenses/alpha.txt");
    expect(by["@s/inner"].text).toBe("SCOPE FALLBACK");
  });

  it("names every package with no licence text at all", () => {
    const { entries, problems } = collect(lock, {
      root,
      fsApi: fakeFs({
        "/app/node_modules/zeta/LICENSE": "ZETA",
        "/app/node_modules/alpha/package.json": "{}",
        "/app/node_modules/alpha/node_modules/@s/inner/package.json": "{}",
      }),
    });
    expect(entries.map((e) => e.name)).toEqual(["zeta"]);
    expect(problems).toHaveLength(2);
    expect(problems.join("\n")).toContain("alpha@2.0.0");
    expect(problems.join("\n")).toContain("@s/inner@1.1.0");
  });

  it("treats a blank licence file as missing", () => {
    const { problems } = collect({ packages: { "node_modules/z": { version: "1.0.0", license: "MIT" } } }, {
      root,
      fsApi: fakeFs({ "/app/node_modules/z/LICENSE": "  \n" }),
    });
    expect(problems).toHaveLength(1);
  });

  it("skips an optional package that is not installed and reports a required one", () => {
    const { problems } = collect(
      {
        packages: {
          "node_modules/native-other-os": { version: "1.0.0", license: "MIT", optional: true },
          "node_modules/required": { version: "1.0.0", license: "MIT" },
        },
      },
      { root, fsApi: fakeFs({}) },
    );
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("required@1.0.0");
  });

  it("reads the licence id from package.json when the lockfile has none", () => {
    const { entries } = collect(
      { packages: { "node_modules/q": { version: "1.0.0" } } },
      {
        root,
        fsApi: fakeFs({
          "/app/node_modules/q/LICENSE": "Q",
          "/app/node_modules/q/package.json": JSON.stringify({ license: "BSD-3-Clause" }),
        }),
      },
    );
    expect(entries[0].license).toBe("BSD-3-Clause");
  });

  it("renders name, version, licence id and the text", () => {
    const out = render([{ name: "p", version: "1.2.3", license: "MIT", text: "THE TEXT\n", source: "LICENSE" }]);
    expect(out).toContain("p@1.2.3\nLicense: MIT");
    expect(out).toContain("THE TEXT");
    expect(formatEntry({ name: "p", version: "1", license: "MIT", text: " x " })).toContain("\nx\n");
  });
});

describe("the real dependency tree", () => {
  const realLock = JSON.parse(fs.readFileSync(path.join(FRONTEND_ROOT, "package-lock.json"), "utf8"));
  const result = collect(realLock);

  it("resolves a licence text for every production package", () => {
    expect(result.problems).toEqual([]);
    expect(result.entries.length).toBeGreaterThan(100);
  });

  it("lists the Primer packages and the BSD-3-Clause packages they pull in", () => {
    const names = new Set(result.entries.map((e) => e.name));
    for (const n of ["@primer/react", "@primer/primitives", "@primer/octicons-react", "@lit-labs/react", "@lit-labs/ssr-dom-shim", "@oddbird/popover-polyfill"]) {
      expect(names.has(n), n).toBe(true);
    }
    const bsd = result.entries.filter((e) => ["@lit-labs/react", "@lit-labs/ssr-dom-shim", "@oddbird/popover-polyfill"].includes(e.name));
    expect(bsd.map((e) => e.license)).toEqual(["BSD-3-Clause", "BSD-3-Clause", "BSD-3-Clause"]);
    for (const e of bsd) expect(e.text).toContain("Redistributions in binary form must reproduce");
  });

  it("carries the holder line for @primer/react", () => {
    const primer = result.entries.find((e) => e.name === "@primer/react");
    expect(primer?.source).toBe("licenses/@primer-react.txt");
    expect(primer?.text).toMatch(/^MIT License\s+Copyright \(c\) \d{4} GitHub, Inc\./);
  });
});
