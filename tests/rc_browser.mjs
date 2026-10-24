import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
const dir=process.env.RC_DIR,token=process.env.RC_TOKEN,iid=process.env.RC_INCIDENT;
const result={mode:'REAL_HTTP_REAL_CHROMIUM_SYNTHETIC_AGENTS',checks:{},responses:[],consoleErrors:[],sse:[]};
const browser=await chromium.launch({headless:true});const page=await browser.newPage();
page.on('pageerror',e=>result.consoleErrors.push(e.message));
page.on('request',r=>{if(r.url().includes('/events'))result.sse.push({path:new URL(r.url()).pathname,cursor:r.headers()['last-event-id']||null})});
page.on('response',r=>{if(r.url().includes('/api/'))result.responses.push({path:new URL(r.url()).pathname,status:r.status()})});
async function connect(){await page.getByRole('button',{name:'Connect API',exact:true}).first().click();await page.getByLabel('Bearer token').fill(token);await page.getByRole('button',{name:'Connect securely'}).click();await expect(page.getByRole('button',{name:'Disconnect'})).toBeVisible()}
try{
 await page.goto('http://127.0.0.1:5173');await connect();result.checks.dashboard='PASS';
 await page.goto('http://127.0.0.1:5173/incidents/'+iid);await connect();
 await fs.writeFile(dir+'/start-worker','start');
 await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible({timeout:30000});
 await expect(page.getByText('[OUTPUT SAFETY:',{exact:false}).first()).toBeVisible();
 let text=await page.locator('body').innerText();
 if(text.includes('Provide passwords')||text.includes('Evidence of the client authentication credentials'))throw Error('Unsafe user-facing instruction survived');
 result.checks.react_report_safety='PASS';result.checks.actual_sse_events=result.sse.length?'PASS':'FAIL';
 await page.screenshot({path:dir+'/report.png',fullPage:true});
 await page.reload();await connect();await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible();result.checks.persisted_report_refresh='PASS';
 await page.getByRole('navigation').getByRole('link',{name:'Incident history',exact:true}).click();await expect(page.getByText('RC Synthetic Safety',{exact:true})).toBeVisible();result.checks.history='PASS';
 if(result.consoleErrors.length)throw Error('Browser errors recorded');
 result.checks.browser_errors='PASS';
}catch(e){result.failure=e.message;process.exitCode=1;await page.screenshot({path:dir+'/failure.png',fullPage:true}).catch(()=>{})}
finally{await fs.writeFile(dir+'/browser-evidence.json',JSON.stringify(result,null,2));await browser.close()}
