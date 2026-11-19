import {useEffect, useRef, useState} from 'react'

type Props = {
  /** 目标数值。变化时从当前显示值缓动到新值。 */
  value: number
  /** 格式化函数，默认整数千分位。 */
  format?: (n: number) => string
  /** 动画时长（毫秒），默认 850。 */
  duration?: number
  className?: string
  title?: string
}

const defaultFormat = (n: number) => Math.round(n).toLocaleString('zh-CN')

/**
 * 数字跳动：从旧值缓动到新值，easeOutCubic。
 * - 首次挂载从 0 跳到目标值（给一个漂亮的入场）
 * - 后续更新从当前显示值继续跳，不闪回 0
 * - 尊重 prefers-reduced-motion：直接显示终值
 */
export function AnimatedNumber({value, format = defaultFormat, duration = 850, className, title}: Props) {
  const [shown, setShown] = useState(0)
  const fromRef = useRef(0)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || duration <= 0) { setShown(value); fromRef.current = value; return }
    const from = fromRef.current
    const delta = value - from
    if (delta === 0) return
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      const current = from + delta * eased
      setShown(current)
      fromRef.current = current
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
      else { setShown(value); fromRef.current = value }
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current != null) cancelAnimationFrame(rafRef.current) }
  }, [value, duration])

  return <span className={`mo-num${className ? ' ' + className : ''}`} data-ticking={Math.abs(value - shown) > 0.5 ? '1' : '0'} title={title}>{format(shown)}</span>
}

export default AnimatedNumber
