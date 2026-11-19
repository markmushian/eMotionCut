import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { ArrowLeft, Moon, Sun } from 'lucide-react'
import { Toaster } from 'sonner'
import { WorkspaceLink as Link } from '@/workspace/navigation-guard'
import { SystemSettings } from '@/settings/SystemSettings'
import { DeployButton, DeployWizard } from '@/onboarding/DeployWizard'
import { FirstRunDeployGuide } from '@/onboarding/FirstRunDeployGuide'
import { DesktopActions } from '@/desktop/DesktopSession'
import { studioScreenOnly } from '@/studio/library-edition'
import { buildStages, entryForPath, stageForPath, type StageId } from './stages'
import { SHELL_MODE_LABELS, applyShellMode, readShellMode, toggleShellMode, type ShellMode } from './shell-mode'
import { WeChatGroup } from './WeChatGroup'
import './tokens.css'
import './shell.css'
import './surfaces.css'
import CreatorTaskDock from '@/creator/CreatorTaskDock'

/**
 * A quiet application frame. Navigation stays available without competing with the work surface.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const stages = useMemo(() => buildStages(studioScreenOnly), [])
  const stage = stageForPath(pathname, stages)
  const entry = entryForPath(pathname, stages)

  const [mode, setMode] = useState<ShellMode>(readShellMode)
  const [open, setOpen] = useState<StageId | null>(null)
  const [lastPath, setLastPath] = useState(pathname)
  if (lastPath !== pathname) { setLastPath(pathname); setOpen(null) }

  const rail = useRef<HTMLElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => { applyShellMode(mode) }, [mode])

  useEffect(() => {
    document.title = entry ? `${entry.label} · eMotionCut` : `${stage.name} · eMotionCut`
  }, [stage.name, entry?.label])

  useEffect(() => {
    if (!open) return
    const away = (event: PointerEvent) => {
      const target = event.target as Node
      if (rail.current?.contains(target) || panel.current?.contains(target)) return
      setOpen(null)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(null) }
    document.addEventListener('pointerdown', away)
    window.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', away); window.removeEventListener('keydown', escape) }
  }, [open])

  const opened = open ? stages.find(item => item.id === open) : undefined

  return <div className="ec-frame">
    <nav ref={rail} className="ec-rail" aria-label="工作阶段">
      <Link to="/" className="ec-rail__mark" aria-label="eMotionCut，回到案台" onClick={() => setOpen(null)}>
        <span aria-hidden="true">eM</span>
      </Link>
      <div className="ec-rail__stages">
        {stages.map(item => {
          const current = item.id === stage.id
          const expandable = item.entries.length > 1
          const target = item.entries[0]?.path ?? item.path
          const shared = {
            className: 'ec-stage-btn',
            'aria-current': current ? ('page' as const) : undefined,
            'data-open': open === item.id ? 'true' : undefined,
          }
          const body = <>
            <span className="ec-stage-btn__name">{item.name}</span>
          </>
          return expandable
            ? <button key={item.id} type="button" {...shared} aria-expanded={open === item.id}
                aria-controls="ec-rail-panel" aria-label={`${item.no} ${item.name}，展开 ${item.entries.length} 个入口`}
                onClick={() => setOpen(value => value === item.id ? null : item.id)}>{body}</button>
            : <Link key={item.id} to={target} {...shared} aria-label={`${item.no} ${item.name}`}
                onClick={() => setOpen(null)}>{body}</Link>
        })}
      </div>
      <div className="ec-rail__foot">
        <WeChatGroup />
        <button type="button" onClick={() => setMode(toggleShellMode(mode))}
          aria-label={`切换外观，当前${SHELL_MODE_LABELS[mode]}`} title={`外观：${SHELL_MODE_LABELS[mode]}`}>
          {mode === 'night' ? <Moon size={15} /> : <Sun size={15} />}
        </button>
      </div>
    </nav>

    {opened && opened.entries.length > 1 && <div ref={panel} id="ec-rail-panel" className="ec-rail-panel">
      <div className="ec-rail-panel__head">
        <span className="ec-rail-panel__no">{opened.no}</span>
        <span className="ec-rail-panel__name">{opened.name}</span>
        <span className="ec-rail-panel__gloss">{opened.gloss}</span>
      </div>
      <div className="ec-rail-panel__list">
        {opened.entries.map(item => <Link key={item.no} to={item.path} className="ec-rail-panel__item"
          aria-current={entry?.no === item.no ? 'page' : undefined} onClick={() => setOpen(null)}>
          <span className="ec-rail-panel__itemno">{item.no}</span>
          <span><b>{item.label}</b><small>{item.hint}</small></span>
          {item.pro && <span className="ec-tag-pro">PRO</span>}
        </Link>)}
      </div>
      <p className="ec-rail-panel__note">按 Esc 收起。每个编号对应一道工序，工序内的房间都在这张清单里。</p>
    </div>}

    <div className="ec-stage-wrap">
      <header className="ec-ruler">
        <div className="ec-ruler__where">
          {pathname!=='/'&&<button type="button" className="ec-ruler__back" onClick={()=>navigate(-1)} aria-label="返回上一页"><ArrowLeft size={15}/></button>}
          <span className="ec-ruler__name">{stage.name}</span>
          {entry && <><span className="ec-ruler__sep" aria-hidden="true" /><span className="ec-ruler__name" style={{ fontWeight: 400, color: 'var(--ec-ink-3)' }}>{entry.label}</span></>}
        </div>
        <div className="ec-ruler__slot"><div id="workspace-project-title-slot" className="workspace-project-title-slot" /></div>
        <div className="ec-ruler__slot"><div id="workspace-preview-island-slot" className="workspace-preview-island-slot" /></div>
        <div className="ec-ruler__spacer" />
        <div id="workspace-editor-slot" className="workspace-editor-slot" />
        <div className="ec-ruler__actions">
          <div id="workspace-primary-action-slot" className="workspace-primary-action-slot" />
          <DeployButton />
          <DesktopActions />
          <SystemSettings />
        </div>
      </header>
      <div className="ec-body">
        <div id="workspace-nav-ambient-slot" hidden aria-hidden="true" />
        {children}
      </div>
    </div>
    <DeployWizard />
    <FirstRunDeployGuide />
    <CreatorTaskDock />
  </div>
}

/** Toasts follow the shell finish instead of being permanently dark. */
export function ShellToaster() {
  const [mode, setMode] = useState<ShellMode>(readShellMode)
  useEffect(() => {
    const sync = () => setMode(readShellMode())
    window.addEventListener('emotioncut-shell-mode', sync)
    return () => window.removeEventListener('emotioncut-shell-mode', sync)
  }, [])
  return <Toaster theme={mode === 'night' ? 'dark' : 'light'} richColors position="top-center" />
}
