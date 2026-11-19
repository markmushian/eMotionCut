/**
 * 新建通用剪辑 — the entrance, redrawn as three ruled steps instead of a 1600×900 stage with a
 * cover-flow ring: ① what you have ② what it is for ③ which style. The data (entries, purposes,
 * posters, Pro gating) is unchanged; only the composition is new.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Mic2, Sparkles, Type, Video } from 'lucide-react'
import { useNavigate } from 'react-router'
import { LIBRARY_STYLES } from '@/workspace/style-library/library-styles'
import { rowEndIndex, styleById } from '@/workspace/style-library/style-library'
import { StyleDetail } from '@/workspace/style-library/StyleLibrary'
import { type GeneralInputKind } from './general-contract'
import { Composer, EntranceHandoff, entranceHasFreeStyle, entranceCountSuffix, categoryAllowed, type Handoff } from './entrance-edition'
import catalog from './entrance-catalog.json'
import '@/general/general.css'
import { FirstAgentChoice, useFirstAgentChoice } from './FirstAgentChoice'
import '@/shell/entrance.css'
import { asLibraryStyle, motionCategory, motionPurpose, motionStyleId, useMotionTemplates, type MotionTemplate } from '@/motion/motion-templates'
import { useColumns } from '@/motion/use-columns'

export type EntryKey = 'free' | GeneralInputKind
type Cat = { name: string; styles: string[]; star: string[] }
const ENTRIES = catalog.entries as { key: GeneralInputKind; name: string; hint: string; cats: Cat[] }[]
const styleCount = (key: GeneralInputKind) => new Set(ENTRIES.find(e => e.key === key)!.cats.flatMap(c => c.styles)).size
export const ALL_STYLE_COUNT = LIBRARY_STYLES.length

const ICONS: Record<EntryKey, typeof Video> = { free: Sparkles, footage: Video, topic: Type }

type MotionManifest = { desc?: string; kinds?: Record<string, { label?: string }> }

/** 点开模板时读一次参数清单：模板自己的介绍和控件名都在这份文件里。 */
function useManifest(item?: MotionTemplate) {
  const [manifest, setManifest] = useState<MotionManifest>()
  const sha = item?.sha256
  useEffect(() => {
    setManifest(undefined)
    if (!sha) return
    let live = true
    void fetch(`/api/motion/templates/${sha}/manifest`).then(response => response.json())
      .then(body => { if (live && body.ok) setManifest(body.result as MotionManifest) })
      .catch(() => {})
    return () => { live = false }
  }, [sha])
  return manifest
}

export function GeneralEntrance({ entry: given, cat: givenCat, focusId, onEntry, onCat, onPick, onAll, handoff, onDropHandoff }: {
  entry?: EntryKey; cat?: number; focusId?: string
  /** 拿去剪: the 稿子 the words came from */
  handoff?: Handoff; onDropHandoff?: () => void
  onEntry: (entry: EntryKey) => void; onCat: (cat: number) => void
  /** a style chosen from the posters of this entrance */
  onPick: (styleId: string, entry: GeneralInputKind, cat: number) => void
  onAll: () => void
}) {
  const firstAgent = useFirstAgentChoice()
  const motion = useMotionTemplates()
  const navigate = useNavigate()
  const [openMotion, setOpenMotion] = useState<string>()
  const shelf = useRef<HTMLUListElement>(null)
  const columns = useColumns(shelf, 4)
  const entry = given
  const data = entry && entry !== 'free' ? ENTRIES.find(e => e.key === entry) : undefined
  const cat = data && givenCat !== undefined && data.cats[givenCat] ? givenCat : undefined
  const cats = data?.cats ?? []
  const shown = cat !== undefined ? cats[cat] : undefined
  const ids = shown ? [...shown.star, ...shown.styles.filter(id => !shown.star.includes(id))] : []

  /** 每个用途下有几个 Motion 模板：分类计数和下面的模板架用的是同一份归类。 */
  const motionCounts = useMemo(() => {
    const map: Record<string, number> = {}
    for (const item of motion.items) { const name = motionPurpose(item); map[name] = (map[name] || 0) + 1 }
    return map
  }, [motion.items])

  // 选了用途就只列这一类的模板（以前是「选了也照样全列」，筛选等于没做）。
  const shelfItems = useMemo(() => shown ? motion.items.filter(item => motionPurpose(item) === shown.name) : motion.items, [motion.items, shown?.name])

  const manifest = useManifest(openMotion ? motion.items.find(item => item.sha256 === openMotion) : undefined)
  const openIndex = openMotion ? shelfItems.findIndex(item => item.sha256 === openMotion) : -1
  const detailAfter = openIndex >= 0 ? rowEndIndex(openIndex, columns, shelfItems.length) : -1
  const detailStyle = openMotion ? (() => {
    const item = motion.items.find(row => row.sha256 === openMotion)
    if (!item) return undefined
    const base = asLibraryStyle(item, entry === 'topic' ? 'topic' : 'footage')
    return manifest ? {...base, description_zh: manifest.desc?.trim() || base.description_zh, uses: [...new Set([...base.uses, ...Object.values(manifest.kinds || {}).map(kind => kind.label).filter((label): label is string => Boolean(label)).slice(0, 4)])]} : base
  })() : undefined

  const entries: { key: EntryKey; name: string; hint: string; count?: number }[] = [
    ...(entranceHasFreeStyle ? [{ key: 'free' as const, name: '不用风格', hint: '把素材和想法交给它，按你的要求做' }] : []),
    ...ENTRIES.map(e => ({ key: e.key, name: e.name, hint: e.hint, count: styleCount(e.key) })),
  ]

  return <main className="ec-ent" aria-label="新建通用剪辑">
    <header className="ec-ent__head">
      <div className="ec-ent__intro">
        <p className="ec-kicker">32 · 剪辑 · 新建通用工程</p>
        <h1 className="ec-h1 ec-grad-title">先看手上有什么，<br />再挑一个模板。</h1>
        <p className="ec-ent__lede">三段路：手上的东西 → 想做什么 → 挑一个 Motion 模板。挑中的模板会带着你的素材进剪辑台。</p>
      </div>
      <div className="ec-ent__head-actions">
        {firstAgent.enabled && <button type="button" className="ec-btn" onClick={firstAgent.show} disabled={firstAgent.open}>{firstAgent.agent && <AgentMark id={firstAgent.agent} size={15}/>}<span>{firstAgent.label}</span></button>}
        <button type="button" className="ec-btn" onClick={() => navigate('/styles')} disabled={firstAgent.open}>
          查看模板库<ChevronRight size={14} aria-hidden="true" />
        </button>
      </div>
    </header>
    {firstAgent.error && <p className="ec-ent__warn" role="status">{firstAgent.error}</p>}

    <section className="ec-ent__step" aria-labelledby="ec-ent-s1">
      <div className="ec-ent__stephead">
        <span className="ec-ent__no" aria-hidden="true">01</span>
        <h2 id="ec-ent-s1" className="ec-h3">你手上有什么</h2>
        <span className="ec-ent__stephint">选一个，后面的步骤跟着变</span>
      </div>
      <ul className="ec-ent__entries">
        {entries.map(item => {
          const Icon = ICONS[item.key]
          return <li key={item.key}>
            <button type="button" className="ec-ent__entry" data-on={entry === item.key || undefined}
              disabled={firstAgent.open} onClick={() => onEntry(item.key)} aria-pressed={entry === item.key}>
              <span className="ec-ent__entryicon"><Icon size={17} aria-hidden="true" /></span>
              <span className="ec-ent__entrytext">
                <b>{item.name}</b>
                <small>{item.hint}</small>
              </span>
              <span className="ec-ent__entrycount">{item.count === undefined ? '自由创作' : item.count > 0 ? `${item.count} 风格 + ${motion.items.length} Motion` : `${motion.items.length} Motion 模板`}</span>
            </button>
          </li>
        })}
        <li><button type="button" className="ec-ent__entry" onClick={()=>navigate('/production')}>
          <span className="ec-ent__entryicon"><Mic2 size={17}/></span><span className="ec-ent__entrytext"><b>口播制作</b><small>真人口播、20 秒样本云端续说或全程数字人</small></span><span className="ec-ent__entrycount">3 种方式</span>
        </button></li>
      </ul>
      {!firstAgent.open && entry === 'free' && <div className="ec-ent__composer"><Composer key={handoff ? 'from' : 'blank'} handoff={handoff} /></div>}
      {!firstAgent.open && handoff && <div className="ec-ent__handoff"><EntranceHandoff handoff={handoff} onDrop={() => onDropHandoff?.()} /></div>}
    </section>

    {data && <section className="ec-ent__step" aria-labelledby="ec-ent-s2">
      <div className="ec-ent__stephead">
        <span className="ec-ent__no" aria-hidden="true">02</span>
        <h2 id="ec-ent-s2" className="ec-h3">想做什么</h2>
        <span className="ec-ent__stephint">{cats.length} 类用途</span>
      </div>
      <div className="ec-ent__cats" role="group" aria-label="用途">
        {cats.map((c, j) => {
          const open = c.styles.some(id => categoryAllowed(j, id)) || (motionCounts[c.name] || 0) > 0
          const total = c.styles.length + (motionCounts[c.name] || 0)
          return <button type="button" key={`${entry}-${c.name}`} className="ec-ent__cat" data-on={cat === j || undefined}
            data-pro={!open || undefined} aria-pressed={cat === j}
            onClick={() => { onCat(j); setOpenMotion(undefined) }}>
            <b>{c.name}</b><i>{total}</i>{!open && <span className="ec-tag-pro">PRO</span>}
          </button>
        })}
      </div>
      {motion.items.length > 0 && <div className="ec-ent__motion-shelf">
        <div className="ec-ent__stephead ec-ent__motionhead">
          <span className="ec-ent__no" aria-hidden="true">M</span>
          <h2 className="ec-h3">Motion 可执行模板</h2>
          <span className="ec-ent__stephint">{shown ? `${shown.name} · ${shelfItems.length} 个` : `${motion.items.length} 个已导入 · 先选用途，这里会跟着筛`} · 点开看详情</span>
        </div>
        {shelfItems.length === 0
          ? <p className="ec-ent__motionempty">这一类暂时没有 Motion 模板，换个用途再看看。</p>
          : <ul ref={shelf} className="ec-ent__styles" data-motion-styles>{shelfItems.flatMap((item, index) => {
            const style = asLibraryStyle(item, entry === 'topic' ? 'topic' : 'footage')
            const card = <li key={item.sha256}><button type="button" className="ec-ent__style" data-motion="true"
              data-open={openMotion === item.sha256 || undefined}
              title={`${style.description_zh} · ${motionCategory(item)}`} onClick={() => setOpenMotion(current => current === item.sha256 ? undefined : item.sha256)}>
              <span className="ec-ent__poster"><img src={style.poster} alt="" draggable={false} loading="lazy" /><span className="ec-ent__motionbadge">动效模板</span></span>
              <b>{style.name_zh}</b><small>{style.description_zh}</small>
              <span className="ec-ent__tags"><i>{motionPurpose(item)}</i><i>{item.template.layout === 'portrait' ? '9:16' : '16:9'}</i></span>
            </button></li>
            return index === detailAfter ? [card, <li key="motion-detail" className="ec-ent__motiondetail">
              <div className="style-library">
                {detailStyle && openMotion && <StyleDetail style={detailStyle} id="entrance-motion-detail" actionLabel="用这个模板新建通用工程"
                  actionDisabled={false} hasPrevious={openIndex > 0} hasNext={openIndex < shelfItems.length - 1}
                  onGo={direction => {
                    const next = shelfItems[direction === 'previous' ? openIndex - 1 : openIndex + 1]
                    if (next) setOpenMotion(next.sha256)
                  }}
                  onClose={() => setOpenMotion(undefined)}
                  onAction={() => onPick(motionStyleId(shelfItems[openIndex]), entry as GeneralInputKind, cat ?? 0)} />}
              </div>
            </li>] : [card]
          })}</ul>}
      </div>}
    </section>}

    {shown && ids.length > 0 && <section className="ec-ent__step ec-ent__step--last" aria-labelledby="ec-ent-s3">
      <div className="ec-ent__stephead">
        <span className="ec-ent__no" aria-hidden="true">03</span>
        <h2 id="ec-ent-s3" className="ec-h3">{shown.name}</h2>
        <span className="ec-ent__stephint">{ids.length} 个风格{shown.star.length ? ' · ★ 最适合你的素材' : ''}</span>
      </div>
      <ul className="ec-ent__styles">
        {ids.map(id => {
          const style = styleById(LIBRARY_STYLES, id)
          if (!style) return null
          const open = categoryAllowed(cat!, id)
          const star = shown.star.includes(id)
          return <li key={id}>
            <button type="button" className="ec-ent__style" data-pro={!open || undefined} data-focus={focusId === id || undefined}
              title={style.description_zh} onClick={() => onPick(id, entry as GeneralInputKind, cat!)}>
              <span className="ec-ent__poster">
                <img src={style.poster} alt="" draggable={false} loading="lazy" />
                {!open && <span className="ec-ent__pro">PRO</span>}
              </span>
              <b>{style.name_zh}{star && <em aria-label="最适合你的素材">★</em>}</b>
              <small>{style.description_zh}</small>
              <span className="ec-ent__tags">{style.uses.slice(0, 2).map(u => <i key={u}>{u}</i>)}</span>
            </button>
          </li>
        })}
      </ul>
    </section>}

    {firstAgent.open && <FirstAgentChoice onDone={firstAgent.close} />}
  </main>
}
