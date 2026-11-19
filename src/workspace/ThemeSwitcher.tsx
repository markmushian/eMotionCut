import {useEffect, useState} from 'react';
import {Contrast} from 'lucide-react';
import {DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger} from '@/components/ui/dropdown-menu';
import {applyShellMode, readShellMode, type ShellMode} from '@/shell/shell-mode';

/**
 * 设置里的「界面主题」和顶栏的图标菜单都走同一个日夜开关。
 * 旧版只写 data-ui-theme（原色/暖雾），点了不会真正切换工作台的昼夜配色；
 * 现在统一写 data-ec-mode，编辑器仍然通过镜像属性保持兼容。
 */
const THEME_LABELS: Record<ShellMode, string> = {paper: '日间', night: '夜间'};
const THEMES = ['night', 'paper'] as const;

/** Segmented switch in 系统设置; `compact` is the one-icon menu used in the top bar. */
export function ThemeSwitcher({compact = false}: {compact?: boolean}) {
  const [theme, setTheme] = useState(readShellMode);
  useEffect(() => {
    const sync = () => setTheme(readShellMode());
    window.addEventListener('storage', sync);
    window.addEventListener('linkreader-ui-theme', sync);
    window.addEventListener('emotioncut-shell-mode', sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener('linkreader-ui-theme', sync);
      window.removeEventListener('emotioncut-shell-mode', sync);
    };
  }, []);
  if (compact) return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <button type="button" className="ui-theme-menu-trigger" aria-label={`界面主题：${THEME_LABELS[theme]}`} title={`界面主题：${THEME_LABELS[theme]}`}><Contrast size={17} /></button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="ui-theme-menu">
      <DropdownMenuLabel>界面主题</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={theme} onValueChange={value => applyShellMode(value as ShellMode)}>
        {THEMES.map(value => <DropdownMenuRadioItem key={value} value={value}>{THEME_LABELS[value]}</DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
  return <div className="ui-theme-switch" role="group" aria-label="界面主题">
    {THEMES.map(value => <button key={value} type="button" aria-pressed={theme === value} onClick={() => applyShellMode(value)}>{THEME_LABELS[value]}</button>)}
  </div>;
}
