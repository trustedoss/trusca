import { chromium } from "@playwright/test";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1800 } });
const page = await ctx.newPage();
page.on("pageerror", (err) => console.log("[pageerror]", err.message));
const target = process.env.PREVIEW_URL || "http://localhost:5174/dev/design-preview";
await page.goto(target);
await page.waitForSelector("text=Vercel base + Linear polish", { timeout: 15000 });
const out = process.argv[2];
await page.screenshot({ path: out, fullPage: true });
console.log("wrote", out);
await browser.close();
