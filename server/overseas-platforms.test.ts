import test from 'node:test'
import assert from 'node:assert/strict'

// 只测纯配置/算法层，不碰文件系统和浏览器。
const {
  DEFAULT_PROFILES, RATIO_SIZES, readProfiles, profileFor, ratiosFor,
  pickCoverFor, composeCaption, normalizeRatio, ratioSize,
} = await import('./cover-platforms.ts')

const OVERSEAS = ['tiktok', 'youtube', 'instagram', 'x']

test('海外四平台都在默认档案里，且各有自己的封面比例', () => {
  const profiles = readProfiles(undefined)
  for (const id of OVERSEAS) {
    const p = profileFor(profiles, id)
    assert.ok(p, `${id} 必须出现在平台档案里`)
    assert.ok(ratioSize(p!.coverRatio), `${id} 的首选比例 ${p!.coverRatio} 必须能出图`)
  }
  const ratioOf = (id: string) => profileFor(profiles, id)!.coverRatio
  assert.equal(ratioOf('tiktok'), '9:16')
  assert.equal(ratioOf('youtube'), '16:9')
  assert.equal(ratioOf('instagram'), '4:5') // 图文帖占屏最高
  assert.equal(ratioOf('x'), '16:9')
})

test('Instagram 的 4:5 与 1:1 都能归一化并拿到出图像素', () => {
  assert.equal(normalizeRatio('4:5'), '4:5')
  assert.equal(normalizeRatio('1080x1350'), '4:5')
  assert.deepEqual(RATIO_SIZES['4:5'], {width: 2160, height: 2700})
  assert.ok(ratioSize('4:5')!.height > ratioSize('4:5')!.width)
})

test('选海外账号时，按比例套版会覆盖到每个平台的尺寸', () => {
  const profiles = readProfiles(undefined)
  const ratios = ratiosFor(profiles, ['tiktok', 'youtube', 'instagram', 'x'])
  const got = ratios.map(r => r.ratio).sort()
  assert.deepEqual(got, ['16:9', '4:5', '9:16']) // 四平台去重成三种尺寸
  const yt = ratios.find(r => r.platforms.includes('YouTube'))
  assert.ok(yt && yt.ratio === '16:9')
})

test('建任务时按平台档案自动配封面：首选比例优先，缺图不静默拿错图', () => {
  const profiles = readProfiles(undefined)
  const covers = {
    '9:16': 'tt-cover.jpg', '16:9': 'yt-cover.jpg', '4:5': 'ig-cover.jpg',
  }
  assert.equal(pickCoverFor(profileFor(profiles, 'tiktok')!, covers)!.file, 'tt-cover.jpg')
  assert.equal(pickCoverFor(profileFor(profiles, 'youtube')!, covers)!.file, 'yt-cover.jpg')
  assert.equal(pickCoverFor(profileFor(profiles, 'instagram')!, covers)!.file, 'ig-cover.jpg')
  // X 首选 16:9 有图
  assert.equal(pickCoverFor(profileFor(profiles, 'x')!, covers)!.file, 'yt-cover.jpg')
  // 只有 9:16 时，Instagram 退到 alt 的 9:16 并标记非精确
  const igAlt = pickCoverFor(profileFor(profiles, 'instagram')!, {'9:16': 'tt-cover.jpg'})!
  assert.equal(igAlt.file, 'tt-cover.jpg')
  assert.equal(igAlt.exact, false)
  // 一张图都没有时返回 undefined，界面据此提示手动选，不撞平台裁剪框
  assert.equal(pickCoverFor(profileFor(profiles, 'x')!, {}), undefined)
})

test('海外平台文案按各自规则拼：X 走正文 280 字，TikTok 话题进正文', () => {
  const profiles = readProfiles(undefined)
  const x = composeCaption(profileFor(profiles, 'x')!, {
    title: ' Viral clip ', tags: ['shorts', 'viral'], desc: 'Watch till the end',
  })
  assert.equal(x.titleField, 'Viral clip')
  assert.match(x.bodyField, /#shorts #viral/)
  assert.equal(x.truncated, false)

  const tt = composeCaption(profileFor(profiles, 'tiktok')!, {title: 'Epic', tags: ['fyp']})
  assert.equal(tt.bodyField, 'Epic\n\n#fyp')

  // 超长标题只截一次且给出 truncated，界面能提示用户
  const long = composeCaption(profileFor(profiles, 'x')!, {title: '字'.repeat(300)})
  assert.equal(long.truncated, true)
  assert.ok(long.titleField.length <= 280)
})
