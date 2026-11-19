import type { IncomingMessage, ServerResponse } from 'node:http'
import { bodyJson, sendJson } from '../http.ts'
import { authorizeMotionGit, dedupeMotionPackages, defaultMotionSearchRoot, installMotionGitTemplate, motionCatalogStats, motionGitOnlineCatalog, motionTemplateManifest, readMotionCatalog, registerMotionFile, registerMotionRoot } from '../motion-library.ts'

export async function motionApi(req: IncomingMessage, res: ServerResponse) {
  const route = new URL(req.url || '/', 'http://localhost').pathname.replace('/api/motion', '') || '/'
  if (req.method === 'GET' && route === '/templates') {
    const catalog = readMotionCatalog()
    // 同名模板只暴露一份（本地正式版优先），MotionGit 在线导入的重复版本不再成对出现。
    const deduped = { ...catalog, packages: dedupeMotionPackages(catalog.packages) }
    return sendJson(res, 200, { ok: true, result: { ...deduped, stats: motionCatalogStats(deduped), suggestedRoot: defaultMotionSearchRoot() } })
  }
  if(req.method==='GET'&&route==='/online-catalog')return sendJson(res,200,{ok:true,result:await motionGitOnlineCatalog()})
  /** 让用户能主动去登录一次，而不是只能靠「安装失败」触发授权窗口。 */
  if(req.method==='POST'&&route==='/authorize'){
    try{return sendJson(res,200,{ok:true,result:await authorizeMotionGit()})}
    catch(error){return sendJson(res,502,{ok:false,error:error instanceof Error?error.message:String(error)})}
  }
  if(req.method==='POST'&&route==='/online-install'){
    try{const input=await bodyJson(req),href=typeof input.href==='string'?input.href.trim():'';if(!href)return sendJson(res,400,{ok:false,error:'缺少模板地址'});return sendJson(res,200,{ok:true,result:await installMotionGitTemplate(href)})}
    catch(error){return sendJson(res,502,{ok:false,error:error instanceof Error?error.message:String(error)})}
  }
  const manifest = route.match(/^\/templates\/([a-f0-9]{64})\/manifest$/)
  if (req.method === 'GET' && manifest) return sendJson(res, 200, { ok: true, result: motionTemplateManifest(manifest[1]) })
  if (req.method === 'POST' && route === '/roots') {
    const input = await bodyJson(req)
    const root = typeof input.root === 'string' ? input.root.trim() : ''
    if (!root) return sendJson(res, 400, { ok: false, error: '请填写 .motion 模板目录' })
    return sendJson(res, 200, { ok: true, result: registerMotionRoot(root) })
  }
  if (req.method === 'POST' && route === '/files') {
    const input = await bodyJson(req)
    const file = typeof input.file === 'string' ? input.file.trim() : ''
    if (!file) return sendJson(res, 400, { ok: false, error: '请填写 .motion 文件路径' })
    return sendJson(res, 200, { ok: true, result: registerMotionFile(file) })
  }
  return sendJson(res, 404, { ok: false, error: '动效库没有这个接口' })
}
