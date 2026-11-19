# -*- coding: utf-8 -*-
"""
Wav2Lip 数字人/对口型桥接层。

工作台主环境是 Python 3.13，Wav2Lip 老项目跑在独立的 3.12 环境里
（E:\\AI小程序\\AI博主创作工具\\数字人引擎\\wav2lip-env）。
本模块只负责：拼参数 → subprocess 调独立环境 → 回传产物路径，
不让老依赖污染主 venv。

两种用法：
  avatar(face_image, audio)   -> 图片 + 音频 -> 数字人开口说话（--static）
  lipsync(face_video, audio)  -> 真人视频 + 音频 -> 对口型
"""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_ROOT = r"E:\AI小程序\AI博主创作工具\数字人引擎"
WAV2LIP_DIR = os.path.join(ENGINE_ROOT, "audio-driven-lipsync-tool")
ENV_PY = os.path.join(ENGINE_ROOT, "wav2lip-env", "Scripts", "python.exe")
CKPT = os.path.join(WAV2LIP_DIR, "checkpoints", "wav2lip.pth")
CKPT_GAN = os.path.join(WAV2LIP_DIR, "checkpoints", "wav2lip_gan.pth")


def _run(args, timeout=1800):
    """调独立环境的 inference.py，返回 (ok, outfile, stderr_tail)。"""
    cmd = [ENV_PY, "-X", "utf8", os.path.join(WAV2LIP_DIR, "inference.py")] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=WAV2LIP_DIR, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, "", "超时（CPU 渲染较慢）"
    tail = (r.stderr or r.stdout or "")[-800:]
    return (r.returncode == 0), "", tail


def avatar(face_image, audio, outfile, gan=False):
    """图片 -> 数字人口播。返回 (ok, err)。"""
    ckpt = CKPT_GAN if gan else CKPT
    args = ["--checkpoint_path", ckpt, "--face", face_image,
            "--audio", audio, "--outfile", outfile, "--static", "True"]
    ok, _, tail = _run(args)
    if not ok:
        return False, "数字人合成失败：" + tail
    if not os.path.isfile(outfile) or os.path.getsize(outfile) == 0:
        return False, "数字人合成失败：未产出视频。"
    return True, ""


def lipsync(face_video, audio, outfile, gan=False):
    """真人视频 -> 对口型。返回 (ok, err)。"""
    ckpt = CKPT_GAN if gan else CKPT
    args = ["--checkpoint_path", ckpt, "--face", face_video,
            "--audio", audio, "--outfile", outfile,
            "--pads", "0", "10", "0", "0"]
    ok, _, tail = _run(args)
    if not ok:
        return False, "对口型合成失败：" + tail
    if not os.path.isfile(outfile) or os.path.getsize(outfile) == 0:
        return False, "对口型合成失败：未产出视频。"
    return True, ""


def available():
    """引擎是否就绪（独立环境 + 权重都在）。"""
    return (os.path.isfile(ENV_PY) and os.path.isfile(CKPT)
            and os.path.isfile(os.path.join(WAV2LIP_DIR, "face_detection", "detection", "sfd", "s3fd.pth")))
