# -*- coding: utf-8 -*-
"""自动剪辑流水线 · 第一步：视频 → whisper 转写 → SRT

复用 全自动生产线/口播一键成片/pipeline.py 已验证的转写链路：
  - faster-whisper small（本地离线，HF_HUB_OFFLINE=1）
  - FFmpeg 解 16k float32 直喂 transcribe（绕开 PyAV）
  - 字级时间戳 → build_cues 按停顿/长度断句

用法:
  python auto_director.py <输入视频.mp4> <输出.srt> [model尺寸]
输出: SRT 文件；stdout 末行 "OK <条数>" 供调用方校验。
"""
import os
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

KOUBAO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "全自动生产线", "口播一键成片")
sys.path.insert(0, KOUBAO)
import pipeline  # noqa: E402  模块级只执行 find_ffmpeg，无副作用


def srt_ts(t):
    """秒 → SRT 时间 00:00:01,600"""
    ms = int(round(max(0.0, float(t)) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def main():
    if len(sys.argv) < 3:
        print("用法: auto_director.py <视频> <输出.srt> [model]")
        raise SystemExit(1)
    src, dst = sys.argv[1], sys.argv[2]
    size = sys.argv[3] if len(sys.argv) > 3 else "small"
    if not os.path.isfile(src):
        print("! 输入视频不存在: " + src)
        raise SystemExit(1)

    t0 = time.time()
    print("[1/2] 加载 whisper %s + 识别中（CPU，几分钟属正常）..." % size, flush=True)
    _, words = pipeline.transcribe(src, size=size)
    if not words:
        print("! 没识别到任何语音（视频没有有效人声？）")
        raise SystemExit(2)
    cues = pipeline.build_cues(words)
    if not cues:
        print("! 识别到了语音但断句为空（异常）")
        raise SystemExit(2)

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        for i, (a, b, txt) in enumerate(cues, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (i, srt_ts(a), srt_ts(b), txt.strip()))
    print("[2/2] 断句 %d 条 · 用时 %.0fs → %s" % (len(cues), time.time() - t0, dst),
          flush=True)
    print("OK %d" % len(cues))


if __name__ == "__main__":
    main()
