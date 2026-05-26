// Exploratory walkthrough — registers a fresh user and walks every
// authenticated route, capturing console errors, page errors, failed
// network calls, and interaction bugs. Temporary; delete after.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://localhost:5173";
const ts = Date.now();
const email = `walk-${ts}@example.com`;
const password = "walkthrough8"; // >= 8 (new floor)
const shotDir = `/tmp/walkthrough-${ts}`;
mkdirSync(shotDir, { recursive: true });

const findings = [];
const browser = await chromium.launch();
// locale ko-KR to reproduce the user's Korean browser-locale date widgets
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  locale: "ko-KR",
});
const page = await ctx.newPage();

let bucket = [];
page.on("console", (m) => {
  if (m.type() === "error") bucket.push(`console.error: ${m.text()}`);
  if (m.type() === "warning" && /missing|i18n|key/i.test(m.text()))
    bucket.push(`console.warn: ${m.text()}`);
});
page.on("pageerror", (e) => bucket.push(`pageerror: ${String(e)}`));
page.on("response", (r) => {
  const u = r.url();
  if (r.status() >= 400 && !u.includes("/favicon"))
    bucket.push(`http ${r.status()}: ${u.replace(BASE, "")}`);
});
const drain = (label) => {
  const e = bucket;
  bucket = [];
  if (e.length) findings.push({ label, errors: [...new Set(e)] });
};

async function snap(name) {
  await page.screenshot({ path: `${shotDir}/${name}.png`, fullPage: true });
}

// ---- Register a fresh account ----
await page.goto(`${BASE}/register`, { waitUntil: "networkidle" });
await page.getByTestId("register-display-name").fill("Walkthrough Bot");
await page.getByTestId("register-email").fill(email);
await page.getByTestId("register-password").fill(password);
await page.getByTestId("register-submit").click();
await page.waitForURL((u) => !u.pathname.includes("/register"), { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1500);
drain("register");
console.log(`landed after register: ${page.url()}`);

// ---- Detect current UI language + toggle behavior ----
const langInfo = await page.evaluate(() => {
  const btn = document.querySelector('[data-testid="language-toggle"]');
  return {
    htmlLang: document.documentElement.lang,
    toggleText: btn?.textContent?.trim() ?? "(no toggle)",
    toggleCurrentAttr: btn?.getAttribute("data-current-language") ?? "?",
  };
});

// ---- Walk routes ----
const routes = [
  "/", "/projects", "/projects/new", "/scans", "/approvals",
  "/policies", "/integrations", "/notifications", "/profile",
  "/admin/users", // expect 404 existence-hide for non-admin
];
for (const r of routes) {
  await page.goto(`${BASE}${r}`, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(700);
  const name = r === "/" ? "root" : r.replace(/\//g, "_").replace(/^_/, "");
  await snap(name);
  // detect raw i18n keys leaking into the DOM (e.g. "approvals.action.x")
  const rawKeys = await page.evaluate(() => {
    const txt = document.body.innerText;
    const m = txt.match(/\b[a-z_]+\.[a-z_]+\.[a-z_.]+\b/g) || [];
    return [...new Set(m)].slice(0, 10);
  });
  if (rawKeys.length) findings.push({ label: `${r} raw-i18n-keys?`, errors: rawKeys });
  drain(`route ${r} (final: ${page.url().replace(BASE, "")})`);
}

// ---- Interaction: empty-state CTA + header register button ----
await page.goto(`${BASE}/projects`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
const inter = {};
// header register button
const hdr = page.getByTestId("project-list-register");
inter.headerRegisterExists = await hdr.count();
if (await hdr.count()) {
  await hdr.first().click().catch(() => {});
  await page.waitForTimeout(600);
  inter.headerRegisterNavTo = page.url().replace(BASE, "");
}
// back, then empty-state CTA (only if no projects)
await page.goto(`${BASE}/projects`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
const cta = page.getByTestId("project-list-empty-cta");
inter.emptyCtaExists = await cta.count();
if (await cta.count()) {
  await cta.first().click().catch(() => {});
  await page.waitForTimeout(600);
  inter.emptyCtaNavTo = page.url().replace(BASE, "");
}
findings.push({ label: "interaction: register buttons", errors: [JSON.stringify(inter)] });

// ---- New Project form: which fields exist? ----
await page.goto(`${BASE}/projects/new`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
const formFields = await page.evaluate(() => {
  const ids = [...document.querySelectorAll("input,textarea,select")].map(
    (el) => el.id || el.getAttribute("name") || el.type,
  );
  return ids;
});
findings.push({ label: "new-project form fields", errors: formFields });
// try an SSH git url (backend allows, frontend regex may reject)
await page.getByTestId("project-name-input").fill("ssh-url-test");
await page.getByTestId("project-git-url-input").fill("git@github.com:org/repo.git");
await page.getByTestId("project-create-submit").click();
await page.waitForTimeout(800);
const gitUrlErr = await page
  .getByTestId("project-git-url-error")
  .textContent()
  .catch(() => null);
findings.push({
  label: "new-project ssh git_url accepted?",
  errors: [gitUrlErr ? `REJECTED by frontend: ${gitUrlErr}` : `final url: ${page.url().replace(BASE, "")}`],
});

// ---- Language toggle interaction ----
await page.goto(`${BASE}/approvals`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
const beforeToggle = await page.evaluate(
  () => document.querySelector("h1,h2")?.textContent?.trim() ?? "",
);
await page.getByTestId("language-toggle").click().catch(() => {});
await page.waitForTimeout(800);
const afterToggle = await page.evaluate(
  () => document.querySelector("h1,h2")?.textContent?.trim() ?? "",
);
findings.push({
  label: "language",
  errors: [
    `html lang=${langInfo.htmlLang}, toggle shows="${langInfo.toggleText}", data-current=${langInfo.toggleCurrentAttr}`,
    `heading before toggle: "${beforeToggle}" → after toggle: "${afterToggle}"`,
  ],
});

// ---- date inputs on /approvals ----
await page.goto(`${BASE}/approvals`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
const dateInputs = await page.evaluate(
  () => document.querySelectorAll('input[type="date"]').length,
);
findings.push({ label: "approvals native date inputs", errors: [`count=${dateInputs} (browser-locale rendered)`] });

await browser.close();

console.log("\n================ WALKTHROUGH FINDINGS ================");
for (const f of findings) {
  console.log(`\n● ${f.label}`);
  for (const e of f.errors) console.log(`   - ${e}`);
}
console.log(`\nscreenshots: ${shotDir}`);
console.log("user:", email);
