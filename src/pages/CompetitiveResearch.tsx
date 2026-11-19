import {useEffect,useState} from 'react'
import {Clock3,ExternalLink,FilePenLine,Link2,Search,Trash2,UserRoundSearch} from 'lucide-react'
import {useLocation,useNavigate} from 'react-router'
import {createCreatorTask,failCreatorTask,runCreatorTask,updateCreatorTask} from '@/creator/creator-task-store'
import './creator-operations.css'

type Result={title:string;author:string;url:string;summary:string;platform?:string}
type HistoryItem={id:string;createdAt:string;platform:string;query:string;links:string;results:Result[];report:string}
const PLATFORM_KEYS:Record<string,string>={'抖音':'douyin','视频号':'channels','快手':'kuaishou','小红书':'xhs','B站':'bilibili'}
const SESSION={query:'emotioncut:bench-session-query',links:'emotioncut:bench-session-links',report:'emotioncut:bench-session-report',results:'emotioncut:bench-session-results'}
const HISTORY='emotioncut:bench-history-v1'
const readHistory=()=>{try{const value=JSON.parse(localStorage.getItem(HISTORY)||'[]');return Array.isArray(value)?value as HistoryItem[]:[]}catch{return []}}
const extractUrls=(text:string)=>{
 const normalized=text.replace(/\\([:/])/g,'$1')
 const urls=normalized.match(/https?:\/\/[^\s<>"“”]+/gi)||[]
 return [...new Set(urls.map(url=>url.replace(/[，。；;、）)\]】]+$/g,'')))]
}

export default function CompetitiveResearch(){
 const navigate=useNavigate()
 const location=useLocation()
 const [platform,setPlatform]=useState('抖音')
 const [query,setQuery]=useState(()=>sessionStorage.getItem(SESSION.query)||'')
 const [links,setLinks]=useState(()=>sessionStorage.getItem(SESSION.links)||'')
 const [busy,setBusy]=useState(false)
 const [results,setResults]=useState<Result[]>(()=>{try{return JSON.parse(sessionStorage.getItem(SESSION.results)||'[]')}catch{return []}})
 const [report,setReport]=useState(()=>sessionStorage.getItem(SESSION.report)||'')
 const [history,setHistory]=useState<HistoryItem[]>(readHistory)
 const [historyOpen,setHistoryOpen]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const needsBrowser=/(窗口没开着|打开抖音登录窗口|没有抓到样本)/.test(error)
 const openLoginWindow=async()=>{
  setNotice(`正在打开${platform}登录窗口…`)
  try{
   const response=await fetch('/api/creator/platform-connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:PLATFORM_KEYS[platform]||'douyin'})})
   const value=await response.json().catch(()=>({error:'服务没有响应'}));if(!response.ok)throw Error(value.error||'打开失败')
   const name=value.result?.accountName||`${platform}账号`
   setError('');setNotice(`已打开「${name}」的 Ego 登录窗口（和「矩阵分发」里是同一个账号）。扫码登录后，回到这里再点一次搜索。`)
  }catch(cause){setNotice('');setError(cause instanceof Error?cause.message:String(cause))}
 }

 useEffect(()=>{for(const key of ['emotioncut:bench-query','emotioncut:bench-links','emotioncut:bench-report'])localStorage.removeItem(key)},[])
 useEffect(()=>{if(location.hash==='#research-report')window.setTimeout(()=>document.querySelector('.ops__research-report')?.scrollIntoView({behavior:'smooth',block:'start'}),80)},[location.hash,report])
 const saveSession=(next:{query?:string;links?:string;report?:string;results?:Result[]})=>{if(next.query!==undefined)sessionStorage.setItem(SESSION.query,next.query);if(next.links!==undefined)sessionStorage.setItem(SESSION.links,next.links);if(next.report!==undefined)sessionStorage.setItem(SESSION.report,next.report);if(next.results!==undefined)sessionStorage.setItem(SESSION.results,JSON.stringify(next.results))}
 const saveHistory=(items:HistoryItem[])=>{const next=items.slice(0,30);setHistory(next);localStorage.setItem(HISTORY,JSON.stringify(next))}

 const research=async()=>{
  if(!query.trim()&&!links.trim())return
  setBusy(true);setError('');setNotice(`正在用「${platform}」账号的 Ego 浏览器抓取，登录态和「矩阵分发」是同一个…`)
  saveSession({query,links});const label=(query.trim()||links.trim()).replace(/#[^\s#]+/g,'').replace(/https?:\/\/\S+/g,'').trim().slice(0,30)||'指定作品';const taskId=createCreatorTask({title:`[${platform}] 正在提取作品：${label}`,returnTo:'/creator-tools/bench#research-report'})
  try{
   let samples:Result[]=[]
   if(platform==='抖音'&&query.trim()){
    const response=await fetch('/api/creator/douyin-search?q='+encodeURIComponent(query)+'&n=20')
    const value=await response.json();if(!response.ok)throw Error(value.error)
    samples=(value.result||[]).map((x:{title:string;author:string;url:string;txt:string})=>({title:x.title,author:x.author,url:x.url,summary:x.txt,platform:'抖音'}))
   }
   const urls=extractUrls(links)
   if(links.trim()&&!urls.length)throw Error(`没有从分享文案中识别到链接，请确认包含 ${platform} 的作品链接（https://…）`)
   if(urls.length)updateCreatorTask(taskId,{title:`[${platform}] 正在识别口播：${label}`})
   const manual:Result[]=platform==='抖音'?await Promise.all(urls.map(async url=>{
    const response=await fetch('/api/creator/douyin-work?url='+encodeURIComponent(url));const value=await response.json()
    if(!response.ok)throw Error(`${url}\n${value.error||'作品抓取失败'}`)
    const item=value.result||{};return {url:item.url||url,title:item.title||'指定作品',author:'',platform:'抖音',summary:[item.transcript&&`【口播转写 · ${item.transcriptSource==='douyin-caption'?'平台字幕':'本地语音识别'}】\n${item.transcript}`,item.description&&`【作品文案】\n${item.description}`,!item.transcript&&item.transcriptError&&`【转写状态】\n${item.transcriptError}`].filter(Boolean).join('\n\n')}
   })):await Promise.all(urls.map(async url=>{
    const response=await fetch(`/api/creator/platform-work?platform=${encodeURIComponent(PLATFORM_KEYS[platform]||'douyin')}&url=${encodeURIComponent(url)}`)
    const value=await response.json()
    if(!response.ok)throw Error(`${url}\n${value.error||'作品抓取失败'}`)
    const item=value.result||{};return {url:item.url||url,title:item.title||'指定作品',author:'',platform,summary:[item.description&&`【作品文案】\n${item.description}`,item.text&&`【页面原文】\n${item.text}`].filter(Boolean).join('\n\n')}
   }))
   samples=[...samples,...manual]
   // 没抓到就说清楚：抖音是登录态问题，其它平台是关键词搜索还没接、要走链接。
   if(!samples.length)throw Error(platform==='抖音'
    ?'没有抓到样本。抖音搜索要登录态：点「打开登录窗口」，在 Ego 里扫码登录后回来再点一次；也可以改用「方式二」贴作品链接。'
    :`没有抓到${platform}样本。${platform}的关键词搜索还没接好，请用「方式二」粘贴该平台的作品链接；会用矩阵分发里同一账号的 Ego 浏览器抓取。`)
   const actual=samples.slice(0,20);setResults(actual);saveSession({results:actual})
   const taskName=`[${platform}] ${query.trim()||actual[0]?.title||'指定作品'}`
   const text=await runCreatorTask({taskId,title:'正在拆解作品：'+taskName,returnTo:'/creator-tools/bench#research-report',system:'你是短视频对标研究员和口播主编。对指定作品，先从抓取内容中归档标题、正文、字幕或口播原文，剔除平台导航、按钮和推荐流等界面噪声；不得补写原文中不存在的句子，也不得编造播放量、评论或作者数据。然后分析选题、钩子、叙事结构、信息密度、实操价值和转化设计。明确区分可复用的事实与方法、不能照搬的表达。资料不足时必须指出缺口。输出中文结构化报告。',user:`平台：${platform}\n账号、领域或关键词：${query}\n抓取到的真实样本：${JSON.stringify(actual)}\n请依次输出：1. 每条指定作品的原文/字幕整理；2. 钩子、结构、价值点与不足；3. 可复用事实和不可照搬表达；4. 三个不抄袭的差异化改写方向；5. 推荐口播稿大纲。`})
   setReport(text);saveSession({report:text})
   setNotice(`已用「${platform}」账号的 Ego 浏览器抓到 ${actual.length} 条样本，并完成 Agent 拆解。`)
   const record:HistoryItem={id:crypto.randomUUID(),createdAt:new Date().toISOString(),platform,query,links,results:actual,report:text}
   saveHistory([record,...history])
  }catch(e){failCreatorTask(taskId,e);setError(e instanceof Error?e.message:'研究失败')}finally{setBusy(false)}
 }

 const restore=(item:HistoryItem)=>{setPlatform(item.platform);setQuery(item.query);setLinks(item.links);setResults(item.results);setReport(item.report);saveSession({query:item.query,links:item.links,results:item.results,report:item.report});setHistoryOpen(false)}
 const removeHistory=(id:string)=>saveHistory(history.filter(item=>item.id!==id))
 const clearHistory=()=>{if(confirm('清空全部对标研究历史？当前正在编辑的内容不会受影响。'))saveHistory([])}
 const toScript=()=>{const first=results[0],topic=(query.trim()||first?.title||'对标作品改写').replace(/#[^\s#]+/g,'').replace(/\s{2,}/g,' ').trim();localStorage.setItem('emotioncut:spoken-script-topic',topic);localStorage.setItem('emotioncut:spoken-script-material',`【对标作品抓取原文 · ${platform} · Ego 浏览器】\n${results.map((item,index)=>`${index+1}. ${item.title}\n来源：${item.url}\n${item.summary||'未抓到正文'}`).join('\n\n')}\n\n【Agent 对标报告】\n${report}`);navigate('/creator-tools/script')}

 return <main className="ops"><header className="ops__header"><div><h1 className="ec-grad-title">对标研究</h1><p>粘贴完整分享文案也可以，系统会自动提取其中的作品链接。当前工作在退出 eMotionCut 后清空，已完成报告单独进入本机历史记录。</p></div><div className="ops__header-actions"><button onClick={()=>setHistoryOpen(value=>!value)}><Clock3 size={15}/>历史记录 <span>{history.length}</span></button></div></header>{historyOpen&&<section className="ops__history"><header><div><h2>对标研究历史</h2><p>只保存在这台电脑的当前用户目录，不会进入源码或安装包。</p></div>{history.length>0&&<button className="danger" onClick={clearHistory}><Trash2 size={14}/>清空</button>}</header>{history.length?<div>{history.map(item=><article key={item.id}><button className="restore" onClick={()=>restore(item)}><b>{item.query||item.results[0]?.title||'指定作品研究'}</b><small>{new Date(item.createdAt).toLocaleString('zh-CN')} · {item.platform} · {item.results.length} 个样本</small></button><button className="delete" aria-label="删除这条历史" onClick={()=>removeHistory(item.id)}><Trash2 size={14}/></button></article>)}</div>:<p className="empty">还没有历史记录</p>}</section>}<section className="ops__main ops__research"><div className="ops__platforms">{['抖音','视频号','快手','小红书','B站'].map(p=><button key={p} data-on={platform===p||undefined} onClick={()=>setPlatform(p)}>{p}</button>)}</div><div className="ops__research-choice"><label><span><Search size={15}/><b>方式一：搜索账号或领域</b></span><small>用关键词寻找一批公开样本，观察共同选题和表达。</small><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="例如：AI 工具教学，或某位博主名称"/></label><label><span><Link2 size={15}/><b>方式二：粘贴分享文案或链接</b></span><small>可直接粘贴抖音“复制链接”得到的整段文字，也支持每行一个链接。</small><textarea value={links} onChange={e=>setLinks(e.target.value)} placeholder={'例如：三分钟教你…… https://v.douyin.com/xxxx/ 复制此链接，打开抖音观看\n也可以直接粘贴 https://www.douyin.com/video/……'}/></label></div><button className="ops__research-run" onClick={()=>void research()} disabled={busy||(!query.trim()&&!links.trim())}><Search size={15}/>{busy?'Agent 正在抓取和拆解，可切换到其他页面':'提取链接、抓取原文并交给 Agent 拆解'}</button><p className="ops__research-note">指定作品需要对应平台保持登录；若页面要求验证，会直接提示，不会拿空数据生成假分析。</p>{error&&<p className="ops__error">{error}{needsBrowser&&<button type="button" className="ops__error-action" onClick={()=>void openLoginWindow()}><ExternalLink size={13}/>打开抖音登录窗口</button>}</p>}{notice&&<div className="ops__notice">{notice}</div>}</section>{results.length>0&&<section className="ops__research-results"><h2>抓到的真实样本 · {platform} · {results.length} 条 · 来源：Ego 浏览器</h2>{results.slice(0,8).map((x,i)=><a href={x.url} target="_blank" rel="noreferrer" key={x.url+i}><b>{x.title}</b><small>[{x.platform||platform}] {x.summary?`已提取 ${x.summary.length} 字文案/页面文本`:x.author||'只获取到链接，未提取正文'}</small></a>)}</section>}{report&&<section className="ops__research-report"><div className="ops__report-head"><h2>[{platform}] Agent 原文拆解与改写报告</h2><button onClick={toScript}><FilePenLine size={15}/>带原文与分析进入文案精修</button></div><pre>{report}</pre></section>}</main>
}
