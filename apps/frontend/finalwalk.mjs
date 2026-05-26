import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
const BASE="http://localhost:5173", RICH="7822b62d-9156-423d-9df6-5e51f546fbe8";
const ts=Date.now(), dir=`/tmp/final-${ts}`; mkdirSync(dir,{recursive:true});
const b=await chromium.launch();
const p=await (await b.newContext({viewport:{width:1440,height:900}})).newPage();
await p.goto(`${BASE}/login`,{waitUntil:"networkidle"});
await p.getByTestId("login-email").fill("devwalk@example.com");
await p.getByTestId("login-password").fill("DeveloperWalk2026");
await p.getByTestId("login-submit").click();
await p.waitForURL(u=>!u.pathname.includes("/login"),{timeout:15000}).catch(()=>{});
await p.waitForTimeout(1200);
// Components tab — open a multiselect
await p.goto(`${BASE}/projects/${RICH}?tab=components`,{waitUntil:"networkidle"});
await p.waitForTimeout(1200);
const trig = p.getByTestId("components-severity-filter").first();
if (await trig.count()) { await trig.click().catch(()=>{}); await p.waitForTimeout(500); }
await p.screenshot({path:`${dir}/01_multiselect.png`});
await p.keyboard.press("Escape").catch(()=>{});
// Releases tab — no actions column, clickable rows
await p.goto(`${BASE}/projects/${RICH}?tab=releases`,{waitUntil:"networkidle"});
await p.waitForTimeout(1200);
await p.screenshot({path:`${dir}/02_releases.png`});
await b.close();
console.log("dir:",dir);
