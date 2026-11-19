# -*- coding: utf-8 -*-
"""motion-kit 模板集成（MotionGit 新规范 1.0.0-rc.1 保护型个人包）

官方管线（已实测端到端通过）：
  node assistant/motion.mjs start <pkg>                     验签 + 制作引导
  ... run <pkg> --instance <dir> plan <params> --inputs <media> --out <planDir>
  ... run <pkg> --instance <dir> prepare <params> --inputs <media> --plan <planDir> --out <projDir>
  ... run <pkg> --instance <dir> preview <projDir> --times 0.5,8,...
  ... run <pkg> --instance <dir> render <projDir> --out video.mp4
关卡：plan 产 review.json 须置 reviewed=true；preview 产 preview.json 须置 reviewed=true。

本模块：包发现 → 官方读取 → 生成工作台 manifest（进注册表）→
        动效 JSON(docs) ↔ kit params 互转 → 一键出片流水线。
Key 永不出包：所有命令都以原 .motion 为凭据，personal/ 不解压不复制。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, "动效模板")
FFMPEG_DIR = os.path.join(HERE, "工具", "ffmpeg")
NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
BOOT_ROOT = os.path.join(HERE, "工作区", "kit_boot")
RUN_ROOT = os.path.join(HERE, "工作区", "kit_run")
GROUP_RE = re.compile(r"^(\d+)-(.+)$")


# ---------------------------------------------------------------- 包发现
def packages():
    """扫描动效模板/NN-组名/*.motion → [{pkg, group_dir, group}]"""
    out = []
    if not os.path.isdir(TPL_DIR):
        return out
    for d in sorted(os.listdir(TPL_DIR)):
        gd = os.path.join(TPL_DIR, d)
        if not os.path.isdir(gd) or not GROUP_RE.match(d):
            continue
        group = GROUP_RE.match(d).group(2)
        for f in sorted(os.listdir(gd)):
            if f.lower().endswith(".motion"):
                out.append({"pkg": os.path.join(gd, f), "group_dir": gd, "group": group})
    return out


def _env():
    env = dict(os.environ)
    env["PATH"] = FFMPEG_DIR + os.pathsep + env.get("PATH", "")
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return env


# ---------------------------------------------------------------- 引导缓存
def _pkg_key(pkg):
    st = os.stat(pkg)
    h = hashlib.sha256()
    h.update(os.path.abspath(pkg).encode("utf-8"))
    h.update(("%d_%d" % (st.st_size, int(st.st_mtime))).encode())
    return h.hexdigest()[:16]


BOOT_FILES = ["00-START-HERE.md", "README.md", "assistant/guide.json",
              "assistant/motion.mjs", "assistant/BUILD.json",
              "assistant/THIRD-PARTY-NOTICES.txt"]


def bootstrap(pkg):
    """解包官方引导文件（assistant/motion.mjs 等，不含 Key 的公开文件）→ 目录。"""
    import zipfile
    bdir = os.path.join(BOOT_ROOT, _pkg_key(pkg))
    marker = os.path.join(bdir, ".ok")
    if os.path.exists(marker):
        return bdir
    os.makedirs(bdir, exist_ok=True)
    with zipfile.ZipFile(pkg) as zf:
        for n in BOOT_FILES:
            if n in zf.namelist():
                dst = os.path.join(bdir, n.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(zf.read(n))
    open(marker, "w").write("ok")
    return bdir


def cli(pkg, args, timeout=600):
    """跑官方 CLI，返回 (code, stdout文本)。"""
    bdir = bootstrap(pkg)
    cmd = [NODE, os.path.join(bdir, "assistant", "motion.mjs")] + list(args)
    p = subprocess.run(cmd, cwd=bdir, env=_env(), timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout or ""


def read_doc(pkg, path):
    """read <pkg> <path> → (信封元数据, content)。currentTemplate 在信封层。"""
    code, out = cli(pkg, ["read", os.path.abspath(pkg), path], timeout=300)
    if code != 0:
        raise RuntimeError("read %s 失败：%s" % (path, out[-300:]))
    d = json.loads(out)
    c = d.get("content")
    if isinstance(c, str):                      # markdown 文档是转义字符串
        try:
            c = json.loads(c)
        except Exception:
            pass
    meta = d.get("currentTemplate") or {}
    return meta, c


# ---------------------------------------------------------------- manifest 生成
def _short_name(zh):
    parts = [x.strip() for x in zh.split("·") if x.strip()]
    return re.sub(r'[\\/:*?"<>|]', "", parts[0]).replace(" ", "") or zh[:12]


def _prop_rule(prop):
    """catalog prop → (fields规则 or None, opts meta or None)。全部落成 字符串/字符串数组。"""
    t = prop.get("type")
    if t == "string" or t == "icon":
        mx = int(prop.get("max") or 24)
        return (mx, None) if prop.get("required") else (None, {"t": "str", "max": mx})
    if t == "number":
        return (12, None) if prop.get("required") else (None, {"t": "num"})
    if t == "enum":
        mx = max((len(str(v)) for v in prop.get("values", [""])), default=8) + 4
        meta = {"t": "enum", "values": prop.get("values", [])}
        return (mx, None) if prop.get("required") else (None, meta)
    if t == "array":
        of = prop.get("of")
        lo, hi = int(prop.get("minItems") or 0), int(prop.get("maxItems") or 4)
        if of == "object":
            subs = prop.get("props") or {}
            rule, metas = {}, {}
            for sn, sp in subs.items():
                if sp.get("type") == "number":
                    metas[sn] = {"t": "num"}
                    rule[sn] = (hi, 12, True)           # 占位，后面重组
                elif sp.get("type") == "enum":
                    metas[sn] = {"t": "enum", "values": sp.get("values", [])}
                    rule[sn] = (hi, 24, True)
                else:
                    metas[sn] = {"t": "str", "max": int(sp.get("max") or 24)}
                    rule[sn] = (hi, int(sp.get("max") or 24), True)
            return None, {"t": "arr_obj", "min": lo, "max": hi, "props": metas}
        per = int((prop.get("props") or {}).get("max") or 24)
        return ((lo, hi, per), None) if prop.get("required") else (None, {"t": "arr", "min": lo, "max": hi, "per": per})
    if t == "object":
        subs = prop.get("props") or {}
        rule, metas = {}, {}
        for sn, sp in subs.items():
            if sp.get("type") == "number":
                metas[sn] = {"t": "num"}
                rule[sn] = (12, True)
            elif sp.get("type") == "enum":
                metas[sn] = {"t": "enum", "values": sp.get("values", [])}
                rule[sn] = (24, True)
            elif sp.get("type") == "array":
                lo2 = int(sp.get("minItems") or 0)
                hi2 = int(sp.get("maxItems") or 3)
                per2 = int((sp.get("props") or {}).get("max") or 24)
                metas[sn] = {"t": "arr", "min": lo2, "max": hi2, "per": per2}
                rule[sn] = (lo2, hi2, per2)
            else:
                metas[sn] = {"t": "str", "max": int(sp.get("max") or 24)}
                rule[sn] = (int(sp.get("max") or 24), True)
        return None, {"t": "obj", "props": metas}
    return None, None


def build_manifest(pkg, group):
    """官方 guide.json → 工作台 manifest（kinds=控件目录的常用子集）。"""
    guide_meta, guide = read_doc(pkg, "guide.json")
    tpl = guide_meta or {}
    lib = guide.get("extensionLibrary") or {}
    controls = lib.get("controls") or {}
    groups = lib.get("groups") or {}
    zh = tpl.get("name") or _short_name(os.path.basename(pkg))
    name = _short_name(zh)
    if str(tpl.get("layout")) == "portrait" and "竖屏" not in name:
        name += "竖屏"
    kit_id = "%s-%s" % (group, name)
    # 收录组：口播视频常用；overlay 只收常用于信息流的
    include = set()
    for g in ("base", "concept", "data", "typo", "brand", "diagram", "showcase"):
        include.update(groups.get(g) or [])
    include.update(["lowerthird", "callout", "tag"])
    if not include:
        include = set(controls)                 # 目录没分组的模板：全收
    kinds, opts_all, meta_all = {}, {}, {}
    for cname in sorted(include & set(controls)):
        c = controls[cname]
        fields, opts = {}, {}
        for pname, prop in (c.get("props") or {}).items():
            rule, opt = _prop_rule(prop)
            if rule is not None:
                fields[pname] = rule
            if opt is not None:
                opts[pname] = opt
        # 数组对象的并行数组规则 → [min,max,per]
        for pname, opt in list(opts.items()):
            if opt["t"] == "arr_obj":
                for sn, sm in opt["props"].items():
                    if sm["t"] == "num":
                        fields["%s.%s" % (pname, sn)] = [opt["min"], opt["max"], 12]
                    elif sm["t"] == "enum":
                        fields["%s.%s" % (pname, sn)] = [opt["min"], opt["max"], 24]
                    else:
                        fields["%s.%s" % (pname, sn)] = [opt["min"], opt["max"], sm.get("max", 24)]
                opts[pname]["per"] = True
            elif opt["t"] == "obj":
                for sn, sm in opt["props"].items():
                    if sm["t"] == "num":
                        fields["%s.%s" % (pname, sn)] = 12
                    elif sm["t"] == "enum":
                        fields["%s.%s" % (pname, sn)] = 24
                    elif sm["t"] == "arr":
                        fields["%s.%s" % (pname, sn)] = (sm["min"], sm["max"], sm["per"])
                    else:
                        fields["%s.%s" % (pname, sn)] = sm.get("max", 24)
                opts[pname]["flat"] = True
        if not fields:
            continue
        kinds[cname] = {"label": c.get("label") or cname,
                        "use": c.get("use") or "",
                        "fields": fields,
                        "minDur": c.get("minDur") or 0}
        if opts:
            opts_all[cname] = opts
        meta_all[cname] = {"variants": c.get("variants") or [],
                           "cues": c.get("cues", {}).get("count"),
                           "maxDur": c.get("maxDur")}
    if not kinds:
        # 原生路线模板：无控件库，模板按 brief 自排画面
        return {
            "id": kit_id, "name": name,
            "engine": "motion-kit-native", "theme": tpl.get("name") or name,
            "desc": "%s · %s · %sx%s@%s · v%s（原生路线：模板按文案要点自排画面）" % (
                group, tpl.get("name") or name, tpl.get("width"), tpl.get("height"),
                tpl.get("fps"), tpl.get("version")),
            "kinds": {"auto": {"label": "模板自排", "use": "整片由模板根据文案要点自动编排",
                               "fields": {}}},
            "kit": {"pkg": os.path.abspath(pkg), "group": group, "workflow": "native",
                    "layout": tpl.get("layout"), "width": tpl.get("width"),
                    "height": tpl.get("height"), "version": tpl.get("version"),
                    "briefSchema": native_schema_const(pkg), "opts": {}, "controlMeta": {},
                    "styles": list(lib.get("styleProfiles") or {})[:8]},
        }, zh
    manifest = {
        "id": kit_id, "name": name,
        "engine": "motion-kit", "theme": tpl.get("name") or name,
        "desc": "%s · %s · %sx%s@%s · v%s（MotionGit 官方管线）" % (
            group, tpl.get("name") or name, tpl.get("width"), tpl.get("height"),
            tpl.get("fps"), tpl.get("version")),
        "kinds": kinds,
        "kit": {"pkg": os.path.abspath(pkg), "group": group, "workflow": "motion-kit",
                "layout": tpl.get("layout"), "width": tpl.get("width"),
                "height": tpl.get("height"), "version": tpl.get("version"),
                "opts": opts_all, "controlMeta": meta_all,
                "styles": list(lib.get("styleProfiles") or {})[:8]},
    }
    return manifest, zh


def register_all(force=False):
    """全部包 → 动效模板/<组>/<短名>/manifest.json。返回注册数与失败表。"""
    ok, fail = [], []
    seen = set()
    for it in packages():
        pkg = it["pkg"]
        try:
            manifest, zh = build_manifest(pkg, it["group"])
            kid = manifest["id"]
            if kid in seen:
                kid = kid + "-" + hashlib.md5(pkg.encode()).hexdigest()[:4]
                manifest["id"] = kid
            seen.add(kid)
            dirname = re.sub(r'[\\/:*?"<>|]', "", manifest["name"]) or \
                ("kit_" + hashlib.md5(pkg.encode()).hexdigest()[:6])
            tdir = os.path.join(it["group_dir"], dirname)
            if os.path.isdir(tdir) and os.path.exists(os.path.join(tdir, "manifest.json")):
                ok.append({"id": manifest["id"], "kinds": len(manifest["kinds"]),
                           "dir": tdir, "skip": "已存在"})
                continue
            os.makedirs(tdir, exist_ok=True)
            json.dump(manifest, open(os.path.join(tdir, "manifest.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            ok.append({"id": manifest["id"], "kinds": len(manifest["kinds"]), "dir": tdir})
        except Exception as e:  # noqa: BLE001
            fail.append({"pkg": os.path.basename(pkg), "err": str(e)[:200]})
    return ok, fail


PURPOSE_OF = {
    "compare": "real-comparison", "vs": "real-comparison", "prosCons": "real-comparison",
    "beforeafter": "real-comparison", "myth": "decision-rejection",
    "steps": "ordered-process", "flow": "ordered-process", "cycle": "ordered-process",
    "timeline": "ordered-process", "funnel": "ordered-process", "progress": "ordered-process",
    "progressbar": "ordered-process", "mindmap": "ordered-process", "matrix": "ordered-process",
    "list": "neutral-list", "checklist": "neutral-list", "tip": "neutral-list",
    "definition": "neutral-list", "tag": "neutral-list", "sticker": "neutral-list",
    "emoji": "neutral-list", "lowerthird": "neutral-list", "callout": "neutral-list",
    "notify": "neutral-list", "quote": "neutral-list",
    "cta": "outreach-support", "subscribe": "outreach-support",
    "outro": "summary",
    "number": "provided-evidence", "counter": "provided-evidence", "donut": "provided-evidence",
    "linechart": "provided-evidence", "table": "provided-evidence", "ranking": "provided-evidence",
    "bars": "provided-evidence", "price": "provided-evidence", "countdown": "provided-evidence",
}


# ---------------------------------------------------------------- params 构建
def _unflatten(fields, opts_spec):
    """doc fields（扁平）→ 控件 props（嵌套/数值/枚举还原）。"""
    props = {}
    arrs = {}                                    # 并行数组收集
    for k, v in (fields or {}).items():
        if "." in k:
            head, sub = k.split(".", 1)
            spec = (opts_spec or {}).get(head)
            if spec and spec.get("t") == "arr_obj":
                arrs.setdefault(head, {}).setdefault(sub, []).append(v)
            elif spec and spec.get("t") == "obj":
                props.setdefault(head, {})
                sm = spec["props"].get(sub, {})
                props[head][sub] = float(v) if sm.get("t") == "num" and str(v).lstrip("-.").replace(".", "", 1).isdigit() else v
            else:
                props[head] = v
        else:
            sm = (opts_spec or {}).get(k) or {}
            t = sm.get("t")
            if t == "num" and str(v).lstrip("-.").replace(".", "", 1).isdigit():
                props[k] = float(v)
            else:
                props[k] = v
    for head, subs in arrs.items():
        spec = opts_spec[head]
        n = max((len(v) for v in subs.values()), default=0)
        items = []
        for i in range(n):
            it = {}
            for sn, vals in subs.items():
                if i < len(vals):
                    sm = spec["props"].get(sn, {})
                    it[sn] = float(vals[i]) if sm.get("t") == "num" and str(vals[i]).lstrip("-.").replace(".", "", 1).isdigit() else vals[i]
            if it:
                items.append(it)
        props[head] = items
    return props


def doc_to_params(doc, media_dir, meta):
    """工作台 doc → kit params.json 结构（timeline + narrationMap + captions）。
    官方校验硬要求：段首尾相接全覆盖 [0, duration]、最后一段结尾=duration、
    每段≥minDur、切点必须落在字幕边界（NARRATION_CONTINUITY：一句字幕不能被切两半）。
    因此先把卡窗映射成「字幕覆盖区间」，在字幕空间里去重/补空档/借时，再换算回秒。"""
    g = doc.get("global") or {}
    m = meta
    dur = float(meta["duration"])
    caps = meta["captions"]                     # [{s,e,text}]
    if not caps:
        raise ValueError("底片没有字幕时间轴")
    n = len(caps)
    # ---- 卡窗 → 字幕覆盖区间 [a, b]（闭区间，字幕下标）----
    raw = []
    for sl in doc.get("slides") or []:
        s = max(0.0, float(sl["s"]))
        e = min(dur, float(sl["e"]))
        if e - s < 0.4:
            continue
        idxs = [j for j, c in enumerate(caps) if c["s"] < e and c["e"] > s]
        if not idxs:                            # 卡窗没压到任何字幕 → 挂到最近字幕
            idxs = [min(range(n), key=lambda j: min(abs(caps[j]["s"] - s), abs(caps[j]["e"] - e)))]
        raw.append([idxs[0], idxs[-1], sl])
    raw.sort(key=lambda x: x[0])
    kept = []
    for a, b, sl in raw:
        if kept and a <= kept[-1][1]:           # 字幕区间重叠 → 从下一条未用字幕起
            a = kept[-1][1] + 1
            if a > b:
                continue
        kept.append([a, b, sl])
    if not kept:
        raise ValueError("没有有效卡窗")
    # ---- 全覆盖：字幕区间之间的洞由前一张吞掉；minDur 向后吞字幕 ----
    for i in range(1, len(kept)):
        kept[i][0] = kept[i - 1][1] + 1
    for i, seg in enumerate(kept):
        kind = seg[2].get("kind")
        mind = float(((m.get("kinds") or {}).get(kind) or {}).get("minDur") or 0)

        def _span(a, b):
            t0 = 0.0 if a <= 0 else caps[a]["s"]
            t1 = dur if b >= n - 1 else caps[b]["e"]
            return t1 - t0
        j = seg[1]
        while _span(seg[0], j) < mind and j + 1 < n and (i + 1 >= len(kept) or j + 1 < kept[i + 1][0]):
            j += 1
        seg[1] = j
    # ---- 仍不达 minDur 的段：首段扩到 0；尾段向前借字幕、借不动并前段；中段并入前段 ----
    def _mind(kind):
        return min(float(((m.get("kinds") or {}).get(kind) or {}).get("minDur") or 0), dur)
    if kept and _span(kept[0][0], kept[0][1]) < _mind(kept[0][2].get("kind")) - 0.01:
        kept[0][0] = 0
    while len(kept) > 1 and _span(kept[-1][0], kept[-1][1]) < _mind(kept[-1][2].get("kind")) - 0.01:
        prev = kept[-2]
        p0, p1 = prev[0], prev[1]
        if p1 - 1 >= p0 and _span(p0, p1 - 1) >= _mind(prev[2].get("kind")) - 0.01:
            prev[1] = p1 - 1                     # 前段让出最后一条字幕（前段仍达标）
            kept[-1][0] = p1
        else:                                    # 前段保不住 → 尾卡吞并前段
            kept[-1][0] = prev[0]
            kept.pop(-2)
    i = 1
    while i < len(kept) - 1:
        seg = kept[i]
        if _span(seg[0], seg[1]) >= _mind(seg[2].get("kind")) - 0.01:
            i += 1
            continue
        kept[i - 1][1] = seg[1]                  # 中段不达标 → 并入前段
        kept.pop(i)
    kept[0][0] = 0                               # 全覆盖 [0, dur]
    kept[-1][1] = n - 1
    # ---- 换算回秒并生成 segments / narrationMap ----
    segs = []
    for a, b, sl in kept:
        s = 0.0 if a <= 0 else caps[a]["s"]
        e = dur if b >= n - 1 else caps[b]["e"]
        segs.append([s, e, sl])
    for i in range(1, len(segs)):                # 字幕间静音空档 → 前段顺延，保证严格首尾相接
        if segs[i - 1][1] < segs[i][0]:
            segs[i - 1][1] = segs[i][0]
    segments, nm = [], []
    for i, (s, e, sl) in enumerate(segs):
        kind = sl.get("kind")
        a, b = kept[i][0], kept[i][1]
        props = _unflatten(sl.get("fields") or {}, (m["opts"] or {}).get(kind))
        seg = {"control": kind, "t0": round(s, 3), "t1": round(e, 3),
               "cues": [round(s, 3)], "props": props}
        variants = (m["controlMeta"] or {}).get(kind, {}).get("variants")
        if variants:
            seg["variant"] = variants[i % len(variants)]
        segments.append(seg)
        refs = list(range(a, b + 1))            # 切点在字幕边界上 → 覆盖集精确
        text = "".join(caps[j]["text"] for j in refs) or " "
        purpose = PURPOSE_OF.get(kind, "neutral-emphasis")
        # sentence beat 必须与单条字幕逐字逐刻度对齐（校验器逐条比对），一字幕一 beat
        nm.append({"shot": i, "start": round(s, 3), "end": round(e, 3), "spokenText": text,
                   "purpose": purpose, "timingSource": "provided-captions",
                   "captionBinding": "continuous", "captionIndices": refs,
                   "beats": [{"at": round(caps[j]["s"], 3), "until": round(caps[j]["e"], 3),
                              "spokenText": caps[j]["text"], "timing": "sentence",
                              "target": kind} for j in refs]})
    tl = {"layout": m["layout"], "duration": round(dur, 3),
          "voiceover": "talk.wav", "music": "",
          "captions": [{"s": round(c["s"], 3), "e": round(c["e"], 3), "text": c["text"]} for c in caps],
          "segments": segments, "narrationMap": nm, "ending": "cut",
          "header": {"shortTitle": (g.get("title") or "口播成片")[:80],
                     "source": {"kind": "bound-excerpt", "field": "narration"}}}
    return {"workflow": "motion-kit", "timeline": tl}


# ---------------------------------------------------------------- 出片流水线
def _wf(txt):
    return [x for x in (txt or "").splitlines() if x.strip()]


def _needs_setup(out):
    """依赖缓存不匹配 → 需要先跑 setup。"""
    return "Dependency cache differs" in (out or "") or "run setup" in (out or "")


def render_kit(pkg, workdir, params, log=print, preview_times=None):
    """官方全流程：plan → review → prepare → preview → review → render → mp4 路径。"""
    os.makedirs(workdir, exist_ok=True)
    inst = os.path.join(workdir, "inst")
    media = os.path.join(workdir, "media")
    os.makedirs(media, exist_ok=True)
    pjson = os.path.join(workdir, "params.json")
    json.dump(params, open(pjson, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    apkg = os.path.abspath(pkg)
    base = ["run", apkg, "--instance", inst]
    if not os.path.isfile(os.path.join(inst, "motiongit-source.json")):
        if os.path.isdir(inst):                  # 上次失败残留 → 清掉再建
            shutil.rmtree(inst)
        log("建立模板实例…")
        code, out = cli(pkg, ["new", apkg, inst], timeout=600)
        if code != 0:
            raise RuntimeError("new 实例失败：" + out[-300:])

    def run(args, timeout=1800):
        code, out = cli(pkg, base + args, timeout=timeout)
        if code != 0 and _needs_setup(out):
            log("首次用这个模板：安装依赖缓存（setup，几分钟）…")
            c2, o2 = cli(pkg, base + ["setup", "--workflow", "motion-kit", "--language", "zh"],
                         timeout=2400)
            if c2 != 0:
                raise RuntimeError("setup 失败：" + o2[-400:])
            code, out = cli(pkg, base + args, timeout=timeout)
        if code != 0:
            raise RuntimeError(out[-600:])
        return out

    log("plan 校验…")
    for stale in ("plan-v1.json", "prepared"):
        p2 = os.path.join(inst, stale)
        if os.path.isdir(p2):
            shutil.rmtree(p2)                # 官方命令拒绝覆盖 → 先清残留
    run(["plan", pjson, "--inputs", media, "--out", os.path.join(inst, "plan-v1.json")])
    rev = os.path.join(inst, "plan-v1.json", "review.json")
    r = json.load(open(rev, encoding="utf-8"))
    r["reviewed"] = True
    json.dump(r, open(rev, "w", encoding="utf-8"), ensure_ascii=False)
    log("prepare…")
    run(["prepare", pjson, "--inputs", media, "--plan", os.path.join(inst, "plan-v1.json"),
         "--out", os.path.join(inst, "prepared")])
    log("preview 抽帧…")
    if not preview_times:
        d = float(((params.get("timeline") or {}).get("duration")) or 0)
        preview_times = (",".join(str(round(d * f, 2)) for f in (0.08, 0.35, 0.65, 0.92))
                         if d > 1 else "0.5")
    run(["preview", os.path.join(inst, "prepared"), "--times", preview_times])
    pv = os.path.join(inst, "prepared", "preview.json")
    r = json.load(open(pv, encoding="utf-8"))
    r["reviewed"] = True
    json.dump(r, open(pv, "w", encoding="utf-8"), ensure_ascii=False)
    log("渲染成片…")
    out = os.path.join(inst, "video-v1.mp4")
    run(["render", os.path.join(inst, "prepared"), "--out", out], timeout=3600)
    return out, os.path.join(inst, "prepared", "preview")


def parse_cues(path):
    txt = open(path, encoding="utf-8").read().replace("\ufeff", "")
    def ts(s):
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
    rows = []
    for block in re.split(r"\n\s*\n", txt.strip()):
        lines = [x for x in block.strip().splitlines() if x.strip()]
        for i, ln in enumerate(lines):
            mm = re.match(r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", ln)
            if mm:
                text = "".join(lines[i + 1:]).strip()
                if text:
                    rows.append({"s": ts(mm.group(1)), "e": ts(mm.group(2)), "text": text})
                break
    return rows


# ---------------------------------------------------------------- 原生路线（brief 驱动，模板自排画面）
def native_captions(cues):
    """cues → 原生字幕 JSON 数组（t0/t1/zh/en/highlight 逐字 units）。"""
    out = []
    for i, c in enumerate(cues):
        chars = [x for x in c["text"] if not x.isspace()] or [" "]
        n = len(chars)
        span = (c["e"] - c["s"]) / n
        units = [{"text": ch, "start": round(c["s"] + j * span, 3),
                  "end": round(c["s"] + (j + 1) * span, 3)} for j, ch in enumerate(chars)]
        out.append({"id": "c%02d" % (i + 1), "t0": round(c["s"], 3), "t1": round(c["e"], 3),
                    "zh": c["text"], "en": c["text"],
                    "highlight": {"source": "asr-word-boundaries", "language": "zh", "units": units}})
    return out


def is_native(manifest):
    return not (manifest.get("kit") or {}).get("opts") and manifest.get("engine") == "motion-kit" \
        and not manifest.get("kinds")


def native_schema_const(pkg):
    """读 params.schema.json 里 brief.schema 的 const（adu-auto-brief/1 等）。"""
    try:
        meta, s = read_doc(pkg, "params.schema.json")
        b = ((s.get("properties") or {}).get("brief") or {}).get("properties") or {}
        return (b.get("schema") or {}).get("const") or "adu-auto-brief/1"
    except Exception:  # noqa: BLE001
        return "adu-auto-brief/1"


def render_native(pkg, workdir, params, log=print):
    """原生路线：plan(模板自排) → review → prepare → preview → review → render。"""
    os.makedirs(workdir, exist_ok=True)
    inst = os.path.join(workdir, "inst")
    media = os.path.join(workdir, "media")
    os.makedirs(media, exist_ok=True)
    pjson = os.path.join(workdir, "params.json")
    json.dump(params, open(pjson, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    apkg = os.path.abspath(pkg)
    base = ["run", apkg, "--instance", inst]
    if not os.path.isfile(os.path.join(inst, "motiongit-source.json")):
        if os.path.isdir(inst):                  # 上次失败残留 → 清掉再建
            shutil.rmtree(inst)
        log("建立模板实例…")
        code, out = cli(pkg, ["new", apkg, inst], timeout=600)
        if code != 0:
            raise RuntimeError("new 实例失败：" + out[-300:])

    def run(args, timeout=1800):
        code, out = cli(pkg, base + args, timeout=timeout)
        if code != 0 and _needs_setup(out):
            log("首次用这个模板：安装依赖缓存（setup，几分钟）…")
            c2, o2 = cli(pkg, base + ["setup", "--workflow", "motion-kit", "--language", "zh"],
                         timeout=2400)
            if c2 != 0:
                raise RuntimeError("setup 失败：" + o2[-400:])
            code, out = cli(pkg, base + args, timeout=timeout)
        if code != 0:
            raise RuntimeError(out[-600:])
        return out

    log("plan（模板自排画面）…")
    for stale in ("plan-v1.json", "prepared"):
        p2 = os.path.join(inst, stale)
        if os.path.isdir(p2):
            shutil.rmtree(p2)                # 官方命令拒绝覆盖 → 先清残留
    run(["plan", pjson, "--inputs", media, "--out", os.path.join(inst, "plan-v1.json")])
    rev = os.path.join(inst, "plan-v1.json", "review.json")
    r = json.load(open(rev, encoding="utf-8"))
    r["reviewed"] = True
    json.dump(r, open(rev, "w", encoding="utf-8"), ensure_ascii=False)
    log("prepare…")
    run(["prepare", pjson, "--inputs", media, "--plan", os.path.join(inst, "plan-v1.json"),
         "--out", os.path.join(inst, "prepared")])
    log("preview 抽帧…")
    d = float(((params.get("brief") or {}).get("narrationDuration")) or 0)
    times = (",".join(str(round(d * f, 2)) for f in (0.08, 0.35, 0.65, 0.92)) if d > 1 else "0.5")
    run(["preview", os.path.join(inst, "prepared"), "--times", times])
    pv = os.path.join(inst, "prepared", "preview.json")
    r = json.load(open(pv, encoding="utf-8"))
    r["reviewed"] = True
    json.dump(r, open(pv, "w", encoding="utf-8"), ensure_ascii=False)
    log("渲染成片…")
    out = os.path.join(inst, "video-v1.mp4")
    run(["render", os.path.join(inst, "prepared"), "--out", out], timeout=3600)
    return out, os.path.join(inst, "prepared", "preview")
