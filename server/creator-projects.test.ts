import assert from 'node:assert/strict'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import type {AddressInfo} from 'node:net'
import {creatorProjectsApi} from './creator-projects.ts'

test('作品主线会创建档案并回写 Agent 剪辑工程',async()=>{
  const previous=process.env.EMOTIONCUT_USER_DATA
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'emotioncut-creator-'))
  process.env.EMOTIONCUT_USER_DATA=dataRoot
  const server=http.createServer((req,res)=>void creatorProjectsApi(req,res))
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve))
  const origin=`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/creator/projects`
  try{
    const response=await fetch(origin,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({topic:'用 Agent 导演一条产品视频',audience:'内容创作者'})})
    assert.equal(response.status,201)
    const created=(await response.json()).result
    assert.equal(created.stage,'brief')
    assert.equal(created.audience,'内容创作者')
    assert.equal(created.metrics.views,null)
    const generalProjectId='12345678-1234-1234-1234-123456789abc'
    const patchedResponse=await fetch(`${origin}/${created.id}`,{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({generalProjectId,stage:'edit'})})
    assert.equal(patchedResponse.status,200)
    const patched=(await patchedResponse.json()).result
    assert.equal(patched.generalProjectId,generalProjectId)
    assert.equal(patched.stage,'edit')
    assert.equal(patched.revision,2)
    const reopened=await fetch(`${origin}/${created.id}`).then(value=>value.json())
    assert.equal(reopened.result.generalProjectId,generalProjectId)
  }finally{
    server.closeAllConnections()
    await new Promise<void>(resolve=>server.close(()=>resolve()))
    if(previous===undefined)delete process.env.EMOTIONCUT_USER_DATA
    else process.env.EMOTIONCUT_USER_DATA=previous
    fs.rmSync(dataRoot,{recursive:true,force:true})
  }
})
