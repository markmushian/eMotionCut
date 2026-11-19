/**
 * 矩阵发布 · 多账号 · 作品监控
 *
 * 每个账号一套独立的浏览器 profile 与调试端口：登录态互不干扰，切换账号等于切换目录。
 * 发布走真实浏览器（Playwright 连本机 CDP），因此平台改版只需要改选择器，不需要跟随签名算法。
 * 默认停在「发布」按钮前由人确认；只有显式打开 autoSubmit 才会代为点击。
 */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import net from 'node:net'
import {spawn} from 'node:child_process'
import {browserInstruction,resolveBrowser} from './browser-executable.ts'
import {generateCoverImages} from './cover-image-generator.ts'
import {composeCovers} from './cover-compose.ts'
import {
  composeCaption, pickCoverFor, profileFor, ratiosFor, readProfiles, saveProfiles,
  type PlatformProfile,
} from './cover-platforms.ts'
import {findCodexCli,generateWithCodex,imageSize} from './codex-image-generator.ts'
import {ensureBrowserProfile} from './browser-profile.ts'
import type {IncomingMessage,ServerResponse} from 'node:http'
import type {Page as PlaywrightPage} from 'playwright-core'
import {bodyJson,sendJson} from './http.ts'
import {askCreatorAgent} from './creator-agent-bridge.ts'
import {COVER_ANALYSIS_SYSTEM,COVER_DIRECTOR_SYSTEM,coverAnalysisPrompt,coverDirectorPrompt} from './cover-playbook.ts'
import {extractHeadline} from './cover-headline.ts'
import {
  collectFollowers, collectWorkMetrics, followerCollectors, metricCollectors,
  overviewOf, readFollowers, reviewOf, weeklyOf,
} from './monitor-hub.ts'

export const MATRIX_PLATFORMS = ['douyin','channels','xhs','kuaishou','bilibili','baijiahao','toutiao','fanqie','tiktok','youtube','instagram','x'] as const
export type MatrixPlatform = typeof MATRIX_PLATFORMS[number]

type PlatformSpec = {
  name: string; domain: string; cookies: string[]
  loginUrl: string; createUrl: string
  /** 登录后能读到昵称的页面；没有就退回 loginUrl。 */
  homeUrl?: string
  /** 能自动上传并填表的平台；其余只打开页面，不假报成功。 */
  upload: boolean
}

export const PLATFORMS: Record<MatrixPlatform, PlatformSpec> = {
  douyin: {name:'抖音',domain:'douyin.com',cookies:['sessionid','sessionid_ss','sid_tt','sid_guard'],loginUrl:'https://creator.douyin.com/',homeUrl:'https://creator.douyin.com/creator-micro/home',createUrl:'https://creator.douyin.com/creator-micro/content/upload',upload:true},
  channels: {name:'视频号',domain:'channels.weixin.qq.com',cookies:['sessionid','ticket','finder-username','data_biz_uid'],loginUrl:'https://channels.weixin.qq.com/platform',homeUrl:'https://channels.weixin.qq.com/platform',createUrl:'https://channels.weixin.qq.com/platform/post/create',upload:true},
  xhs: {name:'小红书',domain:'xiaohongshu.com',cookies:['web_session','web_sessionid'],loginUrl:'https://creator.xiaohongshu.com/',homeUrl:'https://creator.xiaohongshu.com/new/home',createUrl:'https://creator.xiaohongshu.com/publish/publish?source=official',upload:true},
  kuaishou: {name:'快手',domain:'kuaishou.com',cookies:['kuaishou.server.webday7_st','kuaishou.server.web_st','passToken','userId'],loginUrl:'https://cp.kuaishou.com/',createUrl:'https://cp.kuaishou.com/article/publish/video',upload:false},
  bilibili: {name:'B站',domain:'bilibili.com',cookies:['SESSDATA','DedeUserID'],loginUrl:'https://member.bilibili.com/platform/home',createUrl:'https://member.bilibili.com/platform/upload/video/frame',upload:false},
  baijiahao: {name:'百家号',domain:'baijiahao.baidu.com',cookies:['BDUSS','STOKEN'],loginUrl:'https://baijiahao.baidu.com/',createUrl:'https://baijiahao.baidu.com/builder/rc/edit?type=videoV2',upload:false},
  toutiao: {name:'头条号',domain:'mp.toutiao.com',cookies:['sessionid','sid_tt'],loginUrl:'https://mp.toutiao.com/',createUrl:'https://mp.toutiao.com/profile_v4/xigua/upload-video',upload:false},
  fanqie: {name:'番茄视频',domain:'fanqienovel.com',cookies:['sessionid'],loginUrl:'https://fanqienovel.com/',createUrl:'https://fanqienovel.com/',upload:false},
  // 海外四家：只打开发布页、封面与文案由用户手动上传，不假报成功。
  tiktok: {name:'TikTok',domain:'tiktok.com',cookies:['sessionid','sessionid_ss','tt_csrf_token'],loginUrl:'https://www.tiktok.com/tiktokstudio',homeUrl:'https://www.tiktok.com/tiktokstudio',createUrl:'https://www.tiktok.com/tiktokstudio/upload',upload:false},
  youtube: {name:'YouTube',domain:'youtube.com',cookies:['SID','HSID','SSID','APISID'],loginUrl:'https://studio.youtube.com/',homeUrl:'https://studio.youtube.com/',createUrl:'https://studio.youtube.com/channel/UC/videos/upload',upload:false},
  instagram: {name:'Instagram',domain:'instagram.com',cookies:['sessionid','ds_user_id'],loginUrl:'https://www.instagram.com/',homeUrl:'https://www.instagram.com/',createUrl:'https://www.instagram.com/',upload:false},
  x: {name:'X (Twitter)',domain:'x.com',cookies:['auth_token','ct0'],loginUrl:'https://x.com/',homeUrl:'https://x.com/home',createUrl:'https://x.com/compose/post',upload:false},
}

export type MatrixAccount = {
  id: string; platform: MatrixPlatform; alias: string; profile: string; port: number; createdAt: string
  lastCheck?: {at: string; loggedIn: boolean; by: string; error?: string}
  /** 登录后从平台读到的昵称；只做展示与「自动命名」，用户改过备注就不再覆盖。 */
  nickname?: string
  /** 平台侧的账号标识（抖音号 / 小红书号），读得到就存下来，便于区分同名号。 */
  platformId?: string
  /** false = 备注是用户自己写的。缺省 true：没手改过，检测登录时跟着平台昵称走。 */
  aliasAuto?: boolean
  identityCheckedAt?: string
}

export type PublishTask = {
  id: string; accountId: string; platform: MatrixPlatform; file: string
  /** covers/ 目录里的封面文件名；按平台档案的首选比例自动配对。 */
  cover?: string
  /** 这个封面是什么比例，方便判断平台会不会裁。 */
  coverRatio?: string
  /** 封面与平台首选比例不一致、或压根没有对应比例时的说明。 */
  coverNote?: string
  title: string; tags: string[]; scheduledAt?: string; autoSubmit: boolean
  /** 按平台档案拼好的标题（已按上限裁剪、话题已拼好）。 */
  caption?: string
  /** 检查点（借鉴 AiToEarn）：file_uploaded=视频已交给平台，filled=文案/封面已填完。
   *  超时补偿与人工判断都用它区分「卡在哪一步」，绝不在 checkpoint 之后盲目重发。 */
  checkpoint?: 'file_uploaded' | 'filled'
  status: 'queued' | 'running' | 'ready' | 'done' | 'failed' | 'cancelled'
  step: string; error?: string; url?: string; createdAt: string; updatedAt: string
  /** 手机发布（UserHandoff）：人在手机抖音官方发布，桌面只做文案随行 + 自动对账。 */
  handoff?: {startedAt: string}
}

export type CommentRow = { id: string; text: string; user: string; like: number; at?: string; replies?: number }
/** 收藏/分享/投币由后台采集器补齐，老快照没有这些字段，保持可选。 */
export type MetricSnapshot = {at: string; views: number|null; likes: number|null; comments: number|null; danmaku: number|null; favorites?: number|null; shares?: number|null; coins?: number|null}
export type MonitoredWork = {
  id: string; accountId: string; platform: MatrixPlatform; url: string; title: string
  metrics: {views: number|null; likes: number|null; comments: number|null; danmaku: number|null; favorites?: number|null; shares?: number|null; coins?: number|null}
  comments: CommentRow[]
  /** 作品墙的封面：回填发布链接时从任务带走，或手动登记时从素材库选。 */
  cover?: string
  /** 每次同步留一个快照，看板才有真实的增长曲线；同一天只保留最后一次。 */
  series?: MetricSnapshot[]
  syncedAt: string
}

const userRoot = () => process.env.EMOTIONCUT_USER_DATA || path.join(process.cwd(), '.dev')
const matrixRoot = () => path.join(userRoot(), 'creator', 'matrix')
const accountsFile = () => path.join(matrixRoot(), 'accounts.json')
const tasksDir = () => path.join(matrixRoot(), 'tasks')
const worksFile = () => path.join(matrixRoot(), 'works.json')

function readJson<T>(file: string, fallback: T): T {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')) as T } catch { return fallback }
}
function writeJson(file: string, value: unknown) {
  fs.mkdirSync(path.dirname(file), {recursive: true})
  const temp = file + '.tmp'
  fs.writeFileSync(temp, JSON.stringify(value, null, 2) + '\n', 'utf8')
  fs.renameSync(temp, file)
}

export function listAccounts(): MatrixAccount[] {
  return readJson<MatrixAccount[]>(accountsFile(), []).sort((a, b) => a.createdAt.localeCompare(b.createdAt))
}
function saveAccounts(rows: MatrixAccount[]) { writeJson(accountsFile(), rows) }

/** 每个账号一个独立 profile；端口从 9341 起递增，避开旧单账号的 9333。 */
export function addAccount(platform: MatrixPlatform, alias: string): MatrixAccount {
  if (!(platform in PLATFORMS)) throw Object.assign(Error('不支持的平台'), {status: 400})
  const accounts = listAccounts()
  const id = crypto.randomUUID()
  const port = Math.max(9340, ...accounts.map(a => a.port)) + 1
  const account: MatrixAccount = {
    id, platform, alias: (alias || '').trim() || `${PLATFORMS[platform].name}账号${accounts.filter(a => a.platform === platform).length + 1}`,
    profile: path.join(matrixRoot(), 'profiles', id), port, createdAt: new Date().toISOString(),
    aliasAuto: !(alias || '').trim(),
  }
  fs.mkdirSync(account.profile, {recursive: true})
  saveAccounts([...accounts, account])
  return account
}

export function removeAccount(id: string) {
  const accounts = listAccounts(), target = accounts.find(a => a.id === id)
  if (!target) throw Object.assign(Error('账号不存在'), {status: 404})
  saveAccounts(accounts.filter(a => a.id !== id))
  // profile 保留：里面是登录态，误删要重登。只解绑，不销毁用户数据。
  return {removed: id, profileKept: target.profile}
}

export function patchAccount(id: string, input: {alias?: string; follow?: boolean}) {
  const accounts = listAccounts(), target = accounts.find(a => a.id === id)
  if (!target) throw Object.assign(Error('账号不存在'), {status: 404})
  if (typeof input.alias === 'string' && input.alias.trim()) {
    target.alias = input.alias.trim().slice(0, 40)
    // 用户自己写了备注，之后的登录检测不再用平台昵称改名（昵称仍然记下来展示）。
    target.aliasAuto = false
  }
  if (input.follow === true && target.nickname) {
    target.alias = target.nickname.slice(0, 40)
    target.aliasAuto = true
  }
  saveAccounts(accounts)
  return target
}

/** 「备注跟着平台昵称走」的开关：清掉手写备注，下次检测重新按昵称命名。 */
export function followPlatformName(id: string) {
  const accounts = listAccounts(), target = accounts.find(a => a.id === id)
  if (!target) throw Object.assign(Error('账号不存在'), {status: 404})
  target.aliasAuto = true
  if (target.nickname) target.alias = target.nickname.slice(0, 40)
  saveAccounts(accounts)
  return target
}

/** 默认 Ego Lite，装不上再退 Chrome / Edge；三者都认 CDP，账号仍然各自独立 profile。 */
function browserExecutable() {
  const target = resolveBrowser('ego')
  if (!target) throw Object.assign(Error(browserInstruction('ego')), {status: 400})
  return target
}

/** 打开这个账号自己的登录窗口。窗口要一直开着：发布时连的是它，不是新起一个。 */
export function openAccountWindow(id: string) {
  const account = listAccounts().find(a => a.id === id)
  if (!account) throw Object.assign(Error('账号不存在'), {status: 404})
  const browser = browserExecutable()
  // Ego 的首启引导标记要先补上，否则每次新建的 profile 都会停在「欢迎使用 ego(lite)」。
  ensureBrowserProfile(account.profile)
  const child = spawn(browser.path, [
    `--remote-debugging-port=${account.port}`,
    `--user-data-dir=${account.profile}`,
    '--no-first-run', '--no-default-browser-check',
    PLATFORMS[account.platform].loginUrl,
  ], {detached: true, stdio: 'ignore', windowsHide: false})
  child.unref()
  return {accountId: account.id, port: account.port, url: PLATFORMS[account.platform].loginUrl, browser: browser.kind}
}

async function cdp(port: number) {
  const {chromium} = await import('playwright-core')
  return chromium.connectOverCDP(`http://127.0.0.1:${port}`)
}

/** 先探端口再连 CDP：窗口没开时给一句人能照做的话，而不是把 ECONNREFUSED 原样甩给用户。 */
export function portOpen(port: number, timeout = 900): Promise<boolean> {
  return new Promise(resolve => {
    const socket = net.createConnection({host: '127.0.0.1', port})
    let done = false
    const finish = (ok: boolean) => { if (!done) { done = true; socket.destroy(); resolve(ok) } }
    socket.setTimeout(timeout)
    socket.once('connect', () => finish(true))
    socket.once('timeout', () => finish(false))
    socket.once('error', () => finish(false))
  })
}

/** 采集/发布统一入口：没窗口就直说，不产生一串无法判断的浏览器报错。 */
async function connectAccount(account: MatrixAccount): Promise<Awaited<ReturnType<typeof cdp>>> {
  if (!await portOpen(account.port)) {
    throw Object.assign(Error(`账号「${account.alias}」的浏览器窗口没开着（端口 ${account.port}）。到「矩阵分发 → 账号矩阵」点「打开登录窗口」，登录后再试。`), {status: 400})
  }
  try { return await cdp(account.port) }
  catch (cause) {
    throw Object.assign(Error(`连不上账号「${account.alias}」的窗口（端口 ${account.port}）：${cause instanceof Error ? cause.message.slice(0, 120) : String(cause)}。关掉旧窗口后重新打开再试。`), {status: 400})
  }
}

type AccountIdentity = {nickname: string; platformId: string}

type Filler = (page: PlaywrightPage, task: PublishTask) => Promise<PlaywrightPage>
/** 同步公开数据时挂在页面上的响应监听：评论进 sink.comments、统计覆盖 sink.stats.value。 */
type DataWire = (page: PlaywrightPage, sink: {comments: CommentRow[]; stats: {value: MonitoredWork['metrics']}}) => void

/**
 * 平台 Provider 注册表（借鉴 AiToEarn 的 platforms.interface）：
 * identity（账号识别）/ fill（发布填表）/ wire（数据采集监听）按平台收拢。
 * 主流程 runTask / readIdentity / syncWork 只调接口——接新平台 = 加一个 Provider 对象，不动主流程。
 * 没写 fill 的平台 = 自动填表不支持，发布页打开放在那里交给人。
 */
export type MatrixProvider = {
  name: string
  identity?: () => AccountIdentity
  fill?: Filler
  wire?: DataWire
}

export const PROVIDERS: Record<MatrixPlatform, MatrixProvider> = {
  douyin: {
    name: '抖音',
    identity: () => {
      const clean = (value?: string | null) => (value || '').replace(/\s+/g, ' ').trim()
      // 首页左侧账号区：<span class="name-xxxx">昵称</span>，下面一行是「抖音号：…」
      const nameNode = document.querySelector('[class*="name-"]')
      const name = clean(nameNode?.textContent)
      const text = clean(document.body?.innerText)
      const idMatch = text.match(/抖音号[:：]\s*([A-Za-z0-9_.-]{2,40})/)
      return {nickname: name.length > 0 && name.length <= 24 ? name : '', platformId: idMatch?.[1] || ''}
    },
    fill: fillDouyin,
    wire: wireDouyinMetrics,
  },
  channels: {
    name: '视频号',
    identity: () => {
      const clean = (value?: string | null) => (value || '').replace(/\s+/g, ' ').trim()
      const node = document.querySelector('[class*="nickname"], [class*="nick-name"], [class*="account-name"], [class*="finder-name"]')
      const name = clean(node?.textContent)
      return {nickname: name.length > 0 && name.length <= 24 ? name : '', platformId: ''}
    },
    fill: fillChannels,
  },
  xhs: {
    name: '小红书',
    identity: () => {
      const clean = (value?: string | null) => (value || '').replace(/\s+/g, ' ').trim()
      const node = document.querySelector('[class*="nickname"], [class*="user-name"], [class*="account-name"]')
      const name = clean(node?.textContent)
      const text = clean(document.body?.innerText)
      const idMatch = text.match(/小红书号[:：]\s*([A-Za-z0-9_.-]{2,40})/)
      return {nickname: name.length > 0 && name.length <= 24 ? name : '', platformId: idMatch?.[1] || ''}
    },
    fill: fillXhs,
  },
  kuaishou: {name: '快手'},
  bilibili: {name: 'B站'},
  baijiahao: {name: '百家号'},
  toutiao: {name: '头条号'},
  fanqie: {name: '番茄视频'},
  tiktok: {name: 'TikTok'},
  youtube: {name: 'YouTube'},
  instagram: {name: 'Instagram'},
  x: {name: 'X (Twitter)'},
}

/**
 * 只读账号窗口里已经打开的平台页面，不新建标签页、也不导航。
 * 旧实现每次「检测登录态」都 newPage + goto 平台首页，用户看到的就是 Ego 窗口被弹出来跳走；
 * 检测状态应该是原地刷新，读得到就读，读不到就维持原来的昵称。
 */
async function readIdentity(browser: Awaited<ReturnType<typeof cdp>>, platform: MatrixPlatform): Promise<AccountIdentity | undefined> {
  const script = PROVIDERS[platform].identity
  if (!script) return undefined
  const domain = PLATFORMS[platform].domain
  for (const context of browser.contexts()) for (const page of context.pages()) {
    let url = ''
    try { url = page.url() } catch { continue }
    if (!url || url === 'about:blank' || !url.includes(domain)) continue
    try {
      const value = await page.evaluate(script)
      const nickname = (value?.nickname || '').replace(/\s+/g, ' ').trim().slice(0, 40)
      if (nickname) return {nickname, platformId: (value?.platformId || '').trim().slice(0, 60)}
    } catch { /* 页面可能在跳转，换下一个 */ }
  }
  return undefined
}

/** 单独刷新一个账号的昵称（账号列表上的「读昵称」）。窗口没开就直接说清楚。 */
export async function refreshAccountIdentity(id: string) {
  const account = listAccounts().find(a => a.id === id)
  if (!account) throw Object.assign(Error('账号不存在'), {status: 404})
  let browser: Awaited<ReturnType<typeof cdp>> | undefined
  try {
    browser = await connectAccount(account)
    const identity = await readIdentity(browser, account.platform)
    if (!identity) throw Object.assign(Error('这个账号的窗口里没有打开平台页面，没能读到昵称；把窗口停在平台首页再试（不会替你跳转）'), {status: 400})
    const next = listAccounts().map(row => {
      if (row.id !== id) return row
      const named = {...row, nickname: identity.nickname, identityCheckedAt: new Date().toISOString()}
      if (identity.platformId) named.platformId = identity.platformId
      if (named.aliasAuto !== false || !named.alias.trim()) named.alias = identity.nickname.slice(0, 40)
      return named
    })
    saveAccounts(next)
    return next.find(row => row.id === id)!
  } finally { await browser?.close().catch(() => {}) }
}

/**
 * 老版本的「平台登录态」「抖音搜索/抓作品」只认一个固定窗口（端口 9333）。
 * 现在统一走矩阵账号：同一个平台可能有好几个号，优先用已经确认登录的那个。
 */
export function resolveAccount(platform: MatrixPlatform, preferredId?: string): MatrixAccount | undefined {
  const rows = listAccounts().filter(a => a.platform === platform)
  if (preferredId) {
    const hit = rows.find(a => a.id === preferredId)
    if (hit) return hit
  }
  return rows.find(a => a.lastCheck?.loggedIn) || rows[0]
}

/**
 * 老版本把登录态放在 creator/browser-profile，能顶上就不让用户重登一次。
 * 但这个目录以前也被 MotionGit 授权窗口用过（同一个路径、不同端口），
 * 盲接会让两个进程抢同一个 profile，Chrome 单例直接把后来那次的调试端口丢掉。
 * 所以只认「这台机器还没有任何矩阵账号」的那一次搬家。
 */
const legacyProfile = () => path.join(userRoot(), 'creator', 'browser-profile')

/** 没有这个平台的账号时按需建一个，好让「打开登录窗口」永远有窗口可开。 */
export function ensureAccount(platform: MatrixPlatform, alias = ''): MatrixAccount {
  const exist = resolveAccount(platform)
  if (exist) return exist
  const firstAccount = listAccounts().length === 0
  const account = addAccount(platform, alias)
  if (platform !== 'douyin' || !firstAccount) return account
  try {
    if (!fs.existsSync(legacyProfile()) || !fs.readdirSync(legacyProfile()).length) return account
    if (fs.readdirSync(account.profile).length) return account
    const next = listAccounts().map(a => a.id === account.id ? {...a, profile: legacyProfile()} : a)
    saveAccounts(next)
    return next.find(a => a.id === account.id) || account
  } catch { return account }
}

/** 读这个账号在平台上的登录态：先看 cookie，再看页面有没有账号 UI。 */
export async function checkAccount(id: string) {
  const accounts = listAccounts(), account = accounts.find(a => a.id === id)
  if (!account) throw Object.assign(Error('账号不存在'), {status: 404})
  const spec = PLATFORMS[account.platform]
  let result: MatrixAccount['lastCheck'] = {at: new Date().toISOString(), loggedIn: false, by: 'offline'}
  let browser: Awaited<ReturnType<typeof cdp>> | undefined
  let identity: AccountIdentity | undefined
  try {
    browser = await connectAccount(account)
    const contexts = browser.contexts()
    const cookies = (await Promise.all(contexts.map(c => c.cookies(`https://${spec.domain}/`)))).flat()
    const hit = cookies.find(c => spec.cookies.some(name => name.toLowerCase() === c.name.toLowerCase()) && !!c.value)
    if (hit) result = {at: new Date().toISOString(), loggedIn: true, by: `cookie:${hit.name}`}
    else {
      for (const context of contexts) for (const page of context.pages()) {
        if (!page.url().includes(spec.domain)) continue
        const ok = await page.evaluate(() => {
          const text = document.body?.innerText || ''
          return !/扫码登录|登录后即可|请先登录|立即登录/.test(text.slice(0, 2000))
        }).catch(() => false)
        if (ok) { result = {at: new Date().toISOString(), loggedIn: true, by: 'page'}; break }
      }
    }
    // 登录了就把平台昵称读回来：新账号不用手填备注，老账号改了名也能跟上。
    if (result.loggedIn) identity = await readIdentity(browser, account.platform)
  } catch (error) {
    result = {at: new Date().toISOString(), loggedIn: false, by: 'offline', error: error instanceof Error ? error.message : String(error)}
  } finally { await browser?.close().catch(() => {}) }
  const next = listAccounts().map(a => {
    if (a.id !== id) return a
    const row: MatrixAccount = {...a, lastCheck: result}
    if (identity) {
      row.nickname = identity.nickname
      row.identityCheckedAt = new Date().toISOString()
      if (identity.platformId) row.platformId = identity.platformId
      if (row.aliasAuto !== false || !row.alias.trim()) row.alias = identity.nickname.slice(0, 40)
    }
    return row
  })
  saveAccounts(next)
  return result
}

export async function checkAllAccounts() {
  const accounts = listAccounts()
  const out: Record<string, MatrixAccount['lastCheck']> = {}
  for (const account of accounts) out[account.id] = await checkAccount(account.id)
  return out
}

// ——— 发布 ———

const tasksFile = (id: string) => path.join(tasksDir(), id + '.json')

export function listTasks(): PublishTask[] {
  const dir = tasksDir()
  if (!fs.existsSync(dir)) return []
  return fs.readdirSync(dir).filter(n => n.endsWith('.json')).flatMap(name => {
    try { return [JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8')) as PublishTask] } catch { return [] }
  }).sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}

function saveTask(task: PublishTask) { task.updatedAt = new Date().toISOString(); writeJson(tasksFile(task.id), task); return task }

/**
 * 建发布任务：一条视频 × N 个账号。
 * 封面不再「一张发给所有平台」——按平台档案的首选比例去素材库配，
 * 配不到就明确写进 coverNote，绝不静默拿错比例的图去撞平台的裁剪框。
 */
export function createTasks(input: {accountIds: string[]; file: string; title: string; tags?: string[]; scheduledAt?: string; autoSubmit?: boolean; cover?: string; covers?: Record<string,string>}) {
  if (!fs.existsSync(input.file)) throw Object.assign(Error(`找不到要发布的文件：${input.file}`), {status: 400})
  const profiles = listProfiles()
  const assets = listCoverAssets()
  const entries: {file: string; ratio: string}[] = []
  const raw = {...(input.covers || {})}
  if (input.cover) raw.auto = input.cover
  for (const name of Object.values(raw)) {
    if (!name) continue
    const file = path.basename(String(name))
    if (!coverImage(file)) throw Object.assign(Error(`封面 ${file} 已经不在素材库里，请重新选一张`), {status: 400})
    if (entries.some(row => row.file === file)) continue
    entries.push({file, ratio: assets.find(row => row.file === file)?.ratio || ''})
  }
  const accounts = listAccounts()
  const rows = input.accountIds.map(id => accounts.find(a => a.id === id)).filter((a): a is MatrixAccount => !!a)
  if (!rows.length) throw Object.assign(Error('请先选择至少一个已登录的账号'), {status: 400})
  // 防重：同一标题在同一账号上还没结束的任务不重复建（借鉴麦田的 LOCKED 门禁）。
  // 已取消/失败的可以重来；已 done/ready/running/queued 的挡住，避免同一条视频反复上传触发风控。
  const activeStates: PublishTask['status'][] = ['queued', 'running', 'ready', 'done']
  const existing = listTasks().filter(t => activeStates.includes(t.status) && t.title === String(input.title || '').slice(0, 200))
  const taken = new Set(existing.map(t => t.accountId))
  const fresh = rows.filter(a => !taken.has(a.id))
  if (!fresh.length) {
    throw Object.assign(Error(`「${String(input.title || '').slice(0, 30)}」在这些账号上已有未结束的任务，没有重复建。要重发请先取消旧任务。`), {status: 409})
  }
  const now = new Date().toISOString()
  return fresh.map(account => {
    const profile = profileFor(profiles, account.platform)
    const caption = profile
      ? composeCaption(profile, {title: String(input.title || ''), tags: input.tags})
      : undefined
    const picked = profile ? pickCoverFor(profile, Object.fromEntries(entries.filter(row => row.ratio).map(row => [row.ratio, row.file]))) : undefined
    const fallback = picked ? undefined : entries[0]
    const chosen = picked || fallback
    let coverNote = ''
    if (profile) {
      if (!chosen) coverNote = `这个平台要 ${profile.coverRatio} 封面，素材库里没有；发布页请手动选一张`
      else if (picked && !picked.exact) coverNote = `用 ${picked.ratio} 的图代替 ${profile.coverRatio}，平台会裁边`
      else if (!picked && fallback) coverNote = `这张是 ${fallback.ratio || '未知比例'}，平台按 ${profile.coverRatio} 裁；建议先「按比例套版」出一张对得上的`
    }
    const task = saveTask({
      id: crypto.randomUUID(), accountId: account.id, platform: account.platform, file: input.file,
      ...(chosen ? {cover: chosen.file, coverRatio: chosen.ratio || undefined} : {}),
      ...(coverNote ? {coverNote} : {}),
      ...(caption ? {caption: [caption.titleField, caption.bodyField].filter(Boolean).join('\n').slice(0, 600)} : {}),
      title: String(input.title || '').slice(0, 200), tags: (input.tags || []).map(t => String(t).replace(/^#/, '').trim()).filter(Boolean).slice(0, 12),
      ...(input.scheduledAt ? {scheduledAt: input.scheduledAt} : {}), autoSubmit: input.autoSubmit === true,
      status: 'queued', step: '等待执行', createdAt: now, updatedAt: now,
    })
    // 建任务即存档：计划文案快照（平台、账号、标题、拼好的文案、话题、封面）永久留底。
    archiveFromTask(task, account.alias)
    return task
  })
}

/** 任务所属平台的档案；平台被删掉时给一份保守兜底，不让发布流程炸。 */
function taskProfile(task: PublishTask): PlatformProfile {
  const hit = profileFor(listProfiles(), task.platform)
  if (hit) return hit
  return {
    id: task.platform, name: PLATFORMS[task.platform]?.name || task.platform, coverRatio: '9:16', altRatios: [],
    titleLimit: 55, tagLimit: 5, tagPlacement: 'title', descTemplate: '{title} {tags}',
    safeArea: 6, bottomInset: 8, note: '',
  }
}

/** 一个平台一个文案：标题按上限裁、话题按规则拼；返回空串表示这个字段该平台不用填。 */
function taskCaption(task: PublishTask): ReturnType<typeof composeCaption> {
  return composeCaption(taskProfile(task), {title: task.title, tags: task.tags})
}

/** 抖音只有一个「作品描述」框：标题和话题都塞这里。 */
async function fillDouyin(page: PlaywrightPage, task: PublishTask) {
  await page.goto(PLATFORMS.douyin.createUrl, {waitUntil: 'domcontentloaded', timeout: 90000})
  await page.waitForTimeout(3000)
  const input = page.locator('input[type="file"][accept*="video"], input[type="file"]').first()
  await input.setInputFiles(task.file, {timeout: 60000})
  task.checkpoint = 'file_uploaded'
  task.step = '视频已提交，等待转码与编辑页'
  saveTask(task)
  await page.waitForURL(/content\/post|content\/upload/, {timeout: 180000}).catch(() => {})
  await page.waitForTimeout(6000)
  const caption = taskCaption(task)
  const text = caption.bodyField || caption.titleField
  const title = page.locator('div[contenteditable="true"], textarea').filter({hasNot: page.locator('[contenteditable="true"] [contenteditable="true"]')}).first()
  if (text) await title.click({timeout: 20000}).then(() => title.fill(text)).catch(async () => {
    await title.click().catch(() => {}); await page.keyboard.type(text, {delay: 20})
  })
  await attachCover(page, task)
  return page
}

/** 视频号：描述框＝正文，短标题另有字段；话题按档案塞进描述。 */
async function fillChannels(page: PlaywrightPage, task: PublishTask) {
  await page.goto(PLATFORMS.channels.createUrl, {waitUntil: 'domcontentloaded', timeout: 90000})
  await page.waitForTimeout(4000)
  await page.locator('input[type="file"]').first().setInputFiles(task.file, {timeout: 60000})
  task.checkpoint = 'file_uploaded'
  task.step = '视频已提交，等待上传完成'
  saveTask(task)
  await page.waitForTimeout(12000)
  const caption = taskCaption(task)
  const desc = page.locator('div[contenteditable="true"], textarea').first()
  const text = caption.bodyField || caption.titleField
  if (text) await desc.click({timeout: 20000}).then(() => desc.fill(text)).catch(() => {})
  await attachCover(page, task)
  return page
}

/** 小红书：标题 20 字硬上限，话题必须进正文——两处都按档案填。 */
async function fillXhs(page: PlaywrightPage, task: PublishTask) {
  await page.goto(PLATFORMS.xhs.createUrl, {waitUntil: 'domcontentloaded', timeout: 90000})
  await page.waitForTimeout(4000)
  // 小红书默认在「上传视频」页签
  await page.locator('input[type="file"][accept*="video"], input[type="file"]').first().setInputFiles(task.file, {timeout: 60000})
  task.checkpoint = 'file_uploaded'
  task.step = '视频已提交，等待上传完成'
  saveTask(task)
  await page.waitForTimeout(15000)
  const caption = taskCaption(task)
  const title = page.locator('input[placeholder*="标题"]').first()
  if (caption.titleField) await title.fill(caption.titleField, {timeout: 20000}).catch(() => {})
  const body = page.locator('div[contenteditable="true"], textarea').first()
  if (caption.bodyField) await body.click({timeout: 15000}).then(() => body.fill(caption.bodyField)).catch(() => {})
  await attachCover(page, task)
  return page
}

/**
 * 把任务带的成品封面塞进平台上传页。
 * 平台结构常改，所以按「直塞图片 input → 点封面按钮再塞」两级尝试；
 * 都不通就返回 manual，由 step 文案明说请手动选一次——绝不把「没传」写成「已传」。
 */
async function applyCover(page: PlaywrightPage, task: PublishTask): Promise<{state: 'uploaded' | 'manual' | 'none'; trace: string[]}> {
  const trace: string[] = []
  if (!task.cover) return {state: 'none', trace}
  const file = coverImage(task.cover)
  if (!file) return {state: 'manual', trace: ['封面不在素材库']}

  const imageInput = page.locator('input[type="file"][accept*="image"]')
  const direct = await imageInput.count().catch(() => 0)
  trace.push(`图片input=${direct}`)
  if (direct) {
    try {
      await imageInput.first().setInputFiles(file, {timeout: 25000})
      await page.waitForTimeout(2500)
      return {state: 'uploaded', trace}
    } catch { trace.push('直塞失败') }
  }

  for (const label of ['设置封面', '更换封面', '编辑封面', '上传封面', '选择封面', '修改封面']) {
    const button = page.getByText(label, {exact: false}).first()
    const count = await button.count().catch(() => 0)
    trace.push(`${label}=${count}`)
    if (!count) continue
    try {
      await button.click({timeout: 6000})
      await page.waitForTimeout(1800)
      const input = page.locator('input[type="file"][accept*="image"]').first()
      if (await input.count().catch(() => 0)) {
        await input.setInputFiles(file, {timeout: 25000})
        await page.waitForTimeout(2500)
        return {state: 'uploaded', trace}
      }
      trace.push(`${label}后仍无input`)
      await page.keyboard.press('Escape').catch(() => {})
    } catch { trace.push(`${label}点击失败`) }
  }
  return {state: 'manual', trace}
}

/** 封面没带上不影响视频本身发布，所以只更新 step 说明，不抛错中断任务。 */
async function attachCover(page: PlaywrightPage, task: PublishTask) {
  const {state, trace} = await applyCover(page, task)
  if (state === 'none') return
  const line = state === 'uploaded'
    ? `封面已自动带上：${task.cover}`
    : `封面没自动带上，请在发布页手动选一次：${task.cover}（自动尝试：${trace.join(' / ')}）`
  task.step = task.coverNote ? `${line}；${task.coverNote}` : line
  saveTask(task)
}

/** 抖音公开页的响应监听：评论列表与作品详情统计都是打开作品页时平台自己发的请求。 */
function wireDouyinMetrics(page: PlaywrightPage, sink: {comments: CommentRow[]; stats: {value: MonitoredWork['metrics']}}) {
  page.on('response', async response => {
    const url = response.url()
    try {
      if (/comment\/list|comment_list/.test(url)) {
        const json = await response.json() as {comments?: Array<{cid?: string;text?: string;digg_count?: number;reply_comment_total?: number;create_time?: number;user?: {nickname?: string}}>}
        for (const row of json.comments || []) if (row.text) sink.comments.push({
          id: String(row.cid || crypto.randomUUID()), text: row.text, user: row.user?.nickname || '',
          like: Number(row.digg_count) || 0, replies: Number(row.reply_comment_total) || 0,
          ...(row.create_time ? {at: new Date(row.create_time * 1000).toISOString()} : {}),
        })
      } else if (/aweme\/detail/.test(url)) {
        const json = await response.json() as {aweme_detail?: {statistics?: {play_count?: number;digg_count?: number;comment_count?: number;share_count?: number}}}
        const s = json.aweme_detail?.statistics
        if (s) sink.stats.value = {views: s.play_count ?? null, likes: s.digg_count ?? null, comments: s.comment_count ?? null, danmaku: null}
      }
    } catch { /* 非 JSON 或接口结构变化：保留已抓到的部分 */ }
  })
}

/** 跑一个任务。失败要留下原因，绝不把「页面打开了」当成发布成功。 */
export async function runTask(id: string) {
  const task = listTasks().find(t => t.id === id)
  if (!task) throw Object.assign(Error('任务不存在'), {status: 404})
  const account = listAccounts().find(a => a.id === task.accountId)
  if (!account) {
    const dead = saveTask({...task, status: 'failed', step: '账号已解绑', error: '账号已解绑'})
    archiveFromTask(dead)
    return dead
  }
  const spec = PLATFORMS[task.platform]
  const provider = PROVIDERS[task.platform]
  const filler = provider.fill
  task.status = 'running'; task.step = `打开 ${spec.name} 发布页`; task.error = undefined
  task.checkpoint = undefined // 重跑从零开始记检查点，避免拿旧进度误导超时补偿
  saveTask(task)
  let browser: Awaited<ReturnType<typeof cdp>> | undefined
  try {
    browser = await connectAccount(account)
    const context = browser.contexts()[0]
    if (!context) throw Error('这个账号的窗口没开着。请先在账号列表点「打开登录窗口」。')
    const page = await context.newPage()
    if (!filler) {
      // 手动平台（含海外四家）：打开发布页停在那里，封面与文案已在任务卡上备好。
      // 绝不把「页面打开了」当成发布成功，也不报失败——交给人确认。
      await page.goto(spec.createUrl, {waitUntil: 'domcontentloaded', timeout: 90000}).catch(() => {})
      await page.bringToFront().catch(() => {})
      task.status = 'ready'
      task.step = `已打开 ${spec.name} 发布页；封面/文案见任务卡，请在窗口里手动上传并点发布`
      task.autoSubmit = false
      archiveFromTask(saveTask(task), account.alias)
      return task
    }
    await filler(page, task)
    task.checkpoint = 'filled'
    // 现状：filler 只把视频/文案/封面填进平台编辑页，从不替人点「发布」。
    // 所以 autoSubmit 的语义是「自动填完即视为完成」，最终发布按钮仍要人在平台确认——文案必须说真话。
    task.status = task.autoSubmit ? 'done' : 'ready'
    task.step = task.autoSubmit
      ? '已自动填完（视频/文案/封面交给了平台），发布按钮请到平台确认是否已点'
      : '已填好，请在窗口里确认后点发布'
    if (!task.autoSubmit) await page.bringToFront().catch(() => {})
    archiveFromTask(saveTask(task), account.alias)
    return task
  } catch (error) {
    const failed = saveTask({...task, status: 'failed', step: '发布失败', error: error instanceof Error ? error.message : String(error)})
    archiveFromTask(failed)
    return failed
  } finally { await browser?.close().catch(() => {}) }
}

// —— 调度三件套（借鉴 AiToEarn 的 publish.scheduler）——
// 原子领取：runningIds 内存锁 + 状态先行写盘，谁先写到谁执行，并发 run-queue / 定时器不会重复领同一任务。
const runningIds = new Set<string>()

async function claim(task: PublishTask): Promise<boolean> {
  if (runningIds.has(task.id)) return false
  runningIds.add(task.id)
  const fresh = listTasks().find(t => t.id === task.id)
  if (!fresh || fresh.status !== 'queued') { runningIds.delete(task.id); return false }
  saveTask({...fresh, status: 'running', step: '已领取，开始执行', error: undefined, updatedAt: new Date().toISOString()})
  return true
}

/** 释放执行锁；runTask 结束（含失败）后必须调，否则该任务再也跑不了。 */
function release(taskId: string) { runningIds.delete(taskId) }

export async function runQueue(limit = 3) {
  const queued = listTasks().filter(t => t.status === 'queued').slice(0, limit)
  const done: PublishTask[] = []
  for (const task of queued) {
    if (!(await claim(task))) continue
    try { done.push(await runTask(task.id)) } finally { release(task.id) }
  }
  return done
}

let schedulerTimer: ReturnType<typeof setInterval> | undefined

/** 每 tick 的两件事：把到点的排期任务投入执行；把卡死的 running 任务做超时补偿。导出供测试直接驱动。 */
export async function tickScheduler() {
  const now = Date.now()
  const tasks = listTasks()
  // ① 定时发布：scheduledAt 已到点的 queued 任务自动进队列（一次最多 3 条，和手动 run-queue 同限）。
  const due = tasks.filter(t => t.status === 'queued' && t.scheduledAt && Date.parse(t.scheduledAt) <= now)
  for (const task of due.slice(0, 3)) {
    if (!(await claim(task))) continue
    void runTask(task.id).catch(() => {}).finally(() => release(task.id))
  }
  // ② 超时补偿：running 超 30 分钟多半是进程中断/窗口被关。绝不自动重跑（防重复上传触发风控），
  //    按检查点写清楚卡在哪一步，转 failed 交给人判断。
  const stuck = tasks.filter(t => t.status === 'running' && now - Date.parse(t.updatedAt) > 30 * 60_000)
  for (const task of stuck) {
    const where = task.checkpoint === 'filled' ? '文案与封面已填完（停在发布前）'
      : task.checkpoint === 'file_uploaded' ? '视频已上传到平台编辑页'
      : '尚未确认任何步骤完成'
    saveTask({
      ...task, status: 'failed', step: '执行超时，已停止',
      error: `执行超过 30 分钟没有结果（${where}）。请先去平台确认这条是否已经发出，再决定是否重跑；系统不会自动重发。`,
    })
  }
}

/** 启动定时调度器：30 秒一轮，unref 不阻塞进程退出；测试可用 stopMatrixScheduler 关掉。 */
export function startMatrixScheduler(intervalMs = 30_000) {
  if (schedulerTimer) return
  schedulerTimer = setInterval(() => { void tickScheduler().catch(() => {}) }, intervalMs)
  schedulerTimer.unref?.()
}

export function stopMatrixScheduler() {
  if (schedulerTimer) { clearInterval(schedulerTimer); schedulerTimer = undefined }
}

// —— 手机发布对账（借鉴 AiToEarn 的 UserHandoff，合规版）——
// 没有开放平台凭据时，snssdk1128 schema 拉不起 App；落地的是它的对账半环：
// 人把视频带去手机、在抖音官方 App 发布；桌面按任务标题到创作者中心作品列表里对账，命中即自动回链+存档。
// 拿到 client_key 后，把 generateShareSchema(snssdk1128://openplatform/share) 接到 startHandoff 即可升全环。

/** 启动手机发布：标记对账中，返回要带到手机的文案包（标题/文案/话题）。 */
export function startHandoff(id: string) {
  const task = listTasks().find(t => t.id === id)
  if (!task) throw Object.assign(Error('任务不存在'), {status: 404})
  if (task.platform !== 'douyin') throw Object.assign(Error('手机发布对账目前只支持抖音任务'), {status: 400})
  const next = saveTask({...task, handoff: {startedAt: new Date().toISOString()}, updatedAt: new Date().toISOString()})
  return {
    task: next,
    copy: {title: task.title, caption: task.caption || '', tags: task.tags.map(t => '#' + t).join(' ')},
  }
}

/** 对账一次：读创作者中心作品管理列表（已登录窗口），按标题匹配最新一条，命中自动回链。 */
export async function pollHandoff(id: string) {
  const task = listTasks().find(t => t.id === id)
  if (!task) throw Object.assign(Error('任务不存在'), {status: 404})
  if (task.status === 'done' && task.url) return {found: true, url: task.url, scanned: 0, task}
  const account = listAccounts().find(a => a.id === task.accountId)
  if (!account) throw Object.assign(Error('账号已解绑，无法对账'), {status: 400})
  let browser: Awaited<ReturnType<typeof cdp>> | undefined
  try {
    browser = await connectAccount(account)
    const context = browser.contexts()[0]
    if (!context) throw Error('这个账号的窗口没开着。请先打开登录窗口再对账。')
    const page = await context.newPage()
    type ListRow = {title: string; awemeId: string; createTime?: number}
    const rows: ListRow[] = []
    page.on('response', async response => {
      try {
        const url = response.url()
        if (!/aweme\/list|item\/list|content\/manage/i.test(url)) return
        const json = await response.json() as {aweme_list?: unknown[]; data?: {aweme_list?: unknown[]}; aweme_details?: unknown[]}
        const list = json.aweme_list || json.data?.aweme_list || json.aweme_details || []
        for (const raw of list) {
          const row = raw as {title?: string; aweme_id?: string|number; item_id?: string|number; create_time?: number; aweme_info?: {title?: string; aweme_id?: string|number; item_id?: string|number; create_time?: number}}
          const info = row.aweme_info || row
          const title = String(info.title || '')
          const awemeId = String(info.aweme_id || info.item_id || '')
          if (title && awemeId) rows.push({title, awemeId, createTime: info.create_time})
        }
      } catch { /* 非 JSON / 接口结构变化：保留已抓到的 */ }
    })
    await page.goto('https://creator.douyin.com/creator-micro/content/manage', {waitUntil: 'domcontentloaded', timeout: 90000}).catch(() => {})
    await page.waitForTimeout(6000)
    await page.mouse.wheel(0, 900).catch(() => {})
    await page.waitForTimeout(3000)
    await page.close()
    const wanted = task.title.replace(/\s+/g, '')
    const hit = rows
      .filter(r => r.title.replace(/\s+/g, '') === wanted)
      .sort((a, b) => (b.createTime || 0) - (a.createTime || 0))[0]
    if (!hit) {
      saveTask({...task, step: `手机发布对账中（读了 ${rows.length} 条作品，还没匹配到「${task.title.slice(0, 12)}」；手机发出去后点「再对一次」）`})
      return {found: false, scanned: rows.length, task: listTasks().find(t => t.id === id)}
    }
    const workUrl = `https://www.douyin.com/video/${hit.awemeId}`
    const confirmed = confirmTask(id, workUrl)
    return {found: true, url: workUrl, scanned: rows.length, task: confirmed.task}
  } finally { await browser?.close().catch(() => {}) }
}

export function cancelTask(id: string) {
  const task = listTasks().find(t => t.id === id)
  if (!task) throw Object.assign(Error('任务不存在'), {status: 404})
  const cancelled = saveTask({...task, status: 'cancelled', step: '已取消'})
  const account = listAccounts().find(a => a.id === task.accountId)
  archiveFromTask(cancelled, account?.alias)
  return cancelled
}

/** 删除任务记录：取消只是改状态，记录仍在磁盘上；确认后从这里真正清掉。 */
export function deleteTask(id: string) {
  const file = tasksFile(id)
  if (!fs.existsSync(file)) throw Object.assign(Error('任务记录不存在'), {status: 404})
  fs.unlinkSync(file)
  return {removed: id}
}

/** 批量清理已结束的任务：默认只动 done / failed / cancelled，排队与待确认的绝不碰。 */
export function purgeTasks(states: PublishTask['status'][] = ['done', 'failed', 'cancelled']) {
  const safe = states.filter((s): s is PublishTask['status'] => ['queued', 'running', 'ready', 'done', 'failed', 'cancelled'].includes(s))
  const doomed = listTasks().filter(t => safe.includes(t.status))
  for (const task of doomed) {
    try { fs.unlinkSync(tasksFile(task.id)) } catch { /* 已被清掉就跳过 */ }
  }
  return {removed: doomed.length, byStatus: safe}
}

// ——— 发布文案存档（借鉴 AiToEarn 的 publish-record）———
// 任务记录可以清，最终发出去的标题/话题/封面/链接永久留档，按内容文件聚合、按任务幂等更新。

export type ArchiveEntry = {
  id: string; taskId: string; contentFile: string
  platform: MatrixPlatform; accountId: string; alias: string
  title: string; caption?: string; tags: string[]
  cover?: string; url?: string
  status: PublishTask['status'] | 'planned'
  scheduledAt?: string; publishedAt?: string
  createdAt: string; updatedAt: string
}

const archiveFile = () => path.join(matrixRoot(), 'archive.json')

export function listArchive(): ArchiveEntry[] {
  return readJson<ArchiveEntry[]>(archiveFile(), [])
}

function saveArchive(rows: ArchiveEntry[]) { writeJson(archiveFile(), rows) }

/** 按任务幂等更新存档；status 到达 done 时补 publishedAt。存档不随任务清理删除。 */
function upsertArchive(entry: Partial<Omit<ArchiveEntry, 'id' | 'createdAt' | 'updatedAt'>> & {taskId: string; platform: MatrixPlatform; accountId: string}) {
  const rows = listArchive()
  const now = new Date().toISOString()
  const hit = rows.find(r => r.taskId === entry.taskId)
  if (hit) {
    Object.assign(hit, entry, {updatedAt: now})
    if (hit.status === 'done' && !hit.publishedAt) hit.publishedAt = now
    saveArchive(rows)
    return hit
  }
  const fresh: ArchiveEntry = {
    id: crypto.randomUUID(), taskId: entry.taskId,
    contentFile: entry.contentFile || '', platform: entry.platform, accountId: entry.accountId,
    alias: entry.alias || '', title: entry.title || '', caption: entry.caption,
    tags: entry.tags || [], cover: entry.cover, url: entry.url,
    status: entry.status || 'planned', scheduledAt: entry.scheduledAt,
    createdAt: now, updatedAt: now,
  }
  if (fresh.status === 'done') fresh.publishedAt = now
  rows.push(fresh)
  saveArchive(rows)
  return fresh
}

/** 任务状态变化时同步存档；账号已解绑时 alias 留空不炸。 */
function archiveFromTask(task: PublishTask, alias = '') {
  try {
    upsertArchive({
      taskId: task.id, contentFile: task.file, platform: task.platform, accountId: task.accountId, alias,
      title: task.title, caption: task.caption, tags: task.tags, cover: task.cover, url: task.url,
      status: task.status, scheduledAt: task.scheduledAt,
    })
  } catch { /* 存档失败不阻断发布主流程 */ }
}

/** 手动清一条存档（用户明确要删才删）。 */
export function removeArchive(id: string) {
  const rows = listArchive()
  const next = rows.filter(r => r.id !== id)
  if (next.length === rows.length) throw Object.assign(Error('存档不存在'), {status: 404})
  saveArchive(next)
  return {removed: id}
}

// ——— 作品与评论 ———

export function listWorks(): MonitoredWork[] {
  return readJson<MonitoredWork[]>(worksFile(), []).map(work => {
    // 旧文件没有 series：用最后一条读数补一个点，看板至少能显示当前水位。
    if (work.series?.length) return work
    const empty = work.metrics.views === null && work.metrics.likes === null && work.metrics.comments === null && work.metrics.danmaku === null
    return {...work, series: empty ? [] : [{at: work.syncedAt, ...work.metrics}]}
  }).sort((a, b) => b.syncedAt.localeCompare(a.syncedAt))
}
function saveWorks(rows: MonitoredWork[]) { writeJson(worksFile(), rows) }

export function trackWork(input: {accountId: string; url: string; title?: string; cover?: string}) {
  const account = listAccounts().find(a => a.id === input.accountId)
  if (!account) throw Object.assign(Error('账号不存在'), {status: 404})
  if (!/^https?:\/\//.test(input.url)) throw Object.assign(Error('请填写作品链接'), {status: 400})
  const works = listWorks(), exist = works.find(w => w.url === input.url)
  // 重复登记时补上后来才有的封面，不覆盖已有封面。
  if (exist) {
    if (input.cover && !exist.cover) {
      const next = {...exist, cover: input.cover}
      saveWorks(works.map(w => w.id === next.id ? next : w))
      return next
    }
    return exist
  }
  const work: MonitoredWork = {
    id: crypto.randomUUID(), accountId: account.id, platform: account.platform, url: input.url,
    title: (input.title || '').slice(0, 200), metrics: {views: null, likes: null, comments: null, danmaku: null},
    comments: [], cover: input.cover || undefined, syncedAt: new Date().toISOString(),
  }
  saveWorks([work, ...works])
  return work
}

export function untrackWork(id: string) { saveWorks(listWorks().filter(w => w.id !== id)); return {removed: id} }

/**
 * 发布完成回填链接：把 ready/done 的任务标记完成，并自动登记进监控台。
 * 国内四家是「填好由人点发布」，平台不会回调我们，所以链接由用户回填；
 * 一旦给了链接就自动 trackWork，不用再去监控台手动粘一遍。
 */
export function confirmTask(id: string, rawUrl: string) {
  const task = listTasks().find(t => t.id === id)
  if (!task) throw Object.assign(Error('任务不存在'), {status: 404})
  const link = String(rawUrl || '').trim()
  if (!/^https?:\/\//.test(link)) throw Object.assign(Error('请粘贴以 http(s):// 开头的作品链接'), {status: 400})
  const account = listAccounts().find(a => a.id === task.accountId)
  if (!account) throw Object.assign(Error('账号已解绑，无法登记作品'), {status: 400})
  const updated = saveTask({...task, status: 'done', step: '已确认发布', url: link, error: undefined})
  archiveFromTask(updated, account.alias)
  // 幂等：trackWork 内部按 url 去重，重复回填不会建两条。
  const work = trackWork({accountId: account.id, url: link, title: task.title, cover: task.cover})
  return {task: updated, work}
}

/**
 * 同步自己作品的公开数据与评论。
 * 只处理本账号发布的作品（登录态可见范围），不采集他人内容。
 */
export async function syncWork(id: string) {
  const works = listWorks(), work = works.find(w => w.id === id)
  if (!work) throw Object.assign(Error('作品不存在'), {status: 404})
  const account = listAccounts().find(a => a.id === work.accountId)
  if (!account) throw Object.assign(Error('账号已解绑，无法同步'), {status: 400})
  let browser: Awaited<ReturnType<typeof cdp>> | undefined
  // 采集槽：Provider 的 wire 往里写评论与统计，try 结束后从槽里取最终值。
  const sink = {comments: [] as CommentRow[], stats: {value: {...work.metrics} as MonitoredWork['metrics']}}
  try {
    browser = await connectAccount(account)
    const context = browser.contexts()[0]
    if (!context) throw Error('这个账号的窗口没开着，请先打开登录窗口')
    const page = await context.newPage()
    // 平台数据监听由 Provider 提供（抖音：评论列表 + 作品详情统计）。
    PROVIDERS[account.platform].wire?.(page, sink)
    await page.goto(work.url, {waitUntil: 'domcontentloaded', timeout: 90000})
    await page.waitForTimeout(6000)
    // 展开评论区，让列表接口自己触发
    await page.mouse.wheel(0, 1200).catch(() => {})
    await page.waitForTimeout(4000)
    await page.close()
  } finally { await browser?.close().catch(() => {}) }
  // 后台采集器补收藏/分享/投币/弹幕：只读创作后台列表，读不到就保留详情接口已有的字段。
  const stats = sink.stats.value
  let extended = stats
  try {
    const backend = await collectWorkMetrics(account, work)
    if (backend) extended = {
      views: backend.views ?? stats.views, likes: backend.likes ?? stats.likes,
      comments: backend.comments ?? stats.comments, danmaku: backend.danmaku ?? stats.danmaku,
      favorites: backend.favorites ?? stats.favorites, shares: backend.shares ?? stats.shares, coins: backend.coins ?? stats.coins,
    }
  } catch { /* 后台没开或改版：详情接口的四项读数仍然有效 */ }
  const now = new Date().toISOString()
  const merged = [...sink.comments, ...work.comments.filter(c => !sink.comments.some(n => n.id === c.id))]
  // 同一天只留最后一次读数：一天同步五次不该把曲线拉成竖线。
  const byDay = new Map<string, MetricSnapshot>()
  for (const row of [...(work.series || []), {at: now, ...extended}]) byDay.set(row.at.slice(0, 10), row)
  const series = [...byDay.values()].sort((a, b) => a.at.localeCompare(b.at)).slice(-180)
  const next: MonitoredWork = {...work, metrics: extended, comments: merged.slice(0, 500), series, syncedAt: now}
  saveWorks(listWorks().map(w => w.id === id ? next : w))
  return next
}

// ——— 发布草稿（刷新页面不丢）+ 封面风格记忆 ———

export type MatrixDraft = {file: string; title: string; tags: string; scheduledAt: string; autoSubmit: boolean; coverBrief: string}
const EMPTY_DRAFT: MatrixDraft = {file: '', title: '', tags: '', scheduledAt: '', autoSubmit: false, coverBrief: ''}
const draftFile = () => path.join(matrixRoot(), 'draft.json')

export function readDraft(): MatrixDraft {
  const saved = readJson<Partial<MatrixDraft>>(draftFile(), {})
  return {...EMPTY_DRAFT, ...saved, autoSubmit: saved.autoSubmit === true}
}

export function saveDraft(input: Partial<MatrixDraft>): MatrixDraft {
  const next = readDraft()
  if (typeof input.file === 'string') next.file = input.file.slice(0, 600)
  if (typeof input.title === 'string') next.title = input.title.slice(0, 200)
  if (typeof input.tags === 'string') next.tags = input.tags.slice(0, 300)
  if (typeof input.scheduledAt === 'string') next.scheduledAt = input.scheduledAt.slice(0, 40)
  if (typeof input.coverBrief === 'string') next.coverBrief = input.coverBrief.slice(0, 20000)
  if (typeof input.autoSubmit === 'boolean') next.autoSubmit = input.autoSubmit
  writeJson(draftFile(), next)
  return next
}

export type CoverSample = {
  file: string; width: number; height: number; ratio: string
  colors: string[]; brightness: number; contrast: number; zones: [number, number, number]
  summary: string
}
export type CoverMemory = {spec: string; source: ''|'agent'|'local'; samples: CoverSample[]; updatedAt: string; agent?: string}

const coverFile = () => path.join(matrixRoot(), 'cover-memory.json')
const coverDir = () => path.join(matrixRoot(), 'covers')
/** 每个平台一套封面比例 + 文案规则；平台改版只改这个文件，不动代码。 */
const profileFile = () => path.join(matrixRoot(), 'platform-profiles.json')
/** 固定本地资源目录：真人参考照落在这里，清单记路径与用途，换对话/换作品都能复用。 */
const personDir = () => path.join(matrixRoot(), 'cover-assets', 'person')
const personManifestFile = () => path.join(matrixRoot(), 'cover-assets', 'person-refs.json')

export type PersonRef = {file: string; path: string; purpose: string; addedAt: string}
export type PersonManifest = {refs: PersonRef[]; updatedAt: string}

export function personManifest(): PersonManifest {
  return readJson<PersonManifest>(personManifestFile(), {refs: [], updatedAt: ''})
}

/** 接受 dataURL 落盘，最多 2 张（正面 + 侧/全身），返回清单。旧的先清掉，避免越攒越多。 */
export function savePersonRefs(images: string[]): PersonManifest {
  const valid = (Array.isArray(images) ? images : []).slice(0, 2)
    .map(value => /^data:image\/(png|jpe?g|webp);base64,(.+)$/i.exec(String(value)))
    .filter((match): match is RegExpExecArray => !!match)
  if (!valid.length) throw Object.assign(Error('人物参考照要 PNG / JPG / WEBP 格式'), {status: 400})
  fs.mkdirSync(personDir(), {recursive: true})
  for (const old of personManifest().refs) {
    try { fs.unlinkSync(path.join(personDir(), path.basename(old.file))) } catch { /* 已删就跳过 */ }
  }
  const stamp = Date.now()
  const refs: PersonRef[] = valid.map((match, index) => {
    const ext = match[1].toLowerCase().replace('jpeg', 'jpg')
    const file = `person-${stamp}-${index}.${ext}`
    const full = path.join(personDir(), file)
    fs.writeFileSync(full, Buffer.from(match[2], 'base64'))
    return {
      file,
      path: full,
      purpose: index === 0 ? '正面身份参考：脸型、五官比例、发型、年龄感与自然肤色' : '第二角度（侧面/全身）：发型轮廓、身形与服装参照',
      addedAt: new Date().toISOString(),
    }
  })
  const next: PersonManifest = {refs, updatedAt: new Date().toISOString()}
  writeJson(personManifestFile(), next)
  return next
}

export function clearPersonRefs(): PersonManifest {
  for (const old of personManifest().refs) {
    try { fs.unlinkSync(path.join(personDir(), path.basename(old.file))) } catch { /* 已删就跳过 */ }
  }
  const next: PersonManifest = {refs: [], updatedAt: new Date().toISOString()}
  writeJson(personManifestFile(), next)
  return next
}

/** 只读 person 目录里的图，防路径穿越。 */
export function personRefImage(name: string): string | undefined {
  const safe = path.basename(name)
  const file = path.join(personDir(), safe)
  return fs.existsSync(file) && fs.statSync(file).isFile() ? file : undefined
}

export function coverMemory(): CoverMemory {
  return readJson<CoverMemory>(coverFile(), {spec: '', source: '', samples: [], updatedAt: ''})
}

/** 平台档案：默认值做骨架，用户改过的字段盖上去；首次调用顺手落盘，方便手改。 */
export function listProfiles(): PlatformProfile[] {
  const rows = readProfiles(readJson<unknown>(profileFile(), []))
  if (!fs.existsSync(profileFile())) writeJson(profileFile(), rows)
  return rows
}

export function updateProfiles(patches: Partial<PlatformProfile>[]): PlatformProfile[] {
  const next = saveProfiles(listProfiles(), Array.isArray(patches) ? patches : [])
  writeJson(profileFile(), next)
  return next
}

/** 本次选中账号涉及哪几个平台、每个平台要什么比例——界面和出图都用这一份。 */
export function planRatios(platformIds: string[]) {
  return ratiosFor(listProfiles(), platformIds)
}

/**
 * 把 3–10 张历史封面缩成 8×8 像素读客观特征（主色、明度、三分区亮度）。
 * 用 ffmpeg 而不是引入图像库：这套环境里 ffmpeg 一定在。
 */
async function ffmpegRaw(args: string[]): Promise<Buffer> {
  const {ffmpegPath} = await import('./general/runtime-tools.ts')
  const bin = ffmpegPath()
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true})
    const out: Buffer[] = [], err: Buffer[] = []
    child.stdout.on('data', (chunk: Buffer) => out.push(chunk))
    child.stderr.on('data', (chunk: Buffer) => err.push(chunk))
    child.on('error', reject)
    child.on('close', code => code === 0 ? resolve(Buffer.concat(out))
      : reject(Error(Buffer.concat(err).toString('utf8').trim().slice(-260) || `ffmpeg 退出码 ${code}`)))
  })
}

async function probeSize(file: string): Promise<[number, number]> {
  const {ffprobePath} = await import('./general/runtime-tools.ts')
  const text = await new Promise<string>((resolve, reject) => {
    const child = spawn(ffprobePath(), ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', file], {stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true})
    const out: Buffer[] = [], err: Buffer[] = []
    child.stdout.on('data', (chunk: Buffer) => out.push(chunk))
    child.stderr.on('data', (chunk: Buffer) => err.push(chunk))
    child.on('error', reject)
    child.on('close', code => code === 0 ? resolve(Buffer.concat(out).toString('utf8').trim()) : reject(Error(`读不到图片尺寸：${Buffer.concat(err).toString('utf8').trim().slice(-160)}`)))
  })
  const [width, height] = text.split('x').map(Number)
  if (!width || !height) throw Error('读不到图片尺寸')
  return [width, height]
}

const hex = (r: number, g: number, b: number) => '#' + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('')

function readGrid(pixels: Buffer, width: number, height: number, meta: {file: string; width: number; height: number}): CoverSample {
  const lums: number[] = [], buckets = new Map<string, {n: number; r: number; g: number; b: number}>()
  for (let i = 0; i < width * height; i++) {
    const r = pixels[i * 3] ?? 0, g = pixels[i * 3 + 1] ?? 0, b = pixels[i * 3 + 2] ?? 0
    lums.push(0.2126 * r + 0.7152 * g + 0.0722 * b)
    const key = `${r >> 5}-${g >> 5}-${b >> 5}`
    const hit = buckets.get(key) || {n: 0, r: 0, g: 0, b: 0}
    hit.n++; hit.r += r; hit.g += g; hit.b += b
    buckets.set(key, hit)
  }
  const mean = lums.reduce((n, v) => n + v, 0) / Math.max(1, lums.length)
  const variance = lums.reduce((n, v) => n + (v - mean) ** 2, 0) / Math.max(1, lums.length)
  const zone = (from: number, to: number) => {
    const slice = lums.slice(Math.floor(width * height * from), Math.floor(width * height * to))
    return slice.reduce((n, v) => n + v, 0) / Math.max(1, slice.length)
  }
  const colors = [...buckets.values()].sort((a, b) => b.n - a.n).slice(0, 3).map(c => hex(c.r / c.n, c.g / c.n, c.b / c.n))
  const ratio = meta.height >= meta.width ? `竖版 ${(meta.width / meta.height).toFixed(2)}:1` : `横版 ${(meta.width / meta.height).toFixed(2)}:1`
  const contrast = Math.sqrt(variance)
  const zones: [number, number, number] = [zone(0, 1 / 3), zone(1 / 3, 2 / 3), zone(2 / 3, 1)]
  const summary = `${ratio} · 主色 ${colors.join(' / ')} · 平均亮度 ${Math.round(mean)}/255 · 对比度 ${contrast > 60 ? '高' : contrast > 35 ? '中' : '低'} · 上/中/下三段亮度 ${zones.map(v => Math.round(v)).join('/')}`
  return {file: path.basename(meta.file), width: meta.width, height: meta.height, ratio, colors, brightness: Math.round(mean), contrast: Math.round(contrast), zones: zones.map(v => Math.round(v)) as [number, number, number], summary}
}

/** 接受前端传来的 dataURL，落盘后提取特征；失败的那张跳过，不让一张坏图毁掉整次分析。 */
export async function analyzeCovers(samples: string[]): Promise<CoverMemory> {
  if (!Array.isArray(samples) || samples.length < 1) throw Object.assign(Error('请先选择历史封面图片'), {status: 400})
  if (samples.length > 10) throw Object.assign(Error('一次最多分析 10 张，够提炼规律了'), {status: 400})
  fs.mkdirSync(coverDir(), {recursive: true})
  const rows: CoverSample[] = []
  const stamp = Date.now()
  for (const [index, dataUrl] of samples.entries()) {
    const match = /^data:image\/(png|jpe?g|webp|gif);base64,(.+)$/i.exec(dataUrl)
    if (!match) continue
    const ext = match[1].toLowerCase().replace('jpeg', 'jpg')
    const file = path.join(coverDir(), `${stamp}-${index}.${ext}`)
    try {
      fs.writeFileSync(file, Buffer.from(match[2], 'base64'))
      const [width, height] = await probeSize(file)
      const pixels = await ffmpegRaw(['-v', 'error', '-i', file, '-frames:v', '1', '-vf', 'scale=8:8:flags=area', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
      if (pixels.length < 8 * 8 * 3) throw Error('缩略图数据不完整')
      rows.push(readGrid(pixels, 8, 8, {file, width, height}))
    } catch { /* 单张失败跳过；下面统一判断是否还剩够用的样本 */ }
  }
  if (!rows.length) throw Object.assign(Error('这几张图都读不出来，换 PNG / JPG 再试'), {status: 400})

  const localSpec = [
    `样本 ${rows.length} 张`,
    `比例：${[...new Set(rows.map(r => r.ratio))].join('、')}`,
    `常用主色：${[...new Set(rows.flatMap(r => r.colors))].slice(0, 6).join(' ')}`,
    `平均亮度：${Math.round(rows.reduce((n, r) => n + r.brightness, 0) / rows.length)}/255`,
    `对比度：${Math.round(rows.reduce((n, r) => n + r.contrast, 0) / rows.length)}（${rows.filter(r => r.contrast > 60).length}/${rows.length} 张高对比）`,
    `下三分之一偏亮：${rows.filter(r => r.zones[2] > r.zones[0] + 12).length}/${rows.length} 张（字幕压在下方的迹象）`,
  ].join('\n')

  let spec = localSpec, source: CoverMemory['source'] = 'local', agent = ''
  try {
    const answer = await askCreatorAgent(
      COVER_ANALYSIS_SYSTEM,
      coverAnalysisPrompt(rows.map(r => r.summary), rows.map(r => path.join(coverDir(), r.file)), personManifest().refs.length),
    )
    if (answer.text.trim()) { spec = answer.text.trim(); source = 'agent'; agent = answer.agent }
  } catch { /* Agent 不可用时返回本地统计，界面仍可看、可编辑 */ }

  const memory: CoverMemory = {spec, source, samples: rows, updatedAt: new Date().toISOString(), ...(agent ? {agent} : {})}
  writeJson(coverFile(), memory)
  return memory
}

/**
 * 第三步：把「历史规范 + 本次标题/重点词 + 真人参考照路径」封装成一次封面任务。
 * 规则全在 cover-playbook 里；用户能改的只有标题和重点词，是否真人出镜由有没有参考照决定。
 */
export async function generateCoverPrompt(input: {topic?: string; platform?: string; title?: string; keywords?: string}) {
  const spec = coverMemory().spec
  const refs = personManifest().refs
  const platformName = (platform: string) => ({douyin:'抖音',channels:'视频号',xhs:'小红书',kuaishou:'快手',bilibili:'B站',baijiahao:'百家号',toutiao:'头条号',fanqie:'番茄视频',tiktok:'TikTok',youtube:'YouTube',instagram:'Instagram',x:'X (Twitter)'} as Record<string, string>)[platform] || platform || '抖音'
  const answer = await askCreatorAgent(
    COVER_DIRECTOR_SYSTEM,
    coverDirectorPrompt({
      topic: String(input.topic || ''),
      platform: platformName(String(input.platform || '')),
      title: String(input.title || ''),
      keywords: String(input.keywords || ''),
      spec,
      personRefs: refs.map(ref => ({path: ref.path, purpose: ref.purpose})),
    }),
  )
  if (!answer.text.trim()) throw Error('Agent 没有返回提示词，请重试')
  return {text: answer.text.trim(), agent: answer.agent, personRefs: refs.length, hasSpec: !!spec}
}

export function saveCoverSpec(spec: string): CoverMemory {
  const memory = coverMemory()
  const next: CoverMemory = {...memory, spec: String(spec || '').slice(0, 20000), source: memory.source || 'local', updatedAt: new Date().toISOString()}
  writeJson(coverFile(), next)
  return next
}

/** 只读 covers 目录里的图，防止路径穿越。 */
export function coverImage(name: string): string | undefined {
  const safe = path.basename(name)
  const file = path.join(coverDir(), safe)
  return fs.existsSync(file) && fs.statSync(file).isFile() ? file : undefined
}

/** 封面素材库：Codex / GPT-Image 出图后上传的成品，与应用内 Seedream 生成图统一落在这里。 */
export type CoverAsset = {file: string; bytes: number; width?: number; height?: number; ratio: string; source: string; createdAt: string}
type CoverAssetMeta = {width?: number; height?: number; source?: string; createdAt?: string}

const coverLibraryFile = () => path.join(coverDir(), 'library.json')

function readCoverMeta(): Record<string, CoverAssetMeta> {
  return readJson<Record<string, CoverAssetMeta>>(coverLibraryFile(), {})
}

function coverRatio(width?: number, height?: number): string {
  if (!width || !height) return ''
  const ratio = width / height
  // 必须包含 6:7（视频号）和 16:10（B站），否则这两个平台的封面会被认成「未知比例」。
  const table: [string, number][] = [
    ['9:16', 9 / 16], ['3:4', 3 / 4], ['6:7', 6 / 7], ['1:1', 1],
    ['4:3', 4 / 3], ['16:10', 16 / 10], ['16:9', 16 / 9],
  ]
  let best: [string, number] = table[0]
  let diff = Math.abs(ratio - best[1])
  for (const item of table.slice(1)) {
    const next = Math.abs(ratio - item[1])
    if (next < diff) { best = item; diff = next }
  }
  return diff < 0.06 ? best[0] : `${width}×${height}`
}

export function listCoverAssets(): CoverAsset[] {
  const dir = coverDir()
  if (!fs.existsSync(dir)) return []
  const meta = readCoverMeta()
  return fs.readdirSync(dir)
    .filter(name => /\.(png|jpe?g|webp)$/i.test(name))
    .flatMap(name => {
      try {
        const stat = fs.statSync(path.join(dir, name))
        const item = meta[name] || {}
        return [{
          file: name, bytes: stat.size, width: item.width, height: item.height,
          ratio: coverRatio(item.width, item.height),
          source: item.source || (/^generated-/.test(name) ? 'seedream' : 'upload'),
          createdAt: item.createdAt || stat.mtime.toISOString(),
        }]
      } catch { return [] }
    })
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}

/** 上传成品封面（Codex / GPT-Image / 手工修图），落盘并写清单。最多 12 张，单张 20MB。 */
export function saveCoverUploads(images: {data?: string; width?: number; height?: number; source?: string}[]): {added: CoverAsset[]; assets: CoverAsset[]} {
  const allowed: Record<string, string> = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}
  const dir = coverDir()
  fs.mkdirSync(dir, {recursive: true})
  const meta = readCoverMeta()
  const stamp = Date.now()
  const added: CoverAsset[] = []
  ;(Array.isArray(images) ? images : []).slice(0, 12).forEach((row, index) => {
    const match = /^data:(image\/[a-z]+);base64,(.+)$/i.exec(String(row?.data || ''))
    if (!match) return
    const ext = allowed[match[1].toLowerCase()]
    if (!ext) return
    const buffer = Buffer.from(match[2], 'base64')
    if (buffer.length < 1024 || buffer.length > 20 * 1024 * 1024) return
    const file = `upload-${stamp}-${index}.${ext}`
    fs.writeFileSync(path.join(dir, file), buffer)
    const item: CoverAssetMeta = {
      width: Number(row.width) || undefined, height: Number(row.height) || undefined,
      source: String(row.source || 'codex').slice(0, 20), createdAt: new Date().toISOString(),
    }
    meta[file] = item
    added.push({file, bytes: buffer.length, width: item.width, height: item.height, ratio: coverRatio(item.width, item.height), source: item.source || 'codex', createdAt: item.createdAt || ''})
  })
  writeJson(coverLibraryFile(), meta)
  return {added, assets: listCoverAssets()}
}

/** Codex 出图后直接落库：按文件复制，不经 base64，省内存也省一次编解码。 */
export function saveGeneratedCovers(files: {path: string; width?: number; height?: number}[], source = 'codex'): {added: CoverAsset[]; assets: CoverAsset[]} {
  const dir = coverDir()
  fs.mkdirSync(dir, {recursive: true})
  const meta = readCoverMeta()
  const added: CoverAsset[] = []
  const stamp = Date.now()
  files.forEach((row, index) => {
    const ext = (path.extname(row.path) || '.png').toLowerCase().replace('.jpeg', '.jpg')
    const file = `codex-${stamp}-${index}${ext}`
    const target = path.join(dir, file)
    try {
      fs.copyFileSync(row.path, target)
      const bytes = fs.statSync(target).size
      if (bytes < 1024) { fs.unlinkSync(target); return }
      const item: CoverAssetMeta = {width: row.width, height: row.height, source, createdAt: new Date().toISOString()}
      meta[file] = item
      added.push({file, bytes, width: item.width, height: item.height, ratio: coverRatio(item.width, item.height), source, createdAt: item.createdAt || ''})
    } catch { /* 单张失败不影响其他 */ }
  })
  writeJson(coverLibraryFile(), meta)
  return {added, assets: listCoverAssets()}
}

/** 把前端传来的引用标识解析成真实路径，避免前端能传任意路径读盘。 */
export function resolveCoverRefs(ids: string[]): string[] {
  const out: string[] = []
  for (const raw of (Array.isArray(ids) ? ids : []).slice(0, 6)) {
    const id = String(raw)
    if (id === 'person') {
      for (const ref of personManifest().refs.slice(0, 2)) {
        try { if (fs.existsSync(ref.path)) out.push(ref.path) } catch { /* 跳过坏文件 */ }
      }
      continue
    }
    if (id.startsWith('library:')) {
      const file = coverImage(id.slice('library:'.length))
      if (file) out.push(file)
    }
  }
  return out.slice(0, 4)
}

export function deleteCoverAsset(name: string): CoverAsset[] {
  const file = coverImage(name)
  if (!file) throw Object.assign(Error('封面不存在'), {status: 404})
  try { fs.unlinkSync(file) } catch { /* 已删就跳过 */ }
  const meta = readCoverMeta()
  delete meta[path.basename(name)]
  writeJson(coverLibraryFile(), meta)
  return listCoverAssets()
}

/**
 * 系统自检：只看真实状态（端口、登录态、内核、采集器、数据新鲜度），
 * 每一项都能点进对应功能区处理；不显示任何凭空写出来的「运行中」。
 */
export async function systemHealth() {
  const accounts = listAccounts(), works = listWorks(), tasks = listTasks()
  const windows = await Promise.all(accounts.map(async account => ({
    id: account.id, alias: account.alias, platform: account.platform, port: account.port,
    open: await portOpen(account.port), loggedIn: !!account.lastCheck?.loggedIn,
    lastCheck: account.lastCheck?.at || '', error: account.lastCheck?.error || '',
  })))
  let browserTarget: {kind: string; path: string} | undefined
  try { browserTarget = resolveBrowser('auto') } catch { /* 没装内核 */ }
  const browserOk = !!browserTarget
  const browserName = browserTarget?.kind === 'ego' ? 'Ego Lite（默认专用浏览器）'
    : browserTarget?.kind === 'chrome' ? 'Chrome（Ego 未装的兜底）'
    : browserTarget?.kind === 'edge' ? 'Edge（Ego 未装的兜底）' : ''
  let codexOk = false, ffmpegOk = false
  try { codexOk = !!findCodexCli() } catch { /* 没装 Codex */ }
  try {
    const {ffmpegPath} = await import('./general/runtime-tools.ts')
    ffmpegOk = fs.existsSync(ffmpegPath())
  } catch { /* 没有 ffmpeg，风格分析会降级 */ }
  const followers = readFollowers()
  const followerDates = Object.values(followers).flatMap(rows => Object.values(rows).map(row => row.collectedAt)).sort()
  const lastSync = works.map(w => w.syncedAt).sort().at(-1) || ''
  const staleDays = lastSync ? Math.floor((Date.now() - Date.parse(lastSync)) / 864e5) : -1
  const checks = [
    {id: 'browser', label: '浏览器内核', status: browserOk ? 'ok' : 'error', detail: browserOk ? `正在用 ${browserName}，路径 ${browserTarget!.path}` : '没找到内核：到「系统设置 → 浏览器」安装 Ego Lite / Chrome'},
    {id: 'windows', label: '账号窗口', status: !accounts.length ? 'warn' : windows.every(w => w.open) ? 'ok' : 'warn', detail: !accounts.length ? '还没有账号' : `${windows.filter(w => w.open).length}/${accounts.length} 个窗口在线，没开的点账号行右侧的打开按钮`},
    {id: 'login', label: '登录态', status: !accounts.length ? 'warn' : accounts.some(a => a.lastCheck?.loggedIn) ? 'ok' : 'warn', detail: `已确认登录 ${accounts.filter(a => a.lastCheck?.loggedIn).length}/${accounts.length}，未登录的先扫码`},
    {id: 'collectors', label: '采集器', status: 'ok', detail: `粉丝：${Object.keys(followerCollectors).join('、') || '无'}；作品指标：${Object.keys(metricCollectors).join('、') || '无'}`},
    {id: 'followers', label: '粉丝采集', status: followerDates.length ? 'ok' : 'warn', detail: followerDates.length ? `最近一次 ${new Date(followerDates.at(-1)!).toLocaleString('zh-CN')}` : '还没采过粉丝，点顶部「采粉丝」'},
    {id: 'works', label: '作品数据', status: staleDays < 0 ? 'warn' : staleDays <= 3 ? 'ok' : 'warn', detail: staleDays < 0 ? '还没有作品快照' : `最近同步 ${new Date(lastSync).toLocaleString('zh-CN')}（${staleDays} 天前）`},
    {id: 'codex', label: 'Codex 出图', status: codexOk ? 'ok' : 'warn', detail: codexOk ? '本机 Codex 可用' : '没找到本机 Codex，不影响其他功能'},
    {id: 'ffmpeg', label: '封面风格分析', status: ffmpegOk ? 'ok' : 'warn', detail: ffmpegOk ? 'ffmpeg 可用' : '没找到 ffmpeg，风格记忆会降级为手工规范'},
  ]
  return {
    status: checks.some(c => c.status === 'error') ? 'error' : checks.some(c => c.status === 'warn') ? 'warn' : 'ok',
    checks, windows,
    totals: {accounts: accounts.length, loggedIn: accounts.filter(a => a.lastCheck?.loggedIn).length, works: works.length, tasks: tasks.length, ready: tasks.filter(t => t.status === 'ready').length, queued: tasks.filter(t => t.status === 'queued').length},
    updatedAt: new Date().toISOString(),
  }
}

export function matrixSummary() {
  const accounts = listAccounts(), tasks = listTasks(), works = listWorks()
  const loggedIn = accounts.filter(a => a.lastCheck?.loggedIn).length
  return {
    accounts: accounts.length, loggedIn,
    queued: tasks.filter(t => t.status === 'queued').length,
    ready: tasks.filter(t => t.status === 'ready').length,
    done: tasks.filter(t => t.status === 'done').length,
    failed: tasks.filter(t => t.status === 'failed').length,
    works: works.length,
    comments: works.reduce((n, w) => n + w.comments.length, 0),
    views: works.reduce((n, w) => n + (w.metrics.views || 0), 0),
  }
}

/** /api/matrix/* —— 与 creator-projects 同一套 sendJson 契约。 */
export async function matrixApi(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`)
  const parts = url.pathname.replace(/^\/api\/matrix\/?/, '').split('/').filter(Boolean)
  const [head, id, action] = parts
  try {
    if (req.method === 'GET' && head === 'platforms') return sendJson(res, 200, {ok: true, result: Object.entries(PLATFORMS).map(([key, spec]) => ({id: key, ...spec}))})
    if (req.method === 'GET' && head === 'summary') return sendJson(res, 200, {ok: true, result: matrixSummary()})
    if (head === 'profiles') {
      if (req.method === 'GET') return sendJson(res, 200, {ok: true, result: listProfiles()})
      if (req.method === 'PUT' || req.method === 'POST') {
        const input = await bodyJson(req) as {profiles?: Partial<PlatformProfile>[]} | Partial<PlatformProfile>[]
        const patches = Array.isArray(input) ? input : input.profiles || []
        return sendJson(res, 200, {ok: true, result: updateProfiles(patches)})
      }
    }

    if (head === 'draft') {
      if (req.method === 'GET') return sendJson(res, 200, {ok: true, result: readDraft()})
      if (req.method === 'PUT' || req.method === 'POST') return sendJson(res, 200, {ok: true, result: saveDraft(await bodyJson(req) as Partial<MatrixDraft>)})
    }
    if (head === 'cover') {
      if (req.method === 'GET' && !id) return sendJson(res, 200, {ok: true, result: coverMemory()})
      if (req.method === 'POST' && id === 'headline') {
        const input = await bodyJson(req) as {title?: string; tags?: string[]; desc?: string; platform?: string}
        return sendJson(res, 200, {ok: true, result: await extractHeadline({
          title: String(input.title || ''), tags: Array.isArray(input.tags) ? input.tags.map(String).slice(0, 12) : [],
          desc: String(input.desc || ''), platform: String(input.platform || ''),
        })})
      }
      if (req.method === 'POST' && id === 'analyze') {
        const input = await bodyJson(req, 64 * 1024 * 1024) as {samples?: string[]}
        return sendJson(res, 200, {ok: true, result: await analyzeCovers(input.samples || [])})
      }
      if ((req.method === 'PUT' || req.method === 'POST') && id === 'spec') {
        const input = await bodyJson(req) as {spec?: string}
        return sendJson(res, 200, {ok: true, result: saveCoverSpec(String(input.spec || ''))})
      }
      // 固定出镜人物参考照：落盘在 cover-assets/person，清单记路径与用途。
      if (req.method === 'GET' && id === 'person' && !action) return sendJson(res, 200, {ok: true, result: personManifest()})
      if (req.method === 'POST' && id === 'person' && !action) {
        const input = await bodyJson(req, 32 * 1024 * 1024) as {images?: string[]}
        return sendJson(res, 200, {ok: true, result: savePersonRefs(input.images || [])})
      }
      if (req.method === 'DELETE' && id === 'person' && !action) return sendJson(res, 200, {ok: true, result: clearPersonRefs()})
      if (req.method === 'GET' && id === 'person' && action === 'file' && parts[3]) {
        const file = personRefImage(parts[3])
        if (!file) return sendJson(res, 404, {ok: false, error: '参考照不存在'})
        res.writeHead(200, {'Content-Type': file.endsWith('.png') ? 'image/png' : file.endsWith('.webp') ? 'image/webp' : 'image/jpeg', 'Cache-Control': 'public, max-age=31536000'})
        res.end(fs.readFileSync(file))
        return
      }
      // 按规范生成提示词：规则埋在 cover-playbook，用户只给标题和重点词。
      if (req.method === 'POST' && id === 'prompt' && !action) {
        const input = await bodyJson(req) as {topic?: string; platform?: string; title?: string; keywords?: string}
        return sendJson(res, 200, {ok: true, result: await generateCoverPrompt(input)})
      }
      // 一键生图：拿豆包方舟（Seedream）密钥直接出图，不再经过 Agent 产出文字。
      // 提示词为空时先用 cover-playbook 按规范写一段，再生成 9:16 / 3:4 / 4:3 三张原生图。
      if (req.method === 'POST' && id === 'generate' && !action) {
        const input = await bodyJson(req) as {brief?: string; topic?: string; platform?: string; title?: string; keywords?: string; ratios?: string[]}
        let brief = String(input.brief || '').trim()
        if (!brief) brief = (await generateCoverPrompt(input)).text
        const refs = personManifest().refs.map(ref => ref.path).filter(Boolean)
        const result = await generateCoverImages(brief, refs, coverDir(), {}, input.ratios)
        return sendJson(res, 200, {ok: true, result: {...result, prompt: brief}})
      }
      // 用本机 Codex 的 image_gen 出图：走 ChatGPT 账号额度，不需要 OpenAI API Key，支持参考图。
      // 生图真的发生在 codex.exe 进程里，落盘到 $CODEX_HOME/generated_images/<thread_id>/，
      // 这里只负责把产物复制进封面素材库，所以「出图」和「入库」是两步、可分别重试。
      if (req.method === 'GET' && id === 'codex' && !action) {
        const cli = findCodexCli()
        return sendJson(res, 200, {ok: true, result: {available: !!cli, cli: cli ? path.basename(cli) : ''}})
      }
      if (req.method === 'POST' && id === 'codex' && !action) {
        const input = await bodyJson(req) as {brief?: string; topic?: string; platform?: string; title?: string; keywords?: string; refs?: string[]; count?: number; ratio?: string}
        let brief = String(input.brief || '').trim()
        if (!brief) brief = (await generateCoverPrompt(input)).text
        const refs = resolveCoverRefs(input.refs || [])
        const generated = await generateWithCodex({
          prompt: brief, refs,
          count: Number(input.count) || 1,
          ratio: String(input.ratio || '9:16'),
          timeoutMs: 240_000,
        })
        const saved = saveGeneratedCovers(generated.files)
        return sendJson(res, 200, {
          ok: true,
          result: {
            assets: saved.added, all: saved.assets, prompt: brief,
            threadId: generated.threadId, ms: generated.ms, refs: generated.refs, message: generated.message,
          },
        })
      }
      // 套版：拿一张无字底图 + 标题，按各平台比例各排一版成品。
      // 字是字体文件排的，所以改标题不用重新生图，多平台也只是多截几张图。
      if (req.method === 'POST' && id === 'compose' && !action) {
        const input = await bodyJson(req, 24 * 1024 * 1024) as {
          base?: string; title?: string; keyword?: string; subtitle?: string
          headline?: {main?: string; sub?: string; keywords?: string[]}
          ratios?: string[]; palette?: 'auto' | 'viral' | 'ink' | 'warm' | 'paper'; safeArea?: number; bottomInset?: number
        }
        const ref = String(input.base || '')
        const file = ref.startsWith('library:') ? coverImage(ref.slice('library:'.length)) : undefined
        if (!file) throw Object.assign(Error('先在上方素材库点一张无字底图，再套版'), {status: 400})
        // 封面标题走提炼后的主副标题；没传才退回用户填的标题，绝不把整条视频标题原样印上去。
        const title = String(input.headline?.main || input.title || '').trim()
        const subtitle = String(input.headline?.sub || input.subtitle || '').trim()
        const keyword = String(input.keyword || (input.headline?.keywords || []).slice(0, 2).join(' · ')).trim()
        const composed = await composeCovers({
          base: file, title, keyword, subtitle, ratios: (input.ratios || []).slice(0, 6),
          palette: input.palette || 'auto', safeArea: input.safeArea, bottomInset: input.bottomInset,
          outDir: coverDir(),
        })
        const saved = saveGeneratedCovers(composed, 'composed')
        return sendJson(res, 200, {
          ok: true,
          result: {
            added: saved.added, all: saved.assets,
            files: composed.map(row => ({file: row.file, ratio: row.ratio, width: row.width, height: row.height, bytes: row.bytes})),
          },
        })
      }
      if (req.method === 'GET' && id === 'file' && action) {
        const file = coverImage(action)
        if (!file) return sendJson(res, 404, {ok: false, error: '图片不存在'})
        res.writeHead(200, {'Content-Type': file.endsWith('.png') ? 'image/png' : file.endsWith('.webp') ? 'image/webp' : 'image/jpeg', 'Cache-Control': 'public, max-age=31536000'})
        res.end(fs.readFileSync(file))
        return
      }
      // 封面素材库：Codex / GPT-Image 出好的成品直接传进来，和 Seedream 生成图放在一起。
      if (req.method === 'GET' && id === 'library' && !action) return sendJson(res, 200, {ok: true, result: listCoverAssets()})
      if (req.method === 'POST' && id === 'library' && !action) {
        const input = await bodyJson(req, 64 * 1024 * 1024) as {images?: {data?: string; width?: number; height?: number; source?: string}[]}
        return sendJson(res, 200, {ok: true, result: saveCoverUploads(input.images || [])})
      }
      if (req.method === 'DELETE' && id === 'library' && action) {
        return sendJson(res, 200, {ok: true, result: deleteCoverAsset(decodeURIComponent(action))})
      }
    }

    if (head === 'accounts') {
      if (req.method === 'GET' && !id) return sendJson(res, 200, {ok: true, result: listAccounts()})
      if (req.method === 'POST' && !id) {
        const input = await bodyJson(req) as {platform?: MatrixPlatform; alias?: string}
        return sendJson(res, 200, {ok: true, result: addAccount(input.platform as MatrixPlatform, String(input.alias || ''))})
      }
      if (req.method === 'POST' && id && action === 'open') return sendJson(res, 200, {ok: true, result: openAccountWindow(id)})
      if (req.method === 'POST' && id && action === 'check') return sendJson(res, 200, {ok: true, result: await checkAccount(id)})
      if (req.method === 'POST' && id && action === 'identity') return sendJson(res, 200, {ok: true, result: await refreshAccountIdentity(id)})
      if (req.method === 'PATCH' && id) return sendJson(res, 200, {ok: true, result: patchAccount(id, await bodyJson(req) as {alias?: string; follow?: boolean})})
      if (req.method === 'DELETE' && id) return sendJson(res, 200, {ok: true, result: removeAccount(id)})
    }
    if (req.method === 'POST' && head === 'accounts' && parts[1] === 'check-all') return sendJson(res, 200, {ok: true, result: await checkAllAccounts()})

    if (head === 'tasks') {
      if (req.method === 'GET') return sendJson(res, 200, {ok: true, result: listTasks()})
      if (req.method === 'POST' && !id) return sendJson(res, 200, {ok: true, result: createTasks(await bodyJson(req) as Parameters<typeof createTasks>[0])})
      if (req.method === 'POST' && id && action === 'run') return sendJson(res, 200, {ok: true, result: await runTask(id)})
      if (req.method === 'POST' && id && action === 'cancel') return sendJson(res, 200, {ok: true, result: cancelTask(id)})
      if (req.method === 'POST' && id && action === 'confirm') {
        const body = await bodyJson(req) as {url?: string}
        return sendJson(res, 200, {ok: true, result: confirmTask(id, body.url || '')})
      }
      // 手机发布（UserHandoff）：启动对账 / 执行一次对账
      if (req.method === 'POST' && id && action === 'handoff') return sendJson(res, 200, {ok: true, result: startHandoff(id)})
      if (req.method === 'POST' && id && action === 'handoff-poll') return sendJson(res, 200, {ok: true, result: await pollHandoff(id)})
      if (req.method === 'POST' && id === 'run-queue') return sendJson(res, 200, {ok: true, result: await runQueue()})
      if (req.method === 'DELETE' && id && !action) return sendJson(res, 200, {ok: true, result: deleteTask(id)})
      if (req.method === 'POST' && id === 'purge') {
        const body = await bodyJson(req).catch(() => ({})) as {states?: PublishTask['status'][]}
        return sendJson(res, 200, {ok: true, result: purgeTasks(body.states)})
      }
    }

    if (head === 'works') {
      if (req.method === 'GET') return sendJson(res, 200, {ok: true, result: listWorks()})
      if (req.method === 'POST' && !id) return sendJson(res, 200, {ok: true, result: trackWork(await bodyJson(req) as Parameters<typeof trackWork>[0])})
      if (req.method === 'POST' && id && action === 'sync') return sendJson(res, 200, {ok: true, result: await syncWork(id)})
      if (req.method === 'DELETE' && id) return sendJson(res, 200, {ok: true, result: untrackWork(id)})
    }

    // —— 发布文案存档：任务清了文案还在，按内容文件聚合回看 ——
    if (head === 'archive') {
      if (req.method === 'GET' && !id) {
        const file = url.searchParams.get('file')
        const rows = listArchive()
        return sendJson(res, 200, {ok: true, result: file ? rows.filter(r => r.contentFile === file) : rows})
      }
      if (req.method === 'DELETE' && id && !action) return sendJson(res, 200, {ok: true, result: removeArchive(decodeURIComponent(id))})
    }

    // —— 监控台：大屏 / 粉丝 / 复盘 / 周报（数据内核在 monitor-hub.ts）——
    if (head === 'monitor') {
      if (req.method === 'GET' && id === 'health') return sendJson(res, 200, {ok: true, result: await systemHealth()})
      if (req.method === 'GET' && id === 'overview') {
        return sendJson(res, 200, {ok: true, result: overviewOf(listWorks(), listTasks(), listAccounts())})
      }
      if (req.method === 'GET' && id === 'followers') {
        return sendJson(res, 200, {ok: true, result: readFollowers()})
      }
      if (req.method === 'POST' && id === 'followers' && action === 'collect') {
        const body = await bodyJson(req).catch(() => ({})) as {platforms?: string[]}
        const platforms = body.platforms?.filter((p): p is MatrixPlatform => (MATRIX_PLATFORMS as readonly string[]).includes(p))
        return sendJson(res, 200, {ok: true, result: await collectFollowers(listAccounts(), platforms)})
      }
      if (req.method === 'GET' && id === 'review') {
        const works = listWorks(), work = id && action ? works.find(w => w.id === action) : undefined
        if (!work) throw Object.assign(Error('作品不存在'), {status: 404})
        const age = Math.max(1, Math.min(90, Number(url.searchParams.get('age')) || 7))
        return sendJson(res, 200, {ok: true, result: reviewOf(work, works, listTasks(), age)})
      }
      if (req.method === 'GET' && id === 'weekly') {
        return sendJson(res, 200, {ok: true, result: weeklyOf(listWorks(), listTasks())})
      }
    }
    return sendJson(res, 404, {ok: false, error: '未知的矩阵接口'})
  } catch (error) {
    const status = (error as {status?: number}).status || 502
    return sendJson(res, status, {ok: false, error: error instanceof Error ? error.message : String(error)})
  }
}

// 模块加载即挂 30s 调度器：定时排期自动执行 + running 超时补偿。
// unref 了，不会挡进程退出；测试里要静默可用 stopMatrixScheduler()。
startMatrixScheduler()
