/**
 * The workshop's six stages. This is the spine of the new navigation: every route in the app belongs
 * to exactly one stage, and the left rail shows the stage numbers rather than a row of icons.
 *
 * 00 案台 — where you land and where finished work is filed
 * 10 取料 — material gathering (选题库, 链接读取)
 * 20 拍录 — screen recording and camera moves
 * 30 剪辑 — the cutting rooms
 * 40 出片 — copy, covers and motion cards that go out with the film
 * 50 复盘 — numbers afterwards
 */

export type StageId = 'desk' | 'source' | 'shoot' | 'cut' | 'publish' | 'review'

export type StageEntry = {
  readonly no: string
  readonly label: string
  readonly hint: string
  readonly path: string
  readonly pro?: boolean
  /** Route prefixes that belong to this entry; longest prefix wins. */
  readonly matches: readonly string[]
  /** Prefixes that look like this entry but are a different room (a project page vs. its entrance). */
  readonly excludes?: readonly string[]
}

export type Stage = {
  readonly id: StageId
  readonly no: string
  readonly name: string
  /** Latin gloss, printed small under the stage name in the rail panel. */
  readonly gloss: string
  readonly path: string
  readonly matches: readonly string[]
  readonly entries: readonly StageEntry[]
}

export const STAGES: readonly Stage[] = [
  {
    id: 'desk', no: '00', name: '创作首页', gloss: '作品与流程', path: '/',
    matches: ['/'],
    entries: [],
  },
  {
    id: 'source', no: '10', name: '策划研究', gloss: '选题与文案', path: '/pro/topics',
    matches: ['/pro/topics', '/pro/reader', '/creator-tools'],
    entries: [
      { no: '11', label: '对标研究', hint: '原版账号与作品拆解', path: '/creator-tools/bench', matches: ['/creator-tools/bench','/pro/reader'] },
      { no: '12', label: '选题研究', hint: '原版调研、热榜与候选池', path: '/creator-tools/topics', matches: ['/creator-tools/topics','/pro/topics'] },
      { no: '13', label: '文案精修', hint: '原版改写、口播化与标题', path: '/creator-tools/script', matches: ['/creator-tools/script'] },
      { no: '14', label: '稿件打分', hint: '原版盲测评分与改稿建议', path: '/creator-tools/score', matches: ['/creator-tools/score'] },
    ],
  },
  {
    id: 'shoot', no: '20', name: '操作录屏', gloss: '教程素材', path: '/screen-motion',
    matches: ['/screen-motion'],
    entries: [
      { no: '21', label: '录制电脑操作', hint: '录软件操作、教程演示和屏幕素材', path: '/screen-motion', matches: ['/screen-motion'] },
    ],
  },
  {
    id: 'cut', no: '30', name: '制作剪辑', gloss: 'Agent 导演', path: '/studio',
    matches: ['/studio', '/general', '/styles', '/production'],
    entries: [
      { no: '31', label: '剪辑台', hint: '全部工程与通用剪辑', path: '/studio', matches: ['/studio'] },
      { no: '32', label: '新建通用工程', hint: '选风格，放素材', path: '/general', matches: ['/general'], excludes: ['/general/projects'] },
      { no: '33', label: '动效模板库', hint: '挑选动效模板', path: '/styles', matches: ['/styles'] },
      { no: '34', label: '口播制作', hint: '真人口播、云端续说与数字人', path: '/production', matches: ['/production', '/creator-tools/voice', '/creator-tools/avatar'] },
    ],
  },
  {
    id: 'publish', no: '40', name: '矩阵分发', gloss: '多账号批量发布', path: '/pro/publish',
    matches: ['/pro/publish', '/library'],
    entries: [
      { no: '41', label: '矩阵发布', hint: '一个成片，分发到账号矩阵', path: '/pro/publish', matches: ['/pro/publish', '/library'] },
    ],
  },
  {
    id: 'review', no: '50', name: '作品监控', gloss: '评论与数据', path: '/pro/analytics',
    matches: ['/pro/analytics', '/pro/review'],
    entries: [
      { no: '51', label: '作品与评论监控', hint: '同步自己作品的公开数据与评论', path: '/pro/analytics', matches: ['/pro/analytics', '/pro/review'] },
    ],
  },
]

/** Longest matching prefix, so /studio/projects/x resolves to 剪辑台 rather than 剪辑台's parent. */
function depth(prefix: string, pathname: string) {
  return pathname === prefix || pathname.startsWith(`${prefix}/`) ? prefix.length : -1
}

/**
 * The screen-recording build has no cutting room: /studio *is* the recording archive there, so it
 * joins 拍录 and 剪辑 keeps only the general-editing door and the style library.
 */
export function buildStages(screenOnly: boolean): readonly Stage[] {
  if (!screenOnly) return STAGES
  return STAGES.map(stage => {
    if (stage.id === 'shoot') return {
      ...stage,
      matches: ['/screen-motion', '/studio'],
      entries: stage.entries.map(entry => ({ ...entry, matches: ['/screen-motion', '/studio'] })),
    }
    if (stage.id === 'cut') return {
      ...stage,
      path: '/general',
      matches: ['/general', '/styles'],
      entries: stage.entries.filter(entry => entry.no !== '31'),
    }
    return stage
  })
}

export function stageForPath(pathname: string, stages: readonly Stage[] = STAGES): Stage {
  let best: Stage = stages[0]
  let bestDepth = -1
  for (const stage of stages) {
    for (const prefix of stage.matches) {
      const d = depth(prefix, pathname)
      if (d > bestDepth) { bestDepth = d; best = stage }
    }
  }
  return best
}

export function entryForPath(pathname: string, stages: readonly Stage[] = STAGES): StageEntry | undefined {
  let best: StageEntry | undefined
  let bestDepth = -1
  for (const stage of stages) {
    for (const entry of stage.entries) {
      if (entry.excludes?.some(prefix => depth(prefix, pathname) >= 0)) continue
      for (const prefix of entry.matches) {
        const d = depth(prefix, pathname)
        if (d > bestDepth) { bestDepth = d; best = entry }
      }
    }
  }
  return best
}

/** Rail-button order, kept as a plain list for arrow-key movement and for tests. */
export function stageIndex(pathname: string, stages: readonly Stage[] = STAGES) {
  const stage = stageForPath(pathname, stages)
  return stages.findIndex(item => item.id === stage.id)
}
