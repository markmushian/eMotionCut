import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'

const run=promisify(execFile)
process.env.EMOTIONCUT_USER_DATA ||= path.join(process.env.APPDATA||process.cwd(),'eMotionCut')
const motion=await import('../server/general/motion-project.ts')
const library=await import('../server/motion-library.ts')
const item=library.readMotionCatalog().packages.find(value=>(value.template.name||'').includes('酷炫科技风'))||library.readMotionCatalog().packages[0]
if(!item)throw Error('没有已登记的 Motion 模板')
const root=fs.mkdtempSync(path.join(os.tmpdir(),'emotioncut-motion-runtime-'))
try{
 motion.prepareMotionProject(root,'motion-'+item.sha256)
 const entry=path.join(root,'motion','motion.mjs'),pkg=path.join(root,'motion','template.motion'),instance=path.join(root,'motion','instance')
 const requested=process.argv.slice(2)
 const args=requested.length?requested.map(value=>value.replaceAll('{entry}',entry).replaceAll('{package}',pkg).replaceAll('{instance}',instance)):[entry,'run',pkg,'--instance',instance,'start','--summary']
 const result=await run(process.execPath,args,{timeout:120_000,maxBuffer:4*1024*1024,windowsHide:true})
 if(process.env.EMOTIONCUT_MOTION_CHECK_COMPACT==='1'){
  const value=JSON.parse(result.stdout)
  console.log(JSON.stringify({verified:value.verified,template:value.assistantGuide?.template,workflows:value.assistantGuide?.workflows||value.workflows||value.workflowCatalog||value.assistantGuide?.workflowCatalog,runtime:value.runtime},null,2))
 }else console.log(result.stdout.trim())
}finally{
 const resolved=path.resolve(root),base=path.resolve(os.tmpdir())
 if(resolved.startsWith(base+path.sep))fs.rmSync(resolved,{recursive:true,force:true})
}
