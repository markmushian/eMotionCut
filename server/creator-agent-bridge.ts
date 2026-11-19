import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type {IncomingMessage,ServerResponse} from 'node:http'
import {bodyJson,sendJson} from './http.ts'
import {resolveAgent,runAgent} from './agent-runtime.ts'
import {withAgentChoiceScope} from './agent-runtime.ts'
import {readAgentSettings,agentSettingsFile,getAgentCatalog,agentSettingsView} from './agent-settings.ts'
import {FOLLOW_DEFAULT} from '../src/studio/agent-settings.ts'

export type CreatorAgentChoice={version:1;model:string;effort:string}
export type CreatorAgentAnswer={text:string;agent:string;model?:string;effort?:string}

/** 创作台 Agent 的模型选择，全局一份，创作桥的文本任务（发布包、话题、封面提示词、复盘）共用。 */
const choiceFile=()=>path.join(path.dirname(agentSettingsFile()),'creator-agent-choice.json')
export function readCreatorAgentChoice():CreatorAgentChoice|null{
 try{
  const raw=JSON.parse(fs.readFileSync(choiceFile(),'utf8')) as CreatorAgentChoice
  if(raw&&raw.version===1&&typeof raw.model==='string'&&typeof raw.effort==='string')return raw
 }catch{/* 没保存过或文件损坏都回到「跟随通用剪辑出片」 */}
 return null
}
/** 保存前对着真实模型目录校验：模型必须在目录里，强度必须是该模型支持的，不存一个跑不起来的选择。 */
export async function saveCreatorAgentChoice(input:unknown):Promise<CreatorAgentChoice>{
 const body=(input||{}) as {model?:unknown;effort?:unknown}
 const model=String(body.model||''),effort=String(body.effort||'')
 if(!model)throw Error('缺少模型选择')
 // 「跟随默认」不走目录校验：通用剪辑出片可以切到 WorkBuddy 这类没有模型目录的智能体，
 // 此时创作台只有一个合法选择就是跟随；目录只对要选具体模型的 Codex 有意义。
 if(model===FOLLOW_DEFAULT){
  const choice:CreatorAgentChoice={version:1,model,effort:'medium'}
  fs.mkdirSync(path.dirname(choiceFile()),{recursive:true})
  fs.writeFileSync(choiceFile(),JSON.stringify(choice,null,2))
  return choice
 }
 const settings=readAgentSettings()
 let models
 try{models=(await getAgentCatalog(settings.cliPath)).models}
 catch(error){throw Error(`无法读取模型目录，稍后再试：${error instanceof Error?error.message:String(error)}`)}
 if(!models.length)throw Error('Codex 没有返回可选模型')
 if(!models.some(m=>m.id===model))throw Error(`所选模型 ${model} 不在当前 Agent 的模型列表里`)
 const target=models.find(m=>m.id===model)!
 if(!target.efforts.includes(effort))throw Error(`${target.name||target.id} 不支持推理强度 ${effort}`)
 const choice:CreatorAgentChoice={version:1,model,effort}
 fs.mkdirSync(path.dirname(choiceFile()),{recursive:true})
 fs.writeFileSync(choiceFile(),JSON.stringify(choice,null,2))
 return choice
}

/**
 * 让用户配置的 Agent 做一次纯文本任务。
 * 模型优先级：创作台自己的选择（creator-agent-choice.json）>「通用剪辑出片」的设置。
 * 参数只能走 argv，而部分 Windows Agent CLI 会按当前代码页解码参数、把中文变成问号，
 * 所以整段提示词先转成 \uXXXX 形式再交给 Agent 自行还原。
 */
export async function askCreatorAgent(system:string,user:string,timeout=300_000):Promise<CreatorAgentAnswer>{
 if(!user.trim())throw Error('缺少任务内容')
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'emotioncut-creator-agent-')),output=path.join(folder,'answer.txt')
 try{
  const stored=readCreatorAgentChoice()
  const fallback=readAgentSettings().modules.generalEditing
  const choice=stored&&stored.model!==FOLLOW_DEFAULT?{model:stored.model,effort:stored.effort}:fallback
  const agent=await withAgentChoiceScope({captionTranslation:choice},()=>resolveAgent('captionTranslation'))
  const prompt=[system&&`角色与规则：\n${system}`,`任务：\n${user}`].filter(Boolean).join('\n\n')
  const escaped=JSON.stringify(prompt).replace(/[\u0080-\uffff]/g,ch=>`\\u${ch.charCodeAt(0).toString(16).padStart(4,'0')}`)
  const transport=`Decode the following JSON string, then perform the decoded Chinese task. Return only the requested result.\n${escaped}`
  const result=await runAgent(agent,['exec','--output-last-message',output,transport],{timeout})
  const text=fs.existsSync(output)?fs.readFileSync(output,'utf8').trim():result.stdout.trim()
  return {text,agent:agent.agent||'codex',...(agent.model?{model:agent.model}:{}),...(agent.effort?{effort:agent.effort}:{})}
 }finally{try{fs.rmSync(folder,{recursive:true,force:true})}catch{}}
}

export async function creatorAgentBridge(req:IncomingMessage,res:ServerResponse){
 if(req.method!=='POST')return sendJson(res,405,{ok:false,error:'只接受 POST'})
 const input=await bodyJson(req),system=String(input.system||'').trim(),user=String(input.user||'').trim()
 if(!user)return sendJson(res,400,{ok:false,error:'缺少任务内容'})
 try{
  return sendJson(res,200,{ok:true,result:await askCreatorAgent(system,user)})
 }catch(error){
  return sendJson(res,502,{ok:false,error:error instanceof Error?error.message:String(error)})
 }
}

/** 创作台 Agent 模型选择：GET 返回选择+模型目录，POST 保存（先校验再落盘）。 */
export async function creatorAgentChoiceApi(req:IncomingMessage,res:ServerResponse){
 if(req.method==='POST'){
  const input=await bodyJson(req).catch(()=>({}))
  try{return sendJson(res,200,{ok:true,result:await saveCreatorAgentChoice(input)})}
  catch(error){return sendJson(res,400,{ok:false,error:error instanceof Error?error.message:String(error)})}
 }
 if(req.method==='GET'){
  const view=await agentSettingsView()
  return sendJson(res,200,{ok:true,result:{choice:readCreatorAgentChoice(),catalog:view.catalog,catalogError:view.catalogError,generalEditing:view.settings.modules.generalEditing}})
 }
 return sendJson(res,405,{ok:false,error:'只接受 GET/POST'})
}
