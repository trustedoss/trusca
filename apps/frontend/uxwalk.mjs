// Usability walkthrough — drives the app as a developer with real data and
// captures full-page screenshots of every screen + key interactions, so the
// orchestrator can VISUALLY judge friction (not just console errors).
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = "http://localhost:5173";
const EMAIL = "devwalk@example.com";
const PW = "DeveloperWalk2026";
const RICH = "7822b62d-9156-423d-9df6-5e51f546fbe8"; // ci-vulns, 74 vulns (devwalk's team)
const ts = Date.now();
const dir = `/tmp/ux-${ts}`;
mkdirSync(dir, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const shot = async (n) => { await page.screenshot({ path: `${dir}/${n}.png`, fullPage: true }).catch(() => {}); };
const go = async (path, wait = 900) => { await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" }).catch(() => {}); await page.waitForTimeout(wait); };

// login
await go("/login");
await page.getByTestId("login-email").fill(EMAIL);
await page.getByTestId("login-password").fill(PW);
await page.getByTestId("login-submit").click();
await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1500);
console.log("after login:", page.url());

// top-level screens
await go("/"); await shot("01_dashboard");
await go("/projects"); await shot("02_projects_list");
await go("/projects/new"); await shot("03_new_project");
await go("/scans"); await shot("18_scans");
await go("/approvals"); await shot("19_approvals");
await go("/policies"); await shot("20_policies");
await go("/integrations"); await shot("21_integrations");
await go("/notifications"); await shot("22_notifications");
await go("/profile"); await shot("23_profile");

// rich project — every tab via ?tab=
const tabs = ["", "components", "vulnerabilities", "licenses", "obligations", "sbom", "source", "remediation", "settings"];
let i = 4;
for (const tab of tabs) {
  const q = tab ? `?tab=${tab}` : "";
  await go(`/projects/${RICH}${q}`, 1300);
  await shot(`${String(i).padStart(2, "0")}_project_${tab || "overview"}`);
  i += 1;
}

// drawers: components + vulnerabilities first row
await go(`/projects/${RICH}?tab=components`, 1300);
const compRow = page.locator('[data-testid*="component-row"], tbody tr, [data-index="0"]').first();
if (await compRow.count()) { await compRow.click().catch(() => {}); await page.waitForTimeout(900); await shot("13_drawer_component"); await page.keyboard.press("Escape").catch(() => {}); }

await go(`/projects/${RICH}?tab=vulnerabilities`, 1300);
const vulnRow = page.locator('[data-testid*="vuln"], tbody tr, [data-index="0"]').first();
if (await vulnRow.count()) { await vulnRow.click().catch(() => {}); await page.waitForTimeout(900); await shot("14_drawer_vuln"); await page.keyboard.press("Escape").catch(() => {}); }

// scan dialog from the DETAIL page (the new button)
await go(`/projects/${RICH}`, 1200);
const scanBtn = page.getByTestId("project-detail-scan");
if (await scanBtn.count()) {
  await scanBtn.click().catch(() => {});
  await page.waitForTimeout(700);
  await shot("15_scan_dialog_source");
  // switch to container
  const ck = page.getByTestId("scan-kind-container");
  if (await ck.count()) { await ck.click().catch(() => {}); await page.waitForTimeout(400); await shot("16_scan_dialog_container"); }
}

await browser.close();
console.log("screenshots:", dir);
