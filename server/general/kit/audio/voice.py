"""Chinese voice-over that does not sound like 2010.

`core/tts/tts_zh.py` in the upstream kit uses edge-tts; this build had no copy, so films were narrated by
Windows SAPI (`System.Speech`) — a concatenative engine whose Chinese voices are intelligible but robotic,
with flat prosody and no breath. This script is the local stand-in: edge-tts (Microsoft neural voices) with
the same outputs, plus the one thing a film actually needs — every line comes back at the length the
timeline already reserved for it.

    python voice.py lines.json assets/voice --voice zh-CN-YunxiNeural --rate +4%

lines.json:  [{"id": "v01", "text": "昨天有粉丝跟我吐槽。", "duration": 2.8}, …]
             `duration` is optional; without it the wav is as long as the voice needs.
Writes `v01.wav` … at 44100 Hz mono 16-bit (what the page and ffmpeg both expect) and `voice.json`
with the real length of each line.

A line shorter than its slot is padded with silence at the tail (the pause is natural and lands before the
next cut); a line longer than its slot is time-compressed, but never by more than MAX_TEMPO, because past
that the voice starts to chip. When it still does not fit, the line is reported as `over` and the caller
decides: shorten the sentence, or move the cut.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys

SR = 44100
MAX_TEMPO = 1.12  # beyond this atempo smears consonants
MIN_TEMPO = 0.94


def ffmpeg():
    for name in ('ffmpeg.exe', 'ffmpeg'):
        found = shutil.which(name)
        if found:
            return found
    override = os.environ.get('EMOTIONCUT_FFMPEG')
    if override and os.path.isfile(override):
        return override
    raise SystemExit('找不到 ffmpeg：请设置 EMOTIONCUT_FFMPEG 指向可执行文件')


def probe(path):
    out = subprocess.run(
        [ffmpeg().replace('ffmpeg.exe', 'ffprobe.exe').replace(os.sep + 'ffmpeg', os.sep + 'ffprobe'),
         '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


async def synth(text, out_mp3, voice, rate, volume='+0%', pitch='+0Hz'):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
    await communicate.save(out_mp3)


def to_wav(mp3, wav, duration=None, tempo=1.0):
    """mp3 → 44100 mono s16 wav; optionally stretched by `tempo` and padded/cut to `duration`."""
    filters = []
    if abs(tempo - 1.0) > 0.001:
        filters.append(f'atempo={tempo:.4f}')
    if duration:
        # apad keeps the tail silent up to `duration`; atrim guards against a line that is still too long
        filters.append(f'apad,atrim=0:{duration:.3f}')
    args = [ffmpeg(), '-v', 'error', '-y', '-i', mp3, '-ac', '1', '-ar', str(SR), '-c:a', 'pcm_s16le']
    if filters:
        args += ['-af', ','.join(filters)]
    args.append(wav)
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('lines', help='lines.json')
    parser.add_argument('outdir', help='输出目录，例如 assets/voice')
    parser.add_argument('--voice', default=os.environ.get('EMOTIONCUT_TTS_VOICE', 'zh-CN-YunxiNeural'))
    parser.add_argument('--rate', default=os.environ.get('EMOTIONCUT_TTS_RATE', '+4%'))
    parser.add_argument('--pitch', default='+0Hz')
    parser.add_argument('--volume', default='+0%')
    parser.add_argument('--dry-run', action='store_true', help='只报时长，不写文件')
    args = parser.parse_args()

    try:
        import edge_tts  # noqa: F401
    except ImportError:
        raise SystemExit('缺少 edge-tts：先运行 `python -m pip install edge-tts`（需要网络），再重跑本脚本')

    with open(args.lines, encoding='utf-8') as handle:
        lines = json.load(handle)
    os.makedirs(args.outdir, exist_ok=True)
    tmp = os.path.join(args.outdir, '.tts')

    report, failed = [], []
    for index, line in enumerate(lines, 1):
        line_id = str(line['id'])
        text = line['text'].strip()
        wanted = line.get('duration')
        mp3 = os.path.join(tmp, f'{line_id}.mp3')
        wav = os.path.join(args.outdir, f'{line_id}.wav')
        os.makedirs(tmp, exist_ok=True)
        try:
            asyncio.run(synth(text, mp3, args.voice, args.rate, args.volume, args.pitch))
            natural = probe(mp3)
            tempo = 1.0
            if wanted and natural > wanted * 1.004:
                tempo = min(MAX_TEMPO, natural / wanted)
            if args.dry_run:
                state = 'ok'
            else:
                to_wav(mp3, wav, wanted, tempo)
                state = 'over' if wanted and natural / tempo > wanted * 1.004 else 'ok'
            got = wanted if (wanted and state == 'ok') else round(natural / tempo, 3)
            report.append({'id': line_id, 'natural': round(natural, 3), 'slot': wanted, 'result': got,
                           'tempo': round(tempo, 3), 'state': state})
            if state == 'over':
                failed.append(f'{line_id}: {natural:.2f}s 压不进 {wanted:.2f}s 的槽位（已用 {tempo:.2f}× 拉伸）')
            print(f'[{index}/{len(lines)}] {line_id} {natural:.2f}s → {got:.2f}s (tempo {tempo:.2f}) {state}', flush=True)
        except Exception as error:  # one bad line should not cost the other twenty-six
            failed.append(f'{line_id}: {error}')
            print(f'[{index}/{len(lines)}] {line_id} 失败：{error}', file=sys.stderr, flush=True)

    if not args.dry_run:
        with open(os.path.join(args.outdir, 'voice.json'), 'w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
        shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print('\n需要处理：', file=sys.stderr)
        for item in failed:
            print('  ' + item, file=sys.stderr)
        return 1
    print(f'\n完成 {len(report)} 句 · 音色 {args.voice} · 语速 {args.rate}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
