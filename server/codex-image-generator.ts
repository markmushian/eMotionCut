/**
 * 用本机 Codex CLI 的内置 image_gen 工具出图。
 *
 * 为什么这么做：Codex 的 image_gen 走 ChatGPT 账号额度，不需要 OpenAI API Key；
 * 而且它原生支持「参考图 + 提示词」的多图输入（-i 可重复），正好用来做封面风格锁定和真人形象复用。
 *
 * 实测约定（2026-11-16，codex-cli 0.169.0-alpha.11.1）：
 * - 生图落在 `$CODEX_HOME/generated_images/<thread_id>/exec-<call_id>.png`，文件名不可预判，只能按目录取。
 * - thread_id 从 `codex exec --json` 的 `{"type":"thread.started","thread_id":"..."}` 事件里拿。
 * - `-i/--image` 是多值参数，会把后面的位置参数一起吃掉；prompt 必须走 stdin（末尾用 `-`）。
 * - 它自己会读 `~/.codex/skills/.system/imagegen/SKILL.md` 做提示词增强，不要重复包一层方法论。
 */
import { spawn } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export type CodexImageFile = { path: string; file: string; bytes: number; width?: number; height?: number }

export type CodexImageResult = {
  files: CodexImageFile[]
  threadId: string
  ms: number
  cli: string
  /** Codex 最后一条自述，用于判断它到底做了生图还是只回了文字。 */
  message: string
  refs: number
}

export function codexHome(): string {
  const configured = process.env.CODEX_HOME
  return configured && configured.trim() ? configured : path.join(os.homedir(), '.codex')
}

/** 某个会话产出的图片目录。thread_id 必须形如 uuid，避免被当成路径穿越。 */
export function codexThreadDir(threadId: string): string | undefined {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(threadId)) return undefined
  return path.join(codexHome(), 'generated_images', threadId)
}

/**
 * 定位 Codex CLI。顺序：环境变量 → PATH → Windows 应用包。
 * 找不到就返回 undefined，让上层明确提示「还没装 Codex」，绝不假装成功。
 * Windows 的 WindowsApps 目录包很多，扫一次要几十毫秒，结果缓存 30 秒。
 */
let cliCache: { value: string | undefined; at: number } | null = null

export function findCodexCli(): string | undefined {
  if (cliCache && Date.now() - cliCache.at < 30_000) return cliCache.value
  cliCache = { value: scanCodexCli(), at: Date.now() }
  return cliCache.value
}

function scanCodexCli(): string | undefined {
  const explicit = process.env.CODEX_BIN || process.env.CODEX_CLI
  if (explicit && fs.existsSync(explicit)) return explicit

  const names = process.platform === 'win32' ? ['codex.exe', 'codex.cmd', 'codex.bat', 'codex'] : ['codex']
  for (const dir of String(process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue
    for (const name of names) {
      const file = path.join(dir, name)
      try {
        if (fs.existsSync(file) && fs.statSync(file).isFile()) return file
      } catch { /* 目录不可读就跳过 */ }
    }
  }

  if (process.platform === 'win32') {
    const roots = [
      path.join(process.env.ProgramFiles || 'C:\\Program Files', 'WindowsApps'),
      path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'Microsoft', 'WindowsApps'),
    ]
    for (const root of roots) {
      let entries: string[] = []
      try { entries = fs.readdirSync(root) } catch { continue }
      const candidates = entries.filter(name => /^OpenAI\.Codex_/i.test(name)).sort().reverse()
      for (const entry of candidates) {
        for (const rel of [['app', 'resources', 'codex.exe'], ['codex.exe']]) {
          const exe = path.join(root, entry, ...rel)
          try {
            if (fs.existsSync(exe)) return exe
          } catch { /* 权限受限就跳过 */ }
        }
      }
    }
  }
  return undefined
}

/** 从 PNG/JPEG/WEBP 头部读尺寸，读不出返回空，不阻塞主流程。 */
export function imageSize(buffer: Buffer): { width?: number; height?: number } {
  try {
    if (buffer.length > 24 && buffer.slice(1, 4).toString('latin1') === 'PNG') {
      return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) }
    }
    if (buffer.length > 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
      let offset = 2
      while (offset + 9 < buffer.length) {
        if (buffer[offset] !== 0xff) { offset += 1; continue }
        const marker = buffer[offset + 1]
        const size = buffer.readUInt16BE(offset + 2)
        if (marker >= 0xc0 && marker <= 0xc3) {
          return { height: buffer.readUInt16BE(offset + 5), width: buffer.readUInt16BE(offset + 7) }
        }
        offset += 2 + size
      }
    }
    if (buffer.length > 30 && buffer.slice(0, 4).toString('latin1') === 'RIFF' && buffer.slice(8, 12).toString('latin1') === 'WEBP') {
      const kind = buffer.slice(12, 16).toString('latin1')
      if (kind === 'VP8X') {
        return {
          width: 1 + buffer.readUIntLE(24, 3),
          height: 1 + buffer.readUIntLE(27, 3),
        }
      }
    }
  } catch { /* 损坏文件按未知处理 */ }
  return {}
}

export type CodexGenerateInput = {
  prompt: string
  /** 参考图绝对路径：固定出镜人物、风格参考封面等。最多 4 张，Codex 多图输入会变慢。 */
  refs?: string[]
  /** 期望张数，仅用于组织提示词；Codex 实际出图张数以目录里新增的文件为准。 */
  count?: number
  ratio?: string
  timeoutMs?: number
  onStep?: (step: string) => void
}

/** Codex 会并发抢同一个 CODEX_HOME，串行跑，避免会话目录互相污染。 */
let queue: Promise<unknown> = Promise.resolve()

export async function generateWithCodex(input: CodexGenerateInput): Promise<CodexImageResult> {
  const run = () => runCodex(input)
  const next = queue.then(run, run)
  queue = next.catch(() => undefined)
  return next
}

async function runCodex(input: CodexGenerateInput): Promise<CodexImageResult> {
  const prompt = String(input.prompt || '').trim()
  if (prompt.length < 8) throw Object.assign(Error('提示词太短，至少写清主体和画面要求'), { status: 400 })

  const cli = findCodexCli()
  if (!cli) {
    throw Object.assign(
      Error('没找到本机 Codex。请先安装 Codex 桌面版或 CLI，或在环境变量 CODEX_BIN 里指定 codex.exe 路径。'),
      { status: 400 },
    )
  }

  const refs = (input.refs || [])
    .filter(file => { try { return fs.existsSync(file) && fs.statSync(file).size > 512 } catch { return false } })
    .slice(0, 4)

  const started = Date.now()
  const timeoutMs = Math.min(Math.max(input.timeoutMs || 240_000, 60_000), 600_000)
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), 'emotioncut-codex-'))
  const count = Math.min(Math.max(Number(input.count) || 1, 1), 4)
  const ratio = String(input.ratio || '').trim()

  const framed = [
    prompt,
    '',
    '---',
    '生成要求（来自 eMotionCut 封面工作台）：',
    `- 用你内置的 image_gen 工具生成 ${count} 张图片；禁止用代码、脚本或矢量绘制替代，禁止复制已有文件。`,
    ratio ? `- 画面比例：${ratio}（竖版封面），不要加白边。` : '',
    refs.length ? `- 我附上了 ${refs.length} 张参考图：人物照请保持长相与气质一致；风格图请只沿用配色与质感，不要照抄构图。` : '',
    '- 画面里不要出现任何文字、字幕、水印、平台 logo，标题区留白由后期排版加。',
    '- 直接出图，不要先解释。出完图一句话说明即可。',
  ].filter(Boolean).join('\n')

  const args = ['exec', '--json', '--sandbox', 'workspace-write', '--skip-git-repo-check', '-C', workdir]
  for (const ref of refs) args.push('-i', ref)
  args.push('-')

  input.onStep?.(refs.length ? `Codex 正在读取 ${refs.length} 张参考图并出图…` : 'Codex 正在出图…')

  const stdout = await spawnCodex(cli, args, framed, workdir, timeoutMs)
  const ms = Date.now() - started

  const threadId = /"type":"thread\.started"[^}]*"thread_id":"([0-9a-f-]{36})"/i.exec(stdout)?.[1]
    || /"thread_id":"([0-9a-f-]{36})"/i.exec(stdout)?.[1]
    || ''
  const message = [...stdout.matchAll(/"type":"item\.completed","item":\{[^}]*"type":"agent_message","text":"((?:[^"\\]|\\.)*)"/g)]
    .map(match => {
      try { return JSON.parse(`"${match[1]}"`) as string } catch { return match[1] }
    })
    .join('\n')
    .trim()

  if (!threadId) {
    const hint = /not logged in|login required|401|unauthorized/i.test(stdout) ? 'Codex 还没登录，请先在 Codex 里登录 ChatGPT 账号。' : ''
    throw Object.assign(Error(hint || `Codex 没有返回会话信息，可能启动失败。输出片段：${stdout.slice(-400) || '(空)'}`), { status: 502 })
  }

  const dir = codexThreadDir(threadId)
  const files: CodexImageFile[] = []
  if (dir && fs.existsSync(dir)) {
    for (const name of fs.readdirSync(dir)) {
      if (!/\.(png|jpe?g|webp)$/i.test(name)) continue
      const full = path.join(dir, name)
      try {
        const buffer = fs.readFileSync(full)
        files.push({ path: full, file: name, bytes: buffer.length, ...imageSize(buffer) })
      } catch { /* 读不到就跳过 */ }
    }
  }

  cleanup(workdir)

  if (!files.length) {
    throw Object.assign(
      Error(`Codex 这次没有出图${message ? `，它说：${message.slice(0, 200)}` : ''}。可以再试一次，或改用「一键生图」(Seedream)。`),
      { status: 502 },
    )
  }

  return { files, threadId, ms, cli, message: message.slice(0, 500), refs: refs.length }
}

function spawnCodex(cli: string, args: string[], prompt: string, cwd: string, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const isCmd = /\.(cmd|bat)$/i.test(cli)
    const command = isCmd ? (process.env.ComSpec || 'cmd.exe') : cli
    const commandArgs = isCmd ? ['/d', '/s', '/c', `"${cli}"`, ...args] : args

    const child = spawn(command, commandArgs, {
      cwd,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    })

    let out = ''
    let err = ''
    let killed = false
    const timer = setTimeout(() => {
      killed = true
      try { child.kill('SIGKILL') } catch { /* 已退出 */ }
    }, timeoutMs)

    child.stdout?.on('data', chunk => {
      // 只留够解析 thread_id / 消息的体积，事件流里有大量 skill 文本
      if (out.length < 1_500_000) out += String(chunk)
    })
    child.stderr?.on('data', chunk => {
      if (err.length < 20_000) err += String(chunk)
    })
    child.on('error', cause => {
      clearTimeout(timer)
      reject(Object.assign(Error(`Codex 启动失败：${cause.message}`), { status: 500 }))
    })
    child.on('close', code => {
      clearTimeout(timer)
      if (killed) {
        reject(Object.assign(Error(`Codex 超过 ${Math.round(timeoutMs / 1000)} 秒没出图，已中断。封面出图通常 40–120 秒，可再试一次。`), { status: 504 }))
        return
      }
      if (code !== 0 && !out.includes('turn.completed')) {
        reject(Object.assign(Error(`Codex 退出码 ${code}。${(err || out).slice(-400)}`), { status: 502 }))
        return
      }
      resolve(out)
    })

    try {
      child.stdin?.write(prompt)
      child.stdin?.end()
    } catch { /* 进程已退出 */ }
  })
}

function cleanup(dir: string) {
  try {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 2 })
  } catch { /* 临时目录清不掉不影响结果 */ }
}

/** 生成一个不撞名的封面文件名。 */
export function codexCoverName(index: number, ext = 'png'): string {
  return `codex-${Date.now()}-${index}-${crypto.randomBytes(2).toString('hex')}.${ext}`
}
