import {useEffect, useState, type RefObject} from 'react'

/**
 * 网格实际有几列 —— 详情要插在「被点卡片那一行的末尾」。
 * 风格库、Motion 模板库、新建通用剪辑的模板架都用这一份，列数随窗口变化。
 */
export function useColumns(ref: RefObject<HTMLElement | null>, fallback = 4) {
  const [columns, setColumns] = useState(fallback)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const measure = () => setColumns(Math.max(1, getComputedStyle(element).gridTemplateColumns.split(/\s+/).filter(Boolean).length))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return columns
}
