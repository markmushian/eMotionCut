import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import {
  Check, Copy, Film, FolderOpen, HardDrive, MonitorPlay, MoreHorizontal, Pencil, Plus, RotateCcw, Trash2, X,
} from 'lucide-react'
import { toast } from 'sonner'
import { createProject, deleteProject, duplicateProject, listProjects, mediaUrl, patchProject, restoreProject } from '@/studio/api'
import { generalApi } from '@/general/general-api'
import { formatProjectDate, generalLibraryProject, libraryKey, projectKindLabel, type GeneralLibraryProject } from '@/workspace/project-kind'
import { formatProjectDuration, formatScreenProjectDuration } from '@/studio/project-duration'
import { libraryDuration, libraryRunLabel } from '@/general/general-logic'
import { ProjectStorage } from '@/studio/components/ProjectStorage'
import { PurgeProjectButton } from '@/studio/components/PurgeProjectButton'
import { studioProjectPath, studioScreenOnly } from '@/studio/library-edition'
import { ScreenSetupGuide } from '@/screen-motion/ScreenSetupGuide'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import type { EditProject } from '@/types'
import '@/shell/archive.css'

type Entry = { kind: 'koubo'; project: EditProject } | { kind: 'general'; project: GeneralLibraryProject }
type View = 'recent' | 'archived' | 'trash'
type KindFilter = 'all' | 'koubo' | 'general'

const VIEWS: { value: View; label: string; note: string }[] = [
  { value: 'recent', label: '最近', note: '按改动时间排序' },
  { value: 'archived', label: '归档', note: '清理缓存，暂停编辑' },
  { value: 'trash', label: '回收站', note: '恢复或彻底删除' },
]

/** Aspect-correct still for a 口播 project; general projects carry their own poster URL. */
function stillOf(project: EditProject) {
  const firstClip = project.timeline.tracks.filter(t => !t.hidden && (t.kind === 'video' || t.kind === 'proxy'))
    .flatMap(t => t.clips).filter(c => project.media.some(m => m.id === c.assetId && m.type === 'video'))
    .sort((a, b) => a.start - b.start)[0]
  const asset = project.media.find(m => m.id === firstClip?.assetId)
    || project.media.find(m => m.type === 'video' && m.thumbnailFile)
    || project.media.find(m => m.type === 'image')
  return asset?.thumbnailFile ? mediaUrl(project.id, asset.id, 'thumbnail')
    : asset?.type === 'image' ? mediaUrl(project.id, asset.id, 'original') : ''
}

function Thumb({ src, screen }: { src: string; screen?: boolean }) {
  const [failed, setFailed] = useState('')
  if (!src || failed === src) return <span className="ec-row__still ec-row__still--none" aria-hidden="true">{screen ? <MonitorPlay size={15} /> : <Film size={15} />}</span>
  return <span className="ec-row__still"><img src={src} alt="" loading="lazy" onError={() => setFailed(src)} /></span>
}

function Row({ entry, view, screenMode, index, onChanged }: { entry: Entry; view: View; screenMode: boolean; index: number; onChanged: () => void }) {
  const navigate = useNavigate()
  const general = entry.kind === 'general'
  const project = entry.project
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(project.name)
  const [saving, setSaving] = useState(false)

  const picture = general ? (project as GeneralLibraryProject).posterUrl ?? '' : stillOf(project as EditProject)
  const duration = general
    ? ((project as GeneralLibraryProject).duration ? libraryDuration((project as GeneralLibraryProject).duration!) : '')
    : screenMode ? formatScreenProjectDuration(project as EditProject) : formatProjectDuration((project as EditProject).timeline)
  const run = general ? libraryRunLabel((project as GeneralLibraryProject).runState) : undefined
  const offline = !general && Boolean((project as EditProject).storageOffline)

  const open = () => {
    if (offline) { toast.info('工程所在硬盘未连接，请重新连接'); return }
    navigate(general ? `/general/projects/${project.id}` : studioProjectPath(project.id, screenMode))
  }

  const rename = async () => {
    const value = draft.trim()
    if (!value || saving) return
    if (value === project.name) { setRenaming(false); return }
    setSaving(true)
    try {
      if (general) await generalApi.patch(project.id, { name: value })
      else await patchProject(project.id, { name: value })
      setRenaming(false)
      toast.success('工程已重命名')
      onChanged()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '重命名失败，请重试')
    } finally { setSaving(false) }
  }

  const remove = async () => {
    if (!confirm(`将「${project.name}」移入回收站？`)) return
    try {
      if (general) await generalApi.remove(project.id)
      else await deleteProject(project.id)
      onChanged()
    } catch (error) { toast.error(error instanceof Error ? error.message : '删除失败，请重试') }
  }

  const restore = async () => {
    try {
      if (general) await generalApi.restore(project.id)
      else await restoreProject(project.id)
      toast.success('已恢复到最近工程')
      onChanged()
    } catch (error) { toast.error(error instanceof Error ? error.message : '恢复失败，请重试') }
  }

  return <li className="ec-row" data-offline={offline || undefined}>
    <div className="ec-row__main" role="link" tabIndex={0} aria-label={`打开工程 ${project.name}`}
      onClick={() => { if (!renaming) open() }}
      onKeyDown={event => { if (event.target !== event.currentTarget) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open() } }}>
      <span className="ec-row__idx" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
      <Thumb src={picture} screen={screenMode} />
      <span className="ec-row__title">
        {renaming
          ? <form className="ec-row__rename" onClick={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); void rename() }}>
            <input value={draft} autoFocus disabled={saving} aria-label="工程名称" onChange={event => setDraft(event.currentTarget.value)}
              onFocus={event => event.currentTarget.select()}
              onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') { setDraft(project.name); setRenaming(false) } }} />
            <button type="submit" aria-label="保存名称" disabled={saving || !draft.trim()}><Check size={13} /></button>
            <button type="button" aria-label="取消重命名" onClick={() => { setDraft(project.name); setRenaming(false) }}><X size={13} /></button>
          </form>
          : <b title={project.name}>{project.name}</b>}
        {offline && <small className="ec-row__warn">硬盘未连接</small>}
        {run && <small className="ec-row__run" data-tone={run.tone}>{run.text}</small>}
      </span>
      <span className="ec-row__kind" data-kind={general ? 'general' : 'koubo'}>{projectKindLabel(general ? 'general' : 'koubo', screenMode ? '录屏' : undefined)}</span>
      <span className="ec-row__meta">{view === 'trash' ? '—' : duration || '—'}</span>
      <span className="ec-row__meta">{formatProjectDate(project.deletedAt || project.updatedAt)}</span>
      <span className="ec-row__ops">
        {view === 'trash'
          ? <>
            <button type="button" className="ec-row__op" title="恢复工程" aria-label={`恢复工程 ${project.name}`} onClick={() => void restore()}><RotateCcw size={15} /></button>
            <PurgeProjectButton id={project.id} name={project.name} onPurge={general ? generalApi.purge : undefined} onDeleted={onChanged} />
          </>
          : <>
            {!general && <span className="ec-row__storage" onClick={event => event.stopPropagation()}>
              <ProjectStorage project={project as EditProject} onChanged={onChanged} />
            </span>}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button type="button" className="ec-row__op" title="更多操作" aria-label={`${project.name} 的更多操作`} onClick={event => event.stopPropagation()}><MoreHorizontal size={16} /></button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" onClick={event => event.stopPropagation()}>
                <DropdownMenuItem onSelect={() => { setDraft(project.name); setRenaming(true) }}><Pencil size={14} />重命名</DropdownMenuItem>
                {!general && <DropdownMenuItem onSelect={() => void duplicateProject(project.id).then(onChanged).catch(error => toast.error(error.message))}><Copy size={14} />复制一份</DropdownMenuItem>}
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void remove()}><Trash2 size={14} />移入回收站</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>}
      </span>
    </div>
  </li>
}

export default function AtelierProjects() {
  const location = useLocation()
  const navigate = useNavigate()
  const screenMode = studioScreenOnly || location.pathname.startsWith('/screen-motion')
  const onlyScreen = location.pathname.startsWith('/screen-motion')

  const [entries, setEntries] = useState<Entry[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [view, setView] = useState<View>('recent')
  const [kind, setKind] = useState<KindFilter>('all')
  const [creating, setCreating] = useState(false)
  const [screenAvailable, setScreenAvailable] = useState(!onlyScreen)

  const load = async () => {
    try {
      const [all, general] = await Promise.all([listProjects(true), onlyScreen ? Promise.resolve([]) : generalApi.list(true)])
      const rows: Entry[] = [
        ...all.map((project): Entry => ({ kind: 'koubo', project })),
        ...general.map(summary => ({ kind: 'general', project: generalLibraryProject(summary) }) as Entry),
      ]
      setEntries(rows); setState('ready'); setError('')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure)); setState('error')
    }
  }

  useEffect(() => {
    void load()
    const reload = () => void load()
    window.addEventListener('workspace-projects-changed', reload)
    return () => window.removeEventListener('workspace-projects-changed', reload)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onlyScreen])

  const counts = useMemo(() => ({
    recent: entries.filter(row => !row.project.deletedAt && !(row.project as EditProject).archivedAt).length,
    archived: entries.filter(row => (row.project as EditProject).archivedAt && !row.project.deletedAt).length,
    trash: entries.filter(row => row.project.deletedAt).length,
  }), [entries])

  const listed = useMemo(() => entries.filter(row => {
    if (kind !== 'all' && row.kind !== kind) return false
    if (view === 'trash') return Boolean(row.project.deletedAt)
    if (row.project.deletedAt) return false
    if (view === 'archived') return Boolean((row.project as EditProject).archivedAt)
    return !(row.project as EditProject).archivedAt
  }).sort((a, b) => b.project.updatedAt - a.project.updatedAt), [entries, view, kind])

  const create = async () => {
    if (creating) return
    setCreating(true)
    const stamp = new Date().toLocaleString('zh-CN', { hour12: false }).replace(/[/:]/g, '-')
    try {
      const project = await createProject(`空白工程 ${stamp}`)
      window.dispatchEvent(new Event('workspace-projects-changed'))
      navigate(studioProjectPath(project.id, screenMode))
    } catch (failure) { toast.error(failure instanceof Error ? failure.message : '创建失败') }
    finally { setCreating(false) }
  }

  if (onlyScreen && !screenAvailable) return <div className="ec-archive"><ScreenSetupGuide onAvailability={setScreenAvailable}/></div>
  return <div className="ec-archive">
    {onlyScreen && <ScreenSetupGuide onAvailability={setScreenAvailable}/>}
    <header className="ec-archive__mast">
      <p className="ec-kicker">{screenMode ? '21 · 拍录 · 录屏运镜' : '31 · 剪辑 · 剪辑台'}</p>
      <div className="ec-archive__title">
        <h1 className="ec-h1 ec-grad-title">{screenMode ? '电脑操作录屏素材' : '视频工程'}</h1>
        <span className="ec-archive__count">{counts.recent + counts.archived} 件在册</span>
      </div>
      <p className="ec-archive__lede">{screenMode
        ? '录制软件操作、网页演示和教程步骤，作为口播视频里的操作画面或完整录屏教程。'
        : '口播与通用剪辑放在同一本档案里，按改动时间排列。归档会清掉可重建的缓存，素材和历史版本都留着。'}</p>
    </header>

    <div className="ec-archive__body">
      <nav className="ec-archive__side" aria-label="档案筛选">
        <p className="ec-archive__sidehead">视图</p>
        {VIEWS.map(item => <button key={item.value} type="button" className="ec-archive__view" aria-pressed={view === item.value}
          onClick={() => setView(item.value)}>
          <span>{item.label}</span><b>{counts[item.value]}</b>
        </button>)}
        <p className="ec-archive__sidehead">类型</p>
        {([['all', '全部'], ['koubo', screenMode ? '录屏' : '口播'], ['general', '通用']] as const).map(([value, label]) => <button key={value} type="button"
          className="ec-archive__view ec-archive__view--kind" aria-pressed={kind === value} onClick={() => setKind(value)}>
          <span>{label}</span>
        </button>)}
        <div className="ec-archive__hint">
          <HardDrive size={13} aria-hidden="true" />
          <p>归档前会确认缓存占用。素材文件不会被移动。</p>
        </div>
      </nav>

      <section className="ec-archive__list" aria-label="工程列表">
        {state === 'error' && <p className="ec-archive__empty" role="alert">档案读不出来：{error}</p>}
        {view === 'recent' && <button type="button" className="ec-archive__new" disabled={creating} onClick={() => void create()}>
          <Plus size={16} aria-hidden="true" />
          <span><b>{creating ? '正在建…' : screenMode ? '录制电脑操作' : '新建空白工程'}</b><small>{screenMode ? '录屏后可剪掉停顿、放大重点并自动加字幕' : '不带素材，进剪辑台自己搭'}</small></span>
        </button>}

        <div className="ec-archive__legend" aria-hidden="true">
          <span>№</span><span>画面</span><span>名称</span><span>类型</span><span>时长</span><span>改动</span><span />
        </div>

        {state === 'loading' ? <p className="ec-archive__empty">正在翻档案…</p>
          : listed.length === 0 ? <p className="ec-archive__empty">
            {view === 'trash' ? '回收站是空的。' : view === 'archived' ? '还没有归档的工程。' : '档案是空的，先建一个。'}
          </p>
          : <ul className="ec-archive__rows">{listed.map((entry, index) => <Row key={libraryKey(entry.project)} entry={entry} view={view}
            index={index} screenMode={screenMode} onChanged={() => void load()} />)}</ul>}

        {view === 'trash' && listed.length > 0 && <p className="ec-archive__foot"><FolderOpen size={13} aria-hidden="true" />彻底删除会连同存储目录一起移出磁盘，无法撤销。</p>}
      </section>
    </div>
  </div>
}
