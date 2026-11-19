import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {Link} from 'react-router'
import {Activity, AlertTriangle, Check, ChevronDown, Clock, Copy, ExternalLink, Image, Layers, Pencil, Plus, Play, RefreshCw, Rocket, Smartphone, Sparkles, Trash2, Type, UserRound, X} from 'lucide-react'
import {matrixApi} from './agent-client'
import {BrowserSetup} from './BrowserSetup'
import {PLATFORM_BRAND, PlatformLogo} from './platform-logos'
import {readCache, writeCache} from './desk-cache'
import './matrix.css'

type Platform = {id: string; name: string; domain: string; upload: boolean; loginUrl: string; createUrl?: string}
type Account = {id: string; platform: string; alias: string; port: number; createdAt: string; nickname?: string; platformId?: string; aliasAuto?: boolean; lastCheck?: {at: string; loggedIn: boolean; by: string; error?: string}}
type Task = {id: string; accountId: string; platform: string; file: string; cover?: string; coverRatio?: string; coverNote?: string; caption?: string; title: string; tags: string[]; scheduledAt?: string; autoSubmit: boolean; status: 'queued'|'running'|'ready'|'done'|'failed'|'cancelled'; step: string; url?: string; error?: string; createdAt: string; updatedAt: string; handoff?: {startedAt: string}}
type Summary = {accounts: number; loggedIn: number; queued: number; ready: number; done: number; failed: number; works: number; comments: number; views: number}
type Draft = {file: string; title: string; tags: string; scheduledAt: string; autoSubmit: boolean; coverBrief: string}
type CoverSample = {file: string; width: number; height: number; ratio: string; colors: string[]; brightness: number; contrast: number; zones: [number, number, number]; summary: string}
type CoverMemory = {spec: string; source: ''|'agent'|'local'; samples: CoverSample[]; updatedAt: string; agent?: string}
type PersonRef = {file: string; path: string; purpose: string; addedAt: string}
type PersonManifest = {refs: PersonRef[]; updatedAt: string}
type CoverAsset = {file: string; bytes: number; width?: number; height?: number; ratio: string; source: string; createdAt: string}
type Headline = {main: string; sub: string; keywords: string[]; by: 'agent'|'local'}
type Profile = {id: string; name: string; coverRatio: string; altRatios: string[]; titleLimit: number; tagLimit: number; tagPlacement: 'title'|'body'|'description'; descTemplate: string; safeArea: number; bottomInset: number; note: string}

const FALLBACK: Platform[] = [
  {id:'douyin',name:'抖音',domain:'douyin.com',upload:true,loginUrl:''},
  {id:'channels',name:'视频号',domain:'channels.weixin.qq.com',upload:true,loginUrl:''},
  {id:'xhs',name:'小红书',domain:'xiaohongshu.com',upload:true,loginUrl:''},
  {id:'kuaishou',name:'快手',domain:'kuaishou.com',upload:false,loginUrl:''},
  {id:'bilibili',name:'B站',domain:'bilibili.com',upload:false,loginUrl:''},
  {id:'baijiahao',name:'百家号',domain:'baijiahao.baidu.com',upload:false,loginUrl:''},
  {id:'toutiao',name:'头条号',domain:'mp.toutiao.com',upload:false,loginUrl:''},
  {id:'fanqie',name:'番茄视频',domain:'fanqienovel.com',upload:false,loginUrl:''},
  {id:'tiktok',name:'TikTok',domain:'tiktok.com',upload:false,loginUrl:''},
  {id:'youtube',name:'YouTube',domain:'youtube.com',upload:false,loginUrl:''},
  {id:'instagram',name:'Instagram',domain:'instagram.com',upload:false,loginUrl:''},
  {id:'x',name:'X (Twitter)',domain:'x.com',upload:false,loginUrl:''},
]

/**
 * 平台选择下拉：原生 <select> 的 option 放不了图标，平台名前面全是光秃秃的文字。
 * 换成自定义列表——每一项带 App 图标，底部小字标注这个平台是「自动上传」还是「仅打开页面」。
 */
function PlatformPicker({platforms, value, onChange}: {platforms: Platform[]; value: string; onChange: (id: string) => void}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => { if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false) }
    const esc = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])
  const current = platforms.find(p => p.id === value)
  return <div className="mx-pick" ref={ref}>
    <button type="button" className="mx-pick__btn" data-open={open || undefined} onClick={() => setOpen(o => !o)} aria-haspopup="listbox" aria-expanded={open}>
      {current
        ? <span className="mx-pick__value"><PlatformLogo platform={current.id} size={18}/><b>{current.name}</b>{!current.upload && <small>仅打开页面</small>}</span>
        : <span className="mx-pick__value mx-pick__value--ph">选择平台…</span>}
      <ChevronDown size={15}/>
    </button>
    {open && <div className="mx-pick__list" role="listbox">
      {platforms.map(p => <button type="button" key={p.id} role="option" aria-selected={p.id === value}
        className="mx-pick__opt" data-on={p.id === value || undefined} onClick={() => { onChange(p.id); setOpen(false) }}>
        <PlatformLogo platform={p.id} size={18}/>
        <span className="mx-pick__name">{p.name}</span>
        <small>{p.upload ? '自动上传' : '仅打开页面'}</small>
      </button>)}
    </div>}
  </div>
}
const STATE_LABEL: Record<Task['status'], string> = {queued:'排队',running:'进行中',ready:'待你点发布',done:'已发布',failed:'失败',cancelled:'已取消'}
const STATE_TONE: Record<Task['status'], string> = {queued:'',running:'mint',ready:'amber',done:'mint',failed:'rose',cancelled:''}

/**
 * 预填在「底图提示词」框里的模板：只写画面，不写标题。
 *
 * 为什么不再让模型画标题：中文是生图模型最弱的一项，「失忆」会被画成「失意」，
 * 而标题恰恰是封面唯一必须逐字正确的东西。所以分工改成——
 * 模型负责画面（这里），标题/关键词交给「按比例套版」用字体排版。
 */
const COVER_BRIEF_TEMPLATE = `生成一张 9:16 竖版短视频封面底图（2160×3840），只要画面，不要任何文字。

作品主题：【填写作品主题】

画面主体：
- 【真人出镜时写——以随附参考照人物为唯一身份依据，保持真实脸型、五官比例、发型、年龄感与自然肤色，半身近景，表情从「推荐 / 惊喜 / 质疑 / 恍然大悟」中选一种；全画面只出现一个人物。不出镜时写——用与主题强相关的产品主体或抽象符号，并说明选择理由，不得凭空指定具体人物或虚构身份。】
- 背景按主题选具体场景：教程=明亮编辑风、测评=货架实拍风、故事=电影感叙事；不要所有视频都套同一种科技风或纯黑背景。

构图留白（这条最重要，按爆款封面参考图来）：
- 竖向三分法：画面上方约 35% 是「标题层」，必须干净、低信息量、偏暗或虚化，只放氛围不放到处乱跳的细节；后期主标题（两行大字）和副标题色条会排在这里。
- 主体放在画面中下部、居中略偏右，约占画面高度的 50%，脸和手完整、四周留呼吸空间；中景可以放与主题相关的设备/界面/产品作陪衬，但不要盖住上方标题层。
- 视觉重心沿中轴线从上到下依次为「标题留白 → 人物主体 → 氛围延伸」，形成稳定的 Z 型阅读动线。
- 四边各留 6% 安全边距；标题层任何时候都不能被人物头发、道具或文字水印侵入。

配色与光影：【给具体色值与主光方向，如主色墨绿 #0A927C 配米白 #F2F6F5，暖调侧光从左上打来，背景轻度虚化】

负面约束：禁止出现任何文字、字幕、水印、Logo、播放量或评论数字；禁止巨大前景手、畸形手指、重复人物或重复肢体、模板化网红脸；禁止照搬任何参考样本的文案、人物、品牌标识或版式。

自检：① 遮住上方 1/3 仍能看出主题；② 缩到 320px 宽后主体与情绪仍清楚；③ 画面里没有一处文字或疑似文字的笔画；④ 主体约占画面高度 50%、居中略偏下，上方标题层干净不挤。`

const readImages = (files: FileList|null) => Promise.all([...(files || [])].slice(0, 10).map(file => new Promise<string>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(String(reader.result))
  reader.onerror = () => reject(reader.error)
  reader.readAsDataURL(file)
})))

export function MatrixDesk() {
  const [platforms, setPlatforms] = useState<Platform[]>(() => readCache<Platform[]>('mx-platforms') ?? FALLBACK)
  const [accounts, setAccounts] = useState<Account[]>(() => readCache<Account[]>('mx-accounts') || [])
  const [tasks, setTasks] = useState<Task[]>(() => readCache<Task[]>('mx-tasks') || [])
  const [summary, setSummary] = useState<Summary | null>(() => readCache<Summary | null>('mx-summary') ?? null)
  const [picked, setPicked] = useState<string[]>([])
  const [file, setFile] = useState('')
  const [title, setTitle] = useState('')
  const [tags, setTags] = useState('')
  const [scheduledAt, setScheduledAt] = useState('')
  const [autoSubmit, setAutoSubmit] = useState(false)
  const [cover, setCover] = useState<CoverMemory | null>(null)
  const [personRefs, setPersonRefs] = useState<PersonRef[]>([])
  const [assets, setAssets] = useState<CoverAsset[]>([])
  const [coverPick, setCoverPick] = useState('')
  const [coverKeywords, setCoverKeywords] = useState('')
  const [coverBrief, setCoverBrief] = useState(COVER_BRIEF_TEMPLATE)
  const [preview, setPreview] = useState<string[]>([])
  const [profiles, setProfiles] = useState<Profile[]>(() => readCache<Profile[]>('mx-profiles') || [])
  const [profileDraft, setProfileDraft] = useState<Profile[]>([])
  const [composeBase, setComposeBase] = useState('')
  const [composePalette, setComposePalette] = useState<'auto'|'viral'|'ink'|'warm'|'paper'>('auto')
  const [headline, setHeadline] = useState<Headline|null>(null)
  const [headlineKey, setHeadlineKey] = useState('')
  const [codex, setCodex] = useState<{available: boolean; cli: string} | null>(null)
  const [codexPerson, setCodexPerson] = useState(true)
  const [codexRef, setCodexRef] = useState('')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState('')
  const [confirmUrl, setConfirmUrl] = useState('')
  const [viewer, setViewer] = useState<{src: string; title: string} | null>(null)
  // 手机发布（UserHandoff）：文案随行 + 创作者中心自动对账
  const [handoff, setHandoff] = useState<{taskId: string; platform: string; copy: {title: string; caption: string; tags: string}} | null>(null)
  const [handoffPoll, setHandoffPoll] = useState<{checking: boolean; scanned?: number; found?: boolean; url?: string} | null>(null)
  const [adding, setAdding] = useState('')
  const [alias, setAlias] = useState('')
  const [renaming, setRenaming] = useState('')
  const [renameDraft, setRenameDraft] = useState('')
  const timer = useRef<number | undefined>(undefined)
  const restored = useRef(false)

  const load = useCallback(async () => {
    try {
      const [a, t, s] = await Promise.all([matrixApi<Account[]>('accounts'), matrixApi<Task[]>('tasks'), matrixApi<Summary>('summary')])
      setAccounts(a); setTasks(t); setSummary(s); setError('')
      writeCache('mx-accounts', a); writeCache('mx-tasks', t); writeCache('mx-summary', s)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }, [])

  const loadAssets = useCallback(async () => {
    try { setAssets(await matrixApi<CoverAsset[]>('cover/library')) } catch { /* 素材库还没建时忽略 */ }
  }, [])

  useEffect(() => {
    void matrixApi<Platform[]>('platforms').then(rows => { setPlatforms(rows); writeCache('mx-platforms', rows) }).catch(() => {})
    void matrixApi<CoverMemory>('cover').then(setCover).catch(() => {})
    void matrixApi<PersonManifest>('cover/person').then(m => setPersonRefs(m.refs || [])).catch(() => {})
    void matrixApi<{available: boolean; cli: string}>('cover/codex').then(setCodex).catch(() => setCodex({available: false, cli: ''}))
    void matrixApi<Profile[]>('profiles').then(rows => { setProfiles(rows); setProfileDraft(rows); writeCache('mx-profiles', rows) }).catch(() => {})
    void loadAssets()
    void matrixApi<Draft>('draft').then(draft => {
      setFile(draft.file); setTitle(draft.title); setTags(draft.tags)
      setScheduledAt(draft.scheduledAt); setAutoSubmit(draft.autoSubmit); setCoverBrief(draft.coverBrief || COVER_BRIEF_TEMPLATE)
      restored.current = true
    }).catch(() => { restored.current = true })
    void load()
    timer.current = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer.current)
  }, [load, loadAssets])

  // 草稿随打随存：刷新、切页回来都还在，不用重贴路径。
  useEffect(() => {
    if (!restored.current) return
    const id = window.setTimeout(() => {
      void matrixApi('draft', {method: 'PUT', body: JSON.stringify({file, title, tags, scheduledAt, autoSubmit, coverBrief})}).catch(() => {})
    }, 600)
    return () => window.clearTimeout(id)
  }, [file, title, tags, scheduledAt, autoSubmit, coverBrief])

  const name = (id: string) => platforms.find(p => p.id === id)?.name || id
  /** 平台名 + 品牌 LOGO 的小徽标。 */
  const platTag = (id: string, text?: string) => (
    <span style={{display: 'inline-flex', alignItems: 'center', gap: 5, verticalAlign: 'middle'}}>
      <PlatformLogo platform={id} size={14}/>{text ?? name(id)}
    </span>
  )
  const grouped = useMemo(() => platforms.map(p => ({...p, rows: accounts.filter(a => a.platform === p.id)})).filter(p => p.rows.length), [platforms, accounts])
  const online = accounts.filter(a => a.lastCheck?.loggedIn)
  const coverThumbs = useMemo(() => {
    if (preview.length) return preview
    return (cover?.samples || []).map(s => `/api/matrix/cover/file/${encodeURIComponent(s.file)}`)
  }, [preview, cover])

  /** 本次要出哪几个比例：由已选账号的平台决定，选抖音+小红书就只要 9:16 和 3:4。 */
  const neededRatios = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const id of new Set(picked)) {
      const platform = accounts.find(a => a.id === id)?.platform
      const profile = profiles.find(p => p.id === platform)
      if (!profile) continue
      const list = map.get(profile.coverRatio) || []
      if (!list.includes(profile.name)) list.push(profile.name)
      map.set(profile.coverRatio, list)
    }
    return [...map.entries()].map(([ratio, platforms]) => ({ratio, platforms}))
  }, [picked, accounts, profiles])

  const baseOptions = useMemo(() => assets.filter(item => item.source !== 'composed'), [assets])

  /** 封面标题必须先提炼：原视频标题往往带「第N集/完整版」，直接印上去又长又平。 */
  const headlineSource = `${title.trim()}|${tags.trim()}`
  const headlineFor = useCallback(async (force = false): Promise<Headline> => {
    if (!force && headline && headlineKey === headlineSource) return headline
    const result = await matrixApi<Headline>('cover/headline', {
      method: 'POST',
      body: JSON.stringify({
        title: title.trim(), tags: tags.split(/[\s,，#]+/).filter(Boolean),
        platform: picked[0] ? accounts.find(a => a.id === picked[0])?.platform : '',
      }),
    })
    setHeadline(result); setHeadlineKey(headlineSource)
    return result
  }, [title, tags, picked, accounts, headline, headlineKey, headlineSource])

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label); setNotice(''); setError('')
    try { await action(); await load() } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  /** 手机发布对账：读创作者中心作品列表，按标题匹配回链。 */
  const pollHandoff = useCallback(async (taskId: string) => {
    setHandoffPoll(p => ({...(p || {}), checking: true}))
    try {
      const r = await matrixApi<{found: boolean; scanned?: number; url?: string}>(`tasks/${taskId}/handoff-poll`, {method: 'POST'})
      setHandoffPoll({checking: false, scanned: r.scanned, found: r.found, url: r.url})
      if (r.found) { setNotice('对账成功：作品已自动回链并登记监控台。'); void load() }
    } catch (cause) {
      setHandoffPoll({checking: false, found: false})
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [load])

  // 弹层开着时每 15 秒自动对一次账；命中即停。
  useEffect(() => {
    if (!handoff || handoff.platform !== 'douyin' || handoffPoll?.found) return
    const timer = setInterval(() => { void pollHandoff(handoff.taskId) }, 15000)
    return () => clearInterval(timer)
  }, [handoff, handoffPoll?.found, pollHandoff])

  const addAccount = () => run('add', async () => {
    if (!adding) throw Error('先选择要添加的平台')
    const account = await matrixApi<Account>('accounts', {method: 'POST', body: JSON.stringify({platform: adding, alias})})
    setAlias(''); setAdding('')
    await matrixApi(`accounts/${account.id}/open`, {method: 'POST'})
    setNotice(`已创建「${account.alias}」并打开登录窗口（端口 ${account.port}）。扫码登录后点「检测」。`)
  })

  /**
   * 备注随时可改：写进 accounts.json 之后发一个事件，
   * 作品监控的账号下拉不用刷新整页也会跟着变。
   */
  const renameAccount = async (account: Account) => {
    const next = renameDraft.trim()
    if (!next || next === account.alias) { setRenaming(''); return }
    setBusy('rename'); setError(''); setNotice('')
    try {
      const updated = await matrixApi<Account>(`accounts/${account.id}`, {method: 'PATCH', body: JSON.stringify({alias: next})})
      setAccounts(rows => rows.map(row => row.id === account.id ? updated : row))
      setRenaming('')
      window.dispatchEvent(new Event('emotioncut:matrix-accounts-changed'))
      setNotice(`备注已改为「${updated.alias}」，作品监控那边的账号下拉会同步。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const publish = () => run('publish', async () => {
    if (!file.trim()) throw Error('请填写成片的完整路径')
    if (!picked.length) throw Error('请选择要发布的账号')
    if (!title.trim()) throw Error('请填写标题')
    // 每个比例各配一张：素材库按时间倒序，倒着覆盖一遍留下的就是最新那张。
    const covers: Record<string, string> = {}
    for (const item of [...assets].reverse()) if (item.ratio) covers[item.ratio] = item.file
    if (coverPick) {
      const hit = assets.find(item => item.file === coverPick)
      if (hit?.ratio) covers[hit.ratio] = coverPick
    }
    const created = await matrixApi<Task[]>('tasks', {method: 'POST', body: JSON.stringify({
      accountIds: picked, file: file.trim(), title: title.trim(), cover: coverPick || undefined, covers,
      tags: tags.split(/[\s,，#]+/).filter(Boolean), scheduledAt: scheduledAt || undefined, autoSubmit,
    })})
    const missing = created.filter(task => task.coverNote)
    setNotice(`已为 ${created.length} 个账号建好发布任务。${missing.length
      ? `其中 ${missing.length} 个平台的封面要留意：${missing[0].coverNote}。`
      : '每个平台的封面都按它自己的比例配好了。'}`)
  })

  /**
   * 按比例套版：模型出的无字底图 + 标题 → 每个平台比例各一张成品。
   * 字是字体文件排的，所以这里改标题只要 1 秒，不重新生图。
   */
  const composeCovers = () => run('compose', async () => {
    if (!composeBase) throw Error('先在上面素材库点一张无字底图（Codex / Seedance 出的原图）')
    if (!title.trim()) throw Error('先填视频标题，Agent 才知道要提炼什么')
    // 没选账号也能套版：给两个通用竖版兜底，别让按钮文案说能出、点了却报错。
    const ratios = neededRatios.length ? neededRatios.map(row => row.ratio) : ['9:16', '3:4']
    const main = await headlineFor()
    const result = await matrixApi<{added: CoverAsset[]; all: CoverAsset[]; files: {file: string; ratio: string; width: number; height: number}[]}>('cover/compose', {
      method: 'POST',
      body: JSON.stringify({base: `library:${composeBase}`, headline: main, title: title.trim(), ratios, palette: composePalette}),
    })
    setAssets(result.all)
    setPreview(result.files.map(item => `/api/matrix/cover/file/${encodeURIComponent(item.file)}`))
    setNotice(`已按参考图风格排好主副标题「${main.main} / ${main.sub}」，并出 ${result.files.map(item => item.ratio).join(' / ')} 各一版（${result.files.map(item => `${item.width}×${item.height}`).join('、')}）。点素材库里对应比例那张，发布时会自动配给同比例的平台。`)
  })

  const makeHeadline = () => run('headline', async () => {
    if (!title.trim()) throw Error('先填视频标题，再来提炼封面主副标题')
    const main = await headlineFor(true)
    setNotice(`封面主副标题已提炼（${main.by === 'agent' ? 'Agent' : '本地规则'}）：「${main.main}」/「${main.sub}」。套版用的是这行短标题，不是下面那条视频标题。`)
  })

  const saveProfiles = async () => {
    setBusy('profiles'); setError(''); setNotice('')
    try {
      const rows = await matrixApi<Profile[]>('profiles', {method: 'PUT', body: JSON.stringify({profiles: profileDraft})})
      setProfiles(rows); setProfileDraft(rows)
      setNotice('平台模版已保存；之后建任务会按新的字数与话题规则拼文案。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const learnCovers = async (files: FileList|null) => {
    setBusy('cover'); setError(''); setNotice('')
    try {
      const samples = await readImages(files)
      if (samples.length < 3) throw Error('建立稳定的封面规范，一次要选 3–10 张历史封面（单张只能当本次参考）')
      setPreview(samples)
      setNotice('正在读这组封面的版式与配色…')
      const memory = await matrixApi<CoverMemory>('cover/analyze', {method: 'POST', body: JSON.stringify({samples})})
      setCover(memory)
      setNotice(memory.source === 'agent'
        ? `已让 ${memory.agent || 'Agent'} 归纳出封面规范（${memory.samples.length} 张样本），之后的提示词都会沿用它。`
        : 'Agent 没连上，已用本地统计生成基础规范，可以直接改，也可以重试。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const removeTask = async (task: Task) => {
    if (!window.confirm(`删除这条任务记录？「${task.title.slice(0, 30)}」· ${name(task.platform)}。已回填的作品会保留在监控台。`)) return
    await run('del-task', () => matrixApi(`tasks/${task.id}`, {method: 'DELETE'}))
  }

  /** 成品封面：Codex / GPT-Image 出图后直接传进来，落盘进素材库，刷新不丢。 */
  const uploadCovers = async (files: FileList|null) => {
    setBusy('assets'); setError(''); setNotice('')
    try {
      const list = [...(files || [])].slice(0, 6)
      if (!list.length) return
      const big = list.find(item => item.size > 15 * 1024 * 1024)
      if (big) throw Error(`${big.name} 超过 15MB，导出时压一下再传`)
      if (list.reduce((n, item) => n + item.size, 0) > 44 * 1024 * 1024) throw Error('一次最多传 44MB，封面多就分两批传')
      const images = await Promise.all(list.map(async file => {
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result))
          reader.onerror = () => reject(reader.error)
          reader.readAsDataURL(file)
        })
        const size = await new Promise<{width: number; height: number}>((resolve, reject) => {
          const probe = new window.Image()
          probe.onload = () => resolve({width: probe.naturalWidth, height: probe.naturalHeight})
          probe.onerror = () => reject(Error(`${file.name} 读不出尺寸，换一张试试`))
          probe.src = data
        })
        return {data, ...size, source: 'codex'}
      }))
      const result = await matrixApi<{added: CoverAsset[]; assets: CoverAsset[]}>('cover/library', {method: 'POST', body: JSON.stringify({images})})
      setAssets(result.assets)
      if (result.added[0]) setCoverPick(result.added[0].file)
      setNotice(`已存入 ${result.added.length} 张成品封面；刷新不丢，发布时会带上你选的那张。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const removeCoverAsset = async (fileName: string) => {
    if (!window.confirm(`从素材库删除封面「${fileName}」？删除后已建任务不受影响，但文件不可恢复。`)) return
    setBusy('del-assets'); setError(''); setNotice('')
    try {
      const next = await matrixApi<CoverAsset[]>(`cover/library/${encodeURIComponent(fileName)}`, {method: 'DELETE'})
      setAssets(next)
      if (coverPick === fileName) setCoverPick('')
      setNotice('已从素材库删除。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  /** 固定出镜人物：落盘到用户目录的固定资源夹，清单记路径与用途，换作品也能复用；可为空。 */
  const uploadPerson = async (files: FileList|null) => {
    setBusy('person'); setError(''); setNotice('')
    try {
      const images = await readImages(files)
      if (!images.length) return
      const manifest = await matrixApi<PersonManifest>('cover/person', {method: 'POST', body: JSON.stringify({images})})
      setPersonRefs(manifest.refs)
      setNotice(manifest.refs.length > 1
        ? '已保存 2 张同一人物的参考照；之后每条封面都会按这张脸生成，不用重复上传。'
        : '已保存人物参考照；之后每条封面都会保持这位人物的身份，不用重复上传。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const removePerson = async () => {
    setBusy('person'); setError(''); setNotice('')
    try {
      const manifest = await matrixApi<PersonManifest>('cover/person', {method: 'DELETE'})
      setPersonRefs(manifest.refs)
      setNotice('人物参考照已删除；之后的封面按「无真人出镜」生成。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const coverPrompt = async () => {
    setBusy('prompt'); setError(''); setNotice('')
    try {
      const answer = await matrixApi<{text: string; agent: string; personRefs: number; hasSpec: boolean}>('cover/prompt', {
        method: 'POST',
        body: JSON.stringify({topic: title || '', platform: picked[0] ? accounts.find(a => a.id === picked[0])?.platform || '' : '', title: title || '', keywords: coverKeywords || ''}),
      })
      setCoverBrief(answer.text)
      const mode = answer.personRefs ? `已带上 ${answer.personRefs} 张固定人物参考照，保持同一张脸` : '按「无真人出镜」生成'
      setNotice(`封面提示词已由 ${answer.agent} 生成（${mode}）。四步方法论已埋进提示词，可直接交给生图或再改几句。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  /**
   * 用本机 Codex 的 image_gen 出图。
   * 生图真的在 codex.exe 里跑（ChatGPT 账号额度，不需要 OpenAI API Key），
   * 支持把固定人物照和素材库里的封面当参考图一起送进去；产物由后端复制进素材库。
   */
  const generateWithCodexCover = async () => {
    setBusy('codex'); setError(''); setNotice('')
    try {
      const refs: string[] = []
      if (codexPerson && personRefs.length) refs.push('person')
      if (codexRef) refs.push(`library:${codexRef}`)
      const result = await matrixApi<{assets: CoverAsset[]; all: CoverAsset[]; prompt: string; threadId: string; ms: number; refs: number; message: string}>('cover/codex', {
        method: 'POST',
        body: JSON.stringify({
          brief: coverBrief,
          topic: title || '',
          platform: picked[0] ? accounts.find(a => a.id === picked[0])?.platform || '' : '',
          title: title || '', keywords: coverKeywords || '',
          refs, count: 1, ratio: '9:16',
        }),
      })
      setAssets(result.all)
      if (result.assets.length) {
        setCoverPick(result.assets[0].file)
        setPreview(result.assets.map(item => `/api/matrix/cover/file/${encodeURIComponent(item.file)}`))
      }
      setNotice(`Codex 出了 ${result.assets.length} 张无字底图（${Math.round(result.ms / 1000)} 秒${result.refs ? `，带了 ${result.refs} 张参考图` : ''}）。接下来填好标题、点「按比例套版」，把字排上去再发。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy('') }
  }

  const copyBrief = async () => {
    try { await navigator.clipboard.writeText(coverBrief); setNotice('提示词已复制') }
    catch { setError('复制失败，手动选中文本复制') }
  }

  return <main data-mx>
    <header className="mx-head">
      <div>
        <span className="mx-eyebrow">Matrix Distribution / 矩阵分发</span>
        <h1>一次准备，<em>分发到全部账号</em></h1>
        <p>每个账号一套独立浏览器与登录态。发布走真实浏览器，停在「发布」按钮前由你确认；打开自动提交才会代为点击。</p>
      </div>
      <div style={{display: 'flex', gap: 8}}>
        <Link to="/pro/analytics" className="mx-btn" title="打开作品监控台"><Activity size={14}/>监控台</Link>
        <button className="mx-btn" disabled={!accounts.length || busy === 'check'} onClick={() => run('check', () => matrixApi('accounts/check-all', {method: 'POST'}).then(() => setNotice('已重新检测全部账号')))}>
          <RefreshCw size={14} className={busy === 'check' ? 'spin' : ''}/>检测登录态
        </button>
        <button className="mx-btn mx-btn--primary" disabled={!tasks.some(t => t.status === 'queued') || busy === 'queue'} onClick={() => run('queue', () => matrixApi('tasks/run-queue', {method: 'POST'}).then(() => setNotice('队列已执行')))}>
          <Play size={14}/>跑队列
        </button>
      </div>
    </header>

    <BrowserSetup />

    <section className="mx-stats">
      <div className="mx-stat"><span>Online</span><strong>{summary?.loggedIn ?? 0}<small style={{display: 'inline', fontSize: 15, color: 'var(--mx-ink-3)'}}> / {summary?.accounts ?? 0}</small></strong><small>已登录账号</small></div>
      <div className="mx-stat"><span>Queued</span><strong>{summary?.queued ?? 0}</strong><small>等待执行</small></div>
      <div className="mx-stat"><span>Confirm</span><strong>{summary?.ready ?? 0}</strong><small>待人工确认</small></div>
      <div className="mx-stat"><span>Works</span><strong>{summary?.works ?? 0}</strong><small>监控中的作品</small></div>
      <div className="mx-stat"><span>Comments</span><strong>{summary?.comments ?? 0}</strong><small>已采集评论</small></div>
    </section>

    {error && <div className="mx-note" data-tone="error" style={{marginBottom: 14}}><AlertTriangle size={15}/><span>{error}</span></div>}
    {notice && <div className="mx-note" data-tone="warn" style={{marginBottom: 14}}><Check size={15}/><span>{notice}</span></div>}

    <div className="mx-grid">
      <aside>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2>账号矩阵</h2><small>{accounts.length} 个 · 点账号选中</small></div>
          <div className="mx-panel__body">
            {!accounts.length && <div className="mx-empty">还没有账号。<br/>先在下面选平台添加，登录一次就一直记住。</div>}
            {grouped.map(group => <div className="mx-plat-group" key={group.id}>
              <header style={{borderLeftColor: PLATFORM_BRAND[group.id] || undefined}}>
                <span style={{display: 'inline-flex', alignItems: 'center', gap: 6}}><PlatformLogo platform={group.id} size={16}/>{group.name}</span>
                <em style={{fontStyle: 'normal'}}>{group.rows.length}</em>
              </header>
              {group.rows.map(account => {
                const on = picked.includes(account.id), live = account.lastCheck?.loggedIn
                const editing = renaming === account.id
                const nickname = account.nickname?.trim()
                const showNick = nickname && nickname !== account.alias
                return <div className="mx-account" key={account.id} data-picked={on || undefined}
                  onClick={() => setPicked(p => on ? p.filter(x => x !== account.id) : [...p, account.id])}
                  title={on ? '点卡片任意空白处取消选中' : '点卡片任意位置选中这个账号，参与本次发布'}>
                  {editing
                    ? <form className="mx-account__rename" onClick={event => event.stopPropagation()} onSubmit={event => { event.preventDefault(); void renameAccount(account) }}>
                      <input autoFocus value={renameDraft} maxLength={40} aria-label={`${account.alias} 的备注`}
                        onChange={event => setRenameDraft(event.target.value)}
                        onKeyDown={event => { if (event.key === 'Escape') setRenaming('') }}/>
                      <button type="submit" title="保存备注" disabled={busy === 'rename'}><Check size={12}/></button>
                      <button type="button" title="取消" onClick={() => setRenaming('')}><X size={12}/></button>
                    </form>
                    : <button className="mx-account__main" title={on ? '已选中，点一下取消' : '点一下选中这个账号，参与本次发布'} onClick={event => { event.stopPropagation(); setPicked(p => on ? p.filter(x => x !== account.id) : [...p, account.id]) }}>
                      <i className="mx-account__check" data-on={on || undefined}>{on ? <Check size={11}/> : null}</i>
                      <i className="mx-account__dot" data-on={live || undefined} title={live ? '登录态正常' : '未登录或未检测'}/>
                      <PlatformLogo platform={account.platform} size={18} style={{flex: 'none', marginTop: 1}}/>
                      <span><b>{account.alias}</b>
                      <small>{live ? '已登录 · 可发布' : account.lastCheck ? '未登录 · 点开窗口登录' : '未检测'}{showNick ? ` · 昵称 ${nickname}${account.platformId ? `（${account.platformId}）` : ''}` : ''}</small></span>
                    </button>}
                  <div className="mx-account__ops" onClick={event => event.stopPropagation()}>
                    <button className="mx-btn mx-btn--ghost" title="改备注" onClick={() => { setRenaming(account.id); setRenameDraft(account.alias) }}><Pencil size={12}/></button>
                    <button className="mx-btn mx-btn--ghost" title="打开登录窗口" onClick={() => run('open', () => matrixApi(`accounts/${account.id}/open`, {method: 'POST'}))}><ExternalLink size={12}/></button>
                    <button className="mx-btn mx-btn--ghost" title="解绑（保留登录态）" onClick={() => { if (confirm(`解绑「${account.alias}」？登录目录会保留，重新添加还能用。`)) void run('remove', () => matrixApi(`accounts/${account.id}`, {method: 'DELETE'}).then(() => setPicked(p => p.filter(x => x !== account.id)))) }}><Trash2 size={12}/></button>
                  </div>
                </div>
              })}
            </div>)}
          </div>
        </section>

        <section className="mx-panel">
          <div className="mx-panel__head"><h2>接入平台</h2><small>{platforms.length} 个</small></div>
          <div className="mx-panel__body">
            <div className="mx-row" style={{gridTemplateColumns: '1fr', gap: 8}}>
              <PlatformPicker platforms={platforms} value={adding} onChange={setAdding}/>
              <input className="mx-input" placeholder="账号备注，如「大号」" value={alias} onChange={e => setAlias(e.target.value)}/>
              <button className="mx-btn" disabled={!adding || busy === 'add'} onClick={() => void addAccount()}><Plus size={14}/>添加并登录</button>
            </div>
            <div className="mx-note" style={{marginTop: 12}}><Layers size={13}/><span>抖音 / 视频号 / 小红书 已适配自动上传与填表；其余平台会打开发布页，由你手动上传，不会假报成功。</span></div>
          </div>
        </section>
      </aside>

      <div>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2>发布编排</h2><small>{picked.length ? `已选 ${picked.length} 个账号` : '未选账号'}</small></div>
          <div className="mx-panel__body">
            <label className="mx-field"><span>成片路径</span>
              <input type="text" value={file} onChange={e => setFile(e.target.value)} placeholder="D:\剪辑工程库\...\exports\...\成片.mp4"/>
              <small>在文件管理器里右键成片 → 复制文件地址 → 粘贴到这里。发布用的是本地原文件，不重新编码。</small>
            </label>

            <section className="mx-sect">
              <header>
                <h3><Image size={13}/>封面</h3>
                <small>{assets.length ? `素材库 ${assets.length} 张` : cover?.spec ? `已建立规范 · ${cover.samples.length} 张样本` : '还没有成品封面'}</small>
              </header>
              <div className="mx-cover">
                <div className="mx-cover__side">
                  <div className="mx-cover__assets">
                    <h3><Image size={14}/>成品封面</h3>
                    <p>{assets.length
                      ? `素材库 ${assets.length} 张。点一张指定为本次发布封面。`
                      : '在 Codex / GPT-Image 里出好图，直接传进来。存入素材库，刷新不丢。'}</p>
                    {assets.length > 0 && <div className="mx-cover__grid">
                      {assets.slice(0, 9).map(item => <button type="button" key={item.file} className="mx-cover__cell" data-picked={coverPick === item.file || undefined} onClick={() => setCoverPick(item.file)} title={`${item.ratio || '封面'} · ${Math.max(1, Math.round(item.bytes / 1024))} KB · ${item.source}`}>
                        <img src={`/api/matrix/cover/file/${encodeURIComponent(item.file)}`} alt="成品封面"/>
                        {item.ratio && <span>{item.ratio}</span>}
                        <i className="mx-cover__del" title="删除这张" onClick={event => { event.stopPropagation(); void removeCoverAsset(item.file) }}><X size={10}/></i>
                      </button>)}
                    </div>}
                    <label className="mx-btn mx-btn--primary" style={{width: '100%', marginTop: 10}}>
                      {busy === 'assets' ? <RefreshCw size={12} className="spin"/> : <Plus size={12}/>}
                      {busy === 'assets' ? '上传中…' : '上传成品封面'}
                      <input type="file" accept="image/*" multiple hidden onChange={e => void uploadCovers(e.target.files)}/>
                    </label>
                    {coverPick
                      ? <small className="mx-cover__gen-note">本次发布带这张：{coverPick}</small>
                      : assets.length > 0 ? <small className="mx-cover__gen-note">没选封面时任务只发视频；封面可在平台发布页手动选。</small> : null}
                  </div>
                  <details className="mx-cover__mem">
                    <summary><Image size={14}/>封面风格记忆 <em>可选 · 只提炼规范</em></summary>
                    <p>{cover?.spec ? '规范只用于写提示词，不会作为参考图发给生图模型。' : '上传 3–10 张历史封面，让 Agent 归纳版式、配色与标题区规律；规范只用于写提示词。'}</p>
                    {coverThumbs.length > 0 && <div className="mx-cover__thumbs">
                      {coverThumbs.slice(0, 4).map((src, index) => <img key={index} src={src} alt={preview.length ? '生成的封面' : '历史封面'}/>)}
                      {coverThumbs.length > 4 && <span>+{coverThumbs.length - 4}</span>}
                    </div>}
                    {preview.length > 0 && <small className="mx-cover__gen-note">上面 {preview.length} 张是刚生成的封面，右键图片可另存；上传历史封面会替换它们。</small>}
                    <label className="mx-btn mx-btn--ghost" style={{width: '100%', marginTop: 10}}>
                      {busy === 'cover' ? <RefreshCw size={12} className="spin"/> : <Image size={12}/>}
                      {busy === 'cover' ? '正在读封面…' : cover?.spec ? '重新上传历史封面' : '上传历史封面'}
                      <input type="file" accept="image/*" multiple hidden onChange={e => void learnCovers(e.target.files)}/>
                    </label>
                    {cover?.spec && <details className="mx-cover__spec-view">
                      <summary>查看封面规范</summary>
                      <pre>{cover.spec}</pre>
                    </details>}
                  </details>
                  <div className="mx-cover__person">
                    <h3><UserRound size={14}/>固定出镜人物（可选）</h3>
                    <p>{personRefs.length ? '已保存。Codex 出图会把它作为参考照带上，保持同一张脸。' : '上传 1–2 张已授权的人物照，Codex 出图会带上它；不用真人可不传。'}</p>
                    {personRefs.length > 0 && <div className="mx-cover__person-thumbs">
                      {personRefs.map(ref => <img key={ref.file} src={`/api/matrix/cover/person/file/${encodeURIComponent(ref.file)}`} alt="人物参考照" title={ref.purpose}/>)}
                    </div>}
                    <div className="mx-cover__person-ops">
                      <label className="mx-btn mx-btn--ghost">
                        {busy === 'person' ? <RefreshCw size={12} className="spin"/> : <Plus size={12}/>}
                        {busy === 'person' ? '保存中…' : personRefs.length ? '更换' : '上传人物照'}
                        <input type="file" accept="image/*" multiple hidden onChange={e => void uploadPerson(e.target.files)}/>
                      </label>
                      {personRefs.length > 0 && <button type="button" className="mx-btn mx-btn--ghost" disabled={busy === 'person'} onClick={() => void removePerson()}><Trash2 size={12}/>清空</button>}
                    </div>
                    {personRefs.length > 0 && <details className="mx-cover__spec-view">
                      <summary>本地路径与用途</summary>
                      <pre>{personRefs.map(ref => `${ref.purpose}\n${ref.path}`).join('\n\n')}</pre>
                    </details>}
                  </div>
                </div>
                <div className="mx-cover__brief">
                  <label className="mx-field" style={{margin: 0}}>
                    <span>重点词（选填 · 只影响底图提示词；封面强调条由 Agent 自动提炼）</span>
                    <input type="text" value={coverKeywords} onChange={e => setCoverKeywords(e.target.value)} placeholder="例如：永久记忆、Codex"/>
                  </label>
                  <label className="mx-field" style={{margin: '10px 0 0'}}>
                    <span style={{display: 'flex', alignItems: 'center', gap: 8}}>
                      底图提示词（只写画面，不写文字）
                      <button type="button" className="mx-linkbtn" onClick={() => setCoverBrief(COVER_BRIEF_TEMPLATE)}>恢复模板</button>
                    </span>
                    <textarea value={coverBrief} onChange={e => setCoverBrief(e.target.value)} placeholder="这里预填的是「只出画面、不出文字」的底图提示词，把【】换成你的内容"/>
                    <small>模型只负责画面；标题、关键词由下面的「按比例套版」用字体排上去——<b>字不会错</b>，改标题也不用重新生图。两条出图路：<b>①「用 Codex 出图」</b>本机 Codex，走 ChatGPT 额度；<b>② 复制提示词出去出图</b>，成品传回左上「成品封面」。{personRefs.length ? `「用 Codex 出图」会带上 ${personRefs.length} 张人物参考照` : '没上传人物照就按无真人出镜生成'}。</small>
                  </label>
                  <div className="mx-cover__ops">
                    <button className="mx-btn mx-btn--primary" disabled={!!busy || !codex?.available} onClick={() => void generateWithCodexCover()}><Rocket size={14}/>{busy === 'codex' ? 'Codex 出图中（1–2 分钟）…' : '用 Codex 出图'}</button>
                    <button className="mx-btn" disabled={!!busy} onClick={() => void coverPrompt()}><Sparkles size={14}/>{busy === 'prompt' ? '写提示词中…' : '按规范写提示词'}</button>
                    <button className="mx-btn mx-btn--ghost" disabled={!coverBrief} onClick={() => void copyBrief()}><Copy size={13}/>复制</button>
                  </div>

                  <div className="mx-cover__compose">
                    <div className="mx-cover__compose-head">
                      <b><Type size={13}/>按比例套版</b>
                      <small>底图 + 标题 → 每个平台比例一张</small>
                    </div>
                    <div className="mx-cover__ratios">
                      {neededRatios.length
                        ? neededRatios.map(row => {
                          const have = assets.some(item => item.ratio === row.ratio && item.source === 'composed')
                          return <span key={row.ratio} className="mx-cover__ratio" data-miss={have ? undefined : true}>
                            <b>{row.ratio}</b><em>{row.platforms.join(' / ')}</em><i>{have ? '已套版' : '待套版'}</i>
                          </span>
                        })
                        : <small>左侧选账号后，这里会列出本次要出的比例（抖音 9:16、小红书 3:4、视频号 6:7…）。</small>}
                    </div>
                    <label className="mx-field" style={{margin: '10px 0 0'}}>
                      <span>无字底图</span>
                      <select value={composeBase} onChange={e => setComposeBase(e.target.value)}>
                        <option value="">从素材库选一张…</option>
                        {baseOptions.map(item => <option key={item.file} value={item.file}>{item.ratio || '未知比例'} · {item.source} · {item.file}</option>)}
                      </select>
                    </label>
                    <div className="mx-cover__auto">
                      <b>② 标题与风格：交给我自动设计</b>
                      <small>Agent 从视频标题里提炼「短主标题 + 副标题」，按参考图的爆款风格排上方大字、黄/青强调条，主体留在中下部。不用自己选配色。</small>
                    </div>
                    <details className="mx-cover__advanced">
                      <summary>高级：手动指定配色（默认自动，不用展开）</summary>
                      <select value={composePalette} onChange={e => setComposePalette(e.target.value as 'auto'|'viral'|'ink'|'warm'|'paper')}>
                        <option value="auto">自动 · 参考图爆款风格（推荐）</option>
                        <option value="viral">爆款：白字黄条 + 深色描边</option>
                        <option value="ink">墨黑 + 青绿</option>
                        <option value="warm">暖橙</option>
                        <option value="paper">浅色纸感</option>
                      </select>
                    </details>
                    <button className="mx-btn mx-btn--primary" style={{width: '100%', marginTop: 8}} disabled={!!busy} onClick={() => void composeCovers()}>
                      <Type size={14}/>{busy === 'compose' ? '排版合成中…' : neededRatios.length ? `按 ${neededRatios.map(r => r.ratio).join(' / ')} 自动套版` : '按 9:16 / 3:4 自动套版'}
                    </button>
                    <small className="mx-cover__gen-note">改了视频标题后再点一次，会重新提炼并秒级重排，不重新生图。</small>
                  </div>

                  <div className="mx-cover__codex">
                    <p className="mx-cover__codex-head">
                      <b>Codex 这条路</b>：生图在你本机 Codex 里跑（ChatGPT 账号额度，不用另配 API Key），出的是无字底图，自动存进上方「成品封面」，再套版就能发。
                    </p>
                    {codex && !codex.available && <small className="mx-cover__codex-warn"><AlertTriangle size={12}/>没找到本机 Codex。先装 Codex 桌面版，或把 codex.exe 路径写进环境变量 CODEX_BIN，再刷新本页。</small>}
                    <div className="mx-cover__codex-opts">
                      <label><input type="checkbox" checked={codexPerson} disabled={!personRefs.length} onChange={e => setCodexPerson(e.target.checked)}/>带上固定出镜人物照{personRefs.length ? `（${personRefs.length} 张）` : '（还没上传）'}</label>
                      <label>风格参考封面
                        <select value={codexRef} onChange={e => setCodexRef(e.target.value)}>
                          <option value="">不用参考</option>
                          {assets.slice(0, 20).map(item => <option key={item.file} value={item.file}>{item.ratio || '未知比例'} · {item.file}</option>)}
                        </select>
                      </label>
                    </div>
                  </div>
                </div>
              </div>

              <details className="mx-cover__profiles">
                <summary><Layers size={13}/>平台模版（高级：封面比例 / 标题字数 / 话题位置）<em>一般不用改，改完点保存</em></summary>
                <p className="mx-cover__profiles-hint">这里决定「建任务时每家平台用哪张比例的封面、标题最多几个字、话题拼在标题还是正文」。日常使用保持默认即可；平台改版或你想换封面比例时才展开改，改完一定点下面的「保存平台模版」。支持 <code>{'{title}'}</code> <code>{'{desc}'}</code> <code>{'{tags}'}</code>。</p>
                <div className="mx-cover__profiles-head">
                  <span>平台</span><span>封面比例</span><span>标题上限</span><span>话题数</span><span>话题去向</span><span>描述模板</span>
                </div>
                {profileDraft.map((profile, index) => <div className="mx-cover__profiles-row" key={profile.id}>
                  <span title={profile.note} style={{display: 'inline-flex', alignItems: 'center', gap: 5}}><PlatformLogo platform={profile.id} size={13}/>{profile.name}{profile.altRatios.length ? <em> +{profile.altRatios.join(' ')}</em> : null}</span>
                  <input value={profile.coverRatio} onChange={e => setProfileDraft(rows => rows.map((row, i) => i === index ? {...row, coverRatio: e.target.value} : row))} aria-label={`${profile.name} 封面比例`}/>
                  <input type="number" min={4} max={200} value={profile.titleLimit} onChange={e => setProfileDraft(rows => rows.map((row, i) => i === index ? {...row, titleLimit: Number(e.target.value) || 0} : row))} aria-label={`${profile.name} 标题上限`}/>
                  <input type="number" min={0} max={30} value={profile.tagLimit} onChange={e => setProfileDraft(rows => rows.map((row, i) => i === index ? {...row, tagLimit: Number(e.target.value) || 0} : row))} aria-label={`${profile.name} 话题数`}/>
                  <select value={profile.tagPlacement} onChange={e => setProfileDraft(rows => rows.map((row, i) => i === index ? {...row, tagPlacement: e.target.value as Profile['tagPlacement']} : row))} aria-label={`${profile.name} 话题去向`}>
                    <option value="title">拼在标题后</option>
                    <option value="body">写进正文</option>
                    <option value="description">写进描述</option>
                  </select>
                  <input value={profile.descTemplate} onChange={e => setProfileDraft(rows => rows.map((row, i) => i === index ? {...row, descTemplate: e.target.value} : row))} aria-label={`${profile.name} 描述模板`}/>
                </div>)}
                <div className="mx-cover__profiles-foot">
                  <small>模板里可用 <code>{'{title}'}</code> <code>{'{desc}'}</code> <code>{'{tags}'}</code>。标题超上限会截断并在任务卡上标出来，不会静默丢字。</small>
                  <button className="mx-btn" disabled={busy === 'profiles'} onClick={() => void saveProfiles()}>{busy === 'profiles' ? '保存中…' : '保存平台模版'}</button>
                </div>
              </details>
            </section>

            <label className="mx-field"><span>视频标题 <em style={{fontStyle: 'normal', color: 'var(--mx-ink-3)'}}>· 用于发布文案与提炼封面标题，不会原样印到封面上</em></span>
              <input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="这条片子的标题"/>
            </label>
            <div className="mx-headline" data-ready={headline && headlineKey === headlineSource ? true : undefined}>
              <div>
                <span className="mx-eyebrow">COVER HEADLINE / 封面主副标题</span>
                <b>{headline && headlineKey === headlineSource ? headline.main : '点右边按钮，从标题里提炼短主标题'}</b>
                <small>{headline && headlineKey === headlineSource ? `${headline.sub || '（无副标题）'} · ${headline.by === 'agent' ? 'Agent 提炼' : '本地提炼'}` : '封面只印这两行，主标题 4–10 字、副标题补充信息'}</small>
              </div>
              <button className="mx-btn" disabled={!title.trim() || !!busy} onClick={() => void makeHeadline()}><Sparkles size={13}/>{busy === 'headline' ? '提炼中…' : '提炼主副标题'}</button>
            </div>
            <label className="mx-field"><span>话题</span>
              <input type="text" value={tags} onChange={e => setTags(e.target.value)} placeholder="#AI剪辑 #效率工具（空格或逗号分隔）"/>
            </label>
            <div className="mx-row">
              <label className="mx-field"><span><Clock size={12}/>定时</span>
                <input type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)}/>
                <small>只作为排队时间提醒，不会在后台无人值守发送。</small>
              </label>
              <label className="mx-field"><span>提交方式</span>
                <label className="mx-check">
                  <input type="checkbox" checked={autoSubmit} onChange={e => setAutoSubmit(e.target.checked)}/>
                  <span>自动点发布<small>不推荐：平台可能判定为异常行为。关着就停在发布按钮前等你确认。</small></span>
                </label>
              </label>
            </div>
            <button className="mx-btn mx-btn--primary" style={{width: '100%', padding: 13, fontSize: 14}} disabled={busy === 'publish'} onClick={() => void publish()}>
              <Rocket size={16}/>生成 {picked.length || ''} 个发布任务
            </button>
          </div>
        </section>

        <section className="mx-panel">
          <div className="mx-panel__head"><h2>任务流水</h2><small>{tasks.length} 条 · 待你点的去平台发布页点最后一步</small></div>
          <div className="mx-panel__body" style={{maxHeight: 520, overflowY: 'auto'}}>
            {!tasks.length && <div className="mx-empty">还没有发布任务。</div>}
            {tasks.map(task => <div className="mx-task" key={task.id} data-state={task.status}>
              <header>
                <b>{platTag(task.platform)} · {accounts.find(a => a.id === task.accountId)?.alias || '已解绑'}</b>
                <span className="mx-tag" data-tone={STATE_TONE[task.status] || undefined}>{STATE_LABEL[task.status]}</span>
              </header>
              <p>{task.title || '无标题'} · {task.step}</p>
              {task.cover && <div className="mx-task__cover">
                <img src={`/api/matrix/cover/file/${encodeURIComponent(task.cover)}`} alt="封面"/>
                <span>封面：{task.cover}{task.coverRatio ? ` · ${task.coverRatio}` : ''}</span>
                <button type="button" className="mx-linkbtn" title="在应用内看这张封面大图，可随时关闭" onClick={() => { const coverFile = task.cover || ''; setViewer({src: `/api/matrix/cover/file/${encodeURIComponent(coverFile)}`, title: `封面 · ${coverFile}`}) }}>看封面</button>
              </div>}
              {task.coverNote && <p className="mx-task__note"><AlertTriangle size={11}/>{task.coverNote}</p>}
              {task.error && <pre>{task.error}</pre>}
              {task.url && <p style={{margin: '6px 0 0', fontSize: 11.5}}><a href={task.url} target="_blank" rel="noreferrer" style={{color: 'var(--mx-mint)'}}>已发布链接 ↗</a></p>}
              {confirming === task.id && <div style={{display: 'flex', gap: 6, marginTop: 8}}>
                <input type="text" className="mx-input" style={{flex: 1, fontSize: 12}} placeholder="粘贴发布后的作品链接" value={confirmUrl} onChange={e => setConfirmUrl(e.target.value)}/>
                <button className="mx-btn mx-btn--primary" style={{padding: '6px 10px', fontSize: 11}} disabled={!confirmUrl.trim() || busy === 'confirm'} onClick={() => run('confirm', async () => {
                  await matrixApi(`tasks/${task.id}/confirm`, {method: 'POST', body: JSON.stringify({url: confirmUrl.trim()})})
                  setConfirming(''); setConfirmUrl(''); setNotice('已回填链接，作品已自动进入监控台。')
                })}>登记</button>
                <button className="mx-btn mx-btn--ghost" onClick={() => { setConfirming(''); setConfirmUrl('') }}>取消</button>
              </div>}
              <div className="mx-task__ops">
                {(task.status === 'queued' || task.status === 'failed') && <button className="mx-btn mx-btn--ghost" title="重新连账号窗口、打开发布页并尝试填表；停在发布前等你确认，不会自动提交" onClick={() => run('run', () => matrixApi(`tasks/${task.id}/run`, {method: 'POST'}).then(() => setNotice('已重新执行，进度看这条任务的状态；失败原因会写在卡片里。')))}><Play size={11}/>执行</button>}
                {task.status !== 'done' && task.status !== 'cancelled'
                  && <button className="mx-btn mx-btn--ghost" title={task.platform === 'douyin' ? '人在手机抖音官方发布，桌面自动对账回链（合规：不碰发布动作，只读自己账号的作品列表）' : '弹出文案包复制到手机，在该平台 App 里官方发布；发完用「待你点 · 去发布」回填链接'} onClick={() => run('handoff', async () => {
                    const r = await matrixApi<{task: Task; copy: {title: string; caption: string; tags: string}}>(`tasks/${task.id}/handoff`, {method: 'POST'})
                    setHandoff({taskId: task.id, platform: task.platform, copy: r.copy}); setHandoffPoll(null)
                  })}><Smartphone size={11}/>手机发布</button>}
                {task.status === 'ready' && <button className="mx-btn mx-btn--ghost" title="平台最后一步要你点：先去发布页发完，再回填链接" onClick={() => { setConfirming(confirming === task.id ? '' : task.id); setConfirmUrl(task.url || '') }}><Check size={11}/>待你点 · 去发布</button>}
                {(task.status === 'queued' || task.status === 'ready') && <button className="mx-btn mx-btn--ghost" onClick={() => run('cancel', () => matrixApi(`tasks/${task.id}/cancel`, {method: 'POST'}))}><X size={11}/>取消</button>}
                {(task.url || platforms.find(p => p.id === task.platform)?.createUrl)
                  && <a className="mx-btn mx-btn--ghost" href={task.url || platforms.find(p => p.id === task.platform)?.createUrl} target="_blank" rel="noreferrer" title="打开平台作品页或发布页，最后一步在这里点"><ExternalLink size={11}/>打开发布页</a>}
                <button className="mx-btn mx-btn--ghost" title="删除这条任务记录" disabled={busy === 'del-task'} onClick={() => void removeTask(task)}><Trash2 size={11}/>删除</button>
                <span style={{marginLeft: 'auto', fontSize: 11, color: 'var(--mx-ink-3)'}}>{new Date(task.updatedAt).toLocaleTimeString('zh-CN')}</span>
              </div>
            </div>)}
          </div>
        </section>
      </div>

      <aside>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2>已选账号</h2><small>{picked.length}</small></div>
          <div className="mx-panel__body">
            {!picked.length && <div className="mx-empty" style={{padding: '10px 0'}}>点左侧账号把它加入本次发布。</div>}
            {picked.map(id => {
              const account = accounts.find(a => a.id === id)
              if (!account) return null
              return <div key={id} style={{display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', fontSize: 12.5}}>
                <i className="mx-account__dot" data-on={account.lastCheck?.loggedIn || undefined}/>
                <span style={{display: 'inline-flex', alignItems: 'center', gap: 5}}><PlatformLogo platform={account.platform} size={13}/>{name(account.platform)} · {account.alias}</span>
                <button className="mx-btn mx-btn--ghost" style={{marginLeft: 'auto', padding: '3px 6px'}} onClick={() => setPicked(p => p.filter(x => x !== id))}><X size={11}/></button>
              </div>
            })}
            {online.length > 0 && picked.length > 0 && <div className="mx-note" style={{marginTop: 10, fontSize: 11.5}}><span>本次会写 {picked.length} 份，共用一个成片文件。</span></div>}
            {picked.some(id => !accounts.find(a => a.id === id)?.lastCheck?.loggedIn) && <div className="mx-note" data-tone="warn" style={{marginTop: 8, fontSize: 11.5}}><span>有账号还没确认登录，执行前建议先点「检测登录态」。</span></div>}
          </div>
        </section>
        <section className="mx-panel">
          <div className="mx-panel__head"><h2>说明</h2><small>请先读</small></div>
          <div className="mx-panel__body" style={{fontSize: 12.5, lineHeight: 1.85, color: 'var(--mx-ink-2)'}}>
            <p style={{margin: 0}}>· 账号窗口必须开着：发布连的是那个窗口，不是新起一个。</p>
            <p style={{margin: '8px 0 0'}}>· 「对标研究」的抖音搜索和抓作品，用的也是这里第一个抖音账号的登录态，不用再登一次。</p>
            <p style={{margin: '8px 0 0'}}>· 定时排期由后台调度器每 30 秒自动执行；填完表停在发布前的任务等你确认。</p>
            <p style={{margin: '8px 0 0'}}>· 「手机发布」全平台可用：文案包复制到手机官方发布；抖音额外支持自动对账回链。</p>
            <p style={{margin: '8px 0 0'}}>· 平台改版会让自动填表失效；失败会留下原因，不会假报成功。</p>
            <p style={{margin: '8px 0 0'}}>· 所有输入都会自动存成草稿，切页回来不用重填。</p>
          </div>
        </section>
      </aside>
    </div>

    {handoff && <div className="mx-viewer" role="dialog" aria-modal="true" aria-label="手机发布" onClick={() => setHandoff(null)}>
      <div className="mx-viewer__bar">
        <b>手机发布 · {name(handoff.platform)}{handoff.platform === 'douyin' ? '（官方通道 + 自动对账）' : '（文案随行）'}</b>
        <button className="mx-btn mx-btn--ghost" onClick={() => setHandoff(null)}><X size={14}/>关闭</button>
      </div>
      <div className="mx-viewer__stage" style={{maxWidth: 560, maxHeight: '70vh', overflow: 'auto'}} onClick={event => event.stopPropagation()}>
        <p className="mx-dt__minor" style={{marginTop: 0, lineHeight: 1.8}}>{handoff.platform === 'douyin'
          ? '① 把这条视频传到手机（微信文件传输助手 / 网盘都行）。② 在抖音 App 里发布，文案照抄下面的文案包。③ 发完保持这个账号的窗口开着——这里每 15 秒自动对账一次，匹配到就自动回链并登记监控台。'
          : '① 把这条视频传到手机（微信文件传输助手 / 网盘都行）。② 在该平台 App 里官方发布，文案照抄下面的文案包。③ 发完回到任务卡点「待你点 · 去发布」回填作品链接（自动对账目前只支持抖音）。'}</p>
        <h4 className="mx-dt__h">文案包
          <button className="mx-linkbtn" style={{marginLeft: 10}} onClick={() => { const text = [handoff.copy.caption || handoff.copy.title, handoff.copy.tags].filter(Boolean).join('\n'); void navigator.clipboard.writeText(text).then(() => setNotice('文案包已复制，去手机粘贴')).catch(() => setNotice('复制失败，请手动选中复制')) }}>一键复制</button>
        </h4>
        <pre className="mx-review-text" style={{whiteSpace: 'pre-wrap'}}>{[handoff.copy.caption || handoff.copy.title, handoff.copy.tags].filter(Boolean).join('\n')}</pre>
        <div style={{display: 'flex', gap: 10, marginTop: 14, alignItems: 'center'}}>
          {handoff.platform === 'douyin'
            ? <>
              <button className="mx-btn mx-btn--primary" disabled={!!handoffPoll?.checking || !!handoffPoll?.found} onClick={() => void pollHandoff(handoff.taskId)}>
                {handoffPoll?.checking ? '对账中…' : handoffPoll?.found ? '已回链 ✓' : '立即对账'}
              </button>
              <small style={{color: 'var(--mx-ink-2)'}}>
                {handoffPoll?.found ? '作品已自动回链并登记监控台。'
                  : handoffPoll?.scanned !== undefined ? `读了 ${handoffPoll.scanned} 条作品，还没匹配到标题；手机发出去后再点一次或等自动重试。`
                  : '手机发布完成后会自动对账。'}
              </small>
            </>
            : <small style={{color: 'var(--mx-ink-2)'}}>发完记得回任务卡点「待你点 · 去发布」回填链接，作品会自动进监控台。</small>}
        </div>
        {handoffPoll?.found && handoffPoll.url && <p style={{margin: '10px 0 0'}}><a href={handoffPoll.url} target="_blank" rel="noreferrer" style={{color: 'var(--mx-mint)'}}>作品链接 ↗</a></p>}
      </div>
    </div>}

    {viewer && <div className="mx-viewer" role="dialog" aria-modal="true" aria-label={viewer.title} onClick={() => setViewer(null)}>
      <div className="mx-viewer__bar">
        <b>{viewer.title}</b>
        <button className="mx-btn mx-btn--ghost" onClick={() => setViewer(null)}><X size={14}/>关闭</button>
      </div>
      <div className="mx-viewer__stage" onClick={event => event.stopPropagation()}>
        <img src={viewer.src} alt={viewer.title}/>
      </div>
      <small className="mx-viewer__hint">点这里或右上角「关闭」即可返回矩阵分发，不会退出程序。</small>
    </div>}
  </main>
}
