/**
 * 平台档案 —— 每个平台一套封面比例 + 一条发布文案规则。
 *
 * 为什么要有这层：平台之间真正不同的是三件事，
 * 1) 封面按什么比例裁剪（抖音 9:16 全屏、小红书 3:4、视频号 6:7、B站 16:10）；
 * 2) 标题能放多少字（小红书 20 字硬裁、视频号短标题 16 字、抖音信息流只展示前 55 字）；
 * 3) 话题落到哪个字段（抖音接在描述后、小红书进正文、视频号塞描述）。
 * 之前这三件事全写死在三个 filler 里，所以「话题」这个字段存了却从没用上过。
 *
 * 数值都是「以发布页当前裁剪框为准」的默认值，存在
 * <用户目录>/creator/matrix/platform-profiles.json，界面上可改，平台改版不用改代码。
 */

export type TagPlacement = 'title' | 'body' | 'description'

export type PlatformProfile = {
  /** 与 creator-matrix 的 MatrixPlatform 对齐，这里用 string 避免循环依赖。 */
  id: string
  name: string
  /** 上架封面首选比例，出图与配图都按它走。 */
  coverRatio: string
  /** 次要比例：平台也接受，缺首选时可以用。 */
  altRatios: string[]
  /** 标题展示上限（字）。小红书 20 是硬裁，其余是信息流展示建议值。 */
  titleLimit: number
  /** 话题/标签个数上限。 */
  tagLimit: number
  /** 话题拼到哪个字段。 */
  tagPlacement: TagPlacement
  /** 描述/正文模板：{title} {desc} {tags}。 */
  descTemplate: string
  /** 四边安全区（百分比），标题与主体都不得越界。 */
  safeArea: number
  /** 底部额外留白（百分比），让开平台按钮条。 */
  bottomInset: number
  /** 一句话说明这个平台的特殊规矩，界面上直接展示。 */
  note: string
}

/** 比例 → 原生出图像素。竖版给到 2160 宽，缩到 1080 也不糊。 */
export const RATIO_SIZES: Record<string, {width: number; height: number}> = {
  '9:16': {width: 2160, height: 3840},
  '3:4': {width: 2160, height: 2880},
  '6:7': {width: 2160, height: 2520},
  '4:5': {width: 2160, height: 2700},
  '1:1': {width: 2160, height: 2160},
  '4:3': {width: 2880, height: 2160},
  '16:10': {width: 1920, height: 1200},
  '16:9': {width: 1920, height: 1080},
}

export const DEFAULT_PROFILES: PlatformProfile[] = [
  {
    id: 'douyin', name: '抖音', coverRatio: '9:16', altRatios: ['3:4'],
    titleLimit: 55, tagLimit: 5, tagPlacement: 'title',
    descTemplate: '{title} {tags}', safeArea: 6, bottomInset: 12,
    note: '推荐流全屏展示 9:16；底部按钮压住约 12%，标题别往下放。',
  },
  {
    id: 'kuaishou', name: '快手', coverRatio: '9:16', altRatios: ['3:4'],
    titleLimit: 30, tagLimit: 3, tagPlacement: 'title',
    descTemplate: '{title} {tags}', safeArea: 6, bottomInset: 14,
    note: '竖版 9:16 为主；部分信息流会裁成接近 5:4，主体要居中。',
  },
  {
    id: 'xhs', name: '小红书', coverRatio: '3:4', altRatios: ['9:16'],
    titleLimit: 20, tagLimit: 10, tagPlacement: 'body',
    descTemplate: '{title}\n\n{desc}\n{tags}', safeArea: 6, bottomInset: 0,
    note: '标题超过 20 字会被平台截断；话题必须写在正文里。',
  },
  {
    id: 'channels', name: '视频号', coverRatio: '6:7', altRatios: ['9:16', '3:4'],
    titleLimit: 16, tagLimit: 3, tagPlacement: 'description',
    descTemplate: '{title}\n{tags}', safeArea: 8, bottomInset: 6,
    note: '封面 6:7（1080×1260），列表页与详情页都按它裁；短标题建议 16 字内。',
  },
  {
    id: 'bilibili', name: 'B站', coverRatio: '16:10', altRatios: ['16:9'],
    titleLimit: 80, tagLimit: 10, tagPlacement: 'body',
    descTemplate: '{title}\n\n{tags}', safeArea: 5, bottomInset: 0,
    note: '封面 16:10（1146×717 起）；横版图右下角会压播放时长。',
  },
  {
    id: 'baijiahao', name: '百家号', coverRatio: '16:9', altRatios: ['3:4'],
    titleLimit: 30, tagLimit: 3, tagPlacement: 'title',
    descTemplate: '{title} {tags}', safeArea: 5, bottomInset: 0,
    note: '横版 16:9 为主；标题过长会在信息流被截。',
  },
  {
    id: 'toutiao', name: '头条号', coverRatio: '16:9', altRatios: ['3:4'],
    titleLimit: 30, tagLimit: 3, tagPlacement: 'title',
    descTemplate: '{title} {tags}', safeArea: 5, bottomInset: 0,
    note: '横版 16:9；封面右下角可能压播放量角标。',
  },
  {
    id: 'fanqie', name: '番茄视频', coverRatio: '9:16', altRatios: [],
    titleLimit: 30, tagLimit: 3, tagPlacement: 'title',
    descTemplate: '{title} {tags}', safeArea: 6, bottomInset: 10,
    note: '竖版 9:16；短剧类目对封面人工审核更严，别放夸张收益数字。',
  },
  // —— 海外四家：比例按各平台信息流真实裁剪，文案按英文/本地习惯留足字数 ——
  {
    id: 'tiktok', name: 'TikTok', coverRatio: '9:16', altRatios: ['3:4'],
    titleLimit: 150, tagLimit: 5, tagPlacement: 'body',
    descTemplate: '{title}\n\n{desc}\n{tags}', safeArea: 8, bottomInset: 18,
    note: '全屏 9:16；右侧按钮栏与底部文案压住约 18%，标题务必居中偏上、避开底部。',
  },
  {
    id: 'youtube', name: 'YouTube', coverRatio: '16:9', altRatios: ['16:10'],
    titleLimit: 100, tagLimit: 15, tagPlacement: 'body',
    descTemplate: '{title}\n\n{desc}\n\n{tags}', safeArea: 5, bottomInset: 0,
    note: '缩略图 16:9（1280×720）；右下角会压时长角标，标题放左中、字要大。Shorts 另走 9:16。',
  },
  {
    id: 'instagram', name: 'Instagram', coverRatio: '4:5', altRatios: ['1:1', '9:16'],
    titleLimit: 125, tagLimit: 10, tagPlacement: 'body',
    descTemplate: '{title}\n\n{desc}\n{tags}', safeArea: 6, bottomInset: 0,
    note: '图文帖 4:5（1080×1350）占屏最高；Reels 走 9:16。话题写在正文第一段后。',
  },
  {
    id: 'x', name: 'X (Twitter)', coverRatio: '16:9', altRatios: ['1:1'],
    titleLimit: 280, tagLimit: 5, tagPlacement: 'body',
    descTemplate: '{title}\n{desc}\n{tags}', safeArea: 5, bottomInset: 0,
    note: '横图 16:9；正文 280 字硬上限，话题直接写在正文里，别放画面上。',
  },
]

/** 平台写回来的比例写法五花八门，统一成 W:H。 */
export function normalizeRatio(input: string): string {
  const text = String(input || '').trim()
  const hit = /^(\d{1,2})\s*[:x/×]\s*(\d{1,2})$/.exec(text)
  if (hit) return `${hit[1]}:${hit[2]}`
  const px = /^(\d{2,5})\s*[x×]\s*(\d{2,5})$/.exec(text)
  if (px) {
    const ratio = Number(px[1]) / Number(px[2])
    const table: [string, number][] = [['9:16', 9 / 16], ['4:5', 0.8], ['3:4', 0.75], ['6:7', 6 / 7], ['1:1', 1], ['4:3', 4 / 3], ['16:10', 1.6], ['16:9', 16 / 9]]
    let best = table[0]
    for (const row of table) if (Math.abs(ratio - row[1]) < Math.abs(ratio - best[1])) best = row
    return best[0]
  }
  return text
}

export function ratioSize(ratio: string): {width: number; height: number} | undefined {
  return RATIO_SIZES[normalizeRatio(ratio)]
}

function mergeProfile(base: PlatformProfile, patch: Partial<PlatformProfile>): PlatformProfile {
  const next = {...base, ...patch}
  next.coverRatio = normalizeRatio(next.coverRatio) || base.coverRatio
  next.altRatios = (Array.isArray(next.altRatios) ? next.altRatios : base.altRatios).map(normalizeRatio).filter(Boolean)
  next.titleLimit = Math.min(Math.max(Number(next.titleLimit) || base.titleLimit, 4), 200)
  next.tagLimit = Math.min(Math.max(Number(next.tagLimit) || base.tagLimit, 0), 30)
  next.safeArea = Math.min(Math.max(Number(next.safeArea) || base.safeArea, 0), 20)
  next.bottomInset = Math.min(Math.max(Number(next.bottomInset) || 0, 0), 30)
  next.tagPlacement = (['title', 'body', 'description'] as TagPlacement[]).includes(next.tagPlacement) ? next.tagPlacement : base.tagPlacement
  next.descTemplate = String(next.descTemplate || base.descTemplate).slice(0, 400)
  next.note = String(next.note ?? base.note).slice(0, 200)
  return next
}

/** 读档案：以默认值为骨架，把用户改过的字段盖上去；新增平台自动出现。 */
export function readProfiles(raw: unknown): PlatformProfile[] {
  const rows = Array.isArray(raw) ? raw as Partial<PlatformProfile>[] : []
  return DEFAULT_PROFILES.map(base => {
    const patch = rows.find(row => row?.id === base.id)
    return patch ? mergeProfile(base, patch) : {...base}
  })
}

export function saveProfiles(current: PlatformProfile[], patches: Partial<PlatformProfile>[]): PlatformProfile[] {
  const list = Array.isArray(patches) ? patches : []
  return current.map(base => {
    const patch = list.find(row => row?.id === base.id)
    return patch ? mergeProfile(base, {...patch, id: base.id, name: base.name}) : base
  })
}

export function profileFor(rows: PlatformProfile[], id: string): PlatformProfile | undefined {
  return rows.find(row => row.id === id)
}

export type ComposedCaption = {
  /** 填进「标题」输入框的内容。 */
  titleField: string
  /** 填进「描述 / 正文」输入框的内容；平台只有一个输入框时是空串。 */
  bodyField: string
  tags: string[]
  /** 标题被截断过，界面要提示用户，不能静默丢字。 */
  truncated: boolean
  /** 实际写进平台的完整文案，任务卡上展示用。 */
  preview: string
}

/**
 * 按平台档案拼文案。
 * 标题超限只截一次并返回 truncated，让界面明确说「被裁了几个字」——
 * 之前的写法是在 filler 里直接 slice(0, 20)，用户完全不知道发生了什么。
 */
export function composeCaption(profile: PlatformProfile, input: {title: string; tags?: string[]; desc?: string}): ComposedCaption {
  const rawTitle = String(input.title || '').trim()
  const truncated = rawTitle.length > profile.titleLimit
  const title = truncated ? rawTitle.slice(0, Math.max(1, profile.titleLimit - 1)) + '…' : rawTitle
  const seen = new Set<string>()
  const tags = (input.tags || [])
    .map(tag => String(tag).replace(/^#/, '').trim())
    .filter(tag => tag && !seen.has(tag) && seen.add(tag))
    .slice(0, profile.tagLimit)
  const tagLine = tags.map(tag => `#${tag}`).join(' ')

  const body = profile.descTemplate
    .replace(/\{title\}/g, title)
    .replace(/\{desc\}/g, String(input.desc || '').trim())
    .replace(/\{tags\}/g, tagLine)
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  const titleField = profile.tagPlacement === 'title' && tagLine ? `${title} ${tagLine}` : title
  const bodyField = profile.tagPlacement === 'title' ? '' : body
  return {titleField, bodyField, tags, truncated, preview: bodyField || titleField}
}

/** 把选中的账号平台去重成一个比例列表：选抖音+小红书就只出 9:16 和 3:4。 */
export function ratiosFor(profiles: PlatformProfile[], platformIds: string[]): {ratio: string; platforms: string[]}[] {
  const map = new Map<string, string[]>()
  for (const id of new Set(platformIds)) {
    const profile = profileFor(profiles, id)
    if (!profile) continue
    const list = map.get(profile.coverRatio) || []
    list.push(profile.name)
    map.set(profile.coverRatio, list)
  }
  return [...map.entries()].map(([ratio, platforms]) => ({ratio, platforms}))
}

/** 给某个平台挑封面：首选比例优先，其次次要比例，都没有就返回空（界面要明说缺图）。 */
export function pickCoverFor(profile: PlatformProfile, covers: Record<string, string>): {file: string; ratio: string; exact: boolean} | undefined {
  const exact = covers[profile.coverRatio]
  if (exact) return {file: exact, ratio: profile.coverRatio, exact: true}
  for (const ratio of profile.altRatios) {
    const hit = covers[normalizeRatio(ratio)]
    if (hit) return {file: hit, ratio: normalizeRatio(ratio), exact: false}
  }
  return undefined
}
