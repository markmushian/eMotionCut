import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {spawn, spawnSync} from 'node:child_process'
import {downloadVerified} from './download-sources.ts'
import {findOpenScreenExecutable, openScreenEnvironmentStatus} from './editor/openscreen.ts'
import {EGO_DOWNLOAD_URL, egoExecutable} from './browser-executable.ts'

/**
 * 一键下载安装。两个包都在境外（GitHub Releases / cdn.ego.app），国内直连经常失败，
 * 所以：多源下载 + 内容校验 + 静默安装 + 进度可查。
 * 校验不过就绝不执行——这是唯一能放心用镜像的前提。
 */
export type InstallKind = 'openscreen' | 'ego'
export type InstallState = 'idle' | 'downloading' | 'verifying' | 'installing' | 'done' | 'failed'

export type InstallJob = {
  kind: InstallKind
  state: InstallState
  /** 0–100；总大小未知时为 undefined */
  percent?: number
  received: number
  total: number
  source: string
  step: string
  file?: string
  error?: string
  startedAt: number
  endedAt?: number
}

/** 与 WINDOWS-RUNTIME-LOCK.json 的 installerSha256 保持一致；版本一变必须同步改这里。 */
type PackageSpec = {version: string; url: string; sha256?: string; file: string; publisher?: RegExp}

export const OPENSCREEN_PACKAGE: PackageSpec = {
  version: '2.1.2',
  url: 'https://github.com/getopenscreen/openscreen/releases/download/v2.1.2/Openscreen.Setup.2.1.2-x64.exe',
  sha256: '55a7adcd8517159198e2f7a2e31cd69b91c3ce4fb14cf3f56e9809681ccbdfcb',
  file: 'Openscreen.Setup.2.1.2-x64.exe',
}

export const EGO_PACKAGE: PackageSpec = {
  version: 'latest',
  url: EGO_DOWNLOAD_URL,
  /** 官方地址永远是「最新版」，没法钉哈希；改用发布者签名当信任锚。 */
  file: process.platform === 'darwin' ? 'egolite.dmg' : 'egoliteSetup.exe',
  publisher: /CITRO LABS/i,
}

const packages: Record<InstallKind, PackageSpec> = {openscreen: OPENSCREEN_PACKAGE, ego: EGO_PACKAGE}
const jobs = new Map<InstallKind, InstallJob>()

export const installerFolder = () => path.join(os.tmpdir(), 'emotioncut-installers')

/** 已经装好就别再下载一遍。 */
export function installPresence(kind: InstallKind) {
  if (kind === 'openscreen') {
    const executable = findOpenScreenExecutable()
    return {ready: Boolean(executable), path: executable, version: executable ? openScreenEnvironmentStatus().version : undefined}
  }
  const executable = egoExecutable()
  return {ready: Boolean(executable), path: executable, version: undefined}
}

export function readInstallJob(kind: InstallKind): InstallJob {
  return jobs.get(kind) ?? {kind, state: 'idle', received: 0, total: 0, source: '', step: '', startedAt: 0}
}

function patch(kind: InstallKind, next: Partial<InstallJob>) {
  const current = readInstallJob(kind)
  jobs.set(kind, {...current, ...next})
}

/** 只认有效签名且发布者对得上的安装包；否则宁可让用户手动装。 */
export function installerSignature(file: string) {
  if (process.platform !== 'win32') return {status: 'Unknown', subject: ''}
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
    `$s=Get-AuthenticodeSignature -LiteralPath '${file.replace(/'/g, "''")}'; "$($s.Status)|$($s.SignerCertificate.Subject)"`,
  ], {encoding: 'utf8', timeout: 30_000, windowsHide: true})
  const [status = 'Unknown', subject = ''] = String(result.stdout || '').trim().split('|')
  return {status, subject}
}

function runInstaller(file: string, kind: InstallKind) {
  if (process.platform === 'darwin') {
    return new Promise<void>((resolve, reject) => {
      const child = spawn('/usr/bin/open', ['-W', file], {stdio: 'ignore'})
      child.once('error', reject)
      child.once('close', code => code === 0 ? resolve() : reject(Error(`安装程序退出（${code}）`)))
    })
  }
  return new Promise<void>((resolve, reject) => {
    // electron-builder 的 NSIS 安装包支持 /S 静默安装；装完仍在原版本目录，检测能直接认。
    const child = spawn(file, ['/S'], {detached: false, stdio: 'ignore', windowsHide: true})
    const timer = setTimeout(() => {try{child.kill()}catch{}}, 15 * 60_000)
    timer.unref()
    child.once('error', error => {clearTimeout(timer); reject(error)})
    child.once('close', code => {
      clearTimeout(timer)
      if (code === 0) resolve()
      else reject(Error(`${kind === 'ego' ? 'Ego Lite' : 'OpenScreen'} 安装程序退出（${code}），可能被系统拦下了`))
    })
  })
}

async function install(kind: InstallKind, options: {file?: string} = {}) {
  const spec = packages[kind]
  patch(kind, {state: 'downloading', step: '准备下载', percent: 0, received: 0, total: 0, source: '', error: undefined, startedAt: Date.now()})
  const target = path.join(installerFolder(), spec.file)
  let source = '本地安装包'
  if (options.file) {
    if (!fs.existsSync(options.file)) throw Error('选中的安装包不存在')
    fs.copyFileSync(options.file, target)
  } else {
    const cached = fs.existsSync(target)
    if (!(cached && spec.sha256)) {
      const result = await downloadVerified({
        url: spec.url, file: target, sha256: spec.sha256,
        onProgress: ({received, total, source: label}) => patch(kind, {
          state: 'downloading', source: label, received, total,
          percent: total ? Math.min(99, Math.round(received / total * 100)) : undefined,
          step: total ? `正在从 ${label} 下载` : `正在从 ${label} 下载（大小未知）`,
        }),
      })
      source = result.source
    }
  }
  patch(kind, {state: 'verifying', percent: 100, step: '校验安装包'})
  if (spec.sha256) {
    const {createHash} = await import('node:crypto')
    const hash = createHash('sha256').update(fs.readFileSync(target)).digest('hex')
    if (hash !== spec.sha256) throw Error(`安装包校验不通过（${hash.slice(0, 12)}…），已停止。请重试或手动安装。`)
  } else if (spec.publisher) {
    const {status, subject} = installerSignature(target)
    if (status !== 'Valid' || !spec.publisher.test(subject)) throw Error(`安装包签名不可信（${status}），已停止。请从官方站点手动安装。`)
  }
  patch(kind, {state: 'installing', step: '正在安装', source})
  await runInstaller(target, kind)
  const presence = installPresence(kind)
  patch(kind, {
    state: 'done', percent: 100, file: presence.path || target, step: presence.ready ? '安装完成' : '安装程序已结束，但没有检测到程序',
    error: presence.ready ? undefined : '安装程序已运行，但没有在预期位置找到程序。请手动启动一次，或改用官方安装包。',
    endedAt: Date.now(),
  })
}

export function startInstall(kind: InstallKind, options: {file?: string} = {}) {
  const current = readInstallJob(kind)
  if (['downloading', 'verifying', 'installing'].includes(current.state)) return current
  void install(kind, options).catch(error => {
    patch(kind, {state: 'failed', step: '没装成', error: error instanceof Error ? error.message : String(error), endedAt: Date.now()})
  })
  return readInstallJob(kind)
}
