# -*- coding: utf-8 -*-
"""
AI 博主工作台 · 本地服务
=========================
一条线：选题 -> 文案 -> 提词 -> 剪辑 -> 发布 -> 复盘。
一个服务、一个页面、左侧流程导航。只监听 127.0.0.1，纯标准库，不装任何东西。

剪辑环节复用已验证的 pipeline.py / reburn.py（口播一键成片引擎）。
选题/文案环节直接读你项目里的 md 文件，不需要你手填。
"""

import argparse
import glob
import http.server
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

# ---------------------------------------------------------------- 路径
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.get("EMOTIONCUT_BLOGGER_DATA_ROOT", os.path.dirname(HERE))
KOUBAO = os.path.join(PROJ, "全自动生产线", "口播一键成片")
PIPELINE = os.path.join(KOUBAO, "pipeline.py")
REBURN = os.path.join(KOUBAO, "reburn.py")

PAGE = os.path.join(HERE, "工作台.html")
# Remotion 剪辑轨道（motion-engine：props/初剪片/段渲染）
ME_DIR = os.path.join(os.path.dirname(HERE), "motion-engine")
CLIP_DIR = os.path.join(PROJ, "成片库")            # 渲染成片统一放这里，不再混进 motion-engine
os.makedirs(CLIP_DIR, exist_ok=True)
# 两段式重构（2026-10-17）：口播底片工厂（第一部分）+ 动效工作台（第二部分）
import voice_factory as _vf        # noqa: E402  模式一管线（三件套产出）
import media_studio as _cut        # 非破坏口播剪辑、持久任务、审核建议与真实导出
import creation_brain as _director
import creator_workspace as _spaces
import avatar_studio as _avt
import ttsfree                     # noqa: E402  配音引擎（音色清单）
import motion_json as _mj          # noqa: E402  动效 JSON schema/校验/注册表
import llm_brain as _brain         # noqa: E402  编导大脑 v2（LLM 编排，chat 可插拔）
MJ_DIR = os.path.join(HERE, "动效库")
os.makedirs(MJ_DIR, exist_ok=True)
MJ2_JOBS = {}
MJ2_LOCK = threading.Lock()
ME_NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
ME_NODE_PATH = r"C:\Users\Administrator\.workbuddy\binaries\node\workspace\node_modules"
WORK = os.environ.get("EMOTIONCUT_BLOGGER_WORK_ROOT", os.path.join(HERE, "工作区"))
IN_DIR = os.path.join(WORK, "输入")

# ---- Remotion Studio（可视化编辑器，7875 常驻） ----
STUDIO_PORT = 7875


def studio_alive():
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", STUDIO_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def studio_start(props_name="props_gold.json"):
    """拉起 Remotion Studio（后台常驻）。返回 (ok, msg)。"""
    if studio_alive():
        return True, "已在运行"
    cli = os.path.join(ME_DIR, "node_modules", "@remotion", "cli", "remotion-cli.js")
    if not os.path.isfile(cli):
        return False, "缺 @remotion/cli"
    env = dict(os.environ, NODE_PATH=ME_NODE_PATH)
    try:
        subprocess.Popen(
            [ME_NODE, cli, "studio", "src/index.ts",
             "--props=" + props_name, "--port", str(STUDIO_PORT)],
            cwd=ME_DIR, env=env, creationflags=0x08000000,  # CREATE_NO_WINDOW
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, "启动失败: %s（可双击 motion-engine\\启动RemotionStudio.bat）" % e
    import time
    for _ in range(30):  # 最多等 15s
        time.sleep(0.5)
        if studio_alive():
            return True, "已启动"
    return False, "启动超时（可双击 motion-engine\\启动RemotionStudio.bat）"

OUT_DIR = os.path.join(WORK, "输出")
CACHE_DIR = os.path.join(WORK, "缓存")
COVER_DIR = os.path.join(WORK, "封面")
PUBLISH_DIR = os.path.join(WORK, "发布")
LOG_FILE = os.path.join(WORK, "服务日志.txt")
REVIEW_JSON = os.path.join(WORK, "复盘数据.json")
BASELINE_JSON = os.path.join(WORK, "账号基线.json")
BENCH_DIR = os.path.join(WORK, "对标库")          # 对标拆解报告落这里
SKILLS_DIR = os.path.join(HERE, "skills")          # 嵌入的第三方 skill
BENCH_PY = os.path.join(HERE, "bench.py")
WA_PY = os.path.join(HERE, "webaccess.py")         # 对标浏览器驱动
WA_RESULT_DIR = os.path.join(BENCH_DIR, "_结果")    # 搜索/抓取的结构化结果
AI_PY = os.path.join(HERE, "aiwrite.py")           # AI 写稿
AI_CFG = os.path.join(WORK, "模型设置.json")
LEYIA_OUT = os.path.join(COVER_DIR, "leyia生图")   # 隐藏生图入口的产出目录
BGM_DIR = os.path.join(WORK, "BGM曲库")            # 三层曲库（本机打底，可自行丢文件扩充）
DH_DIR = os.path.join(WORK, "数字人")               # 块②③素材：音色参考/形象照（永久配置）
DH_SET = os.path.join(DH_DIR, "数字人设置.json")     # 块②③ API Key 槽位
SKILL_COVER = os.path.join(HERE, "skills", "封面skill")
SPEC_PATH = os.path.join(SKILL_COVER, "references", "封面设计规范.md")
SPEC_VER = os.path.join(SKILL_COVER, "versions")
WA_PORT = 9333                                     # 对标浏览器调试端口

# 项目数据源（只读）
TOPIC_OVERVIEW = os.path.join(PROJ, "选题库", "选题情报总览.md")
CANDIDATES = os.path.join(PROJ, "candidates.md")
HOOK_LIB = os.path.join(PROJ, "钩子库.md")
POSITION = os.path.join(PROJ, "账号定位.md")
TOPIC_DIR = os.path.join(PROJ, "选题库")
RESEARCH_DIR = os.path.join(TOPIC_DIR, "调研")     # 选题调研报告落这里
JHS_METHOD = os.path.join(PROJ, "姜胡说内容方法论-AI赛道应用版.md")
SCRIPTS_DIR = os.path.join(PROJ, "scripts")
PRED_DIR = os.path.join(PROJ, "predictions")

for d in (WORK, IN_DIR, OUT_DIR, CACHE_DIR, COVER_DIR, PUBLISH_DIR, BENCH_DIR, WA_RESULT_DIR, LEYIA_OUT):
    os.makedirs(d, exist_ok=True)

sys.path.insert(0, HERE)   # 让 aiwrite.py 能被 import

PY = sys.executable
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".m4v", ".webm", ".ts"}

PILLARS = {
    "①": "① AI 工具上手教学",
    "②": "② AI 知识 / 防坑科普",
    "③": "③ 我的开源 skill / 工具实战",
}


# ---------------------------------------------------------------- 任务状态（剪辑环节）
JOB_LOCK = threading.Lock()
JOB = {
    "seq": 0, "id": 0, "mode": None, "running": False, "done": False, "ok": False,
    "lines": [], "started": 0.0, "elapsed": 0.0,
    "src_name": "", "src_path": "", "out_path": "", "draft_path": "", "cues_path": "",
    "out_size": 0, "rev": 0, "proc": None,
}


def job_reset(mode, src_name="", src_path=""):
    with JOB_LOCK:
        JOB["seq"] += 1
        JOB.update({
            "id": JOB["seq"], "mode": mode, "running": True, "done": False, "ok": False,
            "lines": [], "started": time.time(), "elapsed": 0.0,
            "src_name": src_name, "src_path": src_path,
            "out_path": "", "out_size": 0, "rev": JOB["rev"] + 1, "proc": None,
        })
        return JOB["id"]


def job_push(line):
    line = line.rstrip("\r\n")
    if not line:
        return
    with JOB_LOCK:
        JOB["lines"].append(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def job_finish(ok, out_path=""):
    with JOB_LOCK:
        JOB["running"] = False
        JOB["done"] = True
        JOB["ok"] = bool(ok)
        JOB["elapsed"] = time.time() - JOB["started"]
        if out_path:
            JOB["out_path"] = out_path
            try:
                JOB["out_size"] = os.path.getsize(out_path)
            except OSError:
                JOB["out_size"] = 0


def pump(proc):
    try:
        for line in iter(proc.stdout.readline, ""):
            # webaccess.py 会吐一坨 WA_JSON=…（原始结果），后台自己会解析它，
            # 没必要铺到进度窗口里吓用户。
            if line.startswith("WA_JSON="):
                continue
            job_push(line)
    except Exception as exc:  # noqa: BLE001
        job_push("[服务] 读取输出出错：%s" % exc)
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
    proc.wait()
    return proc.returncode


# ---------------------------------------------------------------- 通用小工具
def human(n):
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n


def safe_name(name, fallback="input.mp4"):
    name = os.path.basename(urllib.parse.unquote(name or "")).strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or fallback


def safe_stem(title, fallback="未命名"):
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (title or "").strip())
    t = t.strip("_ .")
    return (t or fallback)[:60]


def read_text(path):
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def pick_port(preferred):
    for p in range(preferred, preferred + 25):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return preferred


def kill_stale_on_port(port):
    """双击 bat 永远拿到最新代码：若首选端口被旧的工作台进程（python）占着，清掉它。

    只杀 python 进程，且只处理工作台专用端口段，不会碰到用户其他程序。
    """
    try:
        r = subprocess.run(["cmd", "/c", "netstat", "-ano"],
                           capture_output=True, timeout=15)
        out = r.stdout.decode("gbk", errors="replace")
    except Exception:
        return
    pids = set()
    for line in out.splitlines():
        if (":%d " % port) in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                pids.add(parts[-1])
    for pid in pids:
        try:
            t = subprocess.run(["cmd", "/c", "tasklist", "/FI", "PID eq %s" % pid,
                                "/FO", "CSV", "/NH"], capture_output=True, timeout=15)
            name = (t.stdout.decode("gbk", errors="replace").split(",")[0]
                    or "").strip('"')
        except Exception:
            name = ""
        if "python" in name.lower():
            subprocess.run(["cmd", "/c", "taskkill", "/F", "/PID", pid],
                           capture_output=True)
            print("  检测到旧的工作台进程（PID %s），已停掉，换上新版本。" % pid)
            time.sleep(1)


def open_browser(url):
    cands = [
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if os.path.isfile(c):
            try:
                subprocess.Popen([c, "--new-window", url])
                return c
            except OSError:
                continue
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url])
        return "cmd start"
    except OSError:
        pass
    try:
        webbrowser.open(url)
        return "默认浏览器"
    except Exception:  # noqa: BLE001
        pass
    return ""


def reveal(target):
    if target and os.path.exists(target):
        if os.path.isdir(target):
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
    else:
        subprocess.Popen(["explorer", OUT_DIR])


# ---------------------------------------------------------------- 选题情报解析
BULLET_RE = re.compile(r"^\s*[-*]\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$")
BULLET_PLAIN_RE = re.compile(r"^\s*[-*]\s*(.{1,14}?)\s*[:：]\s*(.+)$")
H3_RE = re.compile(r"^###\s+(.*)$")
H2_RE = re.compile(r"^##\s+(.*)$")
SEC_RE = re.compile(r"^#{2,3}\s*([AB])[\.、\s]")
ITEM_HASH_RE = re.compile(r"^###\s+(?:\d+[\.、\s]*)?(.*)$")
ITEM_NUM_RE = re.compile(r"^\s*(\d{1,2})[\.、]\s+(.*)$")
NUM_HEAD_RE = re.compile(r"^\s*(\d{1,2})[\.、]\s*")


def _bullets(lines):
    """把一段里的 - key：value 收集成 dict（key 保序）。兼容 **key** 和裸 key。"""
    out = {}
    for ln in lines:
        s = ln.strip()
        if not s.startswith(("-", "*")):
            continue
        m = BULLET_RE.match(ln)
        if not m:
            m = BULLET_PLAIN_RE.match(ln)
        if not m:
            continue
        k = m.group(1).strip().strip("*")
        v = m.group(2).strip()
        if not k or len(k) > 16:
            continue
        out[k] = (out[k] + " / " + v) if k in out else v
    return out


def _clean_title(s):
    s = s.strip()
    m = re.match(r"^\*\*(.+?)\*\*\s*[—\-]?\s*(.*)$", s)
    if m:
        tail = m.group(2).strip(" —-")
        return m.group(1).strip() + (("  ·  " + tail) if tail else "")
    s = re.sub(r"^\*\*(.+?)\*\*$", r"\1", s)
    return s.strip()


def parse_intel():
    """解析 选题情报总览.md，取日期最新的那一段。兼容两套排版。"""
    txt = read_text(TOPIC_OVERVIEW)
    if not txt:
        return {"date": "", "facts": [], "hots": [], "raw_lines": 0}
    lines = txt.splitlines()

    marks = []
    for i, ln in enumerate(lines):
        m = re.match(r"^##\s*选题情报\s*·\s*(\d{4}-\d{2}-\d{2})", ln.strip())
        if m:
            marks.append((i, m.group(1)))
    if not marks:
        return {"date": "", "facts": [], "hots": [], "raw_lines": len(lines)}
    last_idx, date = max(marks, key=lambda x: x[1])
    end = len(lines)
    for i, ln in enumerate(lines):
        if i > last_idx and re.match(r"^##\s*选题情报\s*·", ln.strip()):
            end = i
            break
    block = lines[last_idx + 1:end]

    facts, hots = [], []
    c_raw, in_c = [], False      # C 推荐/沉淀提示等其它小节的原始行（选题页情报卡展示用）
    cur_sec = None
    cur_item = None
    buf = []

    def flush():
        nonlocal cur_item
        if cur_item is not None and cur_sec in ("A", "B"):
            cur_item["fields"] = _bullets(buf)
            if cur_item["title"]:
                (facts if cur_sec == "A" else hots).append(cur_item)
        cur_item = None

    for ln in block:
        s = ln.strip()
        if not s or s.startswith(">"):
            continue
        # 二级标题：A/B 段开新段，其它（如 C. 执行备注）结束当前段
        if s.startswith("## ") and not s.startswith("### "):
            if cur_item is not None:
                flush()
            buf = []
            in_c = False
            m2 = SEC_RE.match(s)
            cur_sec = m2.group(1) if m2 else None
            continue
        # 三级标题：可能是段标题（新排版 ### A./### C.）也可能是条目（旧排版 ### 1.）
        if s.startswith("### "):
            in_c = s.lstrip("#").strip().startswith("C")
            m2 = SEC_RE.match(s)
            if m2:
                if cur_item is not None:
                    flush()
                buf = []
                cur_sec = m2.group(1)
                continue
            if re.match(r"^###\s*[A-Za-z]\s*[\.、]", s):
                # 其它字母段（如 C. 今日推荐）——结束当前段
                if cur_item is not None:
                    flush()
                buf = []
                cur_sec = None
                continue
            title = _clean_title(s[4:])
            if title and title.strip("-* "):
                flush()
                buf = []
                cur_item = {"title": title, "idx": len(facts) + len(hots) + 1}
            continue
        # 数字条目（新排版 1. **标题**）
        mt = ITEM_NUM_RE.match(ln)
        if mt:
            title = _clean_title(mt.group(2))
            if title and title.strip("-* "):
                flush()
                buf = []
                cur_item = {"title": title, "idx": len(facts) + len(hots) + 1}
            continue
        if cur_item is not None:
            buf.append(ln)
        elif in_c and s:
            c_raw.append(s)
    flush()
    return {"date": date, "facts": facts, "hots": hots, "c_raw": c_raw,
            "raw_lines": len(lines)}


def parse_candidates():
    """解析 candidates.md 的池子。"""
    txt = read_text(CANDIDATES)
    if not txt:
        return []
    lines = txt.splitlines()
    items, cur, buf = [], None, []

    def flush():
        nonlocal cur
        if cur is not None:
            cur["fields"] = _bullets(buf)
            # 提取 tier / composite / 新三问
            m = re.search(r"composite\s*\(v\d\)\s*\*\*\s*[:：]\s*([\d\.]+)", cur["raw_head"])
            cur["composite"] = m.group(1) if m else ""
            items.append(cur)
        cur = None

    for ln in lines:
        s = ln.strip()
        if s.startswith("###"):
            flush()
            buf = []
            head = s.lstrip("#").strip()
            cur = {"raw_head": head, "title": re.sub(r"^\[[^\]]*\]\s*", "", head).strip(),
                   "tier": "", "status": ""}
            mt = re.match(r"^\[([^\]]+)\]", head)
            if mt:
                t = mt.group(1)
                cur["tier"] = "已消耗" if "已消耗" in t else ("白送" if "白送" in t else t)
            continue
        if cur is not None:
            buf.append(ln)
    flush()
    # composite 解析（composite 值在字段里）
    for it in items:
        f = it.get("fields", {})
        for k, v in f.items():
            if "composite" in k:
                m = re.search(r"([\d\.]+)", v)
                if m:
                    it["composite"] = m.group(1)
            if "状态" in k:
                it["status"] = v
    return items


def _candidates_backup():
    """改动 candidates.md 前先备份进 工作区\\缓存，删错了能翻回来。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.isfile(CANDIDATES):
            bak = os.path.join(CACHE_DIR, "candidates备份-%s.md"
                               % time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(CANDIDATES, bak)
    except OSError:
        pass


def candidates_delete(idx):
    """删除候选池第 idx 条（0 基，顺序与 parse_candidates 一致）。"""
    txt = read_text(CANDIDATES)
    if not txt.strip():
        return {"error": "候选池本来就是空的"}
    lines = txt.splitlines()
    marks = [i for i, ln in enumerate(lines) if ln.strip().startswith("###")]
    if idx < 0 or idx >= len(marks):
        return {"error": "条目编号过期了（池子刚变过），刷新页面再试"}
    start = marks[idx]
    ends = [m for m in marks if m > start]
    h2s = [i for i, ln in enumerate(lines) if ln.strip().startswith("## ") and i > start]
    end = min(ends + h2s + [len(lines)])
    _candidates_backup()
    kept = [ln for ln in (lines[:start] + lines[end:])]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n"
    with open(CANDIDATES, "w", encoding="utf-8") as f:
        f.write(out)
    job_push("[服务] 候选池删除了第 %d 条（删前已备份进 工作区\\缓存）" % (idx + 1))
    return {"ok": True, "left": len(marks) - 1}


def candidates_clear():
    """清空候选池：只留头部说明，条目全移走（删前备份）。"""
    txt = read_text(CANDIDATES)
    if txt.strip():
        _candidates_backup()
        lines = txt.splitlines()
        first_h2 = next((i for i, ln in enumerate(lines)
                         if ln.strip().startswith("## ")), len(lines))
        head = "\n".join(lines[:first_h2]).rstrip()
        with open(CANDIDATES, "w", encoding="utf-8") as f:
            f.write(head + "\n\n## 池子\n\n")
        job_push("[服务] 候选池已清空（清空前的完整版在 工作区\\缓存）")
    return {"ok": True}


def parse_hooks():
    """解析 钩子库.md，按分类聚合。"""
    txt = read_text(HOOK_LIB)
    groups, cur = [], None
    for ln in txt.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            cur = {"name": s[3:].strip(), "items": []}
            groups.append(cur)
        elif s.startswith("- ") and cur is not None:
            body = s[2:].strip()
            if body:
                cur["items"].append(body)
    return groups


# ---------------------------------------------------------------- 文案骨架
def build_outline(topic, pillar="①", extra=""):
    """按姜胡说八段骨架，拼出一篇可编辑的 3 分钟口播稿骨架。"""
    title = (topic or "未命名选题").strip()
    hooks = parse_hooks()
    hook_lines = []
    for g in hooks[:8]:
        if g["items"]:
            hook_lines.append("【%s】%s" % (g["name"], g["items"][0]))
    hook_txt = "\n".join(hook_lines[:6]) or "（钩子库暂无，去 钩子库.md 补）"

    pillar_name = PILLARS.get(pillar, PILLARS["①"])
    tpl = """# {title}

> 栏目：{pillar_name}
> 目标：3 分钟口播 · 真人出镜 · 起号期"结果前置 + 记录自己"口吻
> 用法：这是**骨架**不是成稿。观点和经历必须你自己长——把每段【】里的提示填成你自己的话。
> 弹药在下面"可用钩子"里，挑顺眼的挪进正文。

---

## ① 纠偏钩子（0:00–0:12）
【否定观众默认认知，一句话说反常识。示例："大部分人学 AI 的第一步就错了"】
可用钩子：
{hook_txt}

## ② 例证（0:12–0:40）
【描述一个观众身边真实存在的现象，让他"对，我就是这样"】

## ③ 粗粝比喻（0:40–1:00）
【把核心道理具象化，越土越传播。参考比喻库】

## ④ 本质 / 方法（1:00–1:50）
【讲透"为什么"；给两步以内的可操作动作，具体到"点哪里"】

## ⑤ 抬杠反驳（1:50–2:10）
【预判观众心里反驳你的话，先说出来再怼回去】

## ⑥ 价值拔高（2:10–2:30）
【把事拔到通用能力 / 趋势层】

## ⑦ 自我暴露（2:30–2:45）
【你自己的糗事 / 踩坑。禁止编造】

## ⑧ 行动指令（2:45–3:00）
【今天就能做的零门槛动作，具体到今晚干什么】
【固定收尾 + 留资钩子：评论区扣 XX 领取 <实物>】

---

{extra}
""".format(title=title, pillar_name=pillar_name, hook_txt=hook_txt,
           extra=("## 选题背景（自动带入）\n\n" + extra) if extra else "")
    return tpl


# ---------------------------------------------------------------- 发布文案
TAG_BANK = ["#AI", "#人工智能", "#AI工具", "#普通人学AI", "#AI教程", "#科技",
            "#AI副业", "#效率工具", "#干货分享", "#AI绘画", "#大模型", "#提示词"]


def build_publish(title):
    t = (title or "").strip() or "AI 口播成片"
    titles = [
        t,
        t + "，我用大白话讲明白了",
        "别再瞎折腾了：" + t,
        t + "（附我实操的全过程）",
        "普通人也能搞懂的" + t,
    ]
    desc = ("这条讲的是：%s。\n"
            "我是用大白话教普通人了解 / 用上 AI 的，不讲虚的，只讲你今天就能上手的。\n"
            "视频里的方法我自己天天在用，有问题评论区问我。") % t
    return {"titles": titles, "desc": desc, "tags": TAG_BANK, "formats": PLATFORM_FMT(t)}


# ---------------------------------------------------------------- 平台格式模板
def PLATFORM_FMT(t):
    """各平台发布格式模板（抖音 / 小红书 / 公众号 / 视频号）。"""
    return [
        {
            "platform": "抖音",
            "specs": "标题 ≤ 30 字 · 简介 ≤ 1000 字 · 话题 3-5 个 · 竖屏 9:16",
            "title": t if len(t) <= 20 else t[:20],
            "body": ("%s\n"
                     "（开头 3 秒先说反常识结论，别寒暄）\n"
                     "今天的方法我自己在用，有问题评论区问。") % t,
            "tags": "#AI #AI工具 #普通人学AI #干货分享",
            "tips": "抖音认「前 3 秒完播」和「评论互动」。标题放钩子，别放总结。",
        },
        {
            "platform": "小红书",
            "specs": "标题 ≤ 20 字 · 正文分段换行 · 标签 6-10 个",
            "title": ("0基础也能学会｜" + t)[:20],
            "body": ("姐妹们，这个真的有用👇\n\n"
                     "① 先说结论：%s\n"
                     "② 为什么：普通人最容易卡在哪一步\n"
                     "③ 怎么做：今晚就能试的一步\n\n"
                     "有问题评论区问，我都在") % t,
            "tags": "#AI工具 #AI教程 #普通人学AI #效率提升 #干货分享 #AI副业 #自我提升",
            "tips": "小红书认「收藏」。正文用 ①②③ 分段，首图第一行放大数字。",
        },
        {
            "platform": "公众号",
            "specs": "标题 ≤ 64 字 · 摘要 ≤ 120 字 · 正文用小标题",
            "title": t,
            "body": ("导语：一句话说清这篇解决了什么问题。\n\n"
                     "一、你可能一直卡在哪\n二、正确的做法是什么\n"
                     "三、今晚就能做的第一步\n\n"
                     "结语：把动作留一个，别贪多。"),
            "tags": "（公众号不吃话题标签，靠标题 + 在看 / 转发）",
            "tips": "公众号认「在看」和「转发」。结构清楚比辞藻重要。",
        },
        {
            "platform": "视频号",
            "specs": "标题 ≤ 22 字 · 简介 ≤ 100 字 · 挂话题 2-3 个",
            "title": t[:22],
            "body": "口播原声 + 字幕即可，视频号不重剪辑重「真实感」。",
            "tags": "#AI #人工智能 #效率工具",
            "tips": "视频号靠社交推荐传播，转发权重最高，收尾带一句“转给需要的人”。",
        },
    ]


# ---------------------------------------------------------------- 去 AI 味（Humanizer-zh 规则本地化）
# 规则id 对齐 Humanizer-zh 的 31 条模式（A-F 六组），这里做可执行的本地初筛。
HUMANIZE_RULES = [
    ("A1", "A 铺垫代替陈述", "不是 X 而是 Y", r"不是.{2,20}而是|(不仅|不只是?)是?.{2,20}更是",
     "这种假对比只抬语气、不加信息。直接说后半句的事实。"),
    ("A3", "A 铺垫代替陈述", "格言 / 伪深度", r"的本质是|是一种(态度|哲学|语言|智慧)|说到底，.{2,10}就是",
     "修辞没增加意思时，回到具体判断，别下大词。"),
    ("A4", "A 铺垫代替陈述", "起跑式铺垫", r"让我们(深入|来看|一起|聊聊)|接下来让我们|以下是你需要知道的|话不多说|废话不多说",
     "“接下来让我们看看”只预告不提供信息，直接给内容。"),
    ("A5", "A 铺垫代替陈述", "与假想敌辩论", r"不要误会|我并不是|并不是在(制造|贩卖)|肯定有人(会说|问)|有人可能会说",
     "没有具体内容的自我辩护删掉，直接讲你的主张。"),
    ("B7", "B 公式化节奏", "句式开头复读", r"__REPEAT_HEAD__",
     "连着几句同主语开头会拖沓，能并就并，保留动作和时间关系。"),
    ("B8", "B 公式化节奏", "破折号当万能连接", r"__DASH__",
     "反复用破折号制造悬念会显得套路，承担解释 / 转折时才留。"),
    ("B9", "B 公式化节奏", "限定词堆叠", r"(可能|也许|或许).{0,8}(可能|也许|或许)|在某种程度上.{0,10}一定程度",
     "叠着说同一层不确定，压缩成一个即可。"),
    ("C12", "C 拔高与借权威", "AI 高频词", r"赋能|至关重要|深入探讨|无缝(衔接|对接)?|闭环|在当今|值得注意的?是|需要指出|综上所述|众所周知",
     "这些词是 AI 浓度最高的地方。能换成具体动作就换，换不了就删。"),
    ("C13", "C 拔高与借权威", "意义拔高", r"标志着.{0,12}(新时代|新纪元|里程碑)|开启了.{0,8}新(篇章|时代)|具有里程碑意义",
     "删掉没有独立内容的“新时代”套话，保留真实计划和时间点。"),
    ("C15", "C 拔高与借权威", "句尾补充式拔高", r"彰显了|体现了.{0,8}(不懈追求|卓越)|充分体现了",
     "只重复赞美的尾巴删掉，保留有信息的原因和结果。"),
    ("C16", "C 拔高与借权威", "宣传语", r"堪称|可谓|不得不说|完美的?选择|梦想(天堂|之选)|天花板",
     "无内容的赞美减掉；主观评价就写“我觉得”，别写成结论。"),
    ("C17", "C 拔高与借权威", "借权威", r"(专家|业内人士|权威机构|研究表明|数据显示).{0,6}(认为|指出|表示|显示)",
     "笼统归因要保留“据说/可能”的限定，别转成确定事实。"),
    ("E22", "E 聊天与草稿残留", "客服腔", r"好问题|希望(这|对)您?(有|有所)?帮助|如有(疑问|需要)|感谢(您的)?(阅读|观看)|以上就是(我的)?分享",
     "独立成文的内容删掉无内容的问候和结束语。"),
    ("E23", "E 聊天与草稿残留", "知识边界免责", r"限于篇幅|由于时间关系|篇幅原因|由于篇幅所限",
     "该类免责删掉，不要藏资料缺口。"),
    ("E25", "E 聊天与草稿残留", "谈论上一稿", r"前面(改|说|提)过|刚才(提到|说到)|如上所述|上一(稿|版)",
     "编辑过程不属于正文，删。"),
    ("E26", "E 套路化口播", "廉价情绪钩子", r"先说一句扎心的|你是不是已经|你手指是不是|有人要抬杠|别急着划走|看到这里",
     "这类万能钩子跟具体选题无关。直接用产品结果、具体变化或实操效果开场。"),
    ("E27", "E 套路化口播", "泛鸡汤结论", r"真正拉开差距的|习惯不变.{0,20}还是|才有得聊|重要的从来不是|差的不是.{0,20}而是",
     "换一个产品名仍然成立，说明偏题。改成当前选题的具体事实、步骤或限制。"),
    ("E28", "E 套路化口播", "空行动号召", r"今晚就做|现在就去试试|随便哪个都行|随便问它|评论区说两件事",
     "行动必须包含具体工具、入口、输入示例和验收结果，不能只叫观众去试。"),
    ("F26", "F 中文表达补充", "层叠的“的”", r"的[\u4e00-\u9fa5]{1,4}的[\u4e00-\u9fa5]{1,4}的",
     "长定语读着累，把修饰关系拆成短句。"),
    ("F27", "F 中文表达补充", "“进行＋动词”", r"进行.{1,4}(测试|调整|优化|处理|分析|修改|讲解)",
     "换成直接动词：“进行测试”→“测试”。"),
    ("F29", "F 中文表达补充", "四字词排比", r"([\u4e00-\u9fa5]{2}[\u4e00-\u9fa5]{2}、){2,}[\u4e00-\u9fa5]{2}[\u4e00-\u9fa5]{2}",
     "空泛同义堆叠改自然，但真实的并列项别删。"),
    ("F30", "F 中文表达补充", "“随着……的发展”式开头", r"随着.{1,14}的(发展|进步|到来|普及)|在.{1,12}的时代背景下",
     "背景没有独立信息就压缩掉。"),
    ("F31", "F 中文表达补充", "套话收尾", r"总而言之|让我们一起|让我们拭目以待|期待(更多|未来)|共勉|以上就是全部",
     "删掉不提供信息的祝愿，保留实际总结和下一步。"),
]


# 提示级规则：误报率高（正常表达也常命中），只提示、不计分、不进修复循环。
# A1「不是X而是Y」口语里大量是正常对比；F29 四字排比在中文口播里多半是真实并列。
_HINT_RULE_IDS = {"A1", "F29"}


def humanize_check(text):
    txt = text or ""
    hits, hints = [], []
    for rid, group, name, pat, advice in HUMANIZE_RULES:
        samples = []
        if pat == "__REPEAT_HEAD__":
            sents = [s.strip() for s in re.split(r"[。！？\n]+", txt) if s.strip()]
            run, prev = [], ""
            for s in sents:
                head = s[:3]
                if head and head == prev:
                    run.append(s)
                else:
                    if len(run) >= 2:
                        samples.append(run[0][:40])
                    run, prev = [s], head
            if len(run) >= 2:
                samples.append(run[0][:40])
        elif pat == "__DASH__":
            for m in re.finditer(r"[^\n]{0,24}——[^\n]{0,24}——[^\n]{0,24}", txt):
                samples.append(m.group(0)[:60])
        else:
            for m in re.finditer(pat, txt, re.MULTILINE):
                samples.append(m.group(0)[:48])
        if not samples:
            continue
        # 去重：同一段话被同一规则扫出多份只算一次
        uniq = []
        for s in samples:
            if s not in uniq:
                uniq.append(s)
        item = {"id": rid, "group": group, "name": name,
                "count": len(uniq), "samples": uniq[:4], "advice": advice}
        (hints if rid in _HINT_RULE_IDS else hits).append(item)
    n = len(re.sub(r"\s", "", txt))
    total = sum(h["count"] for h in hits)
    hint_total = sum(h["count"] for h in hints)
    # 长文被切成大量“一句话一段”、连续反问，也是常见的机器口播模板；纳入风险而非永远 100 分。
    paragraphs = [p.strip() for p in re.split(r"\n+", txt) if p.strip()]
    short_ratio = (sum(1 for p in paragraphs if len(p) <= 28) / max(1, len(paragraphs)))
    structural = (2 if len(paragraphs) >= 12 and short_ratio > .72 else 0) + min(3, len(re.findall(r"[？?]", txt)) // 3)
    if structural:
        hits.append({"id": "S1", "group": "S 结构与节奏", "name": "碎句/反问模板化",
                     "count": structural, "samples": ["大量一句一段或连续反问"],
                     "advice": "按信息任务合并段落；用事实、演示和结果推进，不靠连续反问制造节奏。"})
    total += structural
    score = max(0, 100 - total * 4 - hint_total) if n else 0
    return {"hits": hits, "hints": hints, "chars": n, "score": score,
            "total": total, "hint_total": hint_total}


def humanize_prompt(text):
    return ("你是中文口播稿编辑。按 Humanizer-zh 的规则帮我改稿，要求：\n"
            "1) 不改事实、数字、限定（可能/据称/仅），不新增任何我没写的信息；\n"
            "2) 删掉空泛铺垫、假对比、意义拔高、客服腔、套话收尾；\n"
            "3) 保留我原本的口吻，别改成书面语；\n"
            "4) 输出「改后全文」+「改了什么（3 条以内）」。\n\n"
            "【原稿】\n" + (text or ""))


# ---------------------------------------------------------------- 技能库
SKILL_META = [
    {
        "dir": "web-access",
        "name": "web-access",
        "author": "一泽 Eze",
        "repo": "https://github.com/eze-is/web-access",
        "star": "8.7k",
        "tagline": "让 AI 用你的真实浏览器去搜、去抓、去扒",
        "what": "CDP 直连你本机 Chrome，带着登录态操作页面；可多标签并行抓取，"
                "专治动态页面、需要登录的站点、社交媒体内容。",
        "fit": "工作台里已经用它落地了「对标拆解」：给一条链接就自动解析 + 扒文案。",
    },
    {
        "dir": "Humanizer-zh",
        "name": "Humanizer-zh",
        "author": "藏师傅 op7418",
        "repo": "https://github.com/op7418/Humanizer-zh",
        "star": "11k",
        "tagline": "去 AI 味最强的中文 skill",
        "what": "31 条模式（A-F 六组）识别中文里的 AI 腔：假对比、意义拔高、客服腔、"
                "套话收尾、四字堆叠……并给出改写方向和保留边界。",
        "fit": "规则已内嵌到「文案」页的「去 AI 味自检」，粘贴稿子立刻出命中清单。",
    },
    {
        "dir": "short-video-remix-interviewer",
        "name": "short-video-remix-interviewer",
        "author": "tuihou123321",
        "repo": "https://github.com/tuihou123321/short-video-remix-interviewer",
        "star": "新入库（2026-09）",
        "tagline": "借爆款结构做原创：留选题/前5秒/结构，换掉对方素材",
        "what": "对标爆款只保留三样东西——选题、开头前 5 秒钩子、段落推进结构；"
                "对方的经历、客户案例、数据、成绩、身份背书、产品和行动号召全部删掉，"
                "换成你自己的；你没有的素材它会标出来让你补，不替你编。",
        "fit": "方法论已内嵌成「文案」页的「AI 改写（借结构做原创）」："
               "把对标拆解报告丢进「稿子」框，点一下就能出一版你自己口吻的原创口播稿。",
    },
    {
        "dir": "baoyu-skills",
        "name": "baoyu-skills",
        "author": "宝玉 JimLiu",
        "repo": "https://github.com/JimLiu/baoyu-skills",
        "star": "14k+",
        "tagline": "内容创作全家桶：配图 + 排版 + 一键发布",
        "what": "21 个子 skill，分三层：内容生成（封面图 / 信息图 / 小红书图 / 漫画 / 幻灯片）、"
                "排版转换（Markdown→公众号 HTML）、发布（公众号 / 微博 / X）。",
        "fit": "平台格式模板已接到「发布」页；配图和发布仍建议用原 skill 跑（要 API Key）。",
        "sub": ["baoyu-cover-image 封面图", "baoyu-infographic 信息图",
                "baoyu-xhs-images 小红书卡片", "baoyu-markdown-to-html 公众号排版",
                "baoyu-post-to-wechat 一键发公众号", "baoyu-diagram 结构图"],
    },
    # ---- 2026-10-09 从抖音博主「阿刁学ai」视频里扒下来的 5 个 skill（找→筛→拆→做→复盘） ----
    {
        "dir": "Tencent-BrowserSkill",
        "name": "browser-skill",
        "author": "腾讯 Tencent",
        "repo": "https://github.com/Tencent/BrowserSkill",
        "star": "8.5k",
        "tagline": "① 找市场：让 AI 用你已登录的真实浏览器去操作网页",
        "what": "bsk CLI + 浏览器扩展，带着你的登录态驱动真实 Chromium：navigate / observe / "
                "click / fill / scroll / 截图 / 读网络。比我们自写的 web-access 更成熟，"
                "官方文档明确支持 WorkBuddy/CodeBuddy 这类会回收子进程的环境。",
        "fit": "和现有「对标浏览器 + webaccess.py」是同一件事的升级版；"
               "可替换抓取底座，或先只借它的「页面内容不可信 / 会话式操作」规范。",
    },
    {
        "dir": "douyin-daily-surge",
        "name": "douyin-daily-surge",
        "author": "why20261",
        "repo": "https://github.com/why20261/douyin-daily-surge",
        "star": "新仓库（2026-09）",
        "tagline": "② 找高赞：抖音点赞飙升榜 / 关键词搜索 / 热榜 / 评论",
        "what": "Node 脚本调第三方数据 API（guaikei.com，需 GUAIKEI_API_TOKEN）："
                "单次最多 1 万条，可按点赞·发布时间·时长·内容类型筛，出参含作者/互动/标签/链接；"
                "不用登录抖音，无风控封号风险。",
        "fit": "我们现在是「模拟浏览器 + 登录态」抓，慢且有风控风险；"
               "这个能把「① 找爆款」换成走后端 API（代价：要付费 Token）。",
    },
    {
        "dir": "ai-coevolution-skills/skills/lls-short-video-breakdown",
        "name": "lls-short-video-breakdown",
        "author": "PhilRobinluo（罗老师）",
        "repo": "https://github.com/PhilRobinluo/ai-coevolution-skills",
        "star": "新仓库（2026-09）",
        "tagline": "③ 拆爆款：证据驱动的六层拆解（带时间点 + 原文短引）",
        "what": "六层：事实台账 → 功能段落（钩子/承诺/背景/证据/转折/方法/收束/行动）→ "
                "留存机制 → 口语与表演 → 画面与字幕 → 迁移原则。"
                "强制区分「观察 / 推断 / 建议」，只引用必要短片段，用质量门禁 8 条自检。",
        "fit": "直接对标我们的「对标拆解报告」——现在那份是规则模板（藏赞比/结构切片），"
               "这套是真正的方法论，应替换 bench.py 的报告骨架。",
    },
    {
        "dir": "ai-coevolution-skills/skills/lls-ai-video-script-planner",
        "name": "lls-ai-video-script-planner",
        "author": "PhilRobinluo（罗老师）",
        "repo": "https://github.com/PhilRobinluo/ai-coevolution-skills",
        "star": "新仓库（2026-09）",
        "tagline": "④ 做分镜：从传播目标到分镜、素材清单、生成提示、拍摄验收",
        "what": "七步：视频任务书 → 叙事骨架（教程/演示/解释/品牌/复盘五选一）→ 节拍表 → "
                "分镜（景别·机位·旁白·屏幕字·素材来源·转场·风险）→ 生成提示（含连续性锚点）→ "
                "素材清单（必拍/可生成/需授权/可替代）→ 纸面预演。输出 8 部分 + 9 条质量门禁。",
        "fit": "我们要补的「分镜」环节直接抄它——把「文案」页从「一篇稿」升级成「一张能拍的施工图」。",
    },
    {
        "dir": "self-improving-agent/self-improving-agent",
        "name": "self-improving-agent",
        "author": "pskoett",
        "repo": "https://github.com/pskoett/self-improving-agent",
        "star": "768",
        "tagline": "⑤ 复盘进化：把反馈沉淀成下一次的工作规则（.learnings/）",
        "what": "维护 .learnings/LEARNINGS.md（纠错·洞见·知识缺口·最佳实践）/ ERRORS.md / "
                "FEATURE_REQUESTS.md；用 Pattern-Key（area.symptom）去重、按成因与变化速度做"
                "衰减维护，成熟规则升级进 SOUL.md / TOOLS.md / AGENTS.md。",
        "fit": "和我们已有的 cheat-on-content（盲预测→复盘→进化 rubric）是一路的；"
               "可把「复盘」页接到 .learnings/，让每次数据复盘自动变成下一条的规则。",
    },
    # ---- 2026-10-10 新入库 ----
    {
        "dir": "dbskill",
        "name": "dbskill",
        "author": "dontbesilent",
        "repo": "https://github.com/dontbesilent2025/dbskill",
        "star": "新仓库（2026-03，v2.2）",
        "tagline": "把商业咨询变成 AI 技能：内容五维诊断 / 开头优化 / 对标分析",
        "what": "dontbesilent 把 12000+ 条推文的方法论提炼成 28 个诊断 skill，"
                "另带 4176 条知识原子库。核心：内容诊断（文字洁癖/封面标题/表达效率/"
                "认知落差/AI 工作流五维）、开头优化（试用装理论：开头必须独立工作，"
                "写不出好开头 90% 是内容缺素材）、对标分析（五重过滤）。"
                "注意：CC BY-NC 4.0 非商用许可，自己用没问题，别打包进付费课。",
        "fit": "两套方法论已提炼进「文案」页：新增「内容诊断」（五维体检+第一步行动）和"
               "「开头优化」（素材盘点→诊断开头→三法出 10-15 条开头+Top3）两个 AI 按钮。",
    },
    {
        "dir": "__tool_ytdlp__",
        "name": "yt-dlp（命令行工具，非 skill）",
        "author": "yt-dlp 社区",
        "repo": "https://github.com/yt-dlp/yt-dlp",
        "star": "196k+",
        "tagline": "视频下载万金油：1751 个站点的解析器",
        "what": "这不是 skill，是装在隔离 Python 环境里的下载工具（当前版本 2026.08.19）。"
                "支持抖音/B站/快手/YouTube 等 1751 个站点，命令行直接下视频和字幕，"
                "所以「技能库」之前看不到它。",
        "fit": "「批量拆解」下载视频失败时的兜底（可下 B站/快手 等对标浏览抓不稳的平台）；"
               "也可以在电脑命令行手动用：python -m yt_dlp \"视频链接\"。",
    },
    # ---- 2026-10-10 深夜搜完 GitHub 后入库的两个（都是 MIT，可商用） ----
    {
        "dir": "huashu-skills",
        "name": "huashu-skills",
        "author": "花叔 alchaincyf",
        "repo": "https://github.com/alchaincyf/huashu-skills",
        "star": "新仓库（2026，MIT）",
        "tagline": "花叔的 20 个内容 skill：抖音爆款脚本 / 选题 / 审校 / 口语教练",
        "what": "最值钱的是 huashu-douyin-script：竞品视频 7 维拆解（前 3 秒逐帧级：文案钩子×"
                "视觉钩子双线）→ 爆款公式库沉淀（proven-formulas.md）→ 脚本生成 → 5 项审校"
                "（口语化 / 钩子强度 / 节奏时长 / 广告合规 / AI 味）。「时间+功效承诺」合规规则"
                "和口语化审校清单可以直接抄进我们的改写环节。",
        "fit": "已下载到 skills 目录。公式库和审校清单后续可嵌进「文案」页；"
               "下载/逐帧分析部分我们用自己的对标拆解+浏览器，不用它的。",
    },
    {
        "dir": "vyral-content-skills",
        "name": "content-skills（Vyral）",
        "author": "vyralcontent",
        "repo": "https://github.com/vyralcontent/content-skills",
        "star": "95（2026-06，MIT）",
        "tagline": "研究了 20 万条爆款短视频后提炼的钩子/脚本/选题 skill 包",
        "what": "7 个 skill：viral-hooks（三层钩子：视觉/口播/字幕 + 8 位名博框架，一次出 6-10 条"
                "不同原型）、viral-short-form（hook→escalation→payoff→CTA 留存结构）、"
                "viral-short-form-ideas（选题支柱 + 评论区挖掘 + 一题五角度）。英文平台向，"
                "但钩子原型和留存结构是跨语言通用的。",
        "fit": "已下载到 skills 目录。「一题五角度」「钩子出 6-10 条不同原型」的思路可补进"
               "我们的开头优化模式（dbs-hook 是 10-15 条，它强调原型要散开不重复）。",
    },
]


def list_skills():
    out = []
    for s in SKILL_META:
        if s["dir"] == "__tool_ytdlp__":
            # yt-dlp 是装在隔离环境里的工具，不是 skills/ 下的目录——按能否 import 判「已装」
            try:
                import importlib.util as _ilu
                spec = _ilu.find_spec("yt_dlp")
                installed = spec is not None
            except Exception:  # noqa: BLE001
                installed = False
            d = "(隔离 Python 环境)"
        else:
            d = os.path.join(SKILLS_DIR, s["dir"])
            installed = os.path.isdir(d)
        item = dict(s)
        item["installed"] = installed
        item["path"] = d
        out.append(item)
    return out


def list_bench():
    items = []
    for f in sorted(glob.glob(os.path.join(BENCH_DIR, "*.md")),
                    key=os.path.getmtime, reverse=True):
        try:
            items.append({"name": os.path.basename(f), "path": f,
                          "size": os.path.getsize(f), "mtime": os.path.getmtime(f)})
        except OSError:
            pass
    return items


BENCH_TRASH = os.path.join(BENCH_DIR, "_已清空")     # 清空历史 = 挪到这里，不硬删


def _bench_prune_trash(keep=5):
    """回收站只留最近 5 次，别自己越堆越大。"""
    try:
        subs = sorted([d for d in os.listdir(BENCH_TRASH)
                       if os.path.isdir(os.path.join(BENCH_TRASH, d))], reverse=True)
    except OSError:
        return
    for d in subs[keep:]:
        try:
            shutil.rmtree(os.path.join(BENCH_TRASH, d))
        except OSError:
            pass


def bench_clean(keep_results=False):
    """清空「历史拆解」：把 对标库 下的报告 + _结果 里的抓取快照**挪进** 对标库\\_已清空\\。

    故意不硬删——清错了还能翻回来。不碰用户手动放进 对标库 的其他文件/子目录。
    keep_results=True 时只挪报告，留着抓取结果让你还能在界面上接着拆。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trash = os.path.join(BENCH_TRASH, stamp)
    removed, freed = 0, 0

    def _move(f):
        nonlocal removed, freed
        try:
            size = os.path.getsize(f)
        except OSError:
            return
        os.makedirs(trash, exist_ok=True)
        base = os.path.basename(f)
        dst = os.path.join(trash, base)
        stem, ext = os.path.splitext(base)
        i = 1
        while os.path.exists(dst):
            dst = os.path.join(trash, "%s_%d%s" % (stem, i, ext))
            i += 1
        try:
            shutil.move(f, dst)
            removed += 1
            freed += size
        except OSError:
            pass

    for f in glob.glob(os.path.join(BENCH_DIR, "*.md")):
        _move(f)
    if not keep_results:
        try:
            names = os.listdir(WA_RESULT_DIR)
        except OSError:
            names = []
        for n in names:
            p = os.path.join(WA_RESULT_DIR, n)
            if os.path.isfile(p):
                _move(p)
        with JOB_LOCK:
            WA_LAST["kind"] = ""
            WA_LAST["result"] = None
            WA_LAST["path"] = ""
    if removed:
        _bench_prune_trash()
    else:
        try:
            if os.path.isdir(trash) and not os.listdir(trash):
                os.rmdir(trash)
        except OSError:
            pass
    return {"ok": True, "removed": removed, "bytes": freed, "text": human(freed),
            "left": len(list_bench()), "trash": trash if removed else ""}


# ---------------------------------------------------------------- 对标浏览器 / AI 写稿
WA_LAST = {"kind": "", "result": None, "path": ""}

# 抖音链接（含短链）抠取：用户往往整段粘「分享口令」，不能按空格切
WA_URL_RE = re.compile(r"(?:https?://)?(?:v\.|www\.|m\.)?douyin\.com/[^\s，。、；;'\"）)】\]]+", re.I)


def wa_extract_lines(raw):
    """把用户粘的东西整理成「一行一个、且带抖音链接」的行。

    返回 (可用行, 被丢掉的行)。整段分享口令算一行，不再被空格切碎。
    """
    if isinstance(raw, (list, tuple)):
        raw_lines = []
        for x in raw:
            raw_lines.extend(str(x or "").replace("\r", "\n").split("\n"))
    else:
        raw_lines = str(raw or "").replace("\r", "\n").split("\n")
    keep, drop = [], []
    for ln in raw_lines:
        ln = (ln or "").strip()
        if not ln:
            continue
        (keep if WA_URL_RE.search(ln) else drop).append(ln)
    return keep, drop


def wa_alive():
    """「对标浏览器」在不在跑（它是独立的 Chrome，常驻）。"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        op.open("http://127.0.0.1:%d/json/version" % WA_PORT, timeout=2).read()
        return True
    except Exception:  # noqa: BLE001
        return False


# ---- 登录态：轻量探测（只读 cookie，不导航页面），给前端做「自动变绿」----
# 按平台各自缓存：_WA_LOGIN[plat] = {alive, logged_in, by, checked}
SESSION_COOKIE_NAMES = ("sessionid", "sessionid_ss", "sid_tt")
_WA_LOGIN = {}                       # 平台 → 状态
_WA_LOGIN_LOCK = threading.Lock()
_WA_PROBE_BUSY = threading.Lock()


def _plat_cfg(key):
    """平台配置（懒加载 webaccess，失败时只认抖音，别让登录卡挂掉服务）。"""
    try:
        import webaccess
        return webaccess.get_platform(key), key
    except Exception:  # noqa: BLE001
        douyin = {"name": "抖音", "home": "https://www.douyin.com/",
                  "cookies": SESSION_COOKIE_NAMES, "domain": "douyin.com"}
        return douyin, ("douyin" if (key or "") == "douyin" else "douyin")


def wa_probe_login(platform="douyin"):
    """只连 CDP 读指定平台的 cookie 判登录 —— 不打开页面、不打断正在跑的任务。"""
    if not wa_alive():
        return {"alive": False, "logged_in": False, "by": "", "platform": platform}
    plat, key = _plat_cfg(platform)
    try:
        import webaccess
        probe_fn = webaccess.probe_logged_in
    except Exception:  # noqa: BLE001
        probe_fn = None
    cookies = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % WA_PORT)
            try:
                for ctx in b.contexts:
                    if platform == "xhs":
                        # 小红书：游客也发 web_session，cookie 判不了 —— 只信页面 DOM
                        xst = webaccess.xhs_page_login()
                        if xst.get("guest", True):
                            return {"alive": True, "logged_in": False, "by": "xhs-guest",
                                    "platform": platform}
                        return {"alive": True, "logged_in": True, "by": "cookie+page",
                                "platform": platform}
                    if probe_fn is not None:
                        st = probe_fn(ctx, key)
                        if st.get("logged_in"):
                            return {"alive": True, "logged_in": True,
                                    "by": st.get("by", "cookie"), "platform": platform}
                        if st.get("by") == "none":
                            # 记住「确实没登录」的结果，避免白跑
                            cookies = []
                            continue
                    try:
                        cookies += ctx.cookies(plat["home"]) or []
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        # 打包桌面端不要求系统 Python 安装 Playwright；改由内置 Node 浏览器引擎探测。
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:3119/api/creator/platform-status?platform=" +
                urllib.parse.quote(platform), headers={"Accept": "application/json"})
            data = json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8"))
            result = data.get("result") or {}
            return {"alive": bool(result.get("alive")),
                    "logged_in": bool(result.get("logged_in")),
                    "by": result.get("by", "node-probe"), "platform": platform}
        except Exception:
            return {"alive": True, "logged_in": False, "by": "", "platform": platform,
                    "error": str(exc)[:100]}
    names = [n.lower() for n in (plat.get("cookies") or ())]
    for c in cookies:
        n = (c.get("name") or "").lower()
        if n in names and (c.get("value") or ""):
            # 小红书：游客也发 web_session —— cookie 命中后还要过页面 DOM 这一关
            if platform == "xhs":
                try:
                    import webaccess as _wa
                    page_st = _wa.xhs_page_login()
                except Exception:  # noqa: BLE001
                    page_st = {"guest": True}
                if not page_st or page_st.get("guest", True):
                    return {"alive": True, "logged_in": False, "by": "xhs-guest",
                            "platform": platform}
            return {"alive": True, "logged_in": True, "by": "cookie:" + n,
                    "platform": platform}
    return {"alive": True, "logged_in": False, "by": "none", "platform": platform}


def wa_login_state(platform="douyin", max_age=5.0):
    """给前端的登录态：5 秒内的直接吃缓存；过期就重探一次（并发时只放一个进去探）。"""
    platform = (platform or "douyin").strip() or "douyin"
    try:                    # 坏平台名归一化回抖音，别让缓存里长出野 key
        import webaccess
        if platform not in webaccess.PLATFORMS:
            platform = "douyin"
    except Exception:  # noqa: BLE001
        if platform != "douyin":
            platform = "douyin"
    with _WA_LOGIN_LOCK:
        ent = _WA_LOGIN.setdefault(platform, {"alive": False, "logged_in": False,
                                              "by": "", "checked": 0.0})
        age = time.time() - ent.get("checked", 0)
        stale = age > max_age
    if stale and _WA_PROBE_BUSY.acquire(blocking=False):
        try:                       # 别让多个请求同时探
            st = wa_probe_login(platform)
            with _WA_LOGIN_LOCK:
                ent = _WA_LOGIN.setdefault(platform, {})
                ent.update({k: st.get(k, False) for k in ("alive", "logged_in", "by")})
                ent["checked"] = time.time()
        finally:
            _WA_PROBE_BUSY.release()
    with _WA_LOGIN_LOCK:
        ent = _WA_LOGIN.setdefault(platform, {})
        out = {k: ent.get(k, False) for k in ("alive", "logged_in", "by")}
    out["platform"] = platform
    out["platform_name"] = _plat_cfg(platform)[0]["name"]
    return out


# ---- 对标浏览器由「长驻的服务进程」来养，而不是每次任务起一个短命子进程 ----
# 这是「不要每次重新开一个浏览器」的关键：任务的子进程会结束，服务进程不会。
_WA_MOD = None
_WA_KEEP = {"on": False, "thread": None, "lock": threading.Lock()}


def wa_module():
    """把 webaccess.py 当模块加载一次（它自己是 __main__ 保护的，导入无副作用）。"""
    global _WA_MOD
    if _WA_MOD is None:
        spec = importlib.util.spec_from_file_location("wa_webaccess", WA_PY)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _WA_MOD = m
    return _WA_MOD


def _wa_keepalive_loop():
    """保活：浏览器万一被关了/被回收了，自动把它拉回来 —— 用户不用再点一次。"""
    m = wa_module()
    while _WA_KEEP["on"]:
        time.sleep(15)
        if not _WA_KEEP["on"]:
            break
        if not wa_alive():
            try:
                m.ensure_chrome(visible=True, url="https://www.douyin.com/")
            except Exception:  # noqa: BLE001
                pass


def wa_ensure(url="https://www.douyin.com/"):
    """确保对标浏览器活着；成功后开启保活。返回 (ok, msg)。"""
    try:
        m = wa_module()
        ok, msg = m.ensure_chrome(visible=True, url=url)
    except Exception as exc:  # noqa: BLE001
        return False, "启动对标浏览器失败：%s" % exc
    if ok:
        with _WA_KEEP["lock"]:
            _WA_KEEP["on"] = True
            t = _WA_KEEP["thread"]
            if not (t and t.is_alive()):
                t = threading.Thread(target=_wa_keepalive_loop, daemon=True)
                t.start()
                _WA_KEEP["thread"] = t
    return ok, msg


def latest_wa(kind):
    """取最近一次 搜索/抓账号 的结构化结果（服务重启后内存丢了也能兜底）。

    优先给「抓到过东西」的那次：一次失败的重试不该把上回的好结果顶掉。
    """
    try:
        files = glob.glob(os.path.join(WA_RESULT_DIR, "%s_*.json" % kind))
        if kind == "search":
            # 多平台搜索（psearch）的结果也算「搜索」——前端刷新后要能找回 B站/快手/小红书记录
            files += glob.glob(os.path.join(WA_RESULT_DIR, "psearch_*.json"))
    except OSError:
        files = []
    if not files:
        return None
    files = sorted(files, key=os.path.getmtime, reverse=True)   # 从新到旧
    best = None
    for f in files:                       # 优先给最近一次「抓到过东西」的结果
        try:
            d = json.loads(read_text(f) or "{}")
        except Exception:  # noqa: BLE001
            continue
        if d.get("items"):
            best = (f, d)
            break
    if best is None:                      # 全都没有 items → 给最新的那次（含失败原因）
        f = files[0]
        try:
            d = json.loads(read_text(f) or "{}")
        except Exception:  # noqa: BLE001
            d = {}
        best = (f, d)
    d = best[1]
    d["_path"] = best[0]
    return d


def _bench_files():
    """只认拆解下载的素材（bench_*.mp4 之类），别误删缓存目录里别的文件。"""
    out = []
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return out
    for f in names:
        if not f.startswith("bench_"):
            continue
        p = os.path.join(CACHE_DIR, f)
        if os.path.isfile(p):
            try:
                out.append((p, os.path.getsize(p)))
            except OSError:
                pass
    return out


def cache_info():
    """本机临时素材（拆解时下载的对标视频）占用情况。"""
    fs = _bench_files()
    total = sum(s for _p, s in fs)
    return {"count": len(fs), "bytes": total, "text": human(total), "dir": CACHE_DIR}


def cache_clean():
    """清掉拆解下载的临时素材，返回释放了多少。"""
    fs = _bench_files()
    total, n = sum(s for _p, s in fs), 0
    for p, _s in fs:
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return {"ok": True, "removed": n, "bytes": total, "text": human(total),
            "count": len(fs) - n}


def _vid_of(u):
    m = re.search(r"/video/(\d+)", u or "")
    return m.group(1) if m else ""


def pin_first(items, url, meta=None):
    """把你点开的那条视频挪到第一位；列表里没有就补一条在最前面。"""
    pid = _vid_of(url)
    idx = -1
    for i, x in enumerate(items):
        if (pid and _vid_of(x.get("url")) == pid) or (x.get("url") and x.get("url") == url):
            idx = i
            break
    if idx > 0:
        items.insert(0, items.pop(idx))
    elif idx < 0 and meta:
        it = dict(meta)
        it.setdefault("account", it.get("author") or "")
        items.insert(0, it)
    if items:
        items[0]["pinned"] = True
    return items


def ai_cfg_safe():
    """给前端看的模型配置（key 打码）。"""
    try:
        import aiwrite
        cfg = aiwrite.load_cfg()
        return {
            "preset": cfg.get("preset", "deepseek"),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "temperature": cfg.get("temperature", 0.8),
            "has_key": bool((cfg.get("api_key") or "").strip()),
            "key_mask": aiwrite.mask(cfg.get("api_key", "")),
            "presets": aiwrite.PRESETS,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": "aiwrite.py 读不到：%s" % exc, "presets": []}


# ---------------------------------------------------------------- adu 动效包装
def _adupack():
    import adupack
    return adupack


def _ttsfree():
    import ttsfree
    return ttsfree


# ---------------------------------------------------------------- 账号定位（工作台最前一页）
def position_md():
    return read_text(POSITION)


def position_save(md):
    """保存账号定位；旧版先备份进 工作区\\缓存，改砸了能翻回来。"""
    md = (md or "").replace("\r\n", "\n").strip()
    if not md:
        return {"error": "内容是空的"}
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if os.path.isfile(POSITION):
            bak = os.path.join(CACHE_DIR, "账号定位备份-%s.md"
                               % time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(POSITION, bak)
    except OSError:
        pass
    with open(POSITION, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    job_push("[服务] 账号定位已更新（旧版已备份进 工作区\\缓存）")
    return {"ok": True, "path": POSITION}


# ---------------------------------------------------------------- 今日新鲜选题（联网抓 aihot.news × 账号定位）
_AIHOT_HOME = "https://aihot.news/"
_AIHOT_API = "https://aihot.news/api/v1/hot-topics"      # 官方 API：多源热点 Top10（事件聚簇+热度排名）
_FRESH_TAG_RE = re.compile(r'<a[^>]+href="(/items/[^"]+)"[^>]*>(.*?)</a>', re.S)
# follow-builders（github.com/zarazhangrui/follow-builders）：26 位 AI builder 的 X 一手动态，
# 中心 feed 每日更新。raw.githubusercontent 直连常超时，优先走 jsdelivr 镜像。
_FB_FEED_URL = "https://cdn.jsdelivr.net/gh/zarazhangrui/follow-builders@main/feed-x.json"
_FB_FEED_RAW = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json"


def _http_get_json(url, timeout=20):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AIBloggerWorkbench)"})
    return json.loads(op.open(req, timeout=timeout).read().decode("utf-8", "ignore"))


def fetch_aihot_items(limit=24):
    """抓今日 AI 资讯：[(摘要文本, 链接), …]。
    优先 aihot.news 官方 API（/api/v1/hot-topics，多源事件聚簇+热度排名，每 5 分钟刷新），
    失败回退爬首页 HTML。"""
    import html as _html
    # ---- 优先：官方 API ----
    try:
        d = _http_get_json(_AIHOT_API, timeout=15)
        items, seen = [], set()
        for it in d.get("items", []):
            title = (it.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            src = ((it.get("source") or {}).get("name") or "").strip()
            text = "%s（来源：%s）" % (title, src) if src else title
            links = it.get("links") or {}
            url = links.get("original") or links.get("aihot") or ""
            items.append({"text": text, "url": url})
            if len(items) >= limit:
                break
        if items:
            return items
    except Exception:
        pass
    # ---- 回退：爬首页 ----
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(_AIHOT_HOME, headers={"User-Agent": "Mozilla/5.0"})
    raw = op.open(req, timeout=25).read().decode("utf-8", "ignore")
    items, seen = [], set()
    for href, inner in _FRESH_TAG_RE.findall(raw):
        txt = re.sub(r"<[^>]+>", "", inner)
        txt = _html.unescape(txt).replace("\u200b", "").strip()
        txt = re.sub(r"\s+", " ", txt)
        if len(txt) < 24 or txt in seen:      # 太短的是图标/收藏按钮，不是资讯
            continue
        seen.add(txt)
        items.append({"text": txt, "url": _AIHOT_HOME.rstrip("/") + href})
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------- 自定义资讯源（跨行业）
SRC_FILE = os.path.join(WORK, "资讯源.txt")
_SRC_TEMPLATE = """# 自定义资讯源设置（保存后回工作台点「抓今日最新」生效）
#
# 用法：
#   1. 每行填一个 RSS 或资讯网址，# 开头的是注释不算数。
#      例：https://www.ithome.com/rss/
#   2. 账号不是 AI 赛道的，在下面任意一行写上：不带AI源
#      效果=跳过 aihot/builders 两个 AI 专属源，只抓你填的源。
#   3. 模型会按「账号定位」自动筛出能做的选题，源里混着无关内容也没关系。
#
# 示例（去掉行首 # 即生效）：
# https://www.ithome.com/rss/
# https://www.ruanyifeng.com/blog/atom.xml
# 注意：填之前先用浏览器打开一遍，能直接看到新闻列表的才有效（有些站点已停用 RSS）"""


def load_custom_sources():
    """读 工作区\\资讯源.txt：每行一个 RSS/资讯网址，# 注释；出现「不带AI源」则跳过 AI 双源。
    返回 (urls, skip_ai)。"""
    txt = read_text(SRC_FILE)
    if not txt:
        return [], False
    urls, skip_ai = [], False
    for ln in txt.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if "不带AI源" in s:
            skip_ai = True
            continue
        if s.startswith("http"):
            urls.append(s)
    return urls, skip_ai


def src_file_read():
    """读资讯源.txt 原文（给工作台内置弹窗）；不存在先落模板。"""
    if not os.path.isfile(SRC_FILE):
        os.makedirs(os.path.dirname(SRC_FILE), exist_ok=True)
        with open(SRC_FILE, "w", encoding="utf-8") as f:
            f.write(_SRC_TEMPLATE)
    return read_text(SRC_FILE)


def src_file_save(text):
    """保存资讯源.txt（工作台弹窗用）。返回 (有效源数, 是否不带AI源)。"""
    os.makedirs(os.path.dirname(SRC_FILE), exist_ok=True)
    with open(SRC_FILE, "w", encoding="utf-8") as f:
        f.write((text or "").replace("\r\n", "\n"))
    return load_custom_sources()


def probe_source(url):
    """源体检（思路来自 scraping skill 的 probe.py，轻量复刻，纯标准库）：
    2 个礼貌请求判断一个资讯源是「真 RSS」「SPA 壳」还是「死/拒」。
    36kr.com/feed 教训：200 + text/html 且 robots.txt 与它同字节数 = 全路径吐同一个壳。
    """
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def _fetch(u, timeout=12):
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        raw = op.open(req, timeout=timeout).read()
        if raw[:2] == b"\x1f\x8b":
            import gzip as _gzip
            raw = _gzip.decompress(raw)
        return raw
    host = re.sub(r"^https?://([^/]+).*", r"\1", url)
    try:
        raw = _fetch(url)
    except urllib.error.HTTPError as e:
        return {"verdict": "dead", "detail": "HTTP %s——%s" % (e.code, "被拒绝（可能有反爬）" if e.code in (403, 429) else "地址不存在或已停用")}
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "dead", "detail": "打不开：%s" % str(exc)[:80]}
    head = raw[:1200].decode("utf-8", "ignore").lower()
    ct = ""
    try:
        ct = op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=12).headers.get("Content-Type", "") or ""
    except Exception:
        pass
    n_items = raw.count(b"<item") or raw.count(b"<entry")
    if n_items >= 3 and ("<rss" in head or "<feed" in head or "xml" in ct.lower() or "<?xml" in head):
        return {"verdict": "rss", "detail": "活的 RSS/Atom，抓到 %d 条内容，直接可用" % n_items}
    if "<html" in head or "<!doctype" in head:
        same_shell = False
        try:
            robots = _fetch(url.rstrip("/") + "/robots.txt", timeout=10)
            same_shell = abs(len(robots) - len(raw)) < 256   # 所有路径吐同一个壳
        except Exception:
            pass
        if same_shell:
            return {"verdict": "shell", "detail": "这个地址返回的是网页壳不是资讯列表（连 robots.txt 都和它一模一样），源已停用，换一个"}
        return {"verdict": "html", "detail": "是个网页但不是标准 RSS——抓取器会试着提取正文锚点，不保证质量，建议换带 /rss /feed 的地址"}
    return {"verdict": "unknown", "detail": "返回了 %d 字节，既不是 RSS 也不是网页，不确定能不能用，先用「抓今日最新」试一把" % len(raw)}


def fetch_custom_items(urls, limit_per=12):
    """抓自定义资讯源。RSS/Atom 正则解析标题+链接；普通网页退回提取正文锚点。"""
    import html as _html
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    items = []
    for u in urls:
        host = re.sub(r"^https?://([^/]+).*", r"\1", u)
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            raw = op.open(req, timeout=20).read()
            if raw[:2] == b"\x1f\x8b":      # 有的 feed 返回 gzip，urllib 不自动解
                import gzip as _gzip
                raw = _gzip.decompress(raw)
            raw = raw.decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            print("[资讯源] %s 不可用：%s" % (host, str(exc)[:80]), flush=True)
            continue
        from content_sources import parse_feed, canonical_url
        try:
            got = parse_feed(raw, u, limit_per)
        except ValueError as exc:
            print('[资讯源] %s 解析失败：%s' % (host, exc), flush=True)
            continue
        if not got:
            for href, inner in _FRESH_TAG_RE.findall(raw):
                txt = _html.unescape(re.sub(r"<[^>]+>", "", inner)).replace("\u200b", "").strip()
                txt = re.sub(r"\s+", " ", txt)
                if len(txt) < 24:
                    continue
                got.append({"text": txt,
                            "url": canonical_url(href, u)})
                if len(got) >= limit_per:
                    break
        for g in got[:limit_per]:
            if not g.get("url"):
                continue
            g["text"] = "〔%s〕%s" % (host, g["text"])
            items.append(g)
    return items


def fetch_builders_items(limit=18):
    """follow-builders 中心 feed：26 位 AI builder（Karpathy、各公司 CEO/研究员等）
    过去 24 小时的 X 一手动态。返回 [(文本, 链接), …]，按点赞降序取前 limit 条。"""
    d = None
    last_err = None
    for url in (_FB_FEED_URL, _FB_FEED_RAW):
        try:
            d = _http_get_json(url, timeout=20)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    if not d:
        raise RuntimeError("follow-builders feed 拉取失败：%s" % str(last_err)[:100])
    out = []
    for b in d.get("x", []):
        name = (b.get("name") or b.get("handle") or "").strip()
        bio = (b.get("bio") or "").strip()
        role = "%s（%s）" % (name, bio[:40]) if bio else name
        for tw in b.get("tweets", []):
            txt = re.sub(r"\s+", " ", (tw.get("text") or "")).strip()
            if len(txt) < 20:
                continue
            out.append({"text": "〔builder〕%s：%s" % (role, txt[:200]),
                        "url": tw.get("url") or "",
                        "likes": tw.get("likes") or 0})
    out.sort(key=lambda x: x["likes"], reverse=True)
    return out[:limit]


def fresh_stats_lines(items):
    lines = []
    for i, it in enumerate(items, 1):
        lines.append("%d. %s（%s）" % (i, it["text"], it["url"]))
    return "\n".join(lines)


def run_fresh_job():
    """后台跑「今日新鲜选题」：抓资讯 → 模型按账号定位筛选 → 存 选题库\\今日新鲜\\。"""
    try:
        job_push("[新鲜] 正在抓今天的 AI 资讯（aihot.news）…")
        try:
            items = fetch_aihot_items()
        except Exception as exc:  # noqa: BLE001
            job_push("[新鲜] ! 资讯抓取失败：%s" % str(exc)[:140])
            job_finish(False, "")
            return
        if not items:
            job_push("[新鲜] ! 今天没解析到资讯条目，稍后再试。")
            job_finish(False, "")
            return
        job_push("[新鲜] 抓到 %d 条。正在让模型按账号定位筛选题（约 1 分钟）…" % len(items))
        brief = position_md()[:800]
        try:
            import aiwrite
            md = aiwrite.fresh(fresh_stats_lines(items), account_brief=brief)
        except Exception as exc:  # noqa: BLE001
            job_push("[新鲜] 模型筛选失败：%s" % str(exc)[:160])
            job_finish(False, "")
            return
        outdir = os.path.join(TOPIC_DIR, "今日新鲜")
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "今日新鲜-%s.md" % time.strftime("%Y-%m-%d"))
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 今日新鲜选题 · %s\n\n> 资讯源：aihot.news（抓取时间 %s）× 账号定位自动筛选\n\n---\n\n%s\n"
                    % (time.strftime("%Y-%m-%d"), time.strftime("%Y-%m-%d %H:%M"), md))
        job_push("[新鲜] 已存：%s" % path)
        job_finish(True, path)
    except Exception as exc:  # noqa: BLE001
        job_push("[新鲜] 出错：%s" % str(exc)[:160])
        job_finish(False, "")


def fresh_last():
    best = None
    d = os.path.join(TOPIC_DIR, "今日新鲜")
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".md"):
                p = os.path.join(d, fn)
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    if not best:
        return {}
    try:
        with open(best, "r", encoding="utf-8") as f:
            return {"path": best, "md": f.read()}
    except OSError as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------- A/B 卡实时抓取（今日 AI 大事 / 今日爆款热榜）
_AB_DIRS = {"A": "今日AI大事", "B": "今日爆款"}


def fetch_douyin_hotboard(limit=30):
    """用对标浏览器（CDP）开抖音热榜，拦 hot/search/list 接口拿 热词+热度。

    没开浏览器 / 没拦到接口时退回扫页面 DOM。返回 [{"word","hot"}, …]。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        # 桌面版直接复用 eMotionCut 自带的浏览器引擎，不要求系统 Python 装依赖。
        try:
            raw = urllib.request.urlopen(
                "http://127.0.0.1:3119/api/creator/douyin-hotboard", timeout=90).read()
            data = json.loads(raw.decode("utf-8"))
            return (data.get("result") or [])[:limit]
        except Exception as exc:
            raise RuntimeError("桌面浏览器抓取热榜失败：%s" % str(exc)[:120])
    try:
        from webaccess import PORT as CDP_PORT
    except Exception:  # noqa: BLE001
        CDP_PORT = 9333
    payload = {}
    dom_items = []
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % CDP_PORT)
        try:
            ctx = b.contexts[0] if b.contexts else b.new_context()
            page = ctx.new_page()

            def on_resp(resp):
                u = resp.url
                if "hot/search/list" in u and not payload:
                    try:
                        payload.update(resp.json())
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_resp)
            page.goto("https://www.douyin.com/hot", wait_until="domcontentloaded",
                      timeout=60000)
            page.wait_for_timeout(6000)
            for sel in ("[data-e2e='guide-close']", ".dy-account-close"):
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=500):
                        el.click(timeout=800)
                except Exception:  # noqa: BLE001
                    pass
            dom_items = page.evaluate("""() => {
              const out = [];
              document.querySelectorAll('[data-e2e="hot-item"], .hot-item').forEach((it) => {
                const t = ((it.innerText || '').trim().split('\\n')[0] || '').trim();
                if (t && t.length >= 3 && t.length <= 60) out.push(t);
              });
              return out;
            }""") or []
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                b.close()
            except Exception:  # noqa: BLE001
                pass
    items = []
    wl = ((payload.get("data") or {}).get("word_list")) or []
    for w in wl[:limit]:
        items.append({"word": (w.get("word") or "").strip(), "hot": w.get("hot_value", 0)})
    if not items and dom_items:
        for t in dom_items[:limit]:
            items.append({"word": t, "hot": 0})
    return items


def _parse_livefeed_lines(md):
    """把模型输出的「标题 ｜ 理由 ｜ 角度」行解析成结构化 items。解析不出就返回空。"""
    items = []
    for line in (md or "").splitlines():
        line = line.strip().lstrip("-·0123456789.、）) ")
        if line.count("｜") < 2 and line.count("|") < 2:
            continue
        parts = [p.strip() for p in re.split(r"｜|\|", line)]
        if len(parts) >= 3 and parts[0] and "抓取时间" not in parts[0]:
            items.append({"title": parts[0][:40], "why": parts[1], "angle": " ｜ ".join(parts[2:])})
    return items


def run_ab_job(target):
    """后台跑 A（今日AI大事）/ B（今日爆款）实时抓取 + 模型按定位筛选。"""
    label = "大事" if target == "A" else "爆款"
    outdir = os.path.join(TOPIC_DIR, _AB_DIRS.get(target, "今日AI大事"))
    try:
        if target == "A":
            c_urls, skip_ai = load_custom_sources()
            a_src_parts = []
            items = []
            if skip_ai:
                job_push("[大事] 资讯源设置=不带AI源，跳过 aihot/builders。")
            else:
                job_push("[大事] 正在抓今天的 AI 资讯（aihot.news 官方 API）…")
                try:
                    items = fetch_aihot_items()
                    job_push("[大事] aihot 热点拿到 %d 条。" % len(items))
                    a_src_parts.append("aihot.news 热点 API")
                except Exception as exc:  # noqa: BLE001
                    job_push("[大事] ! aihot 抓取失败：%s" % str(exc)[:120])
                try:
                    bitems = fetch_builders_items()
                    if bitems:
                        job_push("[大事] follow-builders 源拿到 %d 条 builder 一手动态。" % len(bitems))
                        items = items + bitems
                        a_src_parts.append("follow-builders builder 动态")
                except Exception as exc:  # noqa: BLE001
                    job_push("[大事] builder 源跳过：%s" % str(exc)[:100])
            if c_urls:
                job_push("[大事] 抓自定义资讯源 %d 个…" % len(c_urls))
                try:
                    citems = fetch_custom_items(c_urls)
                    ok_n = len([x for x in citems if "源不可用" not in x["text"]])
                    job_push("[大事] 自定义源拿到 %d 条。" % ok_n)
                    items = items + citems
                    a_src_parts.append("自定义资讯源 %d 个" % len(c_urls))
                except Exception as exc:  # noqa: BLE001
                    job_push("[大事] 自定义源失败：%s" % str(exc)[:100])
            if not items:
                job_push("[大事] ! 所有源都没抓到内容（非 AI 赛道先去「📡 资讯源」里填源）。")
                job_finish(False, "")
                return
            raw_n = len(items)
            a_src = " + ".join(a_src_parts) if a_src_parts else "自定义资讯源"
            items_md = "\n".join("%d. %s" % (i, it["text"]) for i, it in enumerate(items, 1))
        else:
            job_push("[爆款] 确保对标浏览器在线（没开会自动拉起）…")
            ok, msg = wa_ensure("https://www.douyin.com/hot")
            if not ok:
                job_push("[爆款] ! 对标浏览器启动失败：%s" % msg[:120])
                job_finish(False, "")
                return
            job_push("[爆款] 正在用对标浏览器开抖音热榜…")
            try:
                items = fetch_douyin_hotboard()
            except Exception as exc:  # noqa: BLE001
                job_push("[爆款] ! 热榜抓取失败：%s（先确认对标浏览器开着）" % str(exc)[:120])
                job_finish(False, "")
                return
            if not items:
                job_push("[爆款] ! 热榜没解析到条目（页面没加载出来？稍后再试）。")
                job_finish(False, "")
                return
            raw_n = len(items)
            items_md = "\n".join("%d. %s%s" % (i, it["word"],
                                               ("（热度 %s）" % it["hot"]) if it["hot"] else "")
                                 for i, it in enumerate(items, 1))
        job_push("[%s] 抓到 %d 条，当前 Agent 正在核验并按账号定位筛选（约 1 分钟）…" % (label, raw_n))
        try:
            import aiwrite
            md = aiwrite.livefeed(target, items_md, account_brief=position_md()[:800], n=raw_n)
        except Exception as exc:  # noqa: BLE001
            job_push("[%s] Agent 分析失败：%s" % (label, str(exc)[:160]))
            job_finish(False, "")
            return
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "%s-%s.md" % (_AB_DIRS[target], time.strftime("%Y-%m-%d")))
        src = a_src if target == "A" else "抖音热榜（实时抓取）"
        with open(path, "w", encoding="utf-8") as f:
            f.write("# %s · %s\n\n> 来源：%s（抓取时间 %s）× 账号定位自动筛选\n\n---\n\n%s\n"
                    % (_AB_DIRS[target], time.strftime("%Y-%m-%d"), src,
                       time.strftime("%Y-%m-%d %H:%M"), md))
        jpath = path[:-3] + ".json"
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d %H:%M"), "target": target,
                       "items": _parse_livefeed_lines(md), "raw_n": raw_n},
                      f, ensure_ascii=False, indent=1)
        job_push("[%s] 已存：%s" % (label, path))
        job_finish(True, path)
    except Exception as exc:  # noqa: BLE001
        job_push("[%s] 出错：%s" % (label, str(exc)[:160]))
        job_finish(False, "")


def ab_last(target):
    d = os.path.join(TOPIC_DIR, _AB_DIRS.get(target, "今日AI大事"))
    today = time.strftime("%Y-%m-%d")
    best_md, best_js = None, None
    today_md, today_js = None, None
    if os.path.isdir(d):
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            if fn.endswith(".md") and (best_md is None or os.path.getmtime(p) > os.path.getmtime(best_md)):
                best_md = p
            if fn.endswith(".json") and (best_js is None or os.path.getmtime(p) > os.path.getmtime(best_js)):
                best_js = p
            if today in fn:   # 优先今天日期的文件，别拿昨天的旧货冒充
                if fn.endswith(".md"): today_md = p
                if fn.endswith(".json"): today_js = p
    if today_md:
        best_md, best_js = today_md, (today_js or best_js)
    if not best_md:
        return {}
    out = {}
    try:
        with open(best_md, "r", encoding="utf-8") as f:
            out["md"] = f.read()
        out["path"] = best_md
        out["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(best_md)))
    except OSError as exc:
        return {"error": str(exc)}
    if best_js:
        try:
            with open(best_js, "r", encoding="utf-8") as f:
                out["items"] = json.load(f).get("items") or []
        except Exception:  # noqa: BLE001
            out["items"] = []
    return out


# ---------------------------------------------------------------- 一键生成今日情报（写回 选题情报总览.md）
_INTEL_GEN_SYSTEM = r"""你是博主的选题情报编辑，账号做什么行业、筛什么选题，全部以账号背景为准。
根据给定素材，产出今天的一节选题情报 Markdown。
账号背景（筛选标准全部以它为准）：
{account}

筛选铁律（比格式更重要）：
- A 段每条必须和账号定位强相关：要么账号受众关心，要么能当账号某个内容支柱的开场引子。和账号行业无关的条目直接丢掉，宁少勿滥。
- 素材里的 AI 行业源只是素材之一：账号定位与 AI 无关时，AI 素材最多保留 1 条（还得能挂到账号上讲），其余条目从自定义资讯源/热榜里出。
- A 段每条的「引子判断」必须点明这条跟账号受众的钱/时间/防坑有什么关系，不许写成纯行业新闻播报。

输出格式硬约束（工作台页面靠解析这段显示，格式错=页面空白，必须逐条遵守）：
1. 第一行必须是段标题：## 选题情报 · {today}（星期{weekday}）
2. 必须包含「### A. 今日行业大事 Top5（只做引子，不做主体）」和「### B. 近 7 天爆款视频 / 话题盘点」两个小节（小节标题原文照抄）。A 段最多 5 条，素材不够可写 3 条；B 段无素材就写一行「今日无（未抓取到可靠爆款数据）」，但小节标题不能省。
3. A/B 每条格式：`1. **标题**——一句话描述`，下面跟 bullet：A 段用 `- 来源：[名称](链接)` 和 `- 引子判断：**判断**——理由`；B 段用 `- 平台：xx ｜ 链接：[名称](链接)`、`- 可跟做角度：...`、`- 留资钩子实物：...`。bullet 键名不超过 16 字。
4. A 之后加「### C. 今日推荐」：三条，每条 `- **推荐N（支柱名）**：**标题**` + 两行 `- **开头钩子**：...`（反直觉开场一句）/ `- **留资关键词**：**关键词**（送什么实物）`。留资=评论区扣关键词领实物，禁止「加微信/扫码」站外引流。
5. 最后加「### 沉淀提示（新三问 ≥2 中的候选，可考虑沉淀进 candidates.md 候选池）」编号列表。
6. 严禁四级标题（####）、表格、代码块。只输出这一节 Markdown，不要任何前后解释或寒暄。"""


def api_intel_gen():
    """用 eMotionCut 当前接入的 Agent，把实时素材筛成今日情报并写进总览。"""
    import aiwrite
    cfg = aiwrite.load_cfg()

    stat = {"aihot": 0, "builders": 0, "hotboard": 0, "custom": 0}
    c_urls, skip_ai = load_custom_sources()
    if skip_ai:
        aihot_md = "（资讯源设置=不带AI源，本次不抓）"
        b_md = "（资讯源设置=不带AI源，本次不抓）"
    else:
        # 素材一：aihot 官方 API
        try:
            its = fetch_aihot_items(limit=10)
            stat["aihot"] = len(its)
            aihot_md = "\n".join("%d. %s（链接：%s）" % (i, x["text"], x.get("url") or "无")
                                 for i, x in enumerate(its, 1))
        except Exception as exc:  # noqa: BLE001
            aihot_md = "（此源抓取失败：%s。没有把握的事件不要写。）" % str(exc)[:120]
        # 素材二：builder 一手动态
        try:
            bs = fetch_builders_items(limit=15)
            stat["builders"] = len(bs)
            b_md = "\n".join("%d. %s（点赞 %d）" % (i, x["text"], x.get("likes") or 0)
                             for i, x in enumerate(bs, 1))
        except Exception as exc:  # noqa: BLE001
            b_md = "（此源抓取失败：%s）" % str(exc)[:120]
    # 素材三：自定义资讯源
    if c_urls:
        try:
            cs = fetch_custom_items(c_urls)
            ok_cs = [x for x in cs if "源不可用" not in x["text"]]
            stat["custom"] = len(ok_cs)
            custom_md = "\n".join("%d. %s（链接：%s）" % (i, x["text"], x.get("url") or "无")
                                  for i, x in enumerate(ok_cs, 1)) or "（全部源都抓取失败）"
        except Exception as exc:  # noqa: BLE001
            custom_md = "（抓取失败：%s）" % str(exc)[:120]
    else:
        custom_md = "（未配置自定义资讯源，去工作区\\资讯源.txt 填）"
    # 素材三：今日热榜缓存（有就用，没有不强求——不开浏览器）
    hb = ab_last("B") or {}
    hb_path = hb.get("path") or ""
    hb_items = hb.get("items") or []
    if hb_items and time.strftime("%Y-%m-%d") in os.path.basename(hb_path):
        words = [((x.get("title") or x.get("word") or "").strip()) for x in hb_items]
        words = [w for w in words if w][:30]
        if words:
            hb_md = "、".join(words)
            stat["hotboard"] = len(words)
        else:
            hb_md = "（今日未抓热榜，B 段写「今日无（未抓取到可靠爆款数据）」）"
    else:
        hb_md = "（今日未抓热榜，B 段写「今日无（未抓取到可靠爆款数据）」）"
    # 查重：昨天已推过的标题
    prev = parse_intel()
    prev_md = "、".join((f.get("title") or "").strip()
                        for f in prev.get("facts", [])[:8] if f.get("title")) or "（无）"

    today = time.strftime("%Y-%m-%d")
    weekday = "日一二三四五六"[int(time.strftime("%w"))]
    system = _INTEL_GEN_SYSTEM.format(account=position_md()[:800], today=today, weekday=weekday)
    user = ("今天是 %s（星期%s）。素材如下，全部来自实时抓取，只可采信、不可编造。\n\n"
            "【素材一：aihot.news 官方 API 热点（事件聚簇+热度排名，AI 行业）】\n%s\n\n"
            "【素材二：AI builder 一手动态（过去 24h X 推文，按点赞降序；账号与 AI 无关时只挑能挂到账号上讲的）】\n%s\n\n"
            "【素材三：自定义资讯源（博主自己配的行业源）】\n%s\n\n"
            "【素材四：今日抖音热榜缓存（全行业热词参考）】\n%s\n\n"
            "【昨天已推送过的标题（不要重复主推，可换角度）】\n%s"
            % (today, weekday, aihot_md, b_md, custom_md, hb_md, prev_md))
    try:
        md = aiwrite.chat(cfg, system, user, timeout=240)
    except Exception as exc:  # noqa: BLE001
        return {"error": "模型调用失败：%s" % str(exc)[:200]}

    # 清洗：去代码围栏；段标题必须带今天日期
    md = re.sub(r"```[a-zA-Z]*\n?", "", md).strip()
    md = "\n".join(ln for ln in md.splitlines()
                   if not re.match(r"^##\s*选题情报", ln.strip()))
    md = "## 选题情报 · %s（星期%s）\n\n%s" % (today, weekday, md.strip())
    sec_lines = md.splitlines()

    # 写回：今天已有段就替换，否则追加到末尾
    txt = read_text(TOPIC_OVERVIEW) or ""
    lines = txt.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s*选题情报\s*·\s*" + re.escape(today), ln.strip()):
            start, end = i, len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^##\s*选题情报\s*·", lines[j].strip()):
                    end = j
                    break
            break
    if start is not None:
        new_txt = "\n".join(lines[:start] + sec_lines + lines[end:])
        replaced = True
    else:
        new_txt = (txt.rstrip() + "\n\n" + md + "\n") if txt.strip() else (md + "\n")
        replaced = False
    with open(TOPIC_OVERVIEW, "w", encoding="utf-8") as f:
        f.write(new_txt)
    return {"ok": True, "date": today, "replaced": replaced, "sources": stat}


# ---------------------------------------------------------------- 选题调研统计（Pongfi 方法论）
_NGRAM_STOP = {"我们", "你们", "他们", "这个", "那个", "一个", "什么", "怎么", "如何",
               "可以", "不是", "就是", "视频", "教程", "干货", "分享", "真的", "原来",
               "终于", "今天", "昨天", "大家", "自己", "没有", "还有", "出来", "开始",
               "以后", "时候", "知道", "觉得", "东西", "地方", "直接", "竟然", "居然",
               "小白", "普通人", "一分钟", "三分钟", "分钟"}


def _likeint(v):
    """赞数兼容 int / '246' / '1.2万' / '3亿' 等各种形态，统一转 int。"""
    s = str(v or "").strip().replace("+", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([万亿]?)", s)
    if not m:
        return 0
    n = float(m.group(1))
    if m.group(2) == "万":
        n *= 10000
    elif m.group(2) == "亿":
        n *= 100000000
    return int(n)


def research_stats(items, max_dirs=6):
    """方向聚类：2-4 字 n-gram 找高频词 → 按词分组 → 组内赞中位数（相对表现）。

    Demand=条数、Supply=供给饱和（同方向条数占比）、RelativePerformance=组内赞中位÷全池中位。
    深挖过的样本带 comment（评论数）→ 追加互动维度：全池评论中位、各方向评论中位与评赞比。
    （Momentum 由「按最新排序」保证；Replicability/Fit 交给模型结合账号定位判断。）
    """
    import statistics
    titles = [(it.get("title") or "").strip() for it in items]
    likes = [_likeint(it.get("like")) for it in items]
    pos = [x for x in likes if x > 0] or [0]
    med = statistics.median(pos)
    # 互动维度（没深挖过的样本没有 comment 字段，评论全 0 时自动不展示）
    comments = [_likeint(it.get("comment")) for it in items]
    has_cmt = any(c > 0 for c in comments)
    cmed = statistics.median([c for c in comments if c > 0] or [0]) if has_cmt else 0

    def _eng_meta(idxs):
        if not has_cmt:
            return ""
        cm = statistics.median([comments[i] for i in idxs] or [0])
        lm = statistics.median([likes[i] for i in idxs if likes[i] > 0] or [0])
        ratio = ("%.1f" % (cm / lm)) if lm else "0"
        return " | 评论中位 %d | 评赞比 %s" % (cm, ratio)

    docs = {}
    for idx, t in enumerate(titles):
        t2 = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", "", t)
        for n in (2, 3, 4):
            for i in range(max(0, len(t2) - n + 1)):
                w = t2[i:i + n]
                if w in _NGRAM_STOP or w.isdigit():
                    continue
                if re.search(r"[A-Za-z0-9]", w) and re.search(r"[\u4e00-\u9fa5]", w):
                    continue          # 跨中英文的碎片（如「I剪」「做游」）不要
                docs.setdefault(w, set()).add(idx)
    cand = sorted(((len(v), w) for w, v in docs.items() if len(v) >= 2), reverse=True)
    picked = []
    for c, w in cand:
        if any(w in p["term"] or p["term"] in w for p in picked):
            continue
        grp = sorted(docs[w])
        dl = [likes[i] for i in grp]
        dmed = statistics.median([x for x in dl if x > 0] or [0])
        picked.append({"term": w, "count": c, "med": int(dmed),
                       "ratio": round(dmed / med, 1) if med else 0,
                       "eng": _eng_meta(grp),
                       "titles": [titles[i] for i in grp[:6]]})
        if len(picked) >= max_dirs:
            break
    return {"n": len(items), "med": int(med), "dirs": picked, "has_cmt": has_cmt,
            "cmed": int(cmed)}


# ---------------------------------------------------------------- 隐藏生图（leyia gpt-image-2，仅自用）
LEY_LOCK = threading.Lock()
LEY = {"running": False, "done": False, "ok": False, "lines": [],
       "out": "", "error": "", "started": 0.0, "elapsed": 0.0}
LEY_REFS = os.path.join(CACHE_DIR, "leyia_refs")
os.makedirs(LEY_REFS, exist_ok=True)


def _ley_log(m, lvl="info"):
    with LEY_LOCK:
        LEY["lines"].append("[%s] %s" % (time.strftime("%H:%M:%S"), m))


def run_leyia_job(prompt, aspect, quality, ref_paths):
    """后台线程：调 leyia 生图，状态写 LEY，前端轮询 /api/leyia/job。"""
    import leyia
    with LEY_LOCK:
        LEY.update(running=True, done=False, ok=False, lines=[],
                   out="", error="", started=time.time(), elapsed=0.0)
    _ley_log("提交 leyia 生图任务（%s，%d 张参考图）" % (aspect, len(ref_paths)))
    try:
        cfg = leyia.load_cfg()
        cfg["prompt"] = prompt
        cfg["aspect_ratio"] = aspect
        cfg["quality"] = quality or cfg.get("quality") or "medium"
        dst = os.path.join(LEYIA_OUT, "leyia_%s.png" % time.strftime("%m%d-%H%M%S"))
        r = leyia.generate_image(dst, cfg, ref_paths=ref_paths, on_log=_ley_log)
        with LEY_LOCK:
            LEY["out"] = r["dst"]
            LEY["ok"] = True
    except StopIteration:
        _ley_log("已停止")
    except Exception as exc:  # noqa: BLE001
        with LEY_LOCK:
            LEY["error"] = str(exc)
        _ley_log("失败：%s" % exc)
    finally:
        with LEY_LOCK:
            LEY["running"] = False
            LEY["done"] = True
            LEY["elapsed"] = time.time() - LEY["started"]


def _save_data_url(data_url, idx):
    """把前端传来的 dataURL 参考图落到 缓存\\leyia_refs，返回本地路径。"""
    m = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$",
                 data_url or "", re.S)
    if not m:
        raise ValueError("参考图格式不对（要 png/jpg/webp 的 base64）")
    ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}[m.group(1)]
    import base64
    raw = base64.b64decode(m.group(2))
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("参考图太大（超 12MB）")
    path = os.path.join(LEY_REFS, "%s_r%d.%s"
                        % (time.strftime("%m%d-%H%M%S"), idx, ext))
    with open(path, "wb") as f:
        f.write(raw)
    return path


# ---------------------------------------------------------------- HTTP
LEY_BAL = {"value": None, "ts": 0.0, "note": "", "running": False}


def _ley_refresh_balance():
    global LEY_BAL
    import leyia
    try:
        cfg = leyia.load_cfg()
        bal, cfg2 = leyia.balance_with_relogin(cfg)
        leyia.save_cfg(cfg2)
        LEY_BAL["value"] = bal
        LEY_BAL["note"] = ""
    except Exception as exc:  # noqa: BLE001
        LEY_BAL["note"] = "取余额失败（token 可能过期，重新登录即可）：%s" % str(exc)[:120]
    finally:
        LEY_BAL["ts"] = time.time()
        LEY_BAL["running"] = False


# ---- BGM 三层曲库 + 混音
def parse_srt(text):
    """解析 SRT 字幕 → [{s,e,text}]（容忍 BOM/序号行/多余空行）"""
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff \n"))
    cues = []
    for blk in blocks:
        ls = [l for l in blk.split("\n") if l.strip()]
        ti = None
        for i, l in enumerate(ls):
            if "-->" in l:
                ti = i
                break
        if ti is None:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", ls[ti].strip())
        if not m:
            continue
        g = m.groups()
        s = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000.0
        e = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000.0
        txt = "".join(ls[ti + 1:]).strip()
        if txt:
            cues.append({"s": round(s, 2), "e": round(e, 2), "text": txt})
    return cues


def ffprobe_video(fp):
    """用 ffmpeg -i 探测视频 meta（imageio-ffmpeg 不带 ffprobe）→ {width,height,fps,duration}"""
    import imageio_ffmpeg  # noqa: PLC0415
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        r = subprocess.run([ff, "-i", fp], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
    except Exception:  # noqa: BLE001
        return None
    txt = (r.stderr or "")
    out = {}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        out["duration"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", txt)
    if m:
        out["fps"] = int(round(float(m.group(1))))
    return out if out.get("width") and out.get("height") else None


def cues_to_lines(cues):
    """SRT 短句 → 台词行（编导大脑按行读稿）：句末标点 / 停顿>2s / 行超12s或60字 收一行"""
    lines, cur = [], None
    for c in cues:
        if cur is not None and (c["s"] - cur["e"]) > 2.0:
            lines.append(cur)
            cur = None
        if cur is None:
            cur = {"s": c["s"], "e": c["e"], "text": c["text"]}
        else:
            cur["text"] += c["text"]
            cur["e"] = c["e"]
        if re.search(r"[。！？；…!?;:.：]", c["text"]) or (cur["e"] - cur["s"]) > 12 or len(cur["text"]) > 60:
            lines.append(cur)
            cur = None
    if cur:
        lines.append(cur)
    return lines


def bgm_list():
    try:
        names = [f for f in os.listdir(BGM_DIR)
                 if f.lower().endswith((".mp3", ".wav", ".m4a", ".flac", ".ogg"))]
    except OSError:
        names = []
    return sorted(names)


# ---------------------------------------------------------------- 自动剪辑一条龙
# 🤖 自动剪辑：选视频 → whisper 转写 → LLM 编排 → 错字校正 → 分镜方案摘要
# A 路线（纯文案）的配音初片（文案页 ⚡一键成片 产出）与 B 路线（真人素材）
# 走同一条流水线，区别只是视频来源。
AUTO_PY = os.path.join(HERE, "auto_director.py")   # 转写脚本（复用口播一键成片 whisper 链路）
AUTO_JOBS = {}                                      # id -> {status,stage,pct,t0,msg,log,p,summary}
AUTO_LOCK = threading.Lock()


def new_props_core(nm, vi):
    """选视频源 → 探测 meta → 拷入 motion-engine/public/ → 生成空白 props_<nm>.json
    返回 (props文件名, meta, 错误信息)"""
    if not re.match(r"[\w-]{1,40}$", nm or ""):
        return None, None, "工程名只允许中英文/数字/_-，40 字以内"
    try:
        vi = int(vi)
    except (TypeError, ValueError):
        vi = 0
    pub = os.path.join(ME_DIR, "public")
    pub_files = sorted(f for f in os.listdir(pub)
                       if f.endswith(".mp4")) if os.path.isdir(pub) else []
    cands = [os.path.join(pub, f) for f in pub_files]
    for d in (WORK + os.sep + "缓存", WORK + os.sep + "输出"):
        if os.path.isdir(d):
            cands += [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.endswith(".mp4") and not f.startswith("_")]
    fp = cands[vi] if 0 <= vi < len(cands) else None
    if not fp or not os.path.isfile(fp):
        return None, None, "视频源不存在"
    meta = ffprobe_video(fp)
    if not meta:
        return None, None, "视频探测失败（不是有效的 mp4？）"
    os.makedirs(pub, exist_ok=True)
    dst = os.path.join(pub, nm + ".mp4")
    if os.path.abspath(fp) != os.path.abspath(dst):
        shutil.copy2(fp, dst)
    props = {
        "meta": {"width": meta["width"], "height": meta["height"],
                 "fps": meta.get("fps") or 25, "duration": meta.get("duration") or 0},
        "videoSrc": nm + ".mp4",
        "slides": [], "chapters": [], "tags": [], "phrases": [], "sfx": [],
        "lines": [], "subs": [], "words": [],
    }
    out = "props_" + nm + ".json"
    with open(os.path.join(ME_DIR, out), "w", encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False, indent=1)
    return out, props["meta"], None


def orchestrate_srt(name, srt_text):
    """SRT 文本 → 写入 props（lines/subs）→ director.py 编导大脑(LLM)编排 → 还原原生 subs。
    返回 (ok, log尾部, 台词行数, 字幕条数)"""
    cues = parse_srt(srt_text or "")
    if not cues:
        return False, "SRT 解析失败：没有有效的字幕块（检查 --> 时间轴）", 0, 0
    fp = os.path.join(ME_DIR, name)
    bak = fp + ".bak_pre_srt"
    if not os.path.isfile(bak):
        shutil.copy2(fp, bak)
    props = json.loads(read_text(fp))
    lines = cues_to_lines(cues)
    dur = cues[-1]["e"]
    props["lines"] = lines
    props["subs"] = cues
    props["words"] = []      # SRT 没有词级时间戳，清空避免旧词错位
    props["phrases"] = []    # 旧大字时间全部失效，一并清空
    if isinstance(props.get("meta"), dict):
        props["meta"]["duration"] = dur
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False, indent=1)
    # 编导大脑五步（LLM）：读稿→通读→分镜→校验→音效排期
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(ME_DIR, "director.py"), name],
            cwd=ME_DIR, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900)
        okc = (r.returncode == 0)
        tail = ((r.stdout or "") + (r.stderr or ""))[-1500:]
    except Exception as exc:  # noqa: BLE001
        okc, tail = False, "编导大脑执行异常: %s" % exc
    if okc:
        # director 会用 lines 重建 subs（无词级时兜底），这里还原回 SRT 原生分段
        props = json.loads(read_text(fp))
        props["subs"] = cues
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(props, f, ensure_ascii=False, indent=1)
    return bool(okc), tail, len(lines), len(cues)


def _auto_pipeline_job(jid, nm, vi):
    """🤖 自动剪辑后台线程：转写 → 编排 → 错字校正 → 摘要。状态写 AUTO_JOBS[jid]。"""
    def push(**kw):
        with AUTO_LOCK:
            if jid in AUTO_JOBS:
                AUTO_JOBS[jid].update(kw)

    try:
        # ① 探测视频 + 建空白工程
        push(stage="probe", msg="探测视频 + 建工程…", pct=4)
        props_name, meta, err = new_props_core(nm, vi)
        if err:
            push(status="failed", log=err)
            return
        video = os.path.join(ME_DIR, "public", nm + ".mp4")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")

        # ② whisper 转写 → SRT（复用口播一键成片已验证链路，本地离线）
        push(stage="transcribe", msg="whisper 识别中（CPU，几分钟属正常）…", pct=12)
        srt_fp = os.path.join(ME_DIR, "_auto", nm + ".srt")
        t0 = time.time()
        try:
            r = subprocess.run(
                [PY, "-X", "utf8", AUTO_PY, video, srt_fp],
                cwd=HERE, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800)
            rt = (r.stdout or "") + (r.stderr or "")
        except Exception as exc:  # noqa: BLE001
            rt = "转写执行异常: %s" % exc
            r = None
        if r is None or r.returncode != 0 or not os.path.isfile(srt_fp):
            push(status="failed",
                 log="转写失败：" + rt.strip()[-700:] or "无输出")
            return
        with AUTO_LOCK:
            if jid in AUTO_JOBS:
                AUTO_JOBS[jid]["msg"] = "识别完成 %d 条字幕（%.0fs）→ 编排中…" % (
                    int(re.search(r"OK (\d+)", rt.strip()).group(1))
                    if re.search(r"OK (\d+)", rt.strip()) else 0, time.time() - t0)

        # ③ 编导大脑（LLM）分镜编排
        push(stage="orchestrate", msg="编导大脑（LLM）读稿→分镜→音效排期…", pct=45)
        with open(srt_fp, "r", encoding="utf-8") as f:
            srt_text = f.read()
        okc, tail, nlines, ncues = orchestrate_srt(props_name, srt_text)
        if not okc:
            push(status="failed", log="编排失败：" + tail[-800:])
            return

        # ④ 错字校正（人工校正表 + LLM 上下文校对；失败不阻断）
        push(stage="typo", msg="错字校正（校正表 + LLM）…", pct=82)
        try:
            rt2 = subprocess.run(
                [PY, "-X", "utf8", os.path.join(ME_DIR, "fix_typo.py"), props_name],
                cwd=ME_DIR, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=600)
            tail += "\n[错字校正] " + ((rt2.stdout or "") + (rt2.stderr or ""))[-400:].strip()
        except Exception as exc:  # noqa: BLE001
            tail += "\n[错字校正] 异常(忽略): %s" % exc

        # ⑤ 分镜方案摘要（给用户确认再渲染）
        props = json.loads(read_text(os.path.join(ME_DIR, props_name)))
        slides = sorted((props.get("slides") or []), key=lambda s: (s.get("s") or 0))
        summary = {
            "duration": (props.get("meta") or {}).get("duration") or 0,
            "lines": len(props.get("lines") or []),
            "cues": len(props.get("subs") or []),
            "slides": [{"kind": s.get("kind"), "s": round(s.get("s") or 0, 1),
                        "e": round(s.get("e") or 0, 1),
                        "title": s.get("title") or s.get("leftTitle") or ""}
                       for s in slides],
        }
        push(status="done", pct=100, stage="done", msg="完成",
             log=tail[-900:], p=props_name, summary=summary)
    except Exception as exc:  # noqa: BLE001
        push(status="failed", log="自动剪辑流水线异常: %s" % exc)


# ---------------------------------------------------------------- ⚡科技风模板整片出片
# 酷炫科技风 .motion 模板（adu-motion-standalone）接入：文案 → LLM 拆 B1-B4 场景
# → 模板 plan/prepare/render/mix → 成片库。纯文字动效路线，无需素材视频。
MOTION_DIR = r"E:\AI小程序\adu-motion-standalone"
MOTION_RUNNER = os.path.join(MOTION_DIR, "runner")
MOTION_JOBS_DIR = os.path.join(MOTION_DIR, "jobs")
MOTION_UV = os.path.join(os.path.dirname(PY), "uv.exe")
MOTION_NODE = "node"
MJOBS = {}                           # id -> {status,stage,pct,t0,msg,log,out}
MLOCK = threading.Lock()
JYJOBS = {}                          # id -> 同 MJOBS（剪映草稿导出任务）
JYLOCK = threading.Lock()
PROBE_LOCK = threading.Lock()        # 元素探针的 plan/prepare 缓存互斥

# 字数上限（中文，adapter 强校验）：keyword8 heading10 left/right8 summary10
# explanation42 conclusion48 action42；B1=关键词 B2=要点 B3=对比 B4=行动
# B2 和 B3 共用 heading，一条片只能二选一。
_MOTION_PROMPT = """你是短视频分镜师。把下面的文案拆成「酷炫科技风」文字动效场景，输出纯 JSON（不要多余文字）。

可用场景组（2~5 组，按文案语义选）：
- B1 关键词开场：keyword(≤8字) + explanation(≤36字，一行超18字就用\\n分两行)
- B2 要点列举：heading(≤10字) + points(1~5条，每条≤16字)
- B3 左右对比：heading(≤10字) + left(≤8字) + right(≤8字) + conclusion(≤48字，一行超26字就用\\n分两行)
- B4 行动收尾：summary(≤10字) + action(≤36字，一行超18字就用\\n分两行)

硬规则：
1. B2 和 B3 都用 heading，一条片只能二选一，绝不同时出现。
2. 超字数会被渲染器直接报错，宁短勿长。
3. 保留否定、条件、数字和结论，不要美化未完成的事。
4. 输出格式：
{"scenes":[{"id":"B2","title":"≤45字的小标题"},...],"basic":{"heading":"...","points":["..."],"summary":"...","action":"..."}}
basic 里只放所选场景需要的字段。

文案：
"""


def _motion_rewrap(text, per, total):
    """单行宽度防线：fitBody 固定字号量宽，单行超宽直接渲染崩溃。
    900px@42px≈21字(action/explanation)、1180px@40px≈29字(conclusion)，
    留余量后超长文本在标点处折成两行（fitBody 最多允许 2 行）。"""
    text = str(text or "").strip()
    if not text:
        return text
    if len(text) > total:          # adapter 连 \n 一起计入总长
        text = text[:total - 1]
    if len(text) <= per:
        return text
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) == 2 and all(len(l) <= per for l in lines):
        return "\n".join(lines)
    half = len(text) / 2.0
    best = None
    for i, ch in enumerate(text):
        if ch in "，,；;：:、 ":
            d = abs(i - half)
            if best is None or d < best[1]:
                best = (i, d)
    if best is not None and 1 < best[0] < len(text) - 1:
        a, b = text[:best[0]], text[best[0] + 1:]
    else:
        m = max(1, int(half))
        a, b = text[:m], text[m:]
    return a[:per] + "\n" + b[:per]


# 动效 JSON → .motion 渲染的路由映射
_MJ_SCENE_BASE = {"B1": 8, "B2": 10, "B3": 10, "B4": 8}   # 场景原生动作时长（秒）
_KIN_ALIAS = {"b1_keyword": "B1", "b2_points": "B2",
              "b3_compare": "B3", "b4_action": "B4"}       # 内置科技风卡型 → 场景 id


def _build_ass(cues, w, h):
    """cues(SRT 字典列表) → ASS 字幕全文（纯字幕模式烧录用）。"""
    def ts(t):
        cs = int(round(t * 100))
        return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)
    fs = max(28, round(h * 0.052))
    lines = [
        "[Script Info]", "ScriptType: v4.00+",
        "PlayResX: %d" % w, "PlayResY: %d" % h, "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Sub,Microsoft YaHei,%d,&H00FFFFFF,&H000000FF,&H00101010,&H7F000000,-1,0,0,0,100,100,0,0,1,%d,1,2,%d,%d,%d,1"
        % (fs, max(2, fs // 14), round(w * 0.06), round(w * 0.06), round(h * 0.085)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for c in cues:
        txt = str(c.get("text") or "").replace("\n", "\\N")
        lines.append("Dialogue: 0,%s,%s,Sub,,0,0,0,,%s" % (ts(c["s"]), ts(c["e"]), txt))
    return "\n".join(lines)


def _motion_manifest_from_schema(pkg_dir, slug, filename):
    """.motion 包 → manifest.json（kinds 来自 params.schema.json + adapter.py 真实限制）。
    返回 manifest dict；解析不出场景就抛 ValueError。"""
    import ast
    schema = json.load(open(os.path.join(pkg_dir, "params.schema.json"), encoding="utf-8"))
    props = (schema.get("properties") or {})
    try:
        enum = props["scenes"]["items"]["properties"]["id"]["enum"]
    except (KeyError, TypeError):
        raise ValueError("params.schema.json 里没有 scenes 场景枚举")
    basic_props = (props.get("basic") or {}).get("properties") or {}
    # adapter.py 里的 requirements/limits 是渲染器的真实硬限制（schema 的 maxLength 偏松）
    reqs, lims = {}, {}
    ap = os.path.join(pkg_dir, "adapter.py")
    if os.path.isfile(ap):
        try:
            src = open(ap, encoding="utf-8", errors="replace").read()
            m = re.search(r"requirements=\{([^}]*)\}", src)
            if m:
                reqs = ast.literal_eval("{" + m.group(1) + "}")
            m = re.search(r"limits=\{([^}]*)\}", src)
            if m:
                lims = {k: int(v) for k, v in ast.literal_eval("{" + m.group(1) + "}").items()}
        except Exception:  # noqa: BLE001
            reqs, lims = {}, {}
    ids = list(reqs.keys()) if reqs else [
        x for x in enum if not re.match(r"^(S\d+|A|C|D|F|END)$", str(x))]
    kinds = {}
    for sid in ids:
        fields = {}
        for k in reqs.get(sid, basic_props.keys()):
            v = basic_props.get(k)
            if v is None:
                continue
            if v.get("type") == "array":
                fields[k] = [int(v.get("minItems", 1)), int(v.get("maxItems", 5)),
                             min(int(v.get("items", {}).get("maxLength", 20)), 18)]
            else:
                fields[k] = int(lims.get(k, v.get("maxLength", 20)))
        if fields:
            kinds[str(sid)] = {"fields": fields}
    if not kinds:
        raise ValueError("没解析出任何可用场景")
    name = filename
    try:
        g = json.load(open(os.path.join(pkg_dir, "guide.json"), encoding="utf-8"))
        name = g.get("title") or g.get("name") or name
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": "pkg-" + slug, "name": name[:24], "engine": "motion-pkg",
        "pkg": slug, "theme": "导入模板",
        "desc": "导入的 .motion 模板包（%s），场景卡型 %d 个" % (filename, len(kinds)),
        "kinds": kinds,
    }


def _kit_doc_hit(doc):
    """doc 全部卡型命中同一个 MotionGit 官方管线模板 → 返回该 manifest，否则 None。"""
    slides = doc.get("slides") or []
    if not slides:
        return None
    hit = None
    for sl in slides:
        kid = sl.get("kind")
        h2 = None
        for m in _mj.load_registry():
            if kid in (m.get("kinds") or {}) and m.get("kit"):
                h2 = m
                break
        if h2 is None:
            return None
        if hit is not None and h2["id"] != hit["id"]:
            return None
        hit = h2
    return hit


def _mj_render_job(jid, mid):
    """🎬 导出成片后台线程：动效 JSON → 模板渲染 → 成片库。
    路由：全部卡型属 motion 引擎 → .motion 管线；无卡 → 纯字幕烧录；否则明确报错。"""
    def push(**kw):
        with MLOCK:
            if jid in MJOBS:
                MJOBS[jid].update(kw)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env["PATH"] = os.path.join(HERE, "工具", "ffmpeg") + os.pathsep + env.get("PATH", "")
    FF = os.path.join(HERE, "工具", "ffmpeg", "ffmpeg.exe")
    try:
        fp = os.path.join(MJ_DIR, mid + ".json")
        doc = json.loads(open(fp, encoding="utf-8").read())
        slides = doc.get("slides") or []
        base_id = (doc.get("global") or {}).get("source", {}).get("base", "")
        meta = _vf.load_meta(base_id) or {}
        video = os.path.join(_vf.BASE_DIR, os.path.basename(base_id), "底片.mp4")

        # ---------- 路由 A：纯字幕（无卡）→ ffmpeg 烧字幕 ----------
        if not slides:
            push(stage="subs", msg="纯字幕模式：烧录字幕…", pct=8)
            if not os.path.isfile(video):
                push(status="failed", log="底片文件缺失：" + video); return
            cues = _vf.load_cues(base_id)
            if not cues:
                push(status="failed", log="底片没有时间轴（cues.cue 空）"); return
            import tempfile
            tdir = tempfile.gettempdir()          # ASS 路径必须纯 ASCII（libass 老坑）
            apath = os.path.join(tdir, "subs_%s.ass" % jid)
            with open(apath, "w", encoding="utf-8") as f:
                f.write(_build_ass(cues, meta.get("w", 1920), meta.get("h", 1080)))
            out_name = "成片_%s.mp4" % mid
            dst = os.path.join(CLIP_DIR, out_name)
            proc = subprocess.Popen(
                [FF, "-hide_banner", "-y", "-i", video, "-vf", "ass=subs_%s.ass" % jid,
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "copy", dst],
                cwd=tdir, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            dur = float(meta.get("duration") or 0) or 1
            for line in proc.stderr:
                m2 = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
                if m2:
                    t = int(m2.group(1)) * 3600 + int(m2.group(2)) * 60 + float(m2.group(3))
                    push(pct=int(8 + 88 * t / dur), msg="烧录字幕 %.0f / %.0f 秒…" % (t, dur))
            proc.wait()
            try:
                os.remove(apath)
            except OSError:
                pass
            if proc.returncode != 0 or not os.path.isfile(dst):
                push(status="failed", log="字幕烧录失败（exit %s）" % proc.returncode); return
            push(status="done", pct=100, stage="done", msg="完成",
                 out="/api/tl/seg/" + urllib.parse.quote(out_name))
            return

        # ---------- 路由 C：MotionGit 官方管线（motion-kit / native） ----------
        kit_hit = _kit_doc_hit(doc)
        if kit_hit:
            try:
                import motionkit
                wk = os.path.join(HERE, "工作区", "kit_run", mid)
                media = os.path.join(wk, "media")
                os.makedirs(media, exist_ok=True)
                push(stage="prepare", msg="准备官方管线素材…", pct=4)
                talk = os.path.join(media, "talk.wav")
                if not os.path.isfile(talk):
                    ra = subprocess.run(
                        [FF, "-hide_banner", "-y", "-i", video, "-vn",
                         "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", talk],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace", env=env)
                    if ra.returncode != 0 or not os.path.isfile(talk):
                        push(status="failed",
                             log="提取配音失败：" + (ra.stderr or "")[-200:])
                        return
                dur = float(meta.get("duration") or 0)
                cues = _vf.load_cues(base_id)
                if not cues:
                    push(status="failed", log="底片没有时间轴（cues.cue 空）"); return
                pkg = kit_hit["kit"].get("pkg")

                def klog(msg, _pct={"v": 10}):
                    s2 = str(msg)
                    if "prepare" in s2:
                        _pct["v"] = 35
                    elif "抽帧" in s2 or "preview" in s2:
                        _pct["v"] = 60
                    elif "渲染" in s2:
                        _pct["v"] = 75
                    push(msg=s2, pct=_pct["v"])

                if (kit_hit["kit"].get("workflow") or "") == "native":
                    caps = motionkit.native_captions(cues)
                    with open(os.path.join(media, "captions.json"), "w", encoding="utf-8") as f:
                        json.dump(caps, f, ensure_ascii=False)
                    bf = (doc.get("global") or {}).get("brief") or {}
                    layout = ("portrait" if int(meta.get("h") or 1080) > int(meta.get("w") or 1920)
                              else "landscape")
                    params = {"media": {"talk": "talk.wav", "captions": "captions.json"},
                              "brief": {"schema": kit_hit["kit"].get("briefSchema") or "adu-auto-brief/1",
                                        "brand": bf.get("brand") or "AI博主",
                                        "layout": layout,
                                        "segments": bf.get("segments") or [
                                            {"topic": "口播", "summary": "全片要点",
                                             "text": "".join(c["text"] for c in cues)}],
                                        "narrationDuration": round(dur, 3),
                                        "header": {"shortTitle": bf.get("title") or "口播成片",
                                                   "source": {"kind": "assistant-summary",
                                                              "field": "narration"}}}}
                    push(stage="render", msg="官方管线渲染中（模板自排画面）…", pct=10)
                    out, _pv = motionkit.render_native(pkg, wk, params, log=klog)
                else:
                    mm = dict(kit_hit)
                    mm.update(kit_hit.get("kit") or {})
                    mm["duration"] = dur
                    mm["captions"] = cues
                    params = motionkit.doc_to_params(doc, media, mm)
                    push(stage="render", msg="官方管线渲染中…", pct=10)
                    out, _pv = motionkit.render_kit(pkg, wk, params, log=klog)
                out_name = "成片_kit_%s.mp4" % mid
                dst = os.path.join(CLIP_DIR, out_name)
                shutil.copy2(out, dst)
                push(status="done", pct=100, stage="done", msg="完成",
                     out="/api/tl/seg/" + urllib.parse.quote(out_name))
            except Exception as exc:  # noqa: BLE001
                push(status="failed", log="官方管线失败：%s" % str(exc)[:400])
            return

        # ---------- 路由 B：motion 引擎（内置科技风 / 导入的 .motion 包） ----------
        reg = {m["id"]: m for m in _mj.load_registry()}
        manifest, scene_ids = None, []
        for sl in slides:
            kid = sl.get("kind")
            sid = _KIN_ALIAS.get(kid, kid)
            if sid in _MJ_SCENE_BASE:
                if manifest is None:
                    manifest = {"id": "kinetic-tech", "engine": "motion"}
                scene_ids.append(sid)
                continue
            hit = None
            for m in reg.values():
                if kid in m.get("kinds", {}):
                    hit = m
                    break
            if hit is None:
                push(status="failed",
                     log="卡型 %r 不在任何注册模板里，无法渲染（先保存让校验器把关）" % kid)
                return
            if hit.get("engine") not in ("motion", "motion-pkg"):
                push(status="failed",
                     log="模板【%s】无法渲染（%s 引擎）。目前可渲染：科技风 B1-B4 / "
                         "导入的 .motion 包 / MotionGit 官方管线 / 纯字幕。"
                         "官方管线模板必须整片独占，不能和其它模板混卡。"
                         % (hit.get("name"), hit.get("engine")))
                return
            if manifest is None:
                manifest = hit
            elif manifest.get("id") != hit.get("id"):
                push(status="failed",
                     log="一张片里混了两个 motion 模板（%s / %s），先统一卡型再导出"
                         % (manifest.get("id"), hit.get("id")))
                return
            scene_ids.append(sid)
        if manifest.get("engine") == "motion-pkg":
            runner = os.path.join(_mj.TPL_DIR, manifest.get("pkg", ""))
            if not os.path.isfile(os.path.join(runner, "adapter.py")):
                push(status="failed", log="导入模板的渲染脚本缺失（adapter.py 不在包里）"); return
            if not os.path.isdir(os.path.join(runner, "node_modules", "remotion")):
                push(stage="npm", msg="首次用这个模板：装渲染依赖（npm ci，几分钟）…", pct=3)
                try:
                    r = subprocess.run(["npm.cmd", "ci", "--no-audit", "--no-fund"],
                                       cwd=runner, env=env, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=1800)
                except FileNotFoundError:
                    r = subprocess.run(["cmd", "/c", "npm", "ci", "--no-audit", "--no-fund"],
                                       cwd=runner, env=env, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=1800)
                if r.returncode != 0:
                    push(status="failed",
                         log="npm ci 失败：" + ((r.stdout or "") + (r.stderr or ""))[-500:])
                    return
        else:
            runner = MOTION_RUNNER
        if not os.path.isfile(os.path.join(runner, "adapter.py")):
            push(status="failed", log="科技风模板 runner 不存在"); return

        # 动效 JSON → user.json（场景 + basic 字段；窗长在 [base, base+12] 内保留用户节奏）
        basic, scenes, total = {}, [], 0.0
        for sl, sid in zip(slides, scene_ids):
            f = sl.get("fields") or {}
            for k2, v in f.items():
                if isinstance(v, list):
                    x = [str(i2).strip() for i2 in v if str(i2).strip()]
                    if x:
                        basic[k2] = x
                elif str(v).strip():
                    basic[k2] = str(v)
            for k2, per, tot in (("explanation", 19, 42), ("action", 19, 42),
                                 ("conclusion", 27, 48)):
                if isinstance(basic.get(k2), str):
                    basic[k2] = _motion_rewrap(basic[k2], per, tot)
            if isinstance(basic.get("points"), list):
                basic["points"] = [str(p).strip()[:16] for p in basic["points"]
                                   if str(p).strip()][:5]
            base = _MJ_SCENE_BASE.get(sid)
            item = {"id": sid,
                    "title": str(f.get("title") or f.get("heading") or f.get("keyword")
                                 or f.get("summary") or ("场景" + sid))[:45]}
            if base:
                try:
                    win = float(sl.get("e", 0)) - float(sl.get("s", 0))
                except (TypeError, ValueError):
                    win = 0
                if base <= win <= base + 12:
                    item["duration"] = round(win, 2)
            total += item.get("duration", base or 8)
            scenes.append(item)
        user = {
            "mode": "user-media", "language": "zh", "layout": "landscape",
            "duration": round(total, 2), "title": mid[:30], "presenterLabel": "",
            "scenes": scenes, "content": {}, "basic": basic,
            "media": {"presenter": {"mode": "none"}, "voice": {"mode": "none"},
                      "music": {"mode": "none"}},
            "soundEffects": "native", "captions": [],
        }
        ts = "%d" % int(time.time())
        job_dir = os.path.join(MOTION_JOBS_DIR, "%s_mj_%s" % (ts, mid))
        os.makedirs(job_dir, exist_ok=True)
        uj = os.path.join(job_dir, "user.json")
        with open(uj, "w", encoding="utf-8") as f:
            json.dump(user, f, ensure_ascii=False, indent=1)

        def run_step(cmd, stage, msg, pct, timeout):
            push(stage=stage, msg=msg, pct=pct)
            r = subprocess.run(cmd, cwd=runner, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            if r.returncode != 0:
                raise RuntimeError("%s 失败：%s" % (
                    stage, ((r.stdout or "") + (r.stderr or ""))[-600:]))
            return (r.stdout or "")

        push(stage="plan", msg="模板排期（plan）…", pct=10)
        run_step([MOTION_UV, "run", "adapter.py", "plan", "--input", uj,
                  "--out", os.path.join(job_dir, "plan.json")], "plan", "模板排期（plan）…", 12, 180)
        inst = os.path.join(job_dir, "instance")
        run_step([MOTION_UV, "run", "adapter.py", "prepare", "--input", uj,
                  "--out", inst], "prepare", "生成渲染实例（prepare）…", 25, 300)

        push(stage="render", msg="渲染中（逐帧）…", pct=30)
        silent = os.path.join(job_dir, "silent.mp4")
        ov, nov = _mj_overrides_from_doc(doc)
        rcmd = [MOTION_NODE, "render.mjs", "--root", inst, "--out", silent]
        if nov:
            ovp = os.path.join(job_dir, "overrides.json")
            with open(ovp, "w", encoding="utf-8") as f:
                json.dump(ov, f, ensure_ascii=False)
            rcmd += ["--overrides", ovp]
        r = subprocess.Popen(
            rcmd,
            cwd=runner, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in r.stdout:
            m2 = re.search(r'"seconds":\s*([\d.]+),\s*"total":\s*([\d.]+)', line)
            if m2:
                done, tot = float(m2.group(1)), float(m2.group(2))
                push(pct=int(30 + 60 * done / max(tot, 1)),
                     msg="渲染中 %.0f / %.0f 秒…" % (done, tot))
        r.wait()
        if r.returncode != 0 or not os.path.isfile(silent):
            push(status="failed", log="渲染失败（exit %s）" % r.returncode); return

        run_step([MOTION_UV, "run", "mix.py", "--instance", inst,
                  "--silent-video", silent], "mix", "混音（模板原生音效）…", 92, 600)
        final = os.path.join(job_dir, "silent-with-audio.mp4")
        if not os.path.isfile(final):
            push(status="failed", log="混音产物缺失"); return
        out_name = "成片_%s.mp4" % mid
        dst = os.path.join(CLIP_DIR, out_name)
        shutil.copy2(final, dst)
        push(status="done", pct=100, stage="done", msg="完成",
             out="/api/tl/seg/" + urllib.parse.quote(out_name))
    except Exception as exc:  # noqa: BLE001
        push(status="failed", log="导出异常: %s" % exc)


def _mj_user_from_doc(doc):
    """动效 JSON → .motion 渲染的 user.json 内容（+ 场景 id 序列）。
    basic 全局收集；窗长在 [base, base+12] 内保留用户节奏，否则用模板原生时长。"""
    basic, scenes, sids, total = {}, [], [], 0.0
    for sl in doc.get("slides") or []:
        sid = _KIN_ALIAS.get(sl.get("kind"), sl.get("kind"))
        f = sl.get("fields") or {}
        for k2, v in f.items():
            if isinstance(v, list):
                x = [str(i2).strip() for i2 in v if str(i2).strip()]
                if x:
                    basic[k2] = x
            elif str(v).strip():
                basic[k2] = str(v)
        for k2, per, tot in (("explanation", 19, 42), ("action", 19, 42),
                             ("conclusion", 27, 48)):
            if isinstance(basic.get(k2), str):
                basic[k2] = _motion_rewrap(basic[k2], per, tot)
        if isinstance(basic.get("points"), list):
            basic["points"] = [str(p).strip()[:16] for p in basic["points"]
                               if str(p).strip()][:5]
        base = _MJ_SCENE_BASE.get(sid)
        item = {"id": sid,
                "title": str(f.get("title") or f.get("heading") or f.get("keyword")
                             or f.get("summary") or ("场景" + sid))[:45]}
        if base:
            try:
                win = float(sl.get("e", 0)) - float(sl.get("s", 0))
            except (TypeError, ValueError):
                win = 0
            if base <= win <= base + 12:
                item["duration"] = round(win, 2)
        total += item.get("duration", base or 8)
        scenes.append(item)
        sids.append(sid)
    user = {
        "mode": "user-media", "language": "zh", "layout": "landscape",
        "duration": round(total, 2), "title": "mj", "presenterLabel": "",
        "scenes": scenes, "content": {}, "basic": basic,
        "media": {"presenter": {"mode": "none"}, "voice": {"mode": "none"},
                  "music": {"mode": "none"}},
        "soundEffects": "native", "captions": [],
    }
    return user, sids


def _mj_jy_job(jid, mid):
    """🧩 导出剪映草稿（解除合并）：无字背景层 + 文字事件表 → 剪映草稿。
    主轨=底片(含配音)；画中画=每张动效卡独立片段；文字=每条独立文本段（原位置）。"""
    def push(**kw):
        with JYLOCK:
            if jid in JYJOBS:
                JYJOBS[jid].update(kw)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env["PATH"] = os.path.join(HERE, "工具", "ffmpeg") + os.pathsep + env.get("PATH", "")
    try:
        fp = os.path.join(MJ_DIR, mid + ".json")
        doc = json.loads(open(fp, encoding="utf-8").read())
        if not (doc.get("slides") or []):
            push(status="failed", log="纯字幕工程没有动效卡，用 🎬 导出成片即可"); return

        # ---------- 路由：所有卡型必须同属一个 motion 模板 ----------
        reg = {m["id"]: m for m in _mj.load_registry()}
        manifest, sids = None, []
        for sl in doc.get("slides") or []:
            kid = sl.get("kind")
            sid = _KIN_ALIAS.get(kid, kid)
            if sid in _MJ_SCENE_BASE:
                if manifest is None:
                    manifest = {"id": "kinetic-tech", "engine": "motion"}
                sids.append(sid)
                continue
            hit = None
            for m in reg.values():
                if kid in m.get("kinds", {}):
                    hit = m
                    break
            if hit is None:
                push(status="failed",
                     log="卡型 %r 不在任何注册模板里，无法渲染（先保存让校验器把关）" % kid)
                return
            if hit.get("engine") not in ("motion", "motion-pkg"):
                push(status="failed",
                     log="模板【%s】无法渲染（%s 引擎）。目前可渲染：科技风 B1-B4 / "
                         "导入的 .motion 包 / MotionGit 官方管线 / 纯字幕。"
                         "官方管线模板必须整片独占，不能和其它模板混卡。"
                         % (hit.get("name"), hit.get("engine")))
                return
            if manifest is None:
                manifest = hit
            elif manifest.get("id") != hit.get("id"):
                push(status="failed",
                     log="一张片里混了两个 motion 模板（%s / %s），先统一卡型再导出"
                         % (manifest.get("id"), hit.get("id")))
                return
            sids.append(sid)
        if manifest.get("engine") == "motion-pkg":
            runner = os.path.join(_mj.TPL_DIR, manifest.get("pkg", ""))
            if not os.path.isfile(os.path.join(runner, "adapter.py")):
                push(status="failed", log="导入模板的渲染脚本缺失（adapter.py 不在包里）"); return
            if not os.path.isdir(os.path.join(runner, "node_modules", "remotion")):
                push(stage="npm", msg="首次用这个模板：装渲染依赖（npm ci，几分钟）…", pct=3)
                try:
                    r = subprocess.run(["npm.cmd", "ci", "--no-audit", "--no-fund"],
                                       cwd=runner, env=env, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=1800)
                except FileNotFoundError:
                    r = subprocess.run(["cmd", "/c", "npm", "ci", "--no-audit", "--no-fund"],
                                       cwd=runner, env=env, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=1800)
                if r.returncode != 0:
                    push(status="failed",
                         log="npm ci 失败：" + ((r.stdout or "") + (r.stderr or ""))[-500:])
                    return
        else:
            runner = MOTION_RUNNER
        if not os.path.isfile(os.path.join(runner, "adapter.py")):
            push(status="failed", log="模板 runner 不存在"); return

        # ---------- 底片（主轨：口播画面 + 配音） ----------
        base_id = (doc.get("global") or {}).get("source", {}).get("base", "")
        meta = _vf.load_meta(base_id) or {}
        base_video = os.path.join(_vf.BASE_DIR, os.path.basename(base_id), "底片.mp4")
        if not os.path.isfile(base_video):
            push(status="failed", log="底片文件缺失：" + base_video); return
        base_dur = float(meta.get("duration") or 0) or None

        # ---------- plan / prepare ----------
        user, _ = _mj_user_from_doc(doc)
        ts = "%d" % int(time.time())
        job_dir = os.path.join(MOTION_JOBS_DIR, "%s_jy_%s" % (ts, mid))
        os.makedirs(job_dir, exist_ok=True)
        uj = os.path.join(job_dir, "user.json")
        with open(uj, "w", encoding="utf-8") as f:
            json.dump(user, f, ensure_ascii=False, indent=1)

        def run_step(cmd, stage, msg, pct, timeout):
            push(stage=stage, msg=msg, pct=pct)
            r = subprocess.run(cmd, cwd=runner, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            if r.returncode != 0:
                raise RuntimeError("%s 失败：%s" % (
                    stage, ((r.stdout or "") + (r.stderr or ""))[-600:]))
            return (r.stdout or "")

        push(stage="plan", msg="模板排期（plan）…", pct=8)
        run_step([MOTION_UV, "run", "adapter.py", "plan", "--input", uj,
                  "--out", os.path.join(job_dir, "plan.json")], "plan",
                 "模板排期（plan）…", 8, 180)
        inst = os.path.join(job_dir, "instance")
        run_step([MOTION_UV, "run", "adapter.py", "prepare", "--input", uj,
                  "--out", inst], "prepare", "生成渲染实例（prepare）…", 18, 300)

        # ---------- 渲染：无字背景层 + 文字事件表（一次渲染同时产出；应用元素调整） ----------
        push(stage="render", msg="渲染无字背景层（文字不烧进画面）…", pct=28)
        bg = os.path.join(job_dir, "bg_nosub.mp4")
        tev = os.path.join(job_dir, "textevents.json")
        ov, nov = _mj_overrides_from_doc(doc)
        rcmd = [MOTION_NODE, "render.mjs", "--root", inst, "--out", bg,
                "--notext", "--textevents", tev]
        if nov:
            ovp = os.path.join(job_dir, "overrides.json")
            with open(ovp, "w", encoding="utf-8") as f:
                json.dump(ov, f, ensure_ascii=False)
            rcmd += ["--overrides", ovp]
        r = subprocess.Popen(
            rcmd,
            cwd=runner, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in r.stdout:
            m2 = re.search(r'"seconds":\s*([\d.]+),\s*"total":\s*([\d.]+)', line)
            if m2:
                done, tot = float(m2.group(1)), float(m2.group(2))
                push(pct=int(28 + 55 * done / max(tot, 1)),
                     msg="渲染无字背景层 %.0f / %.0f 秒…" % (done, tot))
        r.wait()
        if r.returncode != 0 or not os.path.isfile(bg):
            push(status="failed", log="无字背景层渲染失败（exit %s）" % r.returncode); return
        if not os.path.isfile(tev):
            push(status="failed", log="文字事件表缺失（textevents.json）"); return

        # ---------- 混音：保留模板原生音效 ----------
        run_step([MOTION_UV, "run", "mix.py", "--instance", inst,
                  "--silent-video", bg], "mix", "混音（模板原生音效）…", 86, 600)
        bg_full = os.path.join(job_dir, "bg_nosub-with-audio.mp4")
        if not os.path.isfile(bg_full):
            push(status="failed", log="混音产物缺失"); return

        # ---------- 卡窗映射：slide（底片时间）× plan span（输出时间） ----------
        plan = json.load(open(os.path.join(job_dir, "plan.json"), encoding="utf-8"))
        spans = plan.get("spans") or []
        used, pairs = set(), []
        for sl, sid in zip(doc.get("slides") or [], sids):
            for sp in spans:
                if sp.get("id") == sid and id(sp) not in used:
                    used.add(id(sp))
                    pairs.append({"s": float(sl.get("s") or 0),
                                  "e": float(sl.get("e") or 0),
                                  "out_s": float(sp.get("outputStart") or 0),
                                  "out_e": float(sp.get("outputEnd") or 0),
                                  "title": str(sp.get("title") or sid)})
                    break
        if not pairs:
            push(status="failed",
                 log="plan 没有可配对的场景（spans=%d）" % len(spans)); return

        # ---------- 组装剪映草稿 ----------
        import motion_jy
        name = motion_jy.draft_name(mid)
        dpath, stats = motion_jy.build_jy_draft(
            job_dir, pairs, tev, base_video,
            base_dur or max(p["e"] for p in pairs), name, bg_full,
            int(meta.get("w") or 1920), int(meta.get("h") or 1080),
            log=lambda m: push(msg=m))
        push(status="done", pct=100, stage="done", msg="完成",
             out=dpath,
             log="剪映草稿已生成：%s（%d 张卡 / %s 条文字）。打开剪映 → 首页草稿列表即见；"
                 "每张卡、每条文字都独立可调，细调后从剪映导出。"
                 % (name, stats["cards"], stats["texts"]))
    except Exception as exc:  # noqa: BLE001
        push(status="failed", log="导出剪映草稿异常: %s" % exc)


def _mj_overrides_from_doc(doc):
    """doc.slides 上的手工调整 → render.mjs --overrides。
    slide.transform = {sci, dx, dy, scale} 整卡（sci 来自探针 scene_sci）；
    slide.overrides = {k: {dx, dy, scale}} 文字元素（k 来自探针 elements[].k）。
    渲染器用 CSS 独立变换属性 translate/scale，与模板 transform 动画复合不冲突。"""
    ov = {"scenes": {}, "elements": {}}
    for sl in doc.get("slides") or []:
        tr = sl.get("transform") or {}
        if isinstance(tr, dict) and tr.get("sci") is not None:
            ov["scenes"][str(int(tr["sci"]))] = {
                "dx": tr.get("dx", 0) or 0, "dy": tr.get("dy", 0) or 0,
                "scale": tr.get("scale", 1) or 1}
        for k, v in (sl.get("overrides") or {}).items():
            if isinstance(v, dict):
                ov["elements"][str(k)] = {
                    "dx": v.get("dx", 0) or 0, "dy": v.get("dy", 0) or 0,
                    "scale": v.get("scale", 1) or 1}
    return ov, len(ov["scenes"]) + len(ov["elements"])


def _mj_probe_prepare(doc, mid):
    """按 user.json 内容哈希缓存 plan/prepare 实例（元素探针用）。
    返回 (hash, inst_dir, sids, spans)；失败抛 RuntimeError。"""
    import hashlib
    user, sids = _mj_user_from_doc(doc)
    ujs = json.dumps(user, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha1((mid + "|" + ujs).encode("utf-8")).hexdigest()[:16]
    cdir = os.path.join(MOTION_JOBS_DIR, "probe_cache", h)
    inst = os.path.join(cdir, "instance")
    plan_fp = os.path.join(cdir, "plan.json")
    ready = os.path.isfile(plan_fp) and os.path.isdir(os.path.join(inst, "data"))
    if not ready:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env["PATH"] = os.path.join(HERE, "工具", "ffmpeg") + os.pathsep + env.get("PATH", "")
        with PROBE_LOCK:
            if not (os.path.isfile(plan_fp) and os.path.isdir(os.path.join(inst, "data"))):
                os.makedirs(cdir, exist_ok=True)
                uj = os.path.join(cdir, "user.json")
                with open(uj, "w", encoding="utf-8") as f:
                    json.dump(user, f, ensure_ascii=False, indent=1)
                for cmd, tag in (
                        ([MOTION_UV, "run", "adapter.py", "plan", "--input", uj,
                          "--out", plan_fp], "plan"),
                        ([MOTION_UV, "run", "adapter.py", "prepare", "--input", uj,
                          "--out", inst], "prepare")):
                    r = subprocess.run(cmd, cwd=MOTION_RUNNER, env=env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace",
                                       timeout=300)
                    if r.returncode != 0:
                        raise RuntimeError("探针 %s 失败：%s" % (
                            tag, ((r.stdout or "") + (r.stderr or ""))[-400:]))
    plan = json.load(open(plan_fp, encoding="utf-8"))
    return h, inst, sids, (plan.get("spans") or [])


def _mj_probe_slide(doc, sids, spans, idx):
    """slide 序号 → plan span（顺序配对，同 jy 导出）。"""
    sid = sids[idx] if idx < len(sids) else None
    used = set()
    for i2, s2 in enumerate(sids):
        for sp in spans:
            if sp.get("id") == s2 and id(sp) not in used:
                used.add(id(sp))
                if i2 == idx:
                    return sp
                break
    return None


def _mj_probe(idx_req):
    """🎯 元素探针：指定卡的场景窗 → 文字元素表（文本/停留矩形/可见时段）+ 海报帧。
    编辑台「调整元素」的数据源；产物缓存 probe_cache/<hash>/probe_<idx>.json|png。"""
    mid, idx = idx_req
    fp = os.path.join(MJ_DIR, mid + ".json")
    doc = json.loads(open(fp, encoding="utf-8").read())
    h, inst, sids, spans = _mj_probe_prepare(doc, mid)
    span = _mj_probe_slide(doc, sids, spans, idx)
    if span is None:
        return {"error": "plan 里找不到该卡的场景窗口"}
    out_s = float(span.get("outputStart") or 0)
    out_e = float(span.get("outputEnd") or 0)
    pf = os.path.join(MOTION_JOBS_DIR, "probe_cache", h, "probe_%d.json" % idx)
    arg = pf.replace(".json", ".arg")
    if os.path.isfile(arg):
        try:
            os.remove(arg)
        except OSError:
            pass
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env["PATH"] = os.path.join(HERE, "工具", "ffmpeg") + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run([MOTION_NODE, "render.mjs", "--root", inst, "--out", arg,
                            "--probe-window", "%g,%g" % (out_s, out_e), "--probe-out", pf],
                           cwd=MOTION_RUNNER, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=240)
    except subprocess.TimeoutExpired:
        return {"error": "探针超时（模板场景太重），稍后再试"}
    if r.returncode != 0 or not os.path.isfile(pf):
        return {"error": "探针失败：" + ((r.stdout or "") + (r.stderr or ""))[-300:]}
    data = json.load(open(pf, encoding="utf-8"))
    return {"ok": True, "h": h,
            "poster": "/api/mj/probe_file?h=%s&f=probe_%d.png" % (h, idx),
            "poster_t": data.get("poster_t"), "scene_sci": data.get("scene_sci"),
            "elements": data.get("elements") or [], "slide": idx,
            "outwin": [out_s, out_e]}


def _motion_job(jid, name, script):
    """⚡科技风模板出片后台线程：LLM 拆稿 → plan/prepare/render/mix → 成片库。"""
    def push(**kw):
        with MLOCK:
            if jid in MJOBS:
                MJOBS[jid].update(kw)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env["PATH"] = os.path.join(HERE, "工具", "ffmpeg") + os.pathsep + env.get("PATH", "")
    try:
        # ① LLM 拆稿
        push(stage="llm", msg="编导大脑拆 B1-B4 场景…", pct=5)
        import aiwrite
        cfg = aiwrite.load_cfg()
        if not cfg.get("api_key"):
            push(status="failed", log="没配大模型 API Key（文案页 AI 写稿用的那个）")
            return
        raw = aiwrite.chat(cfg, "只输出 JSON。",
                           _MOTION_PROMPT + script[:3000], timeout=180) or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            push(status="failed", log="拆稿失败：LLM 没返回 JSON。\n" + raw[-500:])
            return
        try:
            plan_user = json.loads(m.group(0))
        except ValueError as exc:
            push(status="failed", log="拆稿 JSON 解析失败：%s\n%s" % (exc, raw[-500:]))
            return
        scenes = plan_user.get("scenes") or []
        basic = plan_user.get("basic") or {}
        if not scenes or not basic:
            push(status="failed", log="拆稿结果缺 scenes/basic：" + raw[-400:])
            return
        # 校验防线：LLM 产出不可信——字段缺失的组剔除、B2/B3 只留先出现的、超限字数截断
        req = {"B1": ["keyword", "explanation"], "B2": ["heading", "points"],
               "B3": ["heading", "left", "right", "conclusion"],
               "B4": ["summary", "action"]}
        limits = {"keyword": 8, "heading": 10, "left": 8, "right": 8, "summary": 10,
                  "explanation": 42, "conclusion": 48, "action": 42, "label": 32}
        used_heading = False
        kept, dropped = [], []
        for s in scenes:
            sid = s.get("id")
            if sid not in req:
                dropped.append(sid + "(未知组)"); continue
            need = req[sid]
            if sid in ("B2", "B3") and need[0] == "heading":
                if used_heading:
                    dropped.append(sid + "(heading重复)"); continue
            missing = [k for k in need
                       if not str(basic.get(k) or "").strip()
                       and not (k == "points" and isinstance(basic.get("points"), list)
                                and [p for p in basic["points"] if str(p).strip()])]
            if missing:
                dropped.append("%s(缺%s)" % (sid, ",".join(missing))); continue
            if sid in ("B2", "B3"):
                used_heading = True
            kept.append(s)
        if not kept:
            push(status="failed", log="拆稿场景全部无效：" + "；".join(dropped) + "\n原文案：" + raw[-300:])
            return
        for k, lim in limits.items():
            v = basic.get(k)
            if isinstance(v, str) and len(v) > lim:
                basic[k] = v[:lim]

        # 单行宽度防线（_motion_rewrap：标点处折两行，见模块级注释）
        for k, per, total in (("explanation", 19, 42), ("action", 19, 42),
                              ("conclusion", 27, 48)):
            if isinstance(basic.get(k), str):
                basic[k] = _motion_rewrap(basic[k], per, total)
        if isinstance(basic.get("points"), list):
            basic["points"] = [str(p).strip()[:16] for p in basic["points"]
                               if str(p).strip()][:5]
        if dropped:
            with MLOCK:
                if jid in MJOBS:
                    MJOBS[jid]["msg"] = "剔除无效场景：%s" % "；".join(dropped)
        dur_len = {"B1": 8, "B2": 10, "B3": 10, "B4": 8}
        duration = sum(dur_len.get(s.get("id"), 8) for s in kept)
        user = {
            "mode": "user-media", "language": "zh", "layout": "landscape",
            "duration": duration, "title": name[:30], "presenterLabel": "",
            "scenes": kept, "content": {}, "basic": basic,
            "media": {"presenter": {"mode": "none"}, "voice": {"mode": "none"},
                      "music": {"mode": "none"}},
            "soundEffects": "native", "captions": [],
        }

        # ② 准备 job 目录（全新，避免 mix 的防覆盖保护）
        ts = "%d" % int(time.time())
        job_dir = os.path.join(MOTION_JOBS_DIR, "%s_%s" % (ts, name))
        os.makedirs(job_dir, exist_ok=True)
        uj = os.path.join(job_dir, "user.json")
        with open(uj, "w", encoding="utf-8") as f:
            json.dump(user, f, ensure_ascii=False, indent=1)

        def run_step(cmd, stage, msg, pct, timeout):
            push(stage=stage, msg=msg, pct=pct)
            r = subprocess.run(cmd, cwd=MOTION_RUNNER, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            if r.returncode != 0:
                raise RuntimeError("%s 失败：%s" % (
                    stage, ((r.stdout or "") + (r.stderr or ""))[-600:]))
            return (r.stdout or "")

        # ③ plan / prepare
        run_step([MOTION_UV, "run", "adapter.py", "plan", "--input", uj,
                  "--out", os.path.join(job_dir, "plan.json")],
                 "plan", "模板排期（plan）…", 15, 180)
        inst = os.path.join(job_dir, "instance")
        run_step([MOTION_UV, "run", "adapter.py", "prepare", "--input", uj,
                  "--out", inst],
                 "prepare", "生成渲染实例（prepare）…", 30, 300)

        # ④ 渲染（node render.mjs，逐行读 {"seconds":x,"total":y} 刷进度）
        push(stage="render", msg="渲染中（60fps 逐帧）…", pct=32)
        silent = os.path.join(job_dir, "silent.mp4")
        r = subprocess.Popen(
            [MOTION_NODE, "render.mjs", "--root", inst, "--out", silent],
            cwd=MOTION_RUNNER, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in r.stdout:
            m2 = re.search(r'"seconds":\s*([\d.]+),\s*"total":\s*([\d.]+)', line)
            if m2:
                done, total = float(m2.group(1)), float(m2.group(2))
                push(pct=int(32 + 58 * done / max(total, 1)),
                     msg="渲染中 %.0f / %.0f 秒…" % (done, total))
        r.wait()
        if r.returncode != 0 or not os.path.isfile(silent):
            push(status="failed", log="渲染失败（exit %s）" % r.returncode)
            return

        # ⑤ 混音（原生科技音效）
        run_step([MOTION_UV, "run", "mix.py", "--instance", inst,
                  "--silent-video", silent],
                 "mix", "混音（原生动作音效）…", 92, 600)
        final = os.path.join(job_dir, "silent-with-audio.mp4")
        if not os.path.isfile(final):
            push(status="failed", log="混音产物缺失")
            return
        out_name = "科技风_%s.mp4" % re.sub(r"[\W]+", "", name)[:24] or "科技风_%s" % ts
        dst = os.path.join(CLIP_DIR, out_name)
        shutil.copy2(final, dst)
        push(status="done", pct=100, stage="done", msg="完成",
             out="/api/tl/seg/" + out_name,
             log="成片 %d 秒 · %d 组场景 → 成片库\\%s" % (duration, len(scenes), out_name))
    except Exception as exc:  # noqa: BLE001
        push(status="failed", log="科技风出片异常: %s" % exc)


# ---------------------------------------------------------------- 渲染后台任务
RJOBS = {}                          # id -> {status,pct,t0,out,log}
RLOCK = threading.Lock()


def _render_job(jid, name, start, end):
    """异步跑 Remotion 渲染 + BGM 混音，状态写进 RJOBS[jid]（编辑器轮询 /api/tl/render_status）。"""
    out = "seg_%d_%d.mp4" % (start, end)
    dst = os.path.join(CLIP_DIR, out)
    prog = dst + ".progress"
    try:
        if os.path.isfile(dst):
            os.remove(dst)
        if os.path.isfile(prog):
            os.remove(prog)
        env = dict(os.environ, NODE_PATH=ME_NODE_PATH)
        r = subprocess.run(
            [ME_NODE, os.path.join(ME_DIR, "render_media2.js"),
             name, dst, str(start), str(end)],
            cwd=ME_DIR, env=env, capture_output=True, text=True,
            timeout=1800)
        tail = ((r.stdout or "") + (r.stderr or ""))[-800:]
        # 读渲染器写的进度文件（RENDER OK 时已写 100）
        try:
            with open(prog, encoding="utf-8") as f:
                pct = int(f.read().strip() or 0)
        except Exception:  # noqa: BLE001
            pct = 0
        if os.path.isfile(dst) and os.path.getsize(dst) > 10000:
            with RLOCK:
                RJOBS[jid]["pct"] = 100
                RJOBS[jid]["status"] = "mixing"       # 渲染完 → 铺 BGM（视频流 copy，很快）
            bgm_note = ""
            try:
                pr = json.loads(read_text(os.path.join(ME_DIR, name)))
                bgm_name = (pr.get("bgm") or "").strip()
                if bgm_name and bgm_name != "无":
                    bp = os.path.join(BGM_DIR, os.path.basename(bgm_name))
                    if os.path.isfile(bp):
                        try:
                            bv = max(0.03, min(0.9, float(pr.get("bgmVol") or 0.12)))
                        except (TypeError, ValueError):
                            bv = 0.12
                        ok_m, msg_m = mix_bgm(dst, bp, bv, duck=pr.get("bgmDuck", True))
                        bgm_note = "；" + msg_m if ok_m else "；BGM 混音失败: " + msg_m
                    else:
                        bgm_note = "；曲库里没有「%s」，未铺 BGM" % bgm_name
            except Exception as exc:  # noqa: BLE001
                bgm_note = "；BGM 处理异常: %s" % exc
            with RLOCK:
                RJOBS[jid].update(status="done", out="/api/tl/seg/" + out,
                                  log=(tail + bgm_note)[-800:])
        else:
            with RLOCK:
                RJOBS[jid].update(status="failed", log=tail or "未知失败（无产物）")
    except Exception as exc:  # noqa: BLE001
        with RLOCK:
            RJOBS[jid].update(status="failed", log=str(exc)[-800:])
    finally:
        try:
            if os.path.isfile(prog):
                os.remove(prog)
        except OSError:
            pass
        # 任务表防膨胀：只留最近 6 个
        with RLOCK:
            if len(RJOBS) > 6:
                for k in sorted(RJOBS.keys())[:-6]:
                    RJOBS.pop(k, None)


def mix_bgm(video, bgm_path, vol, fade_in=0.8, fade_out=1.5, duck=True, voice_gain=1.0):
    """成片铺 BGM：人声可调增益，BGM 循环到底、淡入淡出、说话时自动闪避，限幅防爆音。视频流直接 copy，很快。"""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        return False, "本机没有可用的 ffmpeg（imageio-ffmpeg 缺失）：%s" % exc
    tmp = video[:-4] + "_bgm_tmp.mp4"
    fi = max(0.0, min(5.0, fade_in))
    fo = max(0.0, min(5.0, fade_out))
    vg = max(0.3, min(2.5, voice_gain))
    # 先探视频时长（imageio-ffmpeg 不带 ffprobe，解析 -i 的 stderr），用于定点淡出
    dur = 0.0
    try:
        import re as _re
        r0 = subprocess.run([ff, "-i", video], capture_output=True, timeout=60)
        m0 = _re.search(rb"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r0.stderr)
        if m0:
            dur = float(m0.group(1)) * 3600 + float(m0.group(2)) * 60 + float(m0.group(3))
    except Exception:  # noqa: BLE001
        dur = 0.0
    fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
    # BGM 链：格式对齐 → 音量 → 淡入 → 定点淡出（不能用 areverse：无限循环流会永远收不到包）
    bg = "[1:a]%s,volume=%.3f" % (fmt, vol)
    if fi > 0.05:
        bg += ",afade=t=in:st=0:d=%.2f" % fi
    if fo > 0.05 and dur > fo + 0.2:
        bg += ",afade=t=out:st=%.2f:d=%.2f" % (dur - fo, fo)
    bg += "[bgf];"
    if duck:
        # 闪避：人声作侧链，人声出现时 BGM 自动压低；normalize=0 保人声不被对半压低
        fc = ("[0:a]%s,volume=%.3f,asplit=2[voice][sc];" % (fmt, vg)
              + bg
              + "[bgf][sc]sidechaincompress=threshold=0.02:ratio=8:attack=25:release=450[bgd];"
              "[voice][bgd]amix=inputs=2:duration=first:dropout_transition=3:normalize=0,"
              "alimiter=limit=0.97,%s[a]" % fmt)
    else:
        fc = ("[0:a]%s,volume=%.3f[voice];" % (fmt, vg)
              + bg
              + "[voice][bgf]amix=inputs=2:duration=first:dropout_transition=3:normalize=0,"
              "alimiter=limit=0.97,%s[a]" % fmt)
    cmd = [ff, "-y", "-i", video, "-stream_loop", "-1", "-i", bgm_path,
           "-t", "%.3f" % dur,           # 双保险：BGM 无限循环，输出按视频时长截断
           "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            err = r.stderr.decode("utf-8", errors="replace")[-300:]
            return False, "ffmpeg 混音失败：%s" % err
        os.replace(tmp, video)
        return True, "BGM 已铺入（音量 %.2f，淡入 %.1fs，淡出 %.1fs，闪避%s，人声 %.2fx）" % (
            vol, fi, fo, "开" if duck else "关", vg)
    except Exception as exc:  # noqa: BLE001
        return False, "BGM 混音出错：%s" % exc
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def audio_gain_only(video, voice_gain):
    """无 BGM 但人声要调音量：只重编码音频轨，视频流 copy，几秒完成。"""
    if abs(voice_gain - 1.0) < 0.02:
        return True, ""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        return False, "本机没有可用的 ffmpeg（imageio-ffmpeg 缺失）：%s" % exc
    tmp = video[:-4] + "_vol_tmp.mp4"
    cmd = [ff, "-y", "-i", video, "-filter:a", "volume=%.3f" % max(0.3, min(2.5, voice_gain)),
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            return False, "调整人声音量失败"
        os.replace(tmp, video)
        return True, "人声音量已调整为 %.2fx" % voice_gain
    except Exception as exc:  # noqa: BLE001
        return False, "调整人声音量出错：%s" % exc
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---- 封面合成器（本机零 API，nana 三层版式：底色大字层 + 人物照卡层 + 角标信息层）
_COVER_W, _COVER_H = 1080, 1920


def _cover_font(size):
    from PIL import ImageFont
    for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _round_card(img, radius, deg):
    """圆角 + 轻旋转，返回带透明通道的卡片。"""
    from PIL import Image, ImageDraw
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img, (0, 0), mask)
    return out.rotate(deg, expand=True, resample=Image.BICUBIC)


def _archive_cover_photos(photos):
    """封面合成器收到的人物照自动归档进 skills/封面skill/assets/人物参考/（md5 去重）。"""
    import base64, hashlib
    dst_dir = os.path.join(SKILL_COVER, "assets", "人物参考")
    os.makedirs(dst_dir, exist_ok=True)
    seen_path = os.path.join(dst_dir, "_已存.md5")
    seen = set()
    if os.path.isfile(seen_path):
        try:
            with open(seen_path, "r", encoding="utf-8") as f:
                seen = set(ln.strip() for ln in f if ln.strip())
        except OSError:
            seen = set()
    saved = 0
    for du in (photos or [])[:3]:
        try:
            raw = base64.b64decode(du.split(",", 1)[-1])
        except Exception:
            continue
        if len(raw) < 1024:
            continue
        h = hashlib.md5(raw).hexdigest()
        if h in seen:
            continue
        ext = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        name = "人物照_%s_%d%s" % (time.strftime("%m%d-%H%M"), saved + 1, ext)
        try:
            with open(os.path.join(dst_dir, name), "wb") as f:
                f.write(raw)
            with open(seen_path, "a", encoding="utf-8") as f:
                f.write(h + "\n")
            seen.add(h)
            saved += 1
        except OSError:
            continue
    return saved


def cover_synth(payload):
    from PIL import Image, ImageDraw, ImageFilter
    import base64

    title = (payload.get("title") or "").strip() or "还没填标题"
    sub = (payload.get("sub") or "").strip()
    tag = (payload.get("tag") or "").strip()
    theme = payload.get("theme") or "dark"
    photos = payload.get("photos") or []
    try:
        _archive_cover_photos(photos)
    except Exception:
        pass  # 归档失败不挡合成

    W, H = _COVER_W, _COVER_H
    img = Image.new("RGB", (W, H), "#101426")
    d = ImageDraw.Draw(img)
    # 渐变底
    top = (16, 20, 38) if theme == "dark" else (36, 20, 12)
    bot = (27, 35, 64) if theme == "dark" else (64, 32, 16)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    # 角落氛围光斑
    glow = Image.new("RGB", (W, H), "#000000")
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 700, H - 900, W + 200, H + 100], fill="#3D2E08" if theme == "dark" else "#4A1E0A")
    glow = glow.filter(ImageFilter.GaussianBlur(180))
    img = Image.blend(img, Image.composite(glow, img, Image.new("L", (W, H), 110)), 0.55)
    d = ImageDraw.Draw(img)

    # 第一层：超大描边标题（自动换行，最多 4 行）
    tsize = 168 if len(title) <= 8 else (132 if len(title) <= 14 else 104)
    tfont = _cover_font(tsize)
    per = max(4, int(7.2 * (168 / tsize)))  # 每行字数随字号反推
    lines = [title[i:i + per] for i in range(0, len(title), per)][:4]
    x, y = 72, 150
    yellow = "#FFD400"
    for ln in lines:
        d.text((x + 10, y + 12), ln, font=tfont, fill="#000000",
               stroke_width=14, stroke_fill="#000000")
        d.text((x, y), ln, font=tfont, fill=yellow, stroke_width=14, stroke_fill="#000000")
        y += int(tsize * 1.28)
    if sub:
        sfont = _cover_font(64)
        d.text((x + 6, y + 30), sub[:18], font=sfont, fill="#FFFFFF",
               stroke_width=8, stroke_fill="#000000")

    # 第二层：人物照卡（右侧错位叠放，最多 3 张）
    cards = []
    for i, du in enumerate(photos[:3]):
        try:
            raw = base64.b64decode(du.split(",", 1)[-1])
            import io as _io
            ph = Image.open(_io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue
        cw = 620 if len(photos) == 1 else 520
        ratio = cw / ph.width
        ph = ph.resize((cw, max(1, int(ph.height * ratio))))
        if ph.height > 760:
            ph = ph.crop((0, 0, cw, 760))
        card = _round_card(ph, 36, (-4 if i % 2 == 0 else 3))
        cards.append(card)
    if cards:
        cx = W - 640 if cards else 0
        cy = H - 460 - cards[0].height
        for i, card in enumerate(cards):
            ox, oy = i * 46, i * 340
            img.paste(card, (max(24, cx + ox), min(H - card.height - 160, cy + oy)), card)
            d = ImageDraw.Draw(img)

    # 第三层：角标信息条
    if tag:
        tfont2 = _cover_font(52)
        tw = d.textlength(tag[:12], font=tfont2)
        d.rounded_rectangle([56, H - 220, 56 + tw + 76, H - 120], radius=24,
                            fill=yellow)
        d.text((94, H - 206), tag[:12], font=tfont2, fill="#111111")

    out_name = "封面_%s.png" % time.strftime("%m%d-%H%M%S")
    out_path = os.path.join(COVER_DIR, out_name)
    os.makedirs(COVER_DIR, exist_ok=True)
    img.save(out_path, "PNG")
    import io as _io2
    buf = _io2.BytesIO()
    img.save(buf, "PNG")
    dataurl = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"ok": True, "file": out_path, "name": out_name, "dataurl": dataurl}


# ---- 数字人素材配置（块②③永久素材 + Key 槽位）
def _save_dataurl(du, dst):
    import base64 as _b64
    raw = _b64.b64decode(du.split(",", 1)[-1])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(raw)
    return dst


def dh_load():
    try:
        with open(DH_SET, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def dh_safe(d):
    out = dict(d)
    for k in ("ttsKey", "dhKey", "lsKey"):
        if out.get(k):
            out[k] = out[k][:4] + "****"
    return out


def dh_state():
    d = dh_load()
    voice = os.path.join(DH_DIR, "音色参考")
    photo = os.path.join(DH_DIR, "形象照")
    st = {
        "config": dh_safe(d),
        "voice": os.path.basename(d.get("voiceFile") or "") if d.get("voiceFile") else "",
        "portraits": [],
    }
    if os.path.isdir(photo):
        st["portraits"] = sorted(f for f in os.listdir(photo)
                                 if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    return st


def cover_spec_read():
    try:
        with open(SPEC_PATH, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        txt = ""
    vers = []
    if os.path.isdir(SPEC_VER):
        vers = sorted(os.listdir(SPEC_VER), reverse=True)
    return {"spec": txt, "versions": vers,
            "updated": time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(os.path.getmtime(SPEC_PATH)))
                     if os.path.isfile(SPEC_PATH) else ""}


ANALYZE_SYSTEM = (
    "你是顶级自媒体封面视觉分析师。你只提炼抽象设计规律，"
    "绝不照搬样本中的文案、人物、Logo、角色形象、插画和高度相似版式。"
    "输出必须是可直接执行的结构化 Markdown 规范，不是宽泛建议。"
)


def cover_analyze(samples):
    """样本(5-10张 dataURL) → 两步合一：视觉规律分析 → 可执行规范，版本化写回。"""
    import aiwrite
    cfg = aiwrite.load_cfg()
    if len(samples) < 3:
        raise RuntimeError("样本太少：至少 3 张，建议 5-10 张有代表性的")
    if len(samples) > 10:
        samples = samples[:10]
    user = (
        "附件是我收集的视频封面样本（只用于分析视觉规律，禁止照搬文案/人物/Logo/"
        "品牌标识/受版权保护元素）。请逐张分析并整理成一套可直接执行的封面设计规范，"
        "Markdown 输出，结构如下：\n\n"
        "## 一、样本分析结论\n"
        "1. 主标题字数、层级、关键词突出方式\n"
        "2. 人物位置/比例/动作/表情\n"
        "3. 标题、人物、图标、辅助元素的视觉关系\n"
        "4. 配色、字体、材质、光影、背景风格\n"
        "5. 手机缩略图尺寸下的阅读顺序\n"
        "6. 哪些是可复用通用规律，哪些只是某博主个人风格\n"
        "7. 哪些元素有版权风险（肖像/Logo/水印/原创插画）\n\n"
        "## 二、稳定规则（必须遵守）\n"
        "## 三、可调变量（按视频主题灵活调）\n"
        "## 四、禁搬元素（版权红线）\n"
        "## 五、检查清单\n"
        "含：无文字测试（藏标题仍能看出主题）、缩略图测试（缩到信息流尺寸最先看到的"
        "3 个信息正确）、标题原文逐字保留、手部约束（禁巨大前景手/畸形手指）、"
        "人物安全区、竖版与横版分别的原生构图要求（禁止裁切/拉伸/扩图代替）。\n\n"
        "要求：具体到数字（标题字数上限、人物占比区间、字号层级），能直接执行。"
    )
    out = aiwrite.chat_vision(cfg, ANALYZE_SYSTEM, user, samples)
    # 版本化写回
    os.makedirs(os.path.dirname(SPEC_PATH), exist_ok=True)
    os.makedirs(SPEC_VER, exist_ok=True)
    if os.path.isfile(SPEC_PATH):
        n = len([f for f in os.listdir(SPEC_VER) if f.endswith(".md")]) + 1
        import shutil
        shutil.copy2(SPEC_PATH, os.path.join(SPEC_VER, "封面设计规范_v%d.md" % n))
    vermark = "\n\n## 版本记录\n\n- v%s 样本分析自动更新（%s，样本 %d 张）\n" % (
        time.strftime("%Y%m%d-%H%M"), time.strftime("%Y-%m-%d %H:%M"), len(samples))
    with open(SPEC_PATH, "w", encoding="utf-8") as f:
        f.write(out.rstrip() + vermark)
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AIBloggerWorkbench/1.0"

    # ---- 基础响应
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, code=200, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, download=False, ctype=None):
        if not path or not os.path.isfile(path):
            self.send_json({"error": "文件不存在", "path": path}, 404)
            return
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        name = os.path.basename(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                if start > end:
                    start, end = 0, size - 1
                else:
                    partial = True
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        disp = "attachment" if download else "inline"
        self.send_header("Content-Disposition",
                         "%s; filename*=UTF-8''%s" % (disp, urllib.parse.quote(name)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, *a):
        pass

    # ---- GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = urllib.parse.unquote(u.path)   # 解码中文路径（如 /编辑器.html）
        if p == '/api/app':
            self.send_json({'app_id': 'ai-creator-workbench', 'version': '2026.10.25-visual',
                            'port': self.server.server_address[1], 'pid': os.getpid()})
            return
        if _cut.handle(self, 'GET', p, q):
            return
        if _director.handle(self, 'GET', p, q):
            return
        if _spaces.handle(self, 'GET', p, q):
            return
        if _avt.handle(self, 'GET', p, q):
            return

        if p in ('/', '/index.html'):
            self.send_response(302)
            self.send_header('Location', '/editor2/?mode=workspace')
            self.send_header('Content-Length', '0')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        if p in ("/legacy", "/工作台.html"):
            self.send_response(302)
            self.send_header('Location', '/editor2/?mode=workspace')
            self.send_header('Content-Length', '0')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        if p == '/tools':
            if not os.path.isfile(PAGE):
                self.send_text("工作台.html 丢了，找小七要。", 500)
                return
            with open(PAGE, "rb") as f:
                body = f.read()
            body = body.replace(b'</head>', b'<style>.app>aside{display:none!important}.app{grid-template-columns:minmax(0,1fr)!important}.app>main{margin:0!important;width:100%!important;padding:24px!important}</style></head>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if p in ("/editor", "/editor.html", "/编辑器.html"):
            # 旧编辑器已退役：所有旧地址 302 到新编辑台 /editor2/（防旧书签/旧入口再进旧界面）
            self.send_response(302)
            self.send_header("Location", "/editor2/")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if p == "/editor.bundle.js":
            fp = os.path.join(HERE, "editor.bundle.js")
            if os.path.isfile(fp):
                self.send_file(fp)
            else:
                self.send_text("// bundle 缺失：到 motion-engine 跑 esbuild 重打包", 404)
            return

        # ---- 两段式：新编辑台（Vite 构建产物 editor-v2/dist）----
        if p.startswith("/mj_prev/"):
            # ▶ 实时预览：托管 probe_cache/<h>/instance 的模板页面（同源，父页可直接驱动 renderAt）
            seg = p[len("/mj_prev/"):].split("/")
            if len(seg) < 2 or not re.match(r"[0-9a-f]{16}$", seg[0]):
                self.send_text("forbidden", 403)
                return
            root = os.path.normpath(os.path.join(MOTION_JOBS_DIR, "probe_cache", seg[0], "instance"))
            rel = "/".join(seg[1:])
            fp = os.path.normpath(os.path.join(root, rel))
            if not fp.startswith(root) or not os.path.isfile(fp):
                self.send_text("not found", 404)
                return
            self.send_file(fp)
            return
        if p.startswith("/editor2"):
            root = os.path.normpath(os.path.join(HERE, "editor-v2", "dist"))
            rel = p[len("/editor2/"):].lstrip("/") if len(p) > len("/editor2/") else ""
            fp = os.path.normpath(os.path.join(root, rel)) if rel else os.path.join(root, "index.html")
            if not fp.startswith(root):
                self.send_text("forbidden", 403)
                return
            if not os.path.isfile(fp):
                fp = os.path.join(root, "index.html")
            if os.path.isfile(fp):
                self.send_file(fp)
            else:
                self.send_text("editor-v2 未构建：到 AI博主工作台/editor-v2 跑 npm run build", 404)
            return
        if p == "/api/mj/probe_file":
            # 元素探针产物（海报帧 png / 探针 json）
            h = (q.get("h") or [""])[0]
            f = (q.get("f") or [""])[0]
            if not re.match(r"[0-9a-f]{16}$", h or "") or not re.match(r"probe_\d+\.(png|json)$", f or ""):
                self.send_json({"error": "参数不合法"}, 400)
                return
            fp = os.path.join(MOTION_JOBS_DIR, "probe_cache", h, f)
            if not os.path.isfile(fp):
                self.send_json({"error": "探针产物不存在（重新点「调整元素」会再生成）"}, 404)
                return
            self.send_file(fp)
            return
        if p == "/api/vf/list":
            self.send_json({"items": _vf.list_bases()})
            return
        if p == "/api/vf/voices":
            self.send_json({"items": [{"id": v, "name": n} for v, n in ttsfree.RECOMMEND]})
            return
        if p == "/api/vf/file":
            bid = os.path.basename((q.get("b") or [""])[0])
            f = (q.get("f") or [""])[0]
            if f not in ("底片.mp4", "audio.wav", "cues.cue", "meta.json"):
                self.send_json({"error": "文件名不合法"}, 400)
                return
            fp = os.path.join(_vf.BASE_DIR, bid, f)
            if not os.path.isfile(fp):
                self.send_json({"error": "文件不存在"}, 404)
                return
            self.send_file(fp)
            return
        if p == "/api/mj/registry":
            self.send_json({"schema": _mj.SCHEMA_VERSION, "items": _mj.load_registry()})
            return
        if p == "/api/mj/list":
            items = []
            for fp in sorted(glob.glob(os.path.join(MJ_DIR, "*.json")), reverse=True):
                try:
                    d = json.load(open(fp, encoding="utf-8"))
                    items.append({"id": os.path.basename(fp)[:-5],
                                  "base": (d.get("global") or {}).get("source", {}).get("base", ""),
                                  "slides": len(d.get("slides") or []),
                                  "mtime": os.path.getmtime(fp)})
                except Exception:  # noqa: BLE001
                    pass
            self.send_json({"items": items})
            return
        if p == "/api/mj/load":
            mid = os.path.basename((q.get("id") or [""])[0])
            fp = os.path.join(MJ_DIR, mid + ".json")
            if not os.path.isfile(fp):
                self.send_json({"error": "工程不存在"}, 404)
                return
            self.send_json({"id": mid, "doc": json.load(open(fp, encoding="utf-8"))})
            return

        if p == "/echarts.min.js":
            fp = os.path.join(HERE, "echarts.min.js")
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_text("// echarts.min.js 缺失", 404)
            return

        if p == "/api/topics":
            self.send_json({"intel": parse_intel(), "candidates": parse_candidates(),
                            "src": TOPIC_OVERVIEW})
            return

        if p == "/api/src/get":
            txt = src_file_read()
            urls, skip_ai = load_custom_sources()
            self.send_json({"text": txt, "urls": urls, "skip_ai": skip_ai,
                            "count": len(urls)})
            return

        if p == "/api/hooks":
            self.send_json({"groups": parse_hooks()})
            return

        if p == "/api/scripts":
            items = []
            for f in sorted(glob.glob(os.path.join(SCRIPTS_DIR, "*.md")),
                            key=os.path.getmtime, reverse=True):
                try:
                    items.append({"name": os.path.basename(f),
                                  "path": f,
                                  "size": os.path.getsize(f),
                                  "mtime": os.path.getmtime(f)})
                except OSError:
                    pass
            self.send_json({"items": items, "dir": SCRIPTS_DIR})
            return

        if p == "/api/script":
            path = (q.get("path", [""])[0] or "")
            if not path:
                name = q.get("name", [""])[0]
                path = os.path.join(SCRIPTS_DIR, name) if name else ""
            # 只允许读 scripts/ 或 工作区
            rp = os.path.abspath(path)
            if not (rp.startswith(os.path.abspath(SCRIPTS_DIR)) or rp.startswith(HERE)):
                self.send_json({"error": "路径不允许"}, 403)
                return
            self.send_json({"path": rp, "text": read_text(rp)})
            return

        if p == "/api/publish":
            title = q.get("title", [""])[0]
            self.send_json(build_publish(title))
            return

        if p == "/api/reviews":
            data = {}
            if os.path.isfile(REVIEW_JSON):
                try:
                    data = json.loads(read_text(REVIEW_JSON) or "{}")
                except Exception:  # noqa: BLE001
                    data = {}
            preds = []
            for f in sorted(glob.glob(os.path.join(PRED_DIR, "*.md")),
                            key=os.path.getmtime, reverse=True):
                preds.append({"name": os.path.basename(f), "path": f,
                              "mtime": os.path.getmtime(f)})
            self.send_json({"predictions": preds, "records": data})
            return

        if p == "/api/overview":
            self.send_json(self._overview_payload())
            return

        # ---- 对标库 / 技能库
        if p == "/api/bench/list":
            self.send_json({"items": list_bench(), "dir": BENCH_DIR})
            return
        if p == "/api/bench/get":
            name = q.get("name", [""])[0]
            path = (q.get("path", [""])[0] or "") or (os.path.join(BENCH_DIR, name) if name else "")
            rp = os.path.abspath(path)
            if not rp.startswith(os.path.abspath(BENCH_DIR)):
                self.send_json({"error": "路径不允许"}, 403); return
            self.send_json({"path": rp, "name": os.path.basename(rp), "text": read_text(rp)})
            return
        if p == "/api/skills":
            self.send_json({"items": list_skills(), "dir": SKILLS_DIR})
            return

        # ---- Remotion 剪辑轨道（motion-engine）
        if p == "/api/tl/list":
            fs = []
            if os.path.isdir(ME_DIR):
                fs = sorted(f for f in os.listdir(ME_DIR)
                            if re.match(r"props[\w-]*\.json$", f))
            self.send_json({"items": fs, "dir": ME_DIR})
            return
        if p == "/api/tl/props":
            name = os.path.basename(q.get("p", [""])[0] or "props.json")
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            fp = os.path.join(ME_DIR, name)
            if not os.path.isfile(fp):
                self.send_json({"error": "不存在 " + name}, 404); return
            self.send_json(json.loads(read_text(fp)))
            return
        if p == "/api/tl/beats":
            fp = os.path.join(ME_DIR, "beats.json")
            self.send_json(json.loads(read_text(fp)) if os.path.isfile(fp) else {})
            return
        if p == "/api/tl/sfx":
            fp = os.path.join(ME_DIR, "sfx_timeline.json")
            self.send_json(json.loads(read_text(fp)) if os.path.isfile(fp) else {})
            return
        if p == "/api/studio/status":
            self.send_json({"running": studio_alive()})
            return
        if p == "/api/studio/start":
            name = os.path.basename(q.get("p", [""])[0] or "props_gold.json")
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            if studio_alive():
                self.send_json({"ok": True, "already": True}); return
            ok, msg = studio_start(name)
            self.send_json({"ok": ok, "msg": msg})
            return
        if p == "/api/tl/export_subs":
            # pysubs2：props 轨道的字幕句 → SRT/ASS 文件（可导入剪映/PR，或喂 reburn 烧录）
            name = os.path.basename(q.get("p", [""])[0] or "props.json")
            fmt = q.get("fmt", ["srt"])[0]
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            if fmt not in ("srt", "ass"):
                self.send_json({"error": "格式只支持 srt/ass"}, 400); return
            fp = os.path.join(ME_DIR, name)
            if not os.path.isfile(fp):
                self.send_json({"error": "不存在 " + name}, 404); return
            try:
                import pysubs2
            except ImportError:
                self.send_json({"error": "pysubs2 未安装（venv 里 pip install pysubs2）"}, 500)
                return
            data = json.loads(read_text(fp))
            subs = pysubs2.SSAFile()
            st = pysubs2.SSAStyle()
            st.fontname = "Microsoft YaHei"
            st.fontsize = 56
            st.bold = True
            st.primarycolor = pysubs2.Color(r=255, g=255, b=255)
            st.outlinecolor = pysubs2.Color(r=0, g=0, b=0)
            st.outline = 2.5
            st.shadow = 1.0
            st.alignment = 2
            subs.styles["Default"] = st
            for ln in (data.get("subs") or data.get("lines", [])):
                try:
                    ev = pysubs2.SSAEvent(start=int(round(ln["s"] * 1000)),
                                          end=int(round(ln["e"] * 1000)),
                                          text=str(ln.get("text", "")).replace("\n", " "))
                    subs.events.append(ev)
                except (KeyError, TypeError, ValueError):
                    continue
            out = name[:-5] + "." + fmt
            dst = os.path.join(ME_DIR, out)
            subs.save(dst)
            self.send_file(dst, download=True)
            return
        if p == "/api/tl/videos":
            # 轨道播放器可选的视频源：当前工程初片 / 缓存初片成片 / 输出成片
            cands = []
            pub = os.path.join(ME_DIR, "public")
            if os.path.isdir(pub):
                for f in sorted(os.listdir(pub)):
                    if f.endswith(".mp4"):
                        cands.append({"name": "当前工程初片 (public/" + f + ")",
                                      "path": os.path.join(pub, f)})
            for d in (os.path.join(WORK, "缓存"), os.path.join(WORK, "输出")):
                if os.path.isdir(d):
                    for f in sorted(os.listdir(d)):
                        if f.endswith(".mp4") and not f.startswith("_"):
                            cands.append({"name": f, "path": os.path.join(d, f)})
            for i, c in enumerate(cands):
                c["i"] = i
            self.send_json({"items": cands})
            return
        if p == "/api/tl/video":
            try:
                vi = int(q.get("i", ["0"])[0])
            except ValueError:
                vi = 0
            pub = os.path.join(ME_DIR, "public")
            pub_files = sorted(f for f in os.listdir(pub)
                               if f.endswith(".mp4")) if os.path.isdir(pub) else []
            cands = [os.path.join(pub, f) for f in pub_files]
            for d in (WORK + os.sep + "缓存", WORK + os.sep + "输出"):
                if os.path.isdir(d):
                    cands += [os.path.join(d, f) for f in sorted(os.listdir(d))
                              if f.endswith(".mp4") and not f.startswith("_")]
            fp = cands[vi] if 0 <= vi < len(cands) else None
            if not fp or not os.path.isfile(fp):
                self.send_json({"error": "视频源不存在"}, 404)
                return
            self.send_file(fp)
            return
        if p.startswith("/api/tl/asset/"):
            name = os.path.basename(u.path[len("/api/tl/asset/"):])
            fp = None
            if re.match(r"[\w-]+\.(png|jpg|jpeg|webp|mp4)$", name):
                # 图片在 public/；mp4 依次找 public/缓存/输出（视频源候选在这三处）
                for d in (os.path.join(ME_DIR, "public"),
                          os.path.join(WORK, "缓存"), os.path.join(WORK, "输出")):
                    c = os.path.join(d, name)
                    if os.path.isfile(c):
                        fp = c
                        break
            if fp:
                self.send_file(fp)
            else:
                self.send_json({"error": "素材不存在"}, 404)
            return
        if p.startswith("/api/tl/seg/"):
            name = os.path.basename(u.path[len("/api/tl/seg/"):])
            fp = os.path.join(CLIP_DIR, name)
            if not os.path.isfile(fp):  # 兼容老产物（曾存在 motion-engine/）
                fp = os.path.join(ME_DIR, name)
            if re.match(r"seg_\d+_\d+\.mp4$", name) and os.path.isfile(fp):
                self.send_file(fp)
            else:
                self.send_json({"error": "段文件不存在"}, 404)
            return

        # ---- 对标浏览器 / AI 写稿
        if p == "/api/wa/state":
            with JOB_LOCK:
                last = dict(WA_LAST)
            self.send_json({"alive": wa_alive(), "port": WA_PORT,
                            "keepalive": bool(_WA_KEEP["on"]),
                            "last_kind": last.get("kind", ""),
                            "last_ok": bool((last.get("result") or {}).get("ok")),
                            "result_dir": WA_RESULT_DIR})
            return
        if p == "/api/wa/last":
            kind = (q.get("kind", ["search"])[0] or "search")
            with JOB_LOCK:
                cached = WA_LAST.get("result") if WA_LAST.get("kind") == kind else None
            self.send_json(cached or latest_wa(kind) or {})
            return
        if p == "/api/research/last":
            self.api_research_last(); return
        if p == "/api/wa/check":
            self.api_wa_check(); return
        if p == "/api/wa/loginstate":
            self.send_json(wa_login_state(q.get("platform", ["douyin"])[0])); return
        if p == "/api/wa/close":
            self.api_wa_close(); return
        if p == "/api/cache/info":
            self.send_json(cache_info()); return
        if p == "/api/ai/config":
            self.send_json(ai_cfg_safe())
            return
        if p == "/api/position":
            self.send_json({"path": POSITION, "md": position_md()})
            return
        if p == "/api/fresh/last":
            self.send_json(fresh_last())
            return
        if p == "/api/ab/last":
            self.send_json(ab_last(q.get("target", ["A"])[0].upper()))
            return
        if p == "/api/bgm/list":
            self.send_json({"tracks": bgm_list()})
            return
        if p == "/api/dh/state":
            self.send_json(dh_state())
            return
        if p == "/api/cover/spec":
            self.send_json(cover_spec_read())
            return
        if p == "/api/cover/list":
            items = []
            try:
                fs = [os.path.join(COVER_DIR, f) for f in os.listdir(COVER_DIR)
                      if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
                fs.sort(key=os.path.getmtime, reverse=True)
                for fp in fs[:12]:
                    items.append({"name": os.path.basename(fp),
                                  "mtime": int(os.path.getmtime(fp)),
                                  "url": "/api/video?path=" + urllib.parse.quote(fp)})
            except OSError:
                pass
            self.send_json({"covers": items})
            return
        if p == "/api/leyia/state":
            self.api_leyia_state(); return
        if p == "/api/leyia/job":
            with LEY_LOCK:
                snap = dict(LEY)
                snap["lines"] = list(LEY["lines"])
            self.send_json(snap); return
        if p == "/api/adu/status":
            self.send_json(_adupack().adu_status()); return
        if p == "/api/adu/styles":
            self.send_json({"styles": _adupack().styles(),
                            "released": _adupack().RELEASED_STYLES}); return
        if p == "/api/adu/videos":
            self.send_json({"videos": _adupack().videos(),
                            "bgm": _adupack().bgm_tracks()}); return
        if p == "/api/tts/status":
            self.send_json(_ttsfree().status()); return
        if p == "/api/tts/voices":
            self.send_json({"voices": _ttsfree().voices()}); return
        if p == "/api/leyia/file":
            fp = (q.get("p", [""])[0] or "").strip()
            real = os.path.realpath(fp)
            if not real.startswith(os.path.realpath(LEYIA_OUT) + os.sep):
                self.send_json({"error": "路径不合法"}, 403); return
            self.send_file(real); return

        # ---- 剪辑环节
        if p == "/api/state":
            with JOB_LOCK:
                j = dict(JOB)
            j.pop("lines", None)
            j.pop("proc", None)
            j["line_count"] = len(JOB["lines"])
            j["in_dir"] = IN_DIR
            j["out_dir"] = OUT_DIR
            self.send_json(j)
            return
        if p == "/api/log":
            try:
                frm = int(q.get("from", ["0"])[0])
            except ValueError:
                frm = 0
            with JOB_LOCK:
                lines = JOB["lines"][frm:]
                total = len(JOB["lines"])
                snap = {k: JOB[k] for k in
                        ("id", "mode", "running", "done", "ok", "elapsed",
                         "src_name", "out_path", "out_size", "rev")}
            snap["lines"] = lines
            snap["from"] = frm
            snap["total"] = total
            snap["elapsed"] = (time.time() - JOB["started"]) if JOB["running"] else JOB["elapsed"]
            self.send_json(snap)
            return
        if p == "/api/recent":
            items = []
            try:
                for f in os.listdir(IN_DIR):
                    fp = os.path.join(IN_DIR, f)
                    if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in VIDEO_EXT:
                        items.append({"name": f, "path": fp,
                                      "size": os.path.getsize(fp),
                                      "size_text": human(os.path.getsize(fp)),
                                      "mtime": os.path.getmtime(fp)})
            except OSError:
                pass
            items.sort(key=lambda x: -x["mtime"])
            self.send_json({"items": items[:12]})
            return
        if p == "/api/outputs":
            items = []
            try:
                for f in os.listdir(OUT_DIR):
                    fp = os.path.join(OUT_DIR, f)
                    if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in VIDEO_EXT:
                        items.append({"name": f, "path": fp,
                                      "size": os.path.getsize(fp),
                                      "size_text": human(os.path.getsize(fp)),
                                      "mtime": os.path.getmtime(fp)})
            except OSError:
                pass
            items.sort(key=lambda x: -x["mtime"])
            self.send_json({"items": items[:12]})
            return
        if p == "/api/video":
            which = (q.get("which", ["out"])[0] or "out")
            dl = q.get("dl", ["0"])[0] == "1"
            path = (q.get("path", [""])[0] or "")
            if path and os.path.isfile(path) and path.startswith(HERE):
                self.send_file(path, download=dl)
                return
            target = {"in": JOB.get("src_path", ""), "draft": JOB.get("draft_path", ""),
                      "out": JOB.get("out_path", "")}.get(which, "")
            self.send_file(target, download=dl)
            return

        self.send_json({"error": "not found"}, 404)

    # ---- POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if _cut.handle(self, 'POST', p, urllib.parse.parse_qs(u.query)):
            return
        if _director.handle(self, 'POST', p, urllib.parse.parse_qs(u.query)):
            return
        if _spaces.handle(self, 'POST', p, urllib.parse.parse_qs(u.query)):
            return
        if _avt.handle(self, 'POST', p, urllib.parse.parse_qs(u.query)):
            return
        # ---- Remotion 轨道：保存 props / 只渲染该段
        if p == "/api/tl/save":
            b = self.read_json()
            name = os.path.basename(str(b.get("p") or "props.json"))
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            fp = os.path.join(ME_DIR, name)
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(b.get("props"), f, ensure_ascii=False, indent=1)
            self.send_json({"ok": True, "path": fp})
            return
        if p == "/api/tl/gen_image":
            # 大模型生图替代 ComfyUI：智谱 CogView 出 PPT 页背景图 → 落 public/ → 写回 props
            b = self.read_json()
            prompt = str(b.get("prompt") or "").strip()
            if not prompt:
                self.send_json({"error": "描述词不能为空"}, 400); return
            key = str(b.get("key") or "").strip()
            if not key:
                try:
                    import aiwrite
                    cfg = aiwrite.load_cfg()
                    # Key 链：模型设置里的 zhipu_key（独立字段）> preset 为智谱时的主 Key
                    key = (cfg.get("zhipu_key") or "").strip()
                    if not key and cfg.get("preset") == "zhipu":
                        key = (cfg.get("api_key") or "").strip()
                except Exception:  # noqa: BLE001
                    key = ""
            if not key:
                self.send_json({"ok": False, "needs_key": True,
                                "error": "没有智谱 Key——模型设置选「智谱 GLM」并填 Key，或本次直接输入"}, 400)
                return
            size = str(b.get("size") or "1344x768")
            req = urllib.request.Request(
                "https://open.bigmodel.cn/api/paas/v4/images/generations",
                data=json.dumps({"model": "cogview-3-flash", "prompt": prompt,
                                 "size": size}).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8"))
                img_url = (resp.get("data") or [{}])[0].get("url", "")
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": "生图 API 失败：%s" % exc}, 502)
                return
            if not img_url:
                self.send_json({"ok": False, "error": "生图 API 未返回图片", "raw": str(resp)[:300]}, 502)
                return
            pub = os.path.join(ME_DIR, "public")
            os.makedirs(pub, exist_ok=True)
            fname = "bg_%s_%d.png" % (os.path.basename(str(b.get("p") or "props"))[:20], int(time.time()))
            dst = os.path.join(pub, fname)
            urllib.request.urlretrieve(img_url, dst)
            if not os.path.isfile(dst) or os.path.getsize(dst) < 5000:
                self.send_json({"ok": False, "error": "图片下载失败"}, 502)
                return
            # 可选：直接挂到 props 的 slide.bg
            props_name = str(b.get("p") or "").strip()
            idx = b.get("slide_idx")
            saved = None
            if re.match(r"props[\w-]*\.json$", props_name) and isinstance(idx, int):
                pf = os.path.join(ME_DIR, props_name)
                if os.path.isfile(pf):
                    data = json.loads(read_text(pf))
                    slides = data.get("slides") or []
                    if 0 <= idx < len(slides):
                        slides[idx]["bg"] = fname
                        with open(pf, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=1)
                        saved = props_name
            self.send_json({"ok": True, "bg": fname, "asset": "/api/tl/asset/" + fname,
                            "saved_to": saved})
            return
        if p == "/api/tl/save_key":
            # 把智谱 Key 存进模型设置.json（zhipu_key 独立字段），全端复用不用重复填
            b = self.read_json()
            key = str(b.get("key") or "").strip()
            if not key:
                self.send_json({"error": "key 为空"}, 400); return
            cfgf = os.path.join(WORK, "模型设置.json")
            data = {}
            if os.path.isfile(cfgf):
                try:
                    data = json.loads(read_text(cfgf) or "{}")
                except Exception:  # noqa: BLE001
                    data = {}
            data["zhipu_key"] = key
            with open(cfgf, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            self.send_json({"ok": True})
            return
        if p == "/api/tl/export_jy":
            # props → 剪映草稿（细调终站：打开剪映即见，可随心改后从剪映导出）
            b = self.read_json()
            name = os.path.basename(str(b.get("p") or "props.json"))
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            fp = os.path.join(ME_DIR, name)
            if not os.path.isfile(fp):
                self.send_json({"error": "不存在 " + name}, 404); return
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(ME_DIR, "export_jianying.py"), fp],
                    capture_output=True, text=True, timeout=120)
                tail = ((r.stdout or "") + (r.stderr or ""))[-400:]
                self.send_json({"ok": "✔" in (r.stdout or ""), "log": tail})
            except Exception as e:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(e)})
            return
        if p == "/api/tl/new_props":
            # 换新片：选视频源 → 探测分辨率/帧率/时长 → 拷入 public/ → 生成空白 props 工程
            b = self.read_json()
            out, meta, err = new_props_core(str(b.get("name") or "").strip(), b.get("i", 0))
            if err:
                self.send_json({"error": err}, 400)
                return
            self.send_json({"ok": True, "p": out, "meta": meta})
            return
        if p == "/api/tl/srt_orchestrate":
            # SRT 转动效编排：导入字幕时间轴 → 编导大脑(LLM) 重排章节/贴片/标签/音效
            b = self.read_json()
            name = os.path.basename(str(b.get("p") or "props.json"))
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            okc, tail, nlines, ncues = orchestrate_srt(name, str(b.get("srt") or ""))
            self.send_json({"ok": bool(okc), "cues": ncues, "lines": nlines, "log": tail})
            return
        if p == "/api/tl/auto_pipeline":
            # 🤖 自动剪辑一条龙：选视频 → 转写 → LLM编排 → 错字校正 → 分镜摘要
            b = self.read_json()
            nm = str(b.get("name") or "").strip()
            if not re.match(r"[\w-]{1,40}$", nm):
                self.send_json({"error": "工程名只允许中英文/数字/_-，40 字以内"}, 400); return
            with AUTO_LOCK:
                running = [j for j in AUTO_JOBS.values() if j.get("status") == "running"]
            if running:
                self.send_json({"error": "已有自动剪辑在跑，等它完成再来。"}, 409); return
            jid = "%d" % int(time.time() * 1000)
            with AUTO_LOCK:
                AUTO_JOBS[jid] = {"status": "running", "stage": "probe", "pct": 0,
                                  "t0": time.time(), "msg": "排队中…", "log": "",
                                  "p": None, "summary": None}
            threading.Thread(target=_auto_pipeline_job, args=(jid, nm, b.get("i", 0)),
                             daemon=True).start()
            self.send_json({"ok": True, "id": jid})
            return
        if p == "/api/tl/auto_status":
            b = self.read_json()
            jid = str(b.get("id") or "")
            with AUTO_LOCK:
                job = dict(AUTO_JOBS.get(jid) or {})
            if not job:
                self.send_json({"error": "任务不存在"}, 404); return
            job["elapsed"] = time.time() - job.get("t0", time.time())
            self.send_json(job)
            return
        if p == "/api/tl/motion_render":
            # ⚡科技风模板整片出片：文案 → LLM 拆 B1-B4 → 模板渲染 → 成片库
            b = self.read_json()
            name = str(b.get("name") or "").strip() or "未命名"
            script = str(b.get("script") or "").strip()
            if len(script) < 10:
                self.send_json({"error": "文案太短（至少 10 字）"}, 400); return
            if not re.match(r"[\w-]{1,40}$", name):
                self.send_json({"error": "名字只允许中英文/数字/_-"}, 400); return
            if not os.path.isfile(os.path.join(MOTION_RUNNER, "adapter.py")):
                self.send_json({"error": "科技风模板 runner 不存在（adu-motion-standalone\\runner）"}, 404); return
            with MLOCK:
                running = [j for j in MJOBS.values() if j.get("status") == "running"]
            if running:
                self.send_json({"error": "已有科技风出片在跑，等它完成再来。"}, 409); return
            jid = "%d" % int(time.time() * 1000)
            with MLOCK:
                MJOBS[jid] = {"status": "running", "stage": "llm", "pct": 0,
                              "t0": time.time(), "msg": "排队中…", "log": "", "out": None}
            threading.Thread(target=_motion_job, args=(jid, name, script), daemon=True).start()
            self.send_json({"ok": True, "id": jid})
            return
        if p == "/api/tl/motion_status":
            b = self.read_json()
            jid = str(b.get("id") or "")
            with MLOCK:
                job = dict(MJOBS.get(jid) or {})
            if not job:
                self.send_json({"error": "任务不存在"}, 404); return
            job["elapsed"] = time.time() - job.get("t0", time.time())
            self.send_json(job)
            return

        # ---- 两段式：底片工厂（模式一）+ 动效编排 ----
        if p == "/api/vf/start":
            b = self.read_json()
            text = str(b.get("text") or "").strip()
            if len(text) < 20:
                self.send_json({"error": "文案太短（至少 20 字）"}, 400)
                return
            try:
                speed = max(0.7, min(1.4, float(b.get("speed") or 1.0)))
            except (TypeError, ValueError):
                speed = 1.0
            r = _vf.start(text,
                          voice=str(b.get("voice") or "zm_009"),
                          speed=speed,
                          aspect=str(b.get("aspect") or "16:9"),
                          gap_policy=str(b.get("gap_policy") or "标准"))
            self.send_json(r)
            return
        if p == "/api/vf/status":
            b = self.read_json()
            self.send_json(_vf.status(str(b.get("id") or "")))
            return
        if p == "/api/mj/orchestrate":
            b = self.read_json()
            base_id = os.path.basename(str(b.get("base_id") or ""))
            meta = _vf.load_meta(base_id)
            if not meta:
                self.send_json({"error": "底片不存在：%s" % base_id}, 404)
                return
            cues = _vf.load_cues(base_id)
            if not cues:
                self.send_json({"error": "底片没有时间轴（cues.cue 空）"}, 400)
                return
            tpl = str(b.get("template_id") or "auto")
            jid = str(int(time.time() * 1000))
            with MJ2_LOCK:
                MJ2_JOBS[jid] = {"running": True, "ok": False, "t0": time.time(),
                                 "stage": "读稿编排中", "log": []}

            def _mj2_job():
                try:
                    doc, log = _brain.orchestrate(cues, meta, tpl)
                    mid = "mj_" + jid
                    (doc.setdefault("global", {}).setdefault("source", {})
                        .update({"base": base_id, "duration": meta.get("duration")}))
                    doc["global"]["canvas"] = {"w": meta.get("w", 1920),
                                               "h": meta.get("h", 1080),
                                               "fps": meta.get("fps", 25)}
                    with open(os.path.join(MJ_DIR, mid + ".json"), "w", encoding="utf-8") as f:
                        json.dump(doc, f, ensure_ascii=False, indent=1)
                    with MJ2_LOCK:
                        MJ2_JOBS[jid].update({"running": False, "ok": True, "mid": mid,
                                              "log": log,
                                              "slides": len(doc.get("slides") or [])})
                except Exception as exc:  # noqa: BLE001
                    with MJ2_LOCK:
                        MJ2_JOBS[jid].update({"running": False, "ok": False,
                                              "error": str(exc), "log": [str(exc)]})
            threading.Thread(target=_mj2_job, daemon=True).start()
            self.send_json({"ok": True, "id": jid})
            return
        if p == "/api/mj/status":
            b = self.read_json()
            jid = str(b.get("id") or "")
            with MJ2_LOCK:
                job = dict(MJ2_JOBS.get(jid) or {})
            if not job:
                self.send_json({"error": "任务不存在"}, 404)
                return
            job["elapsed"] = round(time.time() - job.get("t0", time.time()), 1)
            job.pop("t0", None)
            self.send_json(job)
            return
        if p == "/api/mj/save":
            b = self.read_json()
            mid = os.path.basename(str(b.get("id") or ""))
            if not re.match(r"mj_[\w-]{6,40}$", mid):
                self.send_json({"error": "工程 id 不合法"}, 400)
                return
            doc = b.get("doc")
            if not isinstance(doc, dict):
                self.send_json({"error": "doc 缺失"}, 400)
                return
            doc = _mj.sanitize_time(doc)
            doc = _mj.clamp_fields(doc)
            errs = _mj.validate(doc)
            if errs and not b.get("force"):
                self.send_json({"ok": False, "errors": errs[:8]})
                return
            fp = os.path.join(MJ_DIR, mid + ".json")
            if os.path.isfile(fp):
                shutil.copy2(fp, fp + ".bak")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=1)
            self.send_json({"ok": True, "warnings": errs[:5]})
            return
        if p == "/api/mj/render":
            # 🎬 导出成片：动效 JSON → 模板渲染 → 成片库（异步，进度 /api/mj/render_status）
            b = self.read_json()
            mid = os.path.basename(str(b.get("id") or ""))
            if not re.match(r"mj_[\w-]{6,40}$", mid):
                self.send_json({"error": "工程 id 不合法"}, 400)
                return
            if not os.path.isfile(os.path.join(MJ_DIR, mid + ".json")):
                self.send_json({"error": "动效工程不存在（先保存）"}, 404)
                return
            with MLOCK:
                running = [j for j in MJOBS.values() if j.get("status") == "running"]
            if running:
                self.send_json({"error": "已有导出任务在跑，等它完成再来。"}, 409)
                return
            jid = "%d" % int(time.time() * 1000)
            with MLOCK:
                MJOBS[jid] = {"status": "running", "stage": "route", "pct": 0,
                              "t0": time.time(), "msg": "排队中…", "log": "", "out": None}
            threading.Thread(target=_mj_render_job, args=(jid, mid), daemon=True).start()
            self.send_json({"ok": True, "id": jid})
            return
        if p == "/api/mj/render_status":
            b = self.read_json()
            jid = str(b.get("id") or "")
            with MLOCK:
                job = dict(MJOBS.get(jid) or {})
            if not job:
                self.send_json({"error": "任务不存在"}, 404)
                return
            job["elapsed"] = round(time.time() - job.get("t0", time.time()), 1)
            job.pop("t0", None)
            self.send_json(job)
            return
        if p == "/api/mj/jy_export":
            # 🧩 导出剪映草稿（解除合并）：无字背景层 + 文字事件表 → 剪映草稿
            b = self.read_json()
            mid = os.path.basename(str(b.get("id") or ""))
            if not re.match(r"mj_[\w-]{6,40}$", mid):
                self.send_json({"error": "工程 id 不合法"}, 400)
                return
            if not os.path.isfile(os.path.join(MJ_DIR, mid + ".json")):
                self.send_json({"error": "动效工程不存在（先保存）"}, 404)
                return
            with JYLOCK:
                running = [j for j in JYJOBS.values() if j.get("status") == "running"]
            if running:
                self.send_json({"error": "已有剪映草稿导出在跑，等它完成再来。"}, 409)
                return
            jid = "%d" % int(time.time() * 1000)
            with JYLOCK:
                JYJOBS[jid] = {"status": "running", "stage": "route", "pct": 0,
                               "t0": time.time(), "msg": "排队中…", "log": "", "out": None}
            threading.Thread(target=_mj_jy_job, args=(jid, mid), daemon=True).start()
            self.send_json({"ok": True, "id": jid})
            return
        if p == "/api/mj/jy_status":
            b = self.read_json()
            jid = str(b.get("id") or "")
            with JYLOCK:
                job = dict(JYJOBS.get(jid) or {})
            if not job:
                self.send_json({"error": "任务不存在"}, 404)
                return
            job["elapsed"] = round(time.time() - job.get("t0", time.time()), 1)
            job.pop("t0", None)
            self.send_json(job)
            return
        if p == "/api/mj/preview_start":
            # ▶ 实时预览：准备模板实例（同探针缓存）→ 返回模板页地址+场景窗（画布 iframe 跟播放头跑）
            b = self.read_json()
            mid = os.path.basename(str(b.get("id") or ""))
            if not re.match(r"mj_[\w-]{6,40}$", mid):
                self.send_json({"error": "工程 id 不合法"}, 400)
                return
            if not os.path.isfile(os.path.join(MJ_DIR, mid + ".json")):
                self.send_json({"error": "动效工程不存在（先保存）"}, 404)
                return
            try:
                doc = json.loads(open(os.path.join(MJ_DIR, mid + ".json"), encoding="utf-8").read())
                khit = _kit_doc_hit(doc)
                if khit:
                    self.send_json({"error": "《%s》是 MotionGit 官方管线模板，不支持实时预览，"
                                             "请直接「🎬 导出成片」" % khit["name"]}, 400)
                    return
                h, inst, sids, spans = _mj_probe_prepare(doc, mid)
            except RuntimeError as exc:
                self.send_json({"error": str(exc)}, 500)
                return
            self.send_json({"ok": True, "h": h,
                            "url": "/mj_prev/%s/index.html" % h,
                            "duration": (doc.get("global") or {}).get("duration") or None,
                            "spans": [{"out_s": float(sp.get("outputStart") or 0),
                                       "out_e": float(sp.get("outputEnd") or 0)} for sp in spans]})
            return
        if p == "/api/mj/probe":
            # 🎯 元素探针：卡窗 → 文字元素表 + 海报帧（编辑台「调整元素」数据源）
            b = self.read_json()
            mid = os.path.basename(str(b.get("id") or ""))
            if not re.match(r"mj_[\w-]{6,40}$", mid):
                self.send_json({"error": "工程 id 不合法"}, 400)
                return
            if not os.path.isfile(os.path.join(MJ_DIR, mid + ".json")):
                self.send_json({"error": "动效工程不存在（先保存）"}, 404)
                return
            try:
                idx = int(b.get("slide", 0))
            except (TypeError, ValueError):
                idx = 0
            try:
                doc0 = json.loads(open(os.path.join(MJ_DIR, mid + ".json"), encoding="utf-8").read())
                khit = _kit_doc_hit(doc0)
            except Exception:  # noqa: BLE001
                khit = None
            if khit:
                self.send_json({"ok": False, "error": "官方管线模板不支持元素探针（渲染器不同）"}, 500)
                return
            d = _mj_probe((mid, idx))
            self.send_json(d, 200 if d.get("ok") else 500)
            return
        if p == "/api/tpl/import":
            # 📦 导入模板：上传 .motion 包（zip）→ 解包 → 解析 schema/adapter 生成 manifest → 进注册表
            uq = urllib.parse.urlparse(self.path).query
            q = urllib.parse.parse_qs(uq)
            fn = urllib.parse.unquote((q.get("name") or [""])[0])
            fn = os.path.basename(fn.replace("\\", "/"))
            if not fn.lower().endswith((".motion", ".zip")):
                self.send_json({"error": "只支持 .motion / .zip 模板包"}, 400)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if not (1024 < length < 200 * 1024 * 1024):
                self.send_json({"error": "包大小不对（1KB~200MB）"}, 400)
                return
            data = self.rfile.read(length)
            import io as _io
            import zipfile
            try:
                zf = zipfile.ZipFile(_io.BytesIO(data))
                names = zf.namelist()
            except zipfile.BadZipFile:
                self.send_json({"error": "不是有效的 zip/.motion 包"}, 400)
                return
            if not any(n == "params.schema.json" or n.endswith("/params.schema.json")
                       for n in names):
                self.send_json({"error": "包里没有 params.schema.json，不是 .motion 模板包"}, 400)
                return
            slug = re.sub(r"[^\w-]+", "", fn.rsplit(".", 1)[0])[:40] or ("tpl_%d" % int(time.time()))
            target = os.path.join(_mj.TPL_DIR, slug)
            if os.path.isdir(target):
                shutil.rmtree(target)          # 同名重导 = 覆盖更新
            os.makedirs(target, exist_ok=True)
            for n in names:                    # 防 zip 路径穿越
                if n.startswith("/") or ".." in n or n.endswith("/"):
                    continue
                dstp = os.path.join(target, n.replace("/", os.sep))
                if not os.path.abspath(dstp).startswith(os.path.abspath(target)):
                    continue
                os.makedirs(os.path.dirname(dstp), exist_ok=True)
                with open(dstp, "wb") as f:
                    f.write(zf.read(n))
            try:
                manifest = _motion_manifest_from_schema(target, slug, fn)
            except Exception as exc:  # noqa: BLE001
                shutil.rmtree(target, ignore_errors=True)
                self.send_json({"error": "解析模板失败：%s" % exc}, 400)
                return
            with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=1)
            self.send_json({"ok": True, "id": manifest["id"], "name": manifest["name"],
                            "kinds": len(manifest["kinds"]), "dir": target})
            return
        if p == "/api/tl/open_out":
            # 打开成片库（渲染成片统一存 E:\AI小程序\AI博主创作工具\成片库\）
            os.makedirs(CLIP_DIR, exist_ok=True)
            subprocess.Popen(["explorer", CLIP_DIR])
            self.send_json({"ok": True, "dir": CLIP_DIR})
            return
        if p == "/api/tl/render":
            # 异步渲染：立即返回 job id，进度用 /api/tl/render_status 轮询
            b = self.read_json()
            name = os.path.basename(str(b.get("p") or "props.json"))
            if not re.match(r"props[\w-]*\.json$", name):
                self.send_json({"error": "名字不合法"}, 400); return
            try:
                start, end = int(b.get("start", 0)), int(b.get("end", 0))
            except (TypeError, ValueError):
                self.send_json({"error": "start/end 不合法"}, 400); return
            jid = "%d" % int(time.time() * 1000)
            out = "seg_%d_%d.mp4" % (start, end)
            with RLOCK:
                RJOBS[jid] = {"status": "running", "pct": 0, "t0": time.time(),
                              "out": None, "log": "", "prog": os.path.join(CLIP_DIR, out + ".progress")}
            th = threading.Thread(target=_render_job, args=(jid, name, start, end), daemon=True)
            th.start()
            self.send_json({"ok": True, "id": jid})
            return
        if p == "/api/tl/render_status":
            uq = urllib.parse.urlparse(self.path).query
            q = urllib.parse.parse_qs(uq)
            jid = (q.get("id") or [""])[0]
            with RLOCK:
                job = dict(RJOBS.get(jid) or {})
            if not job:
                self.send_json({"error": "任务不存在"}, 404); return
            job["secs"] = round(time.time() - job.pop("t0", time.time()), 1)
            if job.get("status") == "running":
                # 渲染器每帧刷进度文件 → 查询时实时读
                try:
                    with open(job["prog"], encoding="utf-8") as f:
                        job["pct"] = max(job.get("pct", 0), int(f.read().strip() or 0))
                except Exception:  # noqa: BLE001
                    pass
            self.send_json(job)
            return
        if p == "/api/upload":
            self.api_upload(); return
        if p == "/api/import":
            self.api_import(); return
        if p == "/api/run":
            self.api_run(); return
        if p == "/api/reburn":
            self.api_reburn(); return
        if p == "/api/reveal":
            self.api_reveal(); return
        if p == "/api/stop":
            self.api_stop(); return
        if p == "/api/quit":
            self.send_json({"ok": True})
            threading.Timer(0.4, self.server.shutdown).start()
            return
        if p == "/api/outline":
            cfg = self.read_json()
            md = build_outline(cfg.get("title", ""), cfg.get("pillar", "①"),
                               cfg.get("extra", ""))
            self.send_json({"markdown": md})
            return
        if p == "/api/script/save":
            self.api_script_save(); return
        if p == "/api/review/save":
            self.api_review_save(); return
        if p == "/api/overview/publish-done":
            self.api_publish_done(); return
        if p == "/api/overview/baseline":
            self.api_baseline_save(); return
        if p == "/api/cover":
            self.api_cover(); return
        if p == "/api/bench/run":
            self.api_bench_run(); return
        if p == "/api/tts/run":
            tf = _ttsfree()
            if tf.busy():
                self.send_json({"error": "配音正在生成中，等它跑完。"}, 409); return
            cfg = self.read_json()
            r = tf.run(cfg)
            self.send_json(r); return
        if p == "/api/humanize":
            cfg = self.read_json()
            text = cfg.get("text") or ""
            self.send_json({"report": humanize_check(text),
                            "prompt": humanize_prompt(text)})
            return
        if p == "/api/wa/open":
            self.api_wa_run("open", {}); return
        if p == "/api/wa/login":
            self.api_wa_run("login", self.read_json()); return
        if p == "/api/wa/check":
            self.api_wa_check(); return
        if p == "/api/wa/loginstate":
            self.send_json(wa_login_state(
                (self.read_json() or {}).get("platform") or "douyin")); return
        if p == "/api/wa/close":
            self.api_wa_close(); return
        if p == "/api/wa/search":
            self.api_wa_run("search", self.read_json()); return
        if p == "/api/wa/user":
            self.api_wa_run("user", self.read_json()); return
        if p == "/api/wa/research":
            self.api_wa_research(); return
        if p == "/api/position/save":
            self.send_json(position_save((self.read_json() or {}).get("md") or "")); return
        if p == "/api/fresh":
            if self._busy():
                self.send_json({"error": "还有任务在跑，等它跑完再来。"}, 409); return
            jid = job_reset("fresh", "今日新鲜选题", "")
            threading.Thread(target=run_fresh_job, daemon=True).start()
            self.send_json({"job": jid}); return
        if p == "/api/ab/fetch":
            cfg = self.read_json()
            target = (cfg.get("target") or "A").upper()
            if target not in ("A", "B"):
                self.send_json({"error": "target 只能是 A 或 B"}, 400); return
            if self._busy():
                self.send_json({"error": "还有任务在跑，等它跑完再来。"}, 409); return
            label = "今日AI大事" if target == "A" else "今日爆款"
            jid = job_reset("ab_" + target, label, "")
            threading.Thread(target=run_ab_job, args=(target,), daemon=True).start()
            self.send_json({"job": jid}); return
        if p == "/api/intel/gen":
            self.send_json(api_intel_gen()); return
        if p == "/api/src/save":
            cfg = self.read_json()
            if "urls" in cfg:
                # 弹窗模式：只传网址列表 + 不带AI源勾选，头注释由后端补
                lines = ["# 自定义资讯源（在「📡 资讯源」弹窗里维护）",
                         "# 每行一个 RSS 或资讯网址，# 开头是注释"]
                if cfg.get("skip_ai"):
                    lines.append("不带AI源")
                lines += [str(u).strip() for u in (cfg.get("urls") or [])
                          if str(u).strip()]
                cfg["text"] = "\n".join(lines) + "\n"
            urls, skip_ai = src_file_save(cfg.get("text") or "")
            self.send_json({"ok": True, "count": len(urls), "skip_ai": skip_ai}); return
        if p == "/api/src/probe":
            u = (self.read_json().get("url") or "").strip()
            if not u.startswith("http"):
                self.send_json({"error": "网址不合法"}); return
            self.send_json(probe_source(u)); return
        if p == "/api/adu/run":
            ap = _adupack()
            st = ap.adu_status()
            if st["running"]:
                self.send_json({"error": "动效包装正在跑，等它跑完。"}, 409); return
            cfg = self.read_json()
            ap.adu_start({"style": cfg.get("style") or "auto", "video": cfg.get("video") or ""})
            threading.Thread(target=ap.run_job, args=(cfg,), daemon=True).start()
            self.send_json({"ok": True}); return
        if p == "/api/adu/reveal":
            fp = (self.read_json().get("path") or "").strip()
            if not fp or not os.path.isfile(fp):
                self.send_json({"error": "文件不存在"}); return
            subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])
            self.send_json({"ok": True}); return
        if p == "/api/research/save-candidates":
            cfg = self.read_json()
            kw = (cfg.get("kw") or "调研").strip()[:30]
            md = cfg.get("md") or ""
            if md.strip():
                # 报告里的「选题 N：标题 + - 键：值」转成标准 ### 条目，候选池解析器才认
                items, cur = [], None
                for ln in md.splitlines():
                    s = ln.strip()
                    m = re.match(r"^选题\s*\d*\s*[:：]\s*(.+)$", s)
                    if m:
                        if cur: items.append(cur)
                        cur = {"title": m.group(1).strip(), "bullets": []}
                    elif cur is not None and s.startswith("- "):
                        cur["bullets"].append(s[2:])
                if cur: items.append(cur)
                stamp = time.strftime("%Y-%m-%d %H:%M")
                with open(CANDIDATES, "a", encoding="utf-8") as f:
                    if items:
                        f.write("\n")
                        for it in items:
                            f.write("### [调研] %s\n" % it["title"])
                            for b in it["bullets"]:
                                f.write("- %s\n" % b)
                            f.write("- 来源：选题调研 · %s（%s）\n\n" % (kw, stamp))
                    else:
                        # 格式对不上时兜底：整份挂一条，内容不丢
                        f.write("\n### [调研] %s（%s）\n%s\n\n" % (kw, stamp, md))
            self.send_json({"ok": True, "count": len(items) if md.strip() else 0}); return
        if p == "/api/research/clear":
            r"""清掉选题调研报告：全部挪进 缓存\调研已清空\<时间>\，软删可找回。"""
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dst = os.path.join(CACHE_DIR, "调研已清空", stamp)
            moved = 0
            if os.path.isdir(RESEARCH_DIR):
                os.makedirs(dst, exist_ok=True)
                for fn in os.listdir(RESEARCH_DIR):
                    if fn.endswith(".md"):
                        try:
                            shutil.move(os.path.join(RESEARCH_DIR, fn), os.path.join(dst, fn))
                            moved += 1
                        except OSError:
                            pass
            job_push("[服务] 已清除选题调研报告 %d 份（挪进 缓存\\调研已清空\\%s）" % (moved, stamp))
            self.send_json({"ok": True, "removed": moved}); return
        if p == "/api/candidates/delete":
            cfg = self.read_json()
            try:
                idx = int(cfg.get("idx", -1))
            except (TypeError, ValueError):
                idx = -1
            r = candidates_delete(idx)
            self.send_json(r, 400 if r.get("error") else 200); return
        if p == "/api/candidates/clear":
            self.read_json()
            self.send_json(candidates_clear()); return
        if p == "/api/bench/save-report":
            self.api_bench_save_report(); return
        if p == "/api/wa/grab":
            self.api_wa_grab(); return
        if p == "/api/cache/clean":
            info = cache_clean()
            job_push("[服务] 已清理本机缓存 %d 个文件，腾出 %s" % (info["removed"], info["text"]))
            self.send_json(info); return
        if p == "/api/bench/clean":
            cfg = self.read_json()
            info = bench_clean(keep_results=bool(cfg.get("keep_results")))
            job_push("[服务] 已清空历史拆解 %d 个文件，腾出 %s" % (info["removed"], info["text"]))
            self.send_json(info); return
        if p == "/api/ai/config":
            self.api_ai_config(); return
        if p == "/api/ai/write":
            self.api_ai_write(); return
        if p == "/api/ai/stream":
            self.api_ai_stream(); return
        if p == "/api/ai/test":
            self.api_ai_test(); return
        if p == "/api/leyia/save":
            self.api_leyia_save(); return
        if p == "/api/leyia/gen":
            self.api_leyia_gen(); return
        if p == "/api/leyia/reveal":
            reveal(LEYIA_OUT)
            self.send_json({"ok": True}); return
        if p == "/api/cover/synth":
            payload = self.read_json()
            if not isinstance(payload.get("photos"), list):
                payload["photos"] = []
            if len(payload["photos"]) > 3:
                payload["photos"] = payload["photos"][:3]
            try:
                self.send_json(cover_synth(payload))
            except Exception as exc:  # noqa: BLE001
                self.send_json({"error": "封面合成失败：%s" % exc}, 500)
            return
        if p == "/api/cover/analyze":
            payload = self.read_json()
            samples = payload.get("samples") or []
            try:
                spec = cover_analyze(samples)
                self.send_json({"ok": True, "spec": spec})
            except Exception as exc:  # noqa: BLE001
                self.send_json({"error": str(exc)[:300]}, 400)
            return
        if p == "/api/dh/save":
            payload = self.read_json()
            d = dh_load()
            try:
                import base64 as _bchk
                if payload.get("voice"):
                    _vraw = _bchk.b64decode(payload["voice"].split(",", 1)[-1])
                    if len(_vraw) < 1024:
                        self.send_json({"error": "录音文件读取为空，请重新选择音频文件后再保存"}, 400)
                        return
                for _pu in (payload.get("portraits") or []):
                    if len(_bchk.b64decode(_pu.split(",", 1)[-1])) < 1024:
                        self.send_json({"error": "有形象照读取为空，请重新选择图片后再保存"}, 400)
                        return
                if payload.get("voice"):
                    d["voiceFile"] = _save_dataurl(
                        payload["voice"], os.path.join(DH_DIR, "音色参考",
                        "音色参考_%s%s" % (time.strftime("%m%d-%H%M"),
                                          payload.get("voiceExt") or ".wav")))
                ports = []
                for i, du in enumerate((payload.get("portraits") or [])[:3]):
                    ext = ".png" if "image/png" in du[:40] else ".jpg"
                    ports.append(os.path.basename(_save_dataurl(du,
                        os.path.join(DH_DIR, "形象照",
                        "形象照_%s_%d%s" % (time.strftime("%m%d-%H%M"), i + 1, ext)))))
                if ports:
                    d["portraits"] = ports
                for k in ("ttsKey", "ttsUrl", "dhBase", "dhKey", "dhModel",
                          "lsBase", "lsKey", "lsModel"):
                    v = (payload.get(k) or "").strip()
                    if v and not v.endswith("****"):
                        d[k] = v
                os.makedirs(DH_DIR, exist_ok=True)
                with open(DH_SET, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                self.send_json({"ok": True, "state": dh_state()})
            except Exception as exc:  # noqa: BLE001
                self.send_json({"error": "保存失败：%s" % exc}, 500)
            return
        self.send_json({"error": "not found"}, 404)

    # ---- 接口实现
    def api_upload(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            self.send_json({"error": "没有收到文件内容"}, 400)
            return
        name = safe_name(self.headers.get("X-File-Name"))
        ext = os.path.splitext(name)[1].lower()
        if ext not in VIDEO_EXT:
            self.send_json({"error": "只支持视频文件"}, 400)
            return
        dst = os.path.join(IN_DIR, name)
        stem, e2 = os.path.splitext(name)
        i = 1
        while os.path.exists(dst):
            dst = os.path.join(IN_DIR, "%s_%d%s" % (stem, i, e2))
            i += 1
        left = n
        with open(dst, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(1048576, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left > 0:
            self.send_json({"error": "文件传输中断，请重试"}, 400)
            return
        self.send_json({"path": dst, "name": os.path.basename(dst),
                        "size": os.path.getsize(dst),
                        "size_text": human(os.path.getsize(dst))})

    def api_import(self):
        cfg = self.read_json()
        raw = (cfg.get("path") or "").strip().strip('"').strip("'")
        if not raw:
            self.send_json({"error": "路径是空的"}, 400); return
        if not os.path.isfile(raw):
            self.send_json({"error": "这个路径下没有文件：%s" % raw}, 400); return
        ext = os.path.splitext(raw)[1].lower()
        if ext not in VIDEO_EXT:
            self.send_json({"error": "不是视频文件：%s" % ext}, 400); return
        self.send_json({"path": os.path.abspath(raw), "name": os.path.basename(raw),
                        "size": os.path.getsize(raw),
                        "size_text": human(os.path.getsize(raw))})

    def _busy(self):
        with JOB_LOCK:
            return JOB["running"]

    def api_run(self):
        if self._busy():
            self.send_json({"error": "还有一条正在处理中，等它跑完再来。"}, 409); return
        cfg = self.read_json()
        src = (cfg.get("path") or "").strip()
        if not os.path.isfile(src):
            self.send_json({"error": "找不到原片：%s" % src}, 400); return
        stem = os.path.splitext(os.path.basename(src))[0]
        stamp = time.strftime("%m%d-%H%M")
        out_path = os.path.join(OUT_DIR, "%s_成片_%s.mp4" % (stem, stamp))
        draft_path = os.path.join(CACHE_DIR, "%s_初剪.mp4" % stem)
        cues_path = os.path.join(CACHE_DIR, "%s_字幕.json" % stem)
        cmd = [PY, "-X", "utf8", PIPELINE, src, "-o", out_path,
               "--draft-out", draft_path, "--cues-json", cues_path]
        title = (cfg.get("title") or "").strip()
        if title:
            cmd += ["--title", title]
        if cfg.get("noFiller"):
            cmd.append("--no-filler")
        cmd += ["--margin", str(cfg.get("margin", 0.12)),
                "--max-line", str(cfg.get("maxLine", 14)),
                "--silence-db", str(cfg.get("silenceDb", -32)),
                "--silence-dur", str(cfg.get("silenceDur", 0.50)),
                "--model", str(cfg.get("model", "small"))]
        for bad, good in (cfg.get("fixes") or []):
            bad, good = (bad or "").strip(), (good or "").strip()
            if bad and good:
                cmd += ["--fix", "%s=%s" % (bad, good)]
        extra = (cfg.get("extraFiller") or "").strip()
        if extra:
            cmd += ["--extra-filler", extra]

        # BGM 铺底（成片完成后混入）
        bgm_name = (cfg.get("bgm") or "").strip()
        bgm_path = ""
        if bgm_name and bgm_name != "无":
            cand = os.path.join(BGM_DIR, os.path.basename(bgm_name))
            if os.path.isfile(cand):
                bgm_path = cand
            else:
                job_push("[BGM] 曲库里没有「%s」，本次不加背景音乐" % bgm_name)
        try:
            bgm_vol = max(0.03, min(0.9, float(cfg.get("bgmVol") or 0.12)))
        except (TypeError, ValueError):
            bgm_vol = 0.12
        try:
            bgm_fi = max(0.0, min(5.0, float(cfg.get("bgmFadeIn") if cfg.get("bgmFadeIn") is not None else 0.8)))
        except (TypeError, ValueError):
            bgm_fi = 0.8
        try:
            bgm_fo = max(0.0, min(5.0, float(cfg.get("bgmFadeOut") if cfg.get("bgmFadeOut") is not None else 1.5)))
        except (TypeError, ValueError):
            bgm_fo = 1.5
        bgm_duck = bool(cfg.get("bgmDuck", True))
        try:
            voice_gain = max(0.3, min(2.5, float(cfg.get("voiceGain") or 1.0)))
        except (TypeError, ValueError):
            voice_gain = 1.0

        jid = job_reset("run", os.path.basename(src), src)
        with JOB_LOCK:
            JOB["out_path"] = ""
            JOB["draft_path"] = draft_path
            JOB["cues_path"] = cues_path
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="ignore", bufsize=1, cwd=KOUBAO, env=envc)
        with JOB_LOCK:
            JOB["proc"] = proc

        def worker():
            rc = pump(proc)
            ok = (rc == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0)
            if not ok and rc != 0:
                job_push("[服务] 处理失败（退出码 %s）。上面日志里带 ! 的那行就是原因。" % rc)
            if ok and bgm_path:
                job_push("[BGM] 铺背景音乐：%s（音量 %.2f，人声优先）"
                         % (os.path.basename(bgm_path), bgm_vol))
                okb, msg = mix_bgm(out_path, bgm_path, bgm_vol,
                                   fade_in=bgm_fi, fade_out=bgm_fo,
                                   duck=bgm_duck, voice_gain=voice_gain)
                job_push("[BGM] %s" % msg)
                if not okb:
                    job_push("[BGM] 混音失败不拦成片，先用无 BGM 版本。")
            elif ok and abs(voice_gain - 1.0) >= 0.02:
                okg, msg = audio_gain_only(out_path, voice_gain)
                if msg:
                    job_push("[%s] %s" % ("音量" if okg else "音量失败", msg))
            job_finish(ok, out_path if ok else "")

        threading.Thread(target=worker, daemon=True).start()
        self.send_json({"job": jid})

    def api_reburn(self):
        if self._busy():
            self.send_json({"error": "还有一条正在处理中，等它跑完再来。"}, 409); return
        cfg = self.read_json()
        draft = JOB.get("draft_path", "")
        cues = JOB.get("cues_path", "")
        if not (os.path.isfile(draft) and os.path.isfile(cues)):
            self.send_json({"error": "还没有可复用的初剪片，先完整跑一次。"}, 400); return
        prev = JOB.get("out_path") or ""
        if not prev:
            self.send_json({"error": "还没有成片，先完整跑一次。"}, 400); return
        cmd = [PY, "-X", "utf8", REBURN, "--draft", draft, "--cues", cues, "-o", prev]
        title = (cfg.get("title") or "").strip()
        if title:
            cmd += ["--title", title]
        for bad, good in (cfg.get("fixes") or []):
            bad, good = (bad or "").strip(), (good or "").strip()
            if bad and good:
                cmd += ["--fix", "%s=%s" % (bad, good)]
        jid = job_reset("reburn", JOB.get("src_name", ""), JOB.get("src_path", ""))
        with JOB_LOCK:
            JOB["out_path"] = prev
            JOB["draft_path"] = draft
            JOB["cues_path"] = cues
            JOB["rev"] = JOB["rev"] + 1
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="ignore", bufsize=1, cwd=KOUBAO, env=envc)
        with JOB_LOCK:
            JOB["proc"] = proc

        def worker():
            rc = pump(proc)
            ok = (rc == 0 and os.path.isfile(prev) and os.path.getsize(prev) > 0)
            if not ok and rc != 0:
                job_push("[服务] 重烧失败（退出码 %s）。" % rc)
            job_finish(ok, prev if ok else "")

        threading.Thread(target=worker, daemon=True).start()
        self.send_json({"job": jid})

    def api_stop(self):
        with JOB_LOCK:
            proc = JOB.get("proc")
        if proc and proc.poll() is None:
            job_push("[服务] 收到停止指令，正在中断…")
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self.send_json({"ok": True})
        else:
            self.send_json({"ok": False, "note": "没有正在跑的任务"})

    def api_reveal(self):
        cfg = self.read_json()
        which = cfg.get("which", "out")
        if which == "in":
            target = JOB.get("src_path", "")
        elif which == "cache":
            target = CACHE_DIR
        elif which == "cover":
            target = COVER_DIR
        elif which == "srcfile":
            if not os.path.isfile(SRC_FILE):
                os.makedirs(os.path.dirname(SRC_FILE), exist_ok=True)
                with open(SRC_FILE, "w", encoding="utf-8") as f:
                    f.write(_SRC_TEMPLATE)
            os.startfile(SRC_FILE)      # 默认编辑器（记事本）打开，改完保存即生效
            self.send_json({"ok": True})
            return
        else:
            target = JOB.get("out_path", "")
        reveal(target)
        self.send_json({"ok": True})

    def api_script_save(self):
        cfg = self.read_json()
        name = safe_stem(cfg.get("name") or cfg.get("title") or "未命名")
        text = cfg.get("text") or ""
        if not text.strip():
            self.send_json({"error": "内容是空的"}, 400); return
        if not name.endswith(".md"):
            name = time.strftime("%Y-%m-%d_") + name + ".md"
        path = os.path.join(SCRIPTS_DIR, name)
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.send_json({"ok": True, "path": path, "name": os.path.basename(path)})

    def api_review_save(self):
        cfg = self.read_json()
        data = {}
        if os.path.isfile(REVIEW_JSON):
            try:
                data = json.loads(read_text(REVIEW_JSON) or "{}")
            except Exception:  # noqa: BLE001
                data = {}
        rid = (cfg.get("id") or "").strip() or ("r%s" % int(time.time()))
        data[rid] = {
            "id": rid,
            "title": (cfg.get("title") or "").strip(),
            "date": (cfg.get("date") or time.strftime("%Y-%m-%d")),
            "platform": (cfg.get("platform") or "douyin").strip(),
            "pred": cfg.get("pred"),
            "play": cfg.get("play"),
            "like": cfg.get("like"),
            "comment": cfg.get("comment"),
            "collect": cfg.get("collect"),
            "share": cfg.get("share"),
            "note": (cfg.get("note") or "").strip(),
        }
        with open(REVIEW_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.send_json({"ok": True, "count": len(data)})

    # ---- 总览驾驶舱
    def _load_review(self):
        if not os.path.isfile(REVIEW_JSON):
            return {}
        try:
            return json.loads(read_text(REVIEW_JSON) or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def _overview_payload(self):
        today = time.strftime("%Y-%m-%d")
        it = parse_intel()
        scripts_today = []
        for f in glob.glob(os.path.join(SCRIPTS_DIR, "*.md")):
            try:
                if time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))) == today:
                    scripts_today.append(os.path.basename(f))
            except OSError:
                pass
        cut_today = False
        try:
            for f in glob.glob(os.path.join(OUT_DIR, "**", "*.mp4"), recursive=True):
                if time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))) == today:
                    cut_today = True
                    break
        except OSError:
            pass
        recs = list(self._load_review().values())
        recs.sort(key=lambda r: ((r.get("date") or ""), (r.get("id") or "")))
        published_today = [r for r in recs if r.get("date") == today]
        reviewed_today = [r for r in recs
                          if r.get("date") == today and isinstance(r.get("play"), (int, float))]
        plays = [r["play"] for r in recs if isinstance(r.get("play"), (int, float))]
        best = None
        br = None
        for r in recs:
            if isinstance(r.get("play"), (int, float)) and (br is None or r["play"] > br["play"]):
                br = r
        if br:
            best = {"title": br.get("title") or "", "play": br["play"]}
        totals = {"like": 0, "comment": 0, "collect": 0, "share": 0}
        for r in recs:
            for k in totals:
                if isinstance(r.get(k), (int, float)):
                    totals[k] += int(r[k])
        engage = 0.0
        if sum(plays) > 0:
            engage = totals["like"] / float(sum(plays))
        baseline = {}
        if os.path.isfile(BASELINE_JSON):
            try:
                baseline = json.loads(read_text(BASELINE_JSON) or "{}")
            except Exception:  # noqa: BLE001
                baseline = {}
        return {
            "today": {"date": today, "intel_date": it.get("date") or "",
                      "scripts_today": scripts_today, "cut_today": cut_today,
                      "published_today": len(published_today),
                      "reviewed_today": len(reviewed_today)},
            "account": {"count": len(recs),
                        "total_play": sum(plays),
                        "avg_play": int(sum(plays) / len(plays)) if plays else 0,
                        "best": best,
                        "totals": totals, "engage": round(engage, 4),
                        "records": recs},
            "baseline": baseline,
        }

    def api_publish_done(self):
        """发布打卡：发布页是「复制三件套去平台手动发」，本地记一笔，
        出现在复盘页的已录数据里，等播放出来再回填。"""
        cfg = self.read_json()
        title = (cfg.get("title") or "").strip()
        if not title:
            self.send_json({"error": "填个标题"}, 400); return
        plat = (cfg.get("platform") or "douyin").strip()
        if plat not in ("douyin", "channels", "kuaishou", "xhs", "bilibili"):
            plat = "douyin"
        data = self._load_review()
        rid = "r%s" % int(time.time())
        data[rid] = {"id": rid, "title": title, "date": time.strftime("%Y-%m-%d"),
                     "platform": plat,
                     "pred": cfg.get("pred"), "play": None, "like": None,
                     "comment": None, "collect": None, "share": None,
                     "note": "发布打卡（数据待回填）"}
        with open(REVIEW_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.send_json({"ok": True, "count": len(data)})

    def api_baseline_save(self):
        cfg = self.read_json()
        cur = {}
        if os.path.isfile(BASELINE_JSON):
            try:
                cur = json.loads(read_text(BASELINE_JSON) or "{}")
            except Exception:  # noqa: BLE001
                cur = {}
        if cfg.get("fans") is not None:
            cur["fans"] = cfg.get("fans")
        if cfg.get("portrait") is not None:
            cur["portrait"] = cfg.get("portrait")
        cur["updated"] = time.strftime("%Y-%m-%d %H:%M")
        with open(BASELINE_JSON, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        self.send_json({"ok": True})

    def api_cover(self):
        """从成片里抽一帧做封面（带标题大字），存 工作区\\封面。
        底图优先用「无字幕的初剪片」，避免标题条/字幕被重复叠上去。"""
        cfg = self.read_json()
        final = (cfg.get("path") or JOB.get("out_path") or "").strip()
        draft = JOB.get("draft_path") or ""
        if not os.path.isfile(draft):
            # 服务重启后内存丢了，就从缓存目录找最新的初剪片
            try:
                ds = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)
                      if f.endswith("_初剪.mp4")]
                if ds:
                    draft = max(ds, key=os.path.getmtime)
            except OSError:
                draft = ""
        src = draft if os.path.isfile(draft) else final
        title = (cfg.get("title") or "").strip()
        if not os.path.isfile(src):
            self.send_json({"error": "没有可用的视频，先跑一次剪辑。"}, 400); return
        from_image = os.path.join(COVER_DIR, "%s_帧.png" % safe_stem(title or "封面"))
        # 抽帧
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            self.send_json({"error": "缺少 imageio-ffmpeg"}, 500); return
        t = cfg.get("at", None)
        at = "%.2f" % float(t) if t not in (None, "") else "1.0"
        r = subprocess.run([ff, "-hide_banner", "-nostats", "-y", "-ss", at, "-i", src,
                            "-frames:v", "1", from_image],
                           capture_output=True, text=True, errors="ignore")
        if not os.path.isfile(from_image):
            self.send_json({"error": "抽帧失败：%s" % (r.stderr[-200:])}, 500); return
        # 叠标题大字（用 ffmpeg drawtext，字体走微软雅黑）
        out_png = os.path.join(COVER_DIR, "%s_封面.png" % safe_stem(title or "封面"))
        font = r"C:\Windows\Fonts\msyhbd.ttc"
        if not os.path.isfile(font):
            font = r"C:\Windows\Fonts\msyh.ttc"
        if title and os.path.isfile(font):
            fdir, fname = os.path.split(font)
            vf = ("drawtext=fontfile='%s':text='%s':"
                  "fontcolor=white:fontsize=72:line_spacing=14:"
                  "box=1:boxcolor=black@0.62:boxborderw=22:"
                  "x=(w-text_w)/2:y=h*0.12" % (fname.replace("\\", "/"), title.replace("'", "’")))
            r2 = subprocess.run([ff, "-hide_banner", "-nostats", "-y", "-i", from_image,
                                 "-vf", vf, "-frames:v", "1", out_png],
                                capture_output=True, text=True, errors="ignore", cwd=fdir)
            if not os.path.isfile(out_png):
                out_png = from_image
        else:
            out_png = from_image
        self.send_json({"ok": True, "cover": out_png,
                        "url": "/api/video?path=" + urllib.parse.quote(out_png),
                        "name": os.path.basename(out_png)})

    # ---- 对标拆解
    def api_bench_run(self):
        if self._busy():
            self.send_json({"error": "还有一条正在处理中，等它跑完再来。"}, 409); return
        cfg = self.read_json()
        url = (cfg.get("url") or "").strip()
        text = (cfg.get("text") or "").strip()
        note = (cfg.get("note") or "").strip()
        if not url and not text:
            self.send_json({"error": "要么给一条分享链接，要么把文案粘进来。"}, 400); return
        if not os.path.isfile(BENCH_PY):
            self.send_json({"error": "bench.py 丢了，找小七要。"}, 500); return
        cmd = [PY, "-X", "utf8", BENCH_PY, "--out", BENCH_DIR]
        if url:
            cmd += ["--url", url]
        if text:
            cmd += ["--text", text]
        if note:
            cmd += ["--note", note]

        jid = job_reset("bench", url or "手粘文案", url)
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="ignore", bufsize=1, cwd=HERE, env=envc)
        with JOB_LOCK:
            JOB["proc"] = proc

        def worker():
            rc = pump(proc)
            out = ""
            with JOB_LOCK:
                for ln in JOB["lines"]:
                    if ln.startswith("BENCH_OUT="):
                        out = ln.split("=", 1)[1].strip()
            ok = (rc == 0 and out and os.path.isfile(out))
            if not ok and rc != 0:
                job_push("[服务] 拆解失败（退出码 %s）。上面带 ! 的那行就是原因。" % rc)
            if ok:
                job_push("[服务] 报告已存到：%s" % out)
            job_finish(ok, out if ok else "")

        threading.Thread(target=worker, daemon=True).start()
        self.send_json({"job": jid})


    # ---- 选题调研（Pongfi 方法论：先调研 → 方向聚类 → 候选清单）----
    def api_wa_research(self):
        if self._busy():
            self.send_json({"error": "还有任务在跑，等它跑完再来。"}, 409); return
        cfg = self.read_json()
        kw = (cfg.get("kw") or "").strip()
        if not kw:
            self.send_json({"error": "先填一个领域词，比如「普通人学AI」。"}, 400); return
        try:
            n = max(10, min(int(cfg.get("n") or 40), 50))
        except (TypeError, ValueError):
            n = 40
        try:
            pt = str(int(cfg.get("publish_time", 7)))
        except (TypeError, ValueError):
            pt = "7"                      # 默认一周内：调研要「最近」，太老没意义
        deep = bool(cfg.get("deep"))      # 深挖：进详情页补抓 评论/收藏/转发
        if not os.path.isfile(WA_PY):
            self.send_json({"error": "webaccess.py 丢了，找小七要。"}, 500); return
        os.makedirs(WA_RESULT_DIR, exist_ok=True)
        out_json = os.path.join(WA_RESULT_DIR, "research_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
        cmd = [PY, "-X", "utf8", WA_PY, "search", "--out", out_json,
               "--kw", kw, "--n", str(n), "--sort", "2", "--time", pt]
        jid = job_reset("research", "选题调研：%s" % kw, "")
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        envc["NO_PROXY"] = "127.0.0.1,localhost"
        envc["no_proxy"] = "127.0.0.1,localhost"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="ignore", bufsize=1, cwd=HERE, env=envc)
        with JOB_LOCK:
            JOB["proc"] = proc

        def worker():
            try:
                _research_worker(rc_holder, out_json, kw, deep)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                job_push("[调研] 出错：%s" % str(exc)[:160])
                job_finish(False, "")

        rc_holder = (proc,)

        def _research_worker(ph, out_json, kw, deep=False):
            proc = ph[0]
            rc = pump(proc)
            items = None
            try:
                with open(out_json, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("ok"):
                    items = d.get("items") or []
            except Exception:  # noqa: BLE001
                items = None
            if not items:
                job_push("[调研] 平台搜索受限，切换后台公开索引与今日热榜，不打开抖音页面…")
                items = []
                try:
                    import xml.etree.ElementTree as ET
                    q = urllib.parse.quote('%s 短视频 热点 趋势' % kw)
                    req = urllib.request.Request('https://www.bing.com/search?q=%s&format=rss' % q,
                                                 headers={'User-Agent': 'Mozilla/5.0'})
                    root = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
                    for node in root.findall('.//item')[:20]:
                        title = (node.findtext('title') or '').strip()
                        url = (node.findtext('link') or '').strip()
                        desc = re.sub(r'<[^>]+>', '', node.findtext('description') or '').strip()
                        if title:
                            items.append({'title': title, 'txt': desc, 'url': url,
                                          'author': '公开网页索引', 'like': 0, 'dur': ''})
                except Exception as exc:  # noqa: BLE001
                    job_push("[调研] ! 公开索引暂不可用：%s" % str(exc)[:100])
                try:
                    raw = urllib.request.urlopen(
                        'http://127.0.0.1:3119/api/creator/douyin-hotboard', timeout=90).read()
                    hot = json.loads(raw.decode('utf-8')).get('result') or []
                    items.extend({'title': x.get('word') or '', 'txt': '今日平台热榜',
                                  'url': '', 'author': '抖音热榜',
                                  'like': int(x.get('hot') or 0), 'dur': ''}
                                 for x in hot if x.get('word'))
                except Exception as exc:  # noqa: BLE001
                    job_push("[调研] ! 今日热榜补充失败：%s" % str(exc)[:100])
                if not items:
                    job_push("[调研] 没有拿到可核验的原始事实，任务停止，Agent 不会凭空编造。")
                    job_finish(False, "")
                    return
                job_push("[调研] 后台事实源拿到 %d 条，交给当前 Agent 继续分析。" % len(items))
            # ---- 深挖互动：挑赞最高的 12 条进详情页补 抖音评论/收藏/转发（列表页只有赞）
            if deep:
                urls = [it.get("url") for it in
                        sorted(items, key=lambda x: _likeint(x.get("like")), reverse=True)
                        if it.get("url")][:12]
                if urls:
                    job_push("[调研] 深挖互动数据：进 %d 条详情页抓 评论/收藏/转发（约 1-2 分钟）…" % len(urls))
                    eng_json = out_json.replace("research_", "engage_")
                    try:
                        eng_cmd = [PY, "-X", "utf8", WA_PY, "engage", "--out", eng_json]
                        for u in urls:
                            eng_cmd += ["--url", u]
                        subprocess.run(eng_cmd, capture_output=True, text=True,
                                       encoding="utf-8", errors="ignore",
                                       timeout=420, cwd=HERE, env=envc)
                        eng_map = {}
                        with open(eng_json, "r", encoding="utf-8") as f:
                            ed = json.load(f)
                        for e in (ed.get("items") or []):
                            eng_map[e.get("url")] = e
                        merged = 0
                        for it in items:
                            e = eng_map.get(it.get("url"))
                            if e:
                                it["comment"] = e.get("comment", "")
                                it["collect"] = e.get("collect", "")
                                it["share"] = e.get("share", "")
                                merged += 1
                        job_push("[调研] 互动数据补齐 %d/%d 条%s"
                                 % (merged, len(urls),
                                    "（没抓满：可能有验证码或部分视频打不开）" if merged < len(urls) else ""))
                    except Exception as exc:  # noqa: BLE001
                        job_push("[调研] ! 深挖失败（不影响主流程，只用赞继续）：%s" % str(exc)[:120])
            job_push("[调研] 后台抓到 %d 条真实作品，正在统计方向与互动信号…" % len(items))
            st = research_stats(items)
            lines = ["方向聚类（词 | 条数 | 赞中位数 | 相对全池倍数"
                     + (" | 评论中位 | 评赞比）" if st.get("has_cmt") else "）：")]
            for p in st["dirs"]:
                lines.append("- %s | %d 条 | 赞中位 %s | %.1f 倍%s"
                             % (p["term"], p["count"], p["med"], p["ratio"], p.get("eng", "")))
            lines.append("全池：%d 条，赞中位数 %s" % (st["n"], st["med"]))
            if st.get("has_cmt"):
                lines.append("互动：全池评论中位数 %s（来自详情页深挖；评赞比≈评论÷赞，越高越有讨论度）" % st["cmed"])
            stats_md = "\n".join(lines)
            srt = sorted(items, key=lambda x: (x.get("like") or 0), reverse=True)[:20]
            if st.get("has_cmt"):
                samples_md = "\n".join(
                    "- %s（赞 %s | 评 %s | 藏 %s | 转 %s，%s 秒）@%s" %
                    (it.get("title", ""), it.get("like", 0),
                     it.get("comment") or "?", it.get("collect") or "?",
                     it.get("share") or "?", it.get("dur") or "?", it.get("author") or "?")
                    for it in srt)
            else:
                samples_md = "\n".join("- %s（赞 %s，%s 秒）@%s" %
                                       (it.get("title", ""), it.get("like", 0), it.get("dur") or "?", it.get("author") or "?")
                                       for it in srt)
            job_push("[调研] 聚类完成：%d 个方向。当前 Agent 正在分析并生成候选清单（约 1 分钟）…" % len(st["dirs"]))
            brief = ""
            try:
                with open(POSITION, "r", encoding="utf-8") as f:
                    brief = re.sub(r"\s+", " ", f.read())[:800]
            except OSError:
                pass
            try:
                import aiwrite
                md = aiwrite.research(stats_md, samples_md, kw, account_brief=brief)
            except Exception as exc:  # noqa: BLE001
                job_push("[调研] Agent 生成清单失败：%s" % str(exc)[:160])
                job_finish(False, "")
                return
            os.makedirs(RESEARCH_DIR, exist_ok=True)
            safe = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", kw)[:24] or "调研"
            path = os.path.join(RESEARCH_DIR, "调研-%s-%s.md" % (time.strftime("%Y-%m-%d"), safe))
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 选题调研 · %s\n\n> 生成时间：%s　样本：%d 条（抖音 · 最新发布）\n\n---\n\n%s\n"
                        % (kw, time.strftime("%Y-%m-%d %H:%M"), st["n"], md))
            job_push("[调研] 报告已存：%s" % path)
            job_finish(True, path)

        threading.Thread(target=worker, daemon=True).start()
        self.send_json({"job": jid})

    def api_research_last(self):
        """最新一份选题调研报告（前端渲染用）。"""
        best = None
        if os.path.isdir(RESEARCH_DIR):
            for fn in os.listdir(RESEARCH_DIR):
                if fn.endswith(".md"):
                    p = os.path.join(RESEARCH_DIR, fn)
                    if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                        best = p
        if not best:
            self.send_json({}); return
        try:
            with open(best, "r", encoding="utf-8") as f:
                self.send_json({"path": best, "md": f.read()})
        except OSError as exc:
            self.send_json({"error": str(exc)}, 500)

    def api_bench_save_report(self):
        """把 AI 生成的账号拆解报告存进对标库。"""
        cfg = self.read_json()
        title = (cfg.get("title") or "账号拆解").strip()[:40]
        md = cfg.get("md") or ""
        if not md.strip():
            self.send_json({"error": "没有内容可存。"}, 400); return
        os.makedirs(BENCH_DIR, exist_ok=True)
        stem = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip("_ .") or "账号拆解"
        path = os.path.join(BENCH_DIR, "%s_%s.md" % (time.strftime("%Y-%m-%d"), stem))
        i = 1
        while os.path.exists(path):
            path = os.path.join(BENCH_DIR, "%s_%s_%d.md" % (time.strftime("%Y-%m-%d"), stem, i))
            i += 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        job_push("[服务] 账号拆解报告已存：%s" % path)
        self.send_json({"path": path})


    # ---- 对标浏览器（登录 / 搜爆款 / 扒对标账号）
    def api_wa_check(self):
        """同步检测：对标浏览器在不在 + 抖音登没登录。"""
        if not os.path.isfile(WA_PY):
            self.send_json({"error": "webaccess.py 丢了，找小七要。"}, 500); return
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        envc["NO_PROXY"] = "127.0.0.1,localhost"
        envc["no_proxy"] = "127.0.0.1,localhost"
        try:
            p = subprocess.run([PY, "-X", "utf8", WA_PY, "check"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="ignore", timeout=120, cwd=HERE, env=envc)
        except subprocess.TimeoutExpired:
            self.send_json({"alive": wa_alive(), "logged_in": False,
                            "error": "检测超时（浏览器没响应，可能卡在验证码）"}, 500)
            return
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        data = {}
        for ln in out.splitlines():
            if ln.startswith("WA_JSON="):
                try:
                    data = json.loads(ln[8:])
                except Exception:  # noqa: BLE001
                    data = {}
        if not data:
            data = {"alive": wa_alive(), "logged_in": False,
                    "error": "检测没拿到结果"}
        self.send_json(data)

    def api_wa_close(self):
        """停掉保活，并把对标浏览器关掉（只关我们自己那个，不碰用户日常 Chrome）。"""
        with _WA_KEEP["lock"]:
            _WA_KEEP["on"] = False
        if not wa_alive():
            self.send_json({"ok": True, "msg": "对标浏览器本来就没开"}); return
        if not os.path.isfile(WA_PY):
            self.send_json({"error": "webaccess.py 丢了，找小七要。"}, 500); return
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        envc["NO_PROXY"] = "127.0.0.1,localhost"
        envc["no_proxy"] = "127.0.0.1,localhost"
        try:
            p = subprocess.run([PY, "-X", "utf8", WA_PY, "close", "--out", ""],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="ignore", timeout=60, cwd=HERE, env=envc)
            out = (p.stdout or "") + "\n" + (p.stderr or "")
        except subprocess.TimeoutExpired:
            self.send_json({"error": "关闭超时，直接手动关那个窗口吧"}, 500); return
        data = {}
        for ln in out.splitlines():
            if ln.startswith("WA_JSON="):
                try:
                    data = json.loads(ln[8:])
                except Exception:  # noqa: BLE001
                    data = {}
        job_push("[服务] %s" % (data.get("msg") or "对标浏览器已关闭"))
        self.send_json(data or {"ok": True, "msg": "已请求关闭"})

    def api_wa_run(self, kind, cfg):
        if self._busy():
            self.send_json({"error": "还有任务在跑，等它跑完再来。"}, 409); return
        if not os.path.isfile(WA_PY):
            self.send_json({"error": "webaccess.py 丢了，找小七要。"}, 500); return
        cmd = [PY, "-X", "utf8", WA_PY, kind, "--out", ""]
        label = kind
        pin_url, pin_meta = "", None          # 「扒这个作者」时，把你点的那条置顶
        if kind == "search":
            kw = (cfg.get("kw") or "").strip()
            if not kw:
                self.send_json({"error": "先给个关键词。"}, 400); return
            try:
                n = int(cfg.get("n") or 20)
            except (TypeError, ValueError):
                n = 20
            n = max(1, min(n, 50))
            plat = (cfg.get("platform") or "douyin").strip() or "douyin"
            if plat != "douyin":
                # 非抖音：走多平台搜索引擎（psearch），排序/时间窗参数不适用
                # cmd 结构：[PY, -X, utf8, WA_PY, kind, --out, ""]，kind 在下标 4
                cmd[4] = "psearch"
                cmd += ["--plat", plat, "--kw", kw, "--n", str(n)]
                try:
                    import webaccess
                    pname = webaccess.get_platform(plat)["name"]
                except Exception:  # noqa: BLE001
                    pname = plat
                label = "搜爆款·%s：%s" % (pname, kw)
            else:
                cmd += ["--kw", kw, "--n", str(n)]
                # 排序 / 发布时间：0 综合·1 最热·2 最新；时间 0 不限·1 一天·7 一周·180 半年
                try:
                    st = int(cfg.get("sort_type", 2))
                except (TypeError, ValueError):
                    st = 2
                try:
                    pt = int(cfg.get("publish_time", 0))
                except (TypeError, ValueError):
                    pt = 0
                cmd += ["--sort", str(st), "--time", str(pt)]
                label = "搜爆款：%s" % kw
        elif kind == "user":
            keep, drop = wa_extract_lines(cfg.get("urls"))
            if not keep:
                self.send_json({"error": "没找到抖音链接。在抖音里进对方主页 → 分享 → 复制链接，整段粘进来（一行一个）。"}, 400)
                return
            try:
                n = int(cfg.get("n") or 20)
            except (TypeError, ValueError):
                n = 20
            keep = keep[:10]
            for ln in keep:
                cmd += ["--url", ln]
            cmd += ["--n", str(max(1, min(n, 50)))]
            label = "扒对标账号 ×%d" % len(keep)
            pin = cfg.get("pin") or {}
            if isinstance(pin, dict) and (pin.get("url") or "").strip():
                pin_url = pin["url"].strip()
                pin_meta = pin
        elif kind == "login":
            plat = (cfg.get("platform") or "douyin").strip() or "douyin"
            cmd += ["--plat", plat]
            try:
                import webaccess
                pname = webaccess.get_platform(plat)["name"]
            except Exception:  # noqa: BLE001
                pname = plat
            label = "打开登录页 · %s" % pname
        else:
            label = "打开对标浏览器"

        jid = job_reset("wa", label, "")
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        envc["NO_PROXY"] = "127.0.0.1,localhost"
        envc["no_proxy"] = "127.0.0.1,localhost"

        # 关键：由服务进程把浏览器先养起来（并开启保活）。
        # 任务子进程结束后浏览器仍在，下一次直接复用同一个窗口。
        okb, msgb = wa_ensure()
        if not okb:
            job_push("[服务] %s" % msgb)
            job_finish(False, "")
            self.send_json({"error": msgb}, 500)
            return
        job_push("[服务] %s" % msgb)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="ignore", bufsize=1, cwd=HERE, env=envc)
        with JOB_LOCK:
            JOB["proc"] = proc

        def worker():
            rc = pump(proc)
            out = ""
            with JOB_LOCK:
                for ln in JOB["lines"]:
                    if ln.startswith("WA_OUT="):
                        out = ln.split("=", 1)[1].strip()
            data = {}
            if out and os.path.isfile(out):
                try:
                    data = json.loads(read_text(out) or "{}")
                except Exception:  # noqa: BLE001
                    data = {}
            # 「扒这个作者」：把你点的那条视频置顶到第一位，并写回结果文件（刷新也不丢）
            if kind == "user" and pin_url and data.get("items"):
                try:
                    data["items"] = pin_first(data["items"], pin_url, pin_meta)
                    if out:
                        with open(out, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=1)
                except Exception:  # noqa: BLE001
                    pass
            ok = bool(data.get("ok"))
            if ok and not data.get("count") and data.get("notes"):
                for nt in data["notes"][:3]:
                    job_push("[服务] 一条没抓到：%s" % nt)
            if not ok and data.get("need") == "need_login":
                job_push("[服务] 抖音要登录。点「打开登录页」扫码，登好之后工作台会自动认出来（变绿）。")
            elif not ok and data.get("need") == "need_net":
                job_push("[服务] 页面打不开：本机代理/网络抽风（502/503）。等 10 秒再点一次。")
            elif not ok and data.get("need") == "need_captcha":
                job_push("[服务] 抖音弹了验证码。在「对标浏览器」窗口里过一下，再重试。")
            elif not ok and rc != 0:
                job_push("[服务] 失败（退出码 %s）。上面带 ! 的那行是原因。" % rc)
            with JOB_LOCK:
                WA_LAST["kind"] = kind
                WA_LAST["result"] = data
                WA_LAST["path"] = out
            job_finish(ok, out if os.path.isfile(out) else "")

        threading.Thread(target=worker, daemon=True).start()
        self.send_json({"job": jid})

    def api_wa_grab(self):
        """把勾选的对标视频逐条丢给 bench.py 扒文案 + 拆结构。"""
        if self._busy():
            self.send_json({"error": "还有任务在跑，等它跑完再来。"}, 409); return
        cfg = self.read_json()
        items = cfg.get("items") or []
        note = (cfg.get("note") or "").strip()
        urls = []
        for it in items:
            u = (it.get("url") if isinstance(it, dict) else str(it)) or ""
            if u.strip():
                urls.append(u.strip())
        if not urls:
            self.send_json({"error": "什么都没勾。"}, 400); return
        urls = urls[:10]
        if not os.path.isfile(BENCH_PY):
            self.send_json({"error": "bench.py 丢了，找小七要。"}, 500); return

        jid = job_reset("grab", "批量拆解 ×%d" % len(urls), "")
        envc = dict(os.environ)
        envc["PYTHONIOENCODING"] = "utf-8"
        proc_box = {}
        with JOB_LOCK:
            JOB["proc"] = None

        def worker():
            outs, bad = [], 0
            for i, u in enumerate(urls, 1):
                job_push("[服务] ── 第 %d/%d 条 ──" % (i, len(urls)))
                cmd = [PY, "-X", "utf8", BENCH_PY, "--out", BENCH_DIR, "--url", u]
                if note:
                    cmd += ["--note", note]
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                     errors="ignore", bufsize=1, cwd=HERE, env=envc)
                proc_box["p"] = p
                with JOB_LOCK:
                    JOB["proc"] = p
                pump(p)
                if p.returncode != 0:
                    bad += 1
                    job_push("[服务] 第 %d 条失败（退出码 %s），跳过。" % (i, p.returncode))
            with JOB_LOCK:
                for ln in JOB["lines"]:
                    if ln.startswith("BENCH_OUT="):
                        outs.append(ln.split("=", 1)[1].strip())
            job_push("[服务] 批量完成：成功 %d 条，失败 %d 条。报告都在 工作区\\对标库\\"
                     % (len(outs), bad))
            job_finish(bool(outs), outs[-1] if outs else "")

        threading.Thread(target=worker, daemon=True).start()
        self.send_json({"job": jid, "count": len(urls)})

    # ---- AI 写稿
    def api_ai_config(self):
        cfg = self.read_json()
        try:
            import aiwrite
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": "aiwrite.py 读不到：%s" % exc}, 500); return
        old = aiwrite.load_cfg()
        new = dict(old)
        for k in ("preset", "base_url", "model", "temperature", "vision_model"):
            if cfg.get(k) not in (None, ""):
                new[k] = cfg[k]
        key = (cfg.get("api_key") or "").strip()
        if key and not key.startswith("*"):
            new["api_key"] = key
        if cfg.get("clear_key"):
            new["api_key"] = ""
        try:
            new["temperature"] = float(new.get("temperature") or 0.8)
        except (TypeError, ValueError):
            new["temperature"] = 0.8
        aiwrite.save_cfg(new)
        self.send_json({"ok": True, "config": ai_cfg_safe()})

    # ---- 隐藏生图（leyia，仅自用）
    # 余额查询走 leyia 官方接口，赶上网络抖动可能要几十秒——绝不能在请求线程里同步等。
    # 策略：缓存 5 分钟；过期就起后台线程刷新，本请求立刻返回旧值 + loading 标记。
    def api_leyia_state(self):
        import leyia
        cfg = leyia.load_cfg()
        safe = leyia.cfg_safe(cfg)
        now = time.time()
        fresh = (LEY_BAL["ts"] > 0) and (now - LEY_BAL["ts"] < 300)
        need = safe["has_token"] or (safe["has_password"] and safe["username"])
        if need and not fresh and not LEY_BAL["running"]:
            LEY_BAL["running"] = True
            threading.Thread(target=_ley_refresh_balance, daemon=True).start()
        bal = LEY_BAL["value"] if (fresh or LEY_BAL["ts"] > 0) else None
        self.send_json({"config": safe, "balance": bal, "note": LEY_BAL["note"],
                        "out_dir": LEYIA_OUT, "balance_loading": need and not fresh})

    def api_leyia_save(self):
        import leyia
        cfg_in = self.read_json()
        cfg = leyia.load_cfg()
        user = (cfg_in.get("username") or "").strip()
        pwd = (cfg_in.get("password") or "").strip()
        if not user and not cfg.get("username"):
            self.send_json({"error": "账号不能为空"}, 400); return
        if user:
            cfg["username"] = user
        if pwd:
            cfg["password"] = pwd
        if not (cfg.get("username") and cfg.get("password")):
            self.send_json({"error": "账号和密码都要填"}, 400); return
        try:
            token = leyia.login(cfg["username"], cfg["password"], cfg["base_url"])
            cfg["token"] = token
            leyia.save_cfg(cfg)
            balance = leyia.get_balance(token, cfg["base_url"])
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": "登录失败：%s" % str(exc)[:200]}, 502); return
        _ley_log("账号 %s 登录成功，余额 %.1f" % (cfg["username"], balance))
        self.send_json({"ok": True, "balance": balance, "config": leyia.cfg_safe(cfg)})

    def api_leyia_gen(self):
        cfg = self.read_json()
        prompt = (cfg.get("prompt") or "").strip()
        if not prompt:
            self.send_json({"error": "先填生图提示词"}, 400); return
        with LEY_LOCK:
            busy = LEY["running"]
        if busy:
            self.send_json({"error": "上一张还在生成中，等它出图。"}, 409); return
        refs_in = cfg.get("refs") or []
        if not isinstance(refs_in, list) or len(refs_in) > 4:
            self.send_json({"error": "参考图最多 4 张"}, 400); return
        if not refs_in:
            self.send_json({"error": "leyia 是图生图接口，至少传 1 张参考图"}, 400); return
        try:
            ref_paths = [_save_data_url(u, i) for i, u in enumerate(refs_in)]
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 400); return
        aspect = cfg.get("aspect") if cfg.get("aspect") in ("9:16", "3:4", "4:3", "3:2", "2:3", "16:9", "1:1") else "9:16"
        quality = cfg.get("quality") if cfg.get("quality") in ("medium", "high") else "medium"
        threading.Thread(target=run_leyia_job,
                         args=(prompt, aspect, quality, ref_paths),
                         daemon=True).start()
        self.send_json({"ok": True})

    def _ai_prepare(self, cfg):
        """两种 AI 入口公用的参数整理 + 前置校验。

        返回 (aiwrite模块, mode, 参数字典) 或 (None, 错误消息, None)。
        """
        mode = (cfg.get("mode") or cfg.get("action") or "draft").strip()
        title = (cfg.get("title") or "").strip()
        extra = cfg.get("extra") or ""
        pillar = cfg.get("pillar") or "①"
        text = (cfg.get("text") or "").strip()
        think = bool(cfg.get("think"))
        hits = cfg.get("hits") or None
        hints = cfg.get("hints") or None
        try:
            import aiwrite
        except Exception as exc:  # noqa: BLE001
            return None, "aiwrite.py 读不到：%s" % exc, None
        if mode in ("rewrite", "oral", "humanize", "titles", "storyboard", "score",
                    "audit", "redraft", "content", "hook") and not text:
            hint = {"rewrite": "先把对标报告或参考稿放进「稿子」框。",
                    "oral": "先写或粘贴一版稿子，再点「口播化」。",
                    "humanize": "先写或粘贴一版稿子，再点「去 AI 味」。",
                    "titles": "先有一份稿子，再点「起标题」。",
                    "storyboard": "先有一版口播稿，再点「生成分镜」。",
                    "score": "先有一版稿子，再点「盲测打分」——去文案页写或粘一版。",
                    "audit": "先抓到这个账号的作品数据，再点「拆解账号」。",
                    "redraft": "先有一份拆解报告，再点「生成我们的初稿」。",
                    "content": "先有一版稿子，再点「内容诊断」。",
                    "hook": "先有一版稿子，再点「开头优化」。"}.get(mode, "先在「稿子」框放内容。")
            return None, hint, None
        if mode == "draft" and not title:
            return None, "先在「选题」里填个标题。", None
        brief = ""
        if mode == "redraft":
            try:
                with open(POSITION, "r", encoding="utf-8") as f:
                    brief = re.sub(r"\s+", " ", f.read())[:800]
            except OSError:
                pass
        return aiwrite, mode, {"text": text, "title": title, "pillar": pillar,
                               "extra": extra, "think": think, "hits": hits,
                               "hints": hints, "brief": brief}

    def api_ai_write(self):
        """一次拿完整结果（不流式）。

        模式：draft 写初稿 / rewrite 改写 / oral 口播化 / humanize 去 AI 味
              / titles 起标题 / storyboard 分镜 / score 盲测打分
        """
        aiwrite, mode, kw = self._ai_prepare(self.read_json())
        if aiwrite is None:
            self.send_json({"error": mode}, 400); return
        try:
            out, system, user = aiwrite.process(mode, **kw)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc), "prompt": "", "mode": mode}, 200)
            return
        self.send_json({"text": out, "mode": mode, "prompt": system + "\n\n" + user})

    def api_ai_stream(self):
        """流式出稿（SSE）。前端弹窗里实时显示「思考 → 逐字写」。

        事件协议（每行一条 `data: {...}`）：
          {"t":"think","d":"…"}   思考段的增量
          {"t":"body","d":"…"}    正文段的增量
          {"t":"meta","stage":"…"} 阶段提示
          {"t":"done","elapsed":12.3,"text":"全文"}
          {"t":"error","error":"…","prompt":"…"}  失败时带上提示词，前端可复制兜底
        """
        aiwrite, mode, kw = self._ai_prepare(self.read_json())
        if aiwrite is None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self._sse({"t": "error", "error": mode})
            return

        system, user = aiwrite.build(mode, **kw)
        # 先握手再开跑：客户端一收到 200 就把计时器点起来
        self.close_connection = True          # SSE 没有 Content-Length，靠关连接收尾
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        t0 = time.time()
        self._sse({"t": "meta", "stage": "已连上模型，正在想…"})
        buf = []
        try:
            for delta in aiwrite.chat_stream(aiwrite.load_cfg(), system, user):
                if not delta:
                    continue
                buf.append(delta)
                self._sse({"t": "d", "d": delta})     # 只发增量，切分交给前端
            full = "".join(buf)
            self._sse({"t": "done", "elapsed": round(time.time() - t0, 1), "text": full})
        except Exception as exc:  # noqa: BLE001
            self._sse({"t": "error", "error": str(exc),
                       "prompt": system + "\n\n" + user})

    def _sse(self, obj):
        try:
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n")
                             .encode("utf-8"))
            self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass

    def api_ai_test(self):
        try:
            import aiwrite
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": "aiwrite.py 读不到：%s" % exc}, 500); return
        try:
            r = aiwrite.test()
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 200); return
        self.send_json(r)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """浏览器刷新/断开时连接被强制关闭是常态，静音，别让堆栈刷屏甚至拖垮服务。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7871)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    # Launching again must not terminate active creation jobs in an existing service.
    try:
        identity = _http_get_json('http://127.0.0.1:%d/api/app' % args.port, timeout=1)
        if identity.get('app_id') == 'ai-creator-workbench':
            if not args.no_browser:
                open_browser('http://127.0.0.1:%d/' % args.port)
            print('工作台已运行，复用现有服务。')
            return
    except Exception:
        pass
    port = pick_port(args.port)
    url = "http://127.0.0.1:%d/" % port
    srv = Server(("127.0.0.1", port), Handler)
    _cut.atomic_json(os.path.join(HERE, 'runtime', 'service.json'),
                     {'app_id': 'ai-creator-workbench', 'url': url, 'pid': os.getpid(), 'port': port})

    print("=" * 60)
    print("  AI 博主工作台 · 选题 → 文案 → 提词 → 剪辑 → 发布 → 复盘")
    print("=" * 60)
    print("  浏览器界面：%s" % url)
    print("  工作区目录：%s" % WORK)
    print("  脚本目录  ：%s" % SCRIPTS_DIR)
    print()
    print("  这个黑窗口是服务本体，别关。用完在网页里点「退出」即可关闭。")
    print("=" * 60)

    if not args.no_browser:
        def launch():
            time.sleep(0.4)
            how = open_browser(url)
            if how:
                print("\n  已用 %s 打开界面。" % how)
            else:
                print("\n  ! 浏览器没自动打开，请手动打开 Chrome 访问 %s" % url)
        threading.Thread(target=launch, daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
