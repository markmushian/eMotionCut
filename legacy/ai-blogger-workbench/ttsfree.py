# -*- coding: utf-8 -*-
"""免费配音（本地离线 kokoro-onnx + misaki 中文 G2P）—— 不需要任何 Key。

背景：微软 Edge TTS 在本机网络下被 403（出口 IP 被微软风控），所以走本地引擎。
模型：工作区\\TTS\\models\\（kokoro-v1.1-zh.int8.onnx 114MB + voices-v1.1-zh.bin + config.json）
产出：工作区\\TTS\\配音\\*.wav；可选合成「配音+背景图」初片 mp4 送块①上字幕。
"""
import base64
import os
import random
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TTS_DIR = os.path.join(HERE, "工作区", "TTS")
TTS_MODELS = os.path.join(TTS_DIR, "models")
TTS_OUT = os.path.join(TTS_DIR, "配音")
TTS_BG = os.path.join(TTS_DIR, "背景")
MODEL_PATH = os.path.join(TTS_MODELS, "kokoro-v1.1-zh.int8.onnx")
VOICES_PATH = os.path.join(TTS_MODELS, "voices-v1.1-zh.bin")
VCONF_PATH = os.path.join(TTS_MODELS, "config.json")

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

# 系统推荐音色（v1.1-zh 有 100 个音色，挑几个起步；引擎加载后会校验存在性）
RECOMMEND = [
    ("zf_001", "女声 · 温和亲切（默认）"),
    ("zf_010", "女声 · 清爽利落"),
    ("zf_024", "女声 · 沉稳知性"),
    ("zf_039", "女声 · 活泼一点"),
    ("zm_009", "男声 · 沉稳大气（默认）"),
    ("zm_017", "男声 · 年轻自然"),
    ("zm_030", "男声 · 磁性低音"),
    ("zm_042", "男声 · 硬朗有力"),
]

_LOCK = threading.Lock()
_STATE = {
    "running": False, "done": False, "ok": False, "lines": [], "stage": "",
    "audio": "", "video": "", "elapsed": 0.0, "started": 0.0,
}
_ENGINE = None  # (kokoro, g2p)


def _log(msg):
    with _LOCK:
        _STATE["lines"].append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
    print("[tts] %s" % msg, flush=True)


def busy():
    with _LOCK:
        return _STATE["running"]


def status():
    with _LOCK:
        s = dict(_STATE)
        s["lines"] = list(_STATE["lines"])
    return s


def voices():
    """先给推荐列表；引擎加载过的顺手校验一遍存在性。"""
    out = [{"id": v, "label": label} for v, label in RECOMMEND]
    if _ENGINE is not None:
        try:
            have = set(_ENGINE[0].voices.keys())
            out = [v for v in out if v["id"] in have] + out  # 缺的置后不删，前端能看
        except Exception:
            pass
    return out


def _load_engine():
    """懒加载：首次合成时才 import + 载模型（约 5-15 秒）。"""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    _log("加载本地配音引擎（首次约 5~15 秒）…")
    import kokoro_onnx
    # 词表不传 config：v1.1-zh 的词表（注音ㄅㄆㄇ+声调数字）内嵌在模型元数据里；
    # 外挂的 config.json 是 v1.0 英文词表，传了会把音素压成乱码。
    kokoro = kokoro_onnx.Kokoro(MODEL_PATH, VOICES_PATH)
    _ENGINE = kokoro
    have = sorted(kokoro.voices.keys())
    zhv = [k for k in have if k.startswith(("zf_", "zm_"))]
    _log("引擎就绪：%d 个音色可用（中文 %d 个）" % (len(have), len(zhv)))
    return _ENGINE


# v1.1-zh 的输入音素 = 注音符号 + 声调数字（1-5），不是 misaki 的 IPA。
# 实测 misaki 0.7.4 的 IPA 输出（声调箭头/ɻ/ꭧ）全部是词表外字符 → 出来的语音是乱码。
_BOP_TONE = {"ˊ": "2", "ˇ": "3", "ˋ": "4", "˙": "5"}  # 没标记 = 一声
_PUNCT_MAP = {"，": ",", "。": ".", "！": "!", "？": "?", "；": ";",
              "：": ":", "、": ",", "（": "(", "）": ")", "…": "...",
              "—": "-", "“": "\"", "”": "\""}
_SEG_RE = re.compile(r"[\u4e00-\u9fff]+|[^ \u4e00-\u9fff]+")


def _zhuyin(text):
    """中文 → 注音+声调数字；标点转 ASCII。依赖 pypinyin（misaki 已带）。"""
    from pypinyin import lazy_pinyin, Style
    out = []
    for seg in _SEG_RE.findall(text or ""):
        if "\u4e00" <= seg[0] <= "\u9fff":
            toks = []
            for py in lazy_pinyin(seg, style=Style.BOPOMOFO, errors="ignore"):
                tone = "1"
                for m, d in _BOP_TONE.items():
                    if m in py:
                        tone = d
                        py = py.replace(m, "")
                        break
                if py:
                    toks.append(py + tone)
            out.append(" ".join(toks))
        else:
            for k, v in _PUNCT_MAP.items():
                seg = seg.replace(k, v)
            out.append(seg)
    return " ".join(o for o in out if o).strip()


def _phonemes(g2p, text):
    """兼容旧调用签名（g2p 参数已弃用）。"""
    return _zhuyin(text)


_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])\s*")
# 文案页骨架标记行（【】开头 / markdown 标题 / 空行）在配音时直接跳过
_SKIP_RE = re.compile(r"^\s*(【|#|·|-|\*|>{1,2})")


def clean_script(text):
    """把八段骨架稿洗成纯口播文本：去掉【】提示行、markdown 记号、多空行。"""
    kept = []
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if _SKIP_RE.match(s):
            continue
        s = re.sub(r"[*_`#>]+", "", s)          # markdown 记号
        s = re.sub(r"^\d+[\.、]\s*", "", s)      # 有序列表头
        kept.append(s)
    return "\n".join(kept).strip()


def _chunks(text, limit=60):
    """按句切，凑到 limit 字以内一段（misaki 长句慢且易飘）。"""
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    out, buf = [], ""
    for p in parts:
        if len(p) > limit:  # 超长单句按逗号再切
            sub = re.split(r"(?<=[，,、：:])", p)
            cur = ""
            for s in sub:
                if cur and len(cur) + len(s) > limit:
                    out.append(cur)
                    cur = s
                else:
                    cur += s
            if cur:
                out.append(cur)
            continue
        if buf and len(buf) + len(p) + 1 > limit:
            out.append(buf)
            buf = p
        else:
            buf = ("%s%s" % (buf, p)) if not buf else "%s。%s" % (buf, p)
    if buf:
        out.append(buf)
    return out


# ---------------------------------------------------------------- 人味韵律
_GAP_BASE = {"。": 0.42, "！": 0.34, "？": 0.40, "；": 0.30, "，": 0.16, "、": 0.12, "：": 0.22}


def _tail_punct(chunk):
    """取段尾标点（决定该段后的气口长短）。"""
    for ch in reversed(chunk):
        if ch in _GAP_BASE:
            return ch
        if "\u4e00" <= ch <= "\u9fff":
            return "。"
    return "。"


def _humanize_plan(chunks, seed):
    """给每段生成「语速 × 气口 × 响度」计划——模拟真人口播的节奏起伏：
    - 语速：每段 ±4% 抖动；疑问句放慢；开头第一句慢 5%（抓住注意力）
    - 气口：按段尾标点分级（句号>问号>逗号），±18% 抖动；换行（段落）额外加 0.25s
    - 响度：每段 0.95~1.05 微起伏，模拟气息强弱
    用 seed 固定随机序列，同稿重跑结果一致。"""
    rnd = random.Random(seed)
    plan = []
    for i, ch in enumerate(chunks):
        sp = rnd.uniform(0.96, 1.04)
        if ch.endswith(("？", "?")):
            sp *= 0.97                      # 疑问句稍微放慢
        if i == 0:
            sp *= 0.95                      # 开场句慢一点
        tail = _tail_punct(ch)
        gap = _GAP_BASE.get(tail, 0.42) * rnd.uniform(0.82, 1.18)
        plan.append({"speed": round(sp, 3), "gap": round(gap, 3),
                     "gain": round(rnd.uniform(0.95, 1.05), 3)})
    return plan


def _ff():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _save_bg(dataurl):
    os.makedirs(TTS_BG, exist_ok=True)
    m = re.match(r"data:image/(\w+);base64,(.*)", dataurl, re.S)
    if not m:
        raise ValueError("背景图 dataURL 格式不对")
    ext = "jpg" if m.group(1).lower() in ("jpeg", "jpg") else "png"
    path = os.path.join(TTS_BG, "bg_%s.%s" % (time.strftime("%m%d%H%M%S"), ext))
    with open(path, "wb") as f:
        f.write(base64.b64decode(m.group(2)))
    return path


def _make_video(audio_path, bg_path, aspect, out_path):
    """配音 + 背景图 → 初片（无字幕）。字幕交给块①烧，节奏交给 whisper。"""
    w, h = ASPECTS.get(aspect or "9:16", (1080, 1920))
    ff = _ff()
    if bg_path and os.path.isfile(bg_path):
        # 放大 1.25 倍再缓慢推进（Ken Burns），推近速度与时长自适应
        vf = ("scale=%d:%d:force_original_aspect_ratio=increase,"
              "crop=%d:%d,zoompan=z='min(zoom+0.0004,1.12)':d=1:s=%dx%d:fps=25,"
              "format=yuv420p" % (int(w * 1.25), int(h * 1.25), w, h, w, h))
        cmd = [ff, "-y", "-loop", "1", "-framerate", "25", "-i", bg_path,
               "-i", audio_path, "-vf", vf, "-tune", "stillimage",
               "-c:v", "libx264", "-preset", "fast", "-crf", "20",
               "-c:a", "aac", "-b:a", "128k", "-shortest", out_path]
    else:
        vf = "format=yuv420p"
        cmd = [ff, "-y", "-f", "lavfi", "-i",
               "color=c=0x16161e:s=%dx%d:r=25" % (w, h),
               "-i", audio_path, "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", "-crf", "20",
               "-c:a", "aac", "-b:a", "128k", "-shortest", out_path]
    import subprocess
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", creationflags=0x08000000)
    if p.returncode != 0 or not os.path.isfile(out_path):
        tail = (p.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("合成初片失败：%s" % (" | ".join(tail) or p.returncode))


def _worker(text, voice, speed, make_video, bg_path, aspect):
    with _LOCK:
        _STATE["started"] = time.time()
    t0 = time.time()
    ok, err, audio_path, video_path = False, "", "", ""
    try:
        if not os.path.isfile(MODEL_PATH):
            raise RuntimeError("模型文件缺失：%s" % MODEL_PATH)
        kokoro = _load_engine()
        with _LOCK:
            _STATE["stage"] = "发音规划"
        _log("文案 %d 字 → 切句规划…" % len(text))
        chunks = _chunks(text)
        _log("共 %d 段，音色 %s，语速 %.2fx" % (len(chunks), voice, speed))
        if voice not in kokoro.voices:
            alt = next((k for k in kokoro.voices if k.startswith(voice[:2])), None)
            if not alt:
                raise RuntimeError("音色 %s 不存在" % voice)
            _log("音色 %s 不在库里，自动换成 %s" % (voice, alt))
            voice = alt

        os.makedirs(TTS_OUT, exist_ok=True)
        with _LOCK:
            _STATE["stage"] = "合成语音"
        import numpy as np
        import soundfile as sf
        plan = _humanize_plan(chunks, seed=len(text))
        pieces, sr = [], None
        for i, ch in enumerate(chunks):
            ps = _zhuyin(ch)
            if not ps:
                _log("第 %d 段没出音素，跳过：%s" % (i + 1, ch[:20]))
                continue
            pp = plan[i] if i < len(plan) else {"speed": speed, "gap": 0.3, "gain": 1.0}
            samples, rate = kokoro.create(ps, voice=voice, speed=speed * pp["speed"],
                                          is_phonemes=True)
            if sr is None:
                sr = rate
            samples = samples * pp["gain"]          # 响度微起伏（模拟气息）
            pieces.append(samples)
            pieces.append(np.zeros(int(rate * pp["gap"]), dtype=samples.dtype))  # 气口按标点分级
            _log("合成 %d/%d（语速%.2f 气口%.2fs，累计 %.1f 秒）" % (
                i + 1, len(chunks), pp["speed"], pp["gap"],
                len(np.concatenate(pieces)) / rate))
        if not pieces:
            raise RuntimeError("一句都没合成出来（音素全为空），检查文案内容")
        audio = np.concatenate(pieces)
        stem = time.strftime("%m%d-%H%M%S")
        audio_path = os.path.join(TTS_OUT, "配音_%s_%s.wav" % (stem, voice))
        sf.write(audio_path, audio, sr)
        _log("配音完成：%s（%.1f 秒）" % (os.path.basename(audio_path), len(audio) / sr))

        if make_video:
            with _LOCK:
                _STATE["stage"] = "合成初片"
            _log("配背景画面 → 初片（%s）…" % (aspect or "9:16"))
            video_path = os.path.join(TTS_OUT, "初片_%s.mp4" % stem)
            _make_video(audio_path, bg_path, aspect, video_path)
            _log("初片完成：%s" % os.path.basename(video_path))
        ok = True
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        _log("失败：%s" % err)
    with _LOCK:
        _STATE.update({
            "running": False, "done": True, "ok": ok,
            "audio": audio_path, "video": video_path,
            "elapsed": time.time() - t0, "stage": "",
        })


def run(payload):
    text = clean_script(payload.get("text") or "")
    if len(re.sub(r"\s", "", text)) < 20:
        return {"error": "文案太短（有效字数少于 20），先在文案页把稿子弄好，或直接粘口播文本。"}
    voice = (payload.get("voice") or "zf_001").strip()
    try:
        speed = max(0.7, min(1.5, float(payload.get("speed") or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    make_video = bool(payload.get("makeVideo"))
    aspect = payload.get("aspect") or "9:16"
    bg_path = ""
    if make_video and payload.get("bg"):
        try:
            bg_path = _save_bg(payload["bg"])
            _log("背景图已存：%s" % os.path.basename(bg_path))
        except Exception as exc:  # noqa: BLE001
            _log("背景图存不了（%s），改用纯色背景" % exc)
    with _LOCK:
        _STATE.update({
            "running": True, "done": False, "ok": False, "lines": [],
            "stage": "准备", "audio": "", "video": "",
            "elapsed": 0.0, "started": time.time(),
        })
    threading.Thread(target=_worker,
                     args=(text, voice, speed, make_video, bg_path, aspect),
                     daemon=True).start()
    return {"ok": True}
