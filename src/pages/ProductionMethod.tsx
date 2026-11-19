import {useEffect,useState} from 'react'
import {ArrowRight,Check,CircleAlert,Cloud,Film,Mic2,RefreshCw,UserRound,Video} from 'lucide-react'
import {Link} from 'react-router'
import type {EnvironmentCheck} from '../../shared/creation-tools'
import {openSettings} from '../onboarding/onboarding-store'
import './production-method.css'

type ModeId='real'|'hybrid'|'avatar'
type Readiness={checkedAt:string;local:EnvironmentCheck;cloud:{connected:boolean;privacy:string;capabilities:Array<{id:string;label:string;detail:string;connected:boolean}>}}
const MODES=[
 {id:'real' as const,icon:Video,name:'真人完整口播',note:'推荐',summary:'你负责表达，Agent 负责导演和剪辑。画面最真实，成本最低。'},
 {id:'hybrid' as const,icon:Mic2,name:'真人开头，云端续说',note:'20 秒样本',summary:'保留你的声音与出镜形象，长稿交给云端完成口型和动作。'},
 {id:'avatar' as const,icon:UserRound,name:'全程数字人口播',note:'云端生成',summary:'适合不方便出镜或批量生产，需要连接数字人服务。'},
]
const STEPS:Record<ModeId,string[]>= {
 real:['录制完整口播并导入素材','Agent 分析气口、节奏和可删片段','加入 B-roll、字幕与 Motion 模板','人工预览后导出'],
 hybrid:['录制约 20 秒清晰正脸与自然手势','Agent 按选题生成并核对完整口播稿','云端克隆声音，驱动口型与动作','回传片段，由 Agent 完成导演剪辑'],
 avatar:['选择或上传授权的人物素材','Agent 把稿件拆成适合生成的短段','云端生成声音、口型和人物动作','回传片段，由 Agent 统一节奏与包装'],
}

export default function ProductionMethod(){
 const [mode,setMode]=useState<ModeId>('real'),[data,setData]=useState<Readiness|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
 const check=async()=>{setBusy(true);setError('');try{const r=await fetch('/api/creator/production-readiness');if(!r.ok)throw Error();setData(await r.json())}catch{setError('未能读取制作环境，请确认本地创作服务已启动')}finally{setBusy(false)}}
 useEffect(()=>{void check()},[])
 const cloud=mode!=='real',missing=data?.local.items.filter(x=>!x.ok)??[]
 return <main className="production-method">
  <header className="production-method__header">
   <div><p className="production-method__path">剪辑 / 口播制作</p><h1 className="ec-grad-title">选择这次口播怎么完成</h1><p>三种方式最后都进入同一个 Agent 剪辑台。区别只在真人素材由你拍完，还是由云端补齐。</p></div>
   <div className="production-method__route" aria-label="制作流程">
    <span>口播底片</span><ArrowRight/><strong>Agent 导演</strong><ArrowRight/><span>Motion 包装</span><ArrowRight/><span>成片</span>
   </div>
  </header>

  <section className="production-method__modes" aria-label="口播制作方式">
   {MODES.map(item=>{const Icon=item.icon;return <button key={item.id} type="button" data-active={mode===item.id||undefined} onClick={()=>setMode(item.id)}>
    <span className="production-method__mode-icon"><Icon/></span><span><b>{item.name}</b><small>{item.summary}</small></span><i>{item.note}</i>
   </button>})}
  </section>

  <div className="production-method__work">
   <section className="production-method__plan">
    <div className="production-method__section-title"><div><h2>{MODES.find(x=>x.id===mode)?.name}</h2><p>{cloud?'生成工作在云端运行，本机只负责上传、预览和剪辑。':'无需数字人引擎，也不需要高性能显卡。'}</p></div><Film/></div>
    <ol>{STEPS[mode].map((step,index)=><li key={step}><span>{String(index+1).padStart(2,'0')}</span><p>{step}</p>{index===0&&<em>从这里开始</em>}</li>)}</ol>
    {cloud?<div className="production-method__cloud-note"><Cloud/><div><b>云端连接器尚未配置</b><p>后续可接入第三方 API 或你自己的服务。eMotionCut 负责把 Agent 生成的分段稿、样本和参数发给服务，再把结果送回剪辑台，不要求用户在本地安装模型。</p><button className="production-method__cloud-link" type="button" onClick={()=>openSettings('integrations')}>去「账号与服务接入」配置云端服务 →</button></div></div>:<div className="production-method__local-note"><Check/><span>现有流程已可用：导入真人口播后，继续使用风格库、Motion 模板与 Agent 导演。</span></div>}
    <div className="production-method__actions">
     {cloud?<button className="ec-btn ec-btn--solid" type="button" onClick={()=>openSettings('integrations')}>连接云端服务</button>:<Link className="ec-btn ec-btn--solid" to="/general?entry=footage">导入口播素材</Link>}
     <Link className="ec-btn" to="/studio">查看剪辑工程</Link>
    </div>
   </section>

   <aside className="production-method__readiness">
    <div className="production-method__aside-head"><div><h2>{cloud?'云端能力':'本机准备情况'}</h2><p>{data?`检查于 ${new Date(data.checkedAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`:'正在读取'}</p></div><button type="button" onClick={check} disabled={busy} aria-label="重新检查"><RefreshCw className={busy?'is-spinning':''}/></button></div>
    {error&&<p className="production-method__error"><CircleAlert/>{error}</p>}
    {!cloud&&data&&<ul>{data.local.items.map(item=><li key={item.id} data-ok={item.ok||undefined}><span>{item.ok?<Check/>:<CircleAlert/>}</span><div><b>{item.label}</b><p>{item.ok?(item.version||item.path||'已就绪'):(item.message||'尚未安装')}</p>{!item.ok&&item.install&&<details><summary>查看安装方法</summary><pre>{item.install}</pre></details>}</div></li>)}</ul>}
    {cloud&&data&&<><ul>{data.cloud.capabilities.map(item=><li key={item.id}><span><Cloud/></span><div><b>{item.label}</b><p>{item.detail}</p><small>连接服务后可用</small></div></li>)}</ul><p className="production-method__privacy">{data.cloud.privacy}</p></>}
    {!cloud&&data&&<p className="production-method__status" data-ready={missing.length===0||undefined}>{missing.length===0?'本机已具备 Agent 剪辑条件':`还缺 ${missing.length} 项，按上面的说明安装后重新检查`}</p>}
   </aside>
  </div>
 </main>
}
