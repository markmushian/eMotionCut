import {EnvironmentResults} from '../standard/EnvironmentResults'
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { ChevronLeft, ChevronRight, FolderLock, RefreshCw, Plus, RectangleHorizontal, RectangleVertical, Upload, X } from 'lucide-react'
import { StyleLibrary } from '@/workspace/style-library/StyleLibrary'
import { LIBRARY_STYLES } from '@/workspace/style-library/library-styles'
import { STYLE_INPUT_COPY, isPickableStyle, styleById, type LibraryStyle } from '@/workspace/style-library/style-library'
import type { GeneralAgentChoice, GeneralInputKind, GeneralSettings } from '@/general/general-contract'
import { AgentControls } from '@/general/AgentPicker'
import {
  ASPECT_LABELS, ASPECT_OPTIONS, DEFAULT_GENERAL_SETTINGS, LENGTH_OPTIONS, clipDuration, inputKindForStyle,
  intakeTitle, lengthLabel, mediaKindOf, MEDIA_ACCEPT, startBlocker, styleHint, textCount,
} from '@/general/general-logic'
import { useGeneralStart } from '@/general/general-start'
import { GeneralEntrance, type EntryKey } from '@/general/GeneralEntrance'
import { NavSlot, NewSteps } from '@/general/GeneralBits'
import '@/workspace/style-library/style-library.css'
import '@/general/general.css'
import { asLibraryStyle, isMotionStyleId, motionStyleId, useMotionTemplates } from '@/motion/motion-templates'
import {analyzeScript,buildScriptOutline} from '@/general/script-analysis'

/** A file chosen on ②, kept in the page until 开始生成 creates the project and uploads it. */
type DraftFile = { key: string; file: File; url: string; image: boolean; duration?: number; poster?: string }
type Draft = { files: DraftFile[]; text: string; note: string; settings: GeneralSettings; agent?: GeneralAgentChoice; network: boolean }

const ACCEPT = `${MEDIA_ACCEPT.video},${MEDIA_ACCEPT.image}`
const accepted = (file: File) => mediaKindOf(file) === 'video' || mediaKindOf(file) === 'image'

/** The length and a poster frame of a dropped video, read in the page (nothing is uploaded yet). */
function probeVideo(url: string): Promise<{ duration?: number; poster?: string }> {
  return new Promise(resolve => {
    const video = document.createElement('video')
    let done = false
    const finish = (value: { duration?: number; poster?: string }) => {
      if (done) return
      done = true
      video.removeAttribute('src'); video.load()
      resolve(value)
    }
    const timer = window.setTimeout(() => finish({}), 8000)
    video.muted = true; video.preload = 'auto'; video.playsInline = true
    video.onloadedmetadata = () => { video.currentTime = Math.min(0.5, (video.duration || 0) / 3) }
    video.onseeked = () => {
      window.clearTimeout(timer)
      let poster: string | undefined
      try {
        const canvas = document.createElement('canvas'), scale = Math.min(1, 320 / (video.videoWidth || 320))
        canvas.width = Math.max(1, Math.round((video.videoWidth || 320) * scale)); canvas.height = Math.max(1, Math.round((video.videoHeight || 180) * scale))
        canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
        poster = canvas.toDataURL('image/jpeg', 0.8)
      } catch { poster = undefined }
      finish({ duration: Number.isFinite(video.duration) ? video.duration : undefined, poster })
    }
    video.onerror = () => { window.clearTimeout(timer); finish({}) }
    video.src = url
  })
}

function PickStep({ selectedId, onSelect, onNext, onBack }: { selectedId?: string; onSelect: (style: LibraryStyle) => void; onNext: (style: LibraryStyle) => void; onBack: () => void }) {
  return <main className="styles-page ge-pickpage" aria-labelledby="ge-pick-title">
    <div className="styles-page__shield" aria-hidden="true" />
    <StyleLibrary mode="pick" selectedId={selectedId} onSelect={onSelect} onPick={onNext} pickLabel="下一步：放素材"
      title={<div className="ge ge-picktitle"><button type="button" className="ge-back" onClick={onBack}><ChevronLeft size={15} aria-hidden="true" />按用途挑</button>
        <NewSteps current={1} /><h1 id="ge-pick-title" className="ge-h1 ec-grad-title">全部风格</h1></div>} />
  </main>
}

function Segmented<T extends string | number>({ label, options, value, render, onChange }: {
  label: string; options: readonly T[]; value: T; render: (option: T) => ReactNode; onChange: (value: T) => void
}) {
  return <div>
    <div className="ge-label" id={`ge-opt-${label}`}>{label}</div>
    <div className="ge-segc" role="radiogroup" aria-labelledby={`ge-opt-${label}`}>
      {options.map(option => <button key={String(option)} type="button" role="radio" aria-checked={option === value} onClick={() => onChange(option)}>{render(option)}</button>)}
    </div>
  </div>
}

function IntakeStep({ style, kind, draft, setDraft, onChangeStyle, creatorWorkId }: {
  style: LibraryStyle; kind: GeneralInputKind; draft: Draft; setDraft: (update: (draft: Draft) => Draft) => void; onChangeStyle: () => void; creatorWorkId?:string
}) {
  const picker = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const { busy, busyLabel, error, environment, start: begin } = useGeneralStart()
  const motionStyle = isMotionStyleId(style.id)
  const blocker = startBlocker(kind, { files: draft.files.length, text: draft.text })
  const script = analyzeScript(draft.text,draft.settings.length)

  const addFiles = (list: FileList | File[]) => {
    const files = [...list].filter(accepted)
    if (!files.length) return
    const added = files.map((file): DraftFile => {
      const url = URL.createObjectURL(file)
      return { key: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 7)}`, file, url, image: mediaKindOf(file) === 'image', duration: mediaKindOf(file) === 'image' ? 0 : undefined }
    })
    setDraft(current => ({ ...current, files: [...current.files, ...added] }))
    for (const item of added) {
      if (item.image) continue
      void probeVideo(item.url).then(info => setDraft(current => ({ ...current, files: current.files.map(file => (file.key === item.key ? { ...file, ...info } : file)) })))
    }
  }
  const removeFile = (key: string) => setDraft(current => {
    const gone = current.files.find(file => file.key === key)
    if (gone) URL.revokeObjectURL(gone.url)
    return { ...current, files: current.files.filter(file => file.key !== key) }
  })
  const settings = (patch: Partial<GeneralSettings>) => setDraft(current => ({ ...current, settings: { ...current.settings, ...patch } }))
  // from a 稿子, footage gets the 定稿 too, as the words the film follows
  const start = () => { if (!blocker) void begin({ styleId: style.id, kind, text: kind === 'topic' ? draft.text : '', note: draft.note.trim(), settings: draft.settings, files: draft.files, agent: draft.agent, network: draft.network, creatorWorkId }) }

  const drop = (event: DragEvent) => { event.preventDefault(); setOver(false); if (!busy) addFiles(event.dataTransfer.files) }
  // the style's own note on what to bring, when it is the kind of thing this entrance asks for
  const hint = inputKindForStyle(style) === kind ? styleHint(style.user_provides.summary_zh) : ''

  return <main className="ge ge-intake" aria-labelledby="ge-intake-title"
    onDragOver={event => { if (kind === 'footage' && event.dataTransfer.types.includes('Files')) { event.preventDefault(); setOver(true) } }}
    onDragLeave={event => { if (event.currentTarget === event.target) setOver(false) }} onDrop={kind === 'footage' ? drop : undefined}>
    <div className="ge-col">
      <button type="button" className="ge-back" disabled={!!busy} onClick={onChangeStyle}><ChevronLeft size={15} aria-hidden="true" />返回</button>
      <NewSteps current={2} />
      <h1 id="ge-intake-title" className="ge-h1 ec-grad-title">{intakeTitle(kind)}</h1>
      <div className="ge-picked">
        <img src={style.poster} alt="" draggable={false} /><b>{style.name_zh}</b>· {STYLE_INPUT_COPY[kind].label}
        <button type="button" onClick={onChangeStyle} disabled={!!busy}><RefreshCw size={13} aria-hidden="true" />换个风格</button>
      </div>

      {kind === 'footage' ? <>
        <div className="ge-drop" data-over={over || undefined} data-empty={!draft.files.length || undefined}>
          {draft.files.length > 0 && <ul className="ge-files" aria-label="已放进的素材">
            {draft.files.map(file => <li key={file.key} className="ge-file">
              <div className="th">
                {(file.image ? file.url : file.poster) && <img src={file.image ? file.url : file.poster} alt="" draggable={false} />}
                <button type="button" className="x" aria-label={`移除 ${file.file.name}`} disabled={!!busy} onClick={() => removeFile(file.key)}><X size={12} aria-hidden="true" /></button>
                {!file.image && file.duration !== undefined && <span className="dur">{clipDuration(file.duration)}</span>}
              </div>
              <p title={file.file.name}>{file.file.name}</p>
            </li>)}
            <li className="ge-file ge-file--add"><button type="button" aria-label="继续添加素材" disabled={!!busy} onClick={() => picker.current?.click()}><Plus size={20} aria-hidden="true" /></button><p>继续添加</p></li>
          </ul>}
          <div className="ge-drop__hint">
            <Upload size={15} aria-hidden="true" />把视频或照片拖到这里，或<button type="button" disabled={!!busy} onClick={() => picker.current?.click()}>选择文件</button>
            {hint && <span title={style.user_provides.summary_zh}>{hint}</span>}
          </div>
          <input ref={picker} type="file" accept={ACCEPT} multiple hidden onChange={(event: ChangeEvent<HTMLInputElement>) => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />
        </div>
        <div className="ge-field">
          <label className="ge-label" htmlFor="ge-note">想说点什么<small>可以不填</small></label>
          <input id="ge-note" className="ge-input" value={draft.note} maxLength={400} disabled={!!busy} placeholder="比如片名、想表达的意思。不填，Agent 看完素材自己定"
            onChange={event => { const note = event.target.value; setDraft(current => ({ ...current, note })) }} />
        </div>
      </> : <>
        <div className="ge-area">
          <textarea aria-label="文案" value={draft.text} disabled={!!busy} placeholder="粘贴完整口播稿，或先写一个主题和要点"
            onChange={event => { const text = event.target.value; setDraft(current => ({ ...current, text })) }} />
          <span className="tip">文案会交给 Agent 编排画面和 Motion 动效</span>
          <span className="cnt">{textCount(draft.text)} 字</span>
        </div>
        <div className="ge-script-tools">
          <button type="button" disabled={!!busy||draft.text.trim().length>80} onClick={()=>setDraft(current=>({...current,text:buildScriptOutline(current.text)}))}>把主题展开成八段骨架</button>
          <span>{draft.text.trim().length>80?'已有完整文案，不会覆盖':'骨架会保留事实和数据占位，不替你编内容'}</span>
        </div>
        {script.fit!=='empty'&&<section className="ge-script-check" aria-label="口播校准" data-fit={script.fit}>
          <div className="ge-script-check__timing">
            <span className="ge-script-check__meter" aria-hidden="true"><i style={{width:`${Math.min(100,script.targetSeconds?script.estimatedSeconds/script.targetSeconds*100:68)}%`}}/></span>
            <div><b>预计口播 {script.estimatedSeconds} 秒</b><small>{script.fit==='auto'?'根据文案自动确定片长':script.fit==='good'?`与 ${script.targetSeconds} 秒片长匹配`:script.fit==='short'?`比 ${script.targetSeconds} 秒目标偏短`: `超过 ${script.targetSeconds} 秒目标`}</small></div>
            <span>{script.units} 个口播单位</span>
          </div>
          {script.issues.length>0&&<details className="ge-script-check__issues">
            <summary>发现 {script.issues.length} 处可能的 AI 腔</summary>
            <ul>{script.issues.map(issue=><li key={issue.id}><div><b>{issue.label}</b><q>{issue.sample}</q></div><span>{issue.advice}</span></li>)}</ul>
          </details>}
        </section>}
      </>}

      <div className="ge-opts">
        <Segmented label="片长" options={LENGTH_OPTIONS} value={draft.settings.length} render={lengthLabel} onChange={length => settings({ length })} />
        <Segmented label="比例" options={ASPECT_OPTIONS} value={draft.settings.aspect} onChange={aspect => settings({ aspect })}
          render={aspect => <>{aspect === '16:9' ? <RectangleHorizontal size={14} aria-hidden="true" /> : <RectangleVertical size={14} aria-hidden="true" />}{ASPECT_LABELS[aspect]}</>} />
      </div>

      <div className="ge-go">
        <div>
          <div className="ge-who"><span className="ge-agent-label"><b>执行 Agent</b><small>推荐 Codex</small></span>
            <AgentControls agent={draft.agent} onAgent={agent => setDraft(current => ({ ...current, agent }))} disabled={!!busy}
              network={draft.network} onNetwork={network => setDraft(current => ({ ...current, network }))} />
          </div>
          <div className="ge-safe"><FolderLock size={13} aria-hidden="true" />{draft.network ? '只读写这个工程的文件夹；会上网找素材，软件、插件、代码不下载' : '只读写这个工程的文件夹，不联网；要联网会先问你'}</div>
        </div>
        <button type="button" className="ge-primary" disabled={!!blocker && !busy} data-busy={busy ? '' : undefined} aria-busy={!!busy}
          title={blocker} onClick={start}>
          {busy ? busyLabel : <>{environment && !environment.ok ? '重新检查' : error ? '再试一次' : motionStyle?'让 Agent 按模板制作':'开始生成'}<ChevronRight size={17} aria-hidden="true" /></>}
        </button>
      </div>
      {environment && !environment.ok && <EnvironmentResults value={environment}/>}
      {error && <p className="ge-error" role="alert" style={{color:'var(--ec-indigo)',whiteSpace:'pre-wrap'}}>{error}</p>}
    </div>
  </main>
}

/**
 * /general: the entrance (不用风格 · 上传视频 · 给文案 → 用途 → 风格, or 看全部 for the whole library) → ② 放素材 → 开始生成
 * (creates the project, uploads, starts it) → the work page. Where the person is lives in the address, so Back works:
 * ?entry=footage&cat=2 (the entrance), ?all=1 (the whole library), ?style=<id>[&entry=…&cat=…] (② for that style).
 */
export default function GeneralEdit() {
  const motion = useMotionTemplates()
  const [params, setParams] = useSearchParams()
  const entryParam = params.get('entry'), catParam = params.get('cat')
  const entry: EntryKey | undefined = entryParam === 'free' || entryParam === 'footage' || entryParam === 'topic' ? entryParam : undefined
  const cat = catParam !== null && /^\d+$/.test(catParam) ? Number(catParam) : undefined
  const requestedStyle = params.get('style')
  const motionItem = requestedStyle && isMotionStyleId(requestedStyle) ? motion.items.find(item => motionStyleId(item) === requestedStyle) : undefined
  const picked = styleById(LIBRARY_STYLES, requestedStyle) || (motionItem ? asLibraryStyle(motionItem, entry === 'topic' ? 'topic' : 'footage') : undefined)
  // 上传视频 takes every style (a data style is cut from the person's footage too); elsewhere the pickable ones
  const style = picked && (isMotionStyleId(picked.id) || isPickableStyle(picked)) ? picked : undefined
  const kind: GeneralInputKind | undefined = style && (entry === 'footage' || entry === 'topic' ? entry : inputKindForStyle(style))
  const [selectedId, setSelectedId] = useState(params.get('pick') ?? undefined)
  const [draft, setDraftState] = useState<Draft>(() => ({ files: [], text: params.get('seed') ?? localStorage.getItem('emotioncut:production-script') ?? '', note: localStorage.getItem('emotioncut:production-material-plan') ?? '', settings: DEFAULT_GENERAL_SETTINGS, network: false }))
  // The picked files' preview URLs are released when the page closes.
  const files = useRef(draft.files)
  useEffect(() => { files.current = draft.files }, [draft.files])
  useEffect(() => () => { for (const file of files.current) URL.revokeObjectURL(file.url) }, [])
  const view = style ? 'intake' : params.get('all') ? 'all' : 'entrance'
  useEffect(() => { window.scrollTo(0, 0) }, [view])
  const at = (next: Record<string, string | number | undefined>) => setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])))

  return <>
    <NavSlot id="workspace-project-title-slot"><span className="ge-navtitle"><i aria-hidden="true" /><span>新建通用工程</span></span></NavSlot>
    {style && kind
      ? <IntakeStep key={style.id} style={style} kind={kind} draft={draft} setDraft={update => setDraftState(update)} creatorWorkId={params.get('work')??undefined}
        onChangeStyle={() => at(entry ? { entry, cat, pick: style.id } : { all: 1, pick: style.id })} />
      : view === 'all'
        ? <PickStep selectedId={selectedId} onSelect={next => setSelectedId(next.id)} onNext={next => at({ style: next.id })} onBack={() => at({})} />
        : <GeneralEntrance entry={entry} cat={cat} focusId={params.get('pick') ?? undefined} onEntry={next => at({ entry: next })} onCat={next => at({ entry, cat: next })}
          onPick={(id, selectedEntry, selectedCat) => at({ entry: selectedEntry, cat: selectedCat, style: id })} onAll={() => at({ all: 1 })} />}
  </>
}
