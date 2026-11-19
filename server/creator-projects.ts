import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import type {IncomingMessage,ServerResponse} from 'node:http'
import {bodyJson,sendJson} from './http.ts'

export const CREATOR_STAGES=['brief','script','record','edit','package','publish','review'] as const
type Stage=typeof CREATOR_STAGES[number]
export type CreatorProject={
  id:string;topic:string;audience:string;goal:string;offer:string;cta:string;reference:string;script:string
  aspect:'9:16'|'16:9';seconds:15|30|60|90|180;stage:Stage;generalProjectId?:string;motionTemplateId?:string
  publication:{platform:string;url:string;title:string;description:string;status:'draft'|'scheduled'|'published';titleOptions?:string[];tags?:string[];coverBrief?:string;coverSpec?:string;coverReferences?:string[];scheduledAt?:string}
  metrics:Record<'views'|'likes'|'comments'|'saves'|'shares'|'follows'|'leads'|'orders'|'revenue'|'cost',number|null>
  learning:string;revision:number;createdAt:string;updatedAt:string;legacy?:{id?:string;brainId?:string;cutId?:string;motionId?:string}
}
const metricKeys=['views','likes','comments','saves','shares','follows','leads','orders','revenue','cost'] as const
const root=()=>path.join(process.env.EMOTIONCUT_USER_DATA||path.join(process.cwd(),'.dev'),'creator','projects')
const legacyRoot=()=>process.env.EMOTIONCUT_LEGACY_CREATOR_ROOT||'E:\\AI小程序\\AI博主创作工具'
const file=(id:string)=>path.join(root(),id+'.json')
const valid=(id:string)=>/^[a-f0-9-]{36}$/.test(id)
function save(project:CreatorProject){fs.mkdirSync(root(),{recursive:true});const temp=file(project.id)+'.tmp';fs.writeFileSync(temp,JSON.stringify(project,null,2)+'\n','utf8');fs.renameSync(temp,file(project.id));return project}
function read(id:string){if(!valid(id))throw Object.assign(Error('作品编号不正确'),{status:400});try{return JSON.parse(fs.readFileSync(file(id),'utf8')) as CreatorProject}catch{throw Object.assign(Error('作品不存在'),{status:404})}}
function list(){fs.mkdirSync(root(),{recursive:true});return fs.readdirSync(root()).filter(name=>name.endsWith('.json')).flatMap(name=>{try{return [JSON.parse(fs.readFileSync(path.join(root(),name),'utf8')) as CreatorProject]}catch{return []}}).sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt))}
function emptyMetrics(){return Object.fromEntries(metricKeys.map(key=>[key,null])) as CreatorProject['metrics']}
function create(input:Record<string,unknown>){
  const topic=String(input.topic||'').trim();if(topic.length<3||topic.length>300)throw Object.assign(Error('选题需要 3–300 字'),{status:400})
  const now=new Date().toISOString();return save({id:crypto.randomUUID(),topic,audience:String(input.audience||'').slice(0,300),goal:String(input.goal||'建立信任').slice(0,100),offer:String(input.offer||'').slice(0,500),cta:String(input.cta||'').slice(0,300),reference:String(input.reference||'').slice(0,10000),script:String(input.script||'').slice(0,20000),aspect:input.aspect==='16:9'?'16:9':'9:16',seconds:[15,30,60,90,180].includes(Number(input.seconds))?Number(input.seconds) as CreatorProject['seconds']:60,stage:'brief',publication:{platform:'',url:'',title:'',description:'',status:'draft'},metrics:emptyMetrics(),learning:'',revision:1,createdAt:now,updatedAt:now})
}
function patch(project:CreatorProject,input:Record<string,unknown>){
  for(const [key,max] of [['topic',300],['audience',300],['goal',100],['offer',500],['cta',300],['reference',10000],['script',20000],['learning',3000]] as const)if(input[key]!==undefined)(project as unknown as Record<string,unknown>)[key]=String(input[key]||'').slice(0,max)
  if(typeof input.generalProjectId==='string'&&/^[a-f0-9-]{36}$/.test(input.generalProjectId))project.generalProjectId=input.generalProjectId
  if(typeof input.motionTemplateId==='string')project.motionTemplateId=input.motionTemplateId.slice(0,100)
  if(input.aspect==='9:16'||input.aspect==='16:9')project.aspect=input.aspect
  if([15,30,60,90,180].includes(Number(input.seconds)))project.seconds=Number(input.seconds) as CreatorProject['seconds']
  if(input.publication&&typeof input.publication==='object')project.publication={...project.publication,...input.publication as Partial<CreatorProject['publication']>}
  if(input.metrics&&typeof input.metrics==='object')for(const key of metricKeys){const value=(input.metrics as Record<string,unknown>)[key];if(value===null||Number.isFinite(Number(value)))project.metrics[key]=value===null?null:Number(value)}
  if(CREATOR_STAGES.includes(input.stage as Stage))project.stage=input.stage as Stage
  project.revision++;project.updatedAt=new Date().toISOString();return save(project)
}

export async function creatorProjectsApi(req:IncomingMessage,res:ServerResponse){
  const route=new URL(req.url||'/','http://localhost').pathname.replace('/api/creator/projects','')||'/'
  try{
    if(req.method==='GET'&&route==='/')return sendJson(res,200,{ok:true,result:list()})
    if(req.method==='POST'&&route==='/')return sendJson(res,201,{ok:true,result:create(await bodyJson(req))})
    if(req.method==='DELETE'&&route==='/'){for(const project of list())fs.rmSync(file(project.id),{force:true});return sendJson(res,200,{ok:true,result:[]})}
    const match=route.match(/^\/([a-f0-9-]{36})$/)
    if(req.method==='GET'&&match)return sendJson(res,200,{ok:true,result:read(match[1])})
    if(req.method==='PATCH'&&match)return sendJson(res,200,{ok:true,result:patch(read(match[1]),await bodyJson(req))})
    if(req.method==='DELETE'&&match){read(match[1]);fs.rmSync(file(match[1]),{force:true});return sendJson(res,200,{ok:true,result:{id:match[1]}})}
    return sendJson(res,404,{ok:false,error:'作品空间没有这个接口'})
  }catch(error){const e=error as Error&{status?:number};return sendJson(res,e.status||500,{ok:false,error:e.message||'作品处理失败'})}
}
