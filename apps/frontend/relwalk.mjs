import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
const BASE = "http://localhost:5173";
const RICH = "7822b62d-9156-423d-9df6-5e51f546fbe8";
const OLD = "3c15c82f-c409-4f5f-b7d9-92bca8cc1f7f"; // v0.1 older succeeded scan
const ts = Date.now();
const dir = `/tmp/rel-${ts}`;
mkdirSync(dir, { recursive: true });
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await p.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await p.getByTestId("login-email").fill("devwalk@example.com");
await p.getByTestId("login-password").fill("DeveloperWalk2026");
await p.getByTestId("login-submit").click();
await p.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 15000 }).catch(() => {});
await p.waitForTimeout(1200);
// Releases tab (2 rows)
await p.goto(`${BASE}/projects/${RICH}?tab=releases`, { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
await p.screenshot({ path: `${dir}/01_releases_tab.png`, fullPage: true });
// Pin older v0.1 snapshot → overview with read-only banner
await p.goto(`${BASE}/projects/${RICH}?scan=${OLD}`, { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
await p.screenshot({ path: `${dir}/02_snapshot_overview_banner.png`, fullPage: true });
// Vulnerabilities under the pinned snapshot (write controls should be read-only)
await p.goto(`${BASE}/projects/${RICH}?scan=${OLD}&tab=vulnerabilities`, { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
await p.screenshot({ path: `${dir}/03_snapshot_vulns_readonly.png`, fullPage: true });
await b.close();
console.log("screenshots:", dir);
