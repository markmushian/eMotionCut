import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import type {IncomingMessage,ServerResponse} from 'node:http'
import {bodyJson,sendJson} from './http.ts'

export type CreatorTopic={id:string;title:string;summary:string;source?:string;category:'热点'|'事实'|'候选';score:number;createdAt:string}
type TopicStore={version:1;importedFrom?:string;items:CreatorTopic[]}

const profile=()=>process.env.EMOTIONCUT_USER_DATA||path.join(process.cwd(),'.dev')
const storeFile=()=>path.join(profile(),'creator','topics.json')
const legacyRoot=()=>process.env.EMOTIONCUT_LEGACY_CREATOR_ROOT||'E:\\AI小程序\\AI博主创作工具'
const id=()=>crypto.randomUUID()
const clean=(value:string)=>value.replace(/\*\*/g,'').replace(/^\d+[.、]\s*/,'').trim()
const score=(text:string)=>{
  const rules=[/[小白普通人零基础免费点哪里教程步骤]/,/[省钱赚钱时间防坑避坑效率]/,/[模板清单工具步骤图实物skill]/,/[反差翻车真相不是而是千万别]/]
  return Math.min(100,40+rules.reduce((sum,rule)=>sum+(rule.test(text)?15:0),0))
}

function imported():TopicStore{
  const source=path.join(legacyRoot(),'选题库','选题情报总览.md')
  const items:CreatorTopic[]=[]
  try{
    const lines=fs.readFileSync(source,'utf8').split(/\r?\n/);let category:CreatorTopic['category']='候选'
    for(let i=0;i<lines.length;i++){
      const line=lines[i].trim()
      if(/^## A\./.test(line))category='事实';else if(/^## B\./.test(line))category='热点';else if(/^## C\./.test(line))category='候选'
      const hit=line.match(/^###\s+(?:\d+[.、]\s*)?(.+)/);if(!hit)continue
      const title=clean(hit[1]);if(!title||/沉淀提示/.test(title))continue
      const details:string[]=[]
      for(let j=i+1;j<Math.min(lines.length,i+12)&&!/^#{2,3}\s/.test(lines[j].trim());j++)if(lines[j].trim().startsWith('-'))details.push(clean(lines[j].replace(/^-\s*/,'')))
      const summary=details.slice(0,3).join(' · ')
      items.push({id:id(),title,summary,category,score:score(title+' '+summary),createdAt:new Date().toISOString()})
    }
  }catch{/* first use without the legacy project starts empty */}
  return {version:1,importedFrom:fs.existsSync(source)?source:undefined,items}
}
function read(){
  try{const value=JSON.parse(fs.readFileSync(storeFile(),'utf8')) as TopicStore;if(value.version===1&&Array.isArray(value.items))return value}catch{}
  const value=imported();write(value);return value
}
function write(value:TopicStore){const file=storeFile();fs.mkdirSync(path.dirname(file),{recursive:true});const temp=file+'.tmp';fs.writeFileSync(temp,JSON.stringify(value,null,2)+'\n','utf8');fs.renameSync(temp,file)}

export async function creatorTopicsApi(req:IncomingMessage,res:ServerResponse){
  const route=new URL(req.url||'/','http://localhost').pathname.replace('/api/creator/topics','')||'/'
  if(req.method==='GET'&&route==='/')return sendJson(res,200,{ok:true,result:read()})
  if(req.method==='DELETE'&&route==='/'){write({version:1,items:[]});return sendJson(res,200,{ok:true})}
  if(req.method==='POST'&&route==='/'){
    const body=await bodyJson(req),title=typeof body.title==='string'?body.title.trim():''
    if(!title)return sendJson(res,400,{ok:false,error:'请填写选题标题'})
    const summary=typeof body.summary==='string'?body.summary.trim():'';const value=read()
    const item:CreatorTopic={id:id(),title:title.slice(0,160),summary:summary.slice(0,1200),category:'候选',score:score(title+' '+summary),createdAt:new Date().toISOString()}
    value.items.unshift(item);write(value);return sendJson(res,201,{ok:true,result:item})
  }
  const remove=route.match(/^\/([a-f0-9-]{36})$/)
  if(req.method==='DELETE'&&remove){const value=read(),before=value.items.length;value.items=value.items.filter(item=>item.id!==remove[1]);if(value.items.length===before)return sendJson(res,404,{ok:false,error:'选题不存在'});write(value);return sendJson(res,200,{ok:true})}
  return sendJson(res,404,{ok:false,error:'智能选题没有这个接口'})
}
