#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Third-party notices for the frontend bundle.
 *
 * The production build ships code from its npm dependencies. MIT and BSD-3-Clause
 * (and the other licences in that tree) require the copyright notice and licence
 * text to travel with every copy, and the copy that leaves the image is the one a
 * browser downloads. This script walks the PRODUCTION dependency tree recorded in
 * package-lock.json (every entry that is not dev-only) and writes
 * `dist/licenses/frontend-third-party.txt` with name, version, licence id and the
 * full licence text of each package.
 *
 * Licence text comes from the package's own LICENSE / LICENCE / COPYING file. A
 * few packages ship none (the npm tarball of @primer/react has no LICENSE); for
 * those the upstream text is committed under `licenses/<name>.txt`, with `/`
 * written as `-` (`@primer/react` -> `licenses/@primer-react.txt`), or a
 * scope-wide `licenses/<scope>.txt` for a family such as @radix-ui. A package with
 * neither fails the build and is named in the error, so a new dependency cannot
 * ship without its notice.
 *
 * Usage
 *   node scripts/collect-licenses.mjs                # writes dist/licenses/...
 *   node scripts/collect-licenses.mjs --out <file>   # writes <file> instead
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..");
const OUTPUT_RELATIVE = path.join("dist", "licenses", "frontend-third-party.txt");

const LICENSE_FILE_RX = /^(licen[cs]e|copying)(\.(md|txt|markdown))?$/i;

/** `node_modules/a/node_modules/@s/b` -> `@s/b`. */
export function packageNameFromKey(key) {
  const marker = "node_modules/";
  const at = key.lastIndexOf(marker);
  return at < 0 ? key : key.slice(at + marker.length);
}

/**
 * The production packages of a package-lock.json (lockfileVersion 2 or 3).
 * Dev-only entries, the root project and workspace links are left out.
 *
 * @returns {Array<{key: string, name: string, version: string, license: string | undefined, optional: boolean}>}
 */
export function productionPackages(lock) {
  const out = [];
  for (const [key, entry] of Object.entries(lock.packages ?? {})) {
    if (key === "" || entry.link || entry.dev) continue;
    out.push({
      key,
      name: entry.name ?? packageNameFromKey(key),
      version: entry.version,
      license: normaliseLicense(entry.license),
      optional: Boolean(entry.optional),
    });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
}

/** Lockfiles carry a string; old package.json files carry `{type}` or an array. */
export function normaliseLicense(value) {
  if (!value) return undefined;
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((v) => normaliseLicense(v)).filter(Boolean).join(" OR ") || undefined;
  }
  if (typeof value === "object" && typeof value.type === "string") return value.type;
  return undefined;
}

/** The licence file among a directory listing, preferring LICENSE over COPYING. */
export function pickLicenseFile(fileNames) {
  const hits = fileNames.filter((n) => LICENSE_FILE_RX.test(n));
  const rank = (n) => (/^licen[cs]e/i.test(n) ? 0 : 1);
  return hits.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))[0];
}

/** `@primer/react` -> `@primer-react.txt`. */
export function fallbackFileName(name) {
  return `${name.replace(/\//g, "-")}.txt`;
}

/**
 * The committed files that may stand in for a package's own licence file, most
 * specific first: the package, then (for a scoped package) the whole scope, for
 * families such as @radix-ui that share one upstream licence.
 */
export function fallbackCandidates(name) {
  const scope = name.startsWith("@") ? name.split("/")[0] : undefined;
  return scope ? [fallbackFileName(name), `${scope}.txt`] : [fallbackFileName(name)];
}

/** One entry of the output file. */
export function formatEntry({ name, version, license, text }) {
  const rule = "=".repeat(78);
  return `${rule}\n${name}@${version}\nLicense: ${license}\n${rule}\n\n${text.trim()}\n`;
}

/**
 * Resolve every production package to an entry, or report the ones that cannot
 * be resolved. File access goes through `fsApi` so tests can use a fake tree.
 *
 * @returns {{entries: Array<{name, version, license, text, source}>, problems: string[]}}
 */
export function collect(lock, { root = FRONTEND_ROOT, fsApi = fs } = {}) {
  const entries = [];
  const problems = [];
  for (const pkg of productionPackages(lock)) {
    const dir = path.join(root, pkg.key);
    const label = `${pkg.name}@${pkg.version}`;
    if (!fsApi.existsSync(dir)) {
      // Optional packages are installed per platform; the ones for other
      // platforms are not part of this build.
      if (!pkg.optional) problems.push(`${label}: not installed at ${pkg.key}, run npm ci`);
      continue;
    }

    let license = pkg.license;
    if (!license) {
      try {
        license = normaliseLicense(JSON.parse(fsApi.readFileSync(path.join(dir, "package.json"), "utf8")).license);
      } catch {
        // reported below
      }
    }
    if (!license) {
      problems.push(`${label}: no licence id in package-lock.json or package.json`);
      continue;
    }

    const own = pickLicenseFile(fsApi.readdirSync(dir));
    let text;
    let source;
    if (own) {
      text = fsApi.readFileSync(path.join(dir, own), "utf8");
      source = own;
    } else {
      const found = fallbackCandidates(pkg.name).find((f) =>
        fsApi.existsSync(path.join(root, "licenses", f)),
      );
      if (found) {
        text = fsApi.readFileSync(path.join(root, "licenses", found), "utf8");
        source = `licenses/${found}`;
      }
    }
    if (!text || !text.trim()) {
      problems.push(
        `${label} (${license}): ships no licence file and licenses/${fallbackFileName(pkg.name)} does not exist`,
      );
      continue;
    }
    entries.push({ name: pkg.name, version: pkg.version, license, text, source });
  }
  return { entries, problems };
}

export function render(entries) {
  const header =
    "Third-party software in the TRUSCA frontend bundle\n" +
    `${entries.length} packages from the production dependency tree, with their licences.\n\n`;
  return header + entries.map(formatEntry).join("\n");
}

function main() {
  const lock = JSON.parse(fs.readFileSync(path.join(FRONTEND_ROOT, "package-lock.json"), "utf8"));
  const { entries, problems } = collect(lock);
  if (problems.length > 0) {
    console.error("collect-licenses: cannot build the third-party notices for these packages:\n");
    for (const p of problems) console.error(`  ${p}`);
    console.error(
      "\nAdd the upstream licence text as apps/frontend/licenses/<name>.txt (a '/' in the name becomes '-').",
    );
    return 1;
  }
  const outArg = process.argv.indexOf("--out");
  const out =
    outArg > 0 && process.argv[outArg + 1]
      ? path.resolve(process.argv[outArg + 1])
      : path.join(FRONTEND_ROOT, OUTPUT_RELATIVE);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, render(entries));
  const fromFallback = entries.filter((e) => e.source.startsWith("licenses/")).length;
  console.log(
    `collect-licenses: ${entries.length} packages -> ${path.relative(FRONTEND_ROOT, out) || out} (${fromFallback} from licenses/).`,
  );
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  process.exit(main());
}
