/** 发布台与监控台共用的两个出口：矩阵接口、Agent 文本任务。 */

export async function matrixApi<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/matrix/' + path, {headers: {'Content-Type': 'application/json'}, ...init})
  const body = await response.json().catch(() => ({ok: false, error: '服务没有响应'}))
  if (!response.ok || body.ok === false) throw Error(body.error || `请求失败（${response.status}）`)
  return body.result as T
}

/** 走通用 Agent 桥：和封面提示词、发布包同一个出口，用的是用户自己配的 Agent。 */
export async function askAgent(system: string, user: string): Promise<{text: string; agent: string}> {
  const response = await fetch('/api/creator/agent', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({system, user})})
  const body = await response.json().catch(() => ({ok: false, error: '服务没有响应'}))
  if (!response.ok || body.ok === false) throw Error(body.error || 'Agent 没有响应')
  return body.result as {text: string; agent: string}
}
