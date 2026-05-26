// Developer-account deep walkthrough — exercises the interactions a
// super-admin (empty team) couldn't: scan dialog (all source methods),
// row drawers, vuln status workflow, new-project happy path, approvals,
// integrations create-key.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://localhost:5173";
const EMAIL = "devwalk@example.com";
const PW = "DeveloperWalk2026";
const RICH = "7822b62d-9156-423d-9df6-5e51f546fbe8"; // ci-vulns (this dev's team)
const ts = Date.now();
const shotDir = `/tmp/dev-${ts}`;
mkdirSync(shotDir, { recursive: true });

const findings = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, locale: "ko-KR" });
const page = await ctx.newPage();

let bucket = [];
page.on("console", (m) => {
  const t = m.text();
  if (m.type() === "error" && !/Failed to load resource: the server responded with a status of 401/.test(t))
    bucket.push(`console.error: ${t.slice(0, 220)}`);
  if (m.type() === "warning" && /(cannot update|act\(|key|i18n|missing)/i.test(t))
    bucket.push(`console.warn: ${t.slice(0, 200)}`);
});
page.on("pageerror", (e) => bucket.push(`pageerror: ${String(e).slice(0, 220)}`));
page.on("response", (r) => {
  const u = r.url();
  if (r.status() >= 400 && !u.includes("favicon") && !u.endsWith("/auth/me"))
    bucket.push(`http ${r.status()}: ${u.replace(BASE, "").replace("http://localhost:8000", "API")}`);
});
const drain = (label) => { const e = bucket; bucket = []; if (e.length) findings.push({ label, errors: [...new Set(e)] }); };
const note = (label, ...msgs) => findings.push({ label, errors: msgs });
const snap = async (n) => { await page.screenshot({ path: `${shotDir}/${n}.png`, fullPage: true }).catch(() => {}); };

// ---- login ----
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.getByTestId("login-email").fill(EMAIL);
await page.getByTestId("login-password").fill(PW);
await page.getByTestId("login-submit").click();
await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1200);
drain("login");
console.log("after login:", page.url());

// ---- project list: does the dev see ci-vulns? ----
await page.goto(`${BASE}/projects`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await snap("projects_list");
const rowCount = await page.locator('[data-testid="project-list-virtual"]').getAttribute("data-total").catch(() => "?");
note("project list rows visible to developer", `data-total=${rowCount}`);
drain("project list");

// ---- scan dialog: open from the ci-vulns row ----
// find a Scan button (row action)
const scanBtn = page.locator('[data-testid="project-row-scan"], button:has-text("스캔"), button:has-text("Scan")').first();
note("scan button present on row", `count=${await scanBtn.count()}`);
if (await scanBtn.count()) {
  await scanBtn.click().catch(() => {});
  await page.waitForTimeout(900);
  const dlg = page.locator('[data-testid="source-select-dialog"]');
  note("scan dialog opened", `count=${await dlg.count()}`);
  await snap("scan_dialog_open");
  // toggle each scan kind + source method, record enabled/disabled
  for (const m of ["source-method-git", "source-method-upload", "source-method-folder"]) {
    const el = page.getByTestId(m);
    if (await el.count()) {
      const disabled = await el.isDisabled().catch(() => null);
      await el.click().catch(() => {});
      await page.waitForTimeout(300);
      note(`scan method ${m}`, `disabled=${disabled}`);
    }
  }
  // container kind
  const ck = page.getByTestId("scan-kind-container");
  if (await ck.count()) {
    await ck.click().catch(() => {});
    await page.waitForTimeout(400);
    const imgInput = page.getByTestId("scan-image-ref-input");
    note("container image input present", `count=${await imgInput.count()}`);
    await snap("scan_dialog_container");
  }
  drain("scan dialog interactions");
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(400);
}

// ---- project detail: drawers + vuln status workflow ----
await page.goto(`${BASE}/projects/${RICH}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

async function openTab(name) {
  const tab = page.locator('[role="tab"]', { hasText: name }).first();
  if (await tab.count()) { await tab.click().catch(() => {}); await page.waitForTimeout(1200); return true; }
  return false;
}

// Components drawer
if (await openTab("컴포넌트")) {
  await snap("tab_components");
  const row = page.locator('[data-testid*="component-row"], [data-index="0"], tbody tr').first();
  if (await row.count()) {
    await row.click().catch(() => {});
    await page.waitForTimeout(900);
    const drawer = page.locator('[role="dialog"], [data-testid*="drawer"]');
    note("component drawer", `opened=${await drawer.count()}`);
    await snap("drawer_component");
    await page.keyboard.press("Escape").catch(() => {});
  } else note("component drawer", "no component row found (data not joined to this dev's view?)");
  drain("components tab/drawer");
}

// Vulnerabilities drawer + status workflow
if (await openTab("취약점")) {
  await snap("tab_vulns");
  const row = page.locator('[data-testid*="vuln"], [data-index="0"], tbody tr').first();
  if (await row.count()) {
    await row.click().catch(() => {});
    await page.waitForTimeout(900);
    await snap("drawer_vuln");
    // look for a status selector / action buttons inside the drawer
    const statusCtl = page.locator('[data-testid*="status"], [role="dialog"] button, [role="dialog"] select');
    note("vuln drawer controls", `interactive elements=${await statusCtl.count()}`);
    // try to change status via a select if present
    const sel = page.locator('[role="dialog"] select, [data-testid*="status-select"]').first();
    if (await sel.count()) {
      const before = await sel.inputValue().catch(() => "?");
      const opts = await sel.locator("option").allTextContents().catch(() => []);
      note("vuln status select", `current=${before}, options=${JSON.stringify(opts.slice(0, 8))}`);
    }
    await page.keyboard.press("Escape").catch(() => {});
  } else note("vuln drawer", "no vuln row found");
  drain("vulns tab/drawer");
}

// ---- New Project happy-path submit ----
await page.goto(`${BASE}/projects/new`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
const pname = `dev-walk-${ts}`;
await page.getByTestId("project-name-input").fill(pname);
await page.getByTestId("project-git-url-input").fill("https://github.com/pallets/flask");
await page.getByTestId("project-create-submit").click();
await page.waitForTimeout(1500);
note("new project happy-path", `after submit url=${page.url().replace(BASE, "")}`);
await snap("new_project_after_submit");
drain("new project submit");

// ---- Approvals: filters + row drawer + actions ----
await page.goto(`${BASE}/approvals`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await snap("approvals");
const apRows = await page.locator('tbody tr, [data-testid*="approval-row"]').count();
note("approvals rows", `count=${apRows}`);
if (apRows > 0) {
  await page.locator('tbody tr, [data-testid*="approval-row"]').first().click().catch(() => {});
  await page.waitForTimeout(800);
  await snap("approvals_drawer");
  note("approvals drawer", `dialog=${await page.locator('[role="dialog"]').count()}`);
  await page.keyboard.press("Escape").catch(() => {});
}
drain("approvals");

// ---- Policies (confirm #9 as developer) ----
await page.goto(`${BASE}/policies`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await snap("policies");
drain("policies");

// ---- Integrations: create API key flow ----
await page.goto(`${BASE}/integrations`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
const createKeyBtn = page.locator('button:has-text("Create"), button:has-text("생성"), [data-testid*="create-key"], [data-testid*="api-key-create"]').first();
note("integrations create-key button", `count=${await createKeyBtn.count()}`);
if (await createKeyBtn.count()) {
  await createKeyBtn.click().catch(() => {});
  await page.waitForTimeout(700);
  await snap("integrations_create_dialog");
  // fill a name if a field appears, submit
  const nameField = page.locator('[role="dialog"] input, [data-testid*="key-name"]').first();
  if (await nameField.count()) {
    await nameField.fill(`dev-key-${ts}`).catch(() => {});
    const submit = page.locator('[role="dialog"] button:has-text("Create"), [role="dialog"] button:has-text("생성"), [role="dialog"] [type="submit"]').first();
    if (await submit.count()) { await submit.click().catch(() => {}); await page.waitForTimeout(900); }
    await snap("integrations_key_created");
    // is the plaintext key shown once + copy affordance?
    const keyShown = await page.locator('[data-testid*="plaintext"], [data-testid*="secret"], code, [role="dialog"] input[readonly]').count();
    const copyBtn = await page.locator('button:has-text("Copy"), button:has-text("복사"), [data-testid*="copy"]').count();
    note("API key creation result", `secret-shown-elements=${keyShown}, copy-button=${copyBtn}`);
  }
  await page.keyboard.press("Escape").catch(() => {});
}
drain("integrations create-key");

await browser.close();
console.log("\n================ DEVELOPER WALKTHROUGH FINDINGS ================");
for (const f of findings) {
  console.log(`\n● ${f.label}`);
  for (const e of f.errors) console.log(`   - ${e}`);
}
console.log(`\nscreenshots: ${shotDir}`);
