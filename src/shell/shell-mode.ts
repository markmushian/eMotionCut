/**
 * Two finishes for the workshop: 纸印 (paper) and 夜印 (night).
 *
 * The old build had 原色 / 暖雾 driven by data-ui-theme. The new shell keeps that attribute in sync
 * because the editor stylesheets still read it — paper maps to 暖雾, night maps to 原色 — but the
 * switch the person actually touches now writes data-ec-mode.
 */

export type ShellMode = 'paper' | 'night'

const KEY = 'emotioncut-shell-mode'

export function readShellMode(): ShellMode {
  try {
    const value = localStorage.getItem(KEY)
    if (value === 'paper' || value === 'night') return value
  } catch { /* storage may be unavailable */ }
  return 'night'
}

/** Applies the shell attributes and mirrors the choice onto the legacy theme attribute. */
export function applyShellMode(mode: ShellMode) {
  const root = document.documentElement
  root.dataset.ecShell = ''
  root.dataset.ecMode = mode
  root.dataset.uiTheme = mode === 'night' ? 'original' : 'warm'
  try {
    localStorage.setItem(KEY, mode)
    localStorage.setItem('linkreader-ui-theme', mode === 'night' ? 'original' : 'warm')
  } catch { /* storage may be unavailable */ }
  window.dispatchEvent(new Event('linkreader-ui-theme'))
  window.dispatchEvent(new Event('emotioncut-shell-mode'))
}

export function toggleShellMode(mode: ShellMode): ShellMode {
  return mode === 'night' ? 'paper' : 'night'
}

export const SHELL_MODE_LABELS: Record<ShellMode, string> = { paper: '日间', night: '终端' }

/** The legacy ThemeSwitcher keeps working: 原色 is night, 暖雾 is paper. */
export function shellModeForUiTheme(theme: string): ShellMode {
  return theme === 'warm' ? 'paper' : 'night'
}
