import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// 临时用户数据目录，不碰真实 .dev；模块加载会挂调度器（unref，测试结束即退）。
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mx-prov-'))
process.env.EMOTIONCUT_USER_DATA = tmp
fs.mkdirSync(path.join(tmp, 'creator', 'matrix', 'tasks'), {recursive: true})

const {PROVIDERS, PLATFORMS, MATRIX_PLATFORMS} = await import('./creator-matrix.ts')
const {stopMatrixScheduler} = await import('./creator-matrix.ts')

test('PROVIDERS 覆盖全部 12 个平台，一个不少', () => {
  for (const id of MATRIX_PLATFORMS) {
    const provider = PROVIDERS[id]
    assert.ok(provider, `缺平台 Provider: ${id}`)
    assert.equal(provider.name, PLATFORMS[id].name, `${id} 的显示名要与 PLATFORMS 一致`)
  }
})

test('自动填表能力收拢：douyin/channels/xhs 有 fill，其余只打开发布页', () => {
  const autoFill = MATRIX_PLATFORMS.filter(id => typeof PROVIDERS[id].fill === 'function')
  assert.deepEqual([...autoFill].sort(), ['channels', 'douyin', 'xhs'])
  // PLATFORMS.upload 标记与 Provider 能力必须一致，两处真相不允许打架
  for (const id of MATRIX_PLATFORMS) {
    const canFill = typeof PROVIDERS[id].fill === 'function'
    assert.equal(PLATFORMS[id].upload, canFill, `${id}: upload 标记与 fill 能力不一致`)
  }
})

test('身份识别脚本跟着 Provider 走：三家自动填表平台都有 identity', () => {
  for (const id of ['douyin', 'channels', 'xhs'] as const) {
    assert.equal(typeof PROVIDERS[id].identity, 'function', `${id} 缺 identity`)
  }
})

test('数据监听 wire：抖音有，其余平台没有也不炸', () => {
  assert.equal(typeof PROVIDERS.douyin.wire, 'function')
  for (const id of MATRIX_PLATFORMS) {
    if (id === 'douyin') continue
    // 没有 wire 的平台在 syncWork 里是可选调用，注册表里缺省即可
    assert.equal(PROVIDERS[id].wire, undefined, `${id} 不应有 wire`)
  }
})

test.after?.(() => stopMatrixScheduler())
