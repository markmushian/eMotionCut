/**
 * 封面一键生图：直接调火山方舟 Seedream 的同步生图 API，不经过 Agent。
 *
 * 为什么绕过 Agent：本地命令行 Agent（Codex/Claude）只能产出文字提示词，
 * 运行时还显式 --disable image_generation，本身不会生成图片文件。真正出图
 * 要走豆包方舟 Seedream。应用里已经有 dreamina 服务的配置与系统钥匙串密钥，
 * 这里补上「读密钥 → POST 生图 → 下载落盘 → 交给现有 /api/matrix/cover/file 展示」。
 */
import fs from 'node:fs'
import path from 'node:path'
import {getConnectionConfig, connectionEndpoint, validateConnectionBaseUrl, type ConnectionDependencies} from './agent-connections.ts'
import {connectionSecretStore} from './connection-secrets.ts'
import {ratioSize} from './cover-platforms.ts'

/** 没在设置里填接入点时用的官方默认模型。 */
export const DEFAULT_SEEDREAM_MODEL = 'doubao-seedream-5-0-260128'

/**
 * 默认仍然出三版（竖版母版 + 3:4 + 4:3）；
 * 但平台档案确定后，调用方可以只点自己需要的比例——4:3 现在没有任何平台在用。
 */
export const COVER_SIZES = [
  {key: '9x16', label: '9:16 竖版母版', size: '2160x3840'},
  {key: '3x4', label: '3:4', size: '2160x2880'},
  {key: '4x3', label: '4:3', size: '2880x2160'},
] as const

/** 比例 → Seedream 的「宽x高」写法；不在表里的比例直接丢掉，不猜。 */
function sizesFor(ratios?: string[]) {
  if (!Array.isArray(ratios) || !ratios.length) return [...COVER_SIZES]
  const out: {key: string; label: string; size: string}[] = []
  for (const ratio of ratios) {
    const size = ratioSize(ratio)
    if (!size) continue
    const key = ratio.replace(':', 'x')
    if (out.some(row => row.key === key)) continue
    out.push({key, label: ratio, size: `${size.width}x${size.height}`})
  }
  return out.length ? out : [...COVER_SIZES]
}

export type GeneratedCover = {file: string; size: string; ratio: string; label: string}
export type CoverGenerateResult = {files: GeneratedCover[]; model: string; count: number}

type SeedreamImage = {url?: string; b64_json?: string; size?: string}
type SeedreamResponse = {data?: SeedreamImage[]; error?: {message?: string}; usage?: unknown}

const MIME: Record<string, string> = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}

/** 把落盘的人物参考照读成 Seedream 的 data:image base64 入参，最多 2 张。 */
export function referenceImages(paths: string[]): string[] {
  return (paths || []).slice(0, 2).flatMap(file => {
    try {
      if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) return []
      const ext = path.extname(file).toLowerCase()
      const mime = MIME[ext]
      if (!mime) return []
      const buf = fs.readFileSync(file)
      if (buf.length > 12 * 1024 * 1024) return []
      return [`data:${mime};base64,${buf.toString('base64')}`]
    } catch { return [] }
  })
}

/**
 * 生成一组封面图并落盘。
 * @param prompt 成品生图提示词（来自 cover-playbook / 用户框里的内容）
 * @param refPaths 已落盘的人物参考照绝对路径（可为空 → 文生图）
 * @param targetDir 图片输出目录（creator-matrix 的 covers/）
 * @param deps 仅测试注入用
 */
export async function generateCoverImages(
  prompt: string,
  refPaths: string[],
  targetDir: string,
  deps: ConnectionDependencies = {},
  ratios?: string[],
): Promise<CoverGenerateResult> {
  const clean = String(prompt || '').trim()
  if (!clean) throw Object.assign(Error('先写封面提示词，或点「按规范生成」让 Agent 写一段'), {status: 400})

  const config = getConnectionConfig('dreamina')
  const key = deps.store ? await deps.store.read('dreamina') : await connectionSecretStore().read('dreamina')
  if (!key) throw Object.assign(Error('还没配置豆包方舟密钥：去「系统设置 → 账号与服务接入 → 豆包方舟」填 API Key，再回来一键生图'), {status: 400})

  const baseUrl = validateConnectionBaseUrl(config.baseUrl || 'https://ark.cn-beijing.volces.com/api/v3')
  const endpoint = connectionEndpoint(baseUrl, 'images/generations')
  const model = config.model && config.model !== 'default' ? config.model : DEFAULT_SEEDREAM_MODEL
  const images = referenceImages(refPaths)
  const timeoutMs = deps.timeoutMs || 180000
  const fetcher = deps.fetcher || fetch

  fs.mkdirSync(targetDir, {recursive: true})
  const stamp = Date.now()
  const files: GeneratedCover[] = []

  for (const spec of sizesFor(ratios)) {
    const body: Record<string, unknown> = {
      model,
      prompt: clean,
      size: spec.size,
      response_format: 'url',
      watermark: false,
    }
    if (images.length) body.image = images

    const response = await fetcher(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${key}`},
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    })
    const raw = await response.text()
    if (!response.ok) {
      const msg = parseError(raw, response.status)
      throw Object.assign(Error(`[${spec.label}] ${msg}`), {status: response.status === 401 ? 401 : 502})
    }
    let parsed: SeedreamResponse
    try { parsed = JSON.parse(raw.split(key).join('[密钥已隐藏]')) } catch { throw Object.assign(Error(`[${spec.label}] 生图返回无法解析`), {status: 502}) }
    if (parsed.error?.message) throw Object.assign(Error(`[${spec.label}] ${parsed.error.message}`), {status: 502})
    const item = parsed.data?.[0]
    if (!item) throw Object.assign(Error(`[${spec.label}] 生图服务没返回图片`), {status: 502})

    const buffer = await downloadImage(item, key, fetcher, timeoutMs)
    const ext = item.url && /\.jpe?g(\?|$)/i.test(item.url) ? 'jpg' : 'png'
    const file = `generated-${stamp}-${spec.key}.${ext}`
    fs.writeFileSync(path.join(targetDir, file), buffer)
    files.push({file, size: item.size || spec.size, ratio: `${spec.key}`, label: spec.label})
  }

  return {files, model, count: files.length}
}

async function downloadImage(item: SeedreamImage, key: string, fetcher: typeof fetch, timeoutMs: number): Promise<Buffer> {
  if (item.b64_json) return Buffer.from(item.b64_json, 'base64')
  if (!item.url) throw Error('图片结果既没有 url 也没有 b64_json')
  const response = await fetcher(item.url, {signal: AbortSignal.timeout(timeoutMs)})
  if (!response.ok) throw Error(`下载图片失败（HTTP ${response.status}）`)
  const ab = await response.arrayBuffer()
  if (ab.byteLength < 1024) throw Error('下载的图片太小，可能不是有效图片')
  return Buffer.from(ab)
}

function parseError(raw: string, status: number): string {
  try {
    const body = JSON.parse(raw) as {error?: {message?: string; code?: string}; message?: string}
    const msg = body.error?.message || body.message || ''
    const map: Record<string, string> = {
      OutputImageSensitiveContentDetected: '生成的画面疑似敏感内容，改一下提示词再试',
      InputTextSensitiveContentDetected: '提示词疑似敏感内容，改一下再试',
      InputImageSensitiveContentDetected: '人物参考照疑似敏感内容，换一张再试',
      QuotaExceeded: '方舟名额已满或限流，稍后再试',
    }
    if (body.error?.code && map[body.error.code]) return map[body.error.code]
    if (msg) return msg
  } catch { /* 非 JSON 响应，用 HTTP 状态兜底 */ }
  return status === 401 ? 'API Key 无效或过期，去设置里重新填' : `生图服务返回 HTTP ${status}`
}
