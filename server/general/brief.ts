/**
 * 需求单（work/BRIEF.md）和每一轮发给 Agent 的话：需求已定、不要提问，短到一眼读完。
 * 第一版一轮做完；成片没过 eMotionCut 的检查才改一轮；用户要改片再开一轮。
 * 轮与轮之间，eMotionCut 把用户插的话、联网答复带进下一轮。
 */
import path from 'node:path';
import type { GeneralMedia, GeneralProject } from '../../src/general/general-contract.ts';

export type StyleInfo = { id: string; name_zh: string; input_type: string; description_zh?: string };
/** 不用风格：不添加风格规则，按用户自己的描述生成影片。 */
export const FREE_STYLE: StyleInfo = { id: 'free', name_zh: '不用风格', input_type: 'any', description_zh: '不套风格，完全按你的要求做' };
const isFree = (id: string) => id === FREE_STYLE.id;
export type Phase = 'plan' | 'make' | 'polish' | 'revise';
export type QueuedNote = { text: string; reference?: { time: number; rect: { x: number; y: number; w: number; h: number }; image?: string } };

export const frameSize = (aspect: GeneralProject['settings']['aspect']) => aspect === '9:16' ? { width: 1080, height: 1920 } : { width: 1920, height: 1080 };
const lengthText = (length: GeneralProject['settings']['length']) => length === 'auto' ? '按口播、素材和模板原生时长定，不得默认截成 60 秒；完整口播可为 2–5 分钟' : `${length} 秒（上下不超过 10%）`;

export function buildBrief(project: GeneralProject, style: StyleInfo, kit: string, media: (GeneralMedia & { file: string; fps?: string })[]): string {
  const { width, height } = frameSize(project.settings.aspect);
  const footage = project.inputs.kind === 'footage';
  const free = isFree(style.id);
  const lines = [
    '# eMotionCut 通用剪辑 · 制片任务',
    '',
    '你替用户做一条片子。**自己做完，不要提问，没有人会回答。** 拿不准的按合理默认决定，写进 REPORT.md。',
    '',
    '| 项 | 值 |', '|---|---|',
    free
      ? '| 风格 | 不套风格库，完全按用户的要求设计 |'
      : `| 风格 | ${style.name_zh}：\`${path.join(kit, 'styles', style.id, 'STYLE.md')}\` 是起点，和用户的要求冲突时以用户为准 |`,
    `| 输入 | ${footage ? `用户的视频 ${media.length} 段（见下）` : '一段文案（见下），没有素材'} |`,
    `| 时长 | ${lengthText(project.settings.length)} |`,
    `| 画幅 | ${width}×${height}（${project.settings.aspect}），30 fps |`,
    '| 旁白 | 不要旁白 |',
    '| 屏幕文字 | 中文（品牌名、专有名词保持原文）；每屏停留 ≥ 字数 ÷ 4.5 + 1.5 秒 |',
    footage
      ? '| 声音 | 素材有声音（歌曲、现场声、对白）就以原声为主，不另配乐；没有声音再用 numpy 合成配乐和音效 |'
      : '| 声音 | 必须有覆盖完整口播稿的人声旁白，再配低于人声的背景音乐和必要音效；不得用静音、占位 WAV 或只有配乐冒充完成。音乐转场和重音要跟画面节点对齐 |',
    '',
    '## 声音怎么做（照做，不要自己发明）',
    '',
    `- **中文旁白**：\`python ${path.join(kit, 'audio', 'voice.py')} scripts/lines.json assets/voice --voice zh-CN-YunxiNeural --rate +4%\`。lines.json 写成 \`[{"id":"v01","text":"…","duration":2.8}]\`，\`duration\` 填你已经排好的槽位时长，脚本会自己补齐或轻微压缩，时间线不用改。音色按人物选（男声 YunxiNeural / YunyangNeural，女声 XiaoxiaoNeural / XiaoyiNeural），需要网络。`,
    '- **不要用 Windows 自带的语音合成**（PowerShell 里的 `System.Speech` / `SpeechSynthesizer`）。它是拼接式引擎，中文每句语调一样、尾音含糊，是片子显得「AI 配音」的头号原因。网络不通就等人声脚本能跑，或用 Kokoro，宁可晚一点。',
    `- **转场音效**：\`python ${path.join(kit, 'audio', 'sfx_soft.py')} assets/sfx\` 生成柔和的 whoosh。一个片子 3–8 个就够，按段放，不要每个分镜都放；音效峰值约 0.10，比人声低 6 dB 以上。自己合成音效时不要用一阶低通白噪声——那是嘶嘶声，不是风声。`,
    '',
  ];
  if (footage) {
    lines.push('## 素材（只读）', '');
    for (const m of media) lines.push(`- \`${m.file}\`（页面里 \`/_media/${path.basename(m.file)}\`）：${m.name}，${m.duration.toFixed(1)} 秒，${m.width}×${m.height}${m.fps ? `，约 ${m.fps} fps` : ''}`);
    lines.push('', '只用这些素材，不编造数字、引文和事实。', '');
  }
  if (project.inputs.text.trim()) lines.push('## 文案', '', ...project.inputs.text.trim().split('\n').map(l => `> ${l}`), '', footage ? '文案是补充说明或片中可用的文字。' : '这是片子要讲的内容：可以拆成多屏、精简措辞，每个意思都要在画面上传达到；不伪造截图、数据和来源。', '');
  if (project.inputs.note.trim()) lines.push('## 用户的要求（优先于一切）', '', ...project.inputs.note.trim().split('\n').map(l => `> ${l}`), '');
  lines.push('说明书：`' + path.join(kit, 'HF.md') + '`（片子的格式、eMotionCut 的规则和工具）。', '');
  return lines.join('\n');
}

// One turn makes the whole first cut as a HyperFrames project (read → design → write → check → look once); eMotionCut
// renders and mixes it afterwards. The slow, costly parts were extra turns, long required reading, forced polish and
// the agent rendering by itself (S先生 2026-10-25).
const MAKE = (_kit: string, styleId: string) => [
  '## 怎么做（一轮做完，目标 10 分钟内）',
  '',
  `1. \`film progress read-style\`：读 HF.md${isFree(styleId) ? '' : '和 STYLE.md'}，以及 HF.md 指定的两份格式规则。其他文档只在需要时查一小段。`,
  '2. `film progress read-input`：看清输入。素材用 `film probe`、`film look <素材>`；要卡节奏就自己用 numpy 分析音频。',
  '3. `film progress plan`：心里定好分镜和节拍，运行 `hfx plan <总秒数>` 一次看全切段数量和时长限制；先定每段起止，再写代码，不用写方案文件。',
  '4. `film progress code`：写 index.html、每个画面变化一个子页面（compositions/）、声音片段；素材、字体复制进工程。准备素材就切到 code，不要拖到写完；响度和真峰值由程序统一处理，不写调响度脚本。',
  '5. `film progress self-check`：`hfx check` 直到通过，再 `hfx look`，用 view_image 看一遍接触表，把用户的要求一条一条对照。没做到的改掉，改完再看一次；都做到了就结束，不要为打磨反复返工。',
  '6. 写 REPORT.md：用户的每条要求一行，写做到了没有、怎么做到的。**不要渲染成片**（eMotionCut 会渲染）。最后用中文给用户回两三句：做了什么、有什么已知问题（不讲代码和路径）。',
].join('\n');

const TASKS: Record<Phase, (kit: string, styleId: string) => string> = {
  plan: MAKE, // a session saved before the one-turn first cut
  make: MAKE,
  polish: () => [
    '## 这一轮：成片没过 eMotionCut 的检查',
    '',
    '只改下面写的问题，改完 `hfx check`、`hfx look` 复查，更新 REPORT.md，不用渲染。最后用中文回一两句话说改了什么。',
  ].join('\n'),
  revise: () => [
    '## 这一轮：用户要改片',
    '',
    '这是局部返修，不是重新制作。先打开现有 work，定位用户说的时间、画面、文字或 data-hf-id，直接在原文件上修改；不得重建页面、重写时间线、替换未被点名的素材，也不得改变总时长。用户框选了区域时，只改该时间点和框选区域相交的元素。人改过的片段（属性和位置）保留原样。改完只检查受影响画面，再运行 `hfx check`、`hfx look`，更新 REPORT.md，不用渲染。最后用中文回一两句话说明“改了哪一处、其他保持不变”。',
  ].join('\n'),
};

/** A framed region: seconds into the film, the rectangle in the video's own pixels (x, y from the top left). */
export function referenceText(ref: NonNullable<QueuedNote['reference']>) {
  const r = ref.rect;
  return `第 ${ref.time.toFixed(2)} 秒，画面 (${Math.round(r.x)}, ${Math.round(r.y)}, ${Math.round(r.w)}, ${Math.round(r.h)}) 区域（成片像素坐标：左上角起的 x、y 和宽、高）${ref.image ? '，截图见附图' : ''}`;
}

export function turnPrompt(input: {
  phase: Phase; kit: string; styleId: string; brief?: string; resumed?: boolean; review?: string; revision?: { version: number; text: string; reference?: QueuedNote['reference'] };
  queued?: QueuedNote[]; network?: { allowed: boolean; hosts: string[] };
  /** the project's 联网 switch is on: commands may reach the network this turn */
  networkOn?: boolean;
  /** the person's own edits of the version being revised (eMotionCut keeps them in the next version) */
  personEdits?: string[];
}): string {
  const parts: string[] = [];
  if (input.brief) parts.push(input.brief, '---', '');
  if (input.resumed) parts.push('上一轮被停止了，文件都还在。先看 `progress.json`、`film.json` 和已有文件，从停下的地方接着把这一轮做完。', '');
  // 联网 is for finding material the person did not give (S先生 2026-10-26)
  if (input.networkOn && !input.network) parts.push('用户在 eMotionCut 里打开了联网：这不是只给权限，首次制作时必须先实际检索与主题相关的事实依据和可用素材。把检索词、访问过的网址、选用/未选用原因写入 `SOURCES.md`；至少记录 3 个真实来源。可以上网找图片、图标、音效、配乐和数据，用户素材优先。只使用可以免费发布的素材（公有领域、CC0、CC BY），下载到 `assets/`，并在 REPORT.md 列出来源网址、作者和许可。找不到合规素材就明确写“未采用”，不得假装已经联网检索。局部返修若不需要新素材，不要重复搜索。', '');
  if (input.network) parts.push(input.network.allowed ? `用户允许联网（你申请的：${input.network.hosts.join('、')}）。这一轮命令可以联网，只访问需要的地址，用完就继续离线做。` : '用户没有允许联网。用离线办法完成，做不到的部分简化或去掉，并在 REPORT.md 里说明。', '');
  parts.push(TASKS[input.phase](input.kit, input.styleId));
  if (input.review) parts.push('', '### eMotionCut 的检查结果', '', input.review);
  if (input.revision) {
    const ref = input.revision.reference;
    parts.push('', `### 用户对第 ${input.revision.version} 版的意见`, '', `> ${input.revision.text.split('\n').join('\n> ')}`);
    if (ref) parts.push('', `用户指的是${referenceText(ref)}。`);
  }
  if (input.personEdits?.length) {
    parts.push('', '### 用户在编辑页手动改过的地方', '',
      '用户看到的成片里，下面这些已经是改过的样子。eMotionCut 会把它们自动带到新版，你不用写代码去实现。除非用户这次的意见就是要改这几处，否则不要改这些元素的文字，也不要删它们的 data-hf-id。', '',
      ...input.personEdits);
  }
  if (input.queued?.length) {
    parts.push('', '### 用户在你工作时说的话（这一轮一并处理；和需求单冲突时以用户为准）', '');
    for (const note of input.queued) {
      parts.push(`- ${note.text.replace(/\n/g, ' ')}`);
      if (note.reference) parts.push(`  （指的是${referenceText(note.reference)}）`);
    }
  }
  if (!input.networkOn) parts.push('', '需要联网就 `film need-network --reason … --host …`，然后立刻结束这一轮。');
  return parts.join('\n');
}
