/**
 * 封面主副标题提炼。
 *
 * 视频标题是给信息流与搜索看的，通常是一整句（「第12集 | 她转身离开时才发…完整版」）；
 * 直接印到封面上会又长又平。这里让 Agent 把它压缩成两行大字 + 一行补充信息，
 * 风格对齐参考图：主标题短、冲突强、字大；副标题只补对象/数字/场景，不重复主标题。
 * Agent 连不上时用本地规则兜底，绝不把整条视频标题原样塞上封面。
 */
import {askCreatorAgent} from './creator-agent-bridge.ts'

export type CoverHeadline = {main: string; sub: string; keywords: string[]; by: 'agent' | 'local'}

const NOISE = /第\s*\d+\s*集|全集|完整版|大结局|HD|4K|短剧|高清|合集|上集|下集|预告|抢先看/gi

/** 只留干净文本，避免把标题里的括号备注、emoji、版权尾巴印到封面。 */
function clean(text: string | undefined): string {
  return String(text || '')
    .replace(NOISE, ' ')
    .replace(/[【】\[\]（）()]/g, ' ')
    .replace(/[|｜/\\~—…·\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** 本地兜底：从原句里截出最像「一句话卖点」的一段，够短就好，不硬造文案。 */
export function localHeadline(input: {title?: string; tags?: string[]; desc?: string}): CoverHeadline {
  const parts = clean([input.title, input.desc].filter(Boolean).join(' '))
    .split(/[，,。！!？?：:；;]/)
    .map(x => x.trim())
    .filter(x => x.length >= 2)
  const main = (parts.find(x => x.length >= 4 && x.length <= 12) || parts[0] || clean(input.title) || '看这里').slice(0, 10)
  const subSource = parts.find(x => x !== main) || (input.tags || []).join(' ')
  const sub = clean(subSource).slice(0, 16)
  return {main, sub, keywords: [main.slice(0, 4)], by: 'local'}
}

function pickJson(text: string): Record<string, unknown> | null {
  const body = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim()
  const start = body.indexOf('{'), end = body.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  try { return JSON.parse(body.slice(start, end + 1)) as Record<string, unknown> } catch { return null }
}

/**
 * 提炼封面用主副标题。Agent 失败或输出不合规时退回本地结果，
 * 保证「按比例套版」永远有短标题可用，也不会卡死在 Agent 上。
 */
export async function extractHeadline(input: {title?: string; tags?: string[]; desc?: string; platform?: string}): Promise<CoverHeadline> {
  const fallback = localHeadline(input)
  const source = [input.title, input.desc, (input.tags || []).join(' ')].filter(Boolean).join(' | ').slice(0, 600)
  if (!source.trim()) return fallback
  try {
    const answer = await askCreatorAgent(
      `你是短视频爆款封面的标题编辑，只做「压缩与提炼」，不做创作发挥。
只输出一行 JSON：{"main":"...","sub":"...","keywords":["...","..."]}，不要解释、不要 Markdown。
硬性规则：
1. main 4–10 个汉字，最多两行（中间可用空格断行），必须比原视频标题短；保留原句里的核心名词与冲突/结果/悬念，不允许改写成一个泛泛的口号。
2. 禁止出现：第N集、全集、完整版、大结局、HD、4K、短剧、高清、合集、预告、片名书名号、表情符号、标点结尾。
3. sub 6–16 个汉字，只补充对象、场景、数字或反差，和 main 不重复、不换说法复述。
4. keywords 从 main/sub 里挑 1–2 个最有信息量的词，必须是原文出现过的连续片段。
5. 全篇用中文；拿不到足够信息就用原句里最短的卖点，不要编数字、不要编人物身份。`,
      `平台：${input.platform || '抖音'}
视频标题：${input.title || ''}
话题：${(input.tags || []).join(' ')}
简介：${input.desc || ''}`,
      90_000,
    )
    const json = pickJson(answer.text)
    const main = clean(String(json?.main || '')).slice(0, 12)
    const sub = clean(String(json?.sub || '')).slice(0, 18)
    const keywords = Array.isArray(json?.keywords)
      ? json.keywords.map(x => clean(String(x)).slice(0, 6)).filter(Boolean).slice(0, 2)
      : []
    if (main.length < 2) return fallback
    return {main, sub, keywords, by: 'agent'}
  } catch { return fallback }
}
