/**
 * 模型目录的全局快照。
 *
 * 以前每个模型胶囊（① 放素材、不用风格、工程页输入框）各自在挂载和每次展开时
 * 去拉 `/api/agent-settings` + `/api/agent-connections`，而服务端那份目录要起一次
 * Codex app-server 才读得出来（缓存 5 分钟就过期）。于是「点开一个面板」经常先看到
 * 「正在读取模型」，慢的时候要等十几秒 —— 同一个列表被重复读了 N 次。
 *
 * 现在：启动后读一次，之后所有胶囊、设置页共用这份快照；只有接入配置真的变了
 * （notifyConnectionsChanged / 模块默认模型改了）才重读。
 */
import {getAgentSettings} from '@/studio/api'
import type {AgentModel} from '@/studio/agent-settings'
import {connectionReadyForFilm, connectionRequest, type ConnectionView} from '@/studio/connections'
import type {GeneralAgentChoice} from './general-contract'

export type AgentCatalogView = {
  models: AgentModel[]
  standard: GeneralAgentChoice
  connections: ConnectionView[]
}

type Snapshot = {view?: AgentCatalogView; error: string; loading: boolean}

let snapshot: Snapshot = {error: '', loading: false}
let inflight: Promise<void> | undefined
const listeners = new Set<() => void>()

function publish(patch: Partial<Snapshot>) {
  snapshot = {...snapshot, ...patch}
  listeners.forEach(fn => fn())
}

export const catalogSnapshot = () => snapshot

export function subscribeAgentCatalog(fn: () => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** 读目录。并发调用共用同一个请求；force 只在「重新读取」按钮和接入变更时用。 */
export function loadAgentCatalog(force = false): Promise<void> {
  if (inflight && !force) return inflight
  publish({loading: true})
  const request = (async () => {
    try {
      const [view, list] = await Promise.all([getAgentSettings(), connectionRequest<{connections: ConnectionView[]}>()])
      publish({
        view: {
          models: view.catalog?.models ?? [],
          standard: view.settings.modules.generalEditing,
          connections: list.connections.filter(connectionReadyForFilm),
        },
        error: '',
        loading: false,
      })
    } catch (cause) {
      // 读失败时保留上一次的快照：网络抖一下不该把已经选好的模型变成「读取失败」。
      publish({error: cause instanceof Error ? cause.message : '读取接入状态失败', loading: false})
    }
  })().finally(() => { if (inflight === request) inflight = undefined })
  inflight = request
  return request
}

/** 启动预热：首屏就把模型列表拿好，用户点开胶囊时是现成的。 */
export function primeAgentCatalog() {
  if (snapshot.view || snapshot.loading || inflight) return
  void loadAgentCatalog()
}

if (typeof window !== 'undefined') {
  const refresh = () => void loadAgentCatalog(true)
  window.addEventListener('emotioncut-connections-changed', refresh)
  window.addEventListener('emotioncut:general-agent-changed', refresh)
}
