# -*- coding: utf-8 -*-
"""编导大脑 v2（两段式·第二部分）· LLM 语义编排

职责边界：LLM 只做语义决策——切语义段、选卡型、提关键词、排时间。
样式/颜色/动画参数永远不归它管（模板管），字数硬限由校验器+clamp 兜底。

接口预留：chat(cfg, system, user, json_mode) 可注入任意 OpenAI 兼容实现；
默认用工作台 aiwrite（用户配置的模型），换模型不改一行业务代码。
"""
import json
import re

import aiwrite
import motion_json as mj

# 剪辑语法：从爆款拆解沉淀的语义→卡型映射规则（进提示词，让 LLM 会"剪辑"而不是套模板）
CRAFT = """你要像真正的剪辑师一样做语义编排，不是机械套模板。判断依据（剪辑语法）：
1. 开场钩子（前 3 秒）：反常识判断/疑问/结果前置 → 选一张冲击力强的卡（quote 或 b1_keyword），
   时间窗 ≤4 秒，别贪长。
2. 数字即卡点：口播里出现具体数字/百分比/价格/时长 → stat 或 b1_keyword，
   num 字段只放数字本身，label 放单位或对象。
3. 对比即冲突：出现"不是A而是B / 以前vs现在 / 别人vs你" → versus 或 b3_compare，
   left/right 各 ≤8 字，只放对仗的核心词。
4. 步骤即清单："第一步/其次/最后" 或连续动作 → demo 或 b2_points（steps/points 每条一句）。
5. 金句即收束：一句总结性/情绪高点的话 → quote，允许全文重复出现 2~3 次。
6. 行动指令（结尾）：具体到"点哪里/做什么"的动作 → b4_action，action 可以长一点。
7. 讲解段（普通陈述）不要硬塞卡，留白给字幕跟读——密度比堆砌高级。
8. 节奏：卡与卡之间至少留 1.5 秒空窗让观众喘气；高潮段（数字/对比）可以密，
   平铺段必须疏。整片卡片 3~8 张（按每分钟 2~3 张估）。
9. 字段文案必须来自口播原文的压缩改写，不许引入原文没有的新概念、新数字。"""


SYSTEM = """你是短视频自动剪辑的「编导大脑」。给你一份口播的逐句时间轴（cue）和动效模板注册表，
你输出一份「动效 JSON」——决定哪句话配哪张卡、时间窗开多大、字段放什么词。

%s

【输出格式】只输出一个 JSON 对象（不要 markdown 代码块围栏）：
{
  "schema": "motion-json/1.0",
  "global": {"canvas": {"w": 1920, "h": 1080, "fps": 25}, "theme": "<模板id对应的theme>", "bgm": {}, "source": {}},
  "slides": [
    {"kind": "<注册表里的卡型>", "s": <秒>, "e": <秒>, "fields": {"<字段>": "<值>"}}
  ],
  "subs": {"mode": "template", "cue": ""}
}

硬规则：kind 只能取注册表里存在的卡型；字段名和字数上限严格照注册表；
时间窗必须落在对应句子附近（开头可以提前 0.3 秒），s<e，卡片之间不重叠。""" % CRAFT


def _cues_block(cues):
    lines = []
    for c in cues:
        lines.append("[%06.1f-%06.1f] %s" % (c["s"], c["e"], c["text"]))
    return "\n".join(lines)


def _extract_json(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("输出里没有 JSON")
    return json.loads(m.group(0))


def default_chat():
    cfg = aiwrite.load_cfg()

    def chat(_cfg, system, user, timeout=240, json_mode=True, max_tokens=None):
        return aiwrite.chat(cfg, system, user, timeout=timeout, json_mode=json_mode,
                            max_tokens=max_tokens)
    return chat


def _kit_catalog(m):
    """kit manifest 控件目录 → 提示词文本。"""
    lines = []
    for k, v in (m.get("kinds") or {}).items():
        if k == "auto":
            continue
        flds = []
        for f, r in (v.get("fields") or {}).items():
            if isinstance(r, (list, tuple)):
                flds.append("%s(%d~%d条,每条≤%d字)" % (f, r[0], r[1], r[2]))
            else:
                flds.append("%s(≤%s)" % (f, r))
        lines.append("· %s（%s）%s\n  字段：%s" % (
            k, v.get("label", ""), v.get("use", ""), "；".join(flds) or "无"))
    return "\n".join(lines)


GIT_SYSTEM = """你是短视频自动剪辑的「编导大脑」，本次使用 MotionGit 官方管线模板。

%s

【输出格式】只输出一个 JSON 对象（不要 markdown 围栏）：
{"slides": [{"kind": "<控件名>", "cue": <该卡锚定的句子序号，从0计>, "fields": {"<字段>": "<值>"}}]}

硬规则：
- kind 只能取上面控件目录里的名字；字段名、字数、条数严格照目录，不许发明字段。
- 每张卡锚定一句口播（cue 序号），一张卡覆盖一句；同一句不要放两张卡。
- 开场钩子选冲击力强的控件；数字句选数据类；对比句选对比类；步骤句选清单类。
- 卡与卡至少隔 1 句留白；整片 3~10 张；字段文案来自口播原文压缩改写。"""


NATIVE_SYSTEM = """你是短视频自动剪辑的「编导大脑」，本次使用模板自排画面（原生路线）：
模板会根据你给的文案要点自动编排全部画面，你只负责把口播分成语义段、提炼标题。

【输出格式】只输出一个 JSON 对象（不要 markdown 围栏）：
{"brand": "<品牌/频道名，≤12字>",
 "title": "<整片短标题，≤20字>",
 "segments": [{"topic": "<本段主题，≤8字>", "summary": "<本段要点，≤30字>",
               "text": "<本段覆盖的口播原文，原样拼接>"}]}

硬规则：3~8 段；每段覆盖连续的若干句，全部句子必须被覆盖、顺序不变、不重叠；
text 必须是口播原文的逐字拼接，不许改写；topic/summary 是原文关键词压缩，不引入新概念。"""


def _snap_kit_slides(raw, cues, kinds, dur):
    """LLM 产的卡（锚定 cue 序号）→ 精确落在字幕边界的时间窗。"""
    out, used = [], set()
    for sl in raw or []:
        kind = str(sl.get("kind") or "")
        if kind not in kinds or kind == "auto":
            continue
        try:
            idx = int(sl.get("cue", 0))
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(idx, len(cues) - 1))
        if idx in used:                          # 同句只留一张
            continue
        used.add(idx)
        s, e, j = cues[idx]["s"], cues[idx]["e"], idx
        mind = float((kinds.get(kind) or {}).get("minDur") or 0)
        while e - s < mind and j + 1 < len(cues):   # 停留不够→往后吞句
            j += 1
            e = cues[j]["e"]
        if out and s < out[-1]["e"] - 0.05:      # 和上一张重叠→顺延
            s = out[-1]["e"]
        e = min(e, dur)
        if e - s < 1.0:
            continue
        out.append({"kind": kind, "s": round(s, 3), "e": round(e, 3),
                    "fields": sl.get("fields") or {}})
    return out


def _orchestrate_git(cues, meta, m, chat, log, max_retry=2):
    """MotionGit 官方管线模板的编排分支。kit=我们排控件；native=模板自排。"""
    kit = m.get("kit") or {}
    dur = float((meta or {}).get("duration") or (cues[-1]["e"] if cues else 0))
    last_errs = []
    if kit.get("workflow") == "native":
        user = ("【口播逐句时间轴】\n%s\n\n【底片信息】时长 %.1f 秒，%d 句。请输出分段 JSON。"
                % (_cues_block(cues), dur, len(cues)))
        for attempt in range(1, max_retry + 2):
            try:
                raw = chat(None, NATIVE_SYSTEM, user, json_mode=True)
                d = _extract_json(raw)
                segs = []
                for sgm in (d.get("segments") or [])[:12]:
                    t = str(sgm.get("text") or "").strip()
                    if t:
                        segs.append({"topic": str(sgm.get("topic") or "")[:8],
                                     "summary": str(sgm.get("summary") or "")[:30],
                                     "text": t})
                if not segs:
                    raise ValueError("segments 为空")
                doc = {"schema": "motion-json/1.0",
                       "global": {"canvas": {"w": 1920, "h": 1080, "fps": 25},
                                  "brief": {"brand": str(d.get("brand") or "AI博主")[:12],
                                            "title": str(d.get("title") or "口播成片")[:20],
                                            "segments": segs}},
                       "slides": [{"kind": "auto", "s": 0.0, "e": round(dur, 3), "fields": {}}],
                       "subs": {"mode": "template", "cue": ""}}
                log.append("native 编排：%d 段要点，模板自排画面" % len(segs))
                return doc, log
            except Exception as exc:  # noqa: BLE001
                last_errs = ["解析异常：%s" % exc]
                log.append("第 %d 次失败：%s" % (attempt, last_errs[0]))
        doc = mj.default_plain_subs(cues, meta)
        log.append("降级纯字幕保底（原因：%s）" % "；".join(last_errs[:2]))
        return doc, log

    # ---- motion-kit：我们按控件目录排卡 ----
    catalog = _kit_catalog(m)
    user = ("【口播逐句时间轴】\n%s\n\n【本模板控件目录】\n%s\n\n"
            "【底片信息】时长 %.1f 秒，%d 句。请输出排卡 JSON。"
            % (_cues_block(cues), catalog, dur, len(cues)))
    for attempt in range(1, max_retry + 2):
        try:
            raw = chat(None, GIT_SYSTEM % catalog, user, json_mode=True)
            d = _extract_json(raw)
            slides = _snap_kit_slides(d.get("slides"), cues, m.get("kinds") or {}, dur)
            if not slides:
                raise ValueError("没有产出有效卡")
            doc = {"schema": "motion-json/1.0",
                   "global": {"canvas": {"w": 1920, "h": 1080, "fps": 25},
                              "title": str(d.get("title") or "口播成片")[:40]},
                   "slides": slides,
                   "subs": {"mode": "template", "cue": ""}}
            doc = mj.sanitize_time(doc, dur)
            doc = mj.clamp_fields(doc)
            errs = mj.validate(doc)
            if not errs:
                log.append("kit 编排通过校验，%d 张卡" % len(doc["slides"]))
                return doc, log
            last_errs = errs
            log.append("第 %d 次校验未过：%s" % (attempt, "；".join(errs[:4])))
            user = ("%s\n\n你上一次的输出有以下问题，请修正后重新输出完整 JSON：\n- %s"
                    % (user, "\n- ".join(errs[:8])))
        except Exception as exc:  # noqa: BLE001
            last_errs = ["解析异常：%s" % exc]
            log.append("第 %d 次失败：%s" % (attempt, last_errs[0]))
    doc = mj.default_plain_subs(cues, meta)
    log.append("降级纯字幕保底（原因：%s）" % "；".join(last_errs[:2]))
    return doc, log


def orchestrate(cues, meta, template_id=None, chat=None, max_retry=2):
    """cue 列表 + 元信息 → (动效JSON, 日志)。失败自动降级纯字幕，永不白跑。"""
    log = []
    chat = chat or default_chat()
    m = None
    if template_id and template_id != "auto":
        m = mj.get_manifest(template_id)
        if m and m.get("kit"):
            log.append("MotionGit 官方管线模板：%s（%s 路线）"
                       % (m["id"], (m.get("kit") or {}).get("workflow")))
            return _orchestrate_git(cues, meta, m, chat, log, max_retry)
    reg_block = mj.render_registry_prompt()
    prefer = ""
    if m:
        prefer = "\n【本次优先使用模板】%s（%s）。除非语义完全不匹配，否则卡型从它的 kinds 里选。\n" % (
            m["id"], m["name"])
        log.append("指定模板：%s" % m["id"])
    user = ("【口播逐句时间轴】\n%s\n\n【动效模板注册表】\n%s%s\n\n"
            "【本次底片信息】时长 %.1f 秒，%d 句。请输出动效 JSON。"
            % (_cues_block(cues), reg_block, prefer,
               (meta or {}).get("duration") or 0, len(cues)))
    last_errs = []
    for attempt in range(1, max_retry + 2):
        try:
            raw = chat(None, SYSTEM, user, json_mode=True)
            doc = _extract_json(raw)
            doc = mj.sanitize_time(doc, (meta or {}).get("duration"))
            doc = mj.clamp_fields(doc)
            errs = mj.validate(doc)
            if not errs:
                log.append("第 %d 次尝试通过校验，%d 张卡" % (attempt, len(doc.get("slides") or [])))
                return doc, log
            last_errs = errs
            log.append("第 %d 次校验未过：%s" % (attempt, "；".join(errs[:4])))
            user = ("%s\n\n你上一次的输出有以下问题，请修正后重新输出完整 JSON：\n- %s"
                    % (user, "\n- ".join(errs[:8])))
        except Exception as exc:  # noqa: BLE001
            last_errs = ["解析异常：%s" % exc]
            log.append(last_errs[0])
    doc = mj.default_plain_subs(cues, meta)
    log.append("降级纯字幕保底（原因：%s）" % "；".join(last_errs[:2]))
    return doc, log


if __name__ == "__main__":
    import voice_factory as vf
    bases = vf.list_bases()
    if not bases:
        print("底片库为空，先跑模式一出一条底片")
    else:
        b = bases[0]
        cues = vf.load_cues(b["id"])
        print("底片：%s，%d 句" % (b["id"], len(cues)))
        doc, log = orchestrate(cues, b)
        for l in log:
            print("·", l)
        print(json.dumps(doc, ensure_ascii=False, indent=1)[:1200])
