import { chromium } from '../apps/web/node_modules/playwright/index.mjs';
import { existsSync } from 'node:fs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const {baseURL,username,password}=JSON.parse(input);
const browser=await chromium.launch(existsSync('/Applications/Google Chrome.app')?{channel:'chrome'}:{});
try{
 const context=await browser.newContext({baseURL,viewport:{width:1440,height:900}});
 await context.route('**/*',route=>['127.0.0.1','localhost'].includes(new URL(route.request().url()).hostname)?route.continue():route.abort());
 const response=await context.request.post('/api/v1/auth/login',{data:{username,password}});if(!response.ok())throw new Error('benchmark login failed');
 const {csrf}=await response.json();
 const pair=await context.request.post('/api/v1/display/devices',{headers:{'X-CSRF-Token':csrf},data:{name:'synthetic-benchmark'}});
 if(!pair.ok())throw new Error('benchmark pairing failed');
 const {code}=await pair.json();await context.request.post('/api/v1/display/pair',{data:{code}});
 const page=await context.newPage();const started=performance.now();await page.goto('/display');
 await page.locator('.display-photo img').waitFor();await page.waitForFunction(()=>{const img=document.querySelector('.display-photo img');return img?.complete&&img.naturalWidth>0});
 console.log(JSON.stringify({first_photo_ms:Math.round(performance.now()-started),browser:browser.version()}));
 await context.close();
}finally{await browser.close()}
