# -*- coding: utf-8 -*-
"""
数字人 / 对口型 制作模块。

两条链路：
  avatar（数字人）：人物图 + 文案 → 本机 kokoro 配音 → Wav2Lip 让照片开口
  lipsync（对口型）：真人视频 + 音频 → Wav2Lip 对口型

产物统一落到 成片库/数字人/ 目录，走 job 进度查询（复用 media_studio 的 job 语义，
但独立成模块，不污染剪辑工程）。
"""
import json
import os
import shutil
import threading
import time
import uuid

import ttsfree

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "成片库", "数字人")
os.makedirs(OUT_DIR, exist_ok=True)

JOBS = {}
JLOCK = threading.Lock()
ACTIVE = {"queued", "running"}


def _ff():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _to_wav(src, dst, sr=16000):
    """把任意音频转成 16k 单声道 wav（Wav2Lip 硬性要求）。"""
    import subprocess
    cmd = [_ff(), "-y", "-i", src, "-ar", str(sr), "-ac", "1", dst]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", creationflags=0x08000000)
    if p.returncode != 0 or not os.path.isfile(dst):
        raise RuntimeError("音频转码失败：" + (p.stderr or "")[-200:])


def _synthesize(text, voice, speed):
    """用本机 kokoro 同步配音，返回 (wav_path, dur_sec)。"""
    import numpy as np
    import soundfile as sf
    kokoro = ttsfree._load_engine()
    if voice not in kokoro.voices:
        alt = next((k for k in kokoro.voices if k.startswith(voice[:2])), None)
        if not alt:
            raise RuntimeError("音色 %s 不存在" % voice)
        voice = alt
    sents = [p.strip() for p in __import__('re').split(r"(?<=[。！？；])", text) if p.strip()]
    if not sents:
        raise RuntimeError("文案切不出句子")
    plan = ttsfree._humanize_plan(sents, seed=len(text))
    pieces, sr = [], None
    for i, sent in enumerate(sents):
        ps = ttsfree._zhuyin(sent)
        if not ps:
            continue
        pp = plan[i] if i < len(plan) else {"speed": 1.0, "gap": 0.3, "gain": 1.0}
        samples, rate = kokoro.create(ps, voice=voice, speed=speed * pp["speed"], is_phonemes=True)
        if sr is None:
            sr = rate
        pieces.append(samples * pp["gain"])
        pieces.append(np.zeros(int(rate * pp["gap"]), dtype=samples.dtype))
    if not pieces:
        raise RuntimeError("一句都没合成出来")
    audio = np.concatenate(pieces)
    out = os.path.join(OUT_DIR, "配音_%s.wav" % uuid.uuid4().hex[:10])
    sf.write(out, audio, sr)
    return out, len(audio) / sr


def _save_upload(b64_or_path, ext, sub="素材"):
    """把 base64 图片 或 本地路径 落盘，返回绝对路径。"""
    import base64
    if isinstance(b64_or_path, str) and os.path.isfile(b64_or_path):
        return b64_or_path
    data = b64_or_path
    if isinstance(data, str):
        # base64 data URL 或纯 base64
        if data.startswith("data:"):
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
    else:
        raw = data
    out = os.path.join(OUT_DIR, "%s_%s%s" % (sub, uuid.uuid4().hex[:10], ext))
    with open(out, "wb") as f:
        f.write(raw)
    return out


def _face_to_manageable(path, max_side=768):
    """把超大人脸图缩到 Wav2Lip 友好的尺寸，避免 cv2 检测失败。返回路径。"""
    import subprocess
    out = os.path.join(OUT_DIR, "脸_%s.jpg" % uuid.uuid4().hex[:10])
    cmd = [_ff(), "-y", "-i", path, "-vf", "scale='min(%d,iw)':-2" % max_side, out]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", creationflags=0x08000000)
    if p.returncode == 0 and os.path.isfile(out):
        return out
    return path  # 失败就原样


def _job(jid):
    with JLOCK:
        return dict(JOBS[jid])


def _set(jid, **kw):
    with JLOCK:
        JOBS[jid].update(kw)


def _new_job(name, kind):
    jid = uuid.uuid4().hex[:24]
    with JLOCK:
        JOBS[jid] = {"id": jid, "name": name, "kind": kind, "status": "queued",
                     "pct": 0, "message": "排队中", "created": time.time(),
                     "result": None, "error": ""}
    return jid


def list_jobs():
    with JLOCK:
        return sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:50]


def start_avatar(payload):
    """人物图(base64/路径) + 文案 → 数字人口播。"""
    text = str(payload.get("text") or "").strip()
    if len(text.replace(" ", "")) < 20:
        raise ValueError("文案太短（有效字数少于 20）")
    face = payload.get("face") or payload.get("image")
    if not face:
        raise ValueError("请上传一张清晰正脸照片")
    import wav2lip_bridge as wl
    if not wl.available():
        raise ValueError("数字人引擎未就绪（独立环境或权重缺失）")
    jid = _new_job("数字人：" + text[:16], "avatar")
    t = threading.Thread(target=_avatar_worker, args=(jid, payload), daemon=True)
    t.start()
    return _job(jid)


def _avatar_worker(jid, payload):
    try:
        import wav2lip_bridge as wl
        text = str(payload.get("text") or "").strip()
        voice = str(payload.get("voice") or "zf_001")
        speed = float(payload.get("speed") or 1.0)

        _set(jid, status="running", pct=8, message="本机生成配音")
        audio, dur = _synthesize(text, voice, speed)
        wav16 = os.path.join(OUT_DIR, "音_%s.wav" % uuid.uuid4().hex[:10])
        _to_wav(audio, wav16, 16000)

        _set(jid, pct=25, message="处理人脸照片")
        face = _save_upload(payload.get("face") or payload.get("image"), ".jpg", "脸")
        face = _face_to_manageable(face)

        _set(jid, pct=35, message="Wav2Lip 合成口型（CPU 渲染，约每 10 秒口播需 1-2 分钟）")
        out = os.path.join(OUT_DIR, "数字人_%s.mp4" % uuid.uuid4().hex[:10])
        ok, err = wl.avatar(face, wav16, out, gan=bool(payload.get("gan")))
        if not ok:
            raise RuntimeError(err)

        _set(jid, status="done", pct=100, message="数字人成片已就绪",
             result={"video": "/api/avatar/media?id=" + os.path.basename(out),
                     "duration": round(dur, 2)})
    except Exception as exc:
        _set(jid, status="failed", message=str(exc)[:2000])


def start_lipsync(payload):
    """真人视频(路径/base64) + 音频(路径/base64) → 对口型。"""
    video = payload.get("video")
    audio = payload.get("audio")
    if not video:
        raise ValueError("请上传真人视频")
    if not audio:
        raise ValueError("请上传音频")
    import wav2lip_bridge as wl
    if not wl.available():
        raise ValueError("数字人引擎未就绪")
    jid = _new_job("对口型", "lipsync")
    t = threading.Thread(target=_lipsync_worker, args=(jid, payload), daemon=True)
    t.start()
    return _job(jid)


def _lipsync_worker(jid, payload):
    try:
        import wav2lip_bridge as wl
        _set(jid, status="running", pct=10, message="准备素材")
        video = _save_upload(payload.get("video"), ".mp4", "视频")
        audio = _save_upload(payload.get("audio"), ".wav", "音频")
        wav16 = os.path.join(OUT_DIR, "音_%s.wav" % uuid.uuid4().hex[:10])
        _to_wav(audio, wav16, 16000)

        _set(jid, pct=35, message="Wav2Lip 对口型合成（CPU 渲染，耗时取决于视频时长）")
        out = os.path.join(OUT_DIR, "对口型_%s.mp4" % uuid.uuid4().hex[:10])
        ok, err = wl.lipsync(video, wav16, out, gan=bool(payload.get("gan")))
        if not ok:
            raise RuntimeError(err)

        _set(jid, status="done", pct=100, message="对口型成片已就绪",
             result={"video": "/api/avatar/media?id=" + os.path.basename(out)})
    except Exception as exc:
        _set(jid, status="failed", message=str(exc)[:2000])


def media_file(name):
    """按文件名从 成片库/数字人 取文件，供前端播放/下载。"""
    name = os.path.basename(name)
    p = os.path.join(OUT_DIR, name)
    if not os.path.isfile(p):
        raise FileNotFoundError("文件不存在")
    return p


# --------------------------------------------------------------------------
# 云数字人（阿里云 OSS + 万相 wan2.2-s2v）
# --------------------------------------------------------------------------
def start_cloud_avatar(payload):
    """人物图 + 文案 → 云数字人（动作自然，不只动嘴）。"""
    import cloud_avatar as ca
    text = str(payload.get("text") or "").strip()
    if len(text.replace(" ", "")) < 20:
        raise ValueError("文案太短（有效字数少于 20）")
    face = payload.get("face") or payload.get("image")
    if not face:
        raise ValueError("请上传一张清晰正脸照片")
    ok, missing = ca.is_configured()
    if not ok:
        raise ValueError("云数字人尚未配齐，缺少：" + "、".join(missing))
    jid = _new_job("云数字人：" + text[:16], "cloud_avatar")
    t = threading.Thread(target=_cloud_avatar_worker, args=(jid, payload), daemon=True)
    t.start()
    return _job(jid)


def _cloud_avatar_worker(jid, payload):
    import cloud_avatar as ca
    cfg = ca.load_cfg()
    tmpdir = os.path.join(OUT_DIR, "云_%s" % uuid.uuid4().hex[:8])
    os.makedirs(tmpdir, exist_ok=True)
    try:
        text = str(payload.get("text") or "").strip()
        voice = str(payload.get("voice") or "zf_001")
        speed = float(payload.get("speed") or 1.0)

        _set(jid, status="running", pct=8, message="本机生成配音")
        audio, dur = _synthesize(text, voice, speed)
        wav16 = os.path.join(tmpdir, "配音.wav")
        _to_wav(audio, wav16, 16000)

        _set(jid, pct=20, message="上传人脸照片到云存储")
        face = _save_upload(payload.get("face") or payload.get("image"), ".jpg", "脸")
        face = _face_to_manageable(face, max_side=1024)
        face_key = "avatar/face_%s.jpg" % uuid.uuid4().hex[:10]
        face_url = ca.upload_to_oss(cfg, face, face_key, "image/jpeg")

        _set(jid, pct=28, message="校验图片合规")
        ca.detect_image(cfg, face_url)

        # 切段
        _set(jid, pct=32, message="切分音频（万相要求单段 <20 秒）")
        segs = ca.split_audio(wav16, out_dir=tmpdir)

        # 逐段：上传音频 → 生成 → 轮询 → 下载
        parts = []
        n = len(segs)
        for i, (seg, segdur) in enumerate(segs):
            base = 35 + int(60 * i / max(n, 1))
            _set(jid, pct=base, message="上传音频 %d/%d" % (i + 1, n))
            akey = "avatar/audio_%s_%02d.wav" % (uuid.uuid4().hex[:8], i)
            audio_url = ca.upload_to_oss(cfg, seg, akey, "audio/wav")

            _set(jid, pct=base + 4, message="万相生成第 %d/%d 段（云端渲染，约 1-3 分钟）" % (i + 1, n))
            task_id = ca.submit_generate(cfg, face_url, audio_url)

            def _pollcb(st):
                _set(jid, message="第 %d/%d 段：%s" % (i + 1, n, st or "生成中"))

            video_url = ca.poll_task(cfg, task_id, on_progress=_pollcb)

            _set(jid, pct=base + 8, message="下载第 %d/%d 段" % (i + 1, n))
            part = os.path.join(tmpdir, "part_%02d.mp4" % i)
            ca.download_file(video_url, part)
            parts.append(part)

        _set(jid, pct=96, message="拼接成片")
        out = os.path.join(OUT_DIR, "云数字人_%s.mp4" % uuid.uuid4().hex[:10])
        if len(parts) == 1:
            shutil.move(parts[0], out)
        else:
            ca.concat_videos(parts, out)

        _set(jid, status="done", pct=100, message="云数字人成片已就绪",
             result={"video": "/api/avatar/media?id=" + os.path.basename(out),
                     "duration": round(dur, 2), "segments": n,
                     "engine": "wan2.2-s2v"})
    except Exception as exc:
        _set(jid, status="failed", message=str(exc)[:2000])
    finally:
        # 清理临时段文件（保留最终成片）
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass


def handle(handler, method, path, query):
    """路由适配：/api/avatar/* 交给本模块。返回 True 表示已处理。"""
    if not path.startswith('/api/avatar'):
        return False
    arg = lambda k: (query.get(k) or [''])[0]
    try:
        if method == 'GET':
            if path == '/api/avatar/jobs':
                handler.send_json({'items': list_jobs()})
            elif path == '/api/avatar/media':
                handler.send_file(media_file(arg('id')))
            elif path == '/api/avatar/ready':
                import wav2lip_bridge as wl
                handler.send_json({'ready': wl.available()})
            elif path == '/api/avatar/cloud/ready':
                import cloud_avatar as ca
                ok, missing = ca.is_configured()
                handler.send_json({'ready': ok, 'missing': missing})
            elif path == '/api/avatar/cloud/config':
                import cloud_avatar as ca
                cfg = ca.load_cfg()
                # 回传时打码 Secret/Key，只暴露是否已填
                safe = {
                    "oss": {
                        "access_key_id": cfg["oss"]["access_key_id"],
                        "access_key_secret": ("****" if cfg["oss"]["access_key_secret"] else ""),
                        "bucket": cfg["oss"]["bucket"],
                        "endpoint": cfg["oss"]["endpoint"],
                        "public_url": cfg["oss"]["public_url"],
                    },
                    "dashscope": {
                        "api_key": ("****" if cfg["dashscope"]["api_key"] else ""),
                    },
                    "resolution": cfg.get("resolution", "480P"),
                }
                handler.send_json(safe)
            else:
                handler.send_json({'error': '接口不存在'}, 404)
        else:  # POST
            b = handler.read_json()
            if path == '/api/avatar/start':
                handler.send_json(start_avatar(b))
            elif path == '/api/avatar/lipsync':
                handler.send_json(start_lipsync(b))
            elif path == '/api/avatar/cloud':
                handler.send_json(start_cloud_avatar(b))
            elif path == '/api/avatar/cloud/config':
                import cloud_avatar as ca
                cfg = ca.load_cfg()
                o = b.get("oss") or {}
                d = b.get("dashscope") or {}
                # 只覆盖非空、非打码字段
                for k in ("access_key_id", "access_key_secret", "bucket",
                          "endpoint", "public_url"):
                    if o.get(k) and o[k] != "****":
                        cfg["oss"][k] = o[k]
                if d.get("api_key") and d["api_key"] != "****":
                    cfg["dashscope"]["api_key"] = d["api_key"]
                if b.get("resolution") in ("480P", "720P"):
                    cfg["resolution"] = b["resolution"]
                ca.save_cfg(cfg)
                handler.send_json({"ok": True})
            else:
                handler.send_json({'error': '接口不存在'}, 404)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        handler.send_json({'error': str(exc)}, 400)
    except Exception as exc:
        handler.send_json({'error': '处理失败：' + str(exc)}, 500)
    return True
