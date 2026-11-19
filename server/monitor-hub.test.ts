import test from 'node:test'
import assert from 'node:assert/strict'
import {num, overviewOf, publishedAt, reviewOf, weeklyOf} from './monitor-hub.ts'

const dayAgo = (n: number) => new Date(Date.now() - n * 864e5).toISOString()

function work(id: string, views: number, age: number) {
  const base = dayAgo(age)
  const series = [0, 1, 3, 7].filter(d => d <= age).map(d => ({
    at: new Date(Date.parse(base) + d * 864e5).toISOString(),
    views: Math.round(views * [0, 0.5, 0.8, 1][[0, 1, 3, 7].indexOf(d)]),
    likes: Math.round(views * 0.02), comments: Math.round(views * 0.005),
    danmaku: null,
  }))
  return {
    id, accountId: 'a', platform: 'douyin' as const, url: `https://douyin.com/video/${id}`,
    title: `作品${id}`, metrics: {views, likes: Math.round(views * 0.02), comments: Math.round(views * 0.005), danmaku: null},
    comments: [], series, syncedAt: base,
  }
}

test('num 解析中文/英文数量单位，读不到返回 null', () => {
  assert.equal(num('1.2万'), 12000)
  assert.equal(num('6,017'), 6017)
  assert.equal(num('5.3K'), 5300)
  assert.equal(num('—'), null)
  assert.equal(num(null), null)
})

test('publishedAt 优先定时，其次首条快照，最后登记时间', () => {
  const w = work('1', 1000, 10)
  assert.ok(Math.abs(publishedAt(w, []) - Date.parse(w.series![0].at)) < 1000)
  const scheduledAt = dayAgo(12)
  const task = {id: 't', accountId: 'a', platform: 'douyin' as const, file: '', title: '', tags: [], autoSubmit: false,
    status: 'done' as const, step: '', url: w.url, scheduledAt, createdAt: '', updatedAt: ''}
  assert.equal(publishedAt(w, [task]), Date.parse(scheduledAt))
})

test('reviewOf 用同龄对比，上一期数据不足时不硬比', () => {
  const older = work('old', 5000, 20)
  const newer = work('new', 9000, 10)
  const r = reviewOf(newer, [newer, older], [], 7)
  assert.equal(r.current.views, 9000)
  assert.ok(r.peer)
  assert.equal(r.peer!.views, 5000)
  assert.match(r.text, /同龄对比/)
  // 只有一条作品时明确说没有同龄数据
  const alone = reviewOf(newer, [newer], [], 7)
  assert.equal(alone.peer, null)
  assert.match(alone.text, /还没有上一期/)
})

test('weeklyOf 汇总本周作品数与新增播放', () => {
  const works = [work('w1', 1000, 2), work('w2', 2000, 4)]
  const r = weeklyOf(works, [])
  assert.equal(r.published, 2)
  assert.ok(r.views > 0)
  assert.match(r.text, /周报/)
})

test('overviewOf 聚合总量、平台分布与待发布', () => {
  const w1 = work('a', 1000, 3), w2 = {...work('b', 2000, 2), platform: 'xhs' as const}
  const account = {id: 'a', platform: 'douyin' as const, alias: '号1', profile: '', port: 9341, createdAt: ''}
  const task = {id: 't', accountId: 'a', platform: 'douyin' as const, file: '', title: '', tags: [], autoSubmit: false,
    status: 'ready' as const, step: '', createdAt: '', updatedAt: ''}
  const o = overviewOf([w1, w2], [task], [account])
  assert.equal(o.totals.works, 2)
  assert.equal(o.totals.views, 3000)
  assert.equal(o.awaiting.length, 1)
  assert.equal(o.platforms.length, 2)
  assert.ok(o.latest)
})
