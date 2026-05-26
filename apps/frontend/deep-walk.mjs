// Deep independent walkthrough as super-admin. Walks admin pages, a
// data-rich project's detail tabs, drawers, scan dialog, integrations.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://localhost:5173";
const EMAIL = "walkadmin@example.com";
const PW = "AdminWalk2026!";
const RICH_PROJECT = "7822b62d-9156-423d-9df6-5e51f546fbe8"; // ci-vulns-6b89ce, 74 vulns
const ts = Date.now();
const shotDir = `/tmp/deep-${ts}`;
mkdirSync(shotDir, { recursive: true });

const findings = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, locale: "ko-KR" });
const page = await ctx.newPage();

let bucket = [];
page.on("console", (m) => {
  if (m.type() === "error" && !/auth\/me/.test(m.text())) bucket.push(`console.error: ${m.text().slice(0, 200)}`);
});
page.on("pageerror", (e) => bucket.push(`pageerror: ${String(e).slice(0, 200)}`));
page.on("response", (r) => {
  const u = r.url();
  if (r.status() >= 400 && !u.includes("favicon") && !u.endsWith("/auth/me"))
    bucket.push(`http ${r.status()}: ${u.replace(BASE, "").replace("http://localhost:8000", "API")}`);
});
const drain = (label) => { const e = bucket; bucket = []; if (e.length) findings.push({ label, errors: [...new Set(e)] }); };
const snap = async (n) => { await page.screenshot({ path: `${shotDir}/${n}.png`, fullPage: true }).catch(() => {}); };

// visible error alerts / raw i18n keys / endless skeletons on current page
async function pageHealth(label) {
  const h = await page.evaluate(() => {
    const txt = document.body.innerText;
    const alerts = [...document.querySelectorAll('[role="alert"],[data-testid*="error"]')]
      .map((e) => e.textContent?.trim()).filter(Boolean).slice(0, 5);
    const rawKeys = [...new Set((txt.match(/\b[a-z][a-z_]+\.[a-z_]+\.[a-z_.]+\b/g) || []))].slice(0, 8);
    const skeletons = document.querySelectorAll('[data-testid*="loading"],.animate-pulse').length;
    const failedwords = (txt.match(/(failed|불러오지|오류가|에러|undefined|NaN|\[object Object\])/gi) || []).slice(0, 5);
    return { alerts, rawKeys, skeletons, failedwords };
  });
  const notes = [];
  if (h.alerts.length) notes.push(`visible-alert: ${JSON.stringify(h.alerts)}`);
  if (h.rawKeys.length) notes.push(`raw-i18n-key?: ${JSON.stringify(h.rawKeys)}`);
  if (h.skeletons) notes.push(`skeletons-still-present: ${h.skeletons}`);
  if (h.failedwords.length) notes.push(`suspicious-text: ${JSON.stringify(h.failedwords)}`);
  if (notes.length) findings.push({ label: `${label} [health]`, errors: notes });
}

// ---- login ----
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.getByTestId("login-email").fill(EMAIL);
await page.getByTestId("login-password").fill(PW);
await page.getByTestId("login-submit").click();
await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1200);
drain("login");
console.log("after login:", page.url());

// ---- admin pages ----
const adminRoutes = [
  "/admin/users", "/admin/teams", "/admin/dt", "/admin/scans",
  "/admin/disk", "/admin/audit", "/admin/health", "/admin/backup",
];
for (const r of adminRoutes) {
  await page.goto(`${BASE}${r}`, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(1000);
  await snap("admin_" + r.split("/").pop());
  await pageHealth(r);
  drain(`${r} (final ${page.url().replace(BASE, "")})`);
}

// ---- data-rich project detail: every tab ----
await page.goto(`${BASE}/projects/${RICH_PROJECT}`, { waitUntil: "networkidle" }).catch(() => {});
await page.waitForTimeout(1500);
await snap("project_detail_landing");
await pageHealth("project detail landing");
drain(`project detail landing (final ${page.url().replace(BASE, "")})`);

const tabs = await page.locator('[role="tab"]').all();
findings.push({ label: "project tabs found", errors: [`count=${tabs.length}`] });
for (let i = 0; i < tabs.length; i++) {
  const t = page.locator('[role="tab"]').nth(i);
  const name = (await t.textContent().catch(() => `tab${i}`))?.trim() || `tab${i}`;
  await t.click().catch(() => {});
  await page.waitForTimeout(1200);
  await snap(`tab_${i}_${name.replace(/\s+/g, "")}`);
  await pageHealth(`tab "${name}"`);
  drain(`tab "${name}"`);
}

// ---- open first row drawer on Components / Vulnerabilities (re-click tab then a row) ----
async function tryRowDrawer(tabName) {
  const tab = page.locator('[role="tab"]', { hasText: tabName }).first();
  if (await tab.count()) {
    await tab.click().catch(() => {});
    await page.waitForTimeout(1000);
    // click first table row / virtuoso item
    const row = page.locator('[data-testid*="row"], tbody tr, [data-index="0"]').first();
    if (await row.count()) {
      await row.click().catch(() => {});
      await page.waitForTimeout(900);
      await snap(`drawer_${tabName}`);
      const drawerOpen = await page.locator('[role="dialog"], [data-testid*="drawer"]').count();
      findings.push({ label: `drawer from ${tabName}`, errors: [`drawer elements=${drawerOpen}`] });
      await pageHealth(`drawer ${tabName}`);
      await page.keyboard.press("Escape").catch(() => {});
    } else {
      findings.push({ label: `drawer from ${tabName}`, errors: ["no clickable row found"] });
    }
    drain(`drawer ${tabName}`);
  }
}
await tryRowDrawer("Components");
await tryRowDrawer("Vulnerabilities");

// ---- scan dialog from project list ----
await page.goto(`${BASE}/projects`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
const scanBtn = page.locator('[data-testid*="scan"], button:has-text("Scan")').first();
if (await scanBtn.count()) {
  await scanBtn.click().catch(() => {});
  await page.waitForTimeout(800);
  const dlg = await page.locator('[data-testid="source-select-dialog"]').count();
  findings.push({ label: "scan dialog opens from list", errors: [`dialog=${dlg}`] });
  await snap("scan_dialog");
  await page.keyboard.press("Escape").catch(() => {});
} else {
  findings.push({ label: "scan dialog", errors: ["no Scan button on project list (super-admin sees own team only?)"] });
}
drain("scan dialog");

// ---- integrations: create API key ----
await page.goto(`${BASE}/integrations`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await snap("integrations");
await pageHealth("integrations");
const createKey = page.locator('button:has-text("Create"), [data-testid*="create"], [data-testid*="api-key"]').first();
findings.push({ label: "integrations create-key button present?", errors: [`count=${await createKey.count()}`] });
drain("integrations");

// ---- notifications + profile ----
for (const r of ["/notifications", "/profile"]) {
  await page.goto(`${BASE}${r}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await snap(r.slice(1));
  await pageHealth(r);
  drain(r);
}

await browser.close();
console.log("\n================ DEEP WALKTHROUGH FINDINGS ================");
for (const f of findings) {
  console.log(`\n● ${f.label}`);
  for (const e of f.errors) console.log(`   - ${e}`);
}
console.log(`\nscreenshots: ${shotDir}`);
