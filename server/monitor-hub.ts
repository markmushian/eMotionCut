/**
 * 作品监控 · 数据内核
 *
 * 从麦田（mdldm-maitian）移植的采集与复盘算法，按 eMotionCut 的实际环境重写：
 * - 不引入 node:sqlite：粉丝、指标快照继续落 JSON，跟矩阵台共用一套用户数据目录。
 * - 不依赖 macOS / launchd / osascript / Chrome 插件桥：采集走 playwright-core 连账号自己的 CDP 端口。
 * - 只读取本账号创作后台已经能看到的数据，按作品 ID / 标题匹配；不采集他人内容、不做互动自动化。
 *
 * 本模块只做「采集 + 纯算法」，数据读写由 creator-matrix.ts 统一调度，避免 ESM 循环依赖。
 */
import fs from 'node:fs'
import path from 'node:path'
import net from 'node:net'
import type {Page as PlaywrightPage} from 'playwright-core'
import type {MatrixAccount, MatrixPlatform, MonitoredWork, PublishTask} from './creator-matrix.ts'

const DAY = 864e5
const J = JSON.stringify

// ——— 基础工具 ———

/** "1.2万" "6,017" "5.3K" → 数字；读不到返回 null，不拿 0 冒充。 */
export function num(value: unknown): number | null {
  if (value == null) return null
  const text = String(value).replace(/,/g, '').trim()
  const match = text.match(/^([\d.]+)\s*(万|亿|K|M|w)?/i)
  if (!match) return null
  const unit = {'万': 1e4, 'w': 1e4, '亿': 1e8, 'k': 1e3, 'm': 1e6}[(match[2] || '').toLowerCase() as 'k' | 'm'] || 1
  const parsed = parseFloat(match[1])
  return Number.isFinite(parsed) ? Math.round(parsed * unit) : null
}

const dayOf = (input: string | number | Date): string => {
  const date = new Date(input)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export const today = () => dayOf(Date.now())
const pct = (a: number, b: number, digits = 1) => b ? (a / b * 100).toFixed(digits) + '%' : '–'
const fmt = (n: number | null | undefined) => n == null ? '–' : Math.round(n).toLocaleString('zh-CN')
const idOf = (url: string, re: RegExp) => (url.match(re) || [])[1] || ''

// ——— 数据契约 ———

export type FollowerRow = {total: number; gain: number | null; loss: number | null; source: 'live' | 'backfill'; collectedAt: string}
export type FollowerStore = Partial<Record<MatrixPlatform, Record<string, FollowerRow>>>
export type FollowerReading = {
  total: number
  todayNet?: number
  history: Array<{day: string; total?: number; net?: number; gain?: number; loss?: number}>
}
export type MetricReading = {
  views?: number | null; likes?: number | null; comments?: number | null
  favorites?: number | null; shares?: number | null; danmaku?: number | null; coins?: number | null
  extra?: Record<string, number | null>
}

const userRoot = () => process.env.EMOTIONCUT_USER_DATA || path.join(process.cwd(), '.dev')
const followersFile = () => path.join(userRoot(), 'creator', 'matrix', 'followers.json')

/** 与 creator-matrix 同款预检：窗口没开时只报一句明确的话，不抛 ECONNREFUSED。 */
function portOpen(port: number, timeout = 900): Promise<boolean> {
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
async function connectCdp(port: number) {
  if (!await portOpen(port)) throw Object.assign(Error(`账号浏览器窗口没开着（调试端口 ${port}）。到「矩阵分发 → 账号矩阵」点「打开登录窗口」后再采集。`), {status: 400})
  const {chromium} = await import('playwright-core')
  return chromium.connectOverCDP(`http://127.0.0.1:${port}`)
}

export function readFollowers(): FollowerStore {
  try { return JSON.parse(fs.readFileSync(followersFile(), 'utf8')) as FollowerStore } catch { return {} }
}
function writeFollowers(store: FollowerStore) {
  const file = followersFile()
  fs.mkdirSync(path.dirname(file), {recursive: true})
  const temp = file + '.tmp'
  fs.writeFileSync(temp, J(store, null, 2) + '\n', 'utf8')
  fs.renameSync(temp, file)
}

/** 存今日实测值；平台后台自带的历史趋势只补「还没有实测值」的日子，实测优先于回填。 */
export function saveFollowers(platform: MatrixPlatform, reading: FollowerReading): number {
  const store = readFollowers(), rows = store[platform] || (store[platform] = {}), now = new Date().toISOString(), t = today()
  rows[t] = {total: reading.total, gain: null, loss: null, source: 'live', collectedAt: now}
  let history = (reading.history || []).filter(h => h.day < t).sort((a, b) => a.day < b.day ? 1 : -1)
  if (history.length && history[0].total == null) {
    // 只有每日净增时，从今天的总数倒推每天结束时的总数（今天的净增取平台给的当日数，没有按 0 算）。
    let running = reading.total - (reading.todayNet || 0)
    for (const h of history) { h.total = running; running -= h.net || 0 }
  }
  let filled = 0
  for (const h of history) {
    if (h.total == null || rows[h.day]?.source === 'live') continue
    rows[h.day] = {total: h.total, gain: h.gain ?? (h.net != null && h.net > 0 ? h.net : null), loss: h.loss ?? null, source: 'backfill', collectedAt: now}
    filled++
  }
  store[platform] = Object.fromEntries(Object.entries(rows).sort(([a], [b]) => a.localeCompare(b)))
  writeFollowers(store)
  return filled
}

// ——— 浏览器适配 ———

/** 麦田 withTab(tabId, fn) 的 eMotionCut 版本：在账号自己的浏览器上下文里开一个只读标签。 */
async function withTab<T>(
  browser: {contexts: () => Array<{newPage: () => Promise<PlaywrightPage>}>},
  url: string, fn: (page: PlaywrightPage) => Promise<T> | T,
  options: {wait?: number; timeout?: number} = {},
): Promise<T> {
  const context = browser.contexts()[0]
  if (!context) throw Error('这个账号的窗口没开着，请先打开登录窗口')
  const page = await context.newPage()
  try {
    await page.goto(url, {waitUntil: 'domcontentloaded', timeout: options.timeout ?? 60000})
    await page.waitForTimeout(options.wait ?? 3000)
    return await fn(page)
  } finally { await page.close().catch(() => {}) }
}
const evaluate = (page: PlaywrightPage, expression: string) => page.evaluate(expression) as Promise<any>
const sleep = (page: PlaywrightPage, ms: number) => page.waitForTimeout(ms)
type Browser = Parameters<typeof withTab>[0]

// ——— 粉丝采集器：只读自己账号的粉丝总数与后台自带趋势 ———

export const followerCollectors: Partial<Record<MatrixPlatform, (browser: Browser) => Promise<FollowerReading | null>>> = {
  async bilibili(browser) {
    const get = (u: string) => `fetch(${J(u)},{credentials:'include'}).then(r=>r.json())`
    const result = await withTab(browser, 'https://member.bilibili.com/platform/home', page => evaluate(page,
      `Promise.all([${get('/x/web/index/stat')},${get('/x/web/data/v2/overview/stat/graph?period=2&s_locale=zh_CN&type=fan')}]).then(([a,b])=>({total:a.data&&a.data.total_fans,inc:a.data&&a.data.incr_fans,tend:(b.data&&b.data.tendency)||[]}))`))
    if (result?.total == null) return null
    return {
      total: result.total, todayNet: result.inc || 0,
      history: (result.tend || []).map((x: any) => ({day: dayOf(x.date_key * 1000), net: x.total_inc})),
    }
  },
  async douyin(browser) {
    const total = await withTab(browser, 'https://creator.douyin.com/creator-micro/home', async page => {
      await sleep(page, 3000)
      const result = await evaluate(page, `fetch('/web/api/media/user/info/',{credentials:'include'}).then(x=>x.user&&x.user.follower_count)`)
      return result
    })
    if (total == null) return null
    return {total, history: []}
  },
  async xhs(browser) {
    const result = await withTab(browser, 'https://creator.xiaohongshu.com/statistics/fans-data', async page => {
      await sleep(page, 3000)
      return evaluate(page, `fetch('/api/galaxy/creator/data/fans/overall_new',{credentials:'include'}).then(r=>r.json()).then(x=>{const d=x.data.thirty,m=(a)=>Object.fromEntries((a||[]).map(i=>[i.date,i.count]));const g=m(d.rise_fans_list),l=m(d.leave_fans_list);return {total:d.fans_count,list:(d.fans_list||[]).map(i=>({date:i.date,total:i.count,gain:g[i.date],loss:l[i.date]}))}})`)
    })
    if (result?.total == null) return null
    return {
      total: result.total,
      history: (result.list || []).map((x: any) => ({day: dayOf(`${x.date}`), total: x.total ?? undefined, gain: x.gain ?? undefined, loss: x.loss ?? undefined})),
    }
  },
  async channels(browser) {
    // 视频号数据在 wujie-app 的 shadow DOM 里，用文字节点扫描，不直接调它的接口。
    const SHADOW_TEXT = `(()=>{const R=document.querySelector('wujie-app')&&document.querySelector('wujie-app').shadowRoot;if(!R)return '';const w=document.createTreeWalker(R,NodeFilter.SHOW_TEXT);let s=[],x;while(x=w.nextNode()){const p=x.parentElement;if(p&&/STYLE|SCRIPT/.test(p.tagName))continue;const v=x.textContent.trim().replace(/\\s+/g,' ');if(v)s.push(v)}return s.join(' | ')})()`
    const text = await withTab(browser, 'https://channels.weixin.qq.com/platform/statistic/follower', async page => {
      for (let i = 0; i < 8; i++) {
        await sleep(page, 2000)
        const value = await evaluate(page, SHADOW_TEXT).catch(() => '')
        if (/关注者总数 \| \d/.test(value) && /\d{4}\/\d+\/\d+ \|/.test(value)) return value
      }
      return evaluate(page, SHADOW_TEXT).catch(() => '')
    }, {wait: 9000})
    const total = num((text || '').match(/关注者总数 \| ([\d,.万]+)/)?.[1])
    if (total == null) return null
    const history = [...(text || '').matchAll(/(\d{4})\/(\d{1,2})\/(\d{1,2}) \| (-?[\d,]+) \| ([\d,]+) \| ([\d,]+) \| ([\d,]+)/g)].map(m => ({
      day: `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`, total: num(m[7]) ?? undefined, gain: num(m[5]) ?? undefined, loss: num(m[6]) ?? undefined,
    }))
    return {total, history}
  },
}

/** 采一批账号的粉丝；只采传进来的平台，失败逐个记录不中断。 */
export async function collectFollowers(accounts: MatrixAccount[], platforms?: MatrixPlatform[]) {
  const out: Record<string, unknown> = {}
  for (const account of accounts) {
    if (platforms?.length && !platforms.includes(account.platform)) continue
    const collector = followerCollectors[account.platform]
    if (!collector) { out[account.alias || account.platform] = '此平台暂无粉丝采集器'; continue }
    let browser: Browser | undefined
    try {
      browser = await connectCdp(account.port) as Browser
      const reading = await collector(browser)
      if (!reading) { out[account.alias || account.platform] = '没读到'; continue }
      const backfilled = saveFollowers(account.platform, reading)
      out[account.alias || account.platform] = {total: reading.total, backfilled}
    } catch (error) {
      out[account.alias || account.platform] = '出错：' + (error instanceof Error ? error.message.slice(0, 80) : String(error))
    } finally { await (browser as unknown as {close?: () => Promise<void>})?.close?.().catch(() => {}) }
  }
  return out
}

// ——— 流量采集器：从创作后台按作品 ID / 标题匹配，补齐收藏、分享、投币、弹幕 ———

export const metricCollectors: Partial<Record<MatrixPlatform, (browser: Browser, work: MonitoredWork, title: string) => Promise<MetricReading | null>>> = {
  async bilibili(browser, work, title) {
    const bvid = idOf(work.url, /video\/(BV\w+)/)
    const stats = await withTab(browser, 'https://member.bilibili.com/platform/upload-manager/article', page => evaluate(page,
      `fetch('/x/web/archives?status=is_pubing%2Cpubed%2Cnot_pubed&pn=1&ps=50&coop=1',{credentials:'include'}).then(r=>r.json()).then(x=>{const a=(x.data&&x.data.arc_audits||[]).find(a=>a.Archive.bvid===${J(bvid)}||a.Archive.title===${J(title)});return a?a.stat:null})`))
    return stats && {
      views: stats.view, likes: stats.like, comments: stats.reply, favorites: stats.favorite, shares: stats.share,
      coins: stats.coin, danmaku: stats.danmaku,
    }
  },
  async douyin(browser, work, title) {
    const id = idOf(work.url, /video\/(\d+)/)
    const stats = await withTab(browser, 'https://creator.douyin.com/creator-micro/content/manage', async page => {
      for (let i = 0; i < 15 && !(await evaluate(page, `document.querySelectorAll('[class*=video-card-info]').length`).catch(() => 0)); i++) await sleep(page, 2000)
      await sleep(page, 1500)
      return evaluate(page, `(()=>{for(const c of document.querySelectorAll('[class*=video-card-info]')){const k=Object.keys(c).find(k=>k.startsWith('__reactFiber'));let f=c[k];for(let i=0;i<25&&f;i++,f=f.return){const pr=f.memoizedProps;if(!pr)continue;const v=Object.values(pr).find(v=>v&&typeof v==='object'&&v.aweme_id&&v.desc!==undefined);if(v){if((${J(id)}&&v.aweme_id===${J(id)})||v.item_title===${J(title)})return v.statistics||null;break;}}}return null})()`)
    }, {wait: 6000})
    return stats && {
      views: stats.play_count, likes: stats.digg_count, comments: stats.comment_count,
      favorites: stats.collect_count, shares: stats.share_count,
    }
  },
  async xhs(browser, work, title) {
    const id = idOf(work.url, /explore\/(\w+)/)
    const cells = await withTab(browser, 'https://creator.xiaohongshu.com/new/note-manager', page => evaluate(page,
      `(()=>{const c=[...document.querySelectorAll('.note-card')].find(c=>{let i='';try{i=JSON.parse(c.getAttribute('data-impression')).noteTarget.value.noteId}catch(e){};return (${J(id)}&&i===${J(id)})||((c.querySelector('.note-card__title')||{}).innerText||'').trim()===${J(title)}});return c?[...c.querySelectorAll('.note-card__stat')].map(e=>e.innerText.trim()):null})()`), {wait: 12000})
    // 图标没有文字标签，按页面顺序：浏览、评论、点赞、收藏、分享
    return cells && cells.length >= 5 && {
      views: num(cells[0]), comments: num(cells[1]), likes: num(cells[2]), favorites: num(cells[3]), shares: num(cells[4]),
    }
  },
  async channels(browser, _work, title) {
    const first = title.split('\n')[0].trim().slice(0, 30)
    const cells = await withTab(browser, 'https://channels.weixin.qq.com/platform/post/list', page => evaluate(page,
      `(()=>{const R=document.querySelector('wujie-app')&&document.querySelector('wujie-app').shadowRoot;if(!R)return null;const c=[...R.querySelectorAll('.post-feed-item')].find(c=>c.innerText.trim().startsWith(${J(first)}));if(!c)return null;const o={};for(const d of c.querySelectorAll('.data-item')){const k=/eyes/.test(d.innerHTML)?'views':/outlined-like/.test(d.innerHTML)?'likes':/comment/.test(d.innerHTML)?'comments':/icon-share/.test(d.innerHTML)?'shares':/thumb/.test(d.innerHTML)?'recommend':'';if(k)o[k]=d.innerText.trim()}return o})()`), {wait: 14000})
    return cells && {
      views: num(cells.views), likes: num(cells.likes), comments: num(cells.comments), shares: num(cells.shares),
      extra: {推荐: num(cells.recommend)},
    }
  },
}

/** 采一条作品的后台指标（只读），返回扩展后的字段；写回由 creator-matrix 负责。 */
export async function collectWorkMetrics(account: MatrixAccount, work: MonitoredWork): Promise<MetricReading | null> {
  const collector = metricCollectors[account.platform]
  if (!collector) return null
  let browser: Browser | undefined
  try {
    browser = await connectCdp(account.port) as Browser
    return await collector(browser, work, work.title || '')
  } finally { await (browser as unknown as {close?: () => Promise<void>})?.close?.().catch(() => {}) }
}

// ——— 复盘算法：纯函数，输入 works/followers/tasks，不读数据库 ———

const interaction = (m: MetricReading | null | undefined) =>
  m ? (m.likes || 0) + (m.comments || 0) + (m.favorites || 0) + (m.shares || 0) : 0

/** 发布时间：优先发布任务的定时，其次作品首条快照，最后用登记时间兜底。 */
export function publishedAt(work: MonitoredWork, tasks: PublishTask[]): number {
  const task = tasks.find(t => t.url === work.url && t.status === 'done')
  if (task?.scheduledAt) return Date.parse(task.scheduledAt)
  const first = work.series?.[0]?.at
  return first ? Date.parse(first) : Date.parse(work.syncedAt)
}

/** 某条作品在时间点 t 的最后一条读数；没有就返回 null（不拿 0 冒充）。 */
function readingAt(work: MonitoredWork, t: number): MetricReading | null {
  const day = dayOf(t)
  const rows = (work.series || []).filter(row => dayOf(row.at) <= day)
  const last = rows[rows.length - 1]
  return last ? {
    views: last.views, likes: last.likes, comments: last.comments,
    favorites: (last as any).favorites, shares: (last as any).shares, danmaku: last.danmaku,
  } : null
}

function snapshot(work: MonitoredWork, ageDays: number, tasks: PublishTask[]) {
  const t = Math.min(Date.now(), publishedAt(work, tasks) + ageDays * DAY)
  const metric = readingAt(work, t)
  const views = metric?.views || 0
  return {t, metric, views, interactions: interaction(metric)}
}

const fansGain = (store: FollowerStore, from: number, to: number) => {
  const out: Record<string, number> = {}
  let sum = 0
  for (const [platform, rows] of Object.entries(store) as Array<[MatrixPlatform, Record<string, FollowerRow>]>) {
    const a = Object.entries(rows).filter(([day]) => day <= dayOf(from)).sort(([x], [y]) => y.localeCompare(x))[0]?.[1]
    const b = Object.entries(rows).filter(([day]) => day <= dayOf(to)).sort(([x], [y]) => y.localeCompare(x))[0]?.[1]
    if (a && b) { out[platform] = b.total - a.total; sum += out[platform] }
  }
  return {out, sum}
}

export type ReviewResult = {
  work: {id: string; title: string; platform: MatrixPlatform}
  ageDays: number
  current: {views: number; interactions: number; rate: string; metric: MetricReading | null}
  peer: {title: string; views: number; ratio: string} | null
  fans: {out: Record<string, number>; sum: number}
  text: string
}

/** 单期第 N 天复盘 + 上一期同龄对比（没有同龄数据就明确说没有，不硬比）。 */
export function reviewOf(work: MonitoredWork, works: MonitoredWork[], tasks: PublishTask[], ageDays = 7): ReviewResult {
  const store = readFollowers()
  const now = snapshot(work, ageDays, tasks)
  const candidates = works.filter(w => w.id !== work.id && publishedAt(w, tasks) < publishedAt(work, tasks))
    .map(w => ({w, snap: snapshot(w, ageDays, tasks)}))
    .filter(x => x.snap.metric && x.snap.views > 0)
    .sort((a, b) => publishedAt(b.w, tasks) - publishedAt(a.w, tasks))
  const peer = candidates[0]
  const fans = fansGain(store, publishedAt(work, tasks), now.t)
  const rate = pct(now.interactions, now.views)
  const lines = [
    `第 ${ageDays} 天复盘｜${work.title || work.url.slice(-12)}`,
    `全平台播放 ${fmt(now.views)}，互动 ${fmt(now.interactions)}，互动率 ${rate}`,
  ]
  if (peer) {
    const k = now.views / Math.max(1, peer.snap.views)
    lines.push(`同龄对比：上一期《${peer.w.title || peer.w.url.slice(-12)}》第 ${ageDays} 天 ${fmt(peer.snap.views)}，这一期是它的 ${k >= 1 ? k.toFixed(1) + ' 倍' : pct(now.views, peer.snap.views, 0)}`)
  } else lines.push('同龄对比：还没有上一期在第 ' + ageDays + ' 天的真实数据')
  if (Object.keys(fans.out).length) {
    lines.push(`发布后各平台涨粉 ${fans.sum >= 0 ? '+' : ''}${fmt(fans.sum)}（${Object.entries(fans.out).sort((a, b) => b[1] - a[1]).map(([p, v]) => `${p} ${v >= 0 ? '+' : ''}${fmt(v)}`).join('、')}）`)
  }
  return {
    work: {id: work.id, title: work.title, platform: work.platform}, ageDays,
    current: {views: now.views, interactions: now.interactions, rate, metric: now.metric},
    peer: peer ? {title: peer.w.title, views: peer.snap.views, ratio: now.views >= peer.snap.views ? (now.views / Math.max(1, peer.snap.views)).toFixed(1) + '×' : pct(now.views, peer.snap.views, 0)} : null,
    fans,
    text: lines.join('\n'),
  }
}

/** 周报：本周一到周日，只用我们自己的 works / followers 数据。 */
export function weeklyOf(works: MonitoredWork[], tasks: PublishTask[], now = Date.now()) {
  const local = new Date(now)
  const dow = (local.getDay() + 6) % 7
  const monday = new Date(local.getFullYear(), local.getMonth(), local.getDate() - dow).getTime()
  const from = monday - 7 * DAY, to = monday - 1
  const gain = (a: number, b: number) => works.reduce((sum, work) => {
    const later = readingAt(work, b), earlier = readingAt(work, a)
    return sum + Math.max(0, (later?.views || 0) - (earlier?.views || 0))
  }, 0)
  const store = readFollowers(), fans = fansGain(store, from, to)
  const published = works.filter(w => { const t = publishedAt(w, tasks); return t >= from && t <= to })
  const lines = [
    `周报｜${dayOf(from).slice(5)} ～ ${dayOf(to).slice(5)}`,
    `发了 ${published.length} 条${published.length ? '：' + published.map(w => `《${w.title || w.url.slice(-12)}》`).join('、') : ''}`,
    `全平台新增播放 ${fmt(gain(from, to))}`,
    `各平台涨粉 ${fans.sum >= 0 ? '+' : ''}${fmt(fans.sum)}`,
  ]
  return {key: dayOf(from), from: dayOf(from), to: dayOf(to), published: published.length, views: gain(from, to), fans: fans.sum, text: lines.join('\n')}
}

/** 大屏聚合：作品/粉丝/任务三份数据合成一个响应，前端一次请求就够。 */
export function overviewOf(works: MonitoredWork[], tasks: PublishTask[], accounts: MatrixAccount[]) {
  const store = readFollowers()
  const totalViews = works.reduce((n, w) => n + (w.metrics.views || 0), 0)
  const totalInteractions = works.reduce((n, w) => n + interaction(w.metrics), 0)
  const PLAT_LABEL: Record<string, string> = {
    douyin: '抖音', channels: '视频号', xhs: '小红书', kuaishou: '快手', bilibili: 'B站',
    baijiahao: '百家号', toutiao: '头条号', fanqie: '番茄', tiktok: 'TikTok',
    youtube: 'YouTube', instagram: 'Instagram', x: 'X',
  }
  const valueAt = (days: Array<[string, FollowerRow]>, day: string) =>
    days.filter(([d]) => d <= day).at(-1)?.[1].total ?? null
  const gainBetween = (days: Array<[string, FollowerRow]>, from: string, to: string) => {
    const a = valueAt(days, from), b = valueAt(days, to)
    return a == null || b == null ? null : b - a
  }
  const t = today(), yesterday = dayOf(Date.now() - DAY), weekAgo = dayOf(Date.now() - 7 * DAY), monthAgo = dayOf(Date.now() - 30 * DAY)
  const fans = Object.entries(store).map(([platform, rows]) => {
    const days = Object.entries(rows).sort(([a], [b]) => a.localeCompare(b))
    const total = days.at(-1)?.[1].total ?? 0
    const seriesAll = days.map(([day, row], index) => ({
      day, total: row.total, net: index ? row.total - days[index - 1][1].total : null,
      gain: row.gain ?? null, loss: row.loss ?? null, source: row.source,
    }))
    return {
      platform, total,
      today: gainBetween(days, yesterday, t),
      gain7: gainBetween(days, weekAgo, t),
      gain30: gainBetween(days, monthAgo, t),
      series: seriesAll.slice(-14),
    }
  }).filter(x => x.total > 0)
  /** 每日各平台净增，供总览的堆叠柱使用；没有读数的那天留空，不补 0。 */
  const daily = Array.from({length: 14}, (_, index) => ({
    day: dayOf(Date.now() - (13 - index) * DAY), byPlatform: {} as Record<string, number>,
  }))
  for (const fan of fans) for (const row of fan.series) {
    const slot = daily.find(d => d.day === row.day)
    if (slot && row.net != null) slot.byPlatform[fan.platform] = row.net
  }
  const platformRows = (['douyin', 'xhs', 'channels', 'bilibili', 'kuaishou'] as MatrixPlatform[]).map(id => {
    const rows = works.filter(w => w.platform === id)
    return {
      id, name: PLAT_LABEL[id] || id,
      works: rows.length, views: rows.reduce((n, w) => n + (w.metrics.views || 0), 0),
      interactions: rows.reduce((n, w) => n + interaction(w.metrics), 0),
    }
  }).filter(row => row.works > 0)
  const awaiting = tasks.filter(t => t.status === 'ready')
  const latest = [...works].sort((a, b) => b.syncedAt.localeCompare(a.syncedAt))[0] || null
  // 每天一算的数据分析：只报真实算得出的结论，样本不够就直说。
  const topFan = [...fans].filter(f => (f.gain7 || 0) > 0).sort((a, b) => (b.gain7 || 0) - (a.gain7 || 0))[0]
  const sum7 = fans.reduce((n, f) => n + (f.gain7 || 0), 0)
  const topWork = [...works].filter(w => (w.metrics.views || 0) > 0).sort((a, b) => (b.metrics.views || 0) - (a.metrics.views || 0))[0]
  const bestPlatform = [...platformRows].filter(p => p.views > 0).sort((a, b) => b.interactions / b.views - a.interactions / a.views)[0]
  const insights = [
    topFan && sum7 > 0
      ? `近 7 天涨粉最多的是${PLAT_LABEL[topFan.platform] || topFan.platform} +${fmt(topFan.gain7)}，占已统计平台合计 +${fmt(sum7)} 的 ${pct(topFan.gain7 || 0, sum7, 0)}。`
      : '近 7 天的粉丝数据还不够：先采一次粉丝，之后每天对比才有涨粉结论。',
    topWork
      ? `播放最高的是《${topWork.title || topWork.url.slice(-12)}》${fmt(topWork.metrics.views)}，发在${PLAT_LABEL[topWork.platform] || topWork.platform}。`
      : '还没有读到播放数据：点作品卡上的「同步」后就能比较哪条跑得好。',
    bestPlatform
      ? `互动率最高的是${bestPlatform.name} ${pct(bestPlatform.interactions, bestPlatform.views)}（${fmt(bestPlatform.interactions)} / ${fmt(bestPlatform.views)}）。`
      : '各平台互动率需要至少一条同步过的作品才能算。',
    `今天各平台合计涨粉 ${fmt(fans.reduce((n, f) => n + (f.today || 0), 0))}，统计时间 ${new Date().toLocaleString('zh-CN')}。`,
  ]
  return {
    totals: {
      works: works.length, views: totalViews, interactions: totalInteractions,
      rate: pct(totalInteractions, totalViews), fans7: sum7,
      accounts: accounts.length, loggedIn: accounts.filter(a => a.lastCheck?.loggedIn).length,
    },
    fans, daily, insights,
    platforms: platformRows,
    awaiting: awaiting.map(t => ({id: t.id, platform: t.platform, title: t.title, url: t.url, status: t.status})),
    latest: latest && {id: latest.id, title: latest.title, platform: latest.platform, url: latest.url, cover: latest.cover || undefined, metrics: latest.metrics, syncedAt: latest.syncedAt},
    timeline: works.flatMap(w => (w.series || []).map(s => ({day: dayOf(s.at), workId: w.id, views: s.views || 0, interactions: (s.likes || 0) + (s.comments || 0)}))),
    generatedAt: new Date().toISOString(),
  }
}
