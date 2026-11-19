import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { ArrowRight, ArrowUpRight, CircleAlert, CircleCheck, Clapperboard, FileText, FolderOpen, Plus, Sparkles, Upload } from 'lucide-react'
import { generalApi } from '@/general/general-api'
import { mediaUrl } from '@/studio/api'
import type { EditProject } from '@/types'
import { formatProjectDuration } from '@/studio/project-duration'
import { homeHasKoubo, loadHomeEditorProjects } from '@/workspace/home-edition'
import {
  formatProjectDate, isGeneralProject, libraryKey, mergeProjectLibrary, projectKind, projectKindLabel,
  type GeneralLibraryProject,
} from '@/workspace/project-kind'
import { useConnections } from '@/studio/connection-store'
import { summary, useDeploy, setDeployOpen, openSettings } from '@/onboarding/onboarding-store'
import { buildStages } from './stages'
import { studioScreenOnly } from '@/studio/library-edition'
import '@/shell/desk.css'
import '@/shell/cut-home.css'

type Row = EditProject | GeneralLibraryProject

/** The first clip's still, or the first picture, or nothing. Same rule as the archive rows. */
function still(project: EditProject) {
  const firstClip = project.timeline.tracks.filter(t => !t.hidden && (t.kind === 'video' || t.kind === 'proxy'))
    .flatMap(t => t.clips).filter(c => project.media.some(m => m.id === c.assetId && m.type === 'video'))
    .sort((a, b) => a.start - b.start)[0]
  const asset = project.media.find(m => m.id === firstClip?.assetId)
    || project.media.find(m => m.type === 'video' && m.thumbnailFile)
    || project.media.find(m => m.type === 'image')
  return asset?.thumbnailFile ? mediaUrl(project.id, asset.id, 'thumbnail')
    : asset?.type === 'image' ? mediaUrl(project.id, asset.id, 'original') : ''
}

function useLibrary() {
  const [rows, setRows] = useState<Row[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    const load = () => void Promise.all([loadHomeEditorProjects(), generalApi.list()])
      .then(([koubo, general]) => { if (live) { setRows(mergeProjectLibrary(koubo, general)); setState('ready') } })
      .catch(e => { if (live) { setError(e instanceof Error ? e.message : String(e)); setState('error') } })
    load()
    window.addEventListener('workspace-projects-changed', load)
    return () => { live = false; window.removeEventListener('workspace-projects-changed', load) }
  }, [])
  return { rows, state, error }
}

/** A dated slip: the day, and how much work is filed under it. */
function DaySlip({ count }: { count: number }) {
  const now = new Date()
  const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()]
  return <div className="ec-slip ec-slip--day">
    <p className="ec-slip__no">01</p>
    <div className="ec-slip__day">
      <span className="ec-slip__d">{String(now.getDate()).padStart(2, '0')}</span>
      <span className="ec-slip__my">{now.getMonth() + 1} 月 · 星期{week}</span>
    </div>
    <p className="ec-slip__foot">案上存着 {count} 个工程</p>
  </div>
}

function AgentSlip() {
  const { connections } = useConnections()
  const ready = connections.filter(item => item.state === 'connected').length
  const live = connections.length
  const names = connections.filter(item => item.state === 'connected').slice(0, 3).map(item => item.name)
  return <div className="ec-slip">
    <header className="ec-slip__head"><p className="ec-slip__no">02</p><h2 className="ec-slip__title">Agent 接入</h2></header>
    <p className="ec-slip__big"><b>{ready}</b><span>/ {live || 13}</span></p>
    <p className="ec-slip__line">{names.length ? names.join(' · ') : '还没有接通任何一家'}</p>
    <button type="button" className="ec-slip__go" onClick={() => openSettings('agents')}>管理接入<ArrowUpRight size={13} aria-hidden="true" /></button>
  </div>
}

function MachineSlip() {
  const s = summary(useDeploy())
  return <div className="ec-slip">
    <header className="ec-slip__head"><p className="ec-slip__no">03</p><h2 className="ec-slip__title">这台机器</h2></header>
    <p className="ec-slip__status" data-ok={s.ready || undefined}>
      {s.ready ? <CircleCheck size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}
      {s.ready ? '创作环境已就绪' : <>必须项 {s.okRequired} / {s.required}</>}
    </p>
    <p className="ec-slip__line">Python、编码器、浏览器内核，缺一项成片就会停在半路。</p>
    <button type="button" className="ec-slip__go" onClick={() => setDeployOpen(true)}>{s.ready ? '重新检查' : '去检查'}<ArrowUpRight size={13} aria-hidden="true" /></button>
  </div>
}

/** The stage directory: every room in the workshop, in order, as a printed table of contents. */
function Directory() {
  const rooms = buildStages(studioScreenOnly).filter(stage => stage.entries.length > 0)
  return <section className="ec-dir" aria-labelledby="ec-dir-title">
    <div className="ec-dir__head">
      <h2 id="ec-dir-title" className="ec-h3">工序目录</h2>
      <span className="ec-dir__hint">按编号走，也可以直接跳</span>
    </div>
    <ol className="ec-dir__list">
      {rooms.map(stage => <li key={stage.id} className="ec-dir__row">
        <span className="ec-dir__no">{stage.no}</span>
        <span className="ec-dir__stage">{stage.name}</span>
        <span className="ec-dir__rooms">
          {stage.entries.map(entry => <Link key={entry.no} to={entry.path} className="ec-dir__room">
            <span className="ec-dir__roomno">{entry.no}</span>{entry.label}
            {entry.pro && <i className="ec-tag-pro">PRO</i>}
          </Link>)}
        </span>
      </li>)}
    </ol>
  </section>
}

function ShelfRow({ row, index }: { row: Row; index: number }) {
  const general = isGeneralProject(row)
  const koubo = general ? undefined : row as EditProject
  const picture = koubo ? still(koubo) : (row as GeneralLibraryProject).posterUrl ?? ''
  const [failed, setFailed] = useState('')
  const duration = koubo ? formatProjectDuration(koubo.timeline) : ''
  const to = general ? `/general/projects/${row.id}` : `/studio/projects/${row.id}`
  return <li className="ec-shelf__row">
    <Link to={to} className="ec-shelf__link">
      <span className="ec-shelf__idx">{String(index + 1).padStart(2, '0')}</span>
      <span className="ec-shelf__still">
        {picture && failed !== picture
          ? <img src={picture} alt="" loading="lazy" onError={() => setFailed(picture)} />
          : <span className="ec-shelf__none" aria-hidden="true" />}
      </span>
      <span className="ec-shelf__name">{row.name}</span>
      <span className="ec-shelf__kind" data-kind={projectKind(row)}>{projectKindLabel(projectKind(row), studioScreenOnly ? '录屏' : undefined)}</span>
      <span className="ec-shelf__meta">{duration || '—'}</span>
      <span className="ec-shelf__meta">{formatProjectDate(row.updatedAt)}</span>
      <ArrowRight className="ec-shelf__arrow" size={14} aria-hidden="true" />
    </Link>
  </li>
}

function Shelf({ rows, state, error }: ReturnType<typeof useLibrary>) {
  const recent = useMemo(() => rows
    .filter(row => !row.deletedAt && !(row as EditProject).archivedAt)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, 7), [rows])
  return <section className="ec-shelf" aria-labelledby="ec-shelf-title">
    <div className="ec-shelf__head">
      <h2 id="ec-shelf-title" className="ec-h3">最近工程</h2>
      <Link to="/studio" className="ec-shelf__all">全部工程<ArrowRight size={13} aria-hidden="true" /></Link>
    </div>
    <div className="ec-shelf__legend" aria-hidden="true">
      <span>№</span><span>画面</span><span>名称</span><span>类型</span><span>时长</span><span>改动</span><span />
    </div>
    {state === 'loading' ? <p className="ec-shelf__empty">正在翻档案…</p>
      : state === 'error' ? <p className="ec-shelf__empty" role="alert">工程暂时读不出来：{error}</p>
      : recent.length ? <ul className="ec-shelf__list">{recent.map((row, index) => <ShelfRow key={libraryKey(row)} row={row} index={index} />)}</ul>
      : <p className="ec-shelf__empty">案台上还是空的。上面挑一个入口，做第一条。</p>}
  </section>
}

export default function AtelierHome() {
  const library = useLibrary()
  const { rows } = library
  const recent = useMemo(() => rows.filter(row => !row.deletedAt && !(row as EditProject).archivedAt).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 5), [rows])
  const featured = recent[0]
  const featuredGeneral = featured ? isGeneralProject(featured) : false
  const featuredPicture = featured ? (featuredGeneral ? (featured as GeneralLibraryProject).posterUrl ?? '' : still(featured as EditProject)) : ''
  const featuredTo = featured ? (featuredGeneral ? `/general/projects/${featured.id}` : `/studio/projects/${featured.id}`) : '/general'
  return <main className="cut-home">
    <header className="cut-home__header">
      <div><h1 className="ec-grad-title">内容生产中心</h1><p>从选题、素材和稿件开始，交给 Agent 导演剪辑，最后发布并复盘</p></div>
      <Link to="/general" className="cut-home__new"><Plus size={17} />创建新作品</Link>
    </header>
    <nav className="cut-home__workflow" aria-label="内容生产流程">
      <Link to="/creator-tools/bench"><span>01</span><b>对标研究</b><small>研究账号、作品与受众</small></Link>
      <Link to="/creator-tools/script"><span>02</span><b>写稿</b><small>把选题写成能拍的口播稿</small></Link>
      <Link to="/general"><span>03</span><b>Agent 剪辑</b><small>选风格、放素材，交给导演</small></Link>
      <Link to="/pro/publish"><span>04</span><b>作品发布</b><small>一条成片分发到账号矩阵</small></Link>
      <Link to="/pro/analytics"><span>05</span><b>数据复盘</b><small>播放、评论与 Agent 复盘</small></Link>
    </nav>
    <section className="cut-home__lead" aria-label="开始创作">
      <Link to={featuredTo} className="cut-home__feature"><div className="cut-home__picture">
        {featuredPicture ? <img src={featuredPicture} alt="" /> : <div className="cut-home__empty"><Clapperboard size={36} /><span>第一个画面从这里开始</span></div>}
        <div className="cut-home__shade" /><span className="cut-home__timecode">00:00:00:00</span>
        <div className="cut-home__feature-copy"><small>{featured ? '最近编辑' : '尚无工程'}</small><h2>{featured?.name ?? '创建第一条视频'}</h2><span>{featured ? '继续编辑' : '开始创作'}<ArrowRight size={15} /></span></div>
      </div></Link>
      <aside className="cut-home__start">
        <div className="cut-home__start-head"><h2>你准备从哪一步开始</h2><p>已有素材就直接剪；只有想法就先让 Agent 策划</p></div>
        <Link to="/general?entry=footage"><Upload /><span><b>视频或照片</b><small>上传素材，再选择 Motion 模板</small></span><ArrowRight /></Link>
        <Link to="/creator-tools/topics"><FileText /><span><b>只有一个想法</b><small>先研究选题，再写口播稿</small></span><ArrowRight /></Link>
        <Link to="/production"><Sparkles /><span><b>制作口播视频</b><small>真人口播、云端续说或数字人</small></span><ArrowRight /></Link>
        <Link to={studioScreenOnly ? '/screen-motion' : '/studio'} className="cut-home__archive"><FolderOpen /><span><b>打开工程库</b><small>查看全部视频工程</small></span><ArrowRight /></Link>
      </aside>
    </section>
    <section className="cut-home__recent" aria-labelledby="cut-recent-title">
      <header><div><h2 id="cut-recent-title">最近工程</h2><p>{recent.length ? `显示最近 ${recent.length} 个` : '工程会保存在这里'}</p></div><Link to="/studio">查看全部</Link></header>
      {library.state === 'error' ? <p className="cut-home__message">工程暂时无法读取：{library.error}</p> : recent.length ? <div className="cut-home__recent-grid">{recent.map(row => {
        const general = isGeneralProject(row), picture = general ? (row as GeneralLibraryProject).posterUrl ?? '' : still(row as EditProject)
        const to = general ? `/general/projects/${row.id}` : `/studio/projects/${row.id}`
        return <Link to={to} key={libraryKey(row)} className="cut-home__project"><span className="cut-home__thumb">{picture ? <img src={picture} alt="" /> : <Clapperboard />}</span><span><b>{row.name}</b><small>{projectKindLabel(projectKind(row), studioScreenOnly ? '录屏' : undefined)} · {formatProjectDate(row.updatedAt)}</small></span></Link>
      })}</div> : <p className="cut-home__message">还没有工程。选择上面的方式，开始制作第一条视频</p>}
    </section>
    <footer className="cut-home__foot">素材、渲染和导出都保留在这台电脑上</footer>
  </main>
}
