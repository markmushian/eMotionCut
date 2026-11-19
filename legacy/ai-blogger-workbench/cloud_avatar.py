# -*- coding: utf-8 -*-
"""
云数字人引擎 —— 阿里云 OSS + 万相数字人 wan2.2-s2v。

解决本地 Wav2Lip「只动嘴、脸僵」的问题：阿里万相基于单张图片 + 一段音频，
生成动作自然（头部/身体/表情联动，不只嘴）的说话视频。

链路：
  照片 + 文案
    → 本机 kokoro 配音（复用 avatar_studio._synthesize）
    → 按 <20 秒切段（万相硬限制：单段音频 <20 秒、<15MB）
    → 逐段传阿里云 OSS（公共读 Bucket）拿公网 URL
    → 调 wan2.2-s2v-detect 校验图片
    → 调 wan2.2-s2v 异步生成（图片 URL + 音频 URL）
    → 轮询任务直到 SUCCEEDED
    → 下载每段视频 → ffmpeg 拼接 → 最终成片

配置：本机 cloud_avatar.json，存 OSS AK/SK/Bucket + 万相 API Key，
不落日志、不上传。用 avatar_studio 的 job 进度机制对外暴露进度。

依赖：oss2（阿里云 OSS SDK）、requests。
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "cloud_avatar.json")

# 万相 wan2.2-s2v 硬限制（官方文档）
MAX_AUDIO_SECONDS = 19        # 留 1 秒余量，切段上限
MAX_AUDIO_BYTES = 14 * 1024 * 1024   # 15MB 上限留余量

DASHSCOPE_HOST = "https://dashscope.aliyuncs.com"
DETECT_MODEL = "wan2.2-s2v-detect"
GEN_MODEL = "wan2.2-s2v"


# --------------------------------------------------------------------------
# 配置读写
# --------------------------------------------------------------------------
def _default_cfg():
    return {
        "oss": {
            "access_key_id": "",
            "access_key_secret": "",
            "bucket": "",
            "endpoint": "",          # 例如 oss-cn-hangzhou.aliyuncs.com
            "public_url": "",        # 例如 https://bucket.oss-cn-hangzhou.aliyuncs.com
        },
        "dashscope": {
            "api_key": "",
        },
        "resolution": "480P",        # 480P 0.5元/秒 / 720P 0.9元/秒
    }


def load_cfg():
    if os.path.isfile(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            loaded = {}
        cfg = _default_cfg()
        # 浅合并，保留默认字段结构
        for top in ("oss", "dashscope"):
            if isinstance(loaded.get(top), dict):
                cfg[top].update(loaded[top])
        if "resolution" in loaded:
            cfg["resolution"] = loaded["resolution"]
        return cfg
    return _default_cfg()


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def is_configured(cfg=None):
    """是否已配齐（能跑）。返回 (ok, 缺什么)。"""
    cfg = cfg or load_cfg()
    missing = []
    o, d = cfg.get("oss", {}), cfg.get("dashscope", {})
    if not o.get("access_key_id"):
        missing.append("OSS AccessKey ID")
    if not o.get("access_key_secret"):
        missing.append("OSS AccessKey Secret")
    if not o.get("bucket"):
        missing.append("OSS Bucket 名称")
    if not o.get("endpoint"):
        missing.append("OSS Endpoint")
    if not d.get("api_key"):
        missing.append("万相 API Key")
    return (len(missing) == 0, missing)


# --------------------------------------------------------------------------
# OSS 上传
# --------------------------------------------------------------------------
def _oss_client(cfg):
    import oss2
    o = cfg["oss"]
    auth = oss2.Auth(o["access_key_id"], o["access_key_secret"])
    return oss2.Bucket(auth, o["endpoint"], o["bucket"])


def upload_to_oss(cfg, local_path, object_key, content_type):
    """上传本地文件到 OSS，返回公网 URL。"""
    import oss2
    bucket = _oss_client(cfg)
    bucket.put_object_from_file(object_key, local_path,
                                headers={"Content-Type": content_type})
    o = cfg["oss"]
    # public_url 优先；否则用 endpoint 拼
    base = (o.get("public_url") or "").rstrip("/")
    if not base:
        # endpoint 形如 oss-cn-hangzhou.aliyuncs.com → https://bucket.endpoint
        base = "https://%s.%s" % (o["bucket"], o["endpoint"])
    return "%s/%s" % (base, object_key)


# --------------------------------------------------------------------------
# 万相 DashScope 调用
# --------------------------------------------------------------------------
def _dash_headers(api_key):
    return {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }


def detect_image(cfg, image_url):
    """步骤1：校验图片合规，返回 task_id。"""
    import requests
    resp = requests.post(
        DASHSCOPE_HOST + "/api/v1/services/aigc/image2video/video-synthesis/",
        headers=_dash_headers(cfg["dashscope"]["api_key"]),
        json={"model": DETECT_MODEL, "input": {"image_url": image_url}},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    # 同步接口，通常直接返回 output 里的结果，也可能返回 task
    if data.get("output"):
        return data["output"]
    if data.get("data", {}).get("task_id"):
        return {"task_id": data["data"]["task_id"]}
    raise RuntimeError("图片检测返回异常：" + json.dumps(data, ensure_ascii=False)[:300])


def submit_generate(cfg, image_url, audio_url):
    """步骤2：提交异步视频生成，返回 task_id。"""
    import requests
    payload = {
        "model": GEN_MODEL,
        "input": {"image_url": image_url, "audio_url": audio_url},
        "parameters": {"resolution": cfg.get("resolution", "480P")},
    }
    resp = requests.post(
        DASHSCOPE_HOST + "/api/v1/services/aigc/image2video/video-synthesis/",
        headers=_dash_headers(cfg["dashscope"]["api_key"]),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    task_id = (data.get("output", {}).get("task_id")
               or data.get("data", {}).get("task_id"))
    if not task_id:
        raise RuntimeError("生成任务提交失败：" + json.dumps(data, ensure_ascii=False)[:300])
    return task_id


def poll_task(cfg, task_id, max_wait=600, on_progress=None):
    """轮询任务直到 SUCCEEDED/FAILED，返回 video_url。"""
    import requests
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(
            DASHSCOPE_HOST + "/api/v1/tasks/" + task_id,
            headers={"Authorization": "Bearer " + cfg["dashscope"]["api_key"]},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        out = data.get("output", {})
        status = out.get("task_status") or data.get("data", {}).get("task_status")
        if status == "SUCCEEDED":
            video_url = (out.get("video_url")
                         or (out.get("results") or [{}])[0].get("url"))
            if not video_url:
                # 兼容不同字段
                video_url = _find_video_url(out)
            if not video_url:
                raise RuntimeError("任务成功但未返回视频地址")
            return video_url
        if status in ("FAILED", "CANCELED"):
            msg = out.get("message") or json.dumps(data, ensure_ascii=False)[:300]
            raise RuntimeError("生成失败：" + msg)
        if on_progress:
            on_progress(status)
        time.sleep(4)
    raise TimeoutError("生成超时（>%d 秒）" % max_wait)


def _find_video_url(out):
    """在返回的 output 里递归找视频 URL。"""
    for key in ("video_url", "url", "video"):
        if isinstance(out.get(key), str) and out[key].startswith("http"):
            return out[key]
    # 遍历 dict/list
    stack = [out]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for v in node.values():
                if isinstance(v, str) and v.startswith("http") and any(
                        ext in v.lower() for ext in (".mp4", ".mov", ".webm")):
                    return v
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def download_file(url, dst):
    """下载 URL 到本地文件。"""
    import requests
    resp = requests.get(url, timeout=300, stream=True)
    resp.raise_for_status()
    with open(dst, "wb") as f:
        for chunk in resp.iter_content(65536):
            f.write(chunk)


# --------------------------------------------------------------------------
# 音频切段（万相要求单段 <20 秒）
# --------------------------------------------------------------------------
def split_audio(wav_path, max_sec=MAX_AUDIO_SECONDS, out_dir=None):
    """按时间切段，返回 [(段wav路径, 段时长秒), ...]。"""
    out_dir = out_dir or os.path.dirname(wav_path)
    import wave
    with wave.open(wav_path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
    total = frames / rate
    segments = []
    pos = 0.0
    idx = 0
    ff = _ffmpeg()
    while pos < total:
        dur = min(max_sec, total - pos)
        seg = os.path.join(out_dir, "段_%s_%02d.wav" % (uuid.uuid4().hex[:8], idx))
        cmd = [ff, "-y", "-ss", str(pos), "-t", str(dur),
               "-i", wav_path, "-ar", "44100", "-ac", "1", seg]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore",
                           creationflags=0x08000000)
        if p.returncode != 0 or not os.path.isfile(seg):
            raise RuntimeError("音频切段失败：" + (p.stderr or "")[-200:])
        segments.append((seg, dur))
        pos += dur
        idx += 1
    return segments


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def concat_videos(video_paths, out_path):
    """用 concat demuxer 无损拼接多段视频（同编码参数）。"""
    ff = _ffmpeg()
    listfile = os.path.join(os.path.dirname(out_path),
                            "拼接_%s.txt" % uuid.uuid4().hex[:8])
    with open(listfile, "w", encoding="utf-8") as f:
        for v in video_paths:
            f.write("file '%s'\n" % v.replace("'", "\\'"))
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
           "-c", "copy", out_path]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore",
                       creationflags=0x08000000)
    os.remove(listfile)
    if p.returncode != 0 or not os.path.isfile(out_path):
        # 编码参数不一致时 concat -c copy 会失败，降级重编码
        return concat_reencode(video_paths, out_path)
    return out_path


def concat_reencode(video_paths, out_path):
    """concat -c copy 失败时，用 filter_complex 重编码拼接。"""
    ff = _ffmpeg()
    inputs = []
    for v in video_paths:
        inputs += ["-i", v]
    # 统一转 1280x720 30fps，避免分辨率/帧率不一致
    filters = []
    for i in range(len(video_paths)):
        filters.append("[%d:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v%d]" % (i, i))
    concat_in = "".join("[v%d]" % i for i in range(len(video_paths)))
    filters.append("%sconcat=n=%d:v=1:a=0[outv]" % (concat_in, len(video_paths)))
    cmd = [ff, "-y"] + inputs + ["-filter_complex", ";".join(filters),
                                 "-map", "[outv]", "-c:v", "libx264",
                                 "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                 out_path]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore",
                       creationflags=0x08000000)
    if p.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError("视频拼接失败：" + (p.stderr or "")[-300:])
    return out_path
