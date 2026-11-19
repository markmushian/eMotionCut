# -*- coding: utf-8 -*-
"""adu-motion-video 动效包装：口播成片 + 文案 → 自动选型 → 构建 → 渲染。

链路：whisper 词级转写 → auto-capabilities（裁剪）→ 大模型写 auto-brief
→ auto-plan（失败回喂修复，最多 3 轮）→ auto-build → audio → mix → render → check。
状态机照抄 server.py 的 LEY 模式，独立于剪辑 JOB。
"""
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys_path_saved = list(sys.path)
sys.path.insert(0, HERE)
import aiwrite  # noqa: E402  复用模型配置与 chat()

ADU_ROOT = os.path.join(PROJ, "工作区", "缓存", "skill-参考", "adu-motion-video-main")
ADU_PLAN_PY = os.path.join(ADU_ROOT, "scripts", "pipeline.py")
ADU_VENV = os.path.join(ADU_ROOT, ".venv", "Scripts", "python.exe")
ADU_CATALOG = os.path.join(ADU_ROOT, "references", "template-catalog.json")
ADU_OUT = os.path.join(HERE, "工作区", "动效包装")
FF_DIR = os.path.join(HERE, "工具", "ffmpeg")
FFPROBE = os.path.join(FF_DIR, "ffprobe.exe")
KOUBAO_DIR = os.path.join(PROJ, "全自动生产线", "口播一键成片")
OUT_DIR = os.path.join(HERE, "工作区", "输出")      # 剪辑成片（带字幕）
CACHE_DIR = os.path.join(HERE, "工作区", "缓存")    # 无字幕初剪
BGM_DIR = os.path.join(HERE, "工作区", "BGM曲库")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}

MAX_REPAIR = 3          # auto-plan 失败回喂大模型修复的轮数
MAX_MEDIA = 60          # 素材清单最多列多少个文件

# released 稳定风格（智能推荐的候选范围）
RELEASED_STYLES = ["01-A", "01-B", "01-C", "02-A", "02-B"]

ADU_LOCK = threading.Lock()
ADU = {
    "running": False, "done": False, "ok": False, "lines": [], "started": 0.0,
    "elapsed": 0.0, "stage": "", "out_path": "", "out_size": 0, "project": "",
    "style": "", "video": "",
}


def adu_push(line):
    line = str(line).rstrip("\r\n")
    if not line:
        return
    with ADU_LOCK:
        ADU["lines"].append(line)


def adu_stage(s):
    with ADU_LOCK:
        ADU["stage"] = s
    adu_push("—— %s ——" % s)


def adu_start(meta):
    with ADU_LOCK:
        ADU.update({"running": True, "done": False, "ok": False, "lines": [],
                    "started": time.time(), "elapsed": 0.0, "stage": "准备",
                    "out_path": "", "out_size": 0, "project": ""})
        ADU.update(meta)


def adu_finish(ok, out_path=""):
    with ADU_LOCK:
        ADU["running"] = False
        ADU["done"] = True
        ADU["ok"] = bool(ok)
        ADU["elapsed"] = time.time() - ADU["started"]
        if out_path:
            ADU["out_path"] = out_path
            try:
                ADU["out_size"] = os.path.getsize(out_path)
            except OSError:
                ADU["out_size"] = 0


def adu_status():
    with ADU_LOCK:
        snap = {k: ADU[k] for k in ADU}
        snap["lines"] = list(ADU["lines"])
    snap["elapsed"] = (time.time() - ADU["started"]) if ADU["running"] else ADU["elapsed"]
    return snap


# ---------------------------------------------------------------- 小工具
def _env():
    env = dict(os.environ)
    env["PATH"] = FF_DIR + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["ADU_PYTHON_OVERRIDE"] = ADU_VENV
    return env


def _adu(args, tag):
    """跑 pipeline.py 子命令，输出实时进日志；返回 (退出码, 全部输出文本)。"""
    args = [ADU_VENV, "-X", "utf8", ADU_PLAN_PY] + [str(a) for a in args]
    adu_push("[%s] $ adu %s" % (tag, " ".join(str(a) for a in args[3:])))
    buf = []
    try:
        proc = subprocess.Popen(args, cwd=ADU_ROOT, env=_env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="ignore", bufsize=1)
    except OSError as exc:
        adu_push("[%s] ! 启动失败：%s" % (tag, exc))
        return -1, str(exc)
    for line in iter(proc.stdout.readline, ""):
        if line.strip():
            adu_push("[%s] %s" % (tag, line.rstrip()))
            buf.append(line.rstrip())
    proc.stdout.close()
    proc.wait()
    if proc.returncode:
        adu_push("[%s] ! 退出码 %s" % (tag, proc.returncode))
    return proc.returncode, "\n".join(buf)


def probe_duration(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "format=duration", "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    d = json.loads(r.stdout or "{}")
    return float(d["format"]["duration"])


def video_duration(path):
    """优先用工作台 ffmpeg，找不到就用 adu venv 的 ffprobe。"""
    for exe in (FFPROBE, os.path.join(ADU_ROOT, ".venv", "Scripts", "ffprobe.exe")):
        if os.path.isfile(exe):
            globals()["FFPROBE"] = exe
            break
    return probe_duration(path)


FFMPEG = os.path.join(FF_DIR, "ffmpeg.exe")


def _extend_bgm(path, need):
    """曲子比片短 → 交叉淡化无缝延长成整曲（adu 规则：必须完整曲目，禁止静默循环）。
    产物缓存在 BGM曲库\\_延长\\，同曲同长度直接复用。"""
    out_dir = os.path.join(BGM_DIR, "_延长")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s_%dsexa.wav"
                       % (os.path.splitext(os.path.basename(path))[0], int(need + 0.999)))
    if os.path.isfile(out) and os.path.getsize(out) > 4096:
        return out
    if os.path.isfile(out):
        os.remove(out)          # 上次失败留下的残file，清掉重做
    tlen = probe_duration(path)
    cf = min(4.0, tlen * 0.25)          # 交叉淡化 4 秒（短曲按 1/4 时长）
    copies = 1
    if need > tlen:
        copies = int(-(-(need - tlen) // (tlen - cf))) + 1   # ceil
    args = [FFMPEG, "-y"]
    for _ in range(copies):
        args += ["-i", path]
    if copies == 1:
        args += ["-t", "%.3f" % need]
    else:
        chain = "[0:a][1:a]acrossfade=d=%.2f[x1]" % cf
        for i in range(2, copies):
            chain += ";[x%d][%d:a]acrossfade=d=%.2f[x%d]" % (i - 1, i, cf, i)
        args += ["-filter_complex", chain, "-map", "[x%d]" % (copies - 1),
                 "-t", "%.3f" % need]
    args += ["-c:a", "pcm_s16le", out]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode or not os.path.isfile(out) or os.path.getsize(out) <= 4096:
        if os.path.isfile(out):
            os.remove(out)      # 失败不留残file，避免污染缓存
        raise RuntimeError("BGM 延长失败：%s" % (r.stderr or "")[-300:])
    return out


# ---------------------------------------------------------------- 转写（词级）
_koubo_mod = None


def _koubo():
    """借口播成片台的 whisper 封装（本地离线模型 + 绕 PyAV 解码）。"""
    global _koubo_mod
    if _koubo_mod is None:
        spec = importlib.util.spec_from_file_location(
            "koubo_pipeline", os.path.join(KOUBAO_DIR, "pipeline.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _koubo_mod = m
    return _koubo_mod


def build_transcript(video, job_dir):
    """→ [{id,start,end,text,words:[{start,end,text}]}]，行级=whisper 段、词级=对齐词。"""
    adu_push("[转写] whisper 词级识别（CPU，几分钟内）……")
    m = _koubo()
    _, words = m.transcribe(video, be_quiet=True, size="small")
    # whisper 词时间可能非单调/重叠（前词结束晚于后词），先整流成严格递增，
    # 否则 adapt_project 校验 "word i/j lies outside subtitle"（行尾=末词结束，中间词越界）。
    fixed, prev_end = [], 0.0
    for a, b, w in words:
        s = max(float(a), prev_end)
        e = max(float(b), s + 0.02)
        fixed.append((s, e, w))
        prev_end = e
    words = fixed
    # whisper 词 → 按标点/停顿聚行（行内带词时间，规划器靠词时间绑短语）
    rows, cur = [], []
    for i, (a, b, w) in enumerate(words):
        cur.append({"start": a, "end": b, "text": w})
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap_break = nxt and (nxt[0] - b) > 0.6
        if nxt is None or gap_break or len(cur) >= 18:
            if cur:
                rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    out = []
    for i, ws in enumerate(rows):
        text = "".join(w["text"] for w in ws)
        if not text.strip():
            continue
        out.append({"id": str(i + 1), "start": round(ws[0]["start"], 3),
                    "end": round(ws[-1]["end"], 3), "text": text,
                    "words": [{"start": round(w["start"], 3), "end": round(w["end"], 3),
                               "text": w["text"]} for w in ws]})
    if not out:
        raise RuntimeError("没识别出语音内容，检查视频音轨。")
    with open(os.path.join(job_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    adu_push("[转写] %d 行 / %d 词" % (len(out), sum(len(r["words"]) for r in out)))
    return out


# ---------------------------------------------------------------- 模板能力
def styles():
    if not os.path.isfile(ADU_CATALOG):
        return []
    d = json.load(open(ADU_CATALOG, encoding="utf-8"))
    out = []
    for t in d["templates"]:
        for s in t["styles"]:
            out.append({"id": s["id"], "title": "%s · %s" % (t["title"], s["title"]),
                        "status": s.get("status", "?"),
                        "requires": s.get("requires", []),
                        "released": s.get("status") == "released"})
    return out


def _prune_group(g):
    """裁掉对写 brief 无用的重字段，压提示词体积。"""
    return {
        "id": g["id"], "style": g.get("style"), "status": g.get("status"),
        "selection": g.get("selection"),
        "intents": g.get("intents"), "phases": g.get("phases"),
        "counts": g.get("counts"), "cueRoles": g.get("cueRoles"),
        "entry": g.get("entry"), "exit": g.get("exit"),
        "duration": {k: g.get("duration", {}).get(k)
                     for k in ("sourceSeconds", "minFrames", "maxHoldFrames")
                     if g.get("duration", {}).get(k) is not None},
        "fields": [{"id": f.get("id"), "type": f.get("type"),
                    "maxChars": f.get("maxChars"), "required": f.get("required"),
                    "inputPath": f.get("inputPath"), "inputTemplate": f.get("inputTemplate")}
                   for f in g.get("fields", [])],
    }


def capabilities(layout, only_styles=None):
    args = [ADU_VENV, "-X", "utf8",
            os.path.join(ADU_ROOT, "scripts", "auto_templates.py"),
            "capabilities", "--layout", layout]
    r = subprocess.run(args, cwd=ADU_ROOT, env=_env(), capture_output=True,
                       text=True, encoding="utf-8", errors="ignore", timeout=180)
    if r.returncode:
        raise RuntimeError("auto-capabilities 失败：%s" % (r.stderr or r.stdout)[-500:])
    d = json.loads(r.stdout)
    groups = [_prune_group(g) for g in d.get("groups", [])
              if not only_styles or g.get("style") in only_styles]
    return groups


# ---------------------------------------------------------------- 大模型填文本（结构由服务端定）
SYS_PROMPT = """你是口播视频动效分镜的文案助手。服务端已经把视频切好段、选好动效组并算好动效时机，你只负责填文案。输出单行紧凑 JSON，不要解释、不要 markdown 代码块。

规则：
1. 每段从它给出的 intents 列表里选一个语义最贴合的，逐字复制，绝不改写、绝不发明。
2. inputs：键 = textFields 里给出的 path；值 = 贴合该段口播语义的新文案，长度不超过 maxChars（硬上限，超出会被截断）。数组条目（path 形如 audience.0）同样守 maxChars。arrayFields 里给出的键不需要你输出。
3. label：本段 4-8 字小标题，提炼该段核心。
4. 只输出 {"segments":[{"id":"seg-1","intent":"…","inputs":{…},"label":"…"}]}，每段一个对象，不要输出其他键。"""


def _extract_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("返回里找不到 JSON")
    return json.loads(text[a:b + 1])


MEDIA_SLOT_TYPES = {"image", "video", "sequence", "wall", "wall-sprites", "audio"}


def _pick_combos(groups, frames, style_pref=None):
    """枚举 1~4 组组合（组可重复），返回能罩住 frames 帧的候选。
    排序：优先指定风格 → 相邻不重复组 → 余量小（画面更满）→ 段数多。"""
    from itertools import combinations_with_replacement
    by_style = {}
    for g in groups:
        by_style.setdefault(g.get("style"), []).append(g)
    cands = []
    for st, gs in by_style.items():
        for k in (1, 2, 3, 4):
            for combo in combinations_with_replacement(gs, k):
                durs = [g.get("duration", {}) for g in combo]
                lo = sum(d.get("minFrames", 0) for d in durs)
                hi = sum(d.get("minFrames", 0) + d.get("maxHoldFrames", 0) for d in durs)
                if lo <= frames <= hi:
                    rep = sum(1 for a, b in zip(combo, combo[1:]) if a["id"] == b["id"])
                    cands.append((0 if st == style_pref else 1, rep,
                                  frames - lo, -k, st, combo))
    cands.sort(key=lambda x: x[:4])
    return cands


ADU_CHUNK_MAX = 12   # 长片最多切多少节：再多 AI 填文案和渲染耗时都不划算


def _scaffold_long(groups, rows, total_frames, style_pref):
    """长片路线：单次 4 组拼不下的片子，按转写间隙切小节，每节独立选 1~4 组。
    全片锁同一个风格包，节与节的边界都落在句子停顿上。"""
    by_style = {}
    for g in groups:
        by_style.setdefault(g.get("style"), []).append(g)
    if not by_style:
        raise RuntimeError("没有可用动效组。")
    if style_pref in by_style:
        style = style_pref
    else:
        # 自动选：4 组能罩住的帧数最多者优先，并列取组多的（叙事变化更多）
        def cover(st):
            return 4 * max(g.get("duration", {}).get("minFrames", 0)
                           + g.get("duration", {}).get("maxHoldFrames", 0)
                           for g in by_style[st])
        style = sorted(by_style, key=lambda st: (-cover(st), -len(by_style[st])))[0]
    gs = by_style[style]
    gmin = min(g.get("duration", {}).get("minFrames", 0) for g in gs)
    gmax = max(g.get("duration", {}).get("minFrames", 0)
               + g.get("duration", {}).get("maxHoldFrames", 0) for g in gs)
    max_span = 4 * gmax
    target = int(max_span * 0.92)          # 每节目标帧数：贴近 4 组上限但留切点余量
    min_len = gmin + 120                   # 每节下限：最短组 + 2 秒余量
    if (total_frames + max_span - 1) // max_span > ADU_CHUNK_MAX:
        raise RuntimeError("这条片 %.1f 秒，动效规划最多支持 %d 节——建议剪成两条分别包装。"
                           % (total_frames / 60.0, ADU_CHUNK_MAX))
    cand = sorted({c for s, e, _t in rows for c in (s, e)})
    cuts, cur = [], 0
    while total_frames - cur > max_span:
        ideal = cur + target
        pool = [c for c in cand if cur + min_len <= c <= total_frames - min_len
                and c - cur <= max_span]
        if not pool:
            raise RuntimeError("长片切节失败：%.1f 秒附近找不到转写间隙，这条片停顿太碎。"
                               % (ideal / 60.0))
        cut = min(pool, key=lambda c: abs(c - ideal))
        cuts.append(cut)
        cur = cut
    bounds = [0] + cuts + [total_frames]
    adu_push("[长片] %.1f 秒切 %d 节（风格包 %s，每节独立选组）"
             % (total_frames / 60.0, len(bounds) - 1, style))
    segs, no = [], 0
    for ci in range(len(bounds) - 1):
        c0, c1 = bounds[ci], bounds[ci + 1]
        chunk_rows = [(s - c0, e - c0, t) for (s, e, t) in rows if c0 <= s < c1]
        got = None
        for _, _, _, _, _st, combo in _pick_combos(gs, c1 - c0, style):
            got = _split_frames(combo, chunk_rows, c1 - c0)
            if got:
                break
        if not got:
            raise RuntimeError("第 %d 节（%.1f~%.1f 秒）没有能装下的动效组组合——停顿分布太碎，剪一剪再试。"
                               % (ci + 1, c0 / 60.0, c1 / 60.0))
        for s in got:
            no += 1
            s["id"] = "seg-%d" % no
            s["startFrame"] += c0                       # 回到全片绝对帧
            s["rows"] = [(a + c0, b + c0, t) for (a, b, t) in s["rows"]]
            segs.append(s)
    return segs, style


def _scaffold(caps, transcript, total_frames, style_pref=None, no_media=True):
    """服务端定结构：选风格包、选组、切时间轴。返回 (段骨架列表, 风格名)。"""
    # 组级 status 全是 candidate（auto 流程正常标记），released 白名单由调用方在 caps 里筛好
    groups = [g for g in caps if g.get("intents")]
    if no_media:
        # 没素材的组带必填图片/视频槽永远 ready 不了，直接出局
        # 注意 required 可能是 None（=必填），不能只用 f.get("required", True) 判断
        def media_free(g):
            return not any(f.get("type") in MEDIA_SLOT_TYPES and f.get("required") in (None, True)
                           for f in g.get("fields", []))
        groups = [g for g in groups if media_free(g)]
        if not groups:
            raise RuntimeError("没有素材时全库组都要图片/视频槽——在输入框给一个素材文件夹路径，或换条片子。")
    rows = [(int(round(float(r["start"]) * 60)), int(round(float(r["end"]) * 60)), str(r.get("text") or ""))
            for r in transcript]
    cands = _pick_combos(groups, total_frames, style_pref)
    for _, _, _, _, st, combo in cands:
        segs = _split_frames(combo, rows, total_frames)
        if segs:
            return segs, st
    if cands:
        raise RuntimeError("组组合在帧数上可行，但切不出有效的转写分段，换条停顿少一点的片子再试。")
    # 单次 4 组罩不住的长片 → 切小节分别规划
    return _scaffold_long(groups, rows, total_frames, style_pref)


def _split_frames(combo, rows, total):
    """把 0..total 时间轴切成 len(combo) 段：帧数落窗口内，边界贴转写行间隙。"""
    mins = [g.get("duration", {}).get("minFrames", 0) for g in combo]
    holds = [g.get("duration", {}).get("maxHoldFrames", 0) for g in combo]
    alloc = list(mins)
    rest = total - sum(alloc)
    if rest < 0:
        return None
    for i in sorted(range(len(combo)), key=lambda i: -holds[i]):
        take = min(holds[i], rest)
        alloc[i] += take
        rest -= take
    if rest > 0:
        return None
    # 逐个切点递推：每个切点必须在「上一个切点 + 本段窗口」∩「后缀组还装得下剩余」内，
    # 吸附到最近的转写间隙。这样从构造上保证每一段都落在自己组的时长窗口里。
    targets, c = [], 0
    for a in alloc[:-1]:
        c += a
        targets.append(c)
    suf_min = [0] * (len(combo) + 1)
    suf_cap = [0] * (len(combo) + 1)
    for i in range(len(combo) - 1, -1, -1):
        suf_min[i] = suf_min[i + 1] + mins[i]
        suf_cap[i] = suf_cap[i + 1] + mins[i] + holds[i]
    cuts, prev = [], 0
    for i in range(len(combo) - 1):
        lo = max(prev + mins[i], total - suf_cap[i + 1])
        hi = min(prev + mins[i] + holds[i], total - suf_min[i + 1])
        if lo > hi:
            return None
        t = max(lo, min(hi, targets[i]))
        best, bestd = t, 10 ** 9
        for s, e, _txt in rows:
            for c in (s, e):
                if lo <= c <= hi and abs(c - t) < bestd:
                    best, bestd = c, abs(c - t)
        cuts.append(best)
        prev = best
    edges = [0] + cuts + [total]
    segs = []
    for i, g in enumerate(combo):
        s0, s1 = edges[i], edges[i + 1]
        in_rows = [(s, e, txt) for (s, e, txt) in rows if s0 <= s < s1]
        text = "".join(txt for (_s, _e, txt) in in_rows)
        if not text.strip():
            return None
        counts = {}
        for k, v in (g.get("counts") or {}).items():
            counts[k] = v.get("min") if isinstance(v, dict) else int(v)
        segs.append({"id": "seg-%d" % (i + 1), "group": g, "text": text, "rows": in_rows,
                     "startFrame": s0, "intent": g["intents"][0],
                     "phases": list(g.get("phases") or []),
                     "counts": counts, "durationFrames": s1 - s0})
    return segs


def _llm_fill(cfg, segs, plan_hint=""):
    """AI 只填文本：intent 选择、anchors 短语、inputs 文案、label。"""
    payload = []
    for s in segs:
        g = s["group"]
        payload.append({
            "id": s["id"],
            "narration": s["text"],
            "intents": g.get("intents"),
            "arrayFields": {k: v for k, v in s["counts"].items()},
            "textFields": [{"path": f["inputPath"], "maxChars": f.get("maxChars") or 24}
                           for f in g.get("fields", [])
                           if f.get("type") == "text" and f.get("inputPath")],
        })
    user = ["对下面每一段填文本（服务端已定结构，只补 intent / anchors / inputs / label）：",
            json.dumps(payload, ensure_ascii=False)]
    if plan_hint:
        user.append("【上一轮规划失败的报告，按它修正后重新输出完整 JSON】\n" + plan_hint)
    adu_push("[AI] 填文本（%d 段，约 %d 字提示词）……" % (len(segs), len(SYS_PROMPT) + sum(len(u) for u in user)))
    raw = aiwrite.chat(cfg, SYS_PROMPT, "\n\n".join(user), timeout=600,
                       json_mode=True, max_tokens=8192)
    return _extract_json(raw)


def _dig(d, path):
    """inputs 取值：先按扁平键 'a.b'，再按嵌套 {'a':{'b':…}}。"""
    if not isinstance(d, dict):
        return None
    if path in d:
        return d[path]
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _fallback_text(text):
    parts = [p for p in re.split(r"[，。！？；：、\s]", text or "") if len(p) >= 4]
    if parts:
        return parts[len(parts) // 2][:8]
    return (text or "本段要点")[:6]


def _to_tree(flat):
    """扁平 'a.b' / 'obs.0' 键 → 嵌套树（数字段转数组）。expand_inputs 只认嵌套结构。"""
    tree = {}
    for path, value in (flat or {}).items():
        parts = str(path).split(".")
        cur = tree
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value

    def conv(node):
        if isinstance(node, dict):
            if node and all(k.isdigit() for k in node):
                return [conv(node[k]) for k in sorted(node, key=int)]
            return {k: conv(v) for k, v in node.items()}
        return node
    return conv(tree)


def _scene_spec(selection):
    """selection（如 stage-performance@1.1.1-candidate）→ 该包版本的场景定义表。"""
    key = str(selection or "")
    if key in _SCENE_CACHE:
        return _SCENE_CACHE[key]
    spec = {}
    if "@" in key:
        pack, ver = key.split("@", 1)
        mp = os.path.join(ADU_ROOT, "packs", pack, ver, "manifest.json")
        if os.path.isfile(mp):
            try:
                m = json.load(open(mp, encoding="utf-8"))
                for sc in m.get("scenes", []):
                    spec[str(sc.get("id"))] = sc
            except Exception:
                pass
    _SCENE_CACHE[key] = spec
    return spec


_SCENE_CACHE = {}


def _alt_text(text, cur, mc):
    """和原占位文案撞车时，从口播里换一块不同的词。"""
    parts = [p for p in re.split(r"[，。！？；：、\s]", text or "") if p.strip()]
    for p in reversed(parts):
        c = p[:mc] if mc else p
        if c and c != cur:
            return c
    return (cur + "·")[:mc] if mc else cur + "·"


def _anchor_at(seg, phrase):
    """把短语解析成段内相对秒数（显式 at，绕开 phrase 全文唯一匹配的坑）。"""
    s0 = seg["startFrame"] / 60.0
    dur = seg["durationFrames"] / 60.0
    rows = seg.get("rows") or []
    hit = None
    for rs, _re, txt in rows:
        if phrase and phrase in txt:
            hit = rs
            break
    if hit is None and rows:
        hit = rows[len(rows) // 2][0]
    at = max(0.0, (hit or 0) / 60.0 - s0)
    return round(min(at, max(0.0, dur - 0.3)), 2)


def _assemble(segs, fills, user_ctx, chosen_style):
    """骨架 + AI 文案 → 完整 brief。所有机械约束在这里落地。"""
    by_id = {}
    for f in (fills.get("segments") or []):
        if isinstance(f, dict) and f.get("id"):
            by_id[str(f["id"])] = f
    out = []
    for idx, s in enumerate(segs):
        g = s["group"]
        fill = by_id.get(s["id"], {})
        fin = fill.get("inputs") if isinstance(fill.get("inputs"), dict) else {}
        intent = fill.get("intent")
        if intent not in (g.get("intents") or []):
            intent = g["intents"][0]
        text = s["text"]
        dur = s["durationFrames"] / 60.0
        # anchors：按源编舞重映射。规则：motion window 内所有 cue 共享同一 shift（原速），
        # hold 帧只摊进窗口之间的间隙（按间隙长度比例）。这正好复刻规划器的速度校验。
        spec = _scene_spec(g.get("selection")).get(g["id"].split(":", 1)[1], {})
        src = spec.get("source") or {}
        src_start, src_end = float(src.get("start", 0.0)), float(src.get("end", 0.0))
        dur_frames = s["durationFrames"]
        anchors, prev = {}, -1
        cue_roles = g.get("cueRoles") or {}
        bound_roles = set()

        def _piece_map(t):
            """源时间 t（绝对秒）→ 段内相对输出帧。"""
            src_frames = (src_end - src_start) * 60.0
            extra = dur_frames - src_frames
            wins = sorted([(float(w.get("start")), float(w.get("end")))
                           for w in (spec.get("motionWindows") or [])
                           if w.get("start") is not None and w.get("end") is not None])
            # 间隙列表（窗口之间 / 首尾）
            gaps, cur = [], src_start
            for a, b in wins:
                if a > cur:
                    gaps.append((cur, a))
                cur = max(cur, b)
            if cur < src_end:
                gaps.append((cur, src_end))
            gap_total = sum(b - a for a, b in gaps)
            # 逐段推进：gap 按「源速 + 摊派 hold」，window 按「纯源速」
            acc = 0.0
            events = []
            for a, b in gaps:
                share = extra * ((b - a) / gap_total) if gap_total > 0 else 0.0
                events.append((a, b, share, True))
            for a, b in wins:
                events.append((a, b, 0.0, False))
            events.sort()
            out = (t - src_start) * 60.0   # 兜底：t 落在首个事件之前（如 cue 正好在源起点）
            for a, b, share, is_gap in events:
                if t <= a:
                    break
                seg_end = min(t, b)
                if is_gap:
                    span = b - a
                    out = (seg_end - src_start) * 60.0 + acc + \
                          ((seg_end - a) / span * share if span > 0 else share)
                else:
                    out = (seg_end - src_start) * 60.0 + acc
                if t >= b:
                    if is_gap:
                        acc += share
                else:
                    break
            return out

        if src_end > src_start and spec.get("cues"):
            for cue in spec["cues"]:
                role = cue_roles.get(cue.get("id"))
                if not role or cue.get("at") is None:
                    continue
                fr = int(round(_piece_map(float(cue["at"]))))
                fr = max(fr, prev + 1)                       # 严格递增（帧）
                fr = min(fr, dur_frames - 1)
                anchors[role] = {"frame": fr}
                bound_roles.add(role)
                prev = fr
        if len(bound_roles) < len(set(cue_roles.values())):
            # spec 缺失：退回 LLM 短语定位（换算成段内相对帧）
            roles, seen = [], set()
            for role in cue_roles.values():
                if role not in seen:
                    seen.add(role)
                    roles.append(role)
            fa = fill.get("anchors") or {}
            for role in roles:
                if role in bound_roles:
                    continue
                ph = str(fa.get(role) or "").strip()
                at = _anchor_at(s, ph)                       # 秒（段内相对）
                fr = max(int(round(at * 60.0)), prev + 1)
                fr = min(fr, dur_frames - 1)
                anchors[role] = {"frame": fr}
                prev = fr
        # mustChange 字段的原占位文案表（撞车时换词）
        placeholders = {}
        for slot in spec.get("slots", []):
            if slot.get("inputPath") and slot.get("mustChange"):
                placeholders[str(slot["inputPath"])] = str(slot.get("sourceText") or "").strip()
        # inputs：逐字段填 + 截断 + 兜底（text 和 number 两类）
        inputs = {}
        for f in g.get("fields", []):
            ft = f.get("type")
            if ft == "number":
                v = _dig(fin, f.get("inputPath") or "")
                try:
                    inputs[f["inputPath"]] = int(v)
                except (TypeError, ValueError):
                    inputs[f["inputPath"]] = 1
                continue
            if ft != "text":
                continue
            mc = int(f.get("maxChars") or 24)
            if f.get("inputPath"):
                v = _dig(fin, f["inputPath"])
                if v is None:
                    v = _fallback_text(text)
                if isinstance(v, list):
                    v = "、".join(str(x) for x in v)
                v = str(v).strip() or _fallback_text(text)
                v = v[:mc]
                if v == placeholders.get(f["inputPath"]):
                    v = _alt_text(text, v, mc)
                inputs[f["inputPath"]] = v
        # 数组基数：counts 里的键若没有对应字段路径，就造数组
        for name, n in s["counts"].items():
            if any((f.get("inputPath") or "").startswith(name + ".") for f in g.get("fields", [])):
                continue
            arr = _dig(fin, name)
            if not isinstance(arr, list):
                arr = []
            arr = [str(x).strip()[:12] for x in arr][:n]
            while len(arr) < n:
                arr.append(arr[-1] if arr else "要点%d" % (len(arr) + 1))
            inputs[name] = arr
        # inputTemplate 字段（chapterLabel）需要的 chapter/label
        if any(f.get("inputTemplate") for f in g.get("fields", [])):
            inputs["chapter"] = "%02d" % (idx + 1)
            inputs["label"] = (str(fill.get("label") or "").strip() or _fallback_text(text))[:8]
        out.append({"id": s["id"], "text": text, "intent": intent,
                    "phases": s["phases"], "durationFrames": s["durationFrames"],
                    "counts": s["counts"], "anchors": anchors,
                    "candidates": {g["id"]: {"inputs": _to_tree(inputs)}}})
    return {"schema": "adu-auto-brief/1", "fps": 60, "layout": user_ctx["layout"],
            "brand": user_ctx["brand"], "segments": out,
            "transcript": user_ctx["transcript"], "music": user_ctx["music"],
            "allowedStyles": [chosen_style]}


def _fix_total(brief, total_frames):
    segs = brief.get("segments") or []
    if not segs:
        raise ValueError("brief 里没有 segments")
    diff = total_frames - sum(int(s.get("durationFrames", 0) or 0) for s in segs)
    if diff:
        big = max(segs, key=lambda s: int(s.get("durationFrames", 0) or 0))
        big["durationFrames"] = max(1, int(big.get("durationFrames", 0) or 0) + diff)
        adu_push("[修正] 段帧数总和差 %d，已摊到最长段 %s" % (diff, big.get("id")))
    brief["narrationDuration"] = round(total_frames / 60.0, 4)
    return brief


# ---------------------------------------------------------------- 主流程
def _list_media(media_dir):
    out = []
    if media_dir and os.path.isdir(media_dir):
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.mp4", "*.mov", "*.webm"):
            out += glob.glob(os.path.join(media_dir, "**", ext), recursive=True)
            if len(out) >= MAX_MEDIA:
                break
    return sorted(out)[:MAX_MEDIA]


def run_job(cfg):
    try:
        _worker(cfg)
    except Exception as exc:  # noqa: BLE001
        adu_push("[失败] %s" % exc)
        adu_finish(False)


def _plan_phase(ai_cfg, transcript, caps, total_frames, user_ctx, job_dir, style):
    """服务端定结构 + AI 填文本（含修复轮）→ auto-plan。返回 (rc, plan_json 绝对路径)。"""
    user_ctx["transcript"] = transcript
    segs, chosen_style = _scaffold(caps, transcript, total_frames,
                                   style if style != "auto" else None,
                                   no_media=not user_ctx.get("media"))
    adu_push("[结构] %d 段：%s（风格包 %s）"
             % (len(segs), " + ".join(s["group"]["id"].split(":", 1)[1] for s in segs), chosen_style))
    brief, rc, hint = None, 1, ""
    for attempt in range(1, MAX_REPAIR + 2):      # 1 次初稿 + 3 次修复
        try:
            fills = _llm_fill(ai_cfg, segs, hint)
        except Exception as exc:  # noqa: BLE001
            hint = "上一轮输出无法解析（%s）。请重新输出完整 JSON。" % str(exc)[:150]
            adu_push("[AI] 输出解析失败：%s" % str(exc)[:200])
            if attempt > MAX_REPAIR:
                raise RuntimeError("AI 填文本连续失败：%s" % exc)
            continue
        brief = _assemble(segs, fills, user_ctx, chosen_style)
        _fix_total(brief, total_frames)
        brief_path = os.path.join(job_dir, "brief.json")
        with open(brief_path, "w", encoding="utf-8") as f:
            json.dump(brief, f, ensure_ascii=False, indent=1)
        plan_dir = os.path.join(job_dir, "plan" if attempt == 1 else "plan_%d" % attempt)
        adu_push("[规划] 第 %d 次尝试……" % attempt)
        rc, out_text = _adu(["auto-plan", brief_path, plan_dir], "规划")
        if rc == 0:
            break
        report = os.path.join(plan_dir, "report.md")
        hint = open(report, encoding="utf-8", errors="ignore").read()[-4000:] \
            if os.path.isfile(report) else out_text[-3000:]
        if attempt < MAX_REPAIR:
            adu_push("[规划] 未通过（退出码 %s），按报告修第 %d 轮" % (rc, attempt + 1))
    if rc != 0:
        raise RuntimeError("规划多轮仍未通过。最新报告在工作区\\动效包装\\对应任务目录，通常是素材不够或段落太短，按报告补素材或换模板。")
    cands = glob.glob(os.path.join(job_dir, "plan*", "auto_plan.json"))
    if not cands:
        raise RuntimeError("规划通过但没找到 auto_plan.json。")
    return rc, max(cands, key=os.path.getmtime)


def _worker(cfg):
    video = (cfg.get("video") or "").strip().strip('"')
    if not os.path.isfile(video):
        raise RuntimeError("找不到口播视频：%s" % video)
    script = (cfg.get("script") or "").strip()
    if len(script) < 30:
        raise RuntimeError("文案太短（<30 字），先在文案页把稿子写好。")
    style = (cfg.get("style") or "auto").strip()
    # 注意：不能写 str(a == b) 当条件——恒为真值，画幅选择会被无视（10-12 实锤的 bug）
    layout = "portrait" if str(cfg.get("layout") or "").strip() in ("portrait", "9:16") else "landscape"
    brand = (cfg.get("brand") or "").strip() or "我的账号"
    media_dir = (cfg.get("media") or "").strip().strip('"')

    if not os.path.isfile(ADU_VENV):
        raise RuntimeError("adu 运行环境没装好（.venv 缺失）。先跑 skill-参考\\adu-motion-video-main 的 setup。")
    try:
        ai_cfg = aiwrite.load_cfg()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("模型没配好：%s" % exc)

    stamp = time.strftime("%m%d-%H%M")
    stem = re.sub(r'[\\/:*?"<>|]', "_", os.path.splitext(os.path.basename(video))[0])[:40]
    job_dir = os.path.join(ADU_OUT, "%s_%s" % (stamp, stem))
    proj_dir = os.path.join(job_dir, "project")
    os.makedirs(job_dir, exist_ok=True)     # plan 目录由 auto-plan 自建（拒绝已存在目录）
    if os.path.exists(proj_dir):
        raise RuntimeError("工程目录已存在，等一分钟再试。")
    with ADU_LOCK:
        ADU["project"] = job_dir

    adu_stage("① 读片")
    dur = video_duration(video)
    total_frames = round(dur * 60)
    adu_push("时长 %.2f 秒 → %d 帧（60fps）" % (dur, total_frames))

    adu_stage("② 词级转写")
    transcript = build_transcript(video, job_dir)

    adu_stage("③ 读模板能力")
    if style == "auto":
        # auto 只从 released 稳定风格白名单里挑（未 release 的风格没验证过，渲染风险大）
        caps = [g for g in capabilities(layout, None) if g.get("style") in RELEASED_STYLES]
    else:
        caps = capabilities(layout, [style])
    if not caps:
        raise RuntimeError("模板 %s 在 %s 没有可用组。" % (style, "竖屏" if layout == "portrait" else "横屏"))
    adu_push("候选组 %d 个（%s）" % (len(caps), "released 稳定风格×%d" % len(RELEASED_STYLES) if style == "auto" else style))

    # 音乐：曲库文件存在 → track 绑定；明确选了"无"才 mode=none
    bgm_name = (cfg.get("bgm") or "").strip()
    bgm_path = os.path.join(BGM_DIR, os.path.basename(bgm_name)) if bgm_name and bgm_name != "无" else ""
    if bgm_name == "无":
        music = {"mode": "none"}
        adu_push("[音乐] 明确不配 BGM（动作音效保留）")
    elif bgm_path and os.path.isfile(bgm_path):
        tlen = probe_duration(bgm_path)
        need = dur + 0.5
        if tlen + 0.2 < need:
            bgm_path = _extend_bgm(bgm_path, need)
            adu_push("[音乐] 曲子 %.0fs < 成片 %.0fs，交叉淡化延长为整曲" % (tlen, dur))
        music = {"mode": "track", "path": bgm_path, "offset": 0}
        adu_push("[音乐] 绑定曲库：%s" % bgm_name)
    else:
        raise RuntimeError("要选一首 BGM 曲库里的曲子，或明确勾选「不要 BGM」。adu 规则：不能不声明音乐。")

    media = _list_media(media_dir)
    adu_push("[素材] %d 个文件" % len(media))

    adu_stage("④ AI 写分镜 → 规划")
    user_ctx = {"layout": layout, "brand": brand, "music": music,
                "media": media, "video": os.path.abspath(video), "script": script}
    rc, plan_json = _plan_phase(ai_cfg, transcript, caps, total_frames, user_ctx,
                                job_dir, style)

    adu_stage("⑤ 构建工程")
    rc, _ = _adu(["auto-build", plan_json, video, proj_dir], "构建")
    if rc or not os.path.isdir(proj_dir):
        raise RuntimeError("auto-build 失败（退出码 %s）。" % rc)

    adu_stage("⑥ 音频合成")
    rc, _ = _adu(["audio", proj_dir], "音效")
    if rc:
        raise RuntimeError("生成音效失败。")
    rc, _ = _adu(["mix", proj_dir], "混音")
    if rc:
        raise RuntimeError("混音失败。")

    adu_stage("⑦ 渲染成片（最慢的一步，按片长耐心等）")
    out_path = os.path.join(job_dir, "%s_动效_%s.mp4" % (stem, stamp))
    rc, _ = _adu(["render", proj_dir, out_path], "渲染")
    if rc:
        txt = "\n".join(ADU.get("lines") or [])
        if "EBUSY" in txt:
            raise RuntimeError("渲染被系统拦截（EBUSY）：这个服务不是双击「启动工作台.bat」启动的，"
                               "node 子进程会被安全策略拦。关掉服务用 bat 重启后再点，"
                               "或直接双击任务目录里的「重试动效渲染.bat」补跑渲染（工程已建好，不用重来）。")
        raise RuntimeError("渲染失败。")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 10000:
        raise RuntimeError("渲染完没找到成片文件。")

    adu_stage("⑧ 校验")
    _adu(["check", out_path], "校验")  # 校验只报告，不拦结果

    adu_push("完成：%s" % out_path)
    adu_finish(True, out_path)


# ---------------------------------------------------------------- 对外接口
def videos():
    """可当口播源的视频：优先无字幕初剪，其次输出成片。"""
    items = []
    for d, tag in ((CACHE_DIR, "无字幕初剪"), (OUT_DIR, "成片(带字幕)")):
        try:
            for f in os.listdir(d):
                fp = os.path.join(d, f)
                if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in VIDEO_EXT:
                    items.append({"path": fp, "name": f, "tag": tag,
                                  "mtime": os.path.getmtime(fp)})
        except OSError:
            pass
    items.sort(key=lambda x: -x["mtime"])
    return items[:30]


def bgm_tracks():
    try:
        return sorted(f for f in os.listdir(BGM_DIR)
                      if os.path.splitext(f)[1].lower() in (".mp3", ".wav", ".m4a", ".flac"))
    except OSError:
        return []
