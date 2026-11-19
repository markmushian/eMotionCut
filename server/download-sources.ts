import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import {Readable} from 'node:stream'
import {pipeline} from 'node:stream/promises'

/**
 * 国内用户经常连不上 GitHub Releases（对象存储域名被重置）。
 * 这里把「官方地址」和「加速前缀」排成一条链：逐个试，下载完统一校验哈希。
 * 校验通过就说明内容与官方一致，镜像只当传输通道用。
 */
export type DownloadSource = {label: string; url: string}

/** 默认只对 github.com 拼前缀；其它域名（例如 cdn.ego.app）走原地址。 */
const GITHUB_MIRRORS = [
  'https://gh-proxy.com/',
  'https://ghproxy.net/',
  'https://ghfast.top/',
  'https://mirror.ghproxy.com/',
]

export function mirrorPrefixes(env: NodeJS.ProcessEnv = process.env) {
  const configured = env.EMOTIONCUT_DOWNLOAD_MIRRORS?.trim()
  if (configured === '-') return []
  if (!configured) return GITHUB_MIRRORS
  return configured.split(',').map(value => value.trim()).filter(Boolean).map(value => value.endsWith('/') ? value : value + '/')
}

export function sourceChain(url: string, env: NodeJS.ProcessEnv = process.env): DownloadSource[] {
  const chain: DownloadSource[] = [{label: '官方', url}]
  let hostname = ''
  try { hostname = new URL(url).hostname } catch { return chain }
  if (hostname === 'github.com' || hostname.endsWith('.githubusercontent.com')) {
    for (const prefix of mirrorPrefixes(env)) chain.push({label: new URL(prefix).hostname, url: prefix + url})
  }
  return chain
}

export type DownloadProgress = {received: number; total: number; source: string}

export async function downloadVerified(options: {
  url: string
  file: string
  sha256?: string
  onProgress?: (progress: DownloadProgress) => void
  timeoutMs?: number
  env?: NodeJS.ProcessEnv
}): Promise<{file: string; bytes: number; source: string; sha256: string}> {
  const {url, file, sha256, onProgress} = options
  fs.mkdirSync(path.dirname(file), {recursive: true})
  const parts: string[] = []
  for (const source of sourceChain(url, options.env)) {
    const pending = `${file}.${crypto.randomUUID()}.part`
    try {
      const response = await fetch(source.url, {
        headers: {'user-agent': 'eMotionCut/2.0 installer', accept: 'application/octet-stream'},
        redirect: 'follow',
        signal: AbortSignal.timeout(options.timeoutMs ?? 300_000),
      })
      if (!response.ok || !response.body) throw Error(`${source.label} 返回 ${response.status}`)
      const total = Number(response.headers.get('content-length') || 0)
      const hash = crypto.createHash('sha256')
      let received = 0
      const body = Readable.fromWeb(response.body as Parameters<typeof Readable.fromWeb>[0])
      body.on('data', (chunk: Buffer) => {
        hash.update(chunk)
        received += chunk.length
        onProgress?.({received, total, source: source.label})
      })
      await pipeline(body, fs.createWriteStream(pending, {flags: 'wx'}))
      const actual = hash.digest('hex')
      if (sha256 && actual !== sha256) throw Error(`${source.label} 下载内容与官方校验值不一致`)
      fs.renameSync(pending, file)
      return {file, bytes: received, source: source.label, sha256: actual}
    } catch (error) {
      try { fs.unlinkSync(pending) } catch {}
      parts.push(`${source.label}：${error instanceof Error ? error.message : String(error)}`)
    }
  }
  throw Error(`下载失败，已尝试 ${parts.length} 个来源。${parts.join('；')}`)
}
