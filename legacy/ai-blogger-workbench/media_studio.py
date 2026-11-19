# -*- coding: utf-8 -*-
"""Non-destructive, persistent talking-head editor. Original media is never rewritten.

Original implementation. Design references: Auto-Editor's reviewable cut intervals,
faster-whisper's word timestamps, FFmpeg trim/atrim + concat. See 开源升级调研.md.
"""
import array
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BGM_DIR = HERE / '工作区' / 'BGM曲库'
VIDEO = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}
ACTIVE = {'queued', 'running', 'cancelling'}


def stamp(value):
    parts = value.strip().replace(',', '.').split(':')
    if len(parts) not in (2, 3):
        raise ValueError('时间戳格式错误')
    return sum(float(p) * (60 ** i) for i, p in enumerate(reversed(parts)))


def parse_srt(text):
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r', '').strip().lstrip('\ufeff')):
        lines = block.splitlines()
        idx = next((i for i, s in enumerate(lines) if '-->' in s), None)
        if idx is None:
            continue
        a, b = lines[idx].split('-->', 1)
        s, e = stamp(a), stamp(b.split()[0])
        if math.isfinite(s) and math.isfinite(e) and 0 <= s < e:
            cues.append({'id': 'c%d' % len(cues), 's': round(s, 3), 'e': round(e, 3),
                         'text': '\n'.join(lines[idx + 1:]).strip()})
    return cues


def srt_time(value):
    ms = max(0, round(value * 1000))
    sec, ms = divmod(ms, 1000)
    minute, sec = divmod(sec, 60)
    hour, minute = divmod(minute, 60)
    return f'{hour:02}:{minute:02}:{sec:02},{ms:03}'


def make_srt(cues):
    return '\n\n'.join(f"{i + 1}\n{srt_time(c['s'])} --> {srt_time(c['e'])}\n{c['text']}" for i, c in enumerate(cues)) + '\n'


def normalize_ranges(ranges, duration):
    out = []
    if not isinstance(ranges, list) or len(ranges) > 2000:
        raise ValueError('剪切区间过多或格式错误')
    for item in ranges:
        a, b = float(item['s']), float(item['e'])
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a or b > duration + .05:
            raise ValueError('剪切区间必须在原片时长内')
        out.append((max(0., a), min(duration, b)))
    merged = []
    for a, b in sorted(out):
        if merged and a <= merged[-1][1] + .001:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return merged


def keep_ranges(cuts, duration):
    cuts = normalize_ranges(cuts, duration)
    kept, cur = [], 0.
    for a, b in cuts:
        if a - cur >= .02:
            kept.append((cur, a))
        cur = b
    if duration - cur >= .02:
        kept.append((cur, duration))
    if not kept:
        raise ValueError('不能删除整条视频，请至少保留一段内容')
    return kept


def remap_cues(cues, kept):
    out = []
    for cue in cues:
        cursor, fragments = 0., []
        for a, b in kept:
            s, e = max(a, cue['s']), min(b, cue['e'])
            if e > s + .01:
                fragments.append((cursor + s - a, cursor + e - a))
            cursor += b - a
        if fragments:
            out.append({**cue, 's': round(fragments[0][0], 3), 'e': round(fragments[-1][1], 3)})
    return out


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


class Cancelled(Exception):
    pass


class Studio:
    def __init__(self, work=None, source_roots=None):
        self.work = Path(work or HERE / '工作区' / '智能剪辑')
        self.projects = self.work / 'projects'
        self.jobs_dir = self.work / 'jobs'
        self.uploads = self.work / 'uploads'
        self.exports = self.work / 'exports'
        for p in (self.projects, self.jobs_dir, self.uploads, self.exports):
            p.mkdir(parents=True, exist_ok=True)
        self.source_roots = source_roots or [HERE / '底片库', HERE / '工作区' / '输入',
                                            ROOT / 'samples', ROOT / 'videos', ROOT / '成片库', self.uploads]
        self.lock = threading.RLock()
        self.model_lock = threading.Lock()
        self.jobs = {}
        self.events = {}
        self.processes = {}
        self.ffmpeg = str(HERE / '工具' / 'ffmpeg' / 'ffmpeg.exe')
        self.ffprobe = str(HERE / '工具' / 'ffmpeg' / 'ffprobe.exe')
        for fp in self.jobs_dir.glob('*.json'):
            try:
                j = json.loads(fp.read_text(encoding='utf-8'))
                if j['status'] in ACTIVE:
                    j.update(status='interrupted', message='服务重启中断了任务，可重新运行；原素材未改变')
                    atomic_json(fp, j)
                self.jobs[j['id']] = j
            except (ValueError, KeyError, OSError):
                pass

    @staticmethod
    def valid_id(value):
        if not re.fullmatch(r'[a-f0-9]{24}', str(value or '')):
            raise ValueError('标识不合法')
        return value

    def load(self, pid):
        return json.loads((self.projects / (self.valid_id(pid) + '.json')).read_text(encoding='utf-8'))

    def project_list(self):
        items = []
        for fp in self.projects.glob('*.json'):
            try:
                p = json.loads(fp.read_text(encoding='utf-8'))
                items.append({k: p.get(k) for k in ('id', 'name', 'duration', 'updated', 'revision')})
            except (ValueError, OSError):
                pass
        return sorted(items, key=lambda p: p['updated'], reverse=True)

    def sources(self):
        items, seen = [], set()
        for root in self.source_roots:
            if not Path(root).is_dir():
                continue
            for fp in Path(root).glob('**/*'):
                if len(items) >= 500:
                    break
                if not fp.is_file() or fp.suffix.lower() not in VIDEO or str(fp.resolve()) in seen:
                    continue
                seen.add(str(fp.resolve()))
                key = hashlib.sha256(str(fp.resolve()).encode()).hexdigest()[:24]
                items.append({'id': key, 'name': fp.name, 'path': str(fp.resolve()), 'size': fp.stat().st_size,
                              'modified': fp.stat().st_mtime})
        return sorted(items, key=lambda i: i['modified'], reverse=True)

    def health(self):
        import aiwrite
        cfg = aiwrite.load_cfg()
        cached = list((Path.home() / '.cache/huggingface/hub').glob('models--Systran--faster-whisper-*/snapshots/*/model.bin'))
        llm_ready = bool(cfg.get('base_url') and cfg.get('model') and (cfg.get('api_key') or '11434' in cfg.get('base_url', '')))
        tts_models = HERE / '工作区' / 'TTS' / 'models'
        return {'ffmpeg': Path(self.ffmpeg).is_file(), 'ffprobe': Path(self.ffprobe).is_file(),
                'asr_installed': importlib.util.find_spec('faster_whisper') is not None,
                'asr_cached': bool(cached), 'llm_configured': llm_ready,
                'model': cfg.get('model', ''), 'work': str(self.work),
                'tts_ready': all((tts_models / f).is_file() for f in ('kokoro-v1.1-zh.int8.onnx', 'voices-v1.1-zh.bin')) and importlib.util.find_spec('kokoro_onnx') is not None,
                'capabilities': {'text_cut': True, 'silence': True, 'waveform': True, 'export': True,
                                 'ai_review': llm_ready, 'avatar': False, 'lip_sync': False}}

    def probe(self, path):
        r = subprocess.run([self.ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                           capture_output=True, timeout=30, creationflags=0x08000000)
        if r.returncode:
            raise ValueError('无法读取视频，请检查文件是否完整')
        data = json.loads(r.stdout)
        video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
        if not video:
            raise ValueError('素材没有视频轨道')
        duration = float(data.get('format', {}).get('duration') or video.get('duration') or 0)
        if not math.isfinite(duration) or duration <= 0 or duration > 7200:
            raise ValueError('支持时长在 0–120 分钟内的视频')
        return {'duration': duration, 'w': video['width'], 'h': video['height'],
                'has_audio': any(s.get('codec_type') == 'audio' for s in data.get('streams', []))}

    def create(self, source_id, project_id=None):
        source = next((s for s in self.sources() if s['id'] == source_id), None)
        if not source:
            raise ValueError('找不到素材，请刷新素材列表')
        meta = self.probe(source['path'])
        pid = project_id or uuid.uuid4().hex[:24]
        sidecar = Path(source['path']).parent / 'cues.cue'
        if not sidecar.is_file():
            sidecar = Path(source['path']).with_suffix('.srt')
        cues = parse_srt(sidecar.read_text(encoding='utf-8-sig')) if sidecar.is_file() else []
        p = {'schema': 'cut-project/1', 'id': pid, 'name': Path(source['name']).stem,
             'source': source['path'], **meta, 'cues': cues, 'cuts': [], 'suggestions': [], 'corrections': [], 'waveform': [],
             'settings': {'subtitles': True, 'normalize_audio': True, 'aspect': 'original', 'quality': 'standard',
                          'bgm': '', 'bgm_gain': .12, 'duck_bgm': True},
             'revision': 1, 'created': time.time(), 'updated': time.time()}
        atomic_json(self.projects / (pid + '.json'), p)
        return self.public(p)

    @staticmethod
    def public(p):
        return {k: v for k, v in p.items() if k != 'source'}

    def save(self, pid, payload):
        with self.lock:
            p = self.load(pid)
            if payload.get('revision') != p['revision']:
                raise ValueError('工程已在另一个窗口更新，请重新打开后修改')
            duration = p['duration']
            cuts = payload.get('cuts', p['cuts'])
            keep_ranges(cuts, duration)
            p['cuts'] = [{'s': a, 'e': b} for a, b in normalize_ranges(cuts, duration)]
            if 'cues' in payload:
                cues = payload['cues']
                if not isinstance(cues, list) or len(cues) != len(p['cues']):
                    raise ValueError('字幕数量不匹配，请重新打开工程')
                for old, new in zip(p['cues'], cues):
                    if new.get('id') != old['id']:
                        raise ValueError('字幕顺序不匹配')
                    text = str(new.get('text') or '').strip()
                    if not text or len(text) > 2000:
                        raise ValueError('字幕不能为空或超过 2000 字')
                    old['text'] = text
            settings = payload.get('settings', p['settings'])
            if settings.get('aspect') not in ('original', '16:9', '9:16', '1:1'):
                raise ValueError('画幅选项不支持')
            if settings.get('quality') not in ('standard', 'draft'):
                raise ValueError('画质选项不支持')
            bgm = str(settings.get('bgm') or '')
            if bgm and (Path(bgm).name != bgm or not (BGM_DIR / bgm).is_file()):
                raise ValueError('背景音乐不在本机曲库中')
            gain = float(settings.get('bgm_gain', .12))
            if not math.isfinite(gain) or not 0 <= gain <= .4:
                raise ValueError('背景音乐音量必须在 0–40% 之间')
            p['settings'] = {'aspect': settings['aspect'], 'quality': settings['quality'],
                             'subtitles': bool(settings.get('subtitles')), 'normalize_audio': bool(settings.get('normalize_audio')),
                             'bgm': bgm, 'bgm_gain': gain, 'duck_bgm': bool(settings.get('duck_bgm', True))}
            p['name'] = str(payload.get('name') or p['name'])[:100]
            p['revision'] += 1
            p['updated'] = time.time()
            atomic_json(self.projects / (pid + '.json'), p)
            return self.public(p)

    def update_job(self, jid, **patch):
        with self.lock:
            self.jobs[jid].update(patch, updated=time.time())
            atomic_json(self.jobs_dir / (jid + '.json'), self.jobs[jid])

    def run(self, jid, args, binary=False):
        if self.events[jid].is_set():
            raise Cancelled()
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
        with self.lock:
            self.processes[jid] = proc
        try:
            while True:
                try:
                    out, err = proc.communicate(timeout=.4)
                    break
                except subprocess.TimeoutExpired:
                    if self.events[jid].is_set():
                        if os.name == 'nt':
                            # Only terminate this job's own process tree (TTS may spawn FFmpeg).
                            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                           capture_output=True, creationflags=0x08000000)
                        else:
                            proc.kill()
                        proc.communicate()
                        raise Cancelled()
            if self.events[jid].is_set():
                raise Cancelled()
            if proc.returncode:
                detail = err.decode('utf-8', 'replace')[-1600:]
                if not binary:
                    detail = (detail + '\n' + out.decode('utf-8', 'replace')[-1200:]).strip()
                if '没识别到任何语音' in detail or '断句为空' in detail:
                    raise ValueError('没有识别到有效人声。这段视频可能只有音乐或动效；原片仍可播放，导出时可关闭字幕。请上传带清晰口播的录像，或改用配套字幕。')
                raise RuntimeError(detail or '媒体处理失败（退出码 %s）' % proc.returncode)
            return (out if binary else out.decode('utf-8', 'replace')), err.decode('utf-8', 'replace')
        finally:
            with self.lock:
                self.processes.pop(jid, None)

    def start(self, pid, kind):
        if kind not in ('analyze', 'render', 'base', 'ai'):
            raise ValueError('任务类型不支持')
        with self.lock:
            p = self.load(pid)
            if any(j['project'] == pid and j['status'] in ACTIVE for j in self.jobs.values()):
                raise ValueError('当前作品已有任务运行，请等待或取消')
            if sum(j['status'] in ACTIVE for j in self.jobs.values()) >= 2:
                raise ValueError('已有两个媒体任务运行，完成后再试')
            if kind in ('render', 'base') and not p['cues'] and p['settings']['subtitles']:
                raise ValueError('请先识别字幕，或在导出设置中关闭字幕')
            if kind == 'ai' and not p['cues']:
                raise ValueError('请先识别口播内容')
            jid = uuid.uuid4().hex[:24]
            self.jobs[jid] = {'id': jid, 'project': pid, 'name': p['name'], 'kind': kind, 'revision': p['revision'],
                              'status': 'queued', 'pct': 0, 'message': '排队中', 'created': time.time()}
            self.events[jid] = threading.Event()
            self.update_job(jid)
            threading.Thread(target=self.worker, args=(jid, copy.deepcopy(p), kind), daemon=True).start()
            return dict(self.jobs[jid])

    def start_voice(self, payload):
        text = str(payload.get('text') or '').strip()
        if not 20 <= len(text) <= 10000:
            raise ValueError('配音文案需要 20–10000 字')
        if payload.get('aspect', '16:9') not in ('16:9', '9:16', '1:1'):
            raise ValueError('画幅不支持')
        speed = float(payload.get('speed') or 1)
        if not math.isfinite(speed):
            raise ValueError('语速必须是有限数值')
        if not self.health()['tts_ready']:
            raise ValueError('本机配音模型或运行环境缺失')
        with self.lock:
            if sum(j['status'] in ACTIVE for j in self.jobs.values()) >= 2:
                raise ValueError('已有两个媒体任务运行，完成后再试')
            if any(j['kind'] == 'voice' and j['status'] in ACTIVE for j in self.jobs.values()):
                raise ValueError('已有配音任务在运行')
            jid, pid = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
            self.jobs[jid] = {'id': jid, 'project': pid, 'name': '文案配音：' + text[:20], 'kind': 'voice',
                              'revision': 1, 'status': 'queued', 'pct': 0, 'message': '准备本机配音', 'created': time.time()}
            self.events[jid] = threading.Event()
            self.update_job(jid)
            req = self.work / (jid + '_voice.json')
            atomic_json(req, {'text': text, 'voice': str(payload.get('voice') or 'zm_009'),
                              'speed': max(.7, min(1.4, speed)),
                              'aspect': payload.get('aspect', '16:9'), 'gap_policy': '标准'})
            threading.Thread(target=self.voice_worker, args=(jid, pid, req), daemon=True).start()
            return dict(self.jobs[jid])

    def voice_worker(self, jid, pid, req):
        try:
            self.update_job(jid, status='running', pct=15, message='本机生成配音、逐句字幕和底片，不调用付费 API')
            out = self.work / (jid + '_voice_result.json')
            self.run(jid, [os.sys.executable, '-X', 'utf8', str(Path(__file__)), '--voice', str(req), str(out)])
            data = json.loads(out.read_text(encoding='utf-8'))
            source = HERE / '底片库' / data['id'] / '底片.mp4'
            sid = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:24]
            p = self.create(sid, project_id=pid)
            self.update_job(jid, status='done', pct=100, message='配音与字幕已生成，可直接剪辑', result={'project': p['id'], 'base_id': data['id']})
        except Cancelled:
            self.update_job(jid, status='cancelled', message='已取消配音任务')
        except Exception as exc:
            self.update_job(jid, status='failed', message=str(exc)[:2000])

    def cancel(self, jid):
        with self.lock:
            j = self.jobs[self.valid_id(jid)]
            if j['status'] in ACTIVE:
                self.events[jid].set()
                self.update_job(jid, status='cancelling', message='正在停止；原素材不会改变')
            return dict(j)

    def worker(self, jid, p, kind):
        try:
            self.update_job(jid, status='running', pct=3, message='正在读取工程快照')
            if kind == 'analyze':
                updates = self.analyze(jid, p)
            elif kind == 'ai':
                updates = self.ai_review(jid, p)
            else:
                updates = None
                if kind == 'base':
                    p['settings']['subtitles'] = False
                    p['settings']['bgm'] = ''
                result = self.render(jid, p)
                if kind == 'base':
                    result['base_id'] = self.publish_base(jid, p)
            if self.events[jid].is_set():
                raise Cancelled()
            if updates is not None:
                with self.lock:
                    current = self.load(p['id'])
                    if current['revision'] != p['revision']:
                        raise ValueError('分析期间工程已被修改，未覆盖新修改。请保存后重新分析')
                    current.update(updates, revision=p['revision'] + 1, updated=time.time())
                    atomic_json(self.projects / (p['id'] + '.json'), current)
                result = {'project': p['id']}
            self.update_job(jid, status='done', pct=100, message='完成', result=result)
        except Cancelled:
            self.update_job(jid, status='cancelled', message='已取消；原素材未改变')
        except Exception as exc:
            self.update_job(jid, status='failed', message=str(exc)[:2000])

    def analyze(self, jid, p):
        if not p['has_audio']:
            raise ValueError('素材没有音轨，不能进行口播分析')
        source = p['source']
        self.update_job(jid, pct=10, message='计算音频波形与停顿')
        raw, _ = self.run(jid, [self.ffmpeg, '-v', 'error', '-i', source, '-vn', '-ac', '1', '-ar', '2000',
                                '-f', 's16le', '-'], binary=True)
        samples = array.array('h')
        samples.frombytes(raw)
        stride = max(1, len(samples) // 1200)
        peaks = [round(max(abs(x) for x in samples[i:i + stride]) / 32768, 4) for i in range(0, len(samples), stride)]
        _, silence_log = self.run(jid, [self.ffmpeg, '-hide_banner', '-i', source, '-af',
                                       'silencedetect=noise=-35dB:d=0.65', '-f', 'null', '-'])
        starts = [float(t) for t in re.findall(r'silence_start:\s*([\d.]+)', silence_log)]
        ends = [float(t) for t in re.findall(r'silence_end:\s*([\d.]+)', silence_log)]
        cues = p['cues']
        if not cues:
            self.update_job(jid, pct=35, message='本机识别口播，生成词级时间戳（首次可能较慢）')
            # A separate process is cancellable and avoids sharing pipeline's temporary audio file.
            output = self.work / (jid + '.srt')
            self.run(jid, [os.sys.executable, '-X', 'utf8', str(HERE / 'auto_director.py'), source, str(output)])
            cues = parse_srt(output.read_text(encoding='utf-8-sig'))
        if not cues:
            raise ValueError('未识别到有效口播，请检查音轨')
        self.update_job(jid, pct=85, message='生成可审核的停顿与重复句建议')
        suggestions = []
        for i, a in enumerate(starts):
            b = ends[i] if i < len(ends) else p['duration']
            s, e = a + .16, min(p['duration'], b - .16)
            # Only suggest acoustically silent regions that do not overlap an ASR cue.
            if e - s >= .32 and not any(c['s'] < e and c['e'] > s for c in cues):
                suggestions.append({'id': 's%d' % i, 'kind': 'silence', 's': round(s, 3), 'e': round(e, 3),
                                    'reason': '检测到较长静音，两端保留 160ms 缓冲，请试听后采用'})
        for i, cue in enumerate(cues):
            text = re.sub(r'[^\w\u4e00-\u9fff]', '', cue['text']).lower()
            if text in {'嗯', '呃', '额', '唔', 'um', 'uh'}:
                suggestions.append({'id': 'f%d' % i, 'kind': 'filler', 's': cue['s'], 'e': cue['e'],
                                    'reason': '独立语气词候选；识别可能出错，需试听确认'})
            if i and len(text) >= 5 and text == re.sub(r'[^\w\u4e00-\u9fff]', '', cues[i - 1]['text']).lower():
                suggestions.append({'id': 'r%d' % i, 'kind': 'repeat', 's': cues[i - 1]['s'], 'e': cues[i - 1]['e'],
                                    'reason': '相邻两句文字相同，前一句可能是重录；需确认语气和语义'})
        return {'cues': cues, 'waveform': peaks, 'suggestions': suggestions}

    def ai_review(self, jid, p):
        import aiwrite
        cfg = aiwrite.load_cfg()
        if not cfg.get('api_key') and '11434' not in cfg.get('base_url', ''):
            raise ValueError('请先在账号定位中配置文本模型')
        if len(p['cues']) > 500:
            raise ValueError('AI 审核目前支持 500 句内的作品，请分段处理')
        self.update_job(jid, pct=20, message='文本模型审核重录、赘述和表达断点；不会自动删片')
        indexed = [{'index': i, 'text': c['text']} for i, c in enumerate(p['cues'])]
        raw = aiwrite.chat(cfg, '你是中文口播剪辑助理。只审查提供的逐句文本，不能假装看过画面或听过音频。'
                           '仅提出明确的重复、重录和无信息赘述候选，不删除事实、否定词、数字、转折或必要铺垫。'
                           '同时可提出显而易见的字幕同音错字修正，不能添加事实、改变数字或改写整句。'
                           '输出 JSON：{"suggestions":[{"index":0,"reason":"具体原因"}],'
                           '"corrections":[{"index":0,"before":"完整原句","after":"仅改错字的完整句子","reason":"错字依据"}]}。'
                           'index 必须是输入中存在的整数。没有可靠建议则返回空列表。',
                           json.dumps(indexed, ensure_ascii=False), timeout=180)
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
        data = json.loads(raw)
        proposals, seen = [], set()
        for item in data.get('suggestions', [])[:50]:
            idx = item.get('index')
            if type(idx) is not int or idx < 0 or idx >= len(p['cues']) or idx in seen:
                continue
            seen.add(idx)
            c = p['cues'][idx]
            proposals.append({'id': 'ai%d' % idx, 'kind': 'ai', 's': c['s'], 'e': c['e'],
                              'reason': str(item.get('reason') or '模型建议，请试听确认')[:300]})
        corrections = []
        for item in data.get('corrections', [])[:50]:
            idx = item.get('index')
            if type(idx) is not int or not 0 <= idx < len(p['cues']):
                continue
            before = p['cues'][idx]['text']
            after = item.get('after')
            if not isinstance(after, str) or not after.strip() or len(after) > len(before) * 2 + 20 or after == before:
                continue
            if item.get('before') != before or re.findall(r'\d+(?:\.\d+)?', before) != re.findall(r'\d+(?:\.\d+)?', after):
                continue
            corrections.append({'cue_id': p['cues'][idx]['id'], 'before': before, 'after': after,
                                'reason': str(item.get('reason') or 'AI 文本推断，请对照原声')[:300]})
        return {'suggestions': [s for s in p['suggestions'] if s['kind'] != 'ai'] + proposals, 'corrections': corrections,
                'ai_reviewed': time.time(), 'ai_model': cfg.get('model')}

    def publish_base(self, jid, p):
        """Bridge edited media to the existing motion factory without burning subtitles twice."""
        folder = self.exports / jid
        bid = '底片_cut_' + jid
        target = HERE / '底片库' / bid
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / '成片.mp4', target / '底片.mp4')
        shutil.copy2(folder / '字幕.srt', target / 'cues.cue')
        meta = self.probe(target / '底片.mp4')
        cues = parse_srt((target / 'cues.cue').read_text(encoding='utf-8'))
        atomic_json(target / 'meta.json', {'id': bid, 'mode': '真人剪辑', 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                                         **meta, 'fps': 25, 'sentences': len(cues), 'text': '\n'.join(c['text'] for c in cues),
                                         'source_project': p['id'], 'source_revision': p['revision'],
                                         'files': {'video': '底片.mp4', 'cue': 'cues.cue'}})
        return bid

    def render(self, jid, p):
        kept = keep_ranges(p['cuts'], p['duration'])
        if len(kept) > 500:
            raise ValueError('剪切片段超过 500，请分段导出')
        folder = self.exports / jid
        folder.mkdir(exist_ok=True)
        duration = sum(b - a for a, b in kept)
        cues = remap_cues(p['cues'], kept)
        (folder / '字幕.srt').write_text(make_srt(cues), encoding='utf-8')
        atomic_json(folder / '剪辑清单.json', {'schema': 'cut-export/1', 'project': self.public(p), 'kept': kept,
                                              'duration': duration, 'source_name': Path(p['source']).name})
        filters = []
        for i, (a, b) in enumerate(kept):
            filters.append(f'[0:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]')
            if p['has_audio']:
                filters.append(f'[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[a{i}]')
        streams = ''.join(f'[v{i}]' + (f'[a{i}]' if p['has_audio'] else '') for i in range(len(kept)))
        filters.append(streams + f"concat=n={len(kept)}:v=1:a={int(p['has_audio'])}[vout]" + ('[aout]' if p['has_audio'] else ''))
        setting = p['settings']
        aspect = setting['aspect']
        w, h = p['w'], p['h']
        if aspect != 'original':
            w, h = {'16:9': (1920, 1080), '9:16': (1080, 1920), '1:1': (1080, 1080)}[aspect]
        if setting['quality'] == 'draft':
            scale = min(1, 640 / max(w, h))
            w, h = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
        else:
            w, h = int(w) // 2 * 2, int(h) // 2 * 2
        tail = f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1'
        if setting['subtitles'] and cues:
            # ASCII temporary name and working directory avoid libass Windows path escaping.
            ass = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {w}', f'PlayResY: {h}',
                   '[V4+ Styles]', 'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding',
                   f'Style: Default,Microsoft YaHei,{max(18, round(h * .036))},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,30,30,{max(20, round(h * .06))},1',
                   '[Events]', 'Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text']
            def ass_time(t):
                return '%d:%02d:%05.2f' % (int(t // 3600), int(t % 3600 // 60), t % 60)
            for c in cues:
                txt = c['text'].replace('\\', '＼').replace('{', '（').replace('}', '）').replace('\n', '\\N')
                ass.append(f"Dialogue: 0,{ass_time(c['s'])},{ass_time(c['e'])},Default,,0,0,0,,{txt}")
            (folder / 'subs.ass').write_text('\n'.join(ass), encoding='utf-8')
            # Use a normalized forward-slash escaped path in the filter script.
            apath = str(folder / 'subs.ass').replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")
            tail += f",ass='{apath}'"
        filters.append('[vout]' + tail + '[finalv]')
        voice_label = 'aout'
        if p['has_audio'] and setting['normalize_audio']:
            filters.append('[aout]loudnorm=I=-16:TP=-1.5:LRA=11[voice]')
            voice_label = 'voice'
        music = setting.get('bgm') or ''
        if music and (Path(music).name != music or not (BGM_DIR / music).is_file()):
            raise ValueError('所选背景音乐已被移动或删除')
        if music:
            gain = float(setting.get('bgm_gain', .12))
            filters.append(f'[1:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,volume={gain:.4f},afade=t=in:d=0.3,afade=t=out:st={max(0,duration-1):.6f}:d=1[bg]')
            if p['has_audio']:
                if setting.get('duck_bgm', True):
                    filters.append(f'[{voice_label}]asplit=2[voice_mix][voice_side]')
                    filters.append('[bg][voice_side]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=250[ducked]')
                    filters.append('[voice_mix][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[finala]')
                else:
                    filters.append(f'[{voice_label}][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[finala]')
            else:
                filters.append('[bg]anull[finala]')
            audio_label = 'finala'
        else:
            audio_label = voice_label
        script = folder / 'filters.txt'
        script.write_text(';\n'.join(filters), encoding='utf-8')
        self.update_job(jid, pct=30, message=f'渲染快照 v{p["revision"]}，{len(kept)} 个保留片段；耗时取决于片长')
        cmd = [self.ffmpeg, '-hide_banner', '-y', '-i', p['source']]
        if music:
            cmd += ['-stream_loop', '-1', '-i', str(BGM_DIR / music)]
        cmd += ['-/filter_complex', str(script), '-map', '[finalv]']
        if p['has_audio'] or music:
            cmd += ['-map', f'[{audio_label}]', '-c:a', 'aac', '-b:a', '192k']
        cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24' if setting['quality'] == 'draft' else '20',
                '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(folder / '成片.mp4')]
        self.run(jid, cmd)
        actual = self.probe(folder / '成片.mp4')
        if abs(actual['duration'] - duration) > max(.25, len(kept) / 20):
            raise ValueError('导出时长与剪辑清单不符，请检查源视频时间戳')
        self.update_job(jid, pct=94, message='校验成片并打包字幕与剪辑清单')
        with zipfile.ZipFile(folder / '工程包.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for name in ('成片.mp4', '字幕.srt', '剪辑清单.json'):
                z.write(folder / name, name, compress_type=zipfile.ZIP_STORED if name.endswith('.mp4') else zipfile.ZIP_DEFLATED)
        return {'video': f'/api/cut/output?id={jid}&file=video', 'srt': f'/api/cut/output?id={jid}&file=srt',
                'bundle': f'/api/cut/output?id={jid}&file=bundle', 'duration': actual['duration'], 'revision': p['revision']}


_studio = None
_init_lock = threading.Lock()


def service():
    global _studio
    with _init_lock:
        if _studio is None:
            _studio = Studio()
    return _studio


def handle(handler, method, path, query):
    """Small route adapter; leaves the legacy server and its projects intact."""
    if not path.startswith('/api/cut/'):
        return False
    s = service()
    arg = lambda k: (query.get(k) or [''])[0]
    try:
        if method == 'GET':
            if path == '/api/cut/health':
                handler.send_json(s.health())
            elif path == '/api/cut/sources':
                handler.send_json({'items': [{k: v for k, v in x.items() if k != 'path'} for x in s.sources()]})
            elif path == '/api/cut/projects':
                handler.send_json({'items': s.project_list()})
            elif path == '/api/cut/summary':
                import motion_json
                projects = s.project_list()
                with s.lock:
                    recent = sorted(s.jobs.values(), key=lambda j: j['created'], reverse=True)
                    recent = copy.deepcopy(recent[:30])
                handler.send_json({'health': s.health(), 'projects': projects, 'jobs': recent,
                                   'counts': {'口播工程': len(projects), '配套底片': sum(1 for _ in (HERE / '底片库').glob('*/meta.json')),
                                              '文案文件': sum(1 for _ in (ROOT / 'scripts').glob('*.md')),
                                              '动效模板': len(motion_json.load_registry())}})
            elif path == '/api/cut/project':
                handler.send_json(s.public(s.load(arg('id'))))
            elif path == '/api/cut/jobs':
                with s.lock:
                    handler.send_json({'items': sorted(s.jobs.values(), key=lambda j: j['created'], reverse=True)[:100]})
            elif path == '/api/cut/media':
                handler.send_file(s.load(arg('id'))['source'])
            elif path == '/api/cut/source-media':
                source = next((x for x in s.sources() if x['id'] == arg('id')), None)
                if not source:
                    raise ValueError('素材不存在')
                handler.send_file(source['path'])
            elif path == '/api/cut/output':
                names = {'video': '成片.mp4', 'srt': '字幕.srt', 'bundle': '工程包.zip'}
                name = names.get(arg('file'))
                if not name:
                    raise ValueError('导出文件类型错误')
                handler.send_file(str(s.exports / s.valid_id(arg('id')) / name), download=arg('file') != 'video')
            else:
                handler.send_json({'error': '接口不存在'}, 404)
        elif path == '/api/cut/upload':
            n = int(handler.headers.get('Content-Length') or 0)
            ext = Path(arg('name')).suffix.lower()
            if ext not in VIDEO or n <= 0 or n > 3 * 1024 ** 3:
                raise ValueError('请选择 3GB 以内的 MP4 / MOV / MKV / WebM 视频')
            target = s.uploads / (uuid.uuid4().hex[:24] + ext)
            tmp = target.with_suffix('.upload')
            left = n
            try:
                with tmp.open('wb') as f:
                    while left:
                        chunk = handler.rfile.read(min(left, 1024 * 1024))
                        if not chunk:
                            raise ValueError('上传中断，请重试')
                        f.write(chunk)
                        left -= len(chunk)
                if ext == '.webm':
                    # Browser MediaRecorder writes live WebM without final duration/cues.
                    # A stream-copy remux finalizes it without re-encoding the recording.
                    normalized = tmp.with_suffix('.normalized.webm')
                    try:
                        remux = subprocess.run([s.ffmpeg, '-v', 'error', '-y', '-i', str(tmp),
                                                '-map', '0:v:0', '-map', '0:a?', '-c', 'copy', '-f', 'webm', str(normalized)],
                                               capture_output=True, timeout=120, creationflags=0x08000000)
                        if remux.returncode:
                            raise ValueError('录像封装失败，请保存原始录像或改用手机拍摄的视频')
                        os.replace(normalized, tmp)
                    finally:
                        normalized.unlink(missing_ok=True)
                s.probe(tmp)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            key = hashlib.sha256(str(target.resolve()).encode()).hexdigest()[:24]
            handler.send_json({'ok': True, 'source_id': key})
        else:
            b = handler.read_json()
            if path == '/api/cut/create':
                handler.send_json(s.create(b.get('source_id')))
            elif path == '/api/cut/voice':
                handler.send_json(s.start_voice(b))
            elif path == '/api/cut/save':
                handler.send_json(s.save(b.get('id'), b))
            elif path == '/api/cut/start':
                handler.send_json(s.start(b.get('id'), b.get('kind')))
            elif path == '/api/cut/cancel':
                handler.send_json(s.cancel(b.get('id')))
            else:
                handler.send_json({'error': '接口不存在'}, 404)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        handler.send_json({'error': str(exc)}, 400)
    except Exception as exc:
        handler.send_json({'error': '处理失败：' + str(exc)}, 500)
    return True


if __name__ == '__main__':
    # Dedicated cancellable TTS process; stdout is captured by the parent job runner.
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == '--voice':
        import voice_factory
        payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        job_id = uuid.uuid4().hex[:24]
        voice_factory.JOBS[job_id] = {'id': job_id, 'running': True, 'log': []}
        voice_factory._worker(job_id, payload['text'], payload['voice'], payload['speed'], payload['aspect'], payload['gap_policy'])
        result = voice_factory.JOBS[job_id]
        if not result.get('ok'):
            print(result.get('error', '配音失败'), file=sys.stderr)
            sys.exit(1)
        atomic_json(sys.argv[3], result['base'])
