import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import AdmZip from 'adm-zip'
import { inspectMotionPackage } from './motion-package.ts'

function fixture(folder: string) {
  const file = path.join(folder, 'fixture.motion')
  const zip = new AdmZip()
  zip.addFile('assistant/motion.mjs', Buffer.from('// public helper'))
  zip.addFile('assistant/guide.json', Buffer.from(JSON.stringify({ template: { id: 'demo', name: '测试模板', version: '1.2.3', layout: 'portrait', width: 1080, height: 1920 } })))
  zip.addFile('content/release.bin', Buffer.from('protected'))
  zip.addFile('personal/motiongit.key.json', Buffer.from('{"secret":"must-not-leak"}'))
  zip.addFile('motion.json', Buffer.from(JSON.stringify({
    format: 'motiongit-template', packageProfile: 'personal-download-v1', specVersion: '1.0.0-rc.1',
    content: { path: 'content/release.bin', protection: 'protected' },
    runtime: { operation: 'motion.run', profile: 'motiongit-host/1' },
    extensions: {
      'motiongit.com:assistant-entry': { entry: 'assistant/motion.mjs', guide: 'assistant/guide.json' },
      'motiongit.com:delivery-stage': { templateId: 7, releaseId: 8, templateVersion: '1.2.3' },
    },
  })))
  zip.writeZip(file)
  return file
}

test('reads public MotionGit metadata without exposing personal envelope', t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'emotioncut-motion-'))
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }))
  const result = inspectMotionPackage(fixture(folder))
  assert.equal(result.template.name, '测试模板')
  assert.equal(result.template.layout, 'portrait')
  assert.equal(result.protection, 'protected')
  assert.equal(JSON.stringify(result).includes('must-not-leak'), false)
})

test('rejects a renamed non-motion file', t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'emotioncut-motion-'))
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }))
  const file = path.join(folder, 'fake.motion')
  fs.writeFileSync(file, 'not a zip package')
  assert.throws(() => inspectMotionPackage(file))
})
