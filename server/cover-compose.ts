/**
 * 封面套版：模型只出「无字底图」，标题、关键词、次级信息由这里排版合成。
 *
 * 为什么不让模型画字：中文是生图模型最弱的一环，5–8 字以上就开始缺笔、错字、
 * 同音替换（「失忆」画成「失意」）；而标题恰恰是封面唯一必须逐字正确的部分。
 * 交给 HTML + 字体文件排版，字形 100% 正确，改一个字 1 秒重排，同一张底图
 * 还能按 9:16 / 3:4 / 6:7 / 16:10 各排一版——这是模型做不到的。
 *
 * 实现复用发布已经在用的 playwright-core：起一个 headless 浏览器，把一张
 * 定尺页面截成 PNG/JPEG。不引入 sharp、不引入 canvas 原生模块。
 */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import {resolveBrowser} from './browser-executable.ts'
import {ratioSize} from './cover-platforms.ts'

export type ComposePalette = 'auto' | 'viral' | 'ink' | 'warm' | 'paper'
export type ComposeInput = {
  /** 底图绝对路径（必须已存在，调用方负责校验）。 */
  base: string
  title: string
  /** 第一视觉关键词，1–2 个；做成标题上方的强调条。 */
  keyword?: string
  /** 次级信息，如系列名 / 期数。 */
  subtitle?: string
  /** 要出哪几个比例。 */
  ratios: string[]
  palette?: ComposePalette
  /** 输出目录。 */
  outDir: string
  /** 四边安全区百分比，默认 6。 */
  safeArea?: number
  /** 底部额外留白百分比，抖音/快手这类底部有按钮条的要用。 */
  bottomInset?: number
}

export type ComposedFile = {file: string; path: string; ratio: string; width: number; height: number; bytes: number}

const MIME: Record<string, string> = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}

const PALETTES: Record<Exclude<ComposePalette, 'auto'>, {scrim: string; ink: string; accent: string; chipInk: string; sub: string; edge: string}> = {
  // 参考图风格：上方大字、厚描边、霓虹阴影，黄/青做第一视觉，主体留在中下部。
  viral: {scrim: 'rgba(3,8,12,0.92)', ink: '#FFFFFF', accent: '#FFE14A', chipInk: '#071015', sub: 'rgba(255,255,255,0.92)', edge: '#07131A'},
  ink: {scrim: 'rgba(4,10,14,0.86)', ink: '#FFFFFF', accent: '#5FE3B4', chipInk: '#04231B', sub: 'rgba(255,255,255,0.82)', edge: '#051015'},
  warm: {scrim: 'rgba(24,12,6,0.86)', ink: '#FFF7EC', accent: '#FFB454', chipInk: '#2A1602', sub: 'rgba(255,247,236,0.82)', edge: '#241005'},
  paper: {scrim: 'rgba(250,250,246,0.94)', ink: '#101619', accent: '#0F6E56', chipInk: '#F2FBF6', sub: 'rgba(16,22,25,0.72)', edge: '#FFFFFF'},
}

/** auto 默认走参考图的爆款风格；显式指定时照指定走，Advanced 才需要手动选。 */
function paletteOf(value: ComposePalette | undefined): Exclude<ComposePalette, 'auto'> {
  return !value || value === 'auto' ? 'viral' : value
}

/** 汉字按 1em、拉丁字符按 0.55em 估宽；标题是中文，误差不影响断行。 */
function charWidth(char: string): number {
  return /[\u3000-\u9fff\uff00-\uffef]/.test(char) ? 1 : /[A-Z0-9]/.test(char) ? 0.62 : 0.5
}

function wrapByWidth(text: string, widthEm: number): string[] {
  const lines: string[] = []
  let line = ''
  let used = 0
  for (const char of Array.from(text)) {
    const w = charWidth(char)
    if (used + w > widthEm && line) {
      lines.push(line)
      line = ''
      used = 0
    }
    line += char
    used += w
  }
  if (line) lines.push(line)
  return lines
}

type Layout = {size: number; lines: string[]; lineHeight: number}

/**
 * 找最大可用字号：先按宽度断行，行数或总高超出预算就缩一档。
 * maxLines 竖版 3 行、横版 2 行——再多缩到信息流里就成蚂蚁了。
 */
function fitTitle(title: string, width: number, heightBudget: number, maxLines: number): Layout {
  const maxSize = Math.round(width * 0.155)
  const minSize = Math.max(28, Math.round(width * 0.035))
  for (let size = maxSize; size >= minSize; size -= 4) {
    const lines = wrapByWidth(title, width / size)
    const lineHeight = 1.2
    if (lines.length <= maxLines && lines.length * size * lineHeight <= heightBudget) return {size, lines, lineHeight}
  }
  const size = minSize
  const lines = wrapByWidth(title, width / size).slice(0, maxLines)
  return {size, lines, lineHeight: 1.2}
}

function esc(text: string): string {
  return String(text).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char] as string))
}

function html(input: ComposeInput, size: {width: number; height: number}, dataUrl: string): string {
  const palette = PALETTES[paletteOf(input.palette)]
  const safe = Math.min(Math.max(Number(input.safeArea) || 6, 0), 20) / 100
  const bottom = Math.min(Math.max(Number(input.bottomInset) || 0, 0), 30) / 100
  const vertical = size.height >= size.width
  // 参考图是「上方大字 + 中下部真人」：竖版把标题压在上 1/3，横版仍在左下。
  const topTitle = vertical
  const padX = Math.round(size.width * safe)
  const padY = Math.round(size.height * safe)
  const blockWidth = vertical ? size.width - padX * 2 : Math.round(size.width * 0.54)
  const bottomLimit = Math.round(size.height * (1 - safe - bottom))
  const budget = Math.round(size.height * (vertical ? 0.34 : 0.44))
  const layout = fitTitle(input.title || '', blockWidth, budget, vertical ? 3 : 2)
  const chipSize = Math.max(24, Math.round(layout.size * 0.3))
  const subSize = Math.max(20, Math.round(layout.size * 0.28))
  const gap = Math.round(layout.size * 0.18)
  const edge = Math.max(2, Math.round(layout.size * 0.045))
  const blockPos = topTitle
    ? `top:${padY}px`
    : `bottom:${Math.round(size.height - bottomLimit)}px`
  const scrim = topTitle
    ? `linear-gradient(to bottom,${palette.scrim} 0%,${palette.scrim} 24%,rgba(0,0,0,0) 62%)`
    : `linear-gradient(to top,${palette.scrim} 0%,${palette.scrim} 18%,rgba(0,0,0,0) 62%)`

  const titleHtml = layout.lines.map(line => `<div>${esc(line)}</div>`).join('')
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:${size.width}px;height:${size.height}px;overflow:hidden;background:#05080a}
  .wrap{position:relative;width:100%;height:100%;overflow:hidden}
  .base{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center 58%}
  .scrim{position:absolute;inset:0;background:${scrim}}
  .block{position:absolute;left:${padX}px;${blockPos};width:${blockWidth}px;color:${palette.ink};font-family:"Source Han Sans SC","Microsoft YaHei","PingFang SC","Noto Sans SC",sans-serif;-webkit-font-smoothing:antialiased}
  .chip{display:inline-block;background:${palette.accent};color:${palette.chipInk};font-size:${chipSize}px;font-weight:800;letter-spacing:.08em;padding:${Math.round(chipSize * 0.2)}px ${Math.round(chipSize * 0.55)}px;border-radius:${Math.round(chipSize * 0.2)}px;margin-bottom:${gap}px;box-shadow:0 ${edge}px ${edge * 3}px rgba(0,0,0,.45)}
  .title{font-size:${layout.size}px;line-height:${layout.lineHeight};font-weight:900;letter-spacing:.01em;-webkit-text-stroke:${edge * 0.45}px ${palette.edge};paint-order:stroke fill;text-shadow:0 ${edge}px 0 ${palette.edge},0 ${edge * 2}px ${edge * 4}px rgba(0,0,0,.72),0 ${edge * 5}px ${edge * 9}px rgba(0,0,0,.45)}
  .sub{display:inline-block;margin-top:${Math.round(gap * 1.1)}px;background:${palette.accent};color:${palette.chipInk};font-size:${subSize}px;letter-spacing:.06em;font-weight:800;padding:${Math.round(subSize * 0.28)}px ${Math.round(subSize * 0.65)}px;border-radius:${Math.round(subSize * 0.18)}px;box-shadow:0 ${edge}px ${edge * 3}px rgba(0,0,0,.4)}
  </style></head><body><div class="wrap">
    <img class="base" src="${dataUrl}" alt="">
    <div class="scrim"></div>
    <div class="block">
      ${input.keyword ? `<div class="chip">${esc(input.keyword)}</div>` : ''}
      <div class="title">${titleHtml}</div>
      ${input.subtitle ? `<div class="sub">${esc(input.subtitle)}</div>` : ''}
    </div>
  </div></body></html>`
}

type BrowserLike = {newPage: () => Promise<any>; close: () => Promise<void>; isConnected: () => boolean}
let shared: {browser: BrowserLike; at: number} | null = null

/** 浏览器是重资源：进程内复用，空闲 90 秒再关，别每次套版都起一次。 */
async function browser(): Promise<BrowserLike> {
  if (shared && shared.browser.isConnected()) return shared.browser
  const {chromium} = await import('playwright-core')
  const target = resolveBrowser('auto')
  const candidates = target ? [target.path] : []
  let lastError: unknown
  for (const executablePath of candidates) {
    try {
      const launched = await chromium.launch({
        executablePath, headless: true,
        args: ['--no-proxy-server', '--hide-scrollbars', '--force-color-profile=srgb'],
      }) as unknown as BrowserLike
      shared = {browser: launched, at: Date.now()}
      return launched
    } catch (cause) { lastError = cause }
  }
  throw Object.assign(Error(`套版需要一个 Chromium 内核浏览器，但没能启动${lastError instanceof Error ? `：${lastError.message}` : ''}。请在设置里装 Ego Lite 或 Chrome。`), {status: 500})
}

async function release(browserRef: BrowserLike) {
  shared = {...(shared || {at: Date.now()}), browser: browserRef, at: Date.now()}
}

/** 出一套按比例排好版的封面。任何一张失败都抛错，不假装成功。 */
export async function composeCovers(input: ComposeInput): Promise<ComposedFile[]> {
  const title = String(input.title || '').trim()
  if (!title) throw Object.assign(Error('套版需要标题：封面上的字就是它'), {status: 400})
  const ext = path.extname(input.base).toLowerCase()
  const mime = MIME[ext]
  if (!mime) throw Object.assign(Error('底图只支持 png / jpg / webp'), {status: 400})
  if (!fs.existsSync(input.base)) throw Object.assign(Error('底图文件不在了，重新选一张'), {status: 400})
  const dataUrl = `data:${mime};base64,${fs.readFileSync(input.base).toString('base64')}`

  const wanted = (input.ratios || []).map(ratio => ({ratio, size: ratioSize(ratio)})).filter((row): row is {ratio: string; size: {width: number; height: number}} => !!row.size)
  if (!wanted.length) throw Object.assign(Error('没有可出的比例'), {status: 400})

  fs.mkdirSync(input.outDir, {recursive: true})
  const stamp = Date.now()
  const out: ComposedFile[] = []
  let browserRef = await browser()

  try {
    for (const {ratio, size} of wanted) {
      // ego 截完一张 2160×3840 大图后进程偶尔会直接退出（实测 2026-11-17）：
      // newPage 抛「browser has been closed」时重起浏览器把剩余比例跑完，不让一张崩掉整套。
      let page
      for (let attempt = 0; ; attempt++) {
        if (!browserRef.isConnected()) { shared = null; browserRef = await browser() }
        try { page = await browserRef.newPage(); break }
        catch (cause) {
          if (attempt >= 1) throw cause
          shared = null
          browserRef = await browser()
        }
      }
      try {
        await page.setViewportSize(size)
        await page.setContent(html(input, size, dataUrl), {waitUntil: 'load', timeout: 30000})
        await page.evaluate(() => document.fonts.ready)
        await page.waitForTimeout(120)
        const key = ratio.replace(':', 'x')
        const file = `composed-${stamp}-${key}-${crypto.randomBytes(2).toString('hex')}.jpg`
        const target = path.join(input.outDir, file)
        await page.screenshot({path: target, type: 'jpeg', quality: 92, clip: {x: 0, y: 0, width: size.width, height: size.height}})
        out.push({file, path: target, ratio, width: size.width, height: size.height, bytes: fs.statSync(target).size})
      } finally {
        await page.close().catch(() => {})
      }
    }
  } finally {
    await release(browserRef)
    setTimeout(() => {
      if (shared && Date.now() - shared.at > 85_000) {
        const closing = shared.browser
        shared = null
        void closing.close().catch(() => {})
      }
    }, 90_000).unref?.()
  }

  return out
}
