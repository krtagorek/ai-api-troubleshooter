// Manual live acceptance. No API interception or fabricated responses.
import {chromium,expect} from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
const dir=process.env.ACCEPT_DIR,token=process.env.ACCEPT_TOKEN;
const result={realChromium:true,mockedHttp:false,checks:{},http:[],sse:[],errors:[],blockedEgress:[]};
const browser=await chromium.launch({headless:true,args:['--disable-background-networking','--disable-component-update','--no-proxy-server']});
const context=await browser.newContext();
await context.route('**/*',route=>{const u=new URL(route.request().url());if(['127.0.0.1','localhost'].includes(u.hostname)&&['5173','8000'].includes(u.port))return route.continue();result.blockedEgress.push(u.origin);return route.abort();});
const page=await context.newPage();
page.on('pageerror',e=>result.errors.push(e.name));
page.on('response',async response=>{if(response.url().includes('/api/')){result.http.push({path:new URL(response.url()).pathname,status:response.status()});let body=await response.text().catch(()=> '');if(body.includes(token)||body.includes('BEGIN PRIVATE KEY')){result.securityFailure='credential_response_leak';process.exitCode=2;}}});
page.on('request',r=>{if(r.url().includes('/events'))result.sse.push({path:new URL(r.url()).pathname,cursor:r.headers()['last-event-id']||null});});
async function waitFile(name,timeout=60000){let end=Date.now()+timeout;while(Date.now()<end){try{return JSON.parse(await fs.readFile(dir+'/'+name,'utf8'));}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Orchestration timeout: '+name);}
async function connect(){await page.getByRole('button',{name:'Connect API',exact:true}).first().click();await page.getByLabel('Bearer token').fill(token);await page.getByRole('button',{name:'Connect securely'}).click();await expect(page.getByRole('button',{name:'Disconnect'})).toBeVisible();}
try{
 await page.goto('http://127.0.0.1:5173');await connect();result.checks.authentication='PASS';
 await page.screenshot({path:dir+'/dashboard.png',fullPage:true});
 await page.getByRole('navigation').getByRole('link',{name:'API Registry',exact:true}).click();
 await page.getByRole('button',{name:'Register development mock'}).click();
 const challenge=await page.getByLabel('Ownership challenge').inputValue();
 await page.getByRole('button',{name:'Continue to verification'}).click();
 await page.getByLabel('Ownership challenge').fill(challenge);
 await page.getByRole('button',{name:'Verify ownership'}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByRole('navigation').getByRole('link',{name:'Admin approvals',exact:true}).click();
 await page.getByRole('button',{name:'Approve',exact:true}).click();
 await page.getByRole('button',{name:'Confirm approve',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 await expect(page.getByRole('button',{name:'Suspend',exact:true})).toBeVisible();
 result.checks.registryApproval='PASS';await page.screenshot({path:dir+'/approved-local-api.png',fullPage:true});
 // Tell controller the real registry is ready; no one-time challenge is saved.
 await fs.writeFile(dir+'/registry-ready.json',JSON.stringify({ready:true}));
 const evidence=await waitFile('incident-form.json');
 await page.getByRole('navigation').getByRole('link',{name:'AI Troubleshooter',exact:true}).click();
 for(const [id,value] of Object.entries({actual:evidence.actual_behavior,expected:evidence.expected_behavior,service:evidence.service,endpoint:evidence.endpoint,status:String(evidence.status_code)}))await page.locator('#'+id).fill(value);
 await page.getByText('Attach sanitized evidence',{exact:false}).click();
 await page.locator('#logs').fill(JSON.stringify(evidence.logs));
 await page.getByRole('button',{name:'Create investigation',exact:true}).click();
 await page.waitForURL('**/incidents/*');
 const iid=page.url().split('/').pop();
 await fs.writeFile(dir+'/incident-created.json',JSON.stringify({incident_id:iid}));
 await waitFile('provider-ready.json');
 const receipt=page.waitForResponse(r=>r.url().endsWith('/execute')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Submit to worker',exact:true}).click();
 if((await receipt).status()!==202)throw Error('Worker did not accept submission');
 await fs.writeFile(dir+'/execute-requested.json',JSON.stringify({ready:true}));
 await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible({timeout:45000});
 await expect(page.getByText('Qualified stale fencing rejection; stale routing origin remains unconfirmed',{exact:true})).toBeVisible();
 await expect(page.getByText('Human approval is required.',{exact:false})).toBeVisible();
 await expect(page.getByText('Tentative diagnostic analysis completed; validation NOT_TESTED and human approval required',{exact:true})).toBeVisible();
 await expect(page.getByText('Inspect authoritative routing metadata',{exact:true})).toBeVisible();
 result.checks.tentativeDiagnosis='PASS';result.checks.humanApproval='PASS';result.checks.notTested='PASS';
 result.checks.workerTerminalRendered='PASS';result.checks.actualSse=result.sse.length?'PASS':'FAIL';
 await page.screenshot({path:dir+'/investigation-result.png',fullPage:true});
 result.checks.persistedReport=(await page.getByLabel('Persisted diagnostic report').count())?'PASS':'FAIL';
 result.reportTextVisible=(await page.locator('body').innerText()).includes('Qualified stale fencing');
 await page.reload();await connect();
 await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible();
 result.checks.refresh='PASS';await page.screenshot({path:dir+'/refreshed-investigation.png',fullPage:true});
 await page.getByRole('navigation').getByRole('link',{name:'Incident history',exact:true}).click();
 await expect(page.getByText('Phase 14.6 Synthetic Fencing',{exact:true})).toBeVisible();result.checks.history='PASS';
 await page.screenshot({path:dir+'/history.png',fullPage:true});
 const body=await page.locator('body').innerText();if(body.includes(token)||body.includes('BEGIN PRIVATE KEY')){result.securityFailure='credential_browser_leak';process.exitCode=2;}
 result.checks.browserErrors=result.errors.length?'FAIL':'PASS';
}catch(e){result.failure=e.message.replaceAll(token,'[REDACTED]');process.exitCode=1;await page.locator('input').evaluateAll(els=>els.forEach(e=>{e.value='[MASKED]'})).catch(()=>{});await page.screenshot({path:dir+'/browser-failure.png',fullPage:true}).catch(()=>{});}
finally{await fs.writeFile(dir+'/browser-evidence.json',JSON.stringify(result,null,2));await browser.close();}
