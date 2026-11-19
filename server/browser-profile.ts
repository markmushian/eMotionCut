import fs from 'node:fs'
import path from 'node:path'

/**
 * Ego(lite) 自己有一个首启标记，写在 profile 根目录的 `ego_config.json`：
 * `{"not_first_run": true}`。Chromium 的 `First Run` 和 `--no-first-run` 都管不到它，
 * 缺这个文件时窗口会停在 `chrome://onboarding`（「欢迎使用 ego(lite) / 开始使用」），
 * 于是每开一个新账号窗口都要重走一遍引导 —— 但矩阵账号每次都新建一个空 profile，
 * 所以表现为「每次打开一个窗口都要重新设置」。
 *
 * 这里在启动前把标记补上：已存在就原样保留，只用临时文件替换，避免写到一半留下坏 JSON。
 */
export function ensureBrowserProfile(profile: string) {
  fs.mkdirSync(profile, {recursive: true})
  const configFile = path.join(profile, 'ego_config.json')
  let current: Record<string, unknown> = {}
  try {
    if (fs.existsSync(configFile)) {
      const parsed: unknown = JSON.parse(fs.readFileSync(configFile, 'utf8'))
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) current = parsed as Record<string, unknown>
    }
  } catch { /* 坏文件按缺省处理，下面重写 */ }

  const seeded = current.not_first_run !== true
  if (seeded) {
    const temp = `${configFile}.tmp`
    fs.writeFileSync(temp, `${JSON.stringify({...current, not_first_run: true}, null, 3)}\n`, 'utf8')
    fs.renameSync(temp, configFile)
  }

  // Chromium 那一层的首启哨兵：和 ego_config.json 一起补，避免某些版本只看这个。
  const firstRun = path.join(profile, 'First Run')
  try {
    if (!fs.existsSync(firstRun)) fs.writeFileSync(firstRun, '', 'utf8')
  } catch { /* 只读目录等极端情况：ego_config.json 已经足够 */ }

  return {seeded, profile}
}
