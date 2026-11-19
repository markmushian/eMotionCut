import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import AdmZip from 'adm-zip'

const MAX_PACKAGE_BYTES = 640 * 1024 * 1024
const MAX_PUBLIC_JSON_BYTES = 2 * 1024 * 1024

type JsonObject = Record<string, unknown>

export type MotionPackageInfo = {
  path: string
  fileName: string
  bytes: number
  sha256: string
  format: 'motiongit-template'
  packageProfile: string
  specVersion: string
  protection: string
  template: {
    id?: string
    name?: string
    version?: string
    layout?: string
    width?: number
    height?: number
  }
  runtime: { profile?: string; operation?: string; assistantEntry?: string }
  delivery: { templateId?: number; releaseId?: number; templateVersion?: string }
}

function object(value: unknown, message: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error(message)
  return value as JsonObject
}

function text(entry: import('adm-zip').IZipEntry | null, label: string) {
  if (!entry || entry.isDirectory || entry.header.size > MAX_PUBLIC_JSON_BYTES) throw Error(`${label} 缺失或过大`)
  return entry.getData().toString('utf8')
}

function json(entry: import('adm-zip').IZipEntry | null, label: string) {
  try { return object(JSON.parse(text(entry, label)), `${label} 不是 JSON 对象`) }
  catch (error) { if (error instanceof SyntaxError) throw Error(`${label} JSON 损坏`); throw error }
}

function string(value: unknown) { return typeof value === 'string' ? value : undefined }
function number(value: unknown) { return typeof value === 'number' && Number.isFinite(value) ? value : undefined }

/**
 * Reads only the public envelope and guide. It never returns, extracts or logs personal/* or content/*.
 * Verification/execution remains the responsibility of the package's bundled assistant/motion.mjs entry.
 */
export function inspectMotionPackage(file: string): MotionPackageInfo {
  const absolute = path.resolve(file)
  if (path.extname(absolute).toLowerCase() !== '.motion') throw Error('请选择 .motion 模板包')
  const stat = fs.statSync(absolute)
  if (!stat.isFile() || stat.size < 22 || stat.size > MAX_PACKAGE_BYTES) throw Error('.motion 文件大小不合法')
  const zip = new AdmZip(absolute)
  const entries = zip.getEntries()
  if (entries.some(entry => entry.entryName.includes('\\') || entry.entryName.startsWith('/') || entry.entryName.split('/').includes('..'))) {
    throw Error('.motion 包含不安全路径')
  }
  const manifest = json(zip.getEntry('motion.json'), 'motion.json')
  if (manifest.format !== 'motiongit-template') throw Error('不是受支持的 MotionGit 模板')
  const profile = string(manifest.packageProfile)
  if (!profile) throw Error('.motion 缺少 packageProfile')
  const content = object(manifest.content, 'motion.json content 缺失')
  const runtime = object(manifest.runtime, 'motion.json runtime 缺失')
  const extensions = object(manifest.extensions, 'motion.json extensions 缺失')
  const assistant = object(extensions['motiongit.com:assistant-entry'], '缺少 MotionGit assistant entry')
  const delivery = object(extensions['motiongit.com:delivery-stage'], '缺少 MotionGit delivery metadata')
  const entry = string(assistant.entry)
  if (!entry || entry !== 'assistant/motion.mjs' || !zip.getEntry(entry)) throw Error('MotionGit 运行入口缺失')

  // guide.json is explicitly public metadata. Do not inspect protected release.bin.
  const guidePath = string(assistant.guide) || 'assistant/guide.json'
  const guide = json(zip.getEntry(guidePath), guidePath)
  const template = object(guide.template, 'assistant guide 缺少 template')
  return {
    path: absolute,
    fileName: path.basename(absolute),
    bytes: stat.size,
    sha256: crypto.createHash('sha256').update(fs.readFileSync(absolute)).digest('hex'),
    format: 'motiongit-template',
    packageProfile: profile,
    specVersion: string(manifest.specVersion) || '',
    protection: string(content.protection) || 'unknown',
    template: {
      id: string(template.id), name: string(template.name), version: string(template.version),
      layout: string(template.layout), width: number(template.width), height: number(template.height),
    },
    runtime: { profile: string(runtime.profile), operation: string(runtime.operation), assistantEntry: entry },
    delivery: {
      templateId: number(delivery.templateId), releaseId: number(delivery.releaseId),
      templateVersion: string(delivery.templateVersion),
    },
  }
}

export function findMotionPackages(root: string) {
  const absolute = path.resolve(root)
  const found: MotionPackageInfo[] = []
  const failures: { path: string; error: string }[] = []
  const walk = (folder: string) => {
    for (const item of fs.readdirSync(folder, { withFileTypes: true })) {
      const full = path.join(folder, item.name)
      if (item.isDirectory()) walk(full)
      else if (item.isFile() && item.name.toLowerCase().endsWith('.motion')) {
        try { found.push(inspectMotionPackage(full)) }
        catch (error) { failures.push({ path: full, error: error instanceof Error ? error.message : String(error) }) }
      }
    }
  }
  walk(absolute)
  return { packages: found, failures }
}
