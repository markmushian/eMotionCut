import {useEffect, useState} from 'react'
import {Cpu} from 'lucide-react'
import {EFFORT_LABELS} from '../studio/agent-settings'
import {CONNECTION_DEFINITIONS} from '../../shared/agent-connections'
import './agent-model.css'

/** 智能体 id → 显示名（Codex 之外的出片智能体，模型由它们自己的默认值决定）。 */
const FILM_AGENT_NAMES: Record<string, string> = Object.fromEntries(CONNECTION_DEFINITIONS.filter(d => d.kind === 'agent').map(d => [d.id, d.name]))

type CatalogModel = {id: string; name: string; efforts: string[]; defaultEffort: string; isDefault?: boolean}
type Choice = {model: string; effort: string}
type ChoiceView = {
  choice: Choice | null
  catalog: {models: CatalogModel[]} | null
  catalogError?: string
  generalEditing?: Choice & {agent?: string}
}

/** 模型目录一页全局共用：监控台、发布中心、数据看板三处选择器读同一份。 */
let viewPromise: Promise<ChoiceView> | null = null
function loadView(): Promise<ChoiceView> {
  if (!viewPromise) viewPromise = fetch('/api/creator/agent/choice').then(async r => {
    const v = await r.json().catch(() => ({ok: false, error: '服务没有响应'}))
    if (!r.ok || v.ok === false) throw Error(v.error || '读取模型目录失败')
    return v.result as ChoiceView
  }).catch(e => { viewPromise = null; throw e })
  return viewPromise
}

export function useCreatorAgentModel() {
  const [view, setView] = useState<ChoiceView | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = () => { loadView().then(setView).catch(e => setError(e instanceof Error ? e.message : String(e))) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [])
  const save = async (choice: Choice) => {
    setBusy(true); setError('')
    try {
      const r = await fetch('/api/creator/agent/choice', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(choice)})
      const v = await r.json().catch(() => ({ok: false, error: '服务没有响应'}))
      if (!r.ok || v.ok === false) throw Error(v.error || '保存失败')
      setView(old => old ? {...old, choice: v.result} : old)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      reload()
    } finally { setBusy(false) }
  }
  return {view, error, busy, save}
}

/** 创作台 Agent 模型选择器：发布包、话题、封面提示词、复盘全部共用这份选择。 */
export function AgentModelPicker() {
  const {view, error, busy, save} = useCreatorAgentModel()
  // 模型目录只有 Codex 一家在读：通用剪辑出片切到别的智能体时，这里只保留「跟随默认」。
  const generalAgent = view?.generalEditing?.agent || 'codex'
  const filmAgentName = generalAgent === 'codex' ? 'Codex' : FILM_AGENT_NAMES[generalAgent] || generalAgent
  const filmCatalog = generalAgent === 'codex'
  const models = filmCatalog ? (view?.catalog?.models || []) : []
  const choice = view?.choice || {model: 'default', effort: 'medium'}
  const lockedFollow = !filmCatalog && choice.model === 'default'
  const current = models.find(m => m.id === choice.model) || null
  const efforts = current?.efforts || []
  const followLabel = view?.generalEditing ? `（${view.generalEditing.model === 'default' ? '默认模型' : view.generalEditing.model}）` : ''
  return <label className="am-pick" title={filmCatalog ? '创作台所有 Agent 任务（发布包、热门话题、封面提示词、数据复盘）都用这个模型和推理强度' : `通用剪辑出片用的是 ${filmAgentName}，模型由它自己决定；想选具体模型就把通用剪辑出片切回 Codex`}>
    <Cpu size={13}/>
    <span>Agent</span>
    <select
      value={choice.model}
      disabled={busy || (!view && !error)}
      onChange={e => {
        const model = e.target.value
        const effort = model === 'default' ? 'medium' : (models.find(m => m.id === model)?.defaultEffort || 'medium')
        void save({model, effort})
      }}
    >
      <option value="default">{filmCatalog ? `跟随「通用剪辑出片」${followLabel}` : `跟随「通用剪辑出片」的 ${filmAgentName} 默认模型`}</option>
      {models.map(m => <option key={m.id} value={m.id}>{m.name || m.id}{m.isDefault ? '（默认）' : ''}</option>)}
    </select>
    <select
      value={choice.effort}
      disabled={busy || !current}
      title={current ? '推理思考强度' : '选「跟随」时强度由「通用剪辑出片」决定'}
      onChange={e => void save({...choice, effort: e.target.value})}
    >
      {(efforts.length ? efforts : [choice.effort]).map(e => <option key={e} value={e}>{EFFORT_LABELS[e] || e}</option>)}
    </select>
    {lockedFollow && <em className="am-pick__err" style={{color:'inherit'}} title={`出片智能体是 ${filmAgentName}，模型由它决定`}>{filmAgentName} 默认</em>}
    {error && <em className="am-pick__err" title={error}>⚠ {error}</em>}
    {!error && view?.catalogError && !models.length && <em className="am-pick__err" title={view.catalogError}>模型目录未就绪</em>}
  </label>
}
