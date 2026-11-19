import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import crypto from 'node:crypto'
import { spawn } from 'node:child_process'
import { findMotionPackages, inspectMotionPackage, type MotionPackageInfo } from './motion-package.ts'
import { parseHTML } from 'linkedom'
import { browserInstruction, resolveBrowser } from './browser-executable.ts'
import { ensureBrowserProfile } from './browser-profile.ts'

export type MotionCatalogPackage = MotionPackageInfo & { manifestPath?: string; engine?: string; controlCount?: number }
type MotionCatalog = { version: 1; roots: string[]; packages: MotionCatalogPackage[]; updatedAt: string }

const profileRoot = () => process.env.EMOTIONCUT_USER_DATA
  || path.join(process.cwd(), '.dev')
const catalogFile = () => path.join(profileRoot(), 'motion-library.json')

function empty(): MotionCatalog { return { version: 1, roots: [], packages: [], updatedAt: new Date(0).toISOString() } }

export function readMotionCatalog(): MotionCatalog {
  try {
    const value = JSON.parse(fs.readFileSync(catalogFile(), 'utf8')) as MotionCatalog
    if (value.version === 1 && Array.isArray(value.roots) && Array.isArray(value.packages)) return value
  } catch { /* first run or a damaged cache: rebuild on import */ }
  return empty()
}

function writeCatalog(value: MotionCatalog) {
  const file = catalogFile(), folder = path.dirname(file)
  fs.mkdirSync(folder, { recursive: true })
  const temp = file + '.' + process.pid + '.tmp'
  fs.writeFileSync(temp, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 })
  fs.renameSync(temp, file)
}

export function registerMotionRoot(root: string) {
  if (!path.isAbsolute(root)) throw Error('模板目录必须是绝对路径')
  const resolved = fs.realpathSync(root)
  if (!fs.statSync(resolved).isDirectory()) throw Error('模板路径不是目录')
  const scanned = findMotionPackages(resolved)
  const manifests = new Map<string, { path: string; engine?: string; controlCount: number }>()
  const collectManifests = (folder: string) => {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      const full = path.join(folder, entry.name)
      if (entry.isDirectory()) collectManifests(full)
      else if (entry.isFile() && entry.name === 'manifest.json' && entry.name.length < 64) {
        try {
          const stat = fs.statSync(full); if (stat.size > 1024 * 1024) continue
          const value = JSON.parse(fs.readFileSync(full, 'utf8'))
          const pkg = value?.kit?.pkg
          if (typeof pkg === 'string' && path.isAbsolute(pkg)) manifests.set(fs.realpathSync(pkg).toLowerCase(), {
            path: full, engine: typeof value.engine === 'string' ? value.engine : undefined,
            controlCount: value.kinds && typeof value.kinds === 'object' ? Object.keys(value.kinds).length : 0,
          })
        } catch { /* malformed sidecar metadata never invalidates the signed package */ }
      }
    }
  }
  collectManifests(resolved)
  const old = readMotionCatalog()
  const roots = [...new Set([...old.roots.filter(item => fs.existsSync(item)), resolved])]
  const packages = new Map(old.packages.filter(item => fs.existsSync(item.path)).map(item => [item.path.toLowerCase(), item]))
  for (const item of scanned.packages) {
    const sidecar = manifests.get(item.path.toLowerCase())
    packages.set(item.path.toLowerCase(), { ...item, ...(sidecar ? { manifestPath: sidecar.path, engine: sidecar.engine, controlCount: sidecar.controlCount } : {}) })
  }
  const next: MotionCatalog = {
    version: 1,
    roots,
    packages: [...packages.values()].sort((a, b) => (a.template.name || a.fileName).localeCompare(b.template.name || b.fileName, 'zh-CN')),
    updatedAt: new Date().toISOString(),
  }
  writeCatalog(next)
  return { catalog: next, failures: scanned.failures }
}

export function motionTemplateManifest(sha256: string) {
  if (!/^[a-f0-9]{64}$/.test(sha256)) throw Error('模板编号不正确')
  const item = readMotionCatalog().packages.find(entry => entry.sha256 === sha256)
  if (!item?.manifestPath) throw Error('这个模板还没有可用的参数清单')
  const file = fs.realpathSync(item.manifestPath)
  const stat = fs.statSync(file)
  if (!stat.isFile() || stat.size > 1024 * 1024) throw Error('模板参数清单不合法')
  const value = JSON.parse(fs.readFileSync(file, 'utf8'))
  return { id: value.id, name: value.name, engine: value.engine, theme: value.theme, desc: value.desc, kinds: value.kinds || {}, kit: {
    workflow: value.kit?.workflow, layout: value.kit?.layout, width: value.kit?.width, height: value.kit?.height,
    version: value.kit?.version, styles: Array.isArray(value.kit?.styles) ? value.kit.styles : [],
  } }
}

export function registerMotionFile(file: string) {
  if (!path.isAbsolute(file)) throw Error('模板文件必须是绝对路径')
  const item = inspectMotionPackage(fs.realpathSync(file))
  const old = readMotionCatalog()
  const packages = new Map(old.packages.filter(entry => fs.existsSync(entry.path)).map(entry => [entry.path.toLowerCase(), entry]))
  packages.set(item.path.toLowerCase(), item)
  const next = { ...old, packages: [...packages.values()], updatedAt: new Date().toISOString() }
  writeCatalog(next)
  return next
}

/** 版本排序键：2.7.6 / 2.7.18-alpha.7 / 2.7.20-alpha.12。正式版 > 预发布，预发布按序号比。 */
function versionRank(value?: string): number[] {
  const m = /(d+).(d+).(d+)(?:-([a-z]+).(d+))?/i.exec(value || '')
  if (!m) return [-1, -1, -1, -1, -1]
  return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] ? 0 : 1, m[5] ? Number(m[5]) : -1]
}

function compareVersionRank(a: number[], b: number[]): number {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]
  return 0
}

/**
 * 同名模板只留一份：本地目录和 MotionGit 在线导入目录经常各存一份同款不同版本，
 * 列表里成对出现（比如「活力海报 · 口播讲解 · 竖屏」2.7.6 + 2.7.18-alpha.7）。
 * 保留策略：优先留带参数清单（manifestPath）的那份——详情页的控件、说明全靠它，
 * 在线导入包目前都没有清单，留它详情页就是残废；两份都有或都没有时留版本更高的。
 * 只做读取层去重，磁盘 catalog 不动，被挡掉的 sha 仍能按原样找回。
 */
export function dedupeMotionPackages(packages: MotionCatalogPackage[]): MotionCatalogPackage[] {
  const groups = new Map<string, MotionCatalogPackage[]>()
  for (const item of packages) {
    const name = (item.template.name || item.fileName.replace(/.motion$/i, '')).trim()
    const key = name.toLocaleLowerCase().replace(/s+/g, '')
    const list = groups.get(key)
    if (list) list.push(item)
    else groups.set(key, [item])
  }
  const dropped = new Set<MotionCatalogPackage>()
  for (const list of groups.values()) {
    if (list.length < 2) continue
    const best = [...list].sort((a, b) =>
      Number(Boolean(b.manifestPath)) - Number(Boolean(a.manifestPath))
      || compareVersionRank(versionRank(b.template.version), versionRank(a.template.version))
      || a.path.localeCompare(b.path))[0]
    for (const item of list) if (item !== best) dropped.add(item)
  }
  return packages.filter(item => !dropped.has(item))
}
export function motionCatalogStats(catalog = readMotionCatalog()) {
  const layouts = catalog.packages.reduce<Record<string, number>>((all, item) => {
    const key = item.template.layout || 'unknown'; all[key] = (all[key] || 0) + 1; return all
  }, {})
  return { count: catalog.packages.length, bytes: catalog.packages.reduce((sum, item) => sum + item.bytes, 0), layouts }
}

export function defaultMotionSearchRoot() {
  return process.env.EMOTIONCUT_MOTION_ROOT || path.join(os.homedir(), 'Motion Templates')
}

const safeSlug=(value:string)=>value.toLowerCase().replace(/[^a-z0-9_-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,80)||'motion-template'
const onlineCacheRoot=()=>path.join(profileRoot(),'motion-templates')
/**
 * MotionGit 授权要一个自己的 profile：它和矩阵账号是两套登录态，
 * 共用同一目录时 Chrome 的进程单例会把手动起的第二个窗口转给已在跑的那个实例，
 * 新的 --remote-debugging-port 被丢掉，表现成「说了已打开、其实什么都没开」。
 */
const MOTIONGIT_PORT=9333
const motionGitProfile=()=>path.join(profileRoot(),'creator','motiongit-profile')
const motionGitReady=async()=>{
  try{const response=await fetch(`http://127.0.0.1:${MOTIONGIT_PORT}/json/version`,{signal:AbortSignal.timeout(1500)});return response.ok}
  catch{return false}
}
/** 起窗口并等它真的把调试端口开起来；起不来就说清楚，不要谎报「已打开」。 */
export async function authorizeMotionGit(){
  if(await motionGitReady())return {opened:false,port:MOTIONGIT_PORT,reason:'授权窗口已经开着'}
  const browser=resolveBrowser('ego')
  if(!browser)throw Error(browserInstruction('ego'))
  const profile=motionGitProfile();fs.mkdirSync(profile,{recursive:true})
  // 同样补 Ego 首启标记，否则授权窗口每次都停在「欢迎使用 ego(lite)」。
  ensureBrowserProfile(profile)
  const child=spawn(browser.path,[`--remote-debugging-port=${MOTIONGIT_PORT}`,`--user-data-dir=${profile}`,'--no-first-run','--no-default-browser-check','https://motiongit.com/login'],{detached:true,stdio:'ignore',windowsHide:false})
  child.unref()
  for(let i=0;i<24;i++){
    await new Promise(resolve=>setTimeout(resolve,350))
    if(await motionGitReady())return {opened:true,port:MOTIONGIT_PORT,browser:browser.kind}
  }
  throw Error(`授权窗口没起来：${MOTIONGIT_PORT} 端口一直没响应。可能被别的浏览器实例占用了同一个 profile，先关掉那个窗口再试。`)
}

/** Download one user-authorized MotionGit package and immediately register it.
 * Existing packages are returned without touching the network. The browser is
 * only used to carry the user's own MotionGit session; credentials never enter
 * eMotionCut's catalog or project files.
 */
export async function installMotionGitTemplate(input:string){
  const href=new URL(input)
  if(href.protocol!=='https:'||href.hostname!=='motiongit.com')throw Error('只允许安装 MotionGit 官方模板')
  const slug=safeSlug(href.pathname.split('/').filter(Boolean).at(-1)||'motion-template')
  const existing=readMotionCatalog().packages.find(item=>safeSlug(item.fileName.replace(/\.motion$/i,''))===slug||safeSlug(item.template.id||'')===slug)
  if(existing)return {template:existing,catalog:readMotionCatalog(),cached:true}
  const {chromium}=await import('playwright-core')
  let browser:import('playwright-core').Browser
  try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${MOTIONGIT_PORT}`)}
  catch{await authorizeMotionGit();throw Error('已打开 MotionGit 首次授权窗口。登录一次后回到这里再次点击模板，以后无需手动下载。')}
  try{
    const context=browser.contexts()[0];if(!context)throw Error('没有可用的授权浏览器')
    const previous=context.pages().at(-1),page=await context.newPage();let keepAuthorizationPage=false
    try{
      await page.goto(href.href,{waitUntil:'domcontentloaded',timeout:60_000});await page.waitForTimeout(1200)
      if(/\/login(?:\?|$)/.test(page.url())||await page.locator('a[href^="/login"]').count()&&!await page.locator('a[href*="/download/"]').count()){keepAuthorizationPage=true;await page.goto(`https://motiongit.com/login?next=${encodeURIComponent(href.pathname)}`,{waitUntil:'domcontentloaded'});await page.bringToFront();throw Error('请在刚打开的窗口完成一次 MotionGit 授权，登录后回到 eMotionCut 再点一次模板')}
      let download=await page.locator('a[href*="/download/"]').first().getAttribute('href').catch(()=>null)
      if(!download){
        const claim=page.getByRole('button',{name:/获取模板|添加到我的模板/}).or(page.getByRole('link',{name:/获取模板|添加到我的模板/})).first()
        if(await claim.count()){await claim.click();await page.waitForTimeout(1800);download=await page.locator('a[href*="/download/"]').first().getAttribute('href').catch(()=>null)}
      }
      if(!download)throw Error('这个条目暂未提供可执行的 .motion 包，只能作为创意参考')
      const response=await context.request.get(new URL(download,page.url()).href,{timeout:120_000})
      if(!response.ok())throw Error(`模板下载失败（${response.status()}），请稍后重试`)
      const bytes=await response.body();if(bytes.length<22)throw Error('模板包内容为空')
      const folder=onlineCacheRoot();fs.mkdirSync(folder,{recursive:true})
      const temp=path.join(folder,`.${slug}-${crypto.randomUUID()}.motion`);fs.writeFileSync(temp,bytes,{mode:0o600})
      try{
        const inspected=inspectMotionPackage(temp),file=path.join(folder,`${safeSlug(inspected.template.id||slug)}.motion`)
        fs.renameSync(temp,file);const catalog=registerMotionFile(file)
        const template=catalog.packages.find(item=>item.path===fs.realpathSync(file))||inspectMotionPackage(file)
        return {template,catalog,cached:false}
      }catch(error){try{fs.unlinkSync(temp)}catch{};throw error}
    }finally{if(!keepAuthorizationPage){await page.close();await previous?.bringToFront()}}
  }finally{await browser.close()}
}

let onlineCache:{expires:number;items:unknown[]}|undefined
export async function motionGitOnlineCatalog(){
  if(onlineCache&&onlineCache.expires>Date.now())return onlineCache.items
  const items:Array<{title:string;href:string;poster:string;preview:string;ratio:string;duration:string;category:string;contentType:string}>=[]
  for(let page=1;page<=3;page++){
    const response=await fetch(`https://motiongit.com/templates?sort=popular&page=${page}&lang=zh&currency=CNY`,{headers:{'user-agent':'eMotionCut/2.0 MotionGit catalog'}})
    if(!response.ok)throw Error(`MotionGit 模板目录暂时不可用（${response.status}）`)
    const {document}=parseHTML(await response.text())
    for(const card of [...document.querySelectorAll('article.vcard')]){
      const anchor=card.querySelector('a.vthumb'),href=anchor?.getAttribute('href')||''
      if(!href)continue
      const label=anchor?.getAttribute('aria-label')||'',parts=label.split('，').map(value=>value.trim())
      items.push({title:card.querySelector('h3')?.textContent?.trim()||parts[0]||'未命名模板',href:new URL(href,'https://motiongit.com').href,poster:card.querySelector('img')?.getAttribute('src')||'',preview:card.querySelector('video')?.getAttribute('data-src')||'',ratio:card.querySelector('.vbadge')?.textContent?.trim()||'',duration:card.querySelector('.vtc')?.textContent?.trim()||'',category:parts.at(-2)||'',contentType:card.getAttribute('data-content-type')||''})
    }
  }
  const unique=[...new Map(items.map(item=>[item.href,item])).values()]
  onlineCache={expires:Date.now()+10*60_000,items:unique};return unique
}
