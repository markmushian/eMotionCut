import {useEffect,useMemo,useState} from 'react'
import {ArrowLeft,ArrowRight,Check,ClipboardCheck,FilePenLine,RefreshCw,Sparkles,WandSparkles} from 'lucide-react'
import {useNavigate} from 'react-router'
import {toast} from 'sonner'
import {runCreatorTask} from '@/creator/creator-task-store'
import './script-workbench.css'

const DRAFT_KEY='emotioncut:spoken-script-draft',SCORE_KEY='emotioncut:spoken-script-score',PLAN_KEY='emotioncut:spoken-script-material-plan'
const TOPIC_KEY='emotioncut:spoken-script-topic',MATERIAL_KEY='emotioncut:spoken-script-material'
const SAVED_AT_KEY='emotioncut:spoken-script-saved-at',SESSION_KEY='emotioncut:spoken-script-live'
const STORED_KEYS=[TOPIC_KEY,MATERIAL_KEY,DRAFT_KEY,PLAN_KEY,SCORE_KEY,SAVED_AT_KEY]
const cleanTopic=(value:string)=>value.replace(/#[^\s#]+/g,'').replace(/https?:\/\/\S+/g,'').replace(/\s{2,}/g,' ').trim()
type Dimension={name:string;score:number;max:number;comment:string}
type ScoreResult={totalScore:number;verdict:string;dimensions:Dimension[];fatalProblems:string[];suggestions:string[];revisedDraft:string}
const readScore=(value:string):ScoreResult|undefined=>{try{const match=value.match(/\{[\s\S]*\}/);if(!match)return;const parsed=JSON.parse(match[0]) as ScoreResult;return Number.isFinite(parsed.totalScore)&&Array.isArray(parsed.dimensions)?parsed:undefined}catch{return}}
const readDraft=(value:string)=>{try{const match=value.match(/\{[\s\S]*\}/);if(!match)return {script:value,materialPlan:''};const parsed=JSON.parse(match[0]) as {script?:string;materialPlan?:string};return parsed.script?{script:parsed.script,materialPlan:parsed.materialPlan||''}:{script:value,materialPlan:''}}catch{return {script:value,materialPlan:''}}}

type StoredScript={topic:string;material:string;draft:string;materialPlan:string;result:string;savedAt:number}
const EMPTY:StoredScript={topic:'',material:'',draft:'',materialPlan:'',result:'',savedAt:0}
const readStored=():StoredScript=>({
  topic:cleanTopic(localStorage.getItem(TOPIC_KEY)||''),material:localStorage.getItem(MATERIAL_KEY)||'',
  draft:localStorage.getItem(DRAFT_KEY)||'',materialPlan:localStorage.getItem(PLAN_KEY)||'',
  result:localStorage.getItem(SCORE_KEY)||'',savedAt:Number(localStorage.getItem(SAVED_AT_KEY))||0,
})
const hasContent=(value:StoredScript)=>Boolean(value.topic||value.material||value.draft||value.materialPlan||value.result)

/**
 * 自动保存只在「同一个会话」里生效：窗口重开（应用重启）时不再把上次的选题和稿子
 * 直接灌回输入框，而是留一个「恢复上次草稿 / 清空」的提示。以前重启后这里顶着一篇旧稿，
 * 看着像没清理干净，也容易在旧稿上接着写错题。
 */
function bootScript(){
  const stored=readStored()
  let live=false
  try{live=sessionStorage.getItem(SESSION_KEY)==='1'}catch{/* 隐私模式忽略 */}
  if(live)return{current:stored,previous:undefined as StoredScript|undefined}
  return{current:EMPTY,previous:hasContent(stored)?stored:undefined}
}

function Flow({active}:{active:'script'|'score'}){return <nav className="script-flow" aria-label="口播稿流程"><span data-done><i><Check size={13}/></i>对标与资料</span><b/><span data-active={active==='script'||undefined} data-done={active==='score'||undefined}><i>{active==='score'?<Check size={13}/>:2}</i>写出口播稿</span><b/><span data-active={active==='score'||undefined}><i>3</i>审核与改稿</span><b/><span><i>4</i>选择制作方式</span></nav>}

export default function ScriptWorkbench({mode}:{mode:'script'|'score'}){
 const navigate=useNavigate(),isScore=mode==='score'
 const [boot]=useState(bootScript)
 const [previous,setPrevious]=useState<StoredScript|undefined>(boot.previous)
 const [topic,setTopic]=useState(boot.current.topic)
 const [material,setMaterial]=useState(boot.current.material)
 const [draft,setDraft]=useState(boot.current.draft)
 const [materialPlan,setMaterialPlan]=useState(boot.current.materialPlan)
 const [result,setResult]=useState(boot.current.result)
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 const scoreResult=useMemo(()=>readScore(result),[result])
 // 标记「这个会话已经打开过」：之后刷新、切页回来都正常恢复，只有重开应用才当成新会话。
 useEffect(()=>{try{sessionStorage.setItem(SESSION_KEY,'1')}catch{}},[])
 useEffect(()=>{localStorage.setItem(DRAFT_KEY,draft)},[draft])
 useEffect(()=>{localStorage.setItem(PLAN_KEY,materialPlan)},[materialPlan])
 useEffect(()=>{localStorage.setItem(TOPIC_KEY,cleanTopic(topic))},[topic])
 useEffect(()=>{localStorage.setItem(SAVED_AT_KEY,String(Date.now()))},[topic,draft,materialPlan])

 const restorePrevious=()=>{
  if(!previous)return
  setTopic(previous.topic);setMaterial(previous.material);setDraft(previous.draft);setMaterialPlan(previous.materialPlan);setResult(previous.result)
  localStorage.setItem(TOPIC_KEY,previous.topic);localStorage.setItem(MATERIAL_KEY,previous.material)
  localStorage.setItem(DRAFT_KEY,previous.draft);localStorage.setItem(PLAN_KEY,previous.materialPlan)
  if(previous.result)localStorage.setItem(SCORE_KEY,previous.result)
  setPrevious(undefined);toast.success('已恢复上个会话的稿子')
 }
 const clearAll=()=>{
  setTopic('');setMaterial('');setDraft('');setMaterialPlan('');setResult('');setPrevious(undefined);setError('')
  for(const key of STORED_KEYS)localStorage.removeItem(key)
  toast('这一篇已清空，可以重新选题')
 }

 const generate=async()=>{if(!topic.trim())return;setBusy(true);setError('');localStorage.setItem('emotioncut:spoken-script-material',material);try{const text=await runCreatorTask({title:`正在${draft.trim()?'重新写稿':'写稿'}：${topic.slice(0,24)}`,returnTo:'/creator-tools/script#script-draft',system:'你是短视频口播主编和前期导演。必须紧扣选题和给定事实，写出2至5分钟可直接拍摄的中文口播稿。内容必须给观众实际帮助：明确结论、关键事实、具体操作步骤、适用场景与限制。不得空泛、虚构参数或留占位符。开头15秒说清收益；正文至少有一个可照做的演示；结尾给具体行动。还要为自动剪辑生成配套素材清单，按“必拍口播/屏幕录制/事实证据截图/B-roll或图片”分类，每项写清对应口播段落、建议时长和画面内容，资料不足时标注“需核验”。只返回单一JSON：{"script":"口播正文","materialPlan":"可执行的拍摄与素材清单"}。',user:`选题：${cleanTopic(topic)}\n可核验资料：${material||'暂无。资料不足时不要编造具体参数。'}\n${draft.trim()?`这是旧稿，请基于同一选题重写并提升信息密度：\n${draft}`:''}`});const next=readDraft(text);setDraft(next.script);setMaterialPlan(next.materialPlan);setResult('');localStorage.setItem(DRAFT_KEY,next.script);localStorage.setItem(PLAN_KEY,next.materialPlan);localStorage.removeItem(SCORE_KEY)}catch(e){setError(e instanceof Error?e.message:'写稿失败')}finally{setBusy(false)}}
 const score=async(candidate=draft)=>{if(!candidate.trim())return;setBusy(true);setError('');try{const text=await runCreatorTask({title:'正在审核口播稿',returnTo:'/creator-tools/score#score-result',system:'你是严格的短视频内容审核主编。按100分制审核，不得一律给高分。必须返回单一 JSON 对象，不要 Markdown，不要代码围栏。格式：{"totalScore":整数,"verdict":"一句话结论","dimensions":[{"name":"选题相关度","score":整数,"max":15,"comment":"证据与问题"},{"name":"事实与实操","score":整数,"max":20,"comment":"证据与问题"},{"name":"前15秒留存","score":整数,"max":15,"comment":"证据与问题"},{"name":"结构节奏","score":整数,"max":15,"comment":"证据与问题"},{"name":"口语自然度","score":整数,"max":10,"comment":"证据与问题"},{"name":"可信度","score":整数,"max":10,"comment":"证据与问题"},{"name":"转粉价值","score":整数,"max":15,"comment":"证据与问题"}],"fatalProblems":["致命问题"],"suggestions":["可执行建议"],"revisedDraft":"已经完整落实建议的2至5分钟新版口播稿"}。分项总和必须等于总分。',user:`选题：${cleanTopic(topic)||'未提供'}\n待审核稿件：\n${candidate}`});setResult(text);localStorage.setItem(SCORE_KEY,text)}catch(e){setError(e instanceof Error?e.message:'审核失败')}finally{setBusy(false)}}
 const adoptAndRescore=async()=>{if(!scoreResult?.revisedDraft)return;const next=scoreResult.revisedDraft;setDraft(next);localStorage.setItem(DRAFT_KEY,next);setResult('');localStorage.removeItem(SCORE_KEY);await score(next)}
 const production=()=>{localStorage.setItem('emotioncut:production-script',draft);localStorage.setItem('emotioncut:production-material-plan',materialPlan);navigate('/general?entry=topic')}

 useEffect(()=>{const receive=(event:Event)=>{const task=(event as CustomEvent<{returnTo:string;result?:string}>).detail;if(!task?.result)return;if(task.returnTo.includes('/creator-tools/script')){const next=readDraft(task.result);setDraft(next.script);setMaterialPlan(next.materialPlan);setResult('');requestAnimationFrame(()=>document.getElementById('script-draft')?.scrollIntoView({behavior:'smooth',block:'start'}))}else if(task.returnTo.includes('/creator-tools/score')){setResult(task.result);requestAnimationFrame(()=>document.getElementById('score-result')?.scrollIntoView({behavior:'smooth',block:'start'}))}};window.addEventListener('emotioncut:creator-task-complete',receive);window.addEventListener('emotioncut:creator-task-open',receive);return()=>{window.removeEventListener('emotioncut:creator-task-complete',receive);window.removeEventListener('emotioncut:creator-task-open',receive)}},[])

 return <main className="script-workbench">
  <Flow active={mode}/>
  <header><div><h1 className="ec-grad-title">{isScore?'审核这篇口播稿':'把选题写成可拍的口播稿'}</h1><p>{isScore?'先看明确总分和问题证据，再一键采纳改稿或进入制作。':'资料、初稿和下一步都在同一条流程里，生成后直接进入审核。'}</p></div><div className="script-head-ops"><button type="button" className="script-clear" onClick={clearAll}>清空本篇</button>{isScore?<ClipboardCheck/>:<FilePenLine/>}</div></header>
  {previous&&<div className="script-restore" role="status"><span>上个会话留了一篇没写完的稿子{previous.savedAt?`（${new Date(previous.savedAt).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}）`:''}，这次没有自动灌回来。</span><button type="button" onClick={restorePrevious}>恢复那一篇</button><button type="button" onClick={clearAll}>清空</button></div>}
  <section className={`script-workbench__grid ${isScore?'is-score':''}`}>
   <div id="script-draft" className="script-workbench__editor">
    <label><span>选题</span><input value={topic} onChange={e=>setTopic(cleanTopic(e.target.value))} placeholder="例如：Gemini 新模型发布，普通创作者如何实际使用"/></label>
    {!isScore&&<details className="script-material" open={!draft.trim()}><summary>事实资料与对标原文 <small>{material.length?`${material.length} 字`:'暂无资料'}</small></summary>{material.length>0&&<button type="button" className="script-material__clear" onClick={()=>setMaterial('')}>清空资料</button>}<textarea className="material" value={material} onChange={e=>setMaterial(e.target.value)} placeholder="粘贴发布信息、实测结果和对标研究结论"/></details>}
    <label><span>{isScore?'待审核稿件':'口播稿'} <small>自动保存</small></span><textarea className="draft" value={draft} onChange={e=>{setDraft(e.target.value);if(result){setResult('');localStorage.removeItem(SCORE_KEY)}}} placeholder="在这里粘贴或生成口播稿"/></label>
    {materialPlan&&<label className="script-shot-plan"><span>配套拍摄与素材清单 <small>不会当作口播读出</small></span><textarea value={materialPlan} onChange={e=>setMaterialPlan(e.target.value)} placeholder="需要录制的画面、截图证据和备用素材"/></label>}
    {!isScore?<div className="script-actions"><button className="secondary" disabled={busy||!topic.trim()} onClick={()=>void generate()}>{draft.trim()?<RefreshCw size={16}/>:<Sparkles size={16}/>} {busy?'Agent 正在写稿':draft.trim()?'重新生成':'生成口播稿'}</button>{draft.trim()&&<button className="primary" onClick={()=>navigate('/creator-tools/score')}><ClipboardCheck size={16}/>下一步：审核稿件<ArrowRight size={16}/></button>}</div>:!scoreResult&&<button className="primary wide" disabled={busy||!draft.trim()} onClick={()=>void score()}><WandSparkles size={16}/>{busy?'正在逐项评分并改稿':'开始审核'}</button>}
    {error&&<p className="script-workbench__error">{error}</p>}
   </div>
   {!isScore?<aside className="script-next"><span>当前步骤</span><b>{draft.trim()?'初稿已生成':'等待生成初稿'}</b><p>{draft.trim()?'下一步会检查相关度、干货密度、留存和可信度。':'生成后按钮会切换为“重新生成”，并出现明确的审核入口。'}</p></aside>:<aside id="score-result" className="score-panel">
    {!scoreResult?<div className="score-empty"><ClipboardCheck/><b>等待审核</b><p>点击“开始审核”后，这里会显示总分、分项证据和可直接采纳的改稿。</p></div>:<>
     <div className="score-head"><div><span>审核总分</span><strong>{scoreResult.totalScore}</strong><small>/ 100</small></div><em data-pass={scoreResult.totalScore>=80||undefined}>{scoreResult.totalScore>=80?'可以进入制作':'建议修改后再制作'}</em></div>
     <p className="score-verdict">{scoreResult.verdict}</p>
     <div className="score-dimensions">{scoreResult.dimensions.map(item=><article key={item.name}><header><b>{item.name}</b><span>{item.score}<small>/{item.max}</small></span></header><i><u style={{width:`${Math.max(0,Math.min(100,item.score/item.max*100))}%`}}/></i><p>{item.comment}</p></article>)}</div>
     {scoreResult.fatalProblems?.length>0&&<section className="score-problems"><b>优先解决</b><ul>{scoreResult.fatalProblems.map(item=><li key={item}>{item}</li>)}</ul></section>}
     <div className="score-actions"><button className="secondary" onClick={()=>navigate('/creator-tools/script')}><ArrowLeft size={15}/>返回编辑</button>{scoreResult.revisedDraft&&<button className="secondary" disabled={busy} onClick={()=>void adoptAndRescore()}><WandSparkles size={15}/>{busy?'正在改稿并重评':'采纳改稿并重新评分'}</button>}<button className="primary" onClick={production}>选择视频制作方式<ArrowRight size={15}/></button></div>
    </>}
   </aside>}
  </section>
 </main>
}
