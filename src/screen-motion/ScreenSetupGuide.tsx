import {useEffect, useState} from 'react'
import {Download, MonitorPlay, RefreshCw, Settings} from 'lucide-react'
import './screen-setup-guide.css'

type ScreenCheck = {text: string; screenGuide?: {state: 'missing' | 'outdated' | 'permission-unknown'; version?: string; minimum: string}; action?: {kind: string; target?: string}}
type InstallState = 'idle' | 'downloading' | 'verifying' | 'installing' | 'done' | 'failed'
type InstallJob = {state: InstallState; percent?: number; received: number; total: number; source: string; step: string; error?: string}

const RUNNING: InstallState[] = ['downloading', 'verifying', 'installing']
const mb = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MB`

/** Uses the same non-mutating OpenScreen check as 一键部署. No recording is started. */
export function ScreenSetupGuide({onAvailability}:{onAvailability?:(ready:boolean)=>void}={}) {
  const enabled = typeof window !== 'undefined'
  const [check, setCheck] = useState<ScreenCheck>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [job, setJob] = useState<InstallJob>()
  useEffect(() => {
    if (!enabled) return
    let alive = true
    setBusy(true)
    setError('')
    void fetch('/api/editor/onboarding/openscreen', {cache: 'no-store'}).then(async response => {
      const body = await response.json()
      if (!response.ok || !body.ok || !body.result) throw Error(body.error || 'OpenScreen 暂时没查成，请重试')
      if (body.result.state === 'warn') throw Error(body.result.text || 'OpenScreen 暂时没查成，请重试')
      if (alive) {setCheck(body.result);onAvailability?.(!body.result.screenGuide)}
    }).catch(failure => {if (alive) {setError(failure instanceof Error ? failure.message : 'OpenScreen 暂时没查成，请重试');onAvailability?.(false)}}).finally(() => {if (alive) setBusy(false)})
    return () => {alive = false}
  }, [refresh, enabled, onAvailability])
  const running = Boolean(job && RUNNING.includes(job.state))
  // 下载是后台任务：组件重新挂载时也要认出「正在装」，不能让用户以为没反应又点一次。
  useEffect(() => {
    if (!enabled || !running) return
    let alive = true
    const timer = setInterval(() => {
      void fetch('/api/editor/onboarding/openscreen/install', {cache: 'no-store'})
        .then(response => response.json())
        .then(body => {
          if (!alive || !body?.ok || !body.result) return
          setJob(body.result as InstallJob)
          if (body.result.state === 'done') setRefresh(value => value + 1)
        }).catch(() => {})
    }, 800)
    return () => {alive = false;clearInterval(timer)}
  }, [enabled, running])
  const guide = check?.screenGuide
  const openPermission = async () => {
    setError('')
    try {
      const response = await fetch('/api/editor/onboarding/system/screen-recording', {method: 'POST'})
      const body = await response.json()
      if (!response.ok || !body.ok) throw Error(body.error || '屏幕录制设置没打开，请从系统设置进入')
    } catch (failure) {setError(failure instanceof Error ? failure.message : '屏幕录制设置没打开')}
  }
  const install = async () => {
    setError('')
    try {
      const response = await fetch('/api/editor/onboarding/openscreen/install', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'})
      const body = await response.json()
      if (!response.ok || !body.ok) throw Error(body.error || '下载没有开始，请重试')
      setJob(body.result as InstallJob)
    } catch (failure) {setError(failure instanceof Error ? failure.message : '下载没有开始')}
  }
  if (!enabled || !guide && !error) return null
  const missing = guide?.state === 'missing', outdated = guide?.state === 'outdated'
  const progress = job?.state === 'downloading' && job.total
    ? `${job.step} ${mb(job.received)} / ${mb(job.total)}（${job.percent ?? 0}%）`
    : job?.step
  return <section className="screen-setup-guide" aria-labelledby="screen-setup-title" aria-busy={busy || running}>
    <span className="screen-setup-symbol"><MonitorPlay size={29} aria-hidden /></span>
    <div className="screen-setup-copy"><h2 id="screen-setup-title">{running ? '正在安装 OpenScreen' : missing ? '先装好 OpenScreen' : outdated ? '更新 OpenScreen' : guide ? '打开屏幕录制权限' : '重新检查 OpenScreen'}</h2>
      <p className="screen-setup-notice">{running
        ? '不用管它，装完会自动重新检测。若网络不稳，会自动换到国内可用的加速线路重试。'
        : missing ? '录屏运镜的录制和导出都要用 OpenScreen。可以直接在这里下载安装，不必自己找安装包。'
        : outdated ? `当前 ${guide?.version}，需要 ${guide?.minimum} 以上。直接点安装会升级到已验证的 2.1.2。`
        : guide ? '到系统设置 → 隐私与安全性 → 屏幕录制，给 OpenScreen 打开权限' : '检查暂时没完成，已有工程仍可打开。'}</p>
      {guide?.state === 'permission-unknown' && <p className="screen-setup-detail">eMotionCut 无法读取 OpenScreen 自己的权限状态；如果你已打开，可以直接开始录屏。</p>}
      <div className="screen-setup-actions">
        {(missing || outdated)
          ? <button type="button" className="screen-setup-primary" disabled={running} onClick={() => void install()}><Download size={15} aria-hidden />{running ? '正在安装…' : outdated ? '一键升级到 2.1.2' : '一键下载并安装'}</button>
          : guide && <button type="button" className="screen-setup-primary" onClick={() => void openPermission()}><Settings size={15} aria-hidden />打开屏幕录制设置</button>}
        <button type="button" disabled={busy} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={15} aria-hidden />{busy ? '正在检测…' : '我已装好，重新检测'}</button>
        {(missing || outdated) && !running && <a className="screen-setup-manual" href="https://github.com/getopenscreen/openscreen/releases/latest" target="_blank" rel="noreferrer">或从官方页面手动下载</a>}
      </div>
      {progress && <p className="screen-setup-detail" role="status">{progress}{job?.source ? ` · 线路：${job.source}` : ''}</p>}
      {(job?.error || error) && <p className="screen-setup-notice" role="alert">{job?.error || error}</p>}
    </div>
  </section>
}
