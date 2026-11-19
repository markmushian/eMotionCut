import { useEffect, useMemo, useState } from 'react'
import type { LibraryStyle } from '@/workspace/style-library/style-library'

export type MotionTemplate = {
  path: string; fileName: string; sha256: string; bytes: number; engine?: string; controlCount?: number
  template: { id?: string; name?: string; version?: string; layout?: string; width?: number; height?: number }
  poster?: string; preview?: string
}

type Catalog = { packages: MotionTemplate[] }
const MOTION_PREFIX = 'motion-'
export const motionStyleId = (item: MotionTemplate) => `${MOTION_PREFIX}${item.sha256}`
export const isMotionStyleId = (id: string) => id.startsWith(MOTION_PREFIX) && /^[a-f0-9]{64}$/.test(id.slice(MOTION_PREFIX.length))

export function motionCategory(item: MotionTemplate) {
  const folder = item.path.replaceAll('\\', '/').split('/').at(-2) || 'Motion 模板'
  return folder.replace(/^\d+[-_s]*/, '')
}

/**
 * 九类用途，和「新建通用剪辑 → ② 想做什么」用的是同一套名字。
 * Motion 包的目录名是按作者习惯起的（promo / visual-art / explainer…），
 * 和用户脑子里的用途对不上，所以这里按名字和目录里的词做一次归类；
 * 一条都不命中的落到「品牌广告·宣传片」这个通用兜底，保证每个模板都能在某类里被找到。
 */
export const MOTION_PURPOSES = [
  '产品发布',
  '品牌广告·宣传片',
  '知识讲解·课件',
  '故事·绘本·短剧',
  '音乐 MV·混剪',
  '新闻·数据·报告',
  '节日·活动·开场',
  '旅行·城市·纪录',
  '游戏·动漫',
] as const
export type MotionPurpose = typeof MOTION_PURPOSES[number]

/**
 * 顺序即优先级：名字里能直接看出来的用途（像素闯关、回忆相册、宣纸手卷）先命中，
 * 别被目录名里的「产品宣传」一类的泛词抢走。
 */
const PURPOSE_RULES: { name: MotionPurpose; words: string[] }[] = [
  { name: '游戏·动漫', words: ['游戏', '动漫', '二次元', '像素', '闯关', '电竞', 'game', 'anime', 'pixel'] },
  { name: '音乐 MV·混剪', words: ['音乐', 'mv', '混剪', '节奏', '鼓点', '歌词', 'beat', 'music', '快切'] },
  { name: '新闻·数据·报告', words: ['新闻', '报告', '财报', '数据', '统计', '调查', 'news', 'report', 'data'] },
  { name: '节日·活动·开场', words: ['节日', '活动', '开场', '倒计时', '庆典', '春节', '新年', '生日', '片头', 'opening'] },
  { name: '旅行·城市·纪录', words: ['旅行', '城市', '纪录', '风景', '地理', '公路', '航拍', 'travel', 'city'] },
  { name: '故事·绘本·短剧', words: ['故事', '绘本', '短剧', '小说', '叙事', '童话', '回忆', '相册', '手卷', '手绘', '水彩', 'story'] },
  { name: '产品发布', words: ['发布', '发布会', '拆解', '蓝图', '流程', '展陈', '教程', '工程', '产品', 'keynote', 'product'] },
  { name: '知识讲解·课件', words: ['讲解', '课件', '知识', '课程', '教学', '科普', '图表', '信息图', 'explainer', 'explain'] },
  { name: '品牌广告·宣传片', words: ['宣传', '广告', '品牌', '海报', '拼贴', '视觉', '图形', '照片', '人像', '艺术', 'collage', 'poster', 'brand', 'visual'] },
]

export function motionPurpose(item: MotionTemplate): MotionPurpose {
  const text = `${item.template.name || ''} ${item.fileName} ${motionCategory(item)}`.toLocaleLowerCase()
  for (const rule of PURPOSE_RULES) if (rule.words.some(word => text.includes(word.toLocaleLowerCase()))) return rule.name
  return '品牌广告·宣传片'
}

/** 某一类用途下的模板，保持原顺序。 */
export function motionByPurpose(items: readonly MotionTemplate[], purpose: string) {
  return items.filter(item => motionPurpose(item) === purpose)
}

const poster = (name: string, portrait: boolean) => {
  const safe = name.replace(/[<>&'\"]/g, '')
  const w = portrait ? 720 : 1280, h = portrait ? 1280 : 720
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#07131b"/><stop offset=".55" stop-color="#102933"/><stop offset="1" stop-color="#00d9ae"/></linearGradient><pattern id="p" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="#91ffe8" stroke-opacity=".12"/></pattern></defs><rect width="100%" height="100%" fill="url(#g)"/><rect width="100%" height="100%" fill="url(#p)"/><text x="7%" y="12%" fill="#63fbd8" font-family="Arial" font-size="24" letter-spacing="5">MOTION / VERIFIED</text><text x="7%" y="70%" fill="white" font-family="Microsoft YaHei,Arial" font-weight="700" font-size="${portrait ? 54 : 66}">${safe}</text><text x="7%" y="78%" fill="#b5cbd0" font-family="Arial" font-size="24">AGENT DRIVEN TEMPLATE</text></svg>`
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

export function asLibraryStyle(item: MotionTemplate, input: 'footage' | 'topic' = 'footage'): LibraryStyle {
  const name = item.template.name || item.fileName.replace(/\.motion$/i, '')
  const portrait = item.template.layout === 'portrait'
  const controls = item.controlCount ? ` · ${item.controlCount} 类动效控件` : ''
  return {
    id: motionStyleId(item), name_zh: name, description_zh: `MotionGit ${item.template.version || ''} · ${portrait ? '竖屏' : '横屏'}可执行模板${controls}`,
    category_zh: motionCategory(item), input_type: input,
    user_provides: { summary_zh: input === 'footage' ? '视频、照片和本期文字' : '文案或主题，素材可稍后补充', own_video: 'optional', script_or_topic: 'required', voice_over: 'optional' },
    uses: [motionPurpose(item), motionCategory(item), portrait ? '竖屏' : '横屏'], attribution: 'MotionGit personal template', third_party_in_preview: [],
    preview: item.preview || '', poster: item.poster || poster(name, portrait),
  }
}

/**
 * 本地 Motion 包 + MotionGit 在线目录（只用来匹配封面/样片）。
 * 以前是每进一次页面把两个接口都重读一遍；而且在线目录要现抓页面，慢的时候十几秒，
 * 整个列表跟着一起等 —— 现在拆成两段：本地索引先出，在线封面到了再补。
 * 模块级缓存，整页会话只读一次；导入新目录后调 invalidateMotionTemplates()。
 */
type OnlineEntry = {title?: string; name?: string; href?: string; poster?: string; preview?: string}
let localCache: Promise<MotionTemplate[]> | undefined
let onlineCache: Promise<OnlineEntry[]> | undefined

const normalizeName = (value: string) => value.toLocaleLowerCase().replace(/[\s·・—_\-.,，。:：()（）]/g, '')

function loadLocalTemplates(): Promise<MotionTemplate[]> {
  localCache ??= fetch('/api/motion/templates').then(r => r.json())
    .then(body => body.ok ? (body.result.packages || []) as MotionTemplate[] : [])
    .catch(() => [])
  return localCache
}

function loadOnlineCatalog(): Promise<OnlineEntry[]> {
  onlineCache ??= fetch('/api/motion/online-catalog').then(r => r.json())
    .then(body => body.ok && Array.isArray(body.result) ? body.result as OnlineEntry[] : [])
    .catch(() => [])
  return onlineCache
}

function mergePreviews(items: MotionTemplate[], catalog: OnlineEntry[]): MotionTemplate[] {
  if (!catalog.length) return items
  return items.map(item => {
    const name = normalizeName(item.template.name || item.fileName.replace(/\.motion$/i, '')), id = normalizeName(item.template.id || '')
    const exact = catalog.find(entry => normalizeName(entry.title || entry.name || '') === name)
    const byId = id ? catalog.find(entry => normalizeName((entry.href || '').split('/').at(-1) || '') === id) : undefined
    const fuzzy = catalog.find(entry => {
      const title = normalizeName(entry.title || entry.name || '')
      return title.length >= 6 && (name.includes(title) || title.includes(name))
    })
    const hit = exact || byId || fuzzy
    if (!hit?.poster && !hit?.preview) return item
    return {...item, poster: hit.poster || item.poster || '', preview: hit.preview || item.preview || ''}
  })
}

export function invalidateMotionTemplates() { localCache = undefined; onlineCache = undefined }

// 打开模板库不该每次都等接口：上一次的列表快照存进 localStorage，进页面先把快照画出来（秒开），
// 网络回来后再悄悄换成最新数据。窗口刷新、重启应用后依然能秒开。
const LS_LOCAL = 'motion-templates-local'
const LS_ONLINE = 'motion-templates-online'

function readLS<T>(key: string): T | undefined {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) as T : undefined } catch { return undefined }
}

function writeLS(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* 存不下就算了，不影响主流程 */ }
}

export function useMotionTemplates(version = 0) {
  const [items, setItems] = useState<MotionTemplate[]>([])
  const [ready, setReady] = useState(false)
  useEffect(() => {
    let live = true
    // 先画上一次的快照（有就秒开），再等网络刷新；接口挂了也不清掉已有内容。
    const cachedLocal = readLS<MotionTemplate[]>(LS_LOCAL)
    const cachedOnline = readLS<OnlineEntry[]>(LS_ONLINE)
    if (cachedLocal?.length) {
      setItems(cachedOnline?.length ? mergePreviews(cachedLocal, cachedOnline) : cachedLocal)
      setReady(true)
    }
    void loadLocalTemplates().then(rows => {
      if (!live) return
      if (rows.length) {
        writeLS(LS_LOCAL, rows)
        setItems(rows)
      }
      if (rows.length || !cachedLocal?.length) setReady(true)
      // 本地先出；在线封面/样片到了再补一次，不让一个慢接口挡住整个列表。
      void loadOnlineCatalog().then(catalog => {
        if (!live || !rows.length) return
        if (catalog.length) writeLS(LS_ONLINE, catalog)
        setItems(mergePreviews(rows, catalog))
      })
    })
    return () => { live = false }
  }, [version])
  return useMemo(() => ({ items, ready, styles: items.map(item => asLibraryStyle(item)) }), [items, ready])
}
