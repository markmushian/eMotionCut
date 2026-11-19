import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// 临时用户数据目录，不碰真实 .dev。
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mx-sched-'))
process.env.EMOTIONCUT_USER_DATA = tmp
const root = path.join(tmp, 'creator', 'matrix')
fs.mkdirSync(path.join(root, 'tasks'), {recursive: true})

const {
  createTasks, listTasks, runQueue, tickScheduler,
  stopMatrixScheduler, listArchive, removeArchive,
} = await import('./creator-matrix.ts')

const video = path.join(tmp, 'video.mp4')
fs.writeFileSync(video, 'fake')

function seedAccount(platform = 'douyin', id = 'acc-1') {
  const file = path.join(root, 'accounts.json')
  const rows = [{id, platform, alias: '测试号', profile: path.join(tmp, 'profile'), port: 9341, createdAt: new Date().toISOString()}]
  fs.writeFileSync(file, JSON.stringify(rows))
}

function writeTask(patch: Record<string, unknown>) {
  const id = patch.id as string
  const file = path.join(root, 'tasks', id + '.json')
  fs.writeFileSync(file, JSON.stringify(patch))
  return patch
}

/** 等任务离开 running 到终态（runTask 异步执行，无窗口失败很快）。 */
async function waitUntilSettled(id: string, tries = 50) {
  for (let i = 0; i < tries; i++) {
    const t = listTasks().find(x => x.id === id)
    if (!t || t.status !== 'running') return t
    await new Promise(r => setTimeout(r, 100))
  }
  return listTasks().find(x => x.id === id)
}

test('调度器把到点的排期任务自动投入执行（跑不起来也会明确失败，不卡排队）', async () => {
  seedAccount('douyin', 'acc-due')
  writeTask({
    id: 'due-1', accountId: 'acc-due', platform: 'douyin', file: video,
    title: '到点的排期', tags: [], autoSubmit: false,
    scheduledAt: new Date(Date.now() - 60_000).toISOString(),
    status: 'queued', step: '等待执行', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  })
  await tickScheduler()
  const task = await waitUntilSettled('due-1')
  assert.equal(task!.status, 'failed', '到点任务被调度器领取并执行（无窗口 → 明确失败）')
  assert.ok(task!.error && task!.error.length > 0)
})

test('调度器不碰未到点的排期任务', async () => {
  seedAccount('douyin', 'acc-future')
  writeTask({
    id: 'future-1', accountId: 'acc-future', platform: 'douyin', file: video,
    title: '还没到点', tags: [], autoSubmit: false,
    scheduledAt: new Date(Date.now() + 60 * 60_000).toISOString(),
    status: 'queued', step: '等待执行', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  })
  await tickScheduler()
  const task = listTasks().find(t => t.id === 'future-1')!
  assert.equal(task.status, 'queued', '未到点的任务保持排队')
})

test('超时补偿：running 超 30 分钟按检查点转失败，绝不自动重跑', async () => {
  seedAccount('douyin', 'acc-stuck')
  const old = new Date(Date.now() - 31 * 60_000).toISOString()
  writeTask({
    id: 'stuck-1', accountId: 'acc-stuck', platform: 'douyin', file: video,
    title: '卡死的任务', tags: [], autoSubmit: true, checkpoint: 'filled',
    status: 'running', step: '已填好', createdAt: old, updatedAt: old,
  })
  await tickScheduler()
  const task = listTasks().find(t => t.id === 'stuck-1')!
  assert.equal(task.status, 'failed')
  assert.match(task.error || '', /文案与封面已填完/)
  assert.match(task.error || '', /不会自动重发/)
})

test('超时补偿不碰新鲜的 running 任务', async () => {
  seedAccount('douyin', 'acc-fresh')
  writeTask({
    id: 'fresh-run', accountId: 'acc-fresh', platform: 'douyin', file: video,
    title: '正在跑', tags: [], autoSubmit: false,
    status: 'running', step: '打开 抖音 发布页', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  })
  await tickScheduler()
  const task = listTasks().find(t => t.id === 'fresh-run')!
  assert.equal(task.status, 'running', '30 分钟内的 running 不被误杀')
})

test('并发 runQueue 不重复领取同一任务（原子 claim）', async () => {
  seedAccount('douyin', 'acc-race')
  const [t1] = createTasks({accountIds: ['acc-race'], file: video, title: '并发竞态', autoSubmit: false})
  const [a, b] = await Promise.all([runQueue(3), runQueue(3)])
  const picked = [...a, ...b].filter(t => t && t.id === t1.id)
  assert.equal(picked.length, 1, '两条并发队列只有一条真正执行了这条任务')
  const task = await waitUntilSettled(t1.id)
  assert.notEqual(task!.status, 'queued', '任务已离开排队态')
})

test('发布文案存档：建任务留底、状态流转更新、任务清了存档还在', async () => {
  seedAccount('xhs', 'acc-archive')
  const [task] = createTasks({accountIds: ['acc-archive'], file: video, title: '存档验证', tags: ['测试'], autoSubmit: false})
  let rows = listArchive()
  assert.ok(rows.some(r => r.taskId === task.id && r.title === '存档验证'), '建任务即有计划文案快照')
  assert.equal(rows.find(r => r.taskId === task.id)!.status, 'queued', '建任务时的快照状态与任务一致')
  const file = path.join(root, 'tasks', task.id + '.json')
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'))
  raw.status = 'ready'
  fs.writeFileSync(file, JSON.stringify(raw))
  const {confirmTask} = await import('./creator-matrix.ts')
  const result = confirmTask(task.id, 'https://www.xiaohongshu.com/explore/abc123')
  rows = listArchive()
  const hit = rows.find(r => r.taskId === task.id)!
  assert.equal(hit.status, 'done')
  assert.equal(hit.url, 'https://www.xiaohongshu.com/explore/abc123')
  assert.ok(hit.publishedAt, '确认发布后补 publishedAt')
  fs.unlinkSync(file)
  assert.ok(listArchive().some(r => r.taskId === task.id), '任务清了，文案存档还在')
  const removed = removeArchive(hit.id)
  assert.equal(removed.removed, hit.id)
})

test.after?.(() => stopMatrixScheduler())
