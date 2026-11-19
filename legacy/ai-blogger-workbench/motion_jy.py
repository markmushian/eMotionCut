# -*- coding: utf-8 -*-
"""动效工程 → 剪映草稿（解除合并版）。

思路：导出时不再把动效卡和文字烧死在一个合并视频里，而是——
  1. 渲染一版「无字背景层」（render.mjs --notext，模板动效/图形全保留，文字全隐藏）
  2. 渲染同时采集「文字事件表」（--textevents，每 0.1s 采样每个文字元素的
     文本 / 屏幕矩形 / 可见时间段 —— 来自模板真实排版位置）
  3. 用 pyJianYingDraft 组装剪映草稿：
       主轨     = 底片.mp4（含口播配音）
       画中画轨 = 每张动效卡一条独立片段（从无字背景层按卡的时间窗切出，
                  可单独缩放/移动/删减，隐藏它就露出底片）
       文字轨   = 每条文字独立文本段（按模板原位置/字号摆放，随便改字缩放移动）
细调终站 = 剪映。
"""
import json
import os
import re

JY_DRAFT_DIR = r"C:\Users\Administrator\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
FRAME_W, FRAME_H = 1920, 1080
GAP = 0.45          # 采样断档容忍（秒），元素动画闪帧不至于切碎
WHITE = (1.0, 1.0, 1.0)


def merge_text_events(tev_path):
    """render.mjs --textevents 产物 → 连续文字片段表。
    输出 [{k, sc, text, t0, t1, x, y, w, h}]，时间为模板输出时间轴（秒）。"""
    data = json.load(open(tev_path, encoding="utf-8"))
    samples = data.get("events") or []
    per = {}                                    # k -> [ [t0,t1,text,sc,[x,y,w,h,n]] ]
    for ev in samples:
        t = float(ev.get("t") or 0)
        for it in ev.get("items") or []:
            k = str(it.get("k") or it.get("text"))
            box = per.setdefault(k, [t, t, "", "", [0.0, 0.0, 0.0, 0.0, 0]])
            if t - box[1] > GAP:                # 断档 → 开新段（保留最长那段）
                box = per[k] = [t, t, "", "", [0.0, 0.0, 0.0, 0.0, 0]]
            box[1] = t
            txt = str(it.get("text") or "")
            if len(txt) > len(box[2]):
                box[2] = txt
            if it.get("sc") and not box[3]:
                box[3] = str(it["sc"])
            r = box[4]
            r[0] += float(it.get("x") or 0); r[1] += float(it.get("y") or 0)
            r[2] += float(it.get("w") or 0); r[3] += float(it.get("h") or 0)
            r[4] += 1
    clips = []
    for k, (t0, t1, text, sc, r) in per.items():
        n = max(r[4], 1)
        if t1 - t0 < 0.15 or not text.strip():
            continue
        clips.append({"k": k, "sc": sc, "text": text,
                      "t0": round(t0, 2), "t1": round(t1 + 0.1, 2),
                      "x": r[0] / n, "y": r[1] / n, "w": r[2] / n, "h": r[3] / n})
    clips.sort(key=lambda c: c["t0"])
    # 去重：父子容器同时可见时，父元素文本=子元素聚合。同文本且矩形包含 → 丢父留子。
    out = []
    for c in clips:
        dup = False
        for o in clips:
            if o is c or o["text"] != c["text"]:
                continue
            if (o["x"] >= c["x"] and o["y"] >= c["y"]
                    and o["x"] + o["w"] <= c["x"] + c["w"] + 2
                    and o["y"] + o["h"] <= c["y"] + c["h"] + 2
                    and min(c["t1"], o["t1"]) > max(c["t0"], o["t0"])):
                if c["w"] * c["h"] >= o["w"] * o["h"]:   # c 是父（更大）→ 丢
                    dup = True
                break
        if not dup:
            out.append(c)
    return out


def map_time(t, pairs):
    """模板输出时间轴 → 底片时间轴。pairs = 按输出顺序的卡窗映射表。"""
    for p in pairs:
        if p["out_s"] - 1e-6 <= t < p["out_e"]:
            ratio = (p["e"] - p["s"]) / max(p["out_e"] - p["out_s"], 1e-6)
            return p["s"] + (t - p["out_s"]) * ratio
    return None


def build_jy_draft(job_dir, pairs, tev_path, base_video, base_dur,
                   name, bg_video, width, height, log=lambda m: None):
    """组装剪映草稿。pairs=[{s,e,out_s,out_e,title}]（输出顺序），返回 (path, 统计dict)。"""
    import pyJianYingDraft as draft
    from pyJianYingDraft import trange_seconds as trs

    clips = merge_text_events(tev_path)
    mapped = []
    for c in clips:
        t0 = map_time(c["t0"], pairs)
        t1 = map_time(c["t1"], pairs)
        if t0 is None or t1 is None or t1 - t0 < 0.1:
            continue
        c["s0"], c["s1"] = round(t0, 2), round(t1, 2)
        mapped.append(c)

    folder = draft.DraftFolder(JY_DRAFT_DIR)
    script = folder.create_draft(name, width, height, fps=30, allow_replace=True)

    # ---- 主轨：底片（口播画面 + 配音） ----
    script.append_track(draft.TrackSpec(draft.TrackType.video, "底片"))
    mat_base = draft.VideoMaterial(base_video)
    script.add_segment(draft.VideoSegment(
        mat_base, trs(0, duration=round(base_dur, 2))), "底片")

    # ---- 画中画轨：每张动效卡一条独立片段（无字背景层按卡窗切） ----
    script.append_track(draft.TrackSpec(draft.TrackType.video, "动效卡"))
    mat_bg = draft.VideoMaterial(bg_video)
    n_cards = 0
    for i, p in enumerate(pairs):
        tgt = max(p["e"] - p["s"], 0.2)
        src = max(p["out_e"] - p["out_s"], 0.2)
        src_cut = min(src, tgt)
        seg = draft.VideoSegment(
            mat_bg, trs(p["s"], duration=round(src_cut, 2)),
            source_timerange=trs(p["out_s"], duration=round(src_cut, 2)))
        script.add_segment(seg, "动效卡")
        n_cards += 1

    # ---- 文字轨：同一时刻并存的文字各占一轨（贪心装箱） ----
    tracks = []                                # [[s0,s1],...]
    st = draft.TextStyle(size=8, bold=True, color=WHITE)
    bd = draft.TextBorder(color=(0, 0, 0), width=40)
    for c in mapped:
        idx = next((i for i, tr in enumerate(tracks) if c["s0"] >= tr[-1] - 1e-3), None)
        if idx is None:
            tracks.append([]); idx = len(tracks) - 1
        tracks[idx].append(c["s1"])
        tname = "文字%d" % (idx + 1)
        if tname not in getattr(script, "tracks", {}):
            script.append_track(draft.TrackSpec(draft.TrackType.text, tname))
        h = max(min(c["h"], FRAME_H * 0.25), 24.0)
        size = max(3.0, min(round(h / 9.0, 1), 26.0))
        style = draft.TextStyle(size=size, bold=True, color=WHITE)
        cx = (c["x"] + c["w"] / 2.0 - width / 2.0) / (width / 2.0)
        cy = (height / 2.0 - (c["y"] + c["h"] / 2.0)) / (height / 2.0)
        clip = draft.ClipSettings(transform_x=round(cx, 3), transform_y=round(cy, 3))
        script.add_segment(draft.TextSegment(
            c["text"], trs(c["s0"], duration=round(c["s1"] - c["s0"], 2)),
            style=style, border=bd, clip_settings=clip), tname)

    script.save()
    path = os.path.join(JY_DRAFT_DIR, name)
    log("草稿组装完成：%d 卡 / %d 条文字 / %d 条文字轨" % (n_cards, len(mapped), len(tracks)))
    return path, {"cards": n_cards, "texts": len(mapped), "text_tracks": len(tracks),
                  "clips": len(clips)}


def draft_name(mid):
    import datetime
    return "动效草稿_%s_%s" % (re.sub(r"[\W]+", "", mid)[:24] or "mj",
                              datetime.datetime.now().strftime("%m%d-%H%M"))
