import test from 'node:test'
import assert from 'node:assert/strict'
import {analyzeScript,buildScriptOutline,spokenUnits} from '../src/general/script-analysis.ts'

test('口播单位忽略标点并把英文单词作为一个单位',()=>{
  assert.equal(spokenUnits('你好，eMotionCut 2026！'),4)
})

test('固定片长给出短、匹配和超长状态',()=>{
  assert.equal(analyzeScript('你'.repeat(40),15).fit,'short')
  assert.equal(analyzeScript('你'.repeat(60),15).fit,'good')
  assert.equal(analyzeScript('你'.repeat(90),15).fit,'long')
})

test('本地规则指出 AI 腔但不修改原稿',()=>{
  const result=analyzeScript('在当今时代，让我们深入探讨这个至关重要的问题。感谢您的观看。','auto')
  assert.equal(result.fit,'auto')
  assert.ok(result.issues.some(issue=>issue.id==='ai-words'))
  assert.ok(result.issues.some(issue=>issue.id==='service'))
})

test('八段骨架保留主题并明确事实占位',()=>{
  const outline=buildScriptOutline('普通人怎么选择 AI 视频工具？')
  assert.match(outline,/普通人怎么选择 AI 视频工具/)
  assert.equal((outline.match(/^【/gm)??[]).length,8)
  assert.match(outline,/待补充/)
})
