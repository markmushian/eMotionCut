# -*- coding: utf-8 -*-
"""A work belongs to one workspace from brief to measured business outcome."""
import copy
import json
import math
from pathlib import Path
import threading
import time
import uuid
import re
from urllib.parse import quote

import media_studio
import creation_brain

HERE = Path(__file__).resolve().parent
STAGES = ['brief', 'script', 'record', 'edit', 'package', 'publish', 'review']
METRICS = ('views', 'likes', 'comments', 'saves', 'shares', 'follows', 'leads', 'orders', 'revenue', 'cost')


def metrics_summary(metrics):
    result = {key: metrics.get(key) for key in METRICS}
    def ratio(a, b):
        return round(a / b * 100, 2) if a is not None and b is not None and b > 0 else None
    result['lead_rate'] = ratio(result['leads'], result['views'])
    result['order_rate'] = ratio(result['orders'], result['leads'])
    result['net_revenue'] = round(result['revenue'] - result['cost'], 2) if result['revenue'] is not None and result['cost'] is not None else None
    result['interaction_rate'] = ratio(sum(result[k] for k in ('likes', 'comments', 'saves', 'shares')), result['views']) if all(result[k] is not None for k in ('likes', 'comments', 'saves', 'shares')) else None
    advice = []
    if result['views'] is None:
        advice.append('先回填实际播放量，当前无法判断曝光表现。')
    if result['views'] is not None and result['leads'] is None:
        advice.append('只有播放数据还不能评估转化，请记录实际咨询或留资数。')
    if result['leads'] is not None and result['orders'] is None:
        advice.append('补充成交数，才能计算咨询到成交的转化。')
    if result['revenue'] is None or result['cost'] is None:
        advice.append('收入和制作成本未完整填写，暂不显示净收益。')
    result['advice'] = advice or ['已具备本条作品的曝光和转化数据。继续记录多条同类作品，再比较选题与行动引导的表现。']
    return result


class Workspaces:
    def __init__(self, root=None):
        self.root = Path(root or HERE / '工作区' / '作品空间')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def file(self, wid):
        return self.root / (media_studio.Studio.valid_id(wid) + '.json')

    def load(self, wid):
        return json.loads(self.file(wid).read_text(encoding='utf-8'))

    def list(self):
        items = []
        for file in self.root.glob('*.json'):
            try:
                p = json.loads(file.read_text(encoding='utf-8'))
                items.append({k: p.get(k) for k in ('id', 'topic', 'audience', 'goal', 'updated', 'stage', 'brain_id', 'cut_id')})
            except (OSError, ValueError):
                continue
        return sorted(items, key=lambda x: x['updated'], reverse=True)

    def create(self, data):
        topic = str(data.get('topic') or '').strip()
        if not 3 <= len(topic) <= 300:
            raise ValueError('选题需要 3–300 字')
        p = {'id': uuid.uuid4().hex[:24], 'topic': topic, 'audience': str(data.get('audience') or '')[:300],
             'goal': str(data.get('goal') or '建立信任'), 'offer': str(data.get('offer') or '')[:500],
             'cta': str(data.get('cta') or '')[:300], 'reference': str(data.get('reference') or '')[:10000],
             'aspect': '9:16', 'seconds': 90, 'stage': 'brief', 'brain_id': '', 'cut_id': '', 'motion_id': '',
             'publication': {'platform': '', 'url': '', 'title': '', 'description': '', 'status': 'draft'},
             'metrics': {key: None for key in METRICS}, 'learning': '', 'revision': 1,
             'created': time.time(), 'updated': time.time()}
        media_studio.atomic_json(self.file(p['id']), p)
        return p

    def import_brain(self, rid):
        with self.lock:
            for item in self.list():
                if item.get('brain_id') == rid:
                    return self.load(item['id'])
            run = creation_brain.service().load(rid)
            p = self.create({'topic': run['topic']})
            p.update(brain_id=rid, seconds=run['seconds'], aspect=run['aspect'],
                     cut_id=run.get('project_id', ''), stage='edit' if run.get('project_id') else 'script')
            media_studio.atomic_json(self.file(p['id']), p)
            return p

    def save(self, wid, data):
        with self.lock:
            p = self.load(wid)
            if p['revision'] != data.get('revision'):
                raise ValueError('作品已在另一个窗口修改，请刷新后继续')
            for key, limit in (('topic', 300), ('audience', 300), ('offer', 500), ('cta', 300), ('goal', 100), ('reference', 10000), ('learning', 3000)):
                if key in data:
                    p[key] = str(data[key] or '').strip()[:limit]
            if len(p['topic']) < 3:
                raise ValueError('选题至少 3 字')
            if data.get('stage') in STAGES:
                p['stage'] = data['stage']
            if data.get('aspect') in ('9:16', '16:9'):
                p['aspect'] = data['aspect']
            if data.get('seconds') in (60, 90, 180):
                p['seconds'] = data['seconds']
            for key in ('cut_id', 'motion_id'):
                if key in data and data[key]:
                    if key == 'cut_id':
                        media_studio.service().load(data[key])
                    elif not re.fullmatch(r'mj_[\w-]{6,40}', str(data[key])) or not (HERE / '动效库' / (data[key] + '.json')).is_file():
                        raise ValueError('动效工程不存在')
                    p[key] = data[key]
            if 'publication' in data:
                pub = data['publication']
                if not isinstance(pub, dict):
                    raise ValueError('发布信息格式错误')
                url = str(pub.get('url') or '').strip()
                if url and not url.startswith(('http://', 'https://')):
                    raise ValueError('发布链接应以 http:// 或 https:// 开头')
                status = pub.get('status', 'draft')
                if status not in ('draft', 'published'):
                    raise ValueError('发布状态无效')
                p['publication'] = {'platform': str(pub.get('platform') or '')[:50], 'url': url[:2000],
                                    'title': str(pub.get('title') or '')[:300], 'description': str(pub.get('description') or '')[:3000], 'status': status}
            if 'metrics' in data:
                for key in METRICS:
                    value = data['metrics'].get(key)
                    if value in ('', None):
                        p['metrics'][key] = None
                        continue
                    n = float(value)
                    if type(value) is bool or not math.isfinite(n) or n < 0:
                        raise ValueError('数据必须是非负数；未统计请留空')
                    if key not in ('revenue', 'cost') and n != int(n):
                        raise ValueError('次数与人数应填写整数')
                    p['metrics'][key] = round(n, 2) if key in ('revenue', 'cost') else int(n)
            p['revision'] += 1
            p['updated'] = time.time()
            media_studio.atomic_json(self.file(wid), p)
            return p

    def start(self, wid):
        with self.lock:
            p = self.load(wid)
            if p['brain_id']:
                brain = creation_brain.service()
                run = brain.load(p['brain_id'])
                if run['seconds'] != p['seconds']:
                    brain.redraft(p['brain_id'], p['seconds'])
                p.update(stage='script', revision=p['revision'] + 1, updated=time.time())
                media_studio.atomic_json(self.file(wid), p)
                return p
            context = '\n'.join(['目标受众：'+p['audience'], '内容目标：'+p['goal'], '产品或服务（不虚构效果）：'+p['offer'],
                                  '用户希望的行动：'+p['cta'], '用户提供的参考材料：'+p['reference']])
            run = creation_brain.service().create({'topic': p['topic'], 'context': context, 'aspect': p['aspect'], 'seconds': p['seconds']})
            p.update(brain_id=run['id'], stage='script', revision=p['revision'] + 1, updated=time.time())
            media_studio.atomic_json(self.file(wid), p)
            return p

    def detail(self, wid):
        p = self.load(wid)
        if p['brain_id']:
            try:
                run = creation_brain.service().state(p['brain_id'])
                p['brain'] = run
                if not p['cut_id'] and run.get('project_id'):
                    p['cut_id'] = run['project_id']
            except (OSError, ValueError):
                p['brain_error'] = '关联的自动创作记录无法读取'
        if p['cut_id']:
            studio = media_studio.service()
            with studio.lock:
                jobs = [copy.deepcopy(j) for j in studio.jobs.values() if j['project'] == p['cut_id']]
            jobs.sort(key=lambda j: j['created'], reverse=True)
            p['jobs'] = jobs[:12]
            p['cut_result'] = next((j.get('result') for j in jobs if j['kind'] == 'render' and j['status'] == 'done'), None)
            p['package_base'] = next((j.get('result', {}).get('base_id') for j in jobs if j['kind'] == 'base' and j['status'] == 'done'), None)
        p['summary'] = metrics_summary(p['metrics'])
        if p.get('motion_id'):
            exports = sorted((HERE.parent / '成片库').glob('*'+p['motion_id']+'*.mp4'), key=lambda x: x.stat().st_mtime, reverse=True)
            if exports:
                p['motion_result'] = '/api/tl/seg/' + quote(exports[0].name)
        return p


_workspaces = None
_init = threading.Lock()


def service():
    global _workspaces
    with _init:
        if _workspaces is None:
            _workspaces = Workspaces()
    return _workspaces


def handle(handler, method, path, query):
    if not path.startswith('/api/workspace/'):
        return False
    spaces = service()
    try:
        if method == 'GET':
            if path.endswith('/list'):
                result = {'items': spaces.list()}
            else:
                result = spaces.detail((query.get('id') or [''])[0])
        else:
            data = handler.read_json()
            if path.endswith('/create'):
                result = spaces.create(data)
            elif path.endswith('/import-brain'):
                result = spaces.import_brain(data.get('id'))
            elif path.endswith('/save'):
                spaces.save(data.get('id'), data)
                result = spaces.detail(data['id'])
            elif path.endswith('/start'):
                spaces.start(data.get('id'))
                result = spaces.detail(data['id'])
            else:
                raise ValueError('操作不存在')
        handler.send_json(result)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        handler.send_json({'error': str(exc)}, 400)
    except Exception as exc:
        handler.send_json({'error': '作品处理失败：'+str(exc)}, 500)
    return True
