import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
const BASE = "http://localhost:5173";
const RICH = "7822b62d-9156-423d-9df6-5e51f546fbe8";
const TARGET = "50b3d477-2211-47a3-947b-69022dabb2b3"; // 5/22
const OLD = "3c15c82f-c409-4f5f-b7d9-92bca8cc1f7f";   // v0.1
const ts = Date.now();
const dir = `/tmp/cmp-${ts}`;
mkdirSync(dir, { recursive: true });
const b = await chromium.launch();
const p = await (await b.newContext({ viewport: { width: 1440, height: 1000 } })).newPage();
await p.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await p.getByTestId("login-email").fill("devwalk@example.com");
await p.getByTestId("login-password").fill("DeveloperWalk2026");
await p.getByTestId("login-submit").click();
await p.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 15000 }).catch(() => {});
await p.waitForTimeout(1200);
// header switcher menu open
await p.goto(`${BASE}/projects/${RICH}`, { waitUntil: "networkidle" });
await p.waitForTimeout(1000);
await p.getByTestId("release-switcher").click().catch(() => {});
await p.waitForTimeout(600);
await p.screenshot({ path: `${dir}/01_switcher_menu.png`, fullPage: true });
await p.keyboard.press("Escape").catch(() => {});
// compare view v0.1 -> 5/22
await p.goto(`${BASE}/projects/${RICH}/compare?base=${OLD}&target=${TARGET}`, { waitUntil: "networkidle" });
await p.waitForTimeout(1500);
await p.screenshot({ path: `${dir}/02_compare.png`, fullPage: true });
await b.close();
console.log("screenshots:", dir);
