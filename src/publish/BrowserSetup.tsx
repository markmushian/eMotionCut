import {useEffect, useState} from 'react'
import {Download, ExternalLink, RefreshCw} from 'lucide-react'

type BrowserStatus = {
  preferred: 'ego'
  active?: {kind: string; path: string}
  installed: {ego: boolean; chrome: boolean; edge: boolean}
  fallback: boolean
  site: string
  downloadUrl: string
  message: string
  job?: InstallJob
}
type InstallJob = {state: string; percent?: number; received: number; total: number; step: string; source: string; error?: string}

const RUNNING = ['downloading', 'verifying', 'installing']
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MB`

/**
 * 开源版用户第一次用发布功能时，机器上通常只有 Chrome 或什么都没装。
 * 这里把「要不要装 Ego Lite」说清楚，并直接给一键安装，别让人去配置文件里猜。
 */
export function BrowserSetup() {
  const [status, setStatus] = useState<BrowserStatus>()
  const [job, setJob] = useState<InstallJob>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const running = Boolean(job && RUNNING.includes(job.state))
  const load = async () => {
    const response = await fetch('/api/editor/browser', {cache: 'no-store'})
    const body = await response.json()
    if (!response.ok || !body.ok) throw Error(body.error || '浏览器状态没读到')
    setStatus(body.result)
    setJob(body.result?.job)
    return body.result as BrowserStatus
  }
  useEffect(() => {
    let alive = true
    void load().catch(failure => {if (alive) setError(failure instanceof Error ? failure.message : '浏览器状态没读到')})
    return () => {alive = false}
  }, [])
  useEffect(() => {
    if (!running) return
    let alive = true
    const timer = setInterval(() => {
      void load().then(next => {
        if (!alive) return
        if (next?.installed.ego) setError('')
      }).catch(() => {})
    }, 900)
    return () => {alive = false;clearInterval(timer)}
  }, [running])
  const install = async () => {
    setError('')
    setBusy(true)
    try {
      const response = await fetch('/api/editor/browser/install', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'})
      const body = await response.json()
      if (!response.ok || !body.ok) throw Error(body.error || '下载没有开始')
      setJob(body.result)
    } catch (failure) {setError(failure instanceof Error ? failure.message : '下载没有开始')}
    finally {setBusy(false)}
  }
  if (!status || status.installed.ego) return null
  const progress = job?.state === 'downloading' && job.total
    ? `${job.step} ${size(job.received)} / ${size(job.total)}（${job.percent ?? 0}%）`
    : job?.step
  return <div className="mx-browser" data-tone={status.active ? 'warn' : 'error'}>
    <div className="mx-browser__copy">
      <b>{running ? '正在安装 Ego Lite' : '发布需要一个专用浏览器'}</b>
      <small>{running
        ? '装完会自动切过去；每个账号仍然是独立登录态，不会互相顶掉。'
        : status.active
          ? `现在用 ${status.active.kind === 'chrome' ? 'Chrome' : status.active.kind === 'edge' ? 'Edge' : status.active.kind} 顶着，可以照常发布。装好 Ego Lite 后会自动优先用它。`
          : '没检测到 Ego Lite，也没有 Chrome / Edge，登录窗口打不开。'}</small>
      {progress && <small role="status">{progress}{job?.source ? ` · 线路：${job.source}` : ''}</small>}
      {(job?.error || error) && <small className="mx-browser__error" role="alert">{job?.error || error}</small>}
    </div>
    <div className="mx-browser__ops">
      <button className="mx-btn mx-btn--primary" disabled={busy || running} onClick={() => void install()}><Download size={14} />{running ? '正在安装…' : '一键安装 Ego Lite'}</button>
      <a className="mx-btn" href={status.site} target="_blank" rel="noreferrer"><ExternalLink size={14} />官方页面</a>
      <button className="mx-btn" onClick={() => void load().catch(() => setError('重新检测失败'))}><RefreshCw size={14} />重新检测</button>
    </div>
  </div>
}
