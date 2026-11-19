# -*- coding: utf-8 -*-
"""动效 JSON（两段式·第二部分的核心资产）

单一事实源：LLM 编排产出 → 校验 → 编辑台读写 → 渲染器消费。
本模块 = schema v1.0 + 确定性校验器 + 模板注册表。

模板注册表：每个动效模板一份 manifest.json（kinds → 字段与字数上限），
LLM 提示词与编辑台属性面板都从注册表自动生成——加模板零改代码。
.motion 模板包的 params.schema.json 天然就是 manifest（后续接转换器）。
"""
import glob
import json
import os
import re
import math

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, "动效模板")
SCHEMA_VERSION = "motion-json/1.0"
_cache = {"ts": 0, "reg": []}


# ---------------------------------------------------------------- 注册表
def _builtin_manifests():
    """首批三个模板的内置声明（科技风 B1-B4 字数上限来自 .motion params.schema 实测）。"""
    return [
        {
            "id": "plain-subs", "name": "纯字幕", "engine": "koubo", "theme": "默认",
            "desc": "最简兜底：只有逐句字幕跟随底片，无卡片动效",
            "kinds": {"subtitle": {"fields": {"text": 60}}},
        },
        {
            "id": "koubo-cards", "name": "口播卡片", "engine": "koubo", "theme": "暗金",
            "desc": "金句卡/数据卡/章节卡/对比卡/清单卡，跟随口播时间窗出现",
            "kinds": {
                "quote":   {"fields": {"title": 24, "sub": 30}},
                "stat":    {"fields": {"num": 12, "label": 10, "note": 20}},
                "section": {"fields": {"title": 12, "sub": 20}},
                "versus":  {"fields": {"left": 12, "right": 12, "note": 20}},
                "pins":    {"fields": {"title": 16, "items": [2, 4, 18]}},
                "demo":    {"fields": {"heading": 10, "steps": [2, 4, 22]}},
            },
        },
        {
            "id": "kinetic-tech", "name": "科技风 B1-B4", "engine": "motion", "theme": "科技深色",
            "desc": "酷炫科技风：金属字开场/编号要点/左右对比/行动收尾（1920x1080@60）",
            "kinds": {
                "b1_keyword":  {"fields": {"keyword": 8, "explanation": 42}},
                "b2_points":   {"fields": {"heading": 10, "points": [1, 5, 16]}},
                "b3_compare":  {"fields": {"heading": 10, "left": 8, "right": 8,
                                           "conclusion": 48}},
                "b4_action":   {"fields": {"summary": 10, "action": 42}},
            },
        },
    ]


def load_registry(force=False):
    """读 动效模板/*/manifest.json + 内置声明。返回 list[manifest]。"""
    manifests = sorted(glob.glob(os.path.join(TPL_DIR, '*', 'manifest.json')) +
                       glob.glob(os.path.join(TPL_DIR, '*', '*', 'manifest.json')))
    # Editing an existing nested manifest does not update the top-level directory mtime.
    now = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in manifests)
    if not force and _cache["ts"] == now and _cache["reg"]:
        return _cache["reg"]
    reg = {m["id"]: m for m in _builtin_manifests()}
    if os.path.isdir(TPL_DIR):
        pats = [os.path.join(TPL_DIR, "*", "manifest.json"),          # 平铺（旧）
                os.path.join(TPL_DIR, "*", "*", "manifest.json")]     # 分组（动效模板/NN-组/模板名/）
        for pat in pats:
            for mf in glob.glob(pat):
                try:
                    with open(mf, encoding="utf-8") as handle:
                        m = json.load(handle)
                    if m.get("id") and m.get("kinds"):
                        reg[m["id"]] = m
                except Exception:  # noqa: BLE001
                    pass
    out = list(reg.values())
    _cache.update({"ts": now, "reg": out})
    return out


def get_manifest(tid):
    for m in load_registry():
        if m["id"] == tid:
            return m
    return None


def render_registry_prompt():
    """注册表 → LLM 提示词片段（自动生成，加模板零改码）。"""
    blocks = []
    for m in load_registry():
        kinds = []
        for k, spec in m["kinds"].items():
            f = spec.get("fields", {})
            fs = ", ".join(
                ("%s[%d~%d条,每条≤%d字]" % (n, v[0], v[1], v[2]) if isinstance(v, list)
                 else "%s≤%d字" % (n, v))
                for n, v in f.items())
            kinds.append("    %s: %s" % (k, fs or "无字段"))
        blocks.append("【%s】%s —— %s\n%s" % (m["id"], m["name"], m.get("desc", ""),
                                              "\n".join(kinds)))
    return "\n".join(blocks)


# ---------------------------------------------------------------- 校验器
def _field_err(kind, spec, fields):
    errs = []
    req = spec.get("fields", {})
    for name, rule in req.items():
        v = fields.get(name)
        if isinstance(rule, list):                     # 列表字段 [min, max, 每条字数]
            if not isinstance(v, list) or not (rule[0] <= len(v) <= rule[1]):
                errs.append("%s.%s 需要 %d~%d 条，实际 %s" %
                            (kind, name, rule[0], rule[1], len(v) if isinstance(v, list) else v))
                continue
            for it in v:
                if not str(it).strip():
                    errs.append("%s.%s 有空条目" % (kind, name))
                elif len(str(it)) > rule[2]:
                    errs.append("%s.%s 条目超 %d 字：%s" % (kind, name, rule[2], it))
        else:                                          # 字符串字段 ≤rule 字
            if v is None or not str(v).strip():
                errs.append("%s.%s 缺失" % (kind, name))
            elif len(str(v)) > rule:
                errs.append("%s.%s 超 %d 字（%d）：%s" %
                            (kind, name, rule, len(str(v)), str(v)[:30]))
    extra = set(fields) - set(req)
    if extra:
        errs.append("%s 多余字段：%s" % (kind, ",".join(sorted(extra))))
    return errs


def validate(doc, require_theme=True):
    """确定性校验：结构/字数/时间单调/不重叠/卡型合法。返回错误列表（空=通过）。"""
    errs = []
    if not isinstance(doc, dict):
        return ["整体不是 JSON 对象"]
    ver = str(doc.get("schema") or "")
    if ver.split("/")[0] != "motion-json":
        errs.append("schema 缺失或不识别：%r" % ver)
    g = doc.get("global")
    if not isinstance(g, dict) or not g.get("canvas"):
        errs.append("global.canvas 缺失")
    if not isinstance(g, dict):
        g = {}
    canvas = g.get('canvas')
    if isinstance(canvas, dict):
        for key, maximum in (('w', 8192), ('h', 8192), ('fps', 240)):
            value = canvas.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                errs.append('global.canvas.%s 非法' % key)
    elif canvas:
        errs.append('global.canvas 必须是对象')
    slides = doc.get("slides")
    if not isinstance(slides, list):
        errs.append("slides 必须是数组（纯字幕工程可为空）")
        return errs
    reg = {m["id"]: m for m in load_registry()}
    prev_e = -1.0
    for i, sl in enumerate(slides):
        if not isinstance(sl, dict):
            errs.append("slides[%d] 不是对象" % i)
            continue
        s, e = sl.get("s"), sl.get("e")
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            errs.append("slides[%d] 时间窗缺失" % i)
            continue
        if not (math.isfinite(s) and math.isfinite(e) and 0 <= s < e):
            errs.append("slides[%d] 时间窗非法：s=%s e=%s" % (i, s, e))
        if s < prev_e - 0.01:
            errs.append("slides[%d] 与上一张重叠（%.1f < %.1f）" % (i, s, prev_e))
        prev_e = max(prev_e, e)
        kind = sl.get("kind")
        hit = None
        for m in reg.values():
            if kind in m["kinds"]:
                hit = m
                break
        if hit is None:
            errs.append("slides[%d] 卡型 %r 不在任何注册模板里" % (i, kind))
            continue
        errs += _field_err(kind, hit["kinds"][kind], sl.get("fields") or {})
        if require_theme and g.get("theme") and m_theme_err(g, hit):
            pass  # 主题混排允许（编辑台可逐卡换），不再报错
    subs = doc.get("subs")
    if not isinstance(subs, dict) or subs.get("mode") not in ("template", "none"):
        errs.append("subs.mode 必须是 template 或 none")
    return errs


def m_theme_err(g, manifest):
    return None


def default_plain_subs(cues, meta):
    """降级保底：纯字幕动效 JSON。永不白跑。"""
    return {
        "schema": SCHEMA_VERSION,
        "global": {
            "canvas": {"w": (meta or {}).get("w", 1920), "h": (meta or {}).get("h", 1080),
                       "fps": (meta or {}).get("fps", 25)},
            "theme": "默认",
            "bgm": {},
            "source": {"base": (meta or {}).get("id", "")},
        },
        "slides": [],
        "subs": {"mode": "template", "cue": (meta or {}).get("id", "") + "/cues.cue"},
    }


def clamp_fields(doc):
    """软修正：超限字段自动截断（配合校验器给 LLM 重试用，也用于编辑台保存兜底）。"""
    reg = {k: spec for m in load_registry() for k, spec in m["kinds"].items()}
    for sl in doc.get("slides") or []:
        spec = reg.get(sl.get("kind"))
        if not spec:
            continue
        f = sl.setdefault("fields", {})
        for name, rule in spec.get("fields", {}).items():
            v = f.get(name)
            if isinstance(rule, list) and isinstance(v, list):
                f[name] = [str(x)[: rule[2]] for x in v[: rule[1]]]
            elif isinstance(v, str):
                f[name] = v[: rule]
    return doc


def sanitize_time(doc, dur=None):
    """时间修整：单调化 + 同刻顺延 + 越界钳制（旧 KouboPack 血泪教训的通用化）。"""
    slides = sorted(doc.get("slides") or [], key=lambda x: x.get("s", 0))
    prev_e = 0.0
    for sl in slides:
        s = max(float(sl.get("s", prev_e)), prev_e)
        e = max(float(sl.get("e", s + 1)), s + 0.5)
        if dur:
            e = min(e, dur)
            s = min(s, max(dur - 0.5, 0))
        sl["s"], sl["e"] = round(s, 2), round(e, 2)
        prev_e = e
    doc["slides"] = slides
    return doc
