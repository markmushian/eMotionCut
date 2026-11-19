import {useCallback, useEffect, useMemo, useState} from 'react'
import {
  Activity, AlertTriangle, BarChart3, Bot, CalendarDays, Check, ChevronRight, Clock, ExternalLink, Filter, Layers3,
  LayoutDashboard, Plus, RefreshCw, Sparkles, Trash2, TrendingUp, X,
} from 'lucide-react'
import {askAgent, matrixApi} from './agent-client'
import {AgentModelPicker} from './agent-model'
import {AnimatedNumber} from '../motion/AnimatedNumber'
import {PlatformLogo} from './platform-logos'
import {readCache, writeCache} from './desk-cache'
import './matrix.css'

type Platform = {id: string; name: string; createUrl?: string}
type Account = {id: string; platform: string; alias: string; port?: number; lastCheck?: {at: string; loggedIn: boolean; error?: string}}
type Comment = {id: string; text: string; user: string; like: number; at?: string; replies?: number}
type Snapshot = {at: string; views: number|null; likes: number|null; comments: number|null; danmaku: number|null; favorites?: number|null; shares?: number|null}
type Work = {
  id: string; accountId: string; platform: string; url: string; title: string
  metrics: {views: number|null; likes: number|null; comments: number|null; danmaku: number|null; favorites?: number|null; shares?: number|null; coins?: number|null}
  comments: Comment[]; cover?: string; series?: Snapshot[]; syncedAt: string
}
type Task = {id: string; accountId?: string; platform: string; title: string; status: string; scheduledAt?: string; step?: string; url?: string; cover?: string}
type FanRow = {platform: string; total: number; today: number|null; gain7: number|null; gain30: number|null; series: Array<{day: string; net: number|null}>}
type Overview = {
  totals: {works: number; views: number; interactions: number; rate: string; fans7: number; accounts: number; loggedIn: number}
  fans: FanRow[]
  daily: Array<{day: string; byPlatform: Record<string, number>}>
  insights: string[]
  platforms: Array<{id: string; name: string; works: number; views: number; interactions: number}>
  awaiting: Array<{id: string; platform: string; title: string; url?: string; status: string}>
  latest: {id: string; title: string; platform: string; url: string; syncedAt: string; metrics: Work['metrics']}|null
  timeline: Array<{day: string; workId: string; views: number; interactions: number}>
  generatedAt: string
}
type ReviewResult = {
  work: {id: string; title: string; platform: string}; ageDays: number
  current: {views: number; interactions: number; rate: string}
  peer: {title: string; views: number; ratio: string}|null
  fans: {out: Record<string, number>; sum: number}; text: string
}
type WeeklyResult = {key: string; from: string; to: string; published: number; views: number; fans: number; text: string}
type Health = {
  status: 'ok'|'warn'|'error'
  checks: Array<{id: string; label: string; status: 'ok'|'warn'|'error'; detail: string}>
  windows: Array<{id: string; alias: string; platform: string; port: number; open: boolean; loggedIn: boolean; lastCheck: string; error: string}>
  totals: {accounts: number; loggedIn: number; works: number; tasks: number; ready: number; queued: number}
  updatedAt: string
}

type Tab = 'overview' | 'calendar' | 'review' | 'funnel' | 'works' | 'system'
const TABS: Array<{id: Tab; label: string; icon: typeof LayoutDashboard}> = [
  {id: 'overview', label: '总览', icon: LayoutDashboard},
  {id: 'calendar', label: '分发日历', icon: CalendarDays},
  {id: 'review', label: '单期复盘', icon: BarChart3},
  {id: 'funnel', label: '转化漏斗', icon: Filter},
  {id: 'works', label: '作品档案', icon: Layers3},
  {id: 'system', label: '系统', icon: Activity},
]

const num = (value: number|null|undefined) => value === null || value === undefined ? '—' : value >= 10000 ? `${(value / 10000).toFixed(1)}万` : value.toLocaleString('zh-CN')
/** 数字跳动版：显示格式和 num() 一致，值变化时缓动。null 走占位符。 */
const fmtNum = (n: number) => num(Math.round(n))
const Num = ({value, plus = false}: {value: number|null|undefined; plus?: boolean}) =>
  value === null || value === undefined
    ? <>—</>
    : <>{plus ? '+' : ''}<AnimatedNumber value={value} format={fmtNum}/></>
const TOTAL = (rows: Work[], pick: (w: Work) => number|null|undefined) => rows.reduce((n, w) => n + (pick(w) || 0), 0)
const localDay = (iso: string) => {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const PLAT_NAME: Record<string, string> = {
  douyin: '抖音', channels: '视频号', xhs: '小红书', kuaishou: '快手',
  bilibili: 'B站', baijiahao: '百家号', toutiao: '头条号', fanqie: '番茄',
  tiktok: 'TikTok', youtube: 'YouTube', instagram: 'Instagram', x: 'X (Twitter)',
}
const platName = (id: string, platforms: Platform[]) => platforms.find(p => p.id === id)?.name || PLAT_NAME[id] || id
const statusCn: Record<string, string> = {planned: '已存档', queued: '排队', running: '填写中', ready: '待你点', done: '已发布', failed: '失败', cancelled: '已取消'}
/** 发布文案存档条目（前端轻类型，字段与服务端 archive.json 对齐）。 */
type ArchiveEntry = {
  id: string; taskId: string; contentFile: string; platform: string; accountId: string; alias: string
  title: string; caption?: string; tags: string[]; cover?: string; url?: string
  status: string; scheduledAt?: string; publishedAt?: string; createdAt: string; updatedAt: string
}

/** 近 7 天每日新增：多取一天作基准；某条作品今天和昨天都有读数才算增量，避免把存量当暴涨。 */
function buildTrend(works: Work[]) {
  const now = new Date(); now.setHours(0, 0, 0, 0)
  const days = Array.from({length: 8}, (_, index) => {
    const d = new Date(now.getTime() - (7 - index) * 86400000)
    return {key: localDay(d.toISOString()), label: ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]}
  })
  const tracks = works.map(work => days.map(day => {
    const rows = (work.series || []).filter(row => localDay(row.at) <= day.key)
    return rows[rows.length - 1]
  }))
  const points = days.slice(1).map((_, index) => {
    let views = 0, engage = 0
    for (const track of tracks) {
      const today = track[index + 1], yesterday = track[index]
      if (!today || !yesterday) continue
      views += Math.max(0, (today.views || 0) - (yesterday.views || 0))
      engage += Math.max(0, ((today.likes || 0) + (today.comments || 0)) - ((yesterday.likes || 0) + (yesterday.comments || 0)))
    }
    return {views, engage}
  })
  return {days: days.slice(1), points}
}

/** 纯 SVG 图表：不引图表库，柱=每日新增播放，折线=每日新增互动。 */
function TrendChart({days, points}: {days: {key: string; label: string}[]; points: {views: number; engage: number}[]}) {
  const [hover, setHover] = useState<number|null>(null)
  const W = 780, H = 260, padL = 58, padR = 58, padT = 20, padB = 34
  const rows = points.map((point, index) => ({
    views: index === 0 ? point.views : Math.max(0, point.views - points[index - 1].views),
    engage: index === 0 ? point.engage : Math.max(0, point.engage - points[index - 1].engage),
  }))
  const rawV = Math.max(1, ...rows.map(row => row.views)), rawE = Math.max(1, ...rows.map(row => row.engage))
  const nice = (value: number) => {
    const magnitude = Math.pow(10, Math.floor(Math.log10(Math.max(1, value))))
    const ratio = Math.max(1, value) / magnitude
    return (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 5 ? 5 : 10) * magnitude
  }
  const maxV = nice(rawV / 4) * 4, maxE = nice(rawE / 4) * 4
  const band = (W - padL - padR) / Math.max(1, rows.length)
  const center = (index: number) => padL + band * (index + 0.5)
  const yV = (value: number) => padT + (1 - value / maxV) * (H - padT - padB)
  const yE = (value: number) => padT + (1 - value / maxE) * (H - padT - padB)
  const segments: string[] = []
  let run: string[] = []
  rows.forEach((row, index) => {
    if (!row.views && !row.engage) { if (run.length) { segments.push(run.join(' ')); run = [] } return }
    run.push(`${run.length ? 'L' : 'M'}${center(index).toFixed(1)},${yE(row.engage).toFixed(1)}`)
  })
  if (run.length) segments.push(run.join(' '))
  const flat = rows.every(row => !row.views && !row.engage)
  return <div className="mx-chart-wrap"><svg className="mx-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="近 7 天每日新增播放与互动">
    <defs><linearGradient id="mxBar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#6ff2dc" stopOpacity="1"/><stop offset="1" stopColor="#3fe0c8" stopOpacity="0.42"/></linearGradient></defs>
    {[0, 1, 2, 3, 4].map(row => {
      const y = padT + row * (H - padT - padB) / 4
      return <g key={row}>
        <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="rgba(63,224,200,0.1)" strokeDasharray="3 5"/>
        <text x={padL - 10} y={y + 4} textAnchor="end" className="mx-chart__tick">{num(Math.round(maxV * (1 - row / 4)))}</text>
        <text x={W - padR + 10} y={y + 4} className="mx-chart__tick">{num(Math.round(maxE * (1 - row / 4)))}</text>
      </g>
    })}
    {rows.map((row, index) => {
      const top = yV(row.views), bottom = H - padB, width = Math.min(46, band * 0.55)
      if (row.views <= 0) return null
      return <rect key={days[index]?.key || index} x={center(index) - width / 2} y={top} width={width} height={Math.max(2, bottom - top)} rx="4" fill="url(#mxBar)"/>
    })}
    {segments.map((d, index) => <path key={'line' + index} d={d} fill="none" stroke="#f0b429" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round"/>)}
    {rows.map((row, index) => row.engage > 0 ? <circle key={'e' + index} cx={center(index)} cy={yE(row.engage)} r="3.6" fill="#050a0e" stroke="#f0b429" strokeWidth="2.2"/> : null)}
    {rows.map((row, index) => <text key={'t' + index} x={center(index)} y={H - 10} textAnchor="middle" className="mx-chart__tick">{days[index]?.label}</text>)}
    {flat && <text x={W / 2} y={H / 2} textAnchor="middle" className="mx-chart__empty">还没有同步过数据，跑一次「全部同步」这里就有曲线</text>}
    {hover != null && rows[hover] && <line x1={center(hover)} x2={center(hover)} y1={padT} y2={H - padB} stroke="rgba(63,224,200,0.4)" strokeDasharray="3 3"/>}
    {rows.map((row, index) => <rect key={'hit' + index} className="mx-chart-hit" x={padL + band * index} y={padT} width={band} height={H - padT - padB} fill="transparent" onMouseEnter={() => setHover(index)} onMouseLeave={() => setHover(null)}/>)}
  </svg>
    {hover != null && rows[hover] && <div className="mx-chart-tip" style={{left: `clamp(70px, ${(center(hover) / W * 100).toFixed(2)}%, calc(100% - 70px))`}}>
      <b>{days[hover]?.label}{days[hover] ? `（${days[hover].key.slice(5)}）` : ''}</b><br/>
      <i style={{'--dot': '#3fe0c8'} as React.CSSProperties}>播放 +{num(rows[hover].views)}</i>
      <i style={{'--dot': '#f0b429'} as React.CSSProperties}>互动 +{num(rows[hover].engage)}</i>
    </div>}
  </div>
}

/** 麦田式播放走势：同一条作品的按天采样折线，≥2 个点才画，否则给空态提示。 */
function DetailTrend({series}: {series: Snapshot[]}) {
  const pts = series.filter(s => s.views !== null && s.views !== undefined)
  if (pts.length < 2) return <div className="mx-dt__empty">采样还不够，像两次以上（间隔几小时，点一次「同步数据」记一个点）这里会画出这条作品的播放走势</div>
  const W = 760, H = 220, P = 46
  const maxV = Math.max(...pts.map(p => p.views || 0), 1)
  const x = (i: number) => P + (W - P * 2) * (i / (pts.length - 1))
  const y = (v: number) => H - P - (H - P * 2) * (v / maxV)
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.views || 0).toFixed(1)}`).join(' ')
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${H - P} L${x(0).toFixed(1)},${H - P} Z`
  return <div className="mx-dt__chart">
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="播放走势">
      <defs><linearGradient id="dtFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="#3FE0C8" stopOpacity="0.3"/><stop offset="100%" stopColor="#3FE0C8" stopOpacity="0"/>
      </linearGradient></defs>
      {[0.25, 0.5, 0.75].map(k => <line key={k} x1={P} x2={W - P} y1={P + (H - P * 2) * k} y2={P + (H - P * 2) * k} stroke="rgba(127,127,127,.14)" strokeDasharray="3 5"/>)}
      <path d={area} fill="url(#dtFill)"/>
      <path d={line} fill="none" stroke="#3FE0C8" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round"/>
      {pts.map((p, i) => <circle key={p.at} cx={x(i)} cy={y(p.views || 0)} r="3.4" fill="#0B1520" stroke="#3FE0C8" strokeWidth="1.8">
        <title>{`${localDay(p.at)}：播放 ${num(p.views)}`}</title>
      </circle>)}
    </svg>
    <div className="mx-dt__axis"><span>{localDay(pts[0].at)}</span><span>峰值 {num(maxV)}</span><span>{localDay(pts[pts.length - 1].at)}</span></div>
  </div>
}

/** 平台堆叠涨粉柱：由 overview 的 fans 序列或 works 的 timeline 生成。 */
function FansChart({works}: {works: Work[]}) {
  const [hover, setHover] = useState<number|null>(null)
  const byDay = useMemo(() => {
    const map = new Map<string, number>()
    for (const w of works) for (const s of (w.series || [])) {
      const day = localDay(s.at)
      map.set(day, (map.get(day) || 0) + ((s.likes || 0) + (s.comments || 0)))
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-14)
  }, [works])
  const max = Math.max(1, ...byDay.map(([, v]) => v))
  if (!byDay.length) return <div className="mx-empty" style={{padding: 20}}>同步几次后，这里会画出每天的互动走势</div>
  return <div className="mx-chart-wrap"><svg className="mx-fans-chart" viewBox={`0 0 300 120`} preserveAspectRatio="none" role="img" aria-label="近 14 天每日互动走势">
    {[0, 1, 2].map(i => <line key={i} x1="0" x2="300" y1={20 + i * 40} y2={20 + i * 40} stroke="rgba(63,224,200,.1)" strokeDasharray="3 4"/>)}
    {byDay.map(([, v], i) => {
      const w = 300 / byDay.length, h = (v / max) * 96
      return <rect key={i} x={i * w + 2} y={112 - h} width={Math.max(3, w - 4)} height={Math.max(2, h)} rx="2" fill="#3fe0c8" opacity={0.55 + 0.45 * (i / byDay.length)}/>
    })}
    {hover != null && byDay[hover] && <rect x={hover * (300 / byDay.length)} y={0} width={300 / byDay.length} height={120} fill="rgba(63,224,200,0.08)"/>}
    {byDay.map((_, i) => <rect key={'hit' + i} className="mx-chart-hit" x={i * (300 / byDay.length)} y={0} width={300 / byDay.length} height={120} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}/>)}
  </svg>
    {hover != null && byDay[hover] && <div className="mx-chart-tip" style={{left: `clamp(56px, ${((hover * (300 / byDay.length) + 300 / byDay.length / 2) / 300 * 100).toFixed(2)}%, calc(100% - 56px))`}}>
      <b>{byDay[hover][0].slice(5)}</b> · 互动 <b>{num(byDay[hover][1])}</b>
    </div>}
  </div>
}

/** 各平台每日净增的堆叠柱：悬停每一段能看到平台与数字。 */
const FAN_COLORS = ['#3fe0c8', '#6f8cff', '#ffb454', '#ff5d8f', '#a78bfa', '#94a3b8', '#f0b429', '#67e8f9']
function FanTrendChart({daily}: {daily: Overview['daily']}) {
  const [hover, setHover] = useState<number|null>(null)
  const rows = (daily || []).slice(-14)
  const totals = rows.map(row => Object.values(row.byPlatform).reduce((n, v) => n + v, 0))
  const max = Math.max(1, ...totals)
  if (!rows.some(row => Object.keys(row.byPlatform).length)) {
    return <div className="mx-empty" style={{padding: 20}}>还没有粉丝曲线。点右上角「采粉丝」，平台后台的历史会一起回填。</div>
  }
  const W = 720, H = 220, padT = 16, padB = 30
  const band = (W - 20) / Math.max(1, rows.length)
  return <div className="mx-chart-wrap"><svg className="mx-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="各平台每日净增粉丝">
    {[0, 1, 2, 3].map(i => {
      const y = padT + i * (H - padT - padB) / 3
      return <g key={i}><line x1="8" y1={y} x2={W - 8} y2={y} stroke="rgba(63,224,200,.1)" strokeDasharray="3 5"/><text x="2" y={y + 4} className="mx-chart__tick">{num(Math.round(max * (1 - i / 3)))}</text></g>
    })}
    {rows.map((row, index) => {
      let cursor = H - padB
      const bars = Object.entries(row.byPlatform).filter(([, v]) => v !== 0)
      return <g key={row.day}>
        {bars.map(([platform, value]) => {
          const h = Math.abs(value) / max * (H - padT - padB)
          cursor -= h
          return <rect key={platform} x={10 + index * band + band * 0.16} y={cursor} width={band * 0.68} height={Math.max(2, h)} fill={FAN_COLORS[platform.length % FAN_COLORS.length]} opacity={hover === index ? 1 : 0.88}/>
        })}
        <text x={10 + index * band + band / 2} y={H - 8} textAnchor="middle" className="mx-chart__tick">{row.day.slice(5)}</text>
      </g>
    })}
    {hover != null && rows[hover] && <rect x={10 + hover * band} y={padT} width={band} height={H - padT - padB} fill="rgba(255,255,255,0.05)"/>}
    {rows.map((row, index) => <rect key={'hit' + index} className="mx-chart-hit" x={10 + index * band} y={padT} width={band} height={H - padT - padB} fill="transparent" onMouseEnter={() => setHover(index)} onMouseLeave={() => setHover(null)}/>)}
  </svg>
    {hover != null && rows[hover] && <div className="mx-chart-tip" style={{left: `clamp(80px, ${((10 + hover * band + band / 2) / W * 100).toFixed(2)}%, calc(100% - 80px))`}}>
      <b>{rows[hover].day.slice(5)}</b> 合计 {num(totals[hover])}<br/>
      {Object.entries(rows[hover].byPlatform).filter(([, v]) => v !== 0).map(([platform, value]) => <i key={platform} style={{'--dot': FAN_COLORS[platform.length % FAN_COLORS.length]} as React.CSSProperties}>{platName(platform, [])} {value >= 0 ? '+' : ''}{value}</i>)}
    </div>}
  </div>
}

export function MonitorDesk() {
  const [tab, setTab] = useState<Tab>('overview')
  const [platforms, setPlatforms] = useState<Platform[]>(() => readCache<Platform[]>('mx-platforms') || [])
  const [accounts, setAccounts] = useState<Account[]>(() => readCache<Account[]>('mx-accounts') || [])
  const [works, setWorks] = useState<Work[]>(() => readCache<Work[]>('mo-works') || [])
  const [tasks, setTasks] = useState<Task[]>(() => readCache<Task[]>('mo-tasks') || [])
  const [overview, setOverview] = useState<Overview|null>(() => readCache<Overview|null>('mo-overview') ?? null)
  const [weekly, setWeekly] = useState<WeeklyResult|null>(null)
  const [active, setActive] = useState('')
  const [detailOpen, setDetailOpen] = useState(false)
  const [accountId, setAccountId] = useState('')
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [analysis, setAnalysis] = useState('')
  const [analyzing, setAnalyzing] = useState('')
  const [analyzedBy, setAnalyzedBy] = useState('')
  const [reviewWorkId, setReviewWorkId] = useState('')
  const [reviewAge, setReviewAge] = useState(7)
  const [reviewData, setReviewData] = useState<ReviewResult|null>(null)
  const [reviewBusy, setReviewBusy] = useState(false)
  const [workFilter, setWorkFilter] = useState('')
  const [workQuery, setWorkQuery] = useState('')
  const [workSort, setWorkSort] = useState<'new'|'views'|'likes'>('new')
  const [health, setHealth] = useState<Health|null>(() => readCache<Health|null>('mo-health') ?? null)
  const [spot, setSpot] = useState(0)
  const [rotating, setRotating] = useState(true)

  const load = useCallback(async () => {
    try {
      const [a, w, t] = await Promise.all([
        matrixApi<Account[]>('accounts'), matrixApi<Work[]>('works'), matrixApi<Task[]>('tasks').catch(() => [] as Task[]),
      ])
      setAccounts(a); setWorks(w); setTasks(t); setError('')
      writeCache('mx-accounts', a); writeCache('mo-works', w); writeCache('mo-tasks', t)
      if (!a.some(x => x.id === accountId)) setAccountId(a[0]?.id || '')
      if (!w.some(x => x.id === active)) setActive(w[0]?.id || '')
      if (!reviewWorkId && w[0]) setReviewWorkId(w[0].id)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }, [accountId, active, reviewWorkId])

  const loadHealth = useCallback(async () => {
    try { const h = await matrixApi<Health>('monitor/health'); setHealth(h); writeCache('mo-health', h) } catch { /* 自检失败不挡住其他页 */ }
  }, [])

  const loadDashboard = useCallback(async () => {
    try {
      const [o, wk] = await Promise.all([
        matrixApi<Overview>('monitor/overview').catch(() => null),
        matrixApi<WeeklyResult>('monitor/weekly').catch(() => null),
      ])
      if (o) { setOverview(o); writeCache('mo-overview', o) }
      if (wk) setWeekly(wk)
      await loadHealth()
    } catch { /* 大屏是增强视图，失败不影响作品管理 */ }
  }, [loadHealth])

  useEffect(() => {
    void matrixApi<Platform[]>('platforms').then(rows => { setPlatforms(rows); writeCache('mx-platforms', rows) }).catch(() => {})
    void load()
    void loadDashboard()
  }, [])

  useEffect(() => {
    const refresh = () => { void matrixApi<Account[]>('accounts').then(rows => { setAccounts(rows); writeCache('mx-accounts', rows) }).catch(() => {}) }
    window.addEventListener('emotioncut:matrix-accounts-changed', refresh)
    return () => window.removeEventListener('emotioncut:matrix-accounts-changed', refresh)
  }, [])

  const name = (id: string) => platName(id, platforms)
  /** 平台名 + 品牌 LOGO 的小徽标；展示在卡片标题、作品墙、详情弹层。 */
  const platTag = (id: string, text?: string) => (
    <span style={{display: 'inline-flex', alignItems: 'center', gap: 5, verticalAlign: 'middle'}}>
      <PlatformLogo platform={id} size={14}/>{text ?? name(id)}
    </span>
  )
  const work = works.find(w => w.id === active)
  // 发布文案存档：弹层打开时拉一次，按作品链接匹配当时的标题/话题/封面留底。
  const [archive, setArchive] = useState<ArchiveEntry[]>([])
  useEffect(() => {
    if (!detailOpen || !active) return
    let alive = true
    matrixApi<ArchiveEntry[]>('archive').then(r => { if (alive) setArchive(r) }).catch(() => { if (alive) setArchive([]) })
    return () => { alive = false }
  }, [detailOpen, active])
  // 详情弹层：同期对比（服务端按发布后第 N 天取上一期同龄数据）
  const [detailReview, setDetailReview] = useState<ReviewResult|null>(null)
  const [detailAge, setDetailAge] = useState(1)
  useEffect(() => {
    if (!detailOpen || !active) { setDetailReview(null); return }
    let alive = true
    matrixApi<ReviewResult>(`monitor/review/${active}?age=${detailAge}`)
      .then(r => { if (alive) setDetailReview(r) })
      .catch(() => { if (alive) setDetailReview(null) })
    return () => { alive = false }
  }, [detailOpen, active, detailAge])
  // 详情头部大数字：互动 = 赞+评+弹+藏+转；互动率基于播放
  const detailStats = useMemo(() => {
    if (!work) return null
    const m = work.metrics
    const inter = (m.likes || 0) + (m.comments || 0) + (m.danmaku || 0) + (m.favorites || 0) + (m.shares || 0)
    return {inter, rate: m.views ? `${(inter / m.views * 100).toFixed(1)}%` : '—'}
  }, [work])
  // 当前作品的发布文案存档：按作品链接匹配，一稿多平台各一条
  const copyEntries = useMemo(() => work ? archive.filter(r => r.url && r.url === work.url) : [], [archive, work])
  const copyOne = (row: ArchiveEntry) => {
    const text = [row.caption || row.title, (row.tags || []).map(t => '#' + t).join(' '), row.url || ''].filter(Boolean).join('\n')
    void navigator.clipboard.writeText(text).then(() => setNotice('发布文案已复制')).catch(() => setNotice('复制失败，请手动选中复制'))
  }
  const totals = useMemo(() => ({
    views: TOTAL(works, w => w.metrics.views),
    likes: TOTAL(works, w => w.metrics.likes),
    comments: works.reduce((n, w) => n + w.comments.length, 0),
    danmaku: TOTAL(works, w => w.metrics.danmaku),
  }), [works])
  const online = accounts.filter(a => a.lastCheck?.loggedIn).length
  const trend = useMemo(() => buildTrend(works), [works])
  const byPlatform = useMemo(() => platforms.map(p => {
    const rows = works.filter(w => w.platform === p.id)
    return {id: p.id, name: p.name, works: rows.length, views: TOTAL(rows, w => w.metrics.views)}
  }).filter(p => p.works).sort((a, b) => b.views - a.views), [platforms, works])
  const flow = useMemo(() => [...works].sort((a, b) => b.syncedAt.localeCompare(a.syncedAt)).slice(0, 6), [works])
  const maxPlatform = Math.max(1, ...byPlatform.map(p => p.views))
  /** 作品墙：按平台/关键词筛选，再按最新或数据排序。搜索只看标题与链接，不误伤数据。 */
  const wallWorks = useMemo(() => {
    const q = workQuery.trim().toLowerCase()
    const rows = works.filter(w => (!workFilter || w.platform === workFilter) && (!q || (w.title || '').toLowerCase().includes(q) || w.url.toLowerCase().includes(q)))
    const key = (w: Work) => workSort === 'views' ? (w.metrics.views || 0) : workSort === 'likes' ? (w.metrics.likes || 0) : 0
    return workSort === 'new' ? rows.sort((a, b) => b.syncedAt.localeCompare(a.syncedAt)) : rows.sort((a, b) => key(b) - key(a))
  }, [works, workFilter, workQuery, workSort])
  const wallPlatforms = useMemo(() => [...new Set(works.map(w => w.platform))], [works])
  /** 最近 7 天发布/同步过的作品：在总览里循环切换，直接对比各平台状态与数据。 */
  const recentWorks = useMemo(() => works
    .filter(w => Date.now() - Date.parse(w.syncedAt) <= 7 * 864e5)
    .sort((a, b) => b.syncedAt.localeCompare(a.syncedAt)), [works])
  useEffect(() => {
    if (!rotating || tab !== 'overview' || recentWorks.length < 2) return
    const id = window.setInterval(() => setSpot(index => index + 1), 6000)
    return () => window.clearInterval(id)
  }, [rotating, tab, recentWorks.length])

  const refreshAll = async () => { await load(); await loadDashboard() }

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label); setError(''); setNotice('')
    try { await action(); await refreshAll() } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const syncAll = () => run('all', async () => {
    const failed: string[] = []
    let ok = 0
    for (const w of works) {
      try { await matrixApi(`works/${w.id}/sync`, {method: 'POST'}); ok++ }
      catch (cause) { failed.push(`${w.title || w.url.slice(-10)}：${cause instanceof Error ? cause.message : String(cause)}`) }
    }
    if (failed.length) throw Error(`${ok}/${works.length} 条同步成功，${failed.length} 条失败。前三条原因：\n${failed.slice(0, 3).join('\n')}`)
    setNotice(`已同步 ${ok} 个作品`)
  })

  const deleteTaskRecord = async (id: string, label: string) => {
    if (!window.confirm(`删除任务记录「${label}」？只删本地记录，已回填的作品和封面不受影响。`)) return
    await run('del-task', () => matrixApi(`tasks/${id}`, {method: 'DELETE'}).then(() => loadHealth()))
  }

  const purgeTaskRecords = async () => {
    if (!window.confirm('清理所有「已发布 / 失败 / 已取消」的任务记录？排队和待你点发布的会保留，已回填的作品不受影响。')) return
    await run('purge', async () => {
      const result = await matrixApi<{removed: number}>('tasks/purge', {method: 'POST', body: '{"states":["done","failed","cancelled"]}'})
      setNotice(`已清理 ${result.removed} 条结束的任务记录。`)
      await loadHealth()
    })
  }

  const openAccountWindow = async (id: string) => {
    await run('open', async () => {
      await matrixApi(`accounts/${id}/open`, {method: 'POST'})
      setNotice('已打开账号窗口。扫码登录后回到这里点「检测全部账号」。')
      await loadHealth()
    })
  }

  const jumpToWork = (task: Task) => {
    const hit = works.find(w => (task.url && w.url === task.url) || (w.title && w.title === task.title))
    if (hit) setActive(hit.id)
    else if (task.title) { setWorkQuery(task.title.slice(0, 12)); setNotice('档案里没有同名作品，已按标题帮你筛一遍。') }
    setTab('works')
  }

  const collectFans = () => run('fans', async () => {
    const result = await matrixApi<Record<string, unknown>>('monitor/followers/collect', {method: 'POST', body: '{}'})
    setNotice('粉丝采集完成：' + Object.entries(result).map(([k, v]) => `${k} ${typeof v === 'object' ? '已更新' : v}`).join('、'))
  })

  const doReview = async (rows: Work[]) => {
    if (!rows.length) return
    setAnalyzing(rows.length === 1 ? rows[0].id : 'all'); setError(''); setNotice('')
    try {
      const payload = rows.map(w => ({
        title: w.title || w.url, platform: name(w.platform),
        account: accounts.find(a => a.id === w.accountId)?.alias || '已解绑',
        metrics: w.metrics, comments: w.comments.slice(0, 8).map(c => `${c.user || '匿名'}：${c.text}`),
        trend: (w.series || []).slice(-7).map(s => ({day: localDay(s.at), views: s.views, likes: s.likes, comments: s.comments})),
      }))
      const answer = await askAgent(
        '你是短视频运营数据分析师。只依据给出的真实数据判断，样本不足就明确说不足，绝不编造原因；不要输出 Markdown 代码块。',
        `以下是本账号自己作品的数据：\n${JSON.stringify(payload)}\n\n请输出复盘，分六段：\n1. 一句话结论\n2. 最好与最差的作品，点出具体数字做证据\n3. 可能原因：选题、开头、时长、剪辑、发布时间、封面\n4. 评论区信号：观众在问什么、卡在哪\n5. 下一条作品的 3 条可执行建议\n6. 还缺哪些数据才能判断得更准`,
      )
      setAnalysis(answer.text.trim())
      setAnalyzedBy(rows.length === 1 ? `《${rows[0].title || rows[0].url}》` : `${rows.length} 条作品`)
      setNotice(`Agent（${answer.agent}）已复盘${rows.length === 1 ? '这条作品' : '全部作品'}`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setAnalyzing('') }
  }

  const loadPeerReview = async (age = reviewAge) => {
    if (!reviewWorkId) return
    setReviewBusy(true); setError('')
    try { setReviewData(await matrixApi<ReviewResult>(`monitor/review/${reviewWorkId}?age=${age}`)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setReviewBusy(false) }
  }

  // ——— 总览页：大屏 ———
  const OverviewTab = () => {
    const latest = overview?.latest || works[0]
    const spotlight = recentWorks[spot % Math.max(1, recentWorks.length)] || latest
    const lastMetrics = spotlight?.metrics
    const awaiting = overview?.awaiting || tasks.filter(t => t.status === 'ready').map(t => ({id: t.id, platform: t.platform, title: t.title, status: t.status}))
    const spotTasks = spotlight ? tasks.filter(t => (spotlight.url && t.url === spotlight.url) || (spotlight.title && t.title === spotlight.title)) : []
    return <>
      <section className="mx-stats">
        <div className="mx-stat"><span>总播放</span><strong><Num value={overview?.totals.views ?? totals.views}/></strong><small>{works.length} 条作品</small></div>
        <div className="mx-stat"><span>总互动</span><strong><Num value={overview?.totals.interactions ?? (totals.likes + totals.comments)}/></strong><small>互动率 {overview?.totals.rate || '—'}</small></div>
        <div className="mx-stat"><span>7 日涨粉</span><strong><Num value={overview?.totals.fans7 ?? 0}/></strong><small>{overview?.fans.length || 0} 个平台</small></div>
        <div className="mx-stat"><span>待你点发布</span><strong style={{background: 'none', color: 'var(--mx-amber)', WebkitTextFillColor: 'var(--mx-amber)'}}>{awaiting.length}</strong><small>国内平台需手动确认</small></div>
        <div className="mx-stat"><span>在线账号</span><strong style={{background: 'none', color: 'var(--mx-mint)', WebkitTextFillColor: 'var(--mx-mint)'}}>{online}<small style={{display: 'inline', fontSize: 15, color: 'var(--mx-ink-3)'}}> / {accounts.length}</small></strong><small>独立登录态</small></div>
      </section>

      <div className="mx-hero">
        <div className="mx-hero-card">
          <div className="mx-hero-cover">
            {spotlight?.cover
              ? <img src={`/api/matrix/cover/file/${encodeURIComponent(spotlight.cover)}`} alt={spotlight.title || '作品封面'}/>
              : <span>{spotlight ? '作品\n封面' : '暂无\n作品'}</span>}
          </div>
          <div className="mx-hero-info">
            <span className="mx-eyebrow">{recentWorks.length > 1 ? 'LATEST 7 DAYS / 最近 7 天自动轮播' : 'LATEST / 最新一期'}</span>
            <h3>{spotlight?.title || '还没有登记作品'}</h3>
            <small>{spotlight ? `${name(spotlight.platform)} · ${new Date(spotlight.syncedAt).toLocaleString('zh-CN')}` : '到「作品档案」粘贴一条发布过的链接'}</small>
            <div className="mx-hero-chips">
              {(spotTasks.length ? spotTasks : awaiting.slice(0, 5)).map(t => <span className="mx-chip" data-state={t.status === 'ready' ? 'pending' : t.status === 'done' ? 'published' : undefined} key={t.id}>{platTag(t.platform)} {statusCn[t.status] || t.status}</span>)}
            </div>
            {recentWorks.length > 1 && <div className="mx-carousel" onMouseEnter={() => setRotating(false)} onMouseLeave={() => setRotating(true)}>
              <small>最近 7 天 {recentWorks.length} 条 · {rotating ? '每 6 秒自动切换' : '已暂停'}</small>
              <div>{recentWorks.slice(0, 8).map((w, index) => <button key={w.id} data-on={index === spot % recentWorks.length} title={w.title || w.url} onClick={() => { setSpot(index); setRotating(false) }}>{name(w.platform)}</button>)}</div>
            </div>}
            <div className="mx-hero-metrics">
              <div><strong><Num value={lastMetrics?.views}/></strong><small>播放</small></div>
              <div><strong><Num value={(lastMetrics?.likes || 0) + (lastMetrics?.comments || 0)}/></strong><small>互动</small></div>
              <div><strong>{num(lastMetrics?.favorites)}</strong><small>收藏</small></div>
            </div>
          </div>
        </div>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2><TrendingUp size={14}/>每日互动走势</h2><small>近 14 天</small></div>
          <div className="mx-panel__body" style={{paddingTop: 8}}>
            <FansChart works={works}/>
            {awaiting.length > 0 && <div className="mx-awaiting"><AlertTriangle size={14}/><span>有 {awaiting.length} 项停在「待你点发布」，最后一步由你确认</span><ChevronRight size={14}/></div>}
          </div>
        </section>
      </div>

      <div className="mx-board" style={{marginTop: 16}}>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2>平台分布</h2><small>{(overview?.platforms || byPlatform).length} 个平台</small></div>
          <div className="mx-panel__body">
            {(overview?.platforms || byPlatform).map(row => <div className="mx-bars" key={row.id}>
              <span style={{display: 'inline-flex', alignItems: 'center', gap: 5}}><PlatformLogo platform={row.id} size={13}/>{row.name}</span>
              <i><b style={{width: `${row.views / Math.max(1, ...(overview?.platforms || byPlatform).map(p => p.views)) * 100}%`}}/></i>
              <em>{num(row.views)}</em><small>{row.works} 条</small>
            </div>)}
            {!(overview?.platforms || byPlatform).length && <div className="mx-empty">登记作品后按平台排开。</div>}
          </div>
        </section>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2>近 7 天每日新增</h2><div className="mx-legend"><span data-kind="views">播放 / 天</span><span data-kind="engage">互动 / 天</span></div></div>
          <div className="mx-panel__body" style={{paddingTop: 6}}>
            <TrendChart days={trend.days} points={trend.points}/>
          </div>
        </section>
      </div>

      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2><TrendingUp size={14}/>各平台涨粉趋势</h2><small>今日 / 近 7 天 / 近 30 天 · 数据来自后台采集</small></div>
        <div className="mx-panel__body">
          <div className="mx-fan-cards">
            {(overview?.fans || []).map(fan => <article className="mx-fan-card" key={fan.platform}>
              <span><PlatformLogo platform={fan.platform} size={13}/>{platName(fan.platform, platforms)}</span>
              <strong><Num value={fan.total}/></strong>
              <div><em>今日 <b data-up={(fan.today || 0) >= 0 || undefined}>{fan.today === null ? '—' : `${fan.today >= 0 ? '+' : ''}${num(fan.today)}`}</b></em><em>7 天 <b data-up={(fan.gain7 || 0) >= 0 || undefined}>{fan.gain7 === null ? '—' : `${fan.gain7 >= 0 ? '+' : ''}${num(fan.gain7)}`}</b></em><em>30 天 <b data-up={(fan.gain30 || 0) >= 0 || undefined}>{fan.gain30 === null ? '—' : `${fan.gain30 >= 0 ? '+' : ''}${num(fan.gain30)}`}</b></em></div>
              <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="mx-fan-spark" aria-hidden="true">
                {(() => {
                  const vals = fan.series.map(s => s.net).filter((v): v is number => v != null)
                  const max = Math.max(1, ...vals.map(Math.abs))
                  const step = 100 / Math.max(1, vals.length)
                  return vals.map((v, i) => <rect key={i} x={i * step} y={12 - v / max * 10} width={Math.max(1, step - 0.6)} height={Math.max(1, Math.abs(v) / max * 10)} fill={v >= 0 ? '#3fe0c8' : '#ff6b81'} opacity={0.85}/>)
                })()}
              </svg>
            </article>)}
            {!overview?.fans.length && <div className="mx-empty">点右上角「采粉丝」后，这里会按平台显示今日 / 7 天 / 30 天净增。</div>}
          </div>
          <FanTrendChart daily={overview?.daily || []}/>
        </div>
      </section>

      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2><Sparkles size={14}/>数据分析</h2><small>每天按现有作品与粉丝数据自动重算 · {overview ? new Date(overview.generatedAt).toLocaleString('zh-CN') : '等待数据'}</small></div>
        <div className="mx-panel__body mx-insights">
          {(overview?.insights || ['还没有可分析的数据。先同步作品、采一次粉丝。']).map((line, index) => <p key={index}>{line}</p>)}
        </div>
      </section>

      {weekly && <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2><CalendarDays size={14}/>本周周报</h2><small>{weekly.from} ～ {weekly.to}</small></div>
        <div className="mx-panel__body"><pre className="mx-preline">{weekly.text}</pre></div>
      </section>}
    </>
  }

  // ——— 分发日历：点某天某条作品，直接跳到档案里对应作品 ———
  const CalendarTab = () => {
    const days = useMemo(() => {
      const today = new Date(); today.setHours(0, 0, 0, 0)
      return Array.from({length: 14}, (_, i) => {
        const d = new Date(today.getTime() + (i - 3) * 86400000)
        const key = localDay(d.toISOString())
        return {key, label: `${d.getMonth() + 1}/${d.getDate()}`, week: ['日','一','二','三','四','五','六'][d.getDay()], isToday: i === 3,
          items: tasks.filter(t => t.scheduledAt && localDay(t.scheduledAt) === key)}
      })
    }, [tasks])
    const pending = tasks.filter(t => t.status === 'ready' || t.status === 'failed')
    return <>
      <section className="mx-panel">
        <div className="mx-panel__head"><h2><CalendarDays size={14}/>分发计划</h2>
          <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
            <small>上周 ～ 未来两周 · 共 {tasks.length} 项，点作品进档案看数据</small>
            <button className="mx-btn mx-btn--ghost" disabled={busy === 'purge'} onClick={() => void purgeTaskRecords()}><Trash2 size={12}/>清理已结束记录</button>
          </div>
        </div>
        <div className="mx-panel__body">
          <div className="mx-cal">
            {days.map(d => <div className="mx-cal-day" key={d.key} data-today={d.isToday || undefined}>
              <b>{d.label} 周{d.week}{d.isToday ? ' · 今天' : ''}</b>
              {d.items.map(t => <button type="button" className="mx-cal-item" key={t.id} data-state={t.status} title="点开看这条作品在各平台的状态与数据" onClick={() => jumpToWork(t)}>
                {platTag(t.platform)} · {statusCn[t.status] || t.status}
                <small>{t.title.slice(0, 14)}</small>
              </button>)}
            </div>)}
          </div>
          {!tasks.some(t => t.scheduledAt) && <div className="mx-empty" style={{paddingTop: 16}}>还没有带定时的任务。到「多平台发布」建任务时填定时，这里就会排进日历。</div>}
        </div>
      </section>
      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2>待处理任务</h2><small>{pending.length} 项 · 点「打开发布页」完成平台最后一步</small></div>
        <div className="mx-panel__body">
          {pending.map(t => <div className="mx-task" data-state={t.status} key={t.id}>
            <header><b>{platTag(t.platform)} · {t.title}</b><span className="mx-tag" data-tone={t.status === 'failed' ? 'rose' : 'amber'}>{statusCn[t.status]}</span></header>
            {t.step && <p>{t.step}</p>}
            <div className="mx-task__ops">
              {(t.url || platforms.find(p => p.id === t.platform)?.createUrl)
                && <a className="mx-btn mx-btn--ghost" href={t.url || platforms.find(p => p.id === t.platform)?.createUrl} target="_blank" rel="noreferrer"><ExternalLink size={11}/>打开发布页</a>}
              <button className="mx-btn mx-btn--ghost" onClick={() => jumpToWork(t)}><ChevronRight size={11}/>看档案数据</button>
              <button className="mx-btn mx-btn--ghost" disabled={busy === 'del-task'} onClick={() => void deleteTaskRecord(t.id, t.title)}><Trash2 size={11}/>删除记录</button>
            </div>
          </div>)}
          {!pending.length && <div className="mx-empty">没有待你点或失败的任务。</div>}
        </div>
      </section>
    </>
  }

  // ——— 系统：真实自检 + 手动触发 + 记录清理 ———
  const SystemTab = () => (
    <>
      <section className="mx-panel">
        <div className="mx-panel__head"><h2><Activity size={14}/>自动化自检</h2><small>{health ? `更新于 ${new Date(health.updatedAt).toLocaleTimeString('zh-CN')}` : '读取中…'}</small></div>
        <div className="mx-panel__body">
          <div className="mx-health-grid">
            {(health?.checks || []).map(check => <article className="mx-health" key={check.id} data-status={check.status}>
              <header><i/>{check.label}<em>{check.status === 'ok' ? '正常' : check.status === 'warn' ? '待处理' : '异常'}</em></header>
              <p>{check.detail}</p>
            </article>)}
            {!health && <div className="mx-empty">正在检查浏览器窗口、登录态、采集器与数据新鲜度…</div>}
          </div>
        </div>
      </section>
      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2>手动操作</h2><small>每项都真实调用，执行结果会显示在页面上方</small></div>
        <div className="mx-panel__body mx-health-ops">
          <button className="mx-btn" disabled={busy === 'check'} onClick={() => run('check', async () => { await matrixApi('accounts/check-all', {method: 'POST'}); await load(); await loadHealth() })}>检测全部账号登录态</button>
          <button className="mx-btn" disabled={busy === 'fans'} onClick={() => void collectFans()}>采集各平台粉丝</button>
          <button className="mx-btn" disabled={busy === 'all' || !works.length} onClick={() => void syncAll()}>同步全部作品数据</button>
          <button className="mx-btn" disabled={busy === 'queue'} onClick={() => run('queue', async () => { const r = await matrixApi<unknown[]>('tasks/run-queue', {method: 'POST'}); setNotice(`发布队列已执行 ${Array.isArray(r) ? r.length : 0} 条`) })}>跑发布队列</button>
        </div>
      </section>
      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2>账号窗口状态</h2><small>{health?.windows.filter(w => w.open).length || 0}/{health?.windows.length || 0} 在线</small></div>
        <div className="mx-panel__body">
          {(health?.windows || []).map(w => <div className="mx-health-win" key={w.id}>
            <span style={{display: 'inline-flex', alignItems: 'center', gap: 7}}><PlatformLogo platform={w.platform} size={16}/><b>{name(w.platform)} · {w.alias}</b><small>端口 {w.port} · {w.open ? '窗口在线' : '窗口没开'}{w.loggedIn ? ' · 已登录' : ''}{w.error ? ` · ${w.error.slice(0, 50)}` : ''}</small></span>
            <button className="mx-btn mx-btn--ghost" disabled={busy === 'open'} onClick={() => void openAccountWindow(w.id)}><ExternalLink size={12}/>打开窗口</button>
          </div>)}
          {!health?.windows.length && <div className="mx-empty">还没有矩阵账号，到「矩阵分发」添加。</div>}
        </div>
      </section>
      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2>任务与数据记录</h2><small>{health?.totals.tasks || 0} 条任务 · {health?.totals.works || 0} 条作品</small></div>
        <div className="mx-panel__body">
          <div className="mx-health-ops">
            <button className="mx-btn mx-btn--ghost" disabled={busy === 'purge'} onClick={() => void purgeTaskRecords()}><Trash2 size={12}/>清理已结束任务（{tasks.filter(t => ['done', 'failed', 'cancelled'].includes(t.status)).length}）</button>
          </div>
          <p className="mx-note" style={{marginTop: 12}}>说明：定时只作为排队提醒，应用不会在后台无人值守点发布；这里展示的是当前真实状态与可手动触发的动作，不会显示虚构的「运行中」计划。</p>
        </div>
      </section>
    </>
  )

  // ——— 单期复盘：同龄对比 ———
  const ReviewTab = () => {
    const choose = (id: string) => { setReviewWorkId(id); setReviewData(null) }
    const pickAge = (age: number) => { setReviewAge(age); void loadPeerReview(age) }
    return <>
      <section className="mx-panel">
        <div className="mx-panel__head">
          <h2><BarChart3 size={14}/>单期复盘 · 同龄对比</h2>
          <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
            <select className="mx-input" style={{width: 'auto', padding: '6px 10px', fontSize: 12}} value={reviewWorkId} onChange={e => choose(e.target.value)}>
              {works.map(w => <option key={w.id} value={w.id}>{w.title || w.url}</option>)}
              {!works.length && <option value="">先登记作品</option>}
            </select>
            <button className="mx-btn mx-btn--primary" disabled={!reviewWorkId || reviewBusy} onClick={() => void loadPeerReview()}><Sparkles size={13}/>{reviewBusy ? '计算中…' : '生成复盘'}</button>
          </div>
        </div>
        <div className="mx-panel__body">
          <div style={{display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14}}>
            <span style={{fontSize: 12, color: 'var(--mx-ink-3)'}}>发布后第 N 天</span>
            <div className="mx-review-age">{[1, 3, 7].map(age => <button key={age} data-on={reviewAge === age || undefined} onClick={() => pickAge(age)}>{age} 天</button>)}</div>
          </div>
          {!reviewData && <div className="mx-empty" style={{textAlign: 'left'}}>选一条作品和天数，点「生成复盘」。它会用这条作品的历史快照和上一期同一天的真实数据对比——数据不够就直说，不硬比。</div>}
          {reviewData && <>
            <div className="mx-review-metrics">
              <div className="mx-stat"><span>播放</span><strong><Num value={reviewData.current.views}/></strong><small>第 {reviewData.ageDays} 天</small></div>
              <div className="mx-stat"><span>互动</span><strong>{num(reviewData.current.interactions)}</strong><small>互动率 {reviewData.current.rate}</small></div>
              <div className="mx-stat"><span>同龄对比</span><strong style={{background: 'none', color: 'var(--mx-lime)', WebkitTextFillColor: 'var(--mx-lime)'}}>{reviewData.peer?.ratio || '—'}</strong><small>{reviewData.peer ? reviewData.peer.title.slice(0, 12) : '暂无同龄数据'}</small></div>
              <div className="mx-stat"><span>期间涨粉</span><strong>{reviewData.fans.sum >= 0 ? '+' : ''}{num(reviewData.fans.sum)}</strong><small>{Object.keys(reviewData.fans.out).length} 个平台</small></div>
            </div>
            <pre className="mx-preline">{reviewData.text}</pre>
          </>}
        </div>
      </section>
      {reviewData?.peer && <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2>同龄回放</h2><small>第 {reviewData.ageDays} 天的播放对比</small></div>
        <div className="mx-panel__body">
          <div className="mx-replay"><span>这一期</span><div className="mx-replay-track"><i style={{width: '100%'}}/></div><b><Num value={reviewData.current.views}/></b></div>
          <div className="mx-replay"><span>上一期</span><div className="mx-replay-track"><i style={{width: `${Math.min(100, reviewData.peer.views / Math.max(1, reviewData.current.views) * 100)}%`, opacity: .5}}/></div><b><Num value={reviewData.peer.views}/></b></div>
        </div>
      </section>}
    </>
  }

  // ——— 转化漏斗：只用我们真实拿得到的数据，不编造站点收入 ———
  const FunnelTab = () => {
    const views = TOTAL(works, w => w.metrics.views)
    const engaged = works.filter(w => (w.metrics.views || 0) > 0).length
    const likes = TOTAL(works, w => w.metrics.likes) + TOTAL(works, w => w.metrics.comments)
    const comments = works.reduce((n, w) => n + w.comments.length, 0)
    const rows = [
      {label: '累计播放', value: views, max: Math.max(1, views)},
      {label: '读到数据的作品', value: engaged, max: Math.max(1, works.length), suffix: ` / ${works.length} 条`},
      {label: '点赞 + 评论', value: likes, max: Math.max(1, views)},
      {label: '已采集评论正文', value: comments, max: Math.max(1, likes)},
    ]
    return <>
      <section className="mx-panel">
        <div className="mx-panel__head"><h2><Filter size={14}/>作品数据漏斗</h2><small>只统计本账号可见的真实数据</small></div>
        <div className="mx-panel__body">
          {rows.map((r, i) => <div className="mx-replay" key={r.label} style={{padding: '14px 12px'}}>
            <span>{i + 1}. {r.label}</span>
            <div className="mx-replay-track" style={{height: 14}}><i style={{width: `${Math.min(100, r.value / r.max * 100)}%`, opacity: 1 - i * 0.15}}/></div>
            <b><Num value={r.value}/>{r.suffix || ''}</b>
          </div>)}
        </div>
      </section>
      <div className="mx-note" style={{marginTop: 14}}>
        <AlertTriangle size={15}/><span>这一版漏斗到「评论正文」为止。注册/付费/收入需要接你自己的站点接口（麦田的 <code>site.mjs</code> 方案），目前没有接，所以不显示、也不编造这部分数字。</span>
      </div>
      <section className="mx-panel" style={{marginTop: 16}}>
        <div className="mx-panel__head"><h2>各平台互动率</h2></div>
        <div className="mx-panel__body">
          {byPlatform.map(p => {
            const rows = works.filter(w => w.platform === p.id)
            const v = TOTAL(rows, w => w.metrics.views), e = TOTAL(rows, w => w.metrics.likes) + TOTAL(rows, w => w.metrics.comments)
            return <div className="mx-bars" key={p.id}><span style={{display: 'inline-flex', alignItems: 'center', gap: 5}}><PlatformLogo platform={p.id} size={13}/>{p.name}</span><i><b style={{width: `${v ? Math.min(100, e / v * 100 * 10) : 0}%`}}/></i><em>{v ? (e / v * 100).toFixed(2) + '%' : '—'}</em><small>{num(v)} 播放</small></div>
          })}
          {!byPlatform.length && <div className="mx-empty">登记并同步作品后有数据。</div>}
        </div>
      </section>
    </>
  }

  return <main data-mx>
    <header className="mx-head">
      <div>
        <span className="mx-eyebrow">Signal Desk / 作品监控</span>
        <h1>矩阵数据台 <em>· {TABS.find(t => t.id === tab)?.label}</em></h1>
        <p>登记你已发布的作品链接，用对应账号的登录态同步公开数据与评论。只读取本账号可见的内容，不采集他人作品。</p>
      </div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-end'}}>
        <AgentModelPicker/>
        <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
          <button className="mx-btn" disabled={busy === 'fans'} onClick={() => void collectFans()}><RefreshCw size={14} className={busy === 'fans' ? 'spin' : ''}/>采粉丝</button>
          <button className="mx-btn" disabled={!works.length || busy === 'all'} onClick={() => void syncAll()}><RefreshCw size={14} className={busy === 'all' ? 'spin' : ''}/>全部同步</button>
          <button className="mx-btn mx-btn--primary" disabled={!works.length || analyzing === 'all'} onClick={() => void doReview(works)}><Sparkles size={14}/>{analyzing === 'all' ? '复盘生成中…' : 'Agent 复盘'}</button>
        </div>
      </div>
    </header>

    <nav className="mx-tabs" aria-label="监控台视图">
      {TABS.map(t => { const Icon = t.icon; return <button className="mx-tab" key={t.id} data-on={tab === t.id || undefined} onClick={() => setTab(t.id)}><Icon size={14}/>{t.label}</button> })}
    </nav>

    {error && <div className="mx-note" data-tone="error" style={{marginBottom: 14}}><AlertTriangle size={15}/><span>{error}</span></div>}
    {notice && <div className="mx-note" style={{marginBottom: 14}}><Activity size={15}/><span>{notice}</span></div>}

    {tab === 'overview' && <OverviewTab/>}
    {tab === 'calendar' && <CalendarTab/>}
    {tab === 'review' && <ReviewTab/>}
    {tab === 'funnel' && <FunnelTab/>}
    {tab === 'system' && <SystemTab/>}

    {tab === 'works' && <>
      <div className="mx-wall-bar">
        <input className="mx-input" placeholder="搜标题 / 链接" value={workQuery} onChange={e => setWorkQuery(e.target.value)} aria-label="搜索作品"/>
        <div className="mx-wall-filters">
          <button className="mx-wall-filter" data-on={!workFilter || undefined} onClick={() => setWorkFilter('')}>全部 <em>{works.length}</em></button>
          {wallPlatforms.map(id => <button className="mx-wall-filter" key={id} data-on={workFilter === id || undefined} onClick={() => setWorkFilter(id)}>{platTag(id)} <em>{works.filter(w => w.platform === id).length}</em></button>)}
        </div>
        <select className="mx-input" value={workSort} onChange={e => setWorkSort(e.target.value as typeof workSort)} aria-label="排序">
          <option value="new">最新登记</option><option value="views">播放最高</option><option value="likes">点赞最高</option>
        </select>
      </div>

      {!wallWorks.length && <div className="mx-panel"><div className="mx-empty" style={{padding: 40}}>
        {works.length ? '没有符合筛选条件的作品。换个平台或清空搜索。' : '还没有登记作品。点右上角「+ 登记作品」粘贴一条发布过的链接，作品就会出现在这里。'}
      </div></div>}

      {wallWorks.length > 0 && <div className="mx-wall">
        {wallWorks.map(w => {
          const synced = w.metrics.views !== null
          return <article className="mx-wall-card" key={w.id} data-on={w.id === active || undefined} title="点开看这条作品的详细数据" onClick={() => { setActive(w.id); setDetailOpen(true) }}>
            <div className={`mx-wall-cover mx-wall-cover--${w.platform}`}>
              {w.cover
                ? <img src={`/api/matrix/cover/file/${encodeURIComponent(w.cover)}`} alt={w.title || '作品封面'}/>
                : <span>{/第?\s*(\d+)/.exec(w.title)?.[1] || (w.title || '影').slice(0, 1)}</span>}
              <b className="mx-wall-badge">{platTag(w.platform)}</b>
              <em className="mx-wall-views">{synced ? <><Num value={w.metrics.views}/> 播放</> : '待同步'}</em>
              <span className="mx-wall-check" data-on={synced || undefined}>{synced ? <Check size={12}/> : <Clock size={12}/>}</span>
            </div>
            <div className="mx-wall-info">
              <h4 title={w.title || w.url}>{w.title || w.url}</h4>
              <div className="mx-wall-meta">
                <span>赞 {w.metrics.likes === null ? '—' : num(w.metrics.likes)}</span>
                <span>评 {w.comments.length || '—'}</span>
                <span>{localDay(w.syncedAt)}</span>
              </div>
              <div className="mx-wall-ops">
                <button title="同步数据与评论" onClick={e => { e.stopPropagation(); void run(w.id, () => matrixApi(`works/${w.id}/sync`, {method: 'POST'})) }}><RefreshCw size={11} className={busy === w.id ? 'spin' : ''}/>同步</button>
                <button title="让 Agent 复盘这条" onClick={e => { e.stopPropagation(); void doReview([w]) }}><Sparkles size={11}/>复盘</button>
                <a href={w.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} title="打开平台作品页"><ExternalLink size={11}/></a>
              </div>
            </div>
          </article>
        })}
      </div>}



      <details className="mx-wall-fold">
        <summary><TrendingUp size={13}/>数据趋势与统计 <em>近 7 天涨幅 / 采集流水 / 平台分布 / Agent 复盘</em></summary>
        <div className="mx-board" style={{marginTop: 14}}>
          <section className="mx-panel">
            <div className="mx-panel__head"><h2>近 7 天每日新增</h2><div className="mx-legend"><span data-kind="views">播放 / 天</span><span data-kind="engage">互动 / 天</span></div></div>
            <div className="mx-panel__body" style={{paddingTop: 6}}>
              <TrendChart days={trend.days} points={trend.points}/>
              <p className="mx-chart__hint">某条作品当天和前一天都有同步读数，才算进那天的增量；刚登记的头一天会是 0，属正常。</p>
            </div>
          </section>
          <section className="mx-panel">
            <div className="mx-panel__head"><h2>采集流水</h2><small>最近 {flow.length} 条</small></div>
            <div className="mx-panel__body" style={{maxHeight: 300, overflowY: 'auto'}}>
              {!flow.length && <div className="mx-empty">还没有采集记录。登记作品后点同步。</div>}
              {flow.map(row => <div className="mx-flow" key={row.id}>
                <i data-empty={(!row.metrics.views && row.comments.length === 0) || undefined}/>
                <div><b>{platTag(row.platform)} · {row.title || row.url}</b>
                  <small>{row.metrics.views === null ? '还没读到播放数据' : `${num(row.metrics.views)} 播放 · ${row.comments.length} 条评论`}</small></div>
                <em>{new Date(row.syncedAt).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})}</em>
              </div>)}
            </div>
          </section>
        </div>
        <div className="mx-board mx-board--split" style={{marginTop: 16}}>
          <section className="mx-panel">
            <div className="mx-panel__head"><h2>平台分布</h2><small>{byPlatform.length} 个平台</small></div>
            <div className="mx-panel__body">
              {!byPlatform.length && <div className="mx-empty">登记作品后这里会按平台排开。</div>}
              {byPlatform.map(row => <div className="mx-bars" key={row.id}><span style={{display: 'inline-flex', alignItems: 'center', gap: 5}}><PlatformLogo platform={row.id} size={13}/>{row.name}</span><i><b style={{width: `${row.views / maxPlatform * 100}%`}}/></i><em>{num(row.views)}</em><small>{row.works} 条</small></div>)}
            </div>
          </section>
          <section className="mx-panel">
            <div className="mx-panel__head"><h2><Bot size={14}/>Agent 复盘建议</h2><small>{analysis ? `基于${analyzedBy}` : '基于真实数据'}</small></div>
            <div className="mx-panel__body">
              {!analysis && <div className="mx-empty" style={{textAlign: 'left', padding: '8px 2px'}}>点右上角「Agent 复盘」，它会读播放、点赞、评论正文和每天的读数变化，指出哪条能复用、下一条该改什么。<br/><br/>样本少于 3 条时它会直接说数据不够，不会编原因。</div>}
              {analysis && <pre className="mx-agent__text">{analysis}</pre>}
            </div>
          </section>
        </div>
      </details>

      <details className="mx-wall-fold">
        <summary><Plus size={13}/>登记作品 <em>粘贴一条已发布链接，作品会出现在上方墙里</em></summary>
        <section className="mx-panel" style={{marginTop: 14}}>
          <div className="mx-panel__body" style={{display: 'grid', gridTemplateColumns: '190px 1fr 1fr auto', gap: 10, alignItems: 'end'}}>
            <label className="mx-field" style={{margin: 0}}><span>账号</span>
              <select className="mx-input" value={accountId} onChange={e => setAccountId(e.target.value)}>
                {!accounts.length && <option value="">先添加账号</option>}
                {accounts.map(a => <option key={a.id} value={a.id}>{name(a.platform)} · {a.alias}</option>)}
              </select>
            </label>
            <label className="mx-field" style={{margin: 0}}><span>作品链接</span><input type="text" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://www.douyin.com/video/..."/></label>
            <label className="mx-field" style={{margin: 0}}><span>标题（可选）</span><input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="方便自己认"/></label>
            <button className="mx-btn mx-btn--primary" style={{padding: '11px 16px'}} disabled={!accountId || !url.trim() || busy === 'add'} onClick={() => run('add', async () => {
              await matrixApi<Work>('works', {method: 'POST', body: JSON.stringify({accountId, url: url.trim(), title: title.trim()})})
              setUrl(''); setTitle(''); setNotice('已登记。点作品卡上的「同步」拉取数据与评论。')
            })}><Plus size={14}/>登记</button>
          </div>
        </section>
      </details>
    </>}

    {detailOpen && work && detailStats && <div className="mx-viewer" role="dialog" aria-modal="true" aria-label="作品详情" onClick={() => setDetailOpen(false)}>
      <div className="mx-viewer__bar">
        <b>{platTag(work.platform)} · 作品详情</b>
        <button className="mx-btn mx-btn--ghost" onClick={() => setDetailOpen(false)}><X size={14}/>关闭</button>
      </div>
      <div className="mx-viewer__stage mx-viewer__stage--detail" onClick={event => event.stopPropagation()}>
        <header className="mx-dt__head">
          <div className="mx-dt__title">
            <h3>{work.title || work.url}</h3>
            <small>{name(work.platform)} · {accounts.find(a => a.id === work.accountId)?.alias || '已解绑账号'}{work.series?.length ? ` · 首次采样 ${new Date(work.series[0].at).toLocaleDateString('zh-CN')}` : ''} · 最近同步 {new Date(work.syncedAt).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'})}</small>
          </div>
          <a className="mx-btn mx-btn--ghost" href={work.url} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}><ExternalLink size={12}/>打开作品页</a>
        </header>
        <div className="mx-dt__hero">
          <div className="mx-dt__big">
            <div><span>播放</span><strong>{num(work.metrics.views)}</strong></div>
            <div><span>互动</span><strong>{num(detailStats.inter)}</strong></div>
            <div><span>互动率</span><strong>{detailStats.rate}</strong></div>
            <div><span>评论</span><strong>{num(work.metrics.comments)}</strong></div>
          </div>
          <div className="mx-dt__minor">点赞 {num(work.metrics.likes)} · 弹幕 {num(work.metrics.danmaku)}{work.metrics.favorites !== undefined ? ` · 收藏 ${num(work.metrics.favorites)}` : ''}{work.metrics.shares !== undefined ? ` · 分享 ${num(work.metrics.shares)}` : ''}</div>
        </div>
        <div className="mx-dt__ops">
          <button className="mx-btn mx-btn--ghost" onClick={() => void run(work.id, () => matrixApi(`works/${work.id}/sync`, {method: 'POST'}))}><RefreshCw size={12} className={busy === work.id ? 'spin' : ''}/>同步数据</button>
          <button className="mx-btn mx-btn--ghost" onClick={() => void doReview([work])} disabled={analyzing === work.id}><Sparkles size={12}/>{analyzing === work.id ? '正在读评论…' : '让 Agent 复盘这条'}</button>
          <button className="mx-btn mx-btn--ghost" onClick={() => { if (confirm('取消监控这条作品？已采集的评论会一起删掉。')) { setDetailOpen(false); void run('del', () => matrixApi(`works/${work.id}`, {method: 'DELETE'})) } }}><Trash2 size={12}/>取消监控</button>
        </div>
        {analysis && analyzedBy === work.id && <pre className="mx-review-text">{analysis}</pre>}
        <h4 className="mx-dt__h">发布文案 <small>发出去那一刻的标题/话题留底，任务清了也在这里</small></h4>
        <div className="mx-dt__copy">
          {!copyEntries.length && <div className="mx-empty">这条作品还没有文案存档。在「多平台发布」建任务发布后会自动留底。</div>}
          {copyEntries.map(row => <div className="mx-dt__copyrow" key={row.id}>
            <header>
              <b>{platTag(row.platform)}{row.alias ? ` · ${row.alias}` : ''}</b>
              <span>{statusCn[row.status] || row.status}{row.publishedAt ? ` · ${new Date(row.publishedAt).toLocaleDateString('zh-CN')}` : ''}</span>
              <button className="mx-linkbtn" onClick={() => copyOne(row)}>复制文案</button>
            </header>
            <pre>{row.caption || row.title}</pre>
            {(row.tags || []).length > 0 && <i className="mx-dt__copytags">{row.tags.map(t => '#' + t).join('  ')}</i>}
          </div>)}
        </div>
        <h4 className="mx-dt__h">播放走势</h4>
        <DetailTrend series={work.series || []}/>
        <div className="mx-dt__review">
          <h4 className="mx-dt__h">同期对比 <small>拿上一期发布后同样天数的数据比</small></h4>
          <div className="mx-dt__ages">{[1, 3, 7].map(a => <button key={a} type="button" data-on={detailAge === a || undefined} onClick={() => setDetailAge(a)}>第 {a} 天</button>)}</div>
          {detailReview ? <div className="mx-dt__peer">
            <div><span>播放</span><strong>{num(detailReview.current.views)}</strong></div>
            <div><span>互动</span><strong>{num(detailReview.current.interactions)}</strong></div>
            <div><span>互动率</span><strong>{detailReview.current.rate}</strong></div>
            {detailReview.peer ? <div><span>上一期同期</span><strong>{num(detailReview.peer.views)}</strong><em>{detailReview.peer.ratio}</em></div> : <div><span>上一期同期</span><strong className="mx-dt__na">还没有</strong></div>}
            <div><span>期间涨粉</span><strong>{detailReview.fans.sum >= 0 ? '+' : ''}{num(detailReview.fans.sum)}</strong></div>
          </div> : <div className="mx-dt__empty" style={{marginTop: 8}}>同期数据读取中或暂缺，跑过「同步数据」和「采粉丝」后这里就有对比。</div>}
        </div>
        <h4 className="mx-dt__h">数据同步记录 <small>每点一次「同步数据」记一条，也是这条作品的成长过程</small></h4>
        <div className="mx-dt__log">
          {!(work.series || []).length && <div className="mx-empty">还没有采样。点上方「同步数据」开始记录。</div>}
          {[...(work.series || [])].reverse().slice(0, 10).map(s => <div className="mx-dt__logrow" key={s.at}>
            <span>{new Date(s.at).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'})}</span>
            <b>播放 {num(s.views)}</b>
            <i>赞 {num(s.likes)} · 评 {num(s.comments)}{s.favorites ? ` · 藏 ${num(s.favorites)}` : ''}{s.shares ? ` · 转 ${num(s.shares)}` : ''}</i>
          </div>)}
        </div>
        <h4 className="mx-dt__h">评论正文 <small>{work.comments.length} 条</small></h4>
        <div className="mx-viewer__comments">
          {!work.comments.length && <div className="mx-empty">还没有评论数据。点「同步数据」拉一次。</div>}
          {work.comments.map(comment => <div className="mx-comment" key={comment.id}>
            <header><b>{comment.user || '匿名'}</b><span>{comment.like ? `${comment.like} 赞` : ''}</span><span style={{marginLeft: 'auto'}}>{comment.at ? new Date(comment.at).toLocaleDateString('zh-CN') : ''}</span></header>
            <p>{comment.text}</p>
          </div>)}
        </div>
      </div>
      <small className="mx-viewer__hint">点这里或右上角「关闭」即可返回作品档案。</small>
    </div>}
  </main>
}
