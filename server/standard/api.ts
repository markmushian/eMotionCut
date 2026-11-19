import {trackStorageRequest} from '../editor/storage-activity.ts';
import {scheduleStorageMaintenance} from '../editor/api/storage.ts';
import {handleSharedSettings} from './settings-api.ts';
import {checkCreationEnvironment} from './creation-tools.ts';
import media from '../media-tools.ts';
import {upgradeApi} from './upgrade.ts';
import {motionApi} from './motion-api.ts';
import {creatorTopicsApi} from '../creator-topics.ts';
import {creatorProjectsApi} from '../creator-projects.ts';
import {creatorAgentBridge,creatorAgentChoiceApi} from '../creator-agent-bridge.ts';
import {matrixApi,PLATFORMS,resolveAccount,ensureAccount,openAccountWindow} from '../creator-matrix.ts';
import {creatorProductionReadiness} from '../creator-production.ts';
import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import type {IncomingMessage,ServerResponse} from 'node:http';import {handleGeneralApi} from '../general/api.ts';import {bodyJson,sendJson} from '../http.ts';import {agentSettingsView,saveAgentSettings,clearAgentRuns} from '../agent-settings.ts';import {handleEditorApi} from './openscreen-api.ts';
const creatorPlatforms={douyin:{name:'抖音',domain:'douyin.com',cookies:['sessionid','sessionid_ss','sid_tt','sid_guard']},xhs:{name:'小红书',domain:'xiaohongshu.com',cookies:['web_session','web_sessionid']},kuaishou:{name:'快手',domain:'kuaishou.com',cookies:['kuaishou.server.webday7_st','kuaishou.server.web_st','passToken','userId']},bilibili:{name:'B站',domain:'bilibili.com',cookies:['SESSDATA','DedeUserID']},channels:{name:'视频号',domain:'channels.weixin.qq.com',cookies:['sessionid','ticket','finder-username','data_biz_uid']}} as const;
async function creatorPlatformStatus(id:string){
 const key=id in creatorPlatforms?id as keyof typeof creatorPlatforms:'douyin',cfg=creatorPlatforms[key];
 const account=resolveAccount(key);
 if(!account)return {alive:false,logged_in:false,by:'no-account',platform:key,platform_name:cfg.name,error:`还没有${cfg.name}账号：先去「矩阵分发」添加一个，登录后这里就能用`};
 try{
  const {chromium}=await import('playwright-core'),browser=await chromium.connectOverCDP(`http://127.0.0.1:${account.port}`);
  try{
   const contexts=browser.contexts(),cookies=(await Promise.all(contexts.map(c=>c.cookies(`https://${cfg.domain}/`)))).flat();
   const hit=cookies.find(c=>(cfg.cookies as readonly string[]).some(name=>name.toLowerCase()===c.name.toLowerCase())&&!!c.value);
   if(hit)return {alive:true,logged_in:true,by:`cookie:${hit.name}`,platform:key,platform_name:cfg.name,accountId:account.id,accountName:account.alias};
   for(const context of contexts)for(const page of context.pages())if(page.url().includes(cfg.domain)){
    const dom=await page.evaluate((platform)=>{const text=document.body?.innerText||'',q=(s:string)=>!!document.querySelector(s);if(platform==='douyin')return q('[data-e2e="user-avatar"],a[href*="/user/self"],img[src*="aweme-avatar"],[class*="avatar"] img')&&!/扫码登录|登录后即可/.test(text.slice(0,1800));if(platform==='xhs')return !/登录后查看更多|扫码登录/.test(text.slice(0,1800))&&q('a[href*="/user/profile/"],[class*="avatar"] img');return q('[class*="avatar"] img,[class*="user"] img,a[href*="/account"],a[href*="/space/"]')&&!/扫码登录|立即登录/.test(text.slice(0,1800))},key);
    if(dom)return {alive:true,logged_in:true,by:'page:account-ui',platform:key,platform_name:cfg.name,accountId:account.id,accountName:account.alias};
   }
   return {alive:true,logged_in:false,by:'none',platform:key,platform_name:cfg.name,accountId:account.id,accountName:account.alias};
  }finally{await browser.close()}
 }catch(error){return {alive:false,logged_in:false,by:'window-closed',platform:key,platform_name:cfg.name,accountId:account.id,accountName:account.alias,error:`「${account.alias}」的窗口没开着，点「打开登录窗口」把 ${cfg.name} 登录页打开再检测`}}
}
function openCreatorPlatform(platform:string){
 const key=(platform in PLATFORMS?platform:'douyin') as keyof typeof PLATFORMS;
 const account=ensureAccount(key);
 openAccountWindow(account.id);
 return {platform:key,url:PLATFORMS[key].loginUrl,accountId:account.id,accountName:account.alias,port:account.port};
}
const douyinAccount=(preferred?:string)=>{
 const account=resolveAccount('douyin',preferred);
 if(!account)throw Error('还没有抖音账号：先去「矩阵分发」添加一个抖音账号，点「打开登录窗口」扫码登录，再回来搜索');
 return account;
};
async function creatorDouyinHotboard(){
 /* 热榜也不要另起 Google Chrome：统一走「一键部署」装的 Ego Lite（找不到再退 Chrome/Edge），
    并用独立 profile + 首启标记，避免每次都停在 ego 的欢迎引导页。 */
 const [{resolveBrowser},{ensureBrowserProfile},{chromium}]=await Promise.all([
  import('../browser-executable.ts'),import('../browser-profile.ts'),import('playwright-core')]);
 const target=resolveBrowser('ego');
 if(!target)throw Error('还没有可用的浏览器：先点右上角「一键部署」安装 Ego Lite');
 const profile=path.join(process.env.EMOTIONCUT_USER_DATA||path.join(process.cwd(),'.dev'),'creator','hotboard-profile');
 ensureBrowserProfile(profile);
 const context=await chromium.launchPersistentContext(profile,{executablePath:target.path,headless:true,locale:'zh-CN',args:['--no-first-run','--no-default-browser-check'],timeout:30_000});
 try{
  const page=context.pages()[0]||await context.newPage();const apiItems:Array<{word:string;hot:number}>=[];
  page.on('response',async response=>{if(!response.url().includes('hot/search/list'))return;try{const json=await response.json() as {data?:{word_list?:Array<{word?:string;hot_value?:number}>}};for(const row of json.data?.word_list||[])if(row.word)apiItems.push({word:row.word,hot:Number(row.hot_value)||0})}catch{/* 页面仍可作为兜底 */}});
  try{
   await page.goto('https://www.douyin.com/hot',{waitUntil:'domcontentloaded',timeout:60_000});await page.waitForTimeout(7000);
   if(apiItems.length)return apiItems.slice(0,30);
   return await page.evaluate(()=>{const seen=new Set<string>(),out:Array<{word:string;hot:number}>=[];for(const el of document.querySelectorAll('[data-e2e="hot-item"],a[href*="/hot/"]')){const word=(el.textContent||'').trim().split('\n')[0].replace(/^\d+[.、\s]*/,'').trim();if(word.length>=3&&word.length<=60&&!seen.has(word)){seen.add(word);out.push({word,hot:0})}}return out.slice(0,30)});
  }finally{await page.close().catch(()=>{})}
 }finally{await context.close().catch(()=>{})}
}
async function creatorDouyinSearch(keyword:string,limit:number){
 const account=douyinAccount(),port=account.port;
 const {chromium}=await import('playwright-core');
 /* 抓取一律走矩阵分发里同一个 Ego 账号窗口：登录态、验证码、指纹都在那儿，
    不再另起一个 Google Chrome（异地会话会直接撞安全验证页）。 */
 let session:import('playwright-core').Browser
 try{session=await chromium.connectOverCDP(`http://127.0.0.1:${port}`)}
 catch{throw Error(`「${account.alias}」的 Ego 浏览器窗口没开着（端口 ${port}）。先点「打开登录窗口」，在里面扫码登录一次，再回来搜索。`)}
 const scrape=async(page:import('playwright-core').Page)=>{await page.goto(`https://www.douyin.com/search/${encodeURIComponent(keyword)}?type=video&sort_type=2&publish_time=7`,{waitUntil:'domcontentloaded',timeout:60_000});await page.waitForTimeout(8000);for(let i=0;i<4;i++){await page.mouse.wheel(0,1600);await page.waitForTimeout(900)}return page.evaluate((max)=>{const out:Array<{url:string;id:string;txt:string;title:string;author:string;like:number;dur:string}>=[],seen=new Set<string>();for(const a of document.querySelectorAll<HTMLAnchorElement>('a[href*="/video/"],a[href*="/note/"]')){const match=a.href.match(/\/(?:video|note)\/(\d+)/);if(!match||seen.has(match[1]))continue;let card:HTMLElement|null=a.closest('li');if(!card){card=a;for(let i=0;i<3&&card.parentElement;i++)card=card.parentElement}const txt=(card.innerText||a.innerText||'').replace(/\s+/g,' ').trim();if(txt.length<3||txt.length>500)continue;seen.add(match[1]);const likeMatch=txt.match(/(?:点赞|赞)\s*([\d.万wW]+)/),author=(txt.match(/@([^\s]+)/)||[])[1]||'';out.push({url:a.href,id:match[1],txt,title:txt.slice(0,100),author,like:likeMatch?Number(likeMatch[1].replace(/万|w/gi,''))*(/万|w/i.test(likeMatch[1])?10000:1):0,dur:''});if(out.length>=max)break}return out},limit)};
 try{
  const context=session.contexts()[0]
  if(!context)throw Error('Ego 里没有可用的浏览器上下文，请重新打开登录窗口')
  const previous=context.pages().at(-1),worker=await context.newPage()
  try{
   const rows=await scrape(worker)
   if(!rows.length)throw Error('这次没有抓到作品卡片。可能是抖音要求登录或触发了安全验证，请在已打开的 Ego 窗口里完成后再点一次。')
   return rows
  }finally{await worker.close().catch(()=>{});await previous?.bringToFront().catch(()=>{})}
 }finally{await session.close().catch(()=>{})}
}
type JsonRecord=Record<string,unknown>;
const jsonRecord=(value:unknown):value is JsonRecord=>!!value&&typeof value==='object'&&!Array.isArray(value);
function firstString(value:unknown):string{
 if(typeof value==='string')return value.trim();
 if(Array.isArray(value))for(const item of value){const found=firstString(item);if(found)return found}
 if(jsonRecord(value))for(const key of ['url','uri','src','text','content']){const found=firstString(value[key]);if(found)return found}
 return '';
}
function findAweme(value:unknown):JsonRecord|undefined{
 const queue:unknown[]=[value],seen=new Set<unknown>();let visited=0;
 while(queue.length&&visited++<20_000){const item=queue.shift();if(!item||typeof item!=='object'||seen.has(item))continue;seen.add(item);
  if(jsonRecord(item)){
   const direct=(jsonRecord(item.aweme_detail)&&item.aweme_detail)||(jsonRecord(item.aweme)&&item.aweme);
   if(direct&&jsonRecord(direct.video))return direct;
   if(jsonRecord(item.video)&&(typeof item.aweme_id==='string'||typeof item.desc==='string'))return item;
   queue.push(...Object.values(item));
  }else if(Array.isArray(item))queue.push(...item);
 }
}
function subtitleUrls(aweme:JsonRecord):string[]{
 const video=jsonRecord(aweme.video)?aweme.video:{};const out:string[]=[];
 const visit=(value:unknown,key='')=>{if(Array.isArray(value)){for(const item of value)visit(item,key);return}if(!jsonRecord(value))return;
  for(const [name,item] of Object.entries(value)){const path=(key+'.'+name).toLowerCase();
   if(/subtitle|caption/.test(path)&&(/url|uri/.test(name.toLowerCase())||typeof item==='string')){const url=firstString(item);if(/^https?:\/\//i.test(url))out.push(url)}
   if(path.split('.').length<7)visit(item,path);
  }
 };
 visit(video,'video');return [...new Set(out)];
}
function playUrls(aweme:JsonRecord):string[]{
 const video=jsonRecord(aweme.video)?aweme.video:{};const out:string[]=[];
 for(const key of ['play_addr','play_addr_h264','play_addr_265','download_addr']){const value=video[key];if(!value)continue;
  if(jsonRecord(value)&&Array.isArray(value.url_list))for(const url of value.url_list)if(typeof url==='string'&&/^https?:\/\//i.test(url))out.push(url);
  const direct=firstString(value);if(/^https?:\/\//i.test(direct))out.push(direct);
 }
 return [...new Set(out)];
}
function transcriptText(raw:string):string{
 const cleaned=raw.trim();if(!cleaned)return '';
 try{const json=JSON.parse(cleaned) as unknown,out:string[]=[];const seen=new Set<string>();
  const visit=(value:unknown)=>{if(Array.isArray(value)){for(const item of value)visit(item);return}if(!jsonRecord(value))return;
   for(const [name,item] of Object.entries(value)){const lower=name.toLowerCase();if(typeof item==='string'&&/(^|_)(text|utf8|content|sentence|utterance)$/.test(lower)){const text=item.replace(/<[^>]+>/g,'').trim();if(text.length>0&&text.length<1000&&!seen.has(text)){seen.add(text);out.push(text)}}else if(typeof item==='object')visit(item)}
  };visit(json);if(out.length)return out.join('\n');
 }catch{/* VTT/SRT/plain text falls through */}
 return cleaned.split(/\r?\n/).filter(line=>line.trim()&&!/^(WEBVTT|\d+|\d{2}:\d{2}[:.,][\d:,.-]+\s*-->)/.test(line.trim())).map(line=>line.replace(/<[^>]+>/g,'').trim()).filter(Boolean).join('\n');
}
const runProcess=(command:string,args:string[])=>new Promise<void>((resolve,reject)=>{const child=spawn(command,args,{windowsHide:true,stdio:['ignore','ignore','pipe']});let stderr='';child.stderr.on('data',chunk=>{stderr+=String(chunk)});child.on('error',reject);child.on('exit',code=>code===0?resolve():reject(Error(stderr.trim()||`${path.basename(command)} 退出码 ${code}`)))});
const runNodeWorker=(script:string,args:string[])=>new Promise<string>((resolve,reject)=>{const node=process.versions.electron?(process.env.EMOTIONCUT_NODE||process.env.npm_node_execpath||'node'):process.execPath;const child=spawn(node,[script,...args],{windowsHide:true,env:process.env,stdio:['ignore','pipe','pipe']});let stdout='',stderr='';child.stdout.on('data',chunk=>{stdout+=String(chunk)});child.stderr.on('data',chunk=>{stderr+=String(chunk)});child.on('error',error=>reject(Error(error.message.includes('ENOENT')?'语音转写需要 Node.js 20+；请在环境向导安装后重试':error.message)));child.on('exit',code=>code===0?resolve(stdout):reject(Error(stderr.trim()||`语音识别进程退出码 ${code}`)))});
async function transcribeMedia(context:import('playwright-core').BrowserContext,url:string):Promise<string>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'emotioncut-stt-')),source=path.join(dir,'source.mp4'),pcm=path.join(dir,'speech.f32le');
 try{
  const response=await context.request.get(url,{timeout:120_000});if(!response.ok())throw Error(`视频下载失败（HTTP ${response.status()}）`);const body=await response.body();if(body.length>300*1024*1024)throw Error('视频超过 300MB，暂不自动转写');fs.writeFileSync(source,body);
  await runProcess(media.ffmpegPath(),['-hide_banner','-loglevel','error','-y','-i',source,'-vn','-ac','1','-ar','16000','-f','f32le',pcm]);
  const worker=fileURLToPath(new URL('../creator-stt-worker.mjs',import.meta.url));const cache=path.join(process.env.EMOTIONCUT_USER_DATA||path.join(process.cwd(),'.dev'),'models','whisper');
  const output=JSON.parse(await runNodeWorker(worker,[pcm,cache])) as {text?:string};return (output.text||'').trim();
 }finally{fs.rmSync(dir,{recursive:true,force:true})}
}
async function creatorDouyinWork(input:string){
 const normalized=input.replace(/\\([:/])/g,'$1'),found=normalized.match(/https?:\/\/[^\s<>"“”]+/i)?.[0]?.replace(/[，。；;、）)\]】]+$/g,'');const candidate=found||(/^https?:\/\//i.test(normalized.trim())?normalized.trim():`https://${normalized.trim()}`);let parsed:URL;try{parsed=new URL(candidate)}catch{throw Error('没有从分享文案中识别到抖音作品链接')}
 if(!/(^|\.)douyin\.com$/i.test(parsed.hostname))throw Error('当前只支持抖音作品链接')
 const account=douyinAccount();
 const {chromium}=await import('playwright-core');let browser:import('playwright-core').Browser
 try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${account.port}`)}catch{throw Error(`「${account.alias}」的 Ego 浏览器窗口没开着（端口 ${account.port}）。先点「打开抖音登录窗口」，扫码登录一次再抓作品文案`)}
 try{const context=browser.contexts()[0];if(!context)throw Error('没有可用的抖音登录浏览器');const page=await context.newPage();try{
  const payloads:unknown[]=[],pending:Promise<void>[]=[];
  page.on('response',response=>{if(!/(aweme\/detail|aweme\/v1\/web\/aweme\/detail|iteminfo)/i.test(response.url()))return;pending.push(response.json().then(value=>{payloads.push(value)}).catch(()=>{}))});
  await page.goto(parsed.href,{waitUntil:'domcontentloaded',timeout:60_000});await page.waitForTimeout(6000);await Promise.allSettled(pending)
  const result=await page.evaluate(()=>{const meta=(name:string)=>document.querySelector<HTMLMetaElement>(`meta[property="${name}"],meta[name="${name}"]`)?.content||'';const body=(document.body?.innerText||'').replace(/\n{3,}/g,'\n\n').trim();const title=meta('og:title')||document.title;const description=meta('og:description')||meta('description');const blocked=/扫码登录|登录后即可|验证码|安全验证/.test(body.slice(0,2500));const useful=body.split('\n').map(x=>x.trim()).filter(x=>x.length>1&&!/^(首页|推荐|关注|消息|我的|登录|搜索)$/.test(x)).slice(0,220).join('\n');return {url:location.href,title,description,text:useful.slice(0,16000),blocked}})
  if(result.blocked&&result.text.length<300)throw Error('抖音要求登录或安全验证，请在登录窗口完成后重试')
  const aweme=payloads.map(findAweme).find(Boolean);let transcript='',transcriptSource='',transcriptError='';
  if(aweme)for(const subtitleUrl of subtitleUrls(aweme)){try{const response=await context.request.get(subtitleUrl,{timeout:30_000});if(!response.ok())continue;transcript=transcriptText(await response.text());if(transcript.length>20){transcriptSource='douyin-caption';break}}catch{/* try the next subtitle */}}
  const mediaUrl=aweme?playUrls(aweme)[0]||'':'';
  if(!transcript&&mediaUrl)try{transcript=await transcribeMedia(context,mediaUrl);if(transcript)transcriptSource='whisper-local'}catch(error){transcriptError=error instanceof Error?error.message:String(error)}
  const description=aweme&&typeof aweme.desc==='string'?aweme.desc:result.description;
  return {...result,title:aweme&&typeof aweme.desc==='string'?aweme.desc.slice(0,120):result.title,description,transcript,transcriptSource,transcriptError,mediaUrl}
 }finally{await page.close()}}
 finally{await browser.close()}
}
/** 非抖音平台的指定作品：同样走矩阵分发里该平台的 Ego 账号，抓标题/正文供 Agent 拆解。 */
async function creatorPlatformWork(platform:string,input:string){
 const key=(platform in creatorPlatforms?platform:'douyin') as keyof typeof creatorPlatforms,cfg=creatorPlatforms[key];
 const normalized=input.replace(/\\([:/])/g,'$1'),found=normalized.match(/https?:\/\/[^\s<>"“”]+/i)?.[0]?.replace(/[，。；;、）)\]】]+$/g,'');
 const candidate=found||(/^https?:\/\//i.test(normalized.trim())?normalized.trim():`https://${normalized.trim()}`);let parsed:URL;
 try{parsed=new URL(candidate)}catch{throw Error(`没有从分享文案中识别到${cfg.name}作品链接`)}
 const host=parsed.hostname.toLowerCase(),domain=cfg.domain.toLowerCase();
 if(host!==domain&&!host.endsWith(`.${domain}`))throw Error(`这条链接不是${cfg.name}的（${host}），请粘贴 ${domain} 的作品链接`)
 const account=resolveAccount(key);
 if(!account)throw Error(`还没有${cfg.name}账号：先去「矩阵分发」添加一个，登录一次就能抓取`)
 const {chromium}=await import('playwright-core');let browser:import('playwright-core').Browser
 try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${account.port}`)}catch{throw Error(`「${account.alias}」的 Ego 浏览器窗口没开着（端口 ${account.port}）。先点「打开登录窗口」，登录一次再抓作品文案`)}
 try{
  const context=browser.contexts()[0];if(!context)throw Error('没有可用的浏览器上下文')
  const previous=context.pages().at(-1),page=await context.newPage()
  try{
   await page.goto(parsed.href,{waitUntil:'domcontentloaded',timeout:60_000});await page.waitForTimeout(6000)
   const result=await page.evaluate(()=>{const meta=(name:string)=>document.querySelector<HTMLMetaElement>(`meta[property="${name}"],meta[name="${name}"]`)?.content||'';const body=(document.body?.innerText||'').replace(/\n{3,}/g,'\n\n').trim();const blocked=/扫码登录|登录后即可|验证码|安全验证|请登录/.test(body.slice(0,2500));const useful=body.split('\n').map(x=>x.trim()).filter(x=>x.length>1&&!/^(首页|推荐|关注|消息|我的|登录|搜索)$/.test(x)).slice(0,220).join('\n').slice(0,16000);return {url:location.href,title:meta('og:title')||document.title,description:meta('og:description')||meta('description'),text:useful,blocked}})
   if(result.blocked&&result.text.length<200)throw Error(`${cfg.name}要求登录或安全验证，请在登录窗口完成后重试`)
   return {...result,platform:key,platform_name:cfg.name,accountName:account.alias}
  }finally{await page.close().catch(()=>{});await previous?.bringToFront().catch(()=>{})}
 }finally{await browser.close().catch(()=>{})}
}

export async function standardApi(req:IncomingMessage,res:ServerResponse,next:()=>void){
 const url=new URL(req.url||'/','http://localhost');
 // The upstream editor addresses bundled assets at the origin root. Keep
 // those same-origin routes working in both dev and the packaged App.
 if (/^\/(wallpapers|cursors|wasm)(?:\/|$)/.test(url.pathname)) {res.writeHead(307,{Location:'/openscreen'+url.pathname+url.search});return res.end()}
 if(!url.pathname.startsWith('/api/'))return next();
 // Vite only binds loopback. Reject cross-site writes to the local filesystem service.
 const origin=req.headers.origin;if(origin&&origin!==`http://${req.headers.host}`&&origin!==`https://${req.headers.host}`)return sendJson(res,403,{ok:false,error:'不接受跨站请求'});
 trackStorageRequest(req,res);if(process.platform==='darwin')scheduleStorageMaintenance();
 if(url.pathname==='/api/creator/agent')try{return await creatorAgentBridge(req,res)}catch(e){return sendJson(res,500,{ok:false,error:e instanceof Error?e.message:String(e)})}
 if(url.pathname==='/api/creator/agent/choice')try{return await creatorAgentChoiceApi(req,res)}catch(e){return sendJson(res,500,{ok:false,error:e instanceof Error?e.message:String(e)})}
 if(req.method==='GET'&&url.pathname==='/api/creator/platform-status')try{
  const platform=url.searchParams.get('platform')||'douyin';
  return sendJson(res,200,{ok:true,result:await creatorPlatformStatus(platform),checkedAt:new Date().toISOString()});
 }catch{return sendJson(res,200,{ok:true,result:{alive:false,logged_in:false,by:'offline',platform:url.searchParams.get('platform')||'douyin'},checkedAt:new Date().toISOString()})}
 if(req.method==='POST'&&url.pathname==='/api/creator/platform-connect')try{
  const input=await bodyJson(req),platform=String(input.platform||'douyin');
  return sendJson(res,200,{ok:true,result:openCreatorPlatform(platform)});
 }catch(e){return sendJson(res,502,{ok:false,error:e instanceof Error?e.message:String(e)})}
 if(req.method==='GET'&&url.pathname==='/api/creator/douyin-hotboard')try{return sendJson(res,200,{ok:true,result:await creatorDouyinHotboard(),checkedAt:new Date().toISOString()})}catch(e){return sendJson(res,502,{ok:false,error:e instanceof Error?e.message:String(e)})}
 if((req.method==='GET'||req.method==='POST')&&url.pathname.startsWith('/api/creator/cover-memory'))try{
  const suffix=url.pathname.endsWith('/analyze')?'/api/cover/analyze':'/api/cover/spec';
  const upstream=await fetch('http://127.0.0.1:7891'+suffix,{method:req.method,headers:req.method==='POST'?{'Content-Type':'application/json'}:undefined,body:req.method==='POST'?JSON.stringify(await bodyJson(req)):undefined,signal:AbortSignal.timeout(300_000)});
  return sendJson(res,upstream.status,await upstream.json());
 }catch(e){return sendJson(res,502,{ok:false,error:'原工作台封面记忆服务未启动：'+(e instanceof Error?e.message:String(e))})}
 if(req.method==='GET'&&url.pathname==='/api/creator/douyin-search')try{const keyword=(url.searchParams.get('q')||'').trim(),limit=Math.max(10,Math.min(50,Number(url.searchParams.get('n'))||40));if(!keyword)return sendJson(res,400,{ok:false,error:'缺少调研关键词'});return sendJson(res,200,{ok:true,result:await creatorDouyinSearch(keyword,limit),checkedAt:new Date().toISOString()})}catch(e){return sendJson(res,502,{ok:false,error:e instanceof Error?e.message:String(e)})}
 if(req.method==='GET'&&url.pathname==='/api/creator/douyin-work')try{const link=(url.searchParams.get('url')||'').trim();if(!link)return sendJson(res,400,{ok:false,error:'缺少作品链接'});return sendJson(res,200,{ok:true,result:await creatorDouyinWork(link),checkedAt:new Date().toISOString()})}catch(e){return sendJson(res,502,{ok:false,error:e instanceof Error?e.message:String(e)})}
 if(req.method==='GET'&&url.pathname==='/api/creator/platform-work')try{const platform=(url.searchParams.get('platform')||'douyin').trim(),link=(url.searchParams.get('url')||'').trim();if(!link)return sendJson(res,400,{ok:false,error:'缺少作品链接'});return sendJson(res,200,{ok:true,result:await creatorPlatformWork(platform,link),checkedAt:new Date().toISOString()})}catch(e){return sendJson(res,502,{ok:false,error:e instanceof Error?e.message:String(e)})}
 try{if(req.method==='GET'&&url.pathname==='/api/environment')return sendJson(res,200,await checkCreationEnvironment());if(url.pathname.startsWith('/api/matrix/'))return await matrixApi(req,res);if(req.method==='GET'&&url.pathname==='/api/creator/production-readiness')return sendJson(res,200,await creatorProductionReadiness());if(url.pathname.startsWith('/api/creator/projects'))return await creatorProjectsApi(req,res);if(url.pathname.startsWith('/api/creator/topics'))return await creatorTopicsApi(req,res);if(url.pathname.startsWith('/api/motion/'))return await motionApi(req,res);if(url.pathname.startsWith('/api/upgrade/'))return await upgradeApi(req,res);if(url.pathname.startsWith('/api/general/')){req.url=req.url!.slice('/api/general'.length);return await handleGeneralApi(req,res)}
 if(url.pathname.startsWith('/api/editor/')){if(await handleSharedSettings(req,res,url.pathname.slice('/api/editor/'.length).split('/').map(decodeURIComponent)))return;return await handleEditorApi(req,res);}
 if(url.pathname==='/api/agent-settings/runs/clear'){if(req.method!=='POST')return sendJson(res,405,{ok:false,error:'只接受 POST'});return sendJson(res,200,{ok:true,result:clearAgentRuns(await bodyJson(req).catch(()=>({})) as {statuses?:string[];keep?:number})})}
 if(url.pathname==='/api/agent-settings'){const result=req.method==='PUT'?await saveAgentSettings(await bodyJson(req)):req.method==='GET'?await agentSettingsView():undefined;if(result)return sendJson(res,200,{ok:true,result});}
 return sendJson(res,404,{ok:false,error:'Standard 没有这个接口'});
 }catch(e){if(!res.headersSent)sendJson(res,500,{ok:false,error:e instanceof Error?e.message:String(e)});else res.destroy()}
}
