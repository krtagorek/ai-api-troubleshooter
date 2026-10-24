// Real HTTP only. This executable is never part of the production frontend.
import {chromium,expect} from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
const c=JSON.parse(await new Promise(resolve=>{let b='';process.stdin.on('data',x=>b+=x);process.stdin.on('end',()=>resolve(b));}));
const out={realChromium:true,mockedHttp:false,http:[],sse:[],errors:[],consoleSecretLeak:false};
const browser=await chromium.launch({headless:true,args:['--disable-background-networking','--disable-component-update','--no-proxy-server']});
const context=await browser.newContext();
await context.route('**/*',r=>{const u=new URL(r.request().url());return ['127.0.0.1','localhost'].includes(u.hostname)&&['5173','8000'].includes(u.port)?r.continue():r.abort();});
const page=await context.newPage();
page.on('pageerror',e=>out.errors.push(e.name));
page.on('console',m=>{if(m.text().includes(c.token)||m.text().includes('PRIVATE KEY'))out.consoleSecretLeak=true;});
page.on('request',r=>{if(r.url().includes('/events'))out.sse.push({cursor:r.headers()['last-event-id']||null});});
page.on('response',async r=>{if(r.url().includes('/api/')){out.http.push({path:new URL(r.url()).pathname,status:r.status()});const b=await r.text().catch(()=> '');if(b.includes(c.token)||b.includes('PRIVATE KEY'))out.responseSecretLeak=true;}});
async function wait(name){const end=Date.now()+60000;while(Date.now()<end){try{return JSON.parse(await fs.readFile(c.out+'/'+name));}catch{}await new Promise(r=>setTimeout(r,50));}throw Error('Bounded synchronization deadline: '+name);}
async function mark(name,v){await fs.writeFile(c.out+'/'+name,JSON.stringify(v));}
async function connect(){await page.getByRole('button',{name:'Connect API',exact:true}).first().click();await page.getByLabel('Bearer token').fill(c.token);await page.getByRole('button',{name:'Connect securely'}).click();await expect(page.getByRole('button',{name:'Disconnect'})).toBeVisible();}
try{
 await page.goto('http://127.0.0.1:5173/incidents/'+c.incident_id);await connect();
 const receipt=page.waitForResponse(r=>r.url().endsWith('/execute')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Submit to worker',exact:true}).click();if((await receipt).status()!==202)throw Error('Submission not accepted');
 await expect(page.getByText('Agent timeline',{exact:true})).toBeVisible();
 await mark('browser-submitted.json',{accepted:true});await wait('crashed.json');
 await expect(page.getByText('Claimed',{exact:true}).last()).toBeVisible({timeout:10000});
 await page.screenshot({path:c.out+'/crashed-worker.png',fullPage:true});
 await context.setOffline(true);await mark('browser-offline.json',{offline:true});
 await wait('proxy-restarted.json');
 if(c.terminal){await context.setOffline(false);out.authenticatedReconnect='NOT_APPLICABLE: terminal execution must not reconnect';await mark('browser-reconnected.json',{terminal:true});}
 else{const reconnect=page.waitForRequest(r=>r.url().includes('/events')&&!!r.headers()['last-event-id'],{timeout:15000});
 await context.setOffline(false);await reconnect;out.authenticatedReconnect='PASS';await mark('browser-reconnected.json',{reconnected:true});}
 const recovered=await wait('recovered.json');
 async function verifyTimeline(){
  for(const node of ['classifier','log_investigator','root_cause_analyst','resolution_generator','validator']){
   const row=page.locator(`[data-node="${node}"]`);
   await expect(row).toHaveAttribute('data-wrapper-completed',String(recovered.wrapper_nodes.includes(node)));
   await expect(row.getByText('Model invocation: not recorded',{exact:true})).toBeVisible();
  }
  await expect(page.getByText(`Worker attempts: ${recovered.attempts.join(', ')}. Prior wrapper completions are retained across recovery.`,{exact:true})).toBeVisible();
  out.timeline='PASS';out.wrapperNodes=recovered.wrapper_nodes;out.modelInvocationClaims=0;
 }
 try{
 if(c.hold){await expect(page.getByText('More context is needed',{exact:true})).toBeVisible({timeout:20000});await expect(page.getByLabel('Persisted diagnostic report')).toHaveCount(0);}
 else{await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible({timeout:20000});await expect(page.getByText('Human approval is required.',{exact:false})).toBeVisible();await expect(page.getByText('NOT TESTED',{exact:true})).toBeVisible();}
 out.automaticTerminalReconciliation='PASS';
 }catch(e){out.automaticTerminalReconciliation='FAIL';out.terminalFailure=e.message.replaceAll(c.token,'[REDACTED]');out.stalledVisibleText=(await page.locator('body').innerText()).replaceAll(c.token,'[REDACTED]');}

 await verifyTimeline();
 await page.screenshot({path:c.out+'/recovered.png',fullPage:true});
 await page.reload();await connect();
 if(c.hold)await expect(page.getByText('More context is needed',{exact:true})).toBeVisible();else await expect(page.getByLabel('Persisted diagnostic report')).toBeVisible();
 await verifyTimeline();out.timelineAfterRefresh='PASS';
 await page.screenshot({path:c.out+'/refreshed.png',fullPage:true});
 const body=await page.locator('body').innerText();if(body.includes(c.token)||body.includes('PRIVATE KEY'))throw Error('Credential leakage');
 if(out.consoleSecretLeak||out.responseSecretLeak)throw Error('Credential leakage');
 if(out.errors.length)throw Error('Browser exception');
 out.result=out.automaticTerminalReconciliation==='FAIL'?'FAIL':'PASS';out.refresh='PASS';out.expected=c.hold?'safe_hold':'tentative_not_tested_report';
}catch(e){out.result='FAIL';out.failure=e.message.replaceAll(c.token,'[REDACTED]');process.exitCode=1;}
finally{await fs.writeFile(c.out+'/browser-evidence.json',JSON.stringify(out,null,2));await browser.close();}
