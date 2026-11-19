import type {ConnectionView} from '../studio/connections';
import {useEffect,useRef,useState,type CSSProperties} from 'react';
import {createPortal} from 'react-dom';
import {useNavigate} from 'react-router';
import {AgentMark} from '@/studio/components/AgentMark';
import {UNIFIED_AGENTS} from '@/general/platform';
import {refreshConnections,useConnections} from '@/studio/connection-store';
import {ConnectionActions} from '@/studio/components/ConnectionActions';
import {connectionDefinition,type ConnectionId,type ConnectionMode} from '@/studio/connections';
import type {AgentId} from '@/studio/agent-marks';
import {useAccountAndFirstRun,deployDescription,pendingAgents} from './edition';
import {DEPLOY_ITEMS,openSettings,recheck,runAll,setDeployOpen,summary,syncDeployConnections,useDeploy,useDeployOpen,type CheckAction,type DeployItemId} from './onboarding-store';
import './deploy.css';

const SYMBOL:Record<string,string>={ok:'✓',warn:'!',act:'!',opt:'–',wait:'·',checking:''};

/** 应用内一键安装：下载 → 校验 → 静默装，进度来自服务端的 job。 */
type InstallJob={kind:'openscreen'|'ego';state:'idle'|'downloading'|'verifying'|'installing'|'done'|'failed';percent?:number;received:number;total:number;source:string;step:string;error?:string};
const INSTALL_RUNNING:readonly string[]=['downloading','verifying','installing'];
const installUrl=(kind:string)=>kind==='ego'?'/api/editor/browser/install':'/api/editor/onboarding/openscreen/install';
const mb=(bytes:number)=>`${(bytes/1024**2).toFixed(1)} MB`;
/** 一行话把进度说清：下载看体积，校验和安装看阶段。 */
function installText(job:InstallJob){
 if(job.state==='failed')return job.error||'没装成';
 if(job.state==='done')return '装好了';
 if(job.state==='downloading')return job.total?`${job.step} ${mb(job.received)} / ${mb(job.total)}`:`${job.step} · 已下 ${mb(job.received)}`;
 return job.step;
}


/** The sign-in the desktop shell holds feeds the account row; and on this Mac's first sign-in, the window opens once by itself. */

function Ring({ok,total}:{ok:number;total:number}){
 return <svg className="ot-deploy-ring" viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="11" fill="#ffffff22" stroke="#ffffff44" strokeWidth="3"/><circle cx="14" cy="14" r="11" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeDasharray="69" strokeDashoffset={69-69*ok/Math.max(1,total)} transform="rotate(-90 14 14)"/></svg>;
}

/** The header button: bright purple while something still needs doing, a quiet tick once this Mac is ready. */
export function DeployButton(){
 useAccountAndFirstRun();
 const connections=useConnections();
 useEffect(()=>{if(UNIFIED_AGENTS)syncDeployConnections(connections.connections)},[connections.connections]);
 useEffect(()=>{if(UNIFIED_AGENTS)void refreshConnections()},[]);
 const s=summary(useDeploy());
 if(s.ready)return <button className="ot-deploy-nav is-ready" onClick={()=>setDeployOpen(true)} title="这台电脑已准备好，点开可以重新检查"><span className="ot-deploy-tick" aria-hidden="true">✓</span>已就绪</button>;
 return <button className="ot-deploy-nav" onClick={()=>setDeployOpen(true)} aria-label={`一键部署，必须项已就绪 ${s.okRequired} / ${s.required}`}>
  <Ring ok={s.okRequired} total={s.required}/><span>{s.done?`一键部署 · ${s.okRequired} / ${s.required}`:'一键部署'}</span>
 </button>;
}

/** The home page's strip, only while this Mac is not ready yet. */
export function DeployStrip(){
 const s=summary(useDeploy());
 if(s.ready||!s.done)return null;
 return <section className="ot-deploy-strip" aria-label="一键部署">
  <div><b>先花 2 分钟，把这台电脑准备好</b><span>{deployDescription}</span></div>
  <button className="ot-deploy-nav" onClick={()=>setDeployOpen(true)}><Ring ok={s.okRequired} total={s.required}/><span>一键部署</span></button>
 </section>;
}

export function DeployWizard(){
 const open=useDeployOpen(),snap=useDeploy(),s=summary(snap),navigate=useNavigate();
 const connections=useConnections();
 useEffect(()=>{if(UNIFIED_AGENTS)syncDeployConnections(connections.connections)},[connections.connections]);
 const dialog=useRef<HTMLDivElement>(null);
 const [watching,setWatching]=useState<DeployItemId|null>(null);
 const [install,setInstall]=useState<InstallJob|null>(null);
 const installing=Boolean(install&&INSTALL_RUNNING.includes(install.state));
 // every opening is a fresh check of this Mac
 useEffect(()=>{if(open){void runAll();if(UNIFIED_AGENTS)void refreshConnections()}},[open]);
 useEffect(()=>{if(!open)return;const key=(e:KeyboardEvent)=>{if(e.key==='Escape')setDeployOpen(false)};window.addEventListener('keydown',key);requestAnimationFrame(()=>dialog.current?.focus());return()=>window.removeEventListener('keydown',key)},[open]);
 // after a fix that happens elsewhere (Terminal, a browser, settings), check that row again until it is done
 useEffect(()=>{
  if(!watching)return;
  let tries=0;const timer=setInterval(()=>{tries++;void recheck(watching).then(r=>{if(r.state==='ok'||r.state==='warn'||tries>=40)setWatching(null)})},4000);
  return()=>clearInterval(timer);
 },[watching]);
 // 安装进度：下载可能要几分钟，所以期间不重查整台机器，只盯这一个 job
 useEffect(()=>{
  if(!open||!install||!INSTALL_RUNNING.includes(install.state))return;
  const kind=install.kind;let alive=true,finished=false;
  const timer=setInterval(()=>{
   if(finished)return;
   void fetch(installUrl(kind),{cache:'no-store'}).then(r=>r.json()).then(body=>{
    if(!alive||finished||!body?.ok||!body.result)return;
    const next=body.result as InstallJob;
    if(next.state==='done'&&!next.error){finished=true;void recheck('openscreen').then(()=>{if(alive)setInstall(null)});return}
    setInstall(next);
   }).catch(()=>{});
  },1200);
  return()=>{alive=false;clearInterval(timer)};
 },[open,install?.kind,install?.state]);
 if(!open)return null;
 const jobCall=async(kind:string,method:'GET'|'POST'='GET'):Promise<InstallJob>=>{
  const response=await fetch(installUrl(kind),method==='POST'?{method,headers:{'Content-Type':'application/json'},body:'{}'}:{cache:'no-store'});
  const body=await response.json().catch(()=>null);
  if(!response.ok||!body?.ok)throw Error(body?.error||'安装任务没起来');
  return body.result as InstallJob;
 };
 /** 点「一键安装」就地开始；进度条自己走，装完自动再检测这一项。 */
 const startInstall=async(kind:string)=>{
  setWatching(null);
  setInstall({kind:kind as InstallJob['kind'],state:'downloading',percent:0,received:0,total:0,source:'',step:'正在准备下载'});
  try{setInstall(await jobCall(kind,'POST'))}
  catch(failure){setInstall({kind:kind as InstallJob['kind'],state:'failed',received:0,total:0,source:'',step:'',error:failure instanceof Error?failure.message:String(failure)})}
 };
 const act=async(id:DeployItemId,action:CheckAction)=>{
  if(action.kind==='install'){void startInstall(action.target||'openscreen');return}
  if(action.kind==='codex-login'){await fetch('/api/editor/agent-settings/installation/codex/login',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});setWatching(id)}
  else if(action.kind==='reader-login'){await fetch('/api/reader-browser',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({site:action.target})});setWatching(id)}
  else if(action.kind==='link'){window.open(action.target,'_blank','noreferrer');setWatching(id)}
  else if(action.kind==='system'){await fetch(`/api/editor/onboarding/system/${encodeURIComponent(action.target||'')}`,{method:'POST'})}
  else if(action.kind==='route'){setDeployOpen(false);navigate(action.target||'/')}
  else{setDeployOpen(false);openSettings(action.target||'agents',undefined,true)}
 };
 const manage=(agentId?:ConnectionId,mode?:ConnectionMode)=>{setDeployOpen(false);openSettings('agents',agentId,true,mode)};
 const prominent=(item:ConnectionView)=>item.cliInstalled||item.connected||item.hasKey||!!connections.waiting[item.id];
 const renderAgent=(connection:ConnectionView)=><div key={connection.id} className={`ot-deploy-agent${connections.waiting[connection.id]?' is-waiting':connection.connected?' is-on':!connection.cliInstalled?' is-off':''}`}><div className="ot-deploy-agent-head"><b>{connection.name}</b><button type="button" onClick={()=>manage(connection.id)}>设置 ›</button><AgentMark id={connection.id as AgentId} size={24}/></div><small>{connections.waiting[connection.id]?`终端已打开，在里面登录 ${connection.name}`:connection.mode==='api'?`API 密钥 · ${connection.relay?'中转站':'官方'}`:connection.cliInstalled?'已找到命令行':'没找到命令行'}{connection.mode==='cli'&&connectionDefinition(connection.id).acpVerification==='docs'?' · 未实机验证':''}</small><ConnectionActions connection={connection} compact onConfigure={mode=>manage(connection.id,mode)}/></div>;
 const checking=snap.running;
 const title=s.ready?'可以开工了':checking?'正在检查这台电脑':`还差 ${s.required-s.okRequired} 项`;
 const sub=s.ready?(s.allOk?'全部就绪':'可选的几项，用到时再登录也行'):checking?`已查 ${s.done} / ${s.total} 项`:'点紫色的按钮补上，补完自动再查一遍';
 let n=0,group='';
 return createPortal(<div className="ot-deploy-veil" onMouseDown={e=>{if(e.target===e.currentTarget)setDeployOpen(false)}}>
  <div ref={dialog} className={`ot-deploy${s.ready?' is-ready':''}`} role="dialog" aria-modal="true" aria-labelledby="ot-deploy-title" tabIndex={-1}>
   <div className="ot-deploy-left">
    <button className="ot-deploy-close" onClick={()=>setDeployOpen(false)}>✕ 稍后再说</button>
    <span className="ot-deploy-glow" aria-hidden="true"/><div className="ot-deploy-mark" aria-hidden="true"/>
    <h2 id="ot-deploy-title">{title}</h2><p aria-live="polite">{sub}</p>
    <div className="ot-deploy-meter" aria-hidden="true">{DEPLOY_ITEMS.map(item=>{const st=snap.rows[item.id]?.state;return <i key={item.id} className={st==='ok'||st==='warn'?'ok':st==='act'?'act':''}/>})}</div>
    <p className="ot-deploy-privacy">🔒 只检测电脑性能，以及各个 agent 装在哪、能不能用。<br/>不会读取你的任何账号信息，结果只存在本机。</p>
    <button className="ot-deploy-go" onClick={()=>setDeployOpen(false)} tabIndex={s.ready?0:-1}>可以开工了 →</button>
   </div>
   <div className="ot-deploy-list">
    {DEPLOY_ITEMS.map(item=>{
     const optional='optional' in item,label=optional?'可选 · 用到再登录':'必须';
     const head=label!==group?(group=label,<div className="ot-deploy-grp" key={`g-${label}`}><span>{label}</span><span>{optional?'可以跳过':'全部就绪才能开工'}</span></div>):null;
     const row=snap.rows[item.id],state=row?.state||'wait',result=row?.result,done=state!=='wait'&&state!=='checking';
     // 这一行正在装 / 上次装失败：进度直接顶在行里，不再跳到网页
     const job=item.id==='openscreen'?install:null,runningJob=Boolean(job&&INSTALL_RUNNING.includes(job.state)),failedJob=job?.state==='failed';
     const msg=job&&(runningJob||failedJob)?installText(job):state==='checking'&&!result?'检查中…':state==='wait'?'等待检查':result?.text||'';
     return [head,<div key={item.id} className="ot-deploy-item" data-s={state} style={{'--i':n++} as CSSProperties}>
      <span className="ot-deploy-st">{SYMBOL[state]}</span>
      <div><b>{item.title}</b><small>{msg}</small></div>
      <span className="ot-deploy-slot">{item.id==='agent'&&UNIFIED_AGENTS?<span className="ot-deploy-agent-manage"><button type="button" disabled={connections.loading} onClick={()=>{void refreshConnections();void recheck('agent')}}>重新查找</button><button type="button" onClick={()=>manage()}>在设置里管理 →</button></span>:done&&result?.action&&(state==='act'||state==='opt'||(state==='ok'&&result.action.kind==='system'))?<button className="ot-deploy-act" disabled={runningJob} onClick={()=>void act(item.id,result.action!)}>{runningJob?`安装中${job?.percent!=null?` ${job.percent}%`:''}…`:failedJob?'重试':watching===item.id?'等待完成…':result.action.label}</button>:optional&&state==='ok'?<span className="ot-deploy-tag">可选</span>:null}</span>
      {job&&runningJob&&<div className="ot-deploy-job" aria-live="polite"><i><b style={{width:`${job.percent??6}%`}}/></i></div>}
      {job&&failedJob&&result?.action?.page&&<div className="ot-deploy-job" data-s="failed"><a href={result.action.page} target="_blank" rel="noreferrer">装不上？从官方页面手动下载 ↗</a></div>}
      {item.id==='perf'&&<div className={`ot-deploy-chips${done?'':' is-pending'}`}>{(result?.chips||['CPU','GPU','内存','系统','系统盘','硬件编码']).map(chip=><span key={chip}>{chip}</span>)}</div>}
      {item.id==='agent'&&UNIFIED_AGENTS&&connections.error&&<p role="alert" className="ot-deploy-connection-error">{connections.error}</p>}
      {item.id==='agent'&&UNIFIED_AGENTS&&connections.connections.length>0&&<><div className="ot-deploy-agents ot-deploy-auto-agents">{connections.connections.filter(item=>item.kind==='agent'&&prominent(item)).map(renderAgent)}</div>{connections.connections.some(item=>item.kind==='agent'&&!prominent(item))&&<details className="ot-deploy-agent-more"><summary>更多智能体 · {connections.connections.filter(item=>item.kind==='agent'&&!prominent(item)).length} 个未安装</summary><div className="ot-deploy-agents ot-deploy-auto-agents">{connections.connections.filter(item=>item.kind==='agent'&&!prominent(item)).map(renderAgent)}</div></details>}</>}

      {item.id==='agent'&&(!UNIFIED_AGENTS||!connections.connections.length)&&<div className="ot-deploy-agents">{(result?.agents||pendingAgents.map(id=>({id,name:{codex:'Codex',claude:'Claude Code',kimi:'Kimi',deepseek:'DeepSeek',hermes:'Hermes',minimax:'MiniMax Code',codebuddy:'WorkBuddy',zcode:'ZCode',grok:'Grok',copilot:'GitHub Copilot',cursor:'Cursor',opencode:'OpenCode',qwen:'Qwen Code'}[id],line:'',state:'off' as const,status:''}))).map(tile=>
       <div key={tile.id} className={`ot-deploy-agent${!result?' is-pending':tile.state==='on'?' is-on':tile.state==='off'?' is-off':''}`}>
        <span className="ot-deploy-logo"><AgentMark id={tile.id as AgentId} size={24}/></span>
        <b><i/>{tile.name}</b><small>{result?tile.line:' '}</small><span>{result?tile.status:state==='checking'?'检查中…':'等待检查'}</span>
       </div>)}</div>}
     </div>];
    })}
   </div>
  </div>
 </div>,document.body);
}
