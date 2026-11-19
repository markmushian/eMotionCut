import {useEffect,useMemo,useState} from 'react'
import {Search,Plus,Trash2,ArrowRight,Database,ExternalLink} from 'lucide-react'
import {useNavigate} from 'react-router'
import './creator-topics.css'

type Topic={id:string;title:string;summary:string;category:'热点'|'事实'|'候选';score:number;createdAt:string}
async function api(method='GET',body?:unknown,path=''){
  const response=await fetch('/api/creator/topics'+path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})})
  const value=await response.json();if(!response.ok)throw Error(value.error||'选题库操作失败');return value
}
export default function CreatorTopics(){
  const navigate=useNavigate(),[items,setItems]=useState<Topic[]>([]),[importedFrom,setImportedFrom]=useState(''),[query,setQuery]=useState(''),[category,setCategory]=useState('全部'),[title,setTitle]=useState(''),[summary,setSummary]=useState(''),[error,setError]=useState('')
  const load=()=>api().then(value=>{setItems(value.result.items||[]);setImportedFrom(value.result.importedFrom||'')}).catch(e=>setError(e.message))
  useEffect(()=>{void load()},[])
  const shown=useMemo(()=>items.filter(item=>(category==='全部'||item.category===category)&&(!query||`${item.title} ${item.summary}`.toLowerCase().includes(query.toLowerCase()))).sort((a,b)=>b.score-a.score),[items,query,category])
  const add=async()=>{try{const value=await api('POST',{title,summary});setItems(current=>[value.result,...current]);setTitle('');setSummary('');setError('')}catch(e){setError(e instanceof Error?e.message:String(e))}}
  const remove=async(id:string)=>{try{await api('DELETE',undefined,'/'+id);setItems(current=>current.filter(item=>item.id!==id))}catch(e){setError(e instanceof Error?e.message:String(e))}}
  const clear=async()=>{if(!confirm('清空全部历史候选题？此操作不会删除对标研究报告。'))return;try{await api('DELETE');setItems([]);setImportedFrom('')}catch(e){setError(e instanceof Error?e.message:String(e))}}
  const start=async(item:Topic)=>{try{const response=await fetch('/api/creator/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:item.title,reference:item.summary,goal:'建立信任'})});const value=await response.json();if(!response.ok)throw Error(value.error||'无法创建作品');navigate('/creator/projects/'+value.result.id)}catch(e){setError(e instanceof Error?e.message:String(e))}}
  return <main className="creator-topics">
    <header><div><h1 className="ec-grad-title">选题研究</h1><p>把对标样本、实时情报和你自己的想法沉淀为候选题，再判断它是否值得做。这里是候选池，不是自动生成的实时热榜。</p></div><span>{items.length} 个研究候选</span></header>
    <section className="creator-topics__source"><Database size={17}/><div><b>当前数据来源</b><p>{importedFrom?<>首次启动时从旧版工作台导入了 {items.length} 条历史候选；文件：<code>{importedFrom}</code>。它们不是今天的实时数据。</>:<>当前只有你手动加入或从研究流程沉淀的候选题。</>}</p></div><a href="/creator-tools/bench"><ExternalLink size={14}/>去对标研究获取新样本</a>{items.length>0&&<button onClick={()=>void clear()}><Trash2 size={14}/>清空历史候选</button>}</section>
    <section className="creator-topics__add" aria-label="新增选题"><input value={title} onChange={e=>setTitle(e.target.value)} placeholder="输入一个选题"/><input value={summary} onChange={e=>setSummary(e.target.value)} placeholder="补充角度、来源或要送的实物（可不填）"/><button disabled={!title.trim()} onClick={()=>void add()}><Plus size={15}/>加入候选池</button></section>
    <div className="creator-topics__tools"><label><Search size={15}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索标题和资料"/></label><div>{['全部','候选','热点','事实'].map(value=><button key={value} data-on={category===value||undefined} onClick={()=>setCategory(value)}>{value}</button>)}</div></div>
    {error&&<p role="alert" className="creator-topics__error">{error}</p>}
    <section className="creator-topics__list" aria-label="选题列表">
      {shown.map(item=><article key={item.id}><div className="creator-topics__score"><b>{item.score}</b><span>适合度</span></div><div className="creator-topics__copy"><span>{item.category}</span><h2>{item.title}</h2>{item.summary&&<p>{item.summary}</p>}</div><div className="creator-topics__actions"><button onClick={()=>void start(item)}>建立作品<ArrowRight size={14}/></button><button aria-label={`删除 ${item.title}`} onClick={()=>void remove(item.id)}><Trash2 size={14}/></button></div></article>)}
      {!shown.length&&<div className="creator-topics__empty">这里还没有匹配的选题。换个关键词，或在上方加入一个。</div>}
    </section>
  </main>
}
