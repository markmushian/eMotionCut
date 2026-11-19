/**
 * /styles → Motion 模板。
 *
 * 以前这一页是「一堆卡片 + 一个参数清单弹窗」：分类只按目录分，点卡片直接跳走，
 * 和旁边的风格库是两套手感。现在改成和风格库同一套：用途分类在最上面（点击真的筛），
 * 卡片用同一套海报/排版，点开就在那一行下面展开详情（预览、你要给、适合做、CTA、署名）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronRight, FolderPlus, LayoutGrid, Monitor, RefreshCw, Search, ShieldCheck, Smartphone, Sparkles, X } from 'lucide-react'
import { toast } from 'sonner'
import { useNavigate } from 'react-router'
import { generalEditHref, rowEndIndex } from '@/workspace/style-library/style-library'
import { StyleDetail } from '@/workspace/style-library/StyleLibrary'
import {
  asLibraryStyle, invalidateMotionTemplates, MOTION_PURPOSES, motionPurpose, motionStyleId,
  useMotionTemplates, type MotionTemplate,
} from '@/motion/motion-templates'
import {useColumns} from '@/motion/use-columns'
import '@/shell/motion-library.css'

type Manifest = { name?: string; theme?: string; desc?: string; engine?: string; kinds?: Record<string, { label?: string; use?: string }> }
type OnlineTemplate = { name?: string; title?: string; category: string; ratio: string; href: string; video?: string; preview?: string; poster?: string; duration?: string }

const KNOWN_ROOT = 'E:\\AI小程序\\AI博主创作工具\\AI博主工作台\\动效模板'
const size = (bytes: number) => bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`

export default function MotionLibrary() {
  const navigate = useNavigate()
  const [version, setVersion] = useState(0)
  const motion = useMotionTemplates(version)
  const items = motion.items
  const [query, setQuery] = useState('')
  const [layout, setLayout] = useState<'all' | 'landscape' | 'portrait'>('all')
  const [purpose, setPurpose] = useState<string>('全部')
  const [openId, setOpenId] = useState<string>()
  const [manifest, setManifest] = useState<Manifest>()
  const [root, setRoot] = useState(KNOWN_ROOT)
  const [importing, setImporting] = useState(false)
  const [online, setOnline] = useState<OnlineTemplate[]>([])
  const [installing, setInstalling] = useState('')
  const grid = useRef<HTMLUListElement>(null)
  const columns = useColumns(grid)

  useEffect(() => {
    void fetch('/api/motion/online-catalog').then(r => r.json()).then(body => {
      if (body.ok && Array.isArray(body.result)) setOnline(body.result)
    }).catch(() => {})
  }, [])

  const counts = useMemo(() => {
    const map: Record<string, number> = {全部: items.length}
    for (const name of MOTION_PURPOSES) map[name] = 0
    for (const item of items) map[motionPurpose(item)] = (map[motionPurpose(item)] || 0) + 1
    return map
  }, [items])

  const visible = useMemo(() => items.filter(item => {
    const text = `${item.template.name || ''} ${item.fileName} ${motionPurpose(item)}`
    const hit = text.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
    const layoutHit = layout === 'all' || (layout === 'portrait' ? item.template.layout === 'portrait' : item.template.layout !== 'portrait')
    return hit && layoutHit && (purpose === '全部' || motionPurpose(item) === purpose)
  }), [items, query, layout, purpose])

  const open = openId ? items.find(item => item.sha256 === openId) : undefined

  // 详情要「有详细介绍」：参数清单里有模板自己的 desc 和控件名，点开时读一次。
  useEffect(() => {
    setManifest(undefined)
    if (!open) return
    let live = true
    void fetch(`/api/motion/templates/${open.sha256}/manifest`).then(r => r.json()).then(body => {
      if (live && body.ok) setManifest(body.result as Manifest)
    }).catch(() => {})
    return () => { live = false }
  }, [open?.sha256])

  const styleOf = (item: MotionTemplate) => {
    const base = asLibraryStyle(item)
    if (!open || open.sha256 !== item.sha256 || !manifest) return base
    const controls = Object.values(manifest.kinds || {}).map(kind => kind.label).filter((label): label is string => Boolean(label))
    return {
      ...base,
      description_zh: manifest.desc?.trim() || base.description_zh,
      uses: [...new Set([...base.uses, ...controls.slice(0, 4)])],
    }
  }

  const detailStyle = open ? styleOf(open) : undefined
  const openIndex = open ? visible.findIndex(item => item.sha256 === open.sha256) : -1
  const detailAfter = openIndex >= 0 ? rowEndIndex(openIndex, columns, visible.length) : -1

  const toggle = (item: MotionTemplate) => setOpenId(current => current === item.sha256 ? undefined : item.sha256)
  const go = (direction: 'previous' | 'next') => {
    if (openIndex < 0) return
    const next = visible[direction === 'previous' ? openIndex - 1 : openIndex + 1]
    if (next) setOpenId(next.sha256)
  }

  const importRoot = async () => {
    setImporting(true)
    try {
      const response = await fetch('/api/motion/roots', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ root }) })
      const body = await response.json()
      if (!response.ok || !body.ok) throw Error(body.error || '导入失败')
      const count = body.result.catalog.packages.length
      const failed = body.result.failures?.length || 0
      invalidateMotionTemplates()
      setVersion(value => value + 1)
      toast.success(`已登记 ${count} 个动效模板${failed ? `，${failed} 个需要检查` : ''}`)
    } catch (error) { toast.error(error instanceof Error ? error.message : '导入失败') }
    finally { setImporting(false) }
  }

  const useOnline = async (item: OnlineTemplate) => {
    setInstalling(item.href)
    try {
      const response = await fetch('/api/motion/online-install', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ href: item.href }) })
      const body = await response.json()
      if (!response.ok || !body.ok) throw Error(body.error || '模板接入失败')
      toast.success(body.result.cached ? '模板已就绪，正在进入素材配置' : '模板已自动接入，正在进入素材配置')
      navigate(generalEditHref(motionStyleId(body.result.template as MotionTemplate)))
    } catch (error) { toast.error(error instanceof Error ? error.message : '模板接入失败', { duration: 7000 }) }
    finally { setInstalling('') }
  }

  const landscape = items.filter(item => item.template.layout !== 'portrait').length
  const portrait = items.length - landscape
  const bytes = items.reduce((sum, item) => sum + item.bytes, 0)

  return <main className="motion-terminal">
    <header className="motion-terminal__hero">
      <div>
        <p className="ec-kicker">42 · MOTION ASSET EXCHANGE</p>
        <h1 className="ec-grad-title">动效模板库 <span>{items.length}</span></h1>
        <p>已验证的 MotionGit 模板会在本地执行，受保护内容与个人授权不会被解包或上传。点开任意模板，先看它的样片、适用场景和动效控件，再决定用不用。</p>
        <small className="motion-credit">模板由博主 <b>@阿杜 Next</b> 分享提供 · 特此致谢</small>
      </div>
      <div className="motion-terminal__metrics">
        <span><b>{landscape}</b><small>LANDSCAPE</small></span>
        <span><b>{portrait}</b><small>PORTRAIT</small></span>
        <span><b>{(bytes / 1024 ** 2).toFixed(0)}</b><small>MB LINKED</small></span>
      </div>
    </header>

    <section className="motion-toolbar">
      <label><Search aria-hidden /><input value={query} onChange={event => { setQuery(event.target.value); setOpenId(undefined) }} placeholder="搜索模板、用途或目录名" /></label>
      <div className="motion-layout-filter">
        <button aria-pressed={layout === 'all'} onClick={() => { setLayout('all'); setOpenId(undefined) }}><LayoutGrid />全部</button>
        <button aria-pressed={layout === 'landscape'} onClick={() => { setLayout('landscape'); setOpenId(undefined) }}><Monitor />横屏</button>
        <button aria-pressed={layout === 'portrait'} onClick={() => { setLayout('portrait'); setOpenId(undefined) }}><Smartphone />竖屏</button>
      </div>
    </section>

    <nav className="motion-categories" aria-label="按用途筛选">
      {['全部', ...MOTION_PURPOSES].map(name => <button key={name} aria-pressed={purpose === name}
        onClick={() => { setPurpose(name); setOpenId(undefined) }}>
        {name}<span>{counts[name] || 0}</span>
      </button>)}
    </nav>

    <details className="motion-import-details">
      <summary>登记本地模板目录<small>只建索引，不复制原包</small></summary>
      <div className="motion-import">
        <FolderPlus aria-hidden />
        <div><b>模板目录</b><small>扫到 .motion 包就登记，路径可以随时换</small></div>
        <input value={root} onChange={event => setRoot(event.target.value)} aria-label="Motion 模板目录" />
        <button type="button" onClick={() => void importRoot()} disabled={importing}>{importing ? '正在验证…' : '扫描并导入'}</button>
      </div>
    </details>

    {!motion.ready ? <div className="motion-empty">正在读取动效索引…</div>
      : visible.length ? <ul ref={grid} className="sl-grid" data-motion-grid>
        {visible.flatMap((item, index) => {
          const style = styleOf(item)
          const card = <li key={item.sha256} className="sl-card" data-open={openId === item.sha256 || undefined}>
            <button type="button" className="sl-card__button" aria-expanded={openId === item.sha256}
              onClick={() => toggle(item)}>
              <span className="sl-thumb">
                <img src={style.poster} alt="" loading="lazy" decoding="async" draggable={false} />
                <span className="sl-motion-badge">动效模板</span>
              </span>
              <span className="sl-card__name">{style.name_zh}</span>
              <span className="sl-card__line">{style.description_zh}</span>
            </button>
          </li>
          return index === detailAfter ? [card, <li key="detail" className="sl-grid__detail">
            {detailStyle && open && <StyleDetail style={detailStyle} id="motion-detail" actionLabel="用这个模板新建通用工程"
              actionDisabled={false} hasPrevious={openIndex > 0} hasNext={openIndex < visible.length - 1}
              onGo={go} onClose={() => setOpenId(undefined)}
              onAction={() => navigate(generalEditHref(motionStyleId(open)))} />}
          </li>] : [card]
        })}
      </ul> : <div className="motion-empty">
        {items.length ? <><AlertTriangle />这一类里还没有模板，换个用途或清掉搜索看看</> : <><CheckCircle2 />输入上方目录，扫描并导入现有 .motion 模板</>}
      </div>}

    <section className="motion-online-more">
      <header><span><Sparkles size={14} aria-hidden />MotionGit 在线模板</span><small>本地还没有的模板可以直接接入，第一次用要授权一次</small></header>
      <div className="motion-online__grid">
        {online.slice(0, 6).map(item => {
          const name = item.title || item.name || '未命名模板', src = item.preview || item.video, busy = installing === item.href
          return <article key={item.href}>
            {src ? <video src={src} poster={item.poster} muted loop playsInline preload="none"
              onMouseEnter={event => void event.currentTarget.play()}
              onMouseLeave={event => { event.currentTarget.pause(); event.currentTarget.currentTime = 0 }} />
              : <img src={item.poster} alt="" loading="lazy" />}
            <div><span>{item.category} · {item.ratio}{item.duration ? ` · ${item.duration}` : ''}</span><b>{name}</b>
              <button disabled={!!installing} onClick={() => void useOnline(item)}>{busy ? '正在接入…' : '接入并使用 →'}</button>
            </div>
          </article>
        })}
      </div>
      <p><ShieldCheck size={12} aria-hidden /> 接入的模板会缓存到本机；再次点击直接打开，不走网络。</p>
    </section>
  </main>
}
