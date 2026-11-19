import { test } from 'node:test'
import assert from 'node:assert/strict'
import { STAGES, buildStages, entryForPath, stageForPath, stageIndex } from '../src/shell/stages.ts'

test('every stage number is two digits and unique', () => {
  const numbers = STAGES.map(stage => stage.no)
  assert.equal(new Set(numbers).size, numbers.length)
  for (const no of numbers) assert.match(no, /^\d{2}$/)
})

test('entry numbers sit under their stage number', () => {
  for (const stage of STAGES) {
    const prefix = stage.no[0]
    for (const entry of stage.entries) {
      assert.equal(entry.no[0], prefix, `${entry.no} ${entry.label} should start with ${prefix}`)
    }
  }
})

test('routes land on the room that owns them', () => {
  assert.equal(stageForPath('/').id, 'desk')
  assert.equal(stageForPath('/pro/topics').id, 'source')
  assert.equal(stageForPath('/pro/reader').id, 'source')
  assert.equal(stageForPath('/studio').id, 'cut')
  assert.equal(stageForPath('/general').id, 'cut')
  assert.equal(stageForPath('/styles').id, 'cut')
  assert.equal(stageForPath('/pro/publish').id, 'publish')
  assert.equal(stageForPath('/pro/analytics').id, 'review')
})

test('a project page is not its entrance', () => {
  assert.equal(entryForPath('/general')?.no, '32')
  assert.equal(entryForPath('/general/projects/abc'), undefined)
  // the stage still covers the project page, so the rail keeps 剪辑 lit
  assert.equal(stageForPath('/general/projects/abc').id, 'cut')
})

test('the screen-recording build moves /studio into 拍录', () => {
  const screen = buildStages(true)
  assert.equal(stageForPath('/studio', screen).id, 'shoot')
  assert.equal(stageForPath('/screen-motion', screen).id, 'shoot')
  assert.equal(entryForPath('/studio', screen)?.no, '21')

  const cut = screen.find(stage => stage.id === 'cut')!
  assert.equal(cut.entries.some(entry => entry.no === '31'), false)
  assert.equal(cut.entries.find(entry => entry.no === '32')?.path, '/general')
})

test('the full build keeps /studio in 剪辑', () => {
  const full = buildStages(false)
  assert.equal(stageForPath('/studio', full).id, 'cut')
  assert.equal(entryForPath('/studio', full)?.no, '31')
})

test('unknown paths fall back to the first stage instead of throwing', () => {
  assert.equal(stageForPath('/nope').id, 'desk')
  assert.equal(entryForPath('/nope'), undefined)
})

test('stageIndex counts from the top of the rail', () => {
  assert.equal(stageIndex('/'), 0)
  assert.equal(stageIndex('/pro/analytics'), STAGES.length - 1)
})
