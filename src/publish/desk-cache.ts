/** 矩阵双台的页面级数据快照：切走再切回来时先用缓存渲染，后台再刷新，消灭整页空窗。 */
const store = new Map<string, unknown>()

export function readCache<T>(key: string): T | undefined {
  return store.get(key) as T | undefined
}

export function writeCache(key: string, value: unknown) {
  store.set(key, value)
}
