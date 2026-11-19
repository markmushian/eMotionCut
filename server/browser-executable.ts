import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * 发布、抓作品、MotionGit 授权都要一个「能连 CDP 的浏览器」。
 * 默认用 Ego Lite：它基于 Chromium（当前 156），接受 --remote-debugging-port
 * 和独立的 --user-data-dir，所以每个账号仍然能有自己的登录态。
 *
 * 注意 ~/.local/share/ego/active_version_dir/ego-browser.exe 是 CLI 桩，
 * 起起来不会开调试端口，别拿它当浏览器；真身是安装目录里的 ego.exe。
 */
export type BrowserKind = 'ego' | 'chrome' | 'edge'
export type BrowserTarget = { kind: BrowserKind; path: string }

export const EGO_SITE = 'https://lite.ego.app/'
export const EGO_DOWNLOAD_URL = process.platform === 'darwin'
  ? (process.arch === 'arm64' ? 'https://cdn.ego.app/setup/macos/arm64/egolite.dmg' : 'https://cdn.ego.app/setup/macos/x64/egolite.dmg')
  : 'https://cdn.ego.app/setup/windows/x64/egoliteSetup.exe'

const exists = (file?: string): file is string => Boolean(file) && fs.existsSync(file!)

function winDirs(env: NodeJS.ProcessEnv) {
  return [env.ProgramFiles, env['ProgramFiles(x86)'], env.LOCALAPPDATA && path.join(env.LOCALAPPDATA, 'Programs')]
    .filter((value): value is string => Boolean(value))
}

/** Ego 装在 Citro Labs 目录下；两台机器的安装位置不同，两个都试。 */
function egoCandidates(env: NodeJS.ProcessEnv = process.env) {
  if (process.platform === 'darwin') return [
    '/Applications/ego lite.app/Contents/MacOS/ego',
    path.join(os.homedir(), 'Applications/ego lite.app/Contents/MacOS/ego'),
  ]
  if (process.platform !== 'win32') return []
  return [
    env.EMOTIONCUT_EGO_BROWSER,
    ...winDirs(env).flatMap(dir => [
      path.join(dir, 'Citro Labs', 'ego lite', 'Application', 'ego.exe'),
      path.join(dir, 'ego lite', 'Application', 'ego.exe'),
    ]),
    env.LOCALAPPDATA && path.join(env.LOCALAPPDATA, 'Citro Labs', 'ego lite', 'Application', 'ego.exe'),
  ].filter((value): value is string => Boolean(value))
}

function chromeCandidates(env: NodeJS.ProcessEnv = process.env) {
  if (process.platform === 'darwin') return ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
  if (process.platform !== 'win32') return ['/usr/bin/google-chrome']
  return [
    env.EMOTIONCUT_CHROME,
    env.LOCALAPPDATA && path.join(env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    env.ProgramFiles && path.join(env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    env['ProgramFiles(x86)'] && path.join(env['ProgramFiles(x86)'], 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ].filter((value): value is string => Boolean(value))
}

/** Edge 兜底：国内机器基本都有，同样说 CDP。 */
function edgeCandidates(env: NodeJS.ProcessEnv = process.env) {
  if (process.platform === 'darwin') return ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
  if (process.platform !== 'win32') return []
  return [
    env['ProgramFiles(x86)'] && path.join(env['ProgramFiles(x86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    env.ProgramFiles && path.join(env.ProgramFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
  ].filter((value): value is string => Boolean(value))
}

const ORDER: BrowserKind[] = ['ego', 'chrome', 'edge']

export function egoExecutable(env: NodeJS.ProcessEnv = process.env) {
  return egoCandidates(env).find(exists)
}

/** 按 Ego → Chrome → Edge 找第一个装好的。不会启动任何东西。 */
export function resolveBrowser(prefer: BrowserKind | 'auto' = 'ego', env: NodeJS.ProcessEnv = process.env): BrowserTarget | undefined {
  const order = prefer === 'auto' ? ORDER : [prefer, ...ORDER.filter(kind => kind !== prefer)]
  for (const kind of order) {
    const list = kind === 'ego' ? egoCandidates(env) : kind === 'chrome' ? chromeCandidates(env) : edgeCandidates(env)
    const hit = list.find(exists)
    if (hit) return { kind, path: hit }
  }
  return undefined
}

/** 找不到浏览器时给一句能照着做的话，而不是把 ENOENT 丢到界面上。 */
export function browserInstruction(prefer: BrowserKind | 'auto' = 'ego') {
  if (prefer === 'ego') return `没找到 Ego Lite，也没有可用的备选浏览器。请在设置里安装 Ego Lite，或改用自己的 Chrome。`
  return '没找到可用的浏览器，请安装 Ego Lite 或 Google Chrome。'
}

export function browserStatus(env: NodeJS.ProcessEnv = process.env) {
  const active = resolveBrowser('ego', env)
  return {
    preferred: 'ego' as const,
    active: active ? { kind: active.kind, path: active.path } : undefined,
    installed: {
      ego: Boolean(egoExecutable(env)),
      chrome: Boolean(resolveBrowser('chrome', env)),
      edge: Boolean(resolveBrowser('edge', env)),
    },
    fallback: Boolean(active && active.kind !== 'ego'),
    site: EGO_SITE,
    downloadUrl: EGO_DOWNLOAD_URL,
    message: active?.kind === 'ego'
      ? '正在使用 Ego Lite。'
      : active
        ? `没检测到 Ego Lite，暂时用 ${active.kind === 'chrome' ? 'Chrome' : 'Edge'} 打开登录窗口；装好 Ego Lite 后会自动切过去。`
        : '没检测到 Ego Lite，也没有 Chrome / Edge。',
  }
}
