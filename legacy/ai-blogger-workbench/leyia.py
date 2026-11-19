# -*- coding: utf-8 -*-
"""
leyia 生图引擎 — 调用 gpt-image.leyia.fun 的 gpt-image-2 图生图 API
（接入方式移植自 图片流水线/core/generate.py，已实测踩坑版）

完全基于标准库（urllib），不依赖浏览器、不装第三方 HTTP 库。

流程（已实测确认）：
  POST /api/auth/login {username,password} -> {access_token}（JWT，约 7 天过期）
  GET  /api/auth/me -> 余额（energy_balance）
  POST /api/modify (multipart: prompt + 参数 + reference_files) -> {"task_id": "..."}
  GET  /api/task/{task_id} 轮询 -> status: processing / completed
  GET  /api/history/images/{image_id}/original -> 图片字节

实测踩过的坑（已在此处理）：
  - 任务完成响应里的 image_base64 恒为 null，必须走下载接口拿图
  - 下载接口 content-type 标 image/jpeg 但实际字节可能是 PNG，按文件头嗅探
  - 单次图生图（builtin.modify）消耗 4 点能量
  - token 过期后 /api/modify 返回 401（leyia 也可能返回 403），凭账号密码重登续跑
  - 站点拦截 Python-urllib 默认 UA（403），必须带浏览器 UA
  - prompt 超过约 4800 字符 leyia 会静默 500
"""

import io
import os
import json
import time
import threading
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "工作区")
CFG_PATH = os.path.join(WORK, "leyia设置.json")

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DEFAULTS = {
    "base_url": "https://gpt-image.leyia.fun",
    "model": "gpt-image-2",
    "username": "",
    "password": "",
    "token": "",
    "aspect_ratio": "9:16",
    "quality": "medium",        # medium / high
    "output_format": "jpeg",
    "timeout": 600,             # 单张最长等待（秒）；leyia 排队慢时可能超过 4 分钟
    "poll_interval": 6,
}

ENERGY_PER_IMAGE = 4


# ---------------------------------------------------------------- 配置
def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
    keep = dict(DEFAULTS)
    keep.update({k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS})
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, indent=2)
    return keep


def cfg_safe(cfg=None):
    """给前端的安全版：绝不回传密码/token 明文。"""
    cfg = cfg or load_cfg()
    return {
        "base_url": cfg.get("base_url"),
        "model": cfg.get("model"),
        "username": cfg.get("username") or "",
        "has_password": bool(cfg.get("password")),
        "has_token": bool(cfg.get("token")),
        "aspect_ratio": cfg.get("aspect_ratio"),
        "quality": cfg.get("quality"),
    }


# ---------------------------------------------------------------- 传输层（代理/直连双通道自适应）
# 本机实测：leyia 走系统代理偶发极慢/被掐（10054/502），强制直连偶发 SSL 握手超时，
# 单一通道都不稳。策略：记住上次成功的通道优先用；网络层失败自动切另一条再试。
_PREFER_DIRECT = False

def _urlopen(req, timeout):
    """带通道切换的 urlopen。HTTP 层错误（403/422 等）原样抛给业务层处理。"""
    global _PREFER_DIRECT
    channels = [("proxy", urllib.request.build_opener()),
                ("direct", urllib.request.build_opener(urllib.request.ProxyHandler({})))]
    order = channels[::-1] if _PREFER_DIRECT else channels
    last_exc = None
    for name, op in order:
        try:
            resp = op.open(req, timeout=timeout)
            _PREFER_DIRECT = (name == "direct")
            return resp
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001  网络层错误，换通道再试
            last_exc = exc
    raise last_exc


def _sniff(path):
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _sniff_bytes(raw):
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return "bin"


def _prep_ref(path, max_side=1536):
    """读参考图并压缩：最长边压到 max_side，透明图存 PNG、不透明存 JPEG。
    本机代理上传大文件偶发被掐（10054/502），压缩后 2MB+ 通常能降到几百 KB。"""
    raw = open(path, "rb").read()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.load()
        if max(im.size) > max_side:
            im = im.convert("RGBA") if im.mode not in ("RGB", "L") else im
            ratio = max_side / float(max(im.size))
            im = im.resize((max(1, int(im.size[0] * ratio)),
                            max(1, int(im.size[1] * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im.convert("RGBA").save(buf, "PNG", optimize=True)
            return buf.getvalue(), "image/png", "ref.png"
        im.convert("RGB").save(buf, "JPEG", quality=88)
        return buf.getvalue(), "image/jpeg", "ref.jpg"
    except Exception:  # noqa: BLE001  # Pillow 缺失/图损坏就走原样
        mime = _sniff(path)
        return raw, mime, os.path.basename(path)


def _post_multipart(url, fields, files, headers, timeout, retries=3):
    """multipart POST，对 5xx 与网络层错误自动重试。"""
    boundary = "----workbuddy" + os.urandom(12).hex()
    buf = io.BytesIO()
    for k, val in fields.items():
        buf.write(("--" + boundary + "\r\n").encode())
        buf.write(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode())
        buf.write(str(val).encode("utf-8"))
        buf.write(b"\r\n")
    for name, fname, data, mime in files:
        buf.write(("--" + boundary + "\r\n").encode())
        buf.write(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                   % (name, fname)).encode())
        buf.write(('Content-Type: %s\r\n\r\n' % mime).encode())
        buf.write(data)
        buf.write(b"\r\n")
    buf.write(("--" + boundary + "--\r\n").encode())
    hdrs = dict(headers)
    hdrs.setdefault("User-Agent", DEFAULT_UA)
    hdrs["Content-Type"] = "multipart/form-data; boundary=" + boundary
    req = urllib.request.Request(url, data=buf.getvalue(),
                                 headers=hdrs, method="POST")
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = _urlopen(req, timeout)
            return resp.read().decode("utf-8", "replace"), resp.status
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code >= 500 and e.code < 600 and attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(3 * attempt)
                continue
            raise
    raise last_exc


def _get(url, headers, timeout, retries=4):
    hdrs = dict(headers)
    hdrs.setdefault("User-Agent", DEFAULT_UA)
    req = urllib.request.Request(url, headers=hdrs)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = _urlopen(req, timeout)
            return resp.read().decode("utf-8", "replace"), resp.status
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code >= 500 and e.code < 600 and attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(3 * attempt)
                continue
            raise
    raise last_exc


# ---------------------------------------------------------------- 登录 / 余额
def login(username, password, base_url=None, timeout=30):
    """用账号密码登录，返回 access_token（JWT）。"""
    base = (base_url or DEFAULTS["base_url"]).rstrip("/")
    url = base + "/api/auth/login"
    data = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": DEFAULT_UA},
                                 method="POST")
    resp = _urlopen(req, timeout)
    obj = json.loads(resp.read().decode("utf-8", "replace"))
    token = obj.get("access_token") or obj.get("token")
    if not token:
        raise RuntimeError("登录成功但未返回 token，响应：%s" % str(obj)[:200])
    return token


def get_balance(token, base_url=None, timeout=30):
    base = (base_url or DEFAULTS["base_url"]).rstrip("/")
    url = base + "/api/auth/me"
    txt, _ = _get(url, {"Authorization": "Bearer " + token}, timeout)
    obj = json.loads(txt)
    return float((obj.get("user") or {}).get("energy_balance", 0) or 0)


def balance_with_relogin(cfg):
    """拿余额；token 失效就凭账号密码重登一次（并持久化新 token）。"""
    cfg = dict(cfg)
    token = cfg.get("token") or ""
    base = cfg["base_url"].rstrip("/")
    try:
        return get_balance(token, base), cfg
    except Exception:  # noqa: BLE001
        if not (cfg.get("username") and cfg.get("password")):
            raise
        token = login(cfg["username"], cfg["password"], base)
        cfg["token"] = token
        save_cfg(cfg)
        return get_balance(token, base), cfg


# ---------------------------------------------------------------- 单张生图
def generate_image(dst_path, cfg, ref_paths=None, on_log=None, stop_check=None):
    """
    生成一张图，写入 dst_path。refs 最多 4 张（leyia API 上限）。
    ref_paths 为空 = 纯文生图（API 是否接受以实测为准，失败会抛出原始错误）。
    返回 dict：{ok, dst, image_id, bytes, fmt}；token 刷新时已自动落盘。
    """
    cfg = dict(DEFAULTS, **(cfg or {}))
    base = cfg["base_url"].rstrip("/")
    prompt = (cfg.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("生图提示词（prompt）不能为空")
    if len(prompt) > 4800:
        raise ValueError("提示词过长（%d 字符，上限约 4800，超长 leyia 静默 500）"
                         % len(prompt))
    if not (cfg.get("username") and cfg.get("password")) and not cfg.get("token"):
        raise ValueError("还没配置账号密码，先在隐藏生图页登录一次")

    def log(m, lvl="info"):
        if on_log:
            on_log(m, lvl)

    # token 为空但有账号密码 -> 先登录
    token = cfg.get("token") or ""
    if not token:
        token = login(cfg["username"], cfg["password"], base)
        cfg["token"] = token
        save_cfg(cfg)

    fields = {
        "prompt": prompt,
        "output_format": cfg.get("output_format", "jpeg"),
        "quality": cfg.get("quality", "medium"),
        "resolution": "standard",
        "aspect_ratio": cfg.get("aspect_ratio", "9:16"),
        "style": "natural",
        "moderation": "low",
        "n": 1,
        "model": cfg.get("model", "gpt-image-2"),
    }

    refs = []
    for rp in (ref_paths or [])[:4]:
        if os.path.isfile(rp):
            data, mime, fname = _prep_ref(rp)
            refs.append(("reference_files", fname, data, mime))
        else:
            log("参考图不存在，跳过：" + rp, "warn")
    if refs:
        log("参考图已备好：%d 张，共 %.0f KB" %
            (len(refs), sum(len(r[2]) for r in refs) / 1024.0))

    timeout = int(cfg.get("timeout", 240))
    poll_interval = max(1, int(cfg.get("poll_interval", 3)))
    h = {"Authorization": "Bearer " + token}
    url = base + "/api/modify"

    # 提交（401/403 时重登续跑一次；上传走代理/直连都偶发被掐，短超时多通道轮换重试）
    try:
        txt, status = _post_multipart(url, fields, refs, h, timeout=90, retries=6)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and cfg.get("username") and cfg.get("password"):
            log("HTTP %d，token 可能过期，重新登录..." % e.code, "warn")
            token = login(cfg["username"], cfg["password"], base)
            cfg["token"] = token
            save_cfg(cfg)
            h = {"Authorization": "Bearer " + token}
            txt, status = _post_multipart(url, fields, refs, h, timeout=90, retries=6)
        else:
            raise
    if status != 200:
        raise RuntimeError("提交生图失败 HTTP %s：%s" % (status, txt[:300]))

    obj = json.loads(txt)
    task_id = (obj.get("task_id") or obj.get("id")
               or (obj.get("data") or {}).get("task_id"))
    if not task_id:
        raise RuntimeError("提交生图未返回 task_id：" + txt[:300])
    log("已提交 task=%s，生成中（约 30-90 秒）..." % task_id)

    # 轮询（网络抖动不杀任务，抖一次等一次，直到超时）
    deadline = time.time() + timeout
    last_status = None
    final = None
    while time.time() < deadline:
        if stop_check and stop_check():
            raise StopIteration("用户停止生图")
        time.sleep(poll_interval)
        try:
            ttxt, _ = _get(base + "/api/task/" + task_id, h, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log("轮询网络抖动（%s），继续等..." % str(exc)[:80], "warn")
            continue
        j = json.loads(ttxt)
        st = j.get("status") or (j.get("data") or {}).get("status")
        last_status = st
        if st == "completed":
            final = j
            break
        if st in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError("生图任务失败：%s"
                               % (j.get("failure_reason") or j.get("error") or "未知原因"))
    if not final:
        raise RuntimeError("生图超时（%ss），最后状态=%s" % (timeout, last_status))

    images = (final.get("result") or {}).get("images") or []
    image_id = (images[0].get("image_id") if images else None) \
        or final.get("image_id") \
        or (final.get("result") or {}).get("image_id")
    if not image_id:
        raise RuntimeError("生图完成但未拿到 image_id：" + str(final)[:300])

    dl_url = base + "/api/history/images/" + image_id + "/original"
    hd = dict(h)
    hd.setdefault("User-Agent", DEFAULT_UA)
    req = urllib.request.Request(dl_url, headers=hd)
    raw = None
    last_exc = None
    for attempt in range(1, 5):
        try:
            raw = _urlopen(req, timeout=timeout).read()
            break
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code >= 500 and attempt < 4:
                time.sleep(2 * attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_exc = e
            if attempt < 4:
                time.sleep(3 * attempt)
                continue
            raise
    if not raw:
        raise RuntimeError("下载结果图失败（%r）" % (last_exc,))

    os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
    with open(dst_path, "wb") as f:
        f.write(raw)
    fmt = _sniff_bytes(raw)
    # leyia 下载接口可能标 jpeg 实际给 png（反之亦然），按真实格式纠正扩展名
    root, ext = os.path.splitext(dst_path)
    true_ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}.get(fmt, ext)
    if ext.lower() != true_ext:
        try:
            os.replace(dst_path, root + true_ext)
            dst_path = root + true_ext
        except OSError:
            pass
    log("生图完成：%d 字节（%s）" % (len(raw), fmt))
    return {"ok": True, "dst": dst_path, "image_id": image_id,
            "bytes": len(raw), "fmt": fmt}
