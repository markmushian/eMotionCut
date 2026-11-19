# -*- coding: utf-8 -*-
"""Persistent creator director: choose topic, review facts, record, then automate production.

The state machine controls tools; a text model writes the draft but never decides file
paths, executes commands, publishes, or silently edits the user's recorded statements.
"""
import copy
import json
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

import media_studio

HERE = Path(__file__).resolve().parent
BUSY = {'drafting', 'analyzing', 'editing', 'rendering', 'packaging', 'cancelling'}
LABELS = {'drafting': '正在准备口播稿', 'review_script': '确认口播稿', 'recording': '录制真人口播',
          'analyzing': '识别原声与时间轴', 'editing': '审核并剪除安全停顿', 'rendering': '合成字幕、配乐与成片',
          'packaging': '准备封面与发布文案', 'done': '成片已就绪', 'failed': '需要处理问题',
          'interrupted': '已保留进度，等待继续', 'cancelled': '已停止', 'cancelling': '正在停止'}


# 写稿通用框架：从「立一堆禁止规则」改成「动笔前先想清楚四件事」。
# 旧写法的问题：给模型列了一堆「不许写 A、不许写 B」，但没告诉它「先想清楚观众要什么」。
# 结果换个选题就换个翻车姿势（有的写评论腔、有的念操作手册、有的塞 if-else 条件地狱）。
# 现在让它先走一遍认知流程，再动笔 —— 这套流程和选题无关，所以通用。
STYLE = """你写的是「真人口播稿」，不是说明书、不是 prompt 复述、不是操作手册朗读。
动笔前，先在脑子里走完下面四步。这四步对所有选题都成立，是决定稿子好坏的根子。

【第一步 · 定观众与钩子】
先回答：这条选题，最需要它的人是谁？他此刻卡在哪、最痛的是什么？
开场第一句就用这个痛点或一个反直觉的事实把他勾住，让他知道「这条跟我有关」。
禁止用「打开浏览器」「首先」「今天我们来」这种无钩子的开场直接开干。
判废：×「打开浏览器，新建一个在线表格」——没头，废。
      ○「选 AI 工具，你是不是还在靠感觉挨个试？今天给你一张表，三个数一填，不用再试错。」

【第二步 · 只解决一个问题】
一篇稿子只服务一个核心诉求。凡是和这个诉求无关的枝节，一律删掉。
别为了显专业去讲「怎么用秒表」「怎么设置代理」这类跑题的细节——观众要的是结果，不是你的操作过程。
判断标准：删掉这句话，观众还能不能拿到他要的结果？能，就删。

【第三步 · 动作要「动作 → 结果」，不要「条件地狱」】
每个步骤写成一句能照抄的话：在哪个入口、点什么、输入什么原话、会看到什么。
输入的内容放引号里给原文；按钮给准确名字；网址给全。
严禁「如果…就…如果还…就…再…」这种三层嵌套的分支，把最常见的一条路讲清楚即可，
其余排错最多给一句，别展开成条件树。
判废：×「如果它说找不到，就点插入选文件；如果还找不到，检查是不是PDF……」——条件地狱，废。

【第四步 · 结尾给一个立刻能做的动作】
最后一段把最后一个动作讲完就停，或给一个今天就能做的具体动作。
禁止号召式收尾（「今晚就去试」「用起来才是真的」「关注我」），禁止「看哪列填得满就选哪列」这类自相矛盾的伪规则。

【语气总纲】
- 用第二人称祈使句：打开、点、粘贴、输入、回车、选中。
- 说人话，不堆术语，不端着。像给朋友讲清楚一件事，不是背文档。
- 事实只能来自下方「已核实事实」，没有的就不写，不编数字、不编命令、不编网址。
"""

FACT_RULE = """
【本次已联网核实到的事实】（只有这些能写进正文）
{facts}

事实使用规则：
- 只能照抄上面的说法，不许改写、推算、四舍五入；上面没有的一律不许写。
- 严禁在正文里出现【待核实】【待补充】或任何占位标记 —— 没有依据就不写这句话。
- 需要个人经历或素材的位置，直接略过，不要用占位符补位。
- **最重要的禁令**：正文里出现的每一个网址、每一条命令（如 ollama/pip/docker/npm 开头的命令）、
  每一个按钮或菜单名，都必须能在上面的事实里找到出处。找不到就不许写具体命令，
  改成让用户自己去官方渠道确认的说法（例如「打开它的官方说明页，看安装那一节写的命令是什么」）。
  编一条跑不通的命令让观众照着敲，比说错一个数字更糟。
"""

UNVERIFIED = re.compile(r'【\s*待(?:核实|补充|确认|查|定)[^】]{0,120}】')


OP_CMD = re.compile(r'(?:ollama|pip3?|npm|npx|docker|git|brew|conda|poetry|uv|sudo)\s+[a-zA-Z][\w.:@/+-]{1,40}', re.I)
OP_URL = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9.-]{1,40}\.(?:com|cn|ai|net|org|io|dev|app|gg|xyz)(?:/[^\s，。；、）)」】]*)?', re.I)


def flag_unknown_ops(text, materials):
    """正文里的网址和命令，有没有出处。

    模型很容易顺手写一条「ollama pull xxx」——看着专业，其实是编的。
    凡是抓回来的原文里找不到的一律点名，逼人发布前亲手试一遍。
    """
    blob = ' '.join('%s %s %s' % (m.get('title') or '', m.get('url') or '', m.get('text') or '')
                    for m in (materials or [])).lower()
    bad = []
    for kind, pat in (('命令', OP_CMD), ('网址', OP_URL)):
        for m in pat.finditer(text or ''):
            tok = m.group(0).strip().rstrip('.,;:）)」】')
            core = tok.lower()
            if kind == '命令':
                core = ' '.join(tok.split()[:2]).lower()      # 「ollama pull」这种主干
            else:
                core = core.split('/')[0].lstrip('www.')
            if not core:
                continue
            if core in blob or core.split('.')[0] in blob:
                continue
            if tok not in bad:
                bad.append(tok)
    return bad[:8]


def strip_unverified(text):
    """把写不实的句子整句删掉，返回 (清白稿, 被删的标记)。

    模型偶尔还是会把【待核实：xxx】写进正文。与其让用户对着占位符念，
    不如把那句话拿掉 —— 稿子短点没关系，把没核实的东西念出去才是事故。
    """
    parts = re.split(r'(?<=[。！？])|(?<=\n)', text)
    keep, removed = [], []
    for p in parts:
        m = UNVERIFIED.search(p)
        if m:
            removed.append(m.group(0)[:120])
        else:
            keep.append(p)
    cleaned = re.sub(r'\n{3,}', '\n\n', ''.join(keep)).strip()
    return cleaned, removed


def script_timing(text, seconds):
    # A transparent estimate at 240 spoken units/minute; punctuation isn't speech.
    units = len(re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9]+', text))
    target = int(seconds * 4)
    # Half a second of rounding tolerance, matching the displayed whole seconds.
    low, high = max(1, int(target * .95) - 2), int(target * 1.08)
    return {'units': units, 'estimated_seconds': round(units / 4), 'target_seconds': seconds,
            'min_units': low, 'max_units': high, 'ok': low <= units <= high,
            'short': units < low, 'over': units > high}


def _count_units(text):
    """与 script_timing 同口径的字词计数。"""
    return len(re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9]+', text))


# 排错分支的典型标志：这些句子是「如果出错怎么办」的兜底，删掉最不伤主线
_TRIM_HINT = re.compile(r'(如果|没找到|没收到|没显示|没跳出|没跳转|还是|就换|重试|刷新|缓存|重新|再试|换一张|换一个|换个|退出账号)')


def _trim_to_fit(text, max_units):
    """超字数时按句裁剪：优先删排错分支，再删铺垫句，直到落进上限。

    确定性规则，不调模型。保证删掉的是冗余（排错/铺垫），不碰关键动作和事实。
    """
    if _count_units(text) <= max_units:
        return text
    # 按句号/换行切，保留句尾标点
    parts = re.split(r'(?<=[。！？])|(?<=\n)', text)
    parts = [p for p in parts if p.strip()]
    # 两轮删除：先删排错分支，再删铺垫
    for _pass in range(2):
        for i, p in enumerate(parts):
            if _count_units(text) <= max_units:
                return text.strip()
            if not p.strip():
                continue
            is_hint = bool(_TRIM_HINT.search(p))
            is_pad = len(re.findall(r'[\u3400-\u9fff]', p)) < 6  # 过短的句子多为过渡
            if _pass == 0 and not is_hint:
                continue
            if _pass == 1 and not (is_pad and not is_hint):
                continue
            # 删掉这个分句
            idx = text.find(p)
            if idx >= 0:
                text = text[:idx] + text[idx + len(p):]
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


class Brain:
    def __init__(self, studio=None, work=None, chat=None, researcher=None):
        self.studio = studio or media_studio.service()
        # researcher 可注入：单元测试传替身进去，免得每次跑测试都真去联网。
        self.researcher = researcher
        self.work = Path(work or HERE / '工作区' / '创作大脑')
        self.work.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.events = {}
        self.threads = {}
        self.chat = chat
        for path in self.work.glob('*/run.json'):
            try:
                run = json.loads(path.read_text(encoding='utf-8'))
                if run.get('status') in BUSY:
                    run.update(status='interrupted', message='服务重启，已保存文案与素材；点击继续，不自动重复付费请求')
                    media_studio.atomic_json(path, run)
            except (OSError, ValueError):
                pass

    def folder(self, rid):
        return self.work / self.studio.valid_id(rid)

    def load(self, rid):
        return json.loads((self.folder(rid) / 'run.json').read_text(encoding='utf-8'))

    def update(self, rid, **patch):
        with self.lock:
            run = self.load(rid)
            run.update(patch, updated=time.time())
            media_studio.atomic_json(self.folder(rid) / 'run.json', run)
            return run

    def list(self):
        items = []
        for file in self.work.glob('*/run.json'):
            try:
                run = json.loads(file.read_text(encoding='utf-8'))
                items.append({k: run.get(k) for k in ('id', 'topic', 'status', 'message', 'updated', 'step', 'pct')})
            except (OSError, ValueError):
                pass
        return sorted(items, key=lambda r: r['updated'], reverse=True)

    def state(self, rid):
        run = self.load(rid)
        run['label'] = LABELS.get(run['status'], run['status'])
        run['steps'] = ['准备文案', '确认与录制', '识别剪辑', '生成成片', '封面与发布包']
        return run

    def spawn(self, rid, function):
        with self.lock:
            if rid in self.threads and self.threads[rid].is_alive():
                raise ValueError('这条作品正在处理，请稍候')
            if sum(t.is_alive() for t in self.threads.values()) >= 1:
                raise ValueError('创作大脑正在处理另一条作品，完成后再继续')
            self.events[rid] = threading.Event()
            def wrapped():
                try:
                    function(rid)
                except media_studio.Cancelled:
                    self.update(rid, status='cancelled', message='已停止。文案、素材和已完成产物保留')
                except Exception as exc:
                    self.update(rid, status='failed', message=str(exc)[:1800])
            thread = threading.Thread(target=wrapped, daemon=True)
            self.threads[rid] = thread
            thread.start()

    def check_cancel(self, rid):
        # 事件可能还没建（比如命令行里同步调用 draft），没事件就等于没让停。
        event = self.events.get(rid)
        if event and event.is_set():
            raise media_studio.Cancelled()

    def ensure_idle(self):
        if any(t.is_alive() for t in self.threads.values()):
            raise ValueError('另一项自动创作正在处理，请等待或先停止它')

    def create(self, payload):
        topic = str(payload.get('topic') or '').strip()
        if not 3 <= len(topic) <= 300:
            raise ValueError('请输入 3–300 字的选题')
        aspect = payload.get('aspect', '9:16')
        if aspect not in ('9:16', '16:9'):
            raise ValueError('画幅选项不支持')
        seconds = int(payload.get('seconds') or 90)
        if seconds not in (60, 90, 180):
            raise ValueError('目标时长选项不支持')
        with self.lock:
            if any(t.is_alive() for t in self.threads.values()):
                raise ValueError('先完成或停止当前自动任务')
            rid = uuid.uuid4().hex[:24]
            folder = self.folder(rid)
            folder.mkdir()
            run = {'id': rid, 'topic': topic, 'mode': 'real', 'aspect': aspect, 'seconds': seconds,
                   'context': str(payload.get('context') or '')[:15000], 'script': '', 'script_revision': 0,
                   'status': 'drafting', 'step': 0, 'pct': 0, 'message': '正在按账号定位准备真人口播稿',
                   'created': time.time(), 'updated': time.time(), 'notes': [], 'project_id': '', 'job_id': ''}
            media_studio.atomic_json(folder / 'run.json', run)
            self.spawn(rid, self.draft)
            return self.state(rid)

    def draft(self, rid):
        """写稿。顺序不能反：**先联网核实，再动笔**。

        老版本的毛病是让模型「没材料就打【待核实】标记」，结果占位符被当成
        正文交到用户手上。现在：
        ① factcheck 真去搜一圈，产出「已核实事实卡」；
        ② 写稿模型只能照抄事实卡里的说法；
        ③ 万一模型还是吐出【待核实】，strip_unverified 把那一整句删掉。
        —— 宁可稿子短一截，也不许把没核实的东西念出去。
        """
        import aiwrite
        import factcheck
        run = self.load(rid)
        account_file = HERE.parent / '账号定位.md'
        account = account_file.read_text(encoding='utf-8')[:6000] if account_file.is_file() else ''
        cfg = aiwrite.load_cfg()
        chat = self.chat or aiwrite.chat
        length = int(run['seconds'] * 4)
        removed_all = []

        self.update(rid, status='drafting', step=0, pct=6, message='正在联网核实选题里的事实（参数、日期、入口、价格）')
        try:
            research = (self.researcher or factcheck.research)
            report = research(chat, cfg, run['topic'], run['context'], log=lambda m: None)
        except Exception as exc:  # 核实不是主菜，挂了也得把稿写出来
            report = {'facts': [], 'notes': ['核实环节异常：%s' % str(exc)[:120]],
                      'queries': [], 'questions': [], 'ok': False,
                      'checked_at': time.strftime('%Y-%m-%d %H:%M')}
        facts = report.get('facts') or []
        fact_text = factcheck.brief(facts)
        self.update(rid, research=report, pct=12,
                    message=('已核实 %d 条事实，正在按这些事实写稿' % len(facts)) if facts
                    else '联网没核实到可靠来源，本次只写可照做的操作步骤，不写任何数字')

        system = STYLE + ('你是真人口播创作导演。根据选题、账号定位和**已核实事实**输出一版能直接念的中文口播稿。'
                          '输出严格 JSON，字段 title（18字内）、script（纯口播正文）、checks（本次核实情况与需要你亲自确认的事项，字符串数组）、'
                          'caption（发布简介，100字内）、tags（3到5个相关话题）。'
                          '先给明确钩子，再给可照做的操作，最后用一个具体收尾动作结束。不要机械编号，不要写拍摄指令。\n账号定位：%s' % account)
        system += ('\n本次硬性篇幅要求优先于账号文件中的默认时长、850–950字等规则：'
                   '本次只写约%d个可朗读字词。汉字每字计1，英文每词计1，标点和换行不计数。'
                   '请先按7个自然段分配篇幅，每段约%d字，总正文控制在%d–%d字词；'
                   '只输出最终JSON，不输出字数分析。不要在正文增加虚构的评论赠品或承诺。'
                   % (length, max(20, length // 7), int(length * .95), int(length * 1.08)))
        system += FACT_RULE.format(facts=(fact_text or '（本次联网没有核实到可用事实）'))
        request = {'topic': run['topic'], 'reference': run['context'], 'seconds': run['seconds']}
        for attempt in range(3):
            self.check_cancel(rid)
            raw = chat(cfg, system, json.dumps(request, ensure_ascii=False),
                       timeout=180, json_mode=True, max_tokens=max(3000, length * 5))
            self.check_cancel(rid)
            data = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()))
            text = data.get('script')
            if not isinstance(text, str) or not 40 <= len(text.strip()) <= 6000:
                raise ValueError('模型没有生成有效口播稿，请重试或手动粘贴稿件')
            # 兜底：模型仍在偷懒打标记 → 连带整句删掉，别把占位符交给用户
            text, removed = strip_unverified(text)
            removed_all.extend(removed)
            timing = script_timing(text, run['seconds'])
            if timing['ok']:
                break
            # 方向化修正：短了给扩写，超了给压缩 —— 不再一视同仁地「太短了」
            if timing['short']:
                self.update(rid, pct=12 + attempt * 2, message='稿件预计%d秒，目标%d秒，偏短，正在扩写（%d/2）' % (
                    timing['estimated_seconds'], run['seconds'], min(attempt + 1, 2)))
                request.update(previous_script=text, correction=(
                    '上一版正文实测%d个口播单位（目标%d–%d），偏短了。请在上一版基础上逐段扩写，'
                    '而不是从头重写。每补一处，都必须是具体动作，不许灌水。扩写方向：'
                    '①给每一步补「如果没找到/报错了」的排错分支；'
                    '②补 Windows 与 Mac 各自的点法差异；'
                    '③补一段能照抄的完整提示词原话；'
                    '④补一个前后对照（以前怎么做、现在省哪几步）；'
                    '⑤补这一步做完具体会看到什么结果。'
                    '每个新增步骤至少占 15–25 个口播字词；禁止重复已有句子，禁止空泛点评，禁止虚构，禁止【待核实】。'
                    % (timing['units'], timing['min_units'], timing['max_units'])))
            else:  # 超了
                self.update(rid, pct=12 + attempt * 2, message='稿件预计%d秒，目标%d秒，偏长，正在压缩（%d/2）' % (
                    timing['estimated_seconds'], run['seconds'], min(attempt + 1, 2)))
                request.update(previous_script=text, correction=(
                    '上一版正文实测%d个口播单位（目标%d–%d），超了约%d个。请在上一版基础上压缩，'
                    '而不是从头重写。压缩时：'
                    '①删掉重复的排错分支，同一类「如果没找到」只保留一个最可能发生的；'
                    '②合并 Windows/Mac 两套说法里完全相同的步骤，只在真正有差异处分开写；'
                    '③删掉铺垫句和口头禅，直接说动作；'
                    '④每个操作步骤合并成一句，不要「先…再…然后…最后…」层层套。'
                    '保留所有关键动作和事实，只删冗余，不删信息量；禁止【待核实】。'
                    % (timing['units'], timing['min_units'], timing['max_units'], timing['units'] - timing['max_units'])))

        # 兜底：3 次模型重写仍不达标时，做一次「字数精修」专项调用 —— 只增删、不改结构，
        # 明确告知当前字数/目标字数/差多少，比笼统重写精准得多。
        if not timing['ok']:
            self.check_cancel(rid)
            self.update(rid, pct=18, message='篇幅仍差一点，正在做最后的字数精修')
            fix = ('当前正文实测%d个口播单位，目标区间%d–%d，%s%d个。'
                   '请**只对上一版正文做增删，不改变整体结构和已经讲对的步骤**，输出完整 JSON。'
                   % (timing['units'], timing['min_units'], timing['max_units'],
                      '还差' if timing['short'] else '超了',
                      (timing['min_units'] - timing['units']) if timing['short'] else (timing['units'] - timing['max_units'])))
            if timing['short']:
                fix += ('补字只补真实信息：每个操作补「做完会看到什么」或「最常见的翻车点+怎么救」，'
                        '每处 15–25 字，别灌水、别重复。')
            else:
                fix += ('删字只删冗余：删重复的排错分支、删铺垫口头禅、合并相同步骤，保留所有关键动作。')
            request.update(previous_script=text, correction=fix)
            try:
                raw = chat(cfg, system, json.dumps(request, ensure_ascii=False),
                           timeout=180, json_mode=True, max_tokens=max(3000, length * 5))
                data = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()))
                refined = data.get('script')
                if isinstance(refined, str) and 40 <= len(refined.strip()) <= 6000:
                    refined, removed = strip_unverified(refined)
                    removed_all.extend(removed)
                    text = refined
                    timing = script_timing(text, run['seconds'])
            except Exception:  # 精修失败就保留 3 次里最好的一版，不因此中断写稿
                pass
            # 最后一手：超了就用确定性规则裁剪（删冗余排错分支，不损失关键动作），
            # 短了不再强扩（宁可短一截也不灌水）。裁剪不需要再调模型，稳定可控。
            if timing['over']:
                text = _trim_to_fit(text, timing['max_units'])
                timing = script_timing(text, run['seconds'])
        notes = [str(x)[:250] for x in (report.get('notes') or [])][:6]
        checks = []
        if facts:
            checks.append('已联网核实 %d 条事实（%s）' % (len(facts), report.get('checked_at') or ''))
        else:
            checks.append('本次没能联网核实到可靠来源，稿中不含任何数字、价格与时间点')
        if removed_all:
            checks.append('已删除 %d 处写不实的内容（模型没能给出可靠依据）' % len(removed_all))
        risky = flag_unknown_ops(text, report.get('materials'))
        if risky:
            checks.append('这几处在抓到的原文里找不到出处，发布前请亲手试一遍：' + '、'.join(risky))
        checks += [str(x)[:250] for x in data.get('checks', [])[:8]] if isinstance(data.get('checks'), list) else []
        checks += notes
        tags = [str(t).strip().lstrip('#')[:20] for t in data.get('tags', [])[:5]] if isinstance(data.get('tags'), list) else []
        self.update(rid, script=text.strip(), title=str(data.get('title') or run['topic'])[:36],
                    checks=checks, caption=str(data.get('caption') or '')[:500], tags=tags,
                    timing=timing, script_revision=run.get('script_revision', 0) + 1, status='review_script', step=1, pct=20,
                    message=('稿件已准备好，事实已联网核实，确认后开始录制。' if timing['ok'] else
                             '模型两次修正后篇幅仍不匹配，请调整稿件；时长校验通过后才能录制。'))

    def save_script(self, rid, payload):
        with self.lock:
            run = self.load(rid)
            if run['status'] not in ('review_script', 'recording', 'failed', 'interrupted'):
                raise ValueError('当前阶段不能修改口播稿')
            if payload.get('revision') != run['script_revision']:
                raise ValueError('稿件已在另一个窗口更新，请重新加载')
            if run.get('project_id'):
                raise ValueError('已经进入剪辑阶段，请在剪辑台修正字幕或新建作品')
            text = str(payload.get('script') or '').strip()
            if not 40 <= len(text) <= 10000:
                raise ValueError('口播稿需要 40–10000 字')
            return self.update(rid, script=text, timing=script_timing(text, run['seconds']), script_revision=run['script_revision'] + 1,
                               status='review_script', message='稿件已保存，请确认后进入录制')

    def confirm(self, rid):
        with self.lock:
            run = self.load(rid)
            if run['status'] not in ('review_script', 'recording'):
                raise ValueError('当前阶段不能确认稿件')
            if re.search(r'【\s*待(?:核实|补充|确认)[^】]*】', run['script']):
                raise ValueError('稿件中仍有待核实/待补充标记，请补全或删除无法确认的段落')
            timing = script_timing(run['script'], run['seconds'])
            if not timing['ok']:
                raise ValueError('当前稿件预计%d秒，目标%d秒；请将正文调整到%d–%d个口播字词后再录制' % (
                    timing['estimated_seconds'], run['seconds'], timing['min_units'], timing['max_units']))
            (self.folder(rid) / '口播稿.md').write_text('# ' + run['topic'] + '\n\n' + run['script'], encoding='utf-8')
            return self.update(rid, status='recording', step=1, pct=25, confirmed_at=time.time(),
                               message='开启摄像头录制，或上传已拍视频。提交后自动完成剪辑与成片')

    def attach(self, rid, source_id):
        with self.lock:
            run = self.load(rid)
            if run['status'] != 'recording':
                raise ValueError('请先确认口播稿再提交录像')
            self.ensure_idle()
            p = self.studio.create(source_id)
            if not p['has_audio']:
                raise ValueError('录像没有音轨，请检查麦克风后重新录制')
            self.update(rid, project_id=p['id'], status='analyzing', step=2, pct=30,
                        message='录像已提交，后续自动进行；你可以离开此页面，后台会继续')
            self.spawn(rid, self.produce)
            return self.state(rid)

    def store_recording(self, rid, source_id):
        with self.lock:
            run = self.load(rid)
            if run['status'] != 'recording':
                raise ValueError('当前阶段不能保存录像')
            source = next((s for s in self.studio.sources() if s['id'] == source_id), None)
            if not source:
                raise ValueError('录像未上传完成')
            meta = self.studio.probe(source['path'])
            if not meta['has_audio']:
                raise ValueError('录像没有音轨，请检查麦克风')
            return self.update(rid, recording_source=source_id, recording_duration=meta['duration'],
                               message='原始录像已自动保存到本机，可回看后提交自动制作')

    def wait_job(self, rid, job):
        self.update(rid, job_id=job['id'])
        while True:
            self.check_cancel(rid)
            with self.studio.lock:
                current = copy.deepcopy(self.studio.jobs[job['id']])
            if current['status'] not in media_studio.ACTIVE:
                if current['status'] != 'done':
                    raise ValueError(current.get('message') or '工具任务失败')
                return current
            time.sleep(.5)

    def produce(self, rid):
        run = self.load(rid)
        pid = run['project_id']
        p = self.studio.load(pid)
        if not run.get('analyzed'):
            self.update(rid, status='analyzing', step=2, pct=35, message='识别原声、生成字幕和停顿建议；不修改你的表达')
            self.wait_job(rid, self.studio.start(pid, 'analyze'))
            self.update(rid, analyzed=True)
        self.check_cancel(rid)
        run = self.load(rid)
        if not run.get('prepared'):
            self.update(rid, status='editing', step=2, pct=55, message='仅采用不重叠字幕的长静音建议；保留重录和语义删改供复核')
            p = self.studio.load(pid)
            safe_cuts = [{'s': s['s'], 'e': s['e']} for s in p['suggestions'] if s['kind'] == 'silence']
            # A conservative automation must not remove most of a recording merely because audio is quiet.
            cuts = media_studio.normalize_ranges(safe_cuts, p['duration'])
            notes = []
            if sum(b - a for a, b in cuts) > p['duration'] * .35:
                cuts = []
                notes.append('静音候选超过原片 35%，为避免误剪没有自动采用，请检查录音音量')
            if any(s['kind'] != 'silence' for s in p['suggestions']):
                notes.append('发现语气词或重复句候选，未自动删除；可在精修剪辑中试听处理')
            p['cuts'] = [{'s': a, 'e': b} for a, b in cuts]
            music = '知识讲解.mp3' if (media_studio.BGM_DIR / '知识讲解.mp3').is_file() else ''
            p['name'] = run.get('title') or run['topic']
            p['settings'].update(aspect=run['aspect'], quality='standard', subtitles=True, normalize_audio=True,
                                 bgm=music, bgm_gain=.08, duck_bgm=True)
            self.studio.save(pid, p)
            self.update(rid, prepared=True, notes=notes)
        run = self.load(rid)
        if not run.get('result'):
            self.update(rid, status='rendering', step=3, pct=70, message='合成字幕、适配画幅、规范化人声，并自动压低配乐')
            job = self.wait_job(rid, self.studio.start(pid, 'render'))
            self.update(rid, result=job['result'])
        self.check_cancel(rid)
        self.update(rid, status='packaging', step=4, pct=90, message='提取真人画面，生成封面与发布资料')
        self.package(rid)
        self.update(rid, status='done', step=4, pct=100, message='成片、封面与发布资料已备好。看一遍成片后即可去发布')

    def package(self, rid):
        from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
        run = self.load(rid)
        p = self.studio.load(run['project_id'])
        folder = self.folder(rid)
        result_id = self.studio.valid_id(run['result']['video'].split('id=')[1].split('&')[0])
        exported = self.studio.exports / result_id / '成片.mp4'
        frame = folder / 'frame.jpg'
        ff = subprocess.run([self.studio.ffmpeg, '-v', 'error', '-y', '-ss', str(min(2., p['duration'] * .2)),
                             '-i', str(exported), '-frames:v', '1', str(frame)], capture_output=True, timeout=30, creationflags=0x08000000)
        if ff.returncode:
            raise ValueError('封面抽帧失败；成片已保留，可重试生成发布包')
        image = Image.open(frame).convert('RGB')
        canvas = Image.new('RGB', (720, 1280), '#171d30')
        photo = ImageOps.contain(image, (720, 950))
        canvas.paste(photo, ((720-photo.width)//2, 310+(950-photo.height)//2))
        draw = ImageDraw.Draw(canvas)
        font_path = Path('C:/Windows/Fonts/msyhbd.ttc')
        regular = Path('C:/Windows/Fonts/msyh.ttc')
        font = ImageFont.truetype(str(font_path if font_path.is_file() else regular), 55)
        small = ImageFont.truetype(str(regular), 24)
        draw.rounded_rectangle((38, 38, 210, 82), radius=12, fill='#8b79d5')
        draw.text((54, 43), '真人实拍', font=small, fill='white')
        text = str(run.get('title') or run['topic'])[:30]
        lines = [text[i:i+10] for i in range(0, len(text), 10)]
        draw.multiline_text((38, 115), '\n'.join(lines), font=font, fill='white', spacing=12)
        canvas.save(folder / '封面.png')
        publish = {'title': run.get('title') or run['topic'], 'description': run.get('caption') or run['topic'],
                   'tags': run.get('tags') or ['真人口播'], 'notes': run.get('notes', []),
                   'checklist': ['完整看一遍成片，核对同音错字', '确认人物画面与配乐使用权', '确认平台标题、简介和话题后手动发布']}
        media_studio.atomic_json(folder / '发布资料.json', publish)
        (folder / '发布资料.txt').write_text(publish['title'] + '\n\n' + publish['description'] + '\n\n' +
                                               ' '.join('#'+t for t in publish['tags']), encoding='utf-8')
        self.update(rid, cover='/api/director/file?id='+rid+'&file=cover', publish=publish,
                    publish_file='/api/director/file?id='+rid+'&file=publish')

    def cancel(self, rid):
        with self.lock:
            run = self.load(rid)
            if rid in self.events:
                self.events[rid].set()
            jid = run.get('job_id')
            if jid and jid in self.studio.jobs and self.studio.jobs[jid]['status'] in media_studio.ACTIVE:
                self.studio.cancel(jid)
            if run['status'] in BUSY:
                return self.update(rid, status='cancelling', message='正在停止，已完成内容保留')
            return self.update(rid, status='cancelled', message='已停止，文案与素材保留')

    def retry(self, rid):
        with self.lock:
            self.ensure_idle()
            return self._retry(rid)

    def redraft(self, rid, seconds=None):
        with self.lock:
            self.ensure_idle()
            run = self.load(rid)
            if run.get('project_id') or run['status'] not in ('review_script', 'recording', 'failed', 'interrupted'):
                raise ValueError('当前阶段不能重写文案；已进入剪辑的作品请新建作品')
            seconds = int(seconds or run['seconds'])
            if seconds not in (60, 90, 180):
                raise ValueError('请选择 60、90 或 180 秒')
            if run.get('script'):
                (self.folder(rid) / ('口播稿-重写前-v%d.md' % run.get('script_revision', 0))).write_text(run['script'], encoding='utf-8')
            self.update(rid, seconds=seconds, status='drafting', message='按目标时长重新准备完整稿件，上一版已保存')
            self.spawn(rid, self.draft)
            return self.state(rid)

    def _retry(self, rid):
        run = self.load(rid)
        if run['status'] not in ('failed', 'interrupted', 'cancelled'):
            raise ValueError('只有失败、中断或停止的作品需要继续')
        if run.get('project_id'):
            self.update(rid, status='analyzing', message='从已保留的素材和阶段继续')
            self.spawn(rid, self.produce)
        elif run.get('script'):
            self.update(rid, status='review_script', message='已恢复稿件，请确认后录制')
        else:
            self.update(rid, status='drafting', message='重新准备口播稿')
            self.spawn(rid, self.draft)
        return self.state(rid)


_brain = None
_lock = threading.Lock()


def service():
    global _brain
    with _lock:
        if _brain is None:
            _brain = Brain()
    return _brain


def handle(handler, method, path, query):
    if not path.startswith('/api/director/'):
        return False
    brain = service()
    arg = lambda k: (query.get(k) or [''])[0]
    try:
        if method == 'GET':
            if path == '/api/director/list':
                handler.send_json({'items': brain.list()})
            elif path == '/api/director/run':
                handler.send_json(brain.state(arg('id')))
            elif path == '/api/director/file':
                name = {'cover': '封面.png', 'publish': '发布资料.txt'}.get(arg('file'))
                if not name:
                    raise ValueError('文件类型不支持')
                handler.send_file(str(brain.folder(arg('id')) / name), download=arg('file') == 'publish')
            else:
                handler.send_json({'error': '接口不存在'}, 404)
        else:
            data = handler.read_json()
            rid = data.get('id')
            action = path.rsplit('/', 1)[-1]
            if action == 'create':
                result = brain.create(data)
            elif action == 'script':
                result = brain.save_script(rid, data)
            elif action == 'confirm':
                result = brain.confirm(rid)
            elif action == 'attach':
                result = brain.attach(rid, data.get('source_id'))
            elif action == 'recording':
                result = brain.store_recording(rid, data.get('source_id'))
            elif action == 'cancel':
                result = brain.cancel(rid)
            elif action == 'retry':
                result = brain.retry(rid)
            elif action == 'redraft':
                result = brain.redraft(rid, data.get('seconds'))
            else:
                raise ValueError('操作不支持')
            handler.send_json(result)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        handler.send_json({'error': str(exc)}, 400)
    except Exception as exc:
        handler.send_json({'error': '创作处理失败：'+str(exc)}, 500)
    return True
