import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import {browser,wait} from './browser_harness.mjs';
const root=resolve('.');
const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
const data=await mkdtemp(resolve('work/browser-data-'));
const server=spawn('E:\\Python\\python.exe',['-B',resolve('server.py'),'--data',data,'--port',String(port)],{windowsHide:true,stdio:['ignore','pipe','pipe']});
const base='http://127.0.0.1:'+port;
for(let i=0;i<60;i++){try{if((await fetch(base+'/health')).ok)break}catch{}await wait(100)}
const session=await fetch(base+'/session');const cookie=session.headers.get('set-cookie').split(';')[0];const {csrf}=await session.json();
async function api(path,body){const r=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:{Cookie:cookie,'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();assert(r.ok,JSON.stringify(d));return d}
const samples=[
 ['\u9879\u76ee\u7684\u4e0b\u4e00\u6b65','\u4f18\u5148\u5b8c\u6210\u6765\u6e90\u6838\u9a8c\u548c\u6743\u9650\u9694\u79bb\uff0c\u518d\u6269\u5c55\u65b0\u6e20\u9053\u3002','project'],
 ['\u8ba9\u9605\u8bfb\u66f4\u8f7b\u677e','\u504f\u597d\u7b80\u6d01\u76f4\u63a5\u7684\u56de\u7b54\uff0c\u5173\u952e\u7ed3\u8bba\u653e\u5728\u524d\u9762\u3002','preference'],
 ['\u5173\u4e8e\u65b0\u7684\u51b3\u5b9a','\u957f\u671f\u8bb0\u5fc6\u4fdd\u7559\u6765\u6e90\u3001\u6709\u6548\u65f6\u95f4\u4e0e\u4fee\u8ba2\u8bb0\u5f55\u3002','decision'],
 ['\u4e00\u4e2a\u503c\u5f97\u7559\u4e0b\u7684\u65b9\u6cd5','\u4fee\u6539\u91cd\u8981\u914d\u7f6e\u524d\u5148\u5907\u4efd\uff0c\u68c0\u67e5\u901a\u8fc7\u540e\u518d\u63a8\u8fdb\u3002','procedure'],
 ['\u4ece\u7ecf\u9a8c\u4e2d\u5b66\u5230','\u4e0d\u628a\u8ba1\u5212\u5f53\u4f5c\u5df2\u5b8c\u6210\uff0c\u884c\u52a8\u7ed3\u679c\u9700\u8981\u771f\u5b9e\u56de\u6267\u3002','lesson'],
 ['\u4e3a\u660e\u5929\u4fdd\u7559\u7684\u4e8b','\u51c6\u5907\u4e00\u4efd\u661f\u7a7a\u4e3b\u9898\u7684\u9605\u8bfb\u6e05\u5355\uff0c\u76ee\u524d\u8fd8\u672a\u5f00\u59cb\u3002','commitment']];
for(let i=0;i<36;i++){const x=samples[i%6];let r=await api('/v2/memories',{title:x[0]+(i<6?'':' '+(i+1)),content:x[1],kind:x[2],source:{reference:'Synthetic visual QA fixture'},tags:['test']});if(i%3!==0)await api('/v2/memories/'+r.id+'/review',{version:1,status:'active'})}
const b=await browser(base);const checks=[];const check=(name,v)=>{checks.push({name,passed:!!v});assert(v,name);console.log('PASS '+name)};
async function until(exp){for(let i=0;i<100;i++){if(await b.evaluate(exp))return;await wait(70)}throw Error('Timeout '+exp)}
const click=sel=>b.evaluate('document.querySelector('+JSON.stringify(sel)+').click()');
async function screenshot(name){const r=await b.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(resolve('reports/'+name+'.png'),Buffer.from(r.data,'base64'))}
try{
 await b.send('Emulation.setDeviceMetricsOverride',{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
 await b.send('Page.reload');await until("document.querySelector('#total').textContent==='36'");
 check('Server-backed initial counts',await b.evaluate("document.querySelector('#active').textContent==='24'"));
 check('Bounded page renders 30 records',await b.evaluate("document.querySelectorAll('.card').length===30"));
 await screenshot('memory-desktop');
 await click('#more');await until("document.querySelectorAll('.card').length===36");check('Keyset load more works',true);
 await click('#add');await b.evaluate("document.querySelector('#titleInput').value='Browser QA <img src=x onerror=alert(1)>';document.querySelector('#contentInput').value='Durable browser test content';document.querySelector('[name=reference]').value='Synthetic browser source';");await click('#save');
 await until("document.querySelector('#total').textContent==='37'");check('Create is pending and persistent',await b.evaluate("!document.querySelector('#editDialog').open"));
 check('HTML is text, not executable',await b.evaluate("!document.querySelector('#cards img') && document.querySelector('#cards').textContent.includes('<img')"));
 await b.send('Page.reload');await until("document.querySelector('#total').textContent==='37'");check('Reload persistence',true);
 await b.evaluate("window.confirm=()=>true;document.querySelector('#query').value='Browser';document.querySelector('#query').dispatchEvent(new Event('input'));");
 await until("document.querySelectorAll('.card').length===1");check('Search narrows records',true);await click('.card');await until("document.querySelector('#detailDialog').open");
 await b.evaluate("Array.from(document.querySelectorAll('#detail .detail-actions button')).find(b=>b.classList.contains('primary')).click()");
 await until("document.querySelector('#active').textContent==='25'");check('Explicit approval reaches active',true);
 await b.evaluate("document.querySelector('#detail .detail-actions button').click()");await until("document.querySelector('#editDialog').open");await b.evaluate("document.querySelector('#contentInput').value='Corrected durable browser content'");await click('#save');await until("document.querySelector('#active').textContent==='24'");check('Edit requires reapproval',true);
 await click('.card');await until("document.querySelector('#detailDialog').open");await click('.versions button');await until("document.querySelectorAll('.version').length===3");check('History contains all versions',true);
 await b.evaluate("document.querySelectorAll('.version button')[1].click()");await wait(300);await click('.versions button');await until("document.querySelectorAll('.version').length===4");check('Restore creates a new version',true);
 await click('[data-close=detailDialog]');
 await b.evaluate("document.querySelector('#query').value='';document.querySelector('#query').dispatchEvent(new Event('input'))");await until("document.querySelectorAll('.card').length===30");
 await click('[data-page=review]');await until("document.querySelectorAll('.card').length>0 && Array.from(document.querySelectorAll('.card .state')).every(x=>x.textContent===String.fromCharCode(24453,30830,35748))");check('Review page filters pending',await b.evaluate("Array.from(document.querySelectorAll('.card .state')).every(x=>x.textContent===String.fromCharCode(24453,30830,35748))"));
 await click('[data-page=connections]');await until("!document.querySelector('#connectionsPage').hidden");await click('#backup');await until("document.querySelector('#status').textContent.includes('sqlite3')");check('Backup saved on E drive',await b.evaluate("document.querySelector('#status').textContent.includes('E:')"));
 await click('#export');await until("document.querySelector('#status').textContent.includes('jsonl')");check('Export saved on E drive',true);
 await click('[data-page=events]');await until("document.querySelector('#events .empty')!==null");check('Empty events are honestly empty',await b.evaluate("document.querySelector('#events .empty')!==null"));
 await click('[data-page=library]');await wait(300);
 for(const width of [390,320]){await b.send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:true});await wait(300);check('No overflow '+width,await b.evaluate('document.documentElement.scrollWidth<=innerWidth'));await screenshot('memory-mobile-'+width);await click('#add');check('Dialog accessible '+width,await b.evaluate("document.querySelector('#editDialog').getBoundingClientRect().width<=innerWidth"));await click('[data-close=editDialog]')}
 check('No uncaught browser exceptions',b.errors.filter(e=>e.exceptionDetails||e.exception||e.text==='Uncaught').length===0);
 check('No external page requests',b.network.every(u=>u.startsWith(base)||u.startsWith('data:')));
 await writeFile(resolve('reports/browser-acceptance.json'),JSON.stringify({checks,passed:checks.length,synthetic_fixture:true},null,2));
}finally{await b.close();server.kill()}
