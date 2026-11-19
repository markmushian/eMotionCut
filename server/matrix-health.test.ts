import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mx-health-'))
process.env.EMOTIONCUT_USER_DATA = tmp
const root = path.join(tmp, 'creator', 'matrix')
fs.mkdirSync(path.join(root, 'tasks'), {recursive: true})
fs.writeFileSync(path.join(root, 'accounts.json'), JSON.stringify([]))

const {createTasks, deleteTask, listTasks, purgeTasks, systemHealth} = await import('./creator-matrix.ts')
const {localHeadline} = await import('./cover-headline.ts')
const {overviewOf, saveFollowers} = await import('./monitor-hub.ts')

const video = path.join(tmp, 'video.mp4')
fs.writeFileSync(video, 'fake')

const day = (offset: number) => new Date(Date.now() - offset * 864e5).toISOString().slice(0, 10)

test('封面标题提炼：去掉流水词，压成短主标题 + 不重复的副标题', () => {
  const h = localHeadline({title: '第12集 | 她转身离开时才发完整版 HD', tags: ['短剧', '逆袭'], desc: '她转身离开时才发现问题'})
  assert.ok(h.main.length <= 10, `主标题太长：${h.main}`)
  assert.ok(!/第12集|完整版|HD/.test(h.main + h.sub))
  assert.equal(h.by, 'local')
})

test('封面标题提炼：重点词来自原文，不凭空造数字', () => {
  const h = localHeadline({title: '用 Codex 做封面，三步就够了', tags: ['AI', '效率']})
  assert.ok(h.keywords.length >= 1)
  assert.ok(h.main.includes(h.keywords[0]))
  assert.ok(!/\d{4,}/.test(h.main + h.sub))
})

test('任务记录可单条删除，也可批量清理已结束的', () => {
  fs.writeFileSync(path.join(root, 'accounts.json'), JSON.stringify([
    {id: 'a1', platform: 'douyin', alias: '号1', profile: path.join(tmp, 'p'), port: 9341, createdAt: new Date().toISOString()},
  ]))
  createTasks({accountIds: ['a1'], file: video, title: '任务A'})
  assert.equal(listTasks().length, 1)
  const id = listTasks()[0].id
  deleteTask(id)
  assert.equal(listTasks().length, 0)

  const rows = createTasks({accountIds: ['a1'], file: video, title: '任务B'})
  assert.equal(rows.length, 1)
  const task = listTasks()[0]
  fs.writeFileSync(path.join(root, 'tasks', `${task.id}.json`), JSON.stringify({...task, status: 'cancelled'}))
  createTasks({accountIds: ['a1'], file: video, title: '任务C'})
  assert.equal(listTasks().length, 2)
  const purged = purgeTasks(['cancelled'])
  assert.equal(purged.removed, 1)
  assert.equal(listTasks().length, 1)
  assert.equal(listTasks()[0].title, '任务C')
})

test('overview 提供今日/7天/30天涨粉与每日分平台净增', () => {
  saveFollowers('douyin', {
    total: 1000, todayNet: 12,
    history: [{day: day(1), net: 12}, {day: day(2), net: 8}, {day: day(8), net: 20}, {day: day(20), net: 30}],
  })
  const o = overviewOf([], [], [])
  assert.equal(o.fans.length, 1)
  assert.equal(o.fans[0].total, 1000)
  assert.equal(o.fans[0].today, 12)
  assert.equal(o.fans[0].gain7, 20)
  assert.ok(o.daily.length === 14)
  assert.ok(Object.keys(o.daily.at(-1)!.byPlatform).length > 0)
  assert.ok(o.insights.some(line => /涨粉|播放/.test(line)))
})

test('系统自检返回真实检查项，不虚构运行中', async () => {
  const h = await systemHealth()
  assert.ok(['ok', 'warn', 'error'].includes(h.status))
  assert.ok(h.checks.some(c => c.id === 'windows'))
  assert.ok(h.checks.some(c => c.id === 'codex'))
  assert.equal(typeof h.totals.tasks, 'number')
})
