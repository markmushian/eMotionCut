# -*- coding: utf-8 -*-
"""
AI 写稿 · aiwrite.py
=====================
把「八段骨架」真的填成一份能读的初稿。

它做的是**梳理条理 + 组织语言**，不替你想观点、不编事实。
任何地方需要你的真实经历 / 数据，它会用【待你补充：xxx】留空，逼你自己填。

支持任何 OpenAI 兼容接口：
  DeepSeek / 豆包(火山方舟) / 通义千问 / Kimi / 智谱 / 硅基流动 / OpenAI / 自建 …
设置存在 `工作区\\模型设置.json`，只在本机，不外传。

命令行自测：
  python aiwrite.py --title "用AI十分钟做完一周的活" --pillar ①
  python aiwrite.py --test          # 测连通性
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "工作区")
CFG_PATH = os.path.join(WORK, "模型设置.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PRESETS = [
    {"id": "siliconflow", "name": "硅基流动 ⭐新手推荐",
     "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3",
     "hint": "聚合平台：写稿用 DeepSeek-V3，看图把「视觉模型」填 Qwen/Qwen2.5-VL-72B-Instruct，一个 Key 全搞定。Key 在 cloud.siliconflow.cn →「API 密钥」新建，注册送额度"},
    {"id": "deepseek", "name": "DeepSeek 深度求索",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "hint": "写稿性价比最高，注册就有额度。Key 在 platform.deepseek.com →「API keys」创建。⚠️ DeepSeek 没有看图模型，要用封面分析请选「硅基流动」"},
    {"id": "dashscope", "name": "通义千问 · 阿里云",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
     "hint": "写稿用 qwen-plus；看图把「视觉模型」填 qwen-vl-max。Key 在阿里云百炼控制台（bailian.console.aliyun.com）→「API-KEY」"},
    {"id": "zhipu", "name": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus",
     "hint": "写稿用 glm-4-plus；看图把「视觉模型」填 glm-4v-plus。Key 在 bigmodel.cn →「API 密钥」"},
    {"id": "kimi", "name": "Kimi · 月之暗面",
     "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
     "hint": "长文友好。Key 在 platform.moonshot.cn →「API Key 管理」"},
    {"id": "ark", "name": "豆包 · 火山方舟",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "ep-2024xxxx-xxxxx",
     "hint": "模型名要填「推理接入点 ID」（ep- 开头，火山方舟控制台开通模型后生成），不是 doubao-xxx。Key 在 console.volcengine.com/ark"},
    {"id": "openai", "name": "OpenAI",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
     "hint": "gpt-4o 系列本身能看图（视觉模型留空即可）。国内需自备网络，Key 在 platform.openai.com"},
    {"id": "custom", "name": "自定义 / 本地 Ollama",
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b",
     "hint": "本地 Ollama：接口地址 http://127.0.0.1:11434/v1，Key 随便填。第三方中转站同理——填他们给的接口地址 + Key + 模型名"},
]

DEFAULT_CFG = {"preset": "siliconflow", "base_url": "https://api.siliconflow.cn/v1",
               "model": "deepseek-ai/DeepSeek-V3", "api_key": "", "temperature": 0.8}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.isfile(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:  # noqa: BLE001
            pass
    return cfg


def save_cfg(cfg):
    os.makedirs(WORK, exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def mask(key):
    k = (key or "").strip()
    if len(k) <= 8:
        return "*" * len(k)
    return k[:4] + "*" * 6 + k[-4:]


# ---------------------------------------------------------------- 提示词
SYSTEM = """你是「{account}」这个抖音账号的口播稿代笔。

【账号定位】用大白话教普通人了解 / 用上 AI。真人出镜，口播日更，每条约 3 分钟。
【受众】① 完全没接触过 AI 的小白 ② 接触过但没真正用起来的 ③ 想靠 AI 多赚点钱的普通人。
【你要做的】只做两件事：把结构理顺、把话说得像人话。**不替他想观点，不编事实。**

写作硬要求：
1. 长度 850～950 字（口播 3 分钟），一句话不超过 25 字，多用短句。
2. 结构走这八段（不要写段号，自然过渡）：
   ① 反常识开场（否定观众默认认知，一句抓住）
   ② 例证（观众身边真实存在的现象，让他觉得"对，我就是这样"）
   ③ 土味比喻（把道理具象化）
   ④ 本质 / 方法（讲透为什么，给两步以内、具体到"点哪里"的动作）
   ⑤ 抬杠反驳（预判观众心里的反驳，先说出来再回应）
   ⑥ 价值拔高（拔到通用能力 / 趋势）
   ⑦ 自我暴露（博主自己的糗事或踩坑）
   ⑧ 行动指令（今晚就能做的零门槛动作）+ 收尾引导互动
3. **凡是需要真实数据、真实经历、具体案例的地方，你一律不要编**，写成
   【待你补充：xxx】占位，让他自己填。
4. 禁用这些 AI 腔：让我们 / 接下来 / 总而言之 / 综上所述 / 赋能 / 至关重要 /
   深入探讨 / 不仅是…更是 / 在这个时代 / 首先其次最后。
5. 语感平稳理性，少网感梗，不贩卖焦虑。判断句要经得起评论区抬杠。

【输出格式】只输出口播正文，不要标题、不要 markdown 符号、不要开场白和结束语。
段落之间空一行，方便他对着念。"""

USER = """请按上面的要求，为下面这个选题写一版口播初稿。

选题：{title}
栏目：{pillar}
{slogan}"""

# ------------------------------------------------- 改写模式（借结构做原创）
# 方法论来自 GitHub：tuihou123321/short-video-remix-interviewer
#   「保留选题 / 前 5 秒 / 爆款结构，用你自己的素材替换对方的案例和数据」
REMIX_SYSTEM = """你是「{account}」这个抖音账号的口播稿代笔，现在做的是**改写**，不是重写。

【账号定位】用大白话教普通人了解 / 用上 AI。真人出镜，口播日更，每条约 3 分钟。
【受众】① 完全没接触过 AI 的小白 ② 接触过但没真正用起来的 ③ 想靠 AI 多赚点钱的普通人。

你面前是别人已经验证过的爆款参考稿（可能是一份对标拆解报告，也可能是一段原文）。
你的任务是：**留着它"为什么能让人看下去"的骨架，把肉换成我的。**

必须保留的三个锚点：
1. 选题不变 —— 参考稿讨论的核心矛盾、目标问题、观看承诺，一个字都别换题。
2. 开场前 5 秒保持同一个钩子 —— 同一种抓人方式（反常识 / 结果前置 / 戳痛点），但用我的话重说。
3. 爆款结构不变 —— 段落的功能、顺序、注意力推进照旧（比如：钩子 → 证据 → 常见误区 → 原因 → 案例 → 方法 → 回扣）。结构不变，句子全部重写。

特例 —— 参考稿本身没干货时（榜单播报 / 资讯罗列 / 纯情绪）：只借它的**选题和钩子**，
不要模仿它的空泛。正文按下面第 6、7 条自己补足可操作干货，把它"值得拆的数据"变成
"观众能带走的做法"，这类稿子反而最容易做出差异。

必须全部删掉、换成我的（做不到就留空给我填）：
- 对方的个人经历、客户案例、成绩、播放量、收入、身份背书；
- 对方的产品、课程、社群、行动号召；
- 对方的口头禅、固定句尾、大段原句。

写作硬要求：
1. 长度 850～950 字（口播 3 分钟），一句话不超过 25 字，多用短句，口语，能照着念。
2. 凡是需要**真实数据、真实经历、具体案例**的地方，**一律不要编**，写成
   【待你补充：xxx】占位（说清楚要他补什么，比如"你第一次用这个工具踩的坑"）。
3. 禁用这些 AI 腔：让我们 / 接下来 / 总而言之 / 综上所述 / 赋能 / 至关重要 /
   深入探讨 / 不仅是…更是 / 在这个时代 / 首先其次最后。
4. 语感平稳理性，少网感梗，不贩卖焦虑。判断句要经得起评论区抬杠。
5. 不讲"必爆""保证涨粉"这种兑现不了的话。
6. **营养硬指标（最重要，违反即返工）**：说教、感慨、道理、价值观输出合计不得超过全篇 20%；
   至少 60% 篇幅必须是"可操作内容"——具体方法步骤、判断标准、能直接照做的动作，
   颗粒度到"观众明天就能照着做"。观众看完必须带走至少一个能上手的东西，
   光"转变观念"不算收获。
7. **至少给一个具体交付物**：具体工具名 / 命令 / 网址 / 步骤清单 / 量化标准（如"十分钟内能试出行不行"）。
   参考稿没有的就按常识给"通用可执行做法"，并标明（通用做法，按你的场景替换细节）；
   真实经历和真实数据仍然只用【待你补充】，不许编。

【输出格式】
第一部分「口播正文」：只输出能直接念的稿，不要 markdown 符号、不要开场白、不要"改写说明"。
段落之间空一行，方便他对着念。
最后单独一行输出「【标题候选】」打头的标题区：共 6 个标题，第 1 个标"推荐"，其余覆盖不同机制（数字具体 / 反差认知 / 结果前置 / 悬念提问 / 防坑提醒），每个一行、不超过 20 字、口播友好不夸大，选题锚点不变。"""

REMIX_USER = """参考稿（别人已验证的爆款，保留结构、换掉内容）：

{body}

—— 我的选题：{title}
—— 栏目：{pillar}
—— 额外要求：{extra}

请按上面的规矩改写成**我自己的**口播稿。"""

TOPIC_AND_VALUE_CONTRACT = r"""

【选题锁定与播放价值合同｜优先级最高】
这是一篇以播放完成率、收藏率、转粉率为目标的 2～5 分钟口播稿，不是泛观点作文。
1. 标题里的专有名词、产品名、版本名和核心事件就是主角。开头 80 字内必须完整说出，正文每一部分都要直接服务这个选题。禁止把“某模型发布”偷换成“别追新工具”“养成习惯”等泛鸡汤主题。
2. 如果选题是产品/模型发布，必须讲清：谁发布了什么、相对上一代/竞品最值得关注的 3 个变化、普通人具体能拿它做什么、一个能照做的实操演示、适用边界或当前限制。资料没有的事实必须写【待核验：具体问题】，不能编参数、日期或功能。
3. 至少 60% 篇幅是可执行干货：操作步骤、提示词、按钮路径、判断标准、前后对比或可复用模板。感慨、说教、反问、价值观合计不超过 15%。
4. 至少交付一个观众可直接复制的东西：完整提示词、三步操作清单、检查表或具体示例。不能只说“去试试”“多提问”。
5. 结构按内容选择，不再强套八段式：
   - 新模型/新工具：结果钩子 → 发布事实 → 核心变化 → 实操演示 → 谁适合/不适合 → 最小行动；
   - 教程：先展示结果 → 准备条件 → 逐步操作 → 常见错误 → 验收标准；
   - 观点：具体矛盾 → 证据 → 推理 → 可执行做法。
6. 时长 2～5 分钟，建议 700～1300 个汉字；正常语速能说完。信息不足时宁可短，并明确列待核验项，不能用比喻和情绪填满篇幅。
7. 写完自检：删掉任何与标题换成另一个产品后仍然成立的段落；如果换标题也成立，说明这段太泛，必须重写成当前选题的事实或操作。
"""


def build_remix_prompt(text, title="", pillar="①", extra="", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学",
                   "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    body = (text or "").strip()
    if len(body) > 8000:
        body = body[:8000] + "\n…（参考稿过长已截断）"
    return (REMIX_SYSTEM.format(account=account) + TOPIC_AND_VALUE_CONTRACT,
            REMIX_USER.format(body=body or "（没给参考稿，按选题直接写一版）",
                              title=title or "（沿用参考稿的选题）",
                              pillar=pillar_name,
                              extra=(extra or "").strip() or "无"))


# ================================================ 三个「加工」模式（P0）
# 方法论来自 PhilRobinluo/ai-coevolution-skills（CC BY-NC-SA 4.0）：
#   · oral     ← lls-douyin-oral-script-writer   （真人能说出口的口播稿）
#   · humanize ← lls-human-writing-editor         （去 AI 味 / 还原真人声音）
#   · titles   ← lls-headline-summary-maker       （标题 + 摘要，不夸大）
# 盲测打分（score）复刻自 cheat-on-content 的 rubric 盲评（自有方法论，可自由授权）。

ORAL_SYSTEM = """你是「{account}」这个抖音账号的口播稿编辑。现在把用户给的稿子**改成一张能直接对着镜头念的稿**。

【核心标准】① 开头让目标观众马上觉得"跟我有关"；② 全篇只留一个主张；③ 每隔几句就有新信息或新证据；④ 读起来像人在说，不像文章朗读；⑤ 结尾给与内容匹配的下一步；⑥ 案例数据一律来自原文，不许编。

【动手步骤】
1. 先在心里写一句核心主张：「我希望【某类人】看完相信/学会【一件事】」。一条只讲一个结论，多的删掉或标成"可拆成下一条"。
2. 选一个开头机制（在正文能兑现的前提下）：具体困境 / 反直觉判断 / 结果展示 / 场景代入 / 明确收益。**禁止**与正文无关的惊吓、伪造数字、"所有人都不知道"的空钩子。
3. 搭骨架：相关性钩子 → 一句主张 → 为什么会这样 → 一个可信证据 → 两到三步方法 → 回扣主张 → 最小行动。
4. 写成嘴能说的话：一句一个意思；多用具体动词名词；删掉"我们可以看到/众所周知"；专业词第一次出现用一句通俗解释；数字改成适合听的形式；**在自然呼吸点换行**；允许少量个人口头习惯，但不模仿网红口头禅。
5. 加**克制**的表演/镜头标记：【近景】【停顿 0.5 秒】【重音：xxx】【展示前后对比】—— 只放在影响理解或节奏的位置，别每句都指挥表情。
6. 时长按正常语速估（约 3 分钟 ≈ 850～950 字）；超时优先删重复和背景，不砍关键证据。

【不许做】制造虚假稀缺、收益或效果保证；复制他人口头禅；缺主张/缺证据时编一整篇。
【输出格式】
第一部分「创作说明」四行：目标观众 / 核心主张 / 可信材料 / 时长与语气。
第二部分「可直接拍摄稿」：按呼吸点换行，含少量表演标记。
第三部分「备选」：两个不同机制的开头 + 一个更克制的结尾。
最后单独一行输出「【标题候选】」打头的标题区：共 6 个标题，第 1 个标"推荐"，其余覆盖不同机制（数字具体 / 反差认知 / 结果前置 / 悬念提问 / 防坑提醒），每个一行、不超过 20 字、口播友好不夸大。
不要输出 markdown 井号标题，用中文小标题即可。"""

ORAL_USER = """我的稿子（可能还比较书面/松散，请按上面的标准改成能念的口播稿）：

{body}

—— 选题/标题：{title}
—— 栏目：{pillar}"""

HUMANIZE_SYSTEM = """你是「{account}」这个账号的真人化写作编辑。目标是**提高真实感、信息密度、可读性**，不是骗过 AI 检测器，也不靠加错别字/口头禅伪装。

【第一步 · 建事实账本】先锁定原文里的：人名、组织、日期、数字、因果与时间顺序、直接引语、作者明确观点、未核验内容、不能公开的隐私。**文风调整不授权改变事实强度**，改写后逐项对照。

【第二步 · 诊断模板腔】逐处标出（并说明"为什么妨碍理解 / 准备怎么处理"）：
开头大背景空转；"首先其次最后"却不需要枚举；"赋能/生态/价值/维度/意义"等抽象名词；连续三个同结构句；同一结论换词重复；没有主体的"得到提升/实现优化"；无证据的"显著/极大/全面/必然"；每段末尾都上升到宏大意义；伪引语、伪故事、不属于作者的情绪。

【第三步 · 按五层改】
① 事实与逻辑：修矛盾、补缺失前提、断因果跳跃、纠强度漂移；
② 段落任务：每段只做一个动作（交代/证明/解释/转折/请求/收束），删没新任务的段落；
③ 句子：恢复"谁做了什么、产生什么结果"，把抽象名词换成动作和可观察结果；
④ 节奏：按意义安排长短句与停顿（不是随机切短，而是在重要处留呼吸）；
⑤ 词语：最后才处理套话、冗词、弱动词、重复表达。

【第四步 · 加具体但不编造】具体性只能来自：原文已有的动作/时间/地点/物件、用户确认的真实案例、可核验公开数据、对流程的可观察描述。**缺细节就列问题**，写成【待你补充：这里需要一个真实瞬间——当时谁做了什么？你看到什么结果？】，绝不代替作者编。

【输出格式】
先给「事实账本对照」（列出锁定的关键事实/数字/引语，一行一条）。
再给「改后正文」（可直接念）。
最后给「改动说明 + 待你补充清单」（简短）。不要 markdown 井号。"""

HUMANIZE_USER = """使命：在不改变事实、立场和作者身份的前提下，把下面这篇改得更具体、自然、可朗读，保留个人声音。

{check_block}原稿：
{body}

—— 用途/受众：抖音口播，受众是想学 AI 的普通人
—— 选题：{title}
—— 栏目：{pillar}
—— 可以删改的范围：结构级（可重排段落，但事实不动）"""


# ------------------------------------------------ 「先想后写」思考指令（可开关）
# 让模型在动笔前先走一遍"审题 → 找矛盾 → 搭骨架 → 预判坑 → 标出不能编的地方"，
# 思考过程一并输出，用户能在弹窗里实时看到，而不是"几秒钟给一坨字"。
THINK_PREFIX = """

【先想后写 · 必须照做】
在给正文之前，你必须**先把思考过程写出来**，用下面这个固定格式，不许跳步、不许一句话敷衍：

【思考过程】
1. 这条到底讲给谁听、他现在卡在哪：
2. 这件事最反直觉 / 最值得讲的一个点是什么：
3. 结构怎么走（写出 4-6 步骨架，每步一句）：
4. 这个题最容易写砸的地方，以及我打算怎么避开：
5. 哪些地方是我的知识/经验覆盖不到的、必须留给作者自己填：
6. 自我检查：有没有编造数字、有没有"至关重要/赋能"这类 AI 腔、有没有兑现不了的承诺？

【正文】
（正文从这一行下面开始，只写正文本身）

再强调一遍：**先出「【思考过程】…」，再出「【正文】…」**，两块都要有。"""


def _hits_block(hits, hints=None):
    """把本地初筛命中的模式，整理成给模型看的「逐条整改清单」。

    hints = 提示级命中（泛模式、误报率高）：明确告诉模型**不要动它们**，
    否则模型会把这些正常表达也改掉，改完又触发新一轮检测，形成死循环。
    """
    if not hits:
        return ""
    lines = ["【本稿已被机器初筛命中的 AI 味模式 —— 只准改下面列出的这些，改完在末尾说明每条怎么改的】"]
    for h in hits[:12]:
        samples = "、".join([str(s) for s in (h.get("samples") or [])[:3]])
        lines.append("- %s %s：命中「%s」 → 建议：%s"
                     % (h.get("id", ""), h.get("name", ""), samples, h.get("advice", "")))
    if len(hits) > 12:
        lines.append("（还有 %d 条次要命中，一并顺带处理）" % (len(hits) - 12))
    if hints:
        hs = "、".join("%s(%s)" % (h.get("id", ""), h.get("name", "")) for h in hints[:8])
        lines.append("【重要纪律】以下模式也被初筛扫到，但属于正常表达 / 误报率高，**保持原样、一个字都不要动**：%s。"
                     "同时：你改写产出的新句子，不得引入上述清单之外的新模式（别用「不是A而是B」式假对比、"
                     "别堆四字排比、别加意义拔高），否则复检会越改越多。" % hs)
    lines.append("【输出要求】直接给「改后全文」，别输出对比表；改不动的命中（改了会伤原意）保留原文并在末尾说明。")
    return "\n".join(lines) + "\n\n"

TITLES_SYSTEM = """你是「{account}」这个抖音账号的标题与摘要助手。**标题不是补救空内容的包装**——只从已核验的正文里压缩出承诺，绝不凭空加数字、身份、结果或紧迫感。

【第一步 · 提取主张地图】只从源材料提：对象（真正讨论谁/什么）、问题（受众正面对什么）、新信息（提供了什么不同理解）、结果（看完能理解/完成什么）、证据（案例/数据/步骤）、限制（在什么条件下成立）。**每个标题都要能指回正文某段。**

【第二步 · 写一句核心承诺】格式：这份内容帮助【受众】在【场景】下理解/完成【具体结果】，依据是【证据或方法】。

【第三步 · 按机制出标题】每类出 2 个，不要写成换词游戏：
直给型 / 问题型 / 对比型 / 过程型 / 结果型（正文有证据才用）/ 对象型 / 反常识型（材料确实推翻常识才用）。
**禁用**：震惊、必看、颠覆、封神、所有人都。

【第四步 · 事实强度检查】逐词查："能"是否被写成"必然"；"一个案例"是否被写成"普遍规律"；"省一点时间"是否被写成"效率 10 倍"；"方法说明"是否被写成"官方结论"；"尝试"是否被写成"已经成功"；数字/时间/身份在正文中有没有来源。**删掉标题里无法由正文兑现的最强词。**

【第五步 · 摘要】三个长度各一条：30–50 字（列表/分享卡）、80–120 字（视频简介）、150–200 字（详细导语）。结构：背景/问题 → 核心内容 → 证据或方法 → 适合谁/下一步。摘要不得引入正文没有的新案例、数字或承诺。

【输出格式】
## 核心承诺
（一句）
## 标题候选（按机制分组，每类 2 个）
## 摘要（三档）
## 强度检查（列出被删掉的夸大词 + 原因）
不要用 markdown 井号，用中文小标题分节即可。"""

TITLES_USER = """源材料（已核验的正文/稿子，标题只能从这里面提，不许加料）：

{body}

—— 选题/现有标题：{title}
—— 栏目：{pillar}
—— 渠道：抖音（视频标题 + 简介）
—— 希望观众下一步：看完去评论区扣关键词/点关注
—— 禁止夸大：不许写"必爆/保证涨粉/月入过万"这类兑现不了的话"""


# ------------------------------------------------ 分镜模式（P1 · 复刻）
# 方法论灵感来自 PhilRobinluo/ai-coevolution-skills 的 lls-ai-video-script-planner
# （CC BY-NC-SA 4.0）。此处为独立重写，未复制其原文，仅沿用「任务书→骨架→节拍→分镜→素材→验收」的思路。
STORYBOARD_SYSTEM = """你是「{account}」这个抖音账号的**分镜导演**。现在把用户给的这版口播稿，变成一张**能直接开拍的施工图**。

【前提】面向抖音真人出镜、竖屏 9:16、约 3 分钟。**先用稿子锁定要讲的事和证据，再设计镜头**；绝不为了"高级感"硬加镜头，也不许编数据/案例。

【七步走】
1. **任务书**（8 行）：受众 / 观众现在怎么想 / 看完要变成怎么想 / 一句话主张 / 拿什么证据 / 时长·画幅·平台 / 制作方式（真人出镜+录屏+加字幕）/ 成功信号。
2. **选骨架**（哪一类最贴就选哪类，别所有都套"3 秒钩子+3 个技巧"）：
   · 教程：先看结果 → 前提条件 → 分步演示 → 常见错误 → 怎么算做对
   · 知识解释：抛一个具体问题 → 核心概念 → 一个类比 → 例子 → 怎么用
   · 案例复盘：目标 → 现场 → 关键决定 → 结果 → 能搬走的经验
   · 故事型：当时的处境 → 选择 → 阻力 → 转折 → 今天的原则
3. **节拍表**（表格）：| 节拍 | 时间 | 观众此刻知道什么 | 这条新信息 | 情绪/节奏 | 证据 |。**每一拍只推一个变化**；没有新信息、没有情绪变化、不是必要留白的，直接删。
4. **分镜表**（表格，核心）：| 镜号 | 秒数 | 景别/机位（如 近景·固定）| 画面主体+动作 | 旁白（照稿）| 屏幕字（字幕/标题条）| 素材来源 | 转场 | 备注 |。**镜头变化要跟着信息变化走**，不是随机切。
5. **人物连续性**：把出镜人的**服装、发型、道具、场景锚点、朝向**固定下来写成一行，避免前后不一。要生的图另给提示词 = 主体+动作+环境+景别+机位+光线+色彩+运动+时长+画幅（默认 9:16），工具不支持的项标"后期处理"。
6. **素材清单**（分组）：必拍 / 可生成 / 已有 / 需授权 / 可替代 / 后期。每项注明格式与来源。
7. **验收**：① 旁白按正常语速计时，总时长对得上（约 3 分钟）；② **静音看画面能不能看懂主线**；③ 关键证据看得清；④ 没有画面时旁白是否仍成立。

【缺素材怎么办】没画面就优先设计**能拍的**：桌面演示、录屏、图表、纯字幕卡。别硬凑华丽镜头。
【输出格式】用中文小标题分七节，表格用 markdown 表格。不要输出井号标题、不要开场白和结束语。"""

STORYBOARD_USER = """这是我要拍的口播稿，请按上面的七步给我一张能开拍的分镜施工图：

{body}

—— 选题/标题：{title}
—— 栏目：{pillar}
—— 制作条件：一个人、一部手机、真人出镜 + 电脑录屏 + 后期加字幕，全离线完成
—— 时长：约 3 分钟，竖屏 9:16"""


def _clip(body, n=8000):
    b = (body or "").strip()
    if len(b) > n:
        b = b[:n] + "\n…（原文过长已截断）"
    return b


def build_oral_prompt(text, title="", pillar="①", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    return (ORAL_SYSTEM.format(account=account),
            ORAL_USER.format(body=_clip(text) or "（没给稿子）",
                             title=title or "（沿用稿子的主题）", pillar=pillar_name))


def build_humanize_prompt(text, title="", pillar="①", account="（我的 AI 账号）", hits=None,
                          think=False, hints=None):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    system = HUMANIZE_SYSTEM.format(account=account)
    if think:
        system += THINK_PREFIX
    return (system,
            HUMANIZE_USER.format(check_block=_hits_block(hits, hints),
                                 body=_clip(text) or "（没给稿子）",
                                 title=title or "（沿用原稿主题）", pillar=pillar_name))


def build_titles_prompt(text, title="", pillar="①", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    return (TITLES_SYSTEM.format(account=account),
            TITLES_USER.format(body=_clip(text) or "（没给正文，先补上稿子再起标题）",
                               title=title or "（无）", pillar=pillar_name))


def build_storyboard_prompt(text, title="", pillar="①", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    return (STORYBOARD_SYSTEM.format(account=account),
            STORYBOARD_USER.format(body=_clip(text) or "（没给稿子，先补一版口播稿）",
                                   title=title or "（沿用稿子主题）", pillar=pillar_name))


# ------------------------- 盲测打分（复刻 cheat-on-content 的 rubric 盲评） -------------------------
# 方法论来源：cheat-on-content 的 cheat-score / cheat-score-blind（隔离盲评 + 0-5 整数维度 +
# 按栏目加权公式 + composite）。这里把它重写成一次大模型调用：模型天然只看得到稿子和规则，
# 看不到任何播放数据 / 作者历史，等于内建了 blind 隔离。
SCORE_SYSTEM = """你是短视频口播稿的「盲测打分器」。你只能看到稿子本身和评分规则——看不到播放量、作者信息、历史成绩，这就是盲测：分数只反映稿子质量，不带后视镜。

【打分纪律】
1. 每个维度打 0-5 的**整数**，不许 4.5 这种小数；相信读完稿的第一直觉，不反复横跳。
2. 只看稿子本身打分，不脑补"作者肯定努力过""选题不容易"这类场外分。
3. 每个维度给一句 10-30 字理由，必须引用稿子里的具体词句，不许写"还行""不错"这种空话。

【第一步：按栏目选公式】
- ① 工具教学 / ③ 开源实战 → 用【工具实测公式】
- ② 知识 / 防坑科普 → 用【深度观点公式】
- 若稿子明显是"当日新闻大事点评"，即栏目标的是①②但内容是时事，改用【每日大事公式】并说明原因。

工具实测公式：composite = (RT×2.0 + TH×1.5 + SC×1.5 + HP×1.5 + QD + AC + VA×0.5) / 9.0 × 2.0
深度观点公式：composite = (CG×2.0 + HP×1.5 + QD×1.5 + DA + JD + NA + SI×0.5) / 8.5 × 2.0
每日大事公式：composite = (TL×1.5 + ID×1.5 + HP×1.5 + QD×1.5 + DA + YN + AC×0.5) / 8.5 × 2.0

【维度定义（0-5 整数）】
共享维度：
- HP 钩子强度（前 3 秒逼不逼人看下去）：0 通用开场/背景铺垫；3 具体承诺、反直觉断言或结果前置；5 认知纠偏/反常识归因/冲突直给——观众无法停止处理。注意：安静但具体的开场也可以 5 分，猎奇 ≠ 钩子。
- QD 人话度（去 AI 味六条自检）：①禁排比句 ②金句要像人话（忌工整对仗海报体）③必须有"我"的场景/经历/态度 ④允许口头语、自我打断、不完整句 ⑤能用人物事件场景开场就不用观点句开场 ⑥朗读测试，聊天不会那么说的全改。0 三处以上 AI 病；3 整体口语但有 1 处漏网；5 六条全过。**硬规则：触犯①或②任意一条 → QD 封顶 2**。
- DA 数据锚点：0 无数字或来源可疑；3 数字具体但未核来源；5 具体数字 + 可核来源 + 只做推演锚点。**发现编造数字 → DA 记 0 并在结论里标红警告**。

深度观点专属：CG 认知增量（改"怎么想"而非"发生了什么"，权重最高）；JD 杠点免疫（预判评论区异议并回应）；NA 推演递进（反直觉开场→本质推演→数据锚→意味着什么→带走判断）；SI 自我暴露（真事；发现编造记 0 并标 warning）。
工具实测专属：RT 实测真实性（真跑过+讲翻车点，权重最高；念参数表 = 弃稿 0 分）；TH 门槛判断（免费吗？要代码吗？几分钟出结果？）；SC 场景具体（具体人群的具体痛活）；VA 商单潜力。
每日大事专属：TL 时效（48h 内新；超 72h 扣到 0-1）；ID 信息差（别人报事件我讲门道）；YN 与你有关（落到钱/工作/生活）；AC 行动价值。

【输出格式】严格按下面结构，不要 markdown 井号：
栏目判定：用了哪个公式、一句话为什么
维度打分：（每行）维度名 分数 — 理由
综合分：composite = 代入计算过程 = X.X / 10
最弱两维怎么改：具体到句子级的改法，能直接照着改的那种
结论：8.0 以上 → 直接进提词拍摄；6.5-7.9 → 先改最弱维再拍；6.5 以下 → 建议重写或换角度。有编造/弃稿级问题 → 一票否决并说明。"""

SCORE_USER = """盲测打分下面这篇稿子。

—— 标题：{title}
—— 栏目：{pillar}
—— 账号：{account}（大白话教普通人了解/用上 AI；受众：没接触过的小白 / 接触过没用起来的 / 想靠 AI 赚米的）

稿子：
{body}"""


def build_score_prompt(text, title="", pillar="①", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    return (SCORE_SYSTEM,
            SCORE_USER.format(body=_clip(text) or "（没给稿子）",
                              title=title or "（沿用稿子主题）",
                              pillar=pillar_name, account=account))


def build_prompt(title, pillar="①", extra="", account="（我的 AI 账号）", slogan=""):
    pillar_name = {"①": "① AI 工具上手教学",
                   "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    ex = ""
    if extra and extra.strip():
        head = extra.strip()
        if len(head) > 6000:
            head = head[:6000] + "\n…（已截断）"
        ex = "参考素材（我的想法 / 对标拆解 / 资料，请吸收它的信息，但用我的口吻重写）：\n" + head + "\n"
    return (SYSTEM.format(account=account) + TOPIC_AND_VALUE_CONTRACT,
            USER.format(title=title or "未命名选题", pillar=pillar_name, slogan=ex))


# ---------------------------------------------------------------- 调用
def chat(cfg, system, user, timeout=180, json_mode=False, max_tokens=None):
    bridge = os.environ.get("EMOTIONCUT_AGENT_BRIDGE", "http://127.0.0.1:3119/api/creator/agent")
    req = urllib.request.Request(bridge, data=json.dumps({"system": system, "user": user}).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        payload = json.loads(OPENER.open(req, timeout=max(timeout, 300)).read().decode("utf-8", "ignore"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error") or "Agent 没有完成任务")
        return str((payload.get("result") or {}).get("text") or "").strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        try: detail = json.loads(detail).get("error") or detail
        except Exception: pass
        raise RuntimeError("Agent 调用失败：%s" % detail)
    except urllib.error.URLError as exc:
        raise RuntimeError("连不上 eMotionCut Agent：%s" % exc.reason)

    # 旧 API Key 通道保留作为迁移参考，正常路径不再进入。
    base = (cfg.get("base_url") or "").rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()
    if not base or not model:
        raise RuntimeError("还没配模型。去「账号定位」页的「模型设置」配好。")
    if "11434" not in base and not key:
        raise RuntimeError("还没填 API Key。去「文案」页右上角点「模型设置」。")
    url = base + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": float(cfg.get("temperature") or 0.8),
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % (key or "ollama")})
    try:
        raw = OPENER.open(req, timeout=timeout).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError("接口报 %s：%s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise RuntimeError("连不上接口：%s（检查 Base URL 和网络）" % exc.reason)
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        raise RuntimeError("接口返回的不是 JSON：%s" % raw[:200])
    if d.get("error"):
        raise RuntimeError("接口返回错误：%s" % json.dumps(d["error"], ensure_ascii=False)[:300])
    try:
        return d["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        raise RuntimeError("返回结构看不懂：%s" % raw[:300])


def chat_stream(cfg, system, user, timeout=300):
    """流式版本：逐块 yield 文本增量（OpenAI 兼容的 SSE）。"""
    yield chat(cfg, system, user, timeout=timeout)
    return
    base = (cfg.get("base_url") or "").rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()
    if not base or not model:
        raise RuntimeError("还没配模型。去「账号定位」页的「模型设置」配好。")
    if "11434" not in base and not key:
        raise RuntimeError("还没填 API Key。去「文案」页右上角点「模型设置」。")
    url = base + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": float(cfg.get("temperature") or 0.8),
        "stream": True,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % (key or "ollama")})
    try:
        resp = OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError("接口报 %s：%s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise RuntimeError("连不上接口：%s（检查 Base URL 和网络）" % exc.reason)
    for raw in resp:
        line = raw.decode("utf-8", "ignore").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            d = json.loads(data)
        except Exception:  # noqa: BLE001
            continue
        if d.get("error"):
            raise RuntimeError("接口返回错误：%s"
                               % json.dumps(d["error"], ensure_ascii=False)[:300])
        ch = (d.get("choices") or [{}])[0]
        delta = (ch.get("delta") or {}).get("content")
        if delta:
            yield delta


def split_think(text):
    """把模型的输出拆成 (思考过程, 正文)。没有思考段时正文=全文、思考=''。"""
    t = text or ""
    m = re.search(r"【正文】", t)
    if not m:
        return "", t
    head = t[:m.start()]
    body = t[m.end():]
    head = re.sub(r"^\s*【思考过程】\s*", "", head).strip()
    return head, body.lstrip("\n").strip()


def _clean(text):
    """去掉模型偶尔带出来的 markdown 井号，其余原样保留。"""
    lines = []
    for ln in (text or "").splitlines():
        if ln.strip().startswith("#"):
            ln = ln.lstrip("# ").strip()
        lines.append(ln)
    return "\n".join(lines).strip()


def write(title, pillar="①", extra="", account="（我的 AI 账号）", cfg=None):
    cfg = cfg or load_cfg()
    system, user = build_prompt(title, pillar, extra, account)
    return _clean(chat(cfg, system, user))


def rewrite(text, title="", pillar="①", extra="", account="（我的 AI 账号）", cfg=None):
    """借爆款结构做原创：保留选题/前5秒/结构，换掉对方素材（见 REMIX_SYSTEM）。"""
    cfg = cfg or load_cfg()
    system, user = build_remix_prompt(text, title, pillar, extra, account)
    return _clean(chat(cfg, system, user))


# ------------------------- 三个加工动作（P0）+ 统一入口 -------------------------
def oral(text, title="", pillar="①", account="（我的 AI 账号）", cfg=None):
    """口播化：把书面/松散稿改成能对着镜头念的口播稿。"""
    cfg = cfg or load_cfg()
    system, user = build_oral_prompt(text, title, pillar, account)
    return _clean(chat(cfg, system, user))


def humanize(text, title="", pillar="①", account="（我的 AI 账号）", cfg=None):
    """去 AI 味：事实账本锁定 + 五层编辑，还原真人声音。"""
    cfg = cfg or load_cfg()
    system, user = build_humanize_prompt(text, title, pillar, account)
    return _clean(chat(cfg, system, user))


def titles(text, title="", pillar="①", account="（我的 AI 账号）", cfg=None):
    """起标题：多机制标题候选 + 三档摘要 + 事实强度检查。"""
    cfg = cfg or load_cfg()
    system, user = build_titles_prompt(text, title, pillar, account)
    return _clean(chat(cfg, system, user))


def storyboard(text, title="", pillar="①", account="（我的 AI 账号）", cfg=None):
    """分镜：把口播稿变成任务书 + 节拍表 + 分镜表 + 素材清单 + 验收。"""
    cfg = cfg or load_cfg()
    system, user = build_storyboard_prompt(text, title, pillar, account)
    return _clean(chat(cfg, system, user))


def score(text, title="", pillar="①", account="（我的 AI 账号）", cfg=None):
    """盲测打分：cheat-oncontent rubric 盲评，7 维 0-5 整数 + composite。"""
    cfg = cfg or load_cfg()
    system, user = build_score_prompt(text, title, pillar, account)
    return _clean(chat(cfg, system, user))


# ================================================ Pongfi 调研三模式（MIT 方法论复刻）
# 方法论来自 s15039733700-cpu/Pongfi-Skills（MIT 许可，可自由整合）：
#   · research ← 机会评分 + 证据置信度 + 证据驱动选题（idea-generation）
#   · audit    ← account-audit（账号内容结构、节奏与表现）
#   · redraft  ← 爆款结构提炼 + 人设化重写（viral-breakdown → 自己的初稿）

RESEARCH_SYSTEM = """你是「{account}」的选题调研分析师。你拿到的是刚从抖音搜回来的真实数据统计（不是你的想象）。
任务：把数据转成一份**可以直接开工的候选选题清单**。

【纪律】
1. 只依据给出的统计数据和账号定位说话。数据里没有的方向不许编。
2. 每个选题的「推荐理由」必须引用数据（出现频次 / 赞中位数对比 / 相对表现倍数），一条都不能空。
3. 机会分和置信度按给定组件诚实打，不许为了好看虚高。置信度低就写低，并说明缺什么证据。
4. 「合适的开头」要写成能直接念的一句话（10~25 字），不许写成"可以用一个吸引人的开头"这种废话。
5. 「潜在问题」要具体（比如"这个方向头部账号已经垄断，新号很难被看到"），不许写"要注意质量"。

【机会分（0-100）】已按公式算好给你，直接引用：
  0.25×需求热度 + 0.20×时效增长 + 0.20×(100-供给饱和) + 0.15×相对表现 + 0.10×可复制性 + 0.10×定位适配

【输出格式】每个选题一节：
### 选题 N：<标题方向，可直接当视频标题>
- 推荐理由：（引用数据：X 个视频在讲 / 赞中位数是池子的 Y 倍 / 最近 N 天还在出新的…）
- 合适的开头：（一句能直接念的话）
- 潜在问题：（具体的坑）
- 机会分：NN/100（置信度：高/中/低 —— 一句话说明证据够不够）

最后加一节「本期调研的局限」：样本多大、缺什么数据（比如没抓评论、只有 7 天）、建议下一步补什么。
不要输出 markdown 代码块，直接输出内容。"""

RESEARCH_USER = """【账号定位摘要】（判断「定位适配」用）
{account_brief}

【调研关键词】{kw}

【数据统计 · 方向聚类】（每个方向：出现条数 / 赞中位数 / 相对全池倍数 / 代表标题）
{stats}

【原始样本标题+数据】（最多 {n} 条，按赞降序）
{samples}

请输出候选选题清单。"""

AUDIT_SYSTEM = """你是「{account}」的账号调研分析师。你拿到的是一个对标账号的简介和最近 N 条作品数据（真实抓取）。
任务：输出一份**完整的账号拆解报告**。只依据给出的数据推断，数据不够的地方明确写"证据不足，以下为推测"。

【报告必须回答的问题】
1. 他在服务谁：从简介、内容对象、评论区语言习惯推断目标人群，越具体越好（别写"泛 AI 兴趣人群"这种废话）。
2. 他主要讲什么：内容主题分几类，各占多少条。
3. 什么选题出现最多：按频次排，给前 3 个高频选题方向。
4. 哪些内容表现突出：用相对表现（这条赞 ÷ 该账号赞中位数），列前 3 条最突出的，说清为什么可能跑赢（选题？标题？形式？）。
5. 商业模式推断：从简介签名、内容里卖什么课/服务/引流到哪、更新节奏推断他怎么赚钱。证据不足就写"推测"。
6. 对我们的启示：3 条可搬走的（选题方向 / 结构 / 变现思路），1 条要避开的坑。

【输出格式】六大节对应上面六问，标题用中文小节名。数据引用要具体（"24 条里 11 条讲 xx，中位赞 3.2k"）。
不要输出 markdown 代码块。"""

AUDIT_USER = """【账号简介/签名】
{bio}

【最近 {n} 条作品数据】（标题 | 赞 | 时长秒）
{posts}

请输出完整账号拆解报告。"""

REDRAFT_SYSTEM = """你是「{account}」的主笔。你拿到一份**对标爆款拆解报告**（含原文案、结构、评论区需求）和本账号定位。
任务：**不是仿写，是借骨架长自己的肉**——把对方的验证过的结构，装上我们的人设、真实案例和表达方式，产出属于我们的初稿。

【可用的开头结构库】（选最适合本选题的一种，并在稿首注明用了哪种）
- 结果前置：第一句就把做完的结果/数据拍出来
- 爆款前置：第一句先亮"这条视频怎么火的"再讲为什么
- 反常识开头：第一句给一个反直觉判断（姜胡说式）
- 痛点提问：第一句问出目标观众正在疼的那个问题

【纪律】
1. 结构可以借，观点必须是我们自己的；对方的案例/数据**不许照抄**，换成我们的场景。
2. 需要真实细节的地方写【待你补充：xxx】，逼自己填，不编造。
3. 评论区反复问的问题，优先做进内容里（那是观众自己打出来的需求）。
4. 口语化，一句一个意思，约 3 分钟体量（850~950 字）。

【输出格式】
第一行：〔开头结构：xxx〕+ 一句话说明为什么选它
然后是完整初稿正文。
不要 markdown 井号标题。"""

REDRAFT_USER = """【本账号定位摘要】
{account_brief}

【对标拆解报告】
{report}

请按上述纪律产出属于我们的初稿。"""


def build_research_prompt(stats_md, samples_md, kw, account, account_brief, n=40):
    user = (RESEARCH_USER.replace("{account_brief}", account_brief or "（见下）")
            .replace("{kw}", kw or "（未填）")
            .replace("{stats}", stats_md or "（无）")
            .replace("{n}", str(n))
            .replace("{samples}", samples_md or "（无）"))
    return RESEARCH_SYSTEM.replace("{account}", account), user


def build_audit_prompt(bio, posts_md, n, account):
    user = (AUDIT_USER.replace("{bio}", bio or "（没抓到简介）")
            .replace("{n}", str(n))
            .replace("{posts}", posts_md or "（无）"))
    return AUDIT_SYSTEM.replace("{account}", account), user


def build_redraft_prompt(report_md, account, account_brief=""):
    user = (REDRAFT_USER.replace("{account_brief}", account_brief or "（见工作台·账号定位）")
            .replace("{report}", report_md or "（无）"))
    return REDRAFT_SYSTEM.replace("{account}", account), user


# ================================================ dbskill 两模式（CC BY-NC 4.0，方法论提炼整合）
# 方法论来自 GitHub：dontbesilent2025/dbskill（dontbesilent 商业诊断工具箱）
#   · content  ← dbs-content   内容创作五维诊断（文字洁癖/封面标题/表达效率/认知落差/AI工作流）
#   · hook     ← dbs-hook      开头优化（试用装理论：诊断内容→素材盘点→三法出 10-15 条开头）
# 本地为独立提炼重写，未整段复制原文。

CONTENT_SYSTEM = """你是「{account}」的内容诊断编辑（dontbesilent 五维诊断法）。你不代笔——你诊断这份稿子该怎么做才对，结论要犀利、给行动不给空建议。

【五维逐一体检】（每维给 ✅ / ⚠️ / ❌ + 具体到句子的问题说明）
1. 文字洁癖：有没有 AI 腔（空排比、伪深度大词、每段末尾拔高意义）？语言是不是公共可验证的？
2. 封面/标题：标题是信息传递还是认知劫持？平铺直叙的标题直接指出怎么改。
3. 表达效率：能不能一句话说清核心观点？有没有 99% 的篇幅包装 1% 的内容？
4. 认知落差：同行是不是已经把这个讲清楚了？观众看完会不会觉得"我早知道"？落差在哪一句？
5. AI 工作流建议：这稿属于观点类（想明白直接拍不写稿）/ 分析类（先搜素材再改写）/ 工具类（AI 出清单人工筛）哪类？给具体下一步。

【诊断纪律】
- 只依据稿子本身，不脑补作者的努力。
- 问题必须引用原句说明，"还不错""节奏可以"这类话禁止出现。
- 最后给「如果要发，第一步做什么」——一个具体动作，不是建议。

【输出格式】中文小标题分节：核心判断（一句话）/ 五维诊断（每维 ✅⚠️❌+说明）/ 第一步 / 一句话总结。不要 markdown 井号。"""

CONTENT_USER = """诊断下面这篇稿子（选题：{title}，栏目：{pillar}）：

{body}"""

HOOK_SYSTEM = """你是「{account}」的开头优化师（dontbesilent 开头方法论）。核心信念：**开头是内容的试用装，必须独立工作**——不能假设观众看过标题；写不出好开头，90% 是内容本身缺素材。

【第一步 · 内容体检】（不过关就停下，别硬优化）
- 正文有没有价值素材？盘五个维度：冲击力数据 / 转变故事 / 金句 / 权威背书 / 痛点共鸣。
- 5 个全没有 → 停止优化，直接说"先回去补素材，开头救不了没价值的内容"。
- 有 1-2 个 → 可以优化但要注明"冲击力受限"。
- 3 个以上 → 正常优化。
- 话题太窄（只有垂直人群看）→ 警告流量上限，用更普世的切入点包装。

【第二步 · 诊断现有开头】（有开头才做）六查：独立性（不看标题能懂吗）/ Hook（前5秒有抓手吗）/ 悬念（是不是直接给了答案）/ 可信度（为什么信你）/ 口播友好（自问自答、书面语直接判死）/ 匹配度（开头承诺正文兑现了吗）。

【第三步 · 三法出开头】（共 10-15 条）
- 方法一 素材提取：优先级 = 晒结果+反转 > 数据冲击 > 反差转变 > 金句 > 权威+观点 > 痛点+悬念。
- 方法二 素材增补：素材不够就主动补（真实数据型空位用【待你补充：xxx】标出，不许编）。
- 方法三 悬念制造：把结论改成问题——用"为什么"不用"证明"，用"问题"不用"结论"。
- 全部口播友好：能直接念出来，禁自问自答、书面语。

【输出格式】中文小标题分节：素材盘点（5 维各有没有）/ 现有开头诊断（六查结果）/ 优化方案（三法分组列表，每条一行）/ Top3 推荐（每条带为什么）。不要 markdown 井号。"""

HOOK_USER = """优化下面这篇稿子的开头（选题：{title}）：

{body}"""

FRESH_SYSTEM = """你是「{account}」的选题编辑。你拿到的是**今天刚抓的 AI 行业资讯/热点清单**（真实抓取，不是你想象的）和本账号定位。
任务：从今天的资讯里，筛出**这个账号值得做**的选题，每条给足推荐理由。

【筛选标准】（按账号定位：大白话教普通人了解/用上 AI；受众=小白/没用起来的/想靠 AI 赚米的）
1. 与「普通人用上 AI」直接相关 → 优先；纯行业高层动态（融资/财报/大佬互怼）→ 除非能落到"对你的影响"，否则丢掉。
2. 教学潜力：能不能做出"3 分钟教你上手/避坑"的结构？工具类、新功能开放类优先。
3. 防坑/认知价值：AI 味检测、水印、版权、骗局类是本账号强选题。
4. 时效窗：今天就该做的（大家今晚就会刷到，明早再做就晚了）标注"今日做"。

【纪律】
1. 只从给定清单里选，清单外的一个字不提。
2. 推荐理由必须引用资讯里的具体事实（谁/发布了什么/数字），并说清"跟我的受众有什么关系"。
3. 开头要能直接念（10~25 字）。
4. 宁缺毋滥：一条能打的都没有，就直说"今天没有值得做的，建议做工具实测"并给替代方向。

【输出格式】每条：
### 选题 N：<可直接当视频标题>
- 推荐理由：（引资讯事实 + 与受众的关系）
- 合适的开头：（一句能念的话）
- 栏目建议：①工具教学 / ②防坑科普 / ③开源实战
- 时效：今日做 / 本周内即可

最后加一行「今日行情」：今天整体值得做几条、普遍什么方向。不要 markdown 代码块。"""

FRESH_USER = """【账号定位摘要】
{account_brief}

【今日资讯清单】（抓取时间 {now}，共 {n} 条）
{items}

请筛出值得做的选题。"""


def build_content_prompt(text, title="", pillar="①", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    return (CONTENT_SYSTEM.format(account=account),
            CONTENT_USER.format(body=_clip(text) or "（没给稿子）",
                                title=title or "（沿用稿子主题）", pillar=pillar_name))


def build_hook_prompt(text, title="", pillar="①", account="（我的 AI 账号）"):
    pillar_name = {"①": "① AI 工具上手教学", "②": "② AI 知识 / 防坑科普",
                   "③": "③ 我的开源 skill / 工具实战"}.get(pillar, pillar)
    return (HOOK_SYSTEM.format(account=account),
            HOOK_USER.format(body=_clip(text) or "（没给稿子）",
                             title=title or "（沿用稿子主题）", pillar=pillar_name))


def build_fresh_prompt(items_md, account, account_brief, now="", n=0):
    user = (FRESH_USER.replace("{account_brief}", account_brief or "（见下）")
            .replace("{items}", items_md or "（无）")
            .replace("{now}", now or time.strftime("%Y-%m-%d %H:%M"))
            .replace("{n}", str(n)))
    return FRESH_SYSTEM.replace("{account}", account), user


ACTIONS = {
    "draft":      ("写初稿", None),
    "rewrite":    ("借结构改写", None),
    "oral":       ("口播化",   build_oral_prompt),
    "humanize":   ("去 AI 味", build_humanize_prompt),
    "titles":     ("起标题",   build_titles_prompt),
    "storyboard": ("分镜",     build_storyboard_prompt),
    "score":      ("盲测打分", build_score_prompt),
}


def research(stats_md, samples_md, kw, account="（我的 AI 账号）",
             account_brief="", cfg=None):
    """选题调研：数据统计 → 候选选题清单（Pongfi idea-generation）。"""
    cfg = cfg or load_cfg()
    system, user = build_research_prompt(stats_md, samples_md, kw, account, account_brief)
    return _clean(chat(cfg, system, user))


def audit(bio, posts_md, n, account="（我的 AI 账号）", cfg=None):
    """账号拆解报告（Pongfi account-audit）。"""
    cfg = cfg or load_cfg()
    system, user = build_audit_prompt(bio, posts_md, n, account)
    return _clean(chat(cfg, system, user))


def redraft(report_md, account="（我的 AI 账号）", account_brief="", cfg=None):
    """借爆款结构 + 我们的人设 → 属于我们的初稿。"""
    cfg = cfg or load_cfg()
    system, user = build_redraft_prompt(report_md, account, account_brief)
    return _clean(chat(cfg, system, user))



def build(action, text="", title="", pillar="①", extra="",
          account="（我的 AI 账号）", hits=None, think=False, brief="", hints=None):
    """按 action 造出 (system, user) 提示词。think=True 时追加「先想后写」指令。

    brief = 账号定位摘要（redraft 用；其余模式提示词内已含定位，不需要）。
    hints = 提示级命中（humanize 用，模型被明令不许改它们）。"""
    action = (action or "draft").strip()
    if action == "rewrite":
        system, user = build_remix_prompt(text, title, pillar, extra, account)
    elif action == "oral":
        system, user = build_oral_prompt(text, title, pillar, account)
    elif action == "humanize":
        system, user = build_humanize_prompt(text, title, pillar, account,
                                             hits=hits, think=think, hints=hints)
        return system, user          # humanize 内部已处理 think，别重复追加
    elif action == "titles":
        system, user = build_titles_prompt(text, title, pillar, account)
    elif action == "storyboard":
        system, user = build_storyboard_prompt(text, title, pillar, account)
    elif action == "score":
        system, user = build_score_prompt(text, title, pillar, account)
        return system, user          # 打分不做「先想后写」——盲评纪律：相信第一直觉
    elif action == "audit":
        # text = 已格式化的作品数据（标题|赞|时长 每行一条），extra = 账号简介
        n = max(1, text.count("\n") + 1)
        system, user = build_audit_prompt(extra, text, n, account)
        return system, user
    elif action == "redraft":
        # text = 对标拆解报告全文
        system, user = build_redraft_prompt(text, account, brief)
        return system, user
    elif action == "content":
        system, user = build_content_prompt(text, title, pillar, account)
        return system, user
    elif action == "hook":
        system, user = build_hook_prompt(text, title, pillar, account)
        return system, user
    else:
        system, user = build_prompt(title, pillar, extra, account)
    if think:
        system += THINK_PREFIX
    return system, user


def process(action, text="", title="", pillar="①", extra="",
            account="（我的 AI 账号）", cfg=None, hits=None, think=False, brief="",
            hints=None):
    """统一入口：按 action 走不同方法论，返回 (text, system, user)。

    action ∈ draft / rewrite / oral / humanize / titles / storyboard / score
             / content / hook
    """
    cfg = cfg or load_cfg()
    system, user = build(action, text=text, title=title, pillar=pillar, extra=extra,
                         account=account, hits=hits, think=think, brief=brief, hints=hints)
    return _clean(chat(cfg, system, user)), system, user


def fresh(items_md, account="（我的 AI 账号）", account_brief="", cfg=None):
    """今日新鲜选题：今日资讯清单 × 账号定位 → 值得做的选题 + 推荐理由。"""
    cfg = cfg or load_cfg()
    system, user = build_fresh_prompt(items_md, account, account_brief)
    return _clean(chat(cfg, system, user))


# ------------------------- A/B 卡实时抓取（今日 AI 大事 / 今日爆款热榜） -------------------------
LIVEFEED_SYSTEM = """你是「{account}」这个抖音账号的选题值班编辑。刚从网上抓回来一批今天的原始清单，
你的任务：按账号定位筛掉不相干的，把值得知道的整理成一张能直接用的清单。

账号定位（最高依据）：
{account_brief}

【硬纪律】
1. 宁缺毋滥：不合适的直接丢，别硬凑数量；一条都挑不出来就明说。
2. 每条只给三样：标题（≤24字，直接能当选题标题）、为什么跟我们受众有关（一句话，
   必须引用原始清单里的事实，不许自己编）、能做的角度（一句话，说清我们讲什么）。
3. 别改写事实、别加原始清单里没有的数字。
4. 输出格式（严格遵守，前端要按行解析）：
   每条一行，格式为：标题 ｜ 相关性理由 ｜ 角度
   第一行先给一行日期说明，例如：抓取时间 {now} ｜ 原始 {n} 条 ｜ 筛出 X 条
   全部挑不动时只输出一行：今天这批没有值得做的，理由：……"""

LIVEFEED_USER_A = """下面是今天抓到的 AI 资讯，两个来源：
- 常规条目：aihot.news 多源热点（事件聚簇+热度排名，来源标在括号里）；
- 带〔builder〕前缀：AI 圈一线建造者（Karpathy、各公司 CEO/研究员等 26 人）过去 24 小时的 X 一手发言——观点新鲜、常领先媒体报道，优先挖"这个人说了什么普通人能用上的"。
受众三类：没接触过 AI 的小白 / 接触过没用起来的 / 想靠 AI 做点事的。
筛选标准：① 普通人听得懂、跟他们用 AI 有关；② 或者直接影响"用 AI 做自媒体"这件事。
纯行业高层动态、融资八卦、跟普通人用 AI 无关的，直接丢。

原始清单（{n} 条）：
{items}"""

LIVEFEED_USER_B = """下面是刚从抖音热榜抓回来的热门话题（带热度值）。
筛选标准：① 这个热点能和我们账号的三支柱之一结合（AI 工具教学 / AI 防坑科普 / 开源实战）；
② 能做出"别人没有、我们有"的角度，而不是纯蹭热度复读。
明显蹭不上的（明星八卦、社会新闻跟 AI/自媒体无关的），直接丢。

原始清单（{n} 条）：
{items}"""


def build_livefeed_prompt(target, items_md, account, account_brief="", now="", n=0):
    user_tpl = LIVEFEED_USER_A if target == "A" else LIVEFEED_USER_B
    user = (user_tpl.replace("{items}", items_md or "（无）").replace("{n}", str(n)))
    system = (LIVEFEED_SYSTEM.replace("{account_brief}", account_brief or "（见下）")
              .replace("{account}", account)
              .replace("{now}", now or time.strftime("%Y-%m-%d %H:%M")))
    return system, user


def livefeed(target, items_md, account="（我的 AI 账号）", account_brief="", cfg=None, n=0):
    """A/B 卡实时抓取的模型筛选：原始清单 → 按定位筛出值得做的清单。"""
    cfg = cfg or load_cfg()
    system, user = build_livefeed_prompt(target, items_md, account, account_brief, n=n)
    return _clean(chat(cfg, system, user))


def test(cfg=None):
    cfg = cfg or load_cfg()
    t0 = time.time()
    out = chat(cfg, "你是一个测试助手。", "只回复四个字：连接正常")
    return {"ok": True, "reply": out[:40], "ms": int((time.time() - t0) * 1000)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="")
    ap.add_argument("--pillar", default="①")
    ap.add_argument("--extra", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    try:
        if args.test:
            r = test()
            print("OK %s ms" % r["ms"])
            print(r["reply"])
            return
        txt = write(args.title, args.pillar, args.extra)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(txt)
            print("SAVED " + args.out)
        else:
            print(txt)
    except Exception as exc:  # noqa: BLE001
        print("ERR " + str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- 视觉分析
def chat_vision(cfg, system, user_text, images, timeout=300):
    """多模态分析：images 是 dataURL 列表。OpenAI 兼容 chat/completions。

    模型名优先取 cfg["vision_model"]（视觉专用槽位，如 Qwen2.5-VL），
    没配就退回主 model —— 主模型若不支持图片，接口会报错并提示去配 vision_model。
    """
    base = (cfg.get("base_url") or "").rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("vision_model") or "").strip() or (cfg.get("model") or "").strip()
    if not base or not model:
        raise RuntimeError("还没配模型。去「账号定位」页的「模型设置」配好。")
    if "11434" not in base and not key:
        raise RuntimeError("还没填 API Key。")
    content = [{"type": "text", "text": user_text}]
    for du in images:
        content.append({"type": "image_url", "image_url": {"url": du}})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": content}],
        "temperature": 0.3,
        "stream": False,
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % (key or "ollama")})
    try:
        raw = OPENER.open(req, timeout=timeout).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        hint = ""
        if exc.code in (400, 404, 422) and not (cfg.get("vision_model") or "").strip():
            hint = "（主模型可能不支持看图。去「账号定位」页模型设置里加「视觉模型」名，"
            hint += "如硅基流动的 Qwen/Qwen2.5-VL-72B-Instruct，Key 不用换）"
        raise RuntimeError("接口报 %s：%s %s" % (exc.code, detail, hint))
    except urllib.error.URLError as exc:
        raise RuntimeError("连不上接口：%s（检查 Base URL 和网络）" % exc.reason)
    try:
        d = json.loads(raw)
        return d["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001
        raise RuntimeError("返回结构看不懂：%s" % raw[:300])
