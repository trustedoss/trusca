#!/usr/bin/env node
/**
 * dump_postman_collection: convert the committed OpenAPI spec into a
 * Postman collection for the docs site (C6).
 *
 * Mirrors scripts/dump_openapi.py's contract: a *committed* static artifact
 * (docs-site/static/postman/) regenerated from the OpenAPI snapshot, with a
 * --check mode CI runs to fail a PR whose committed copy has drifted.
 *
 * openapi-to-postmanv2 already turns C7's securitySchemes + per-operation
 * security into correct per-request bearer auth and public-endpoint
 * no-auth, so nothing below re-derives that. What it does NOT produce is
 * anything a person could actually run start-to-finish: generated example
 * values are schema-shaped nonsense ("string", a random email), and nothing
 * carries a project id from one request into the next. postProcess() below
 * fixes exactly those two things, and only for the four-step scenario named
 * in the C6 backlog entry (login -> create project -> trigger scan ->
 * download SBOM); every other endpoint keeps openapi-to-postmanv2's
 * defaults, which are perfectly fine for "show me the shape of this call".
 *
 * Login's example credential (admin@demo.trustedoss.dev / DemoTest2026!) is
 * the project's own published demo login (quickstart.md, live-demo.md,
 * auth-and-profile.md), not a new disclosure, and it only authenticates
 * against a demo/dev instance seeded with scripts/seed_demo.py in the first
 * place (env-guarded there).
 *
 * Usage:
 *   node tools/postman/dump_postman_collection.mjs           # write the collection
 *   node tools/postman/dump_postman_collection.mjs --check   # exit 1 if stale
 */
import { convert } from "openapi-to-postmanv2";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const SPEC_PATH = path.join(REPO_ROOT, "docs-site", "static", "openapi.json");
const OUTPUT_PATH = path.join(
  REPO_ROOT,
  "docs-site",
  "static",
  "postman",
  "trusca.postman_collection.json",
);

const DEFAULT_BASE_URL = "https://trustedoss.example.com";

// (method, path template as Postman renders it with `:name` path params,
// name substring) -> patch function. The name substring guards against a
// path collision (there is only one today) picking the wrong operation.
const SCENARIO_PATCHES = [
  {
    method: "POST",
    urlSuffix: "/auth/login",
    patch(item) {
      item.request.body = {
        mode: "raw",
        options: { raw: { language: "json" } },
        raw: JSON.stringify(
          { email: "admin@demo.trustedoss.dev", password: "DemoTest2026!" },
          null,
          2,
        ),
      };
      item.event = item.event ?? [];
      item.event.push({
        listen: "test",
        script: {
          type: "text/javascript",
          exec: [
            "// Carries the access token into every later request in this",
            "// collection that authenticates with {{bearerToken}}.",
            "const body = pm.response.json();",
            "if (body.access_token) {",
            '    pm.collectionVariables.set("bearerToken", body.access_token);',
            "}",
          ],
        },
      });
    },
  },
  {
    method: "POST",
    urlSuffix: "/v1/projects",
    patch(item) {
      item.request.body = {
        mode: "raw",
        options: { raw: { language: "json" } },
        raw: JSON.stringify(
          {
            team_id: "8f0c1e2a-...your team UUID...",
            name: "checkout-service",
            slug: "checkout-service",
            description: "Storefront checkout service",
            git_url: "https://github.com/acme/checkout-service.git",
          },
          null,
          2,
        ),
      };
      item.event = item.event ?? [];
      item.event.push({
        listen: "test",
        script: {
          type: "text/javascript",
          exec: [
            "// Carries the new project's id into the scan-trigger and",
            "// SBOM-download requests below.",
            "const body = pm.response.json();",
            "if (body.id) {",
            '    pm.collectionVariables.set("projectId", body.id);',
            "}",
          ],
        },
      });
    },
  },
  {
    method: "POST",
    urlSuffix: "/v1/projects/:project_id/scans",
    patch(item) {
      setPathVariable(item, "project_id", "{{projectId}}");
      item.request.body = {
        mode: "raw",
        options: { raw: { language: "json" } },
        raw: JSON.stringify({ kind: "source" }, null, 2),
      };
      item.event = item.event ?? [];
      item.event.push({
        listen: "test",
        script: {
          type: "text/javascript",
          exec: [
            "// Carries the scan id into whatever polls it next (not wired",
            "// to a request here: a scan runs for minutes, not something",
            "// this one-shot chain should block on).",
            "const body = pm.response.json();",
            "if (body.id) {",
            '    pm.collectionVariables.set("scanId", body.id);',
            "}",
          ],
        },
      });
    },
  },
  {
    method: "GET",
    urlSuffix: "/v1/projects/:project_id/sbom",
    patch(item) {
      setPathVariable(item, "project_id", "{{projectId}}");
      const url = item.request.url;
      url.query = url.query ?? [];
      const existing = url.query.find((q) => q.key === "format");
      if (existing) {
        existing.value = "cyclonedx-json";
      } else {
        url.query.push({ key: "format", value: "cyclonedx-json" });
      }
      url.raw = buildRawUrl(url);
    },
  },
];

function urlSuffixOf(item) {
  const url = item.request?.url;
  if (!url) return null;
  const pathParts = (url.path ?? []).join("/");
  return "/" + pathParts;
}

function setPathVariable(item, key, value) {
  const variables = item.request.url.variable ?? [];
  const existing = variables.find((v) => v.key === key);
  if (existing) existing.value = value;
  item.request.url.variable = variables;
}

function buildRawUrl(url) {
  const host = (url.host ?? []).join(".");
  const p = (url.path ?? []).join("/");
  const query = (url.query ?? [])
    .map((q) => `${q.key}=${q.value}`)
    .join("&");
  return `{{baseUrl}}/${p}${query ? "?" + query : ""}`;
}

function walk(items, fn) {
  for (const item of items) {
    if (item.item) {
      walk(item.item, fn);
    } else {
      fn(item);
    }
  }
}

function postProcess(collection) {
  collection.info.description =
    "TRUSCA REST API. Run 'Login', then 'Create a project', then 'Trigger a " +
    "scan', then 'Export SBOM' in order: each carries its result into the " +
    "next via collection variables (bearerToken / projectId / scanId). " +
    "Every other request in this collection needs the same bearerToken " +
    "(set it directly, or run Login first) and its own path/query values.";

  collection.variable = [
    {
      key: "baseUrl",
      value: DEFAULT_BASE_URL,
      type: "string",
      description: "Your TRUSCA deployment's base URL, no trailing slash.",
    },
    {
      key: "bearerToken",
      value: "",
      type: "string",
      description:
        "A JWT access token (run 'Login' to fill this in) or a tos_... API key.",
    },
    { key: "projectId", value: "", type: "string" },
    { key: "scanId", value: "", type: "string" },
  ];

  const remaining = new Set(SCENARIO_PATCHES.map((_, i) => i));
  walk(collection.item, (item) => {
    const suffix = urlSuffixOf(item);
    if (!suffix) return;
    for (const i of remaining) {
      const target = SCENARIO_PATCHES[i];
      if (item.request.method === target.method && suffix === target.urlSuffix) {
        target.patch(item);
        remaining.delete(i);
        return;
      }
    }
  });
  if (remaining.size > 0) {
    const missed = [...remaining].map(
      (i) => `${SCENARIO_PATCHES[i].method} ${SCENARIO_PATCHES[i].urlSuffix}`,
    );
    throw new Error(
      "dump_postman_collection: scenario request(s) not found in the " +
        `generated collection (renamed or removed?): ${missed.join(", ")}`,
    );
  }

  return collection;
}

function buildCollection() {
  const spec = fs.readFileSync(SPEC_PATH, "utf8");
  return new Promise((resolve, reject) => {
    convert(
      { type: "string", data: spec },
      {
        folderStrategy: "Tags",
        requestParametersResolution: "Example",
        includeAuthInfoInExample: false,
      },
      (err, result) => {
        if (err) return reject(err);
        if (!result.result) return reject(new Error(result.reason));
        resolve(result.output[0].data);
      },
    );
  });
}

function serialize(collection) {
  return JSON.stringify(collection, null, 2) + "\n";
}

async function main() {
  const check = process.argv.includes("--check");
  const collection = postProcess(await buildCollection());
  const text = serialize(collection);

  if (check) {
    if (!fs.existsSync(OUTPUT_PATH)) {
      console.error(
        `Postman collection missing: ${OUTPUT_PATH}\n` +
          "Generate it with: node tools/postman/dump_postman_collection.mjs",
      );
      process.exit(1);
    }
    const committed = fs.readFileSync(OUTPUT_PATH, "utf8");
    if (committed !== text) {
      console.error(
        "Postman collection drift: the committed " +
          "docs-site/static/postman/trusca.postman_collection.json does not " +
          "match the current OpenAPI spec. Regenerate and commit:\n" +
          "    node tools/postman/dump_postman_collection.mjs",
      );
      process.exit(1);
    }
    console.log(`Postman collection is up to date: ${OUTPUT_PATH}`);
    return;
  }

  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, text);
  console.log(`Wrote ${OUTPUT_PATH} (${text.length} bytes)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
