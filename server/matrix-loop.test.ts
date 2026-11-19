import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// 在临时用户数据目录里跑，不碰真实 .dev。
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mx-loop-'))
process.env.EMOTIONCUT_USER_DATA = tmp
const root = path.join(tmp, 'creator', 'matrix')
fs.mkdirSync(root, {recursive: true})
fs.mkdirSync(path.join(root, 'tasks'), {recursive: true})

const {createTasks, confirmTask, listTasks, listAccounts, listWorks, trackWork} = await import('./creator-matrix.ts')

const video = path.join(tmp, 'video.mp4')
fs.writeFileSync(video, 'fake')

function seedAccount(platform = 'douyin', id = 'acc-1') {
  const file = path.join(root, 'accounts.json')
  const rows = [{id, platform, alias: '测试号', profile: path.join(tmp, 'profile'), port: 9341, createdAt: new Date().toISOString()}]
  fs.writeFileSync(file, JSON.stringify(rows))
  return rows
}

test('createTasks 拦截同一账号下同名未结束任务的重复建单', () => {
  seedAccount()
  createTasks({accountIds: ['acc-1'], file: video, title: '同一条视频'})
  assert.equal(listTasks().length, 1)
  assert.throws(
    () => createTasks({accountIds: ['acc-1'], file: video, title: '同一条视频'}),
    (err: any) => err.status === 409 && /已有未结束的任务/.test(err.message),
  )
  assert.equal(listTasks().length, 1, '防重后任务数不变')
})

test('confirmTask 标记完成并自动登记作品，重复回填不产生两条', () => {
  seedAccount()
  const task = listTasks().find(t => t.title === '同一条视频')!
  const link = 'https://www.douyin.com/video/1234567890'
  const result = confirmTask(task.id, link)
  assert.equal(result.task.status, 'done')
  assert.equal(result.task.url, link)
  assert.equal(result.work.url, link)
  assert.ok(listWorks().some(w => w.url === link))
  // 重复回填：作品按 url 去重，不新增
  const again = confirmTask(task.id, link)
  assert.equal(again.work.id, result.work.id)
  assert.equal(listWorks().filter(w => w.url === link).length, 1)
})

test('回填发布链接时把任务封面带进作品墙；重复登记补封面不覆盖已有封面', () => {
  seedAccount('tiktok', 'acc-cover')
  // 用真实建单流程造一个带封面的任务（封面文件要真实存在，校验才会放行）
  fs.mkdirSync(path.join(root, 'covers'), {recursive: true})
  fs.writeFileSync(path.join(root, 'covers', 'codex-cover.jpg'), 'fake-image')
  const [task] = createTasks({accountIds: ['acc-cover'], file: video, title: '海外封面作品', cover: 'codex-cover.jpg'})
  const link = 'https://www.tiktok.com/@someone/video/999'
  const result = confirmTask(task.id, link)
  assert.equal(result.work.cover, 'codex-cover.jpg', '回填后作品墙应拿到任务封面')
  // 手动再登记同一链接、带另一张封面：不覆盖已有封面
  const again = trackWork({accountId: 'acc-cover', url: link, title: '改过的标题', cover: 'other.jpg'})
  assert.equal(again.cover, 'codex-cover.jpg')
  assert.equal(again.title, '海外封面作品')
})

test('confirmTask 拒绝非 http 链接', () => {
  const task = listTasks()[0]
  assert.throws(() => confirmTask(task.id, 'javascript:alert(1)'), (err: any) => err.status === 400)
})

test.after(() => { try { fs.rmSync(tmp, {recursive: true, force: true}) } catch {} })
