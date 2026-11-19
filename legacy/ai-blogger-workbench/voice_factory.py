# -*- coding: utf-8 -*-
"""口播底片工厂（两段式·第一部分）· 模式一：文案 → TTS → 气口策略 → 三件套

三件套契约（第一部分 → 第二部分唯一接口）：
  底片.mp4   纯底 + 最终音频（无字幕、无动效）
  cues.cue   逐句时间轴（SRT 格式；模式一从合成装配直接推得，零转写零漂移）
  meta.json  时长/画幅/帧率/模式/音色/AI合规标注

核心思想：模式一的"去气口"不是检测剪除，而是**气口策略**——
我们自己逐句合成、按策略插静音，时间轴精确到采样点。
"""
import json
import os
import re
import threading
import time
import uuid

import ttsfree

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.join(HERE, "底片库")
os.makedirs(BASE_DIR, exist_ok=True)

CORRECT_TABLE = os.path.join(HERE, "..", "motion-engine", "转写校正表.json")
GAP_POLICIES = {"紧凑": 0.45, "标准": 1.0, "舒缓": 1.5}
ASPECTS = ttsfree.ASPECTS
BG_COLORS = {"16:9": "0x12131c", "9:16": "0x12131c", "1:1": "0x12131c"}

JOBS = {}
JLOCK = threading.Lock()


def _ff():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _log(job, msg):
    job["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg))
    print("[vf] %s" % msg, flush=True)


def correct_text(text):
    """轻量文案校正：套用人工校正表（ASR 高频错字表同样适用于文案里的同形错字）。"""
    try:
        table = json.load(open(CORRECT_TABLE, encoding="utf-8"))
        if isinstance(table, dict):
            for wrong, right in table.items():
                if isinstance(wrong, str) and isinstance(right, str):
                    text = text.replace(wrong, right)
    except Exception:  # noqa: BLE001
        pass
    return text


def split_sentences(text):
    """按句切（cue 粒度=句），超长句按逗号再切，每段 ≤60 字（misaki 上限友好）。"""
    parts = [p.strip() for p in re.split(r"(?<=[。！？；])", text) if p.strip()]
    out = []
    for p in parts:
        if len(p) > 60:
            cur = ""
            for s in re.split(r"(?<=[，,、：:])", p):
                if cur and len(cur) + len(s) > 60:
                    out.append(cur)
                    cur = s
                else:
                    cur += s
            if cur:
                out.append(cur)
        else:
            out.append(p)
    return out


def fmt_ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def build_srt(cues):
    lines = []
    for i, c in enumerate(cues, 1):
        lines.append(str(i))
        lines.append("%s --> %s" % (fmt_ts(c["s"]), fmt_ts(c["e"])))
        lines.append(c["text"])
        lines.append("")
    return "\n".join(lines)


def _worker(jid, text, voice, speed, aspect, gap_policy):
    job = JOBS[jid]
    t0 = time.time()

    def push(stage, pct):
        job["stage"] = stage
        job["pct"] = pct

    try:
        push("校正", 3)
        text = correct_text(text.strip())
        sents = split_sentences(text)
        if not sents:
            raise RuntimeError("文案切不出句子")
        _log(job, "%d 句，音色 %s，画幅 %s，气口 %s" % (len(sents), voice, aspect, gap_policy))

        push("加载引擎", 8)
        kokoro = ttsfree._load_engine()
        if voice not in kokoro.voices:
            raise RuntimeError("音色 %s 不存在，请选择本机已安装音色" % voice)

        push("合成语音", 12)
        import numpy as np
        import soundfile as sf
        plan = ttsfree._humanize_plan(sents, seed=len(text))
        mul = GAP_POLICIES.get(gap_policy, 1.0)
        pieces, cues, sr = [], [], None
        for i, sent in enumerate(sents):
            ps = ttsfree._zhuyin(sent)
            if not ps:
                raise RuntimeError("第 %d 句无法生成音素，请检查文字后重试：%s" % (i + 1, sent[:30]))
            pp = plan[i] if i < len(plan) else {"speed": 1.0, "gap": 0.3, "gain": 1.0}
            samples, rate = kokoro.create(ps, voice=voice, speed=speed * pp["speed"],
                                          is_phonemes=True)
            if sr is None:
                sr = rate
            start = sum(len(p) for p in pieces) / sr
            pieces.append(samples * pp["gain"])
            end = sum(len(p) for p in pieces) / sr
            cues.append({"i": len(cues) + 1, "s": round(start, 3), "e": round(end, 3),
                         "text": sent})
            gap = pp["gap"] * mul
            pieces.append(np.zeros(int(rate * gap), dtype=samples.dtype))
            pct = 12 + int(70 * (i + 1) / len(sents))
            push("合成 %d/%d" % (i + 1, len(sents)), pct)
        if not pieces:
            raise RuntimeError("一句都没合成出来")
        audio = np.concatenate(pieces)
        dur = len(audio) / sr
        _log(job, "装配完成：%.1f 秒 / % 句".replace("% 句", "%d 句") % (dur, len(cues)))

        stem = time.strftime("%m%d-%H%M%S")
        bid = "底片_%s_%s" % (stem, uuid.uuid4().hex[:6])
        bdir = os.path.join(BASE_DIR, bid)
        os.makedirs(bdir, exist_ok=True)

        push("写音频", 85)
        audio_path = os.path.join(bdir, "audio.wav")
        sf.write(audio_path, audio, sr)

        cues_path = os.path.join(bdir, "cues.cue")
        with open(cues_path, "w", encoding="utf-8") as f:
            f.write(build_srt(cues))

        push("合成底片", 88)
        w, h = ASPECTS.get(aspect, (1920, 1080))
        video_path = os.path.join(bdir, "底片.mp4")
        ff = _ff()
        cmd = [ff, "-y", "-f", "lavfi", "-i",
               "color=c=%s:s=%dx%d:r=25" % (BG_COLORS.get(aspect, "0x12131c"), w, h),
               "-i", audio_path,
               "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-tune", "stillimage",
               "-c:a", "aac", "-b:a", "128k", "-shortest", video_path]
        import subprocess
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", creationflags=0x08000000)
        if p.returncode != 0 or not os.path.isfile(video_path):
            tail = (p.stderr or "").strip().splitlines()[-2:]
            raise RuntimeError("底片合成失败：%s" % (" | ".join(tail)))

        meta = {
            "id": bid, "mode": "T1", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": round(dur, 2), "aspect": aspect, "w": w, "h": h, "fps": 25,
            "voice": voice, "gap_policy": gap_policy, "sentences": len(cues),
            "text": text, "ai_label": "AI 生成配音",
            "files": {"video": "底片.mp4", "audio": "audio.wav", "cue": "cues.cue"},
        }
        with open(os.path.join(bdir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)

        job.update({"ok": True, "pct": 100, "stage": "完成", "base": meta})
        _log(job, "三件套就绪：%s（%.1f 秒）" % (bid, dur))
    except Exception as exc:  # noqa: BLE001
        job["ok"] = False
        job["error"] = str(exc)
        job["stage"] = "失败"
        _log(job, "失败：%s" % exc)
    job["running"] = False
    job["elapsed"] = round(time.time() - t0, 1)


def start(text, voice="zm_009", speed=1.0, aspect="16:9", gap_policy="标准"):
    with JLOCK:
        running = [j for j in JOBS.values() if j.get("running")]
        if running:
            return {"ok": False, "error": "已有底片任务在跑（%s），等它完成" % running[0]["id"]}
        jid = str(int(time.time() * 1000))
        JOBS[jid] = {"id": jid, "running": True, "ok": False, "pct": 0, "stage": "排队",
                     "log": [], "t0": time.time()}
    threading.Thread(target=_worker, daemon=True,
                     args=(jid, text, voice, speed, aspect, gap_policy)).start()
    return {"ok": True, "id": jid}


def status(jid):
    with JLOCK:
        job = dict(JOBS.get(jid) or {})
    if not job:
        return {"error": "任务不存在"}
    job.pop("t0", None)
    return job


def list_bases():
    out = []
    if os.path.isdir(BASE_DIR):
        for name in sorted(os.listdir(BASE_DIR), reverse=True):
            mp = os.path.join(BASE_DIR, name, "meta.json")
            if os.path.isfile(mp):
                try:
                    m = json.load(open(mp, encoding="utf-8"))
                    m["dir"] = name
                    out.append(m)
                except Exception:  # noqa: BLE001
                    pass
    return out


def load_meta(base_id):
    mp = os.path.join(BASE_DIR, os.path.basename(base_id), "meta.json")
    if not os.path.isfile(mp):
        return None
    return json.load(open(mp, encoding="utf-8"))


def load_cues(base_id):
    cp = os.path.join(BASE_DIR, os.path.basename(base_id), "cues.cue")
    if not os.path.isfile(cp):
        return []
    from media_studio import parse_srt
    with open(cp, encoding="utf-8-sig") as f:
        return parse_srt(f.read())


if __name__ == "__main__":
    r = start("很多人觉得学AI很难，其实不难。你只需要会说人话，AI就能帮你干活。今天就教你三步上手。")
    print(r)
    while True:
        time.sleep(2)
        st = status(r["id"])
        print(st.get("stage"), st.get("pct"), st.get("error", ""))
        if not st.get("running"):
            print(json.dumps(st.get("base") or st, ensure_ascii=False)[:400])
            break
