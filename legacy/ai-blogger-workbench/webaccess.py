# -*- coding: utf-8 -*-
r"""
对标浏览器 · webaccess.py
==========================
把「找对标」这件事真正跑起来：驱动一个**专用的真实 Chrome**（独立 profile +
调试端口），替你去抖音 搜爆款 / 扒对标账号 / 抓单条数据。

这就是 web-access 的思路：不模拟请求、不伪造接口，而是用一个真实的、
（可登录的）浏览器去干活。工作台只是坐在旁边指挥它。

设计要点
--------
* 用的是**独立的 profile**（`工作区\对标浏览器\profile`），不碰你日常那个 Chrome。
* 你只需要**第一次**在这个窗口里扫码登录一次抖音，之后登录态长期保留。
* 干完活只断开连接，**不关闭浏览器**（Chrome 常驻，下次秒开）。
* 只监听/只连接本机 127.0.0.1。

子命令（都支持 `--out 结果文件`）
--------------------------------
  open                     打开对标浏览器（让你登录抖音）
  login                    打开浏览器并直接弹出登录二维码（给你扫码）
  check / status           报告浏览器 / 登录状态
  search  --kw 关键词       关键词搜爆款（默认取 20 条；--sort 0综合/1最热/2最新，--time 0不限/1一天/7一周/180半年）
  user    --url 主页链接     抓对标账号近期作品（可整段分享口令；支持多个 --url）
  extract --url 视频链接     抓单条视频的标题 / 作者 / 数据

「user」的 --url 可以是一行行**原样粘贴的抖音分享口令**，脚本会自己把
`v.douyin.com/xxx` 这类链接抠出来；如果粘的是单条视频链接，会自动跳到该作者主页。

输出：stdout 逐行日志，最后一行 `WA_JSON={...}`；若给了 --out 也会写一份。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# 本机回环一律不走系统代理（否则 127.0.0.1 的 CDP 请求会被代理拦成 502）
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("EMOTIONCUT_BLOGGER_WORK_ROOT", os.path.join(HERE, "工作区"))
WA_DIR = os.path.join(WORK, "对标浏览器")
PROFILE = os.path.join(WA_DIR, "profile")
RESULT_DIR = os.path.join(WORK, "对标库", "_结果")
LOGIN_FLAG = os.path.join(WA_DIR, "登录状态.json")
PORT = 9333
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Windows 上把 Chrome「脱钩」启动：不挂在当前进程/控制台上，
# 这样工作台服务器关掉了、会话结束了，对标浏览器也不会被一起回收。
_DETACHED = 0x00000008 | 0x00000200 if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
_BREAKAWAY = 0x01000000 if os.name == "nt" else 0               # CREATE_BREAKAWAY_FROM_JOB

# 从「分享口令整段文本」里挑抖音链接：这一条是这次的核心修复
URL_RE = re.compile(r"(?:https?://)?(?:v\.|www\.|m\.)?douyin\.com/[^\s，。、；;'\"）)】\]】]+", re.I)


def pick_urls(lines):
    """把用户粘的一行行东西（可能是整段分享口令 / 短链 / 视频链接 / 主页链接）
    统一抠成干净的 URL 列表。"""
    out, seen = [], set()
    for ln in lines or []:
        for m in URL_RE.finditer(ln or ""):
            u = m.group(0).rstrip("，。、,.;:!?)]】）>")
            if not u.lower().startswith("http"):
                u = "https://" + u
            key = u.rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                out.append(u)
    return out

CHROME_CANDIDATES = [
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def log(msg):
    print(msg, flush=True)


def chrome_path():
    for p in CHROME_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return ""


def cdp_alive(port=PORT, timeout=3):
    try:
        d = OPENER.open("http://127.0.0.1:%d/json/version" % port, timeout=timeout)
        return json.loads(d.read().decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001
        return None


def ensure_chrome(visible=True, url="about:blank", wait=30):
    """确保「对标浏览器」在跑；没有就拉起来。返回 (ok, msg)。

    关键：Chrome 用「脱钩」方式启动，常驻不随工作台关闭而消失 ——
    所以正常情况只有第一次需要开这个窗口，之后都复用同一个。
    """
    if cdp_alive():
        return True, "对标浏览器已在运行（复用，不新开窗口）"
    exe = chrome_path()
    if not exe:
        return False, "没找到 Chrome。你的 Google Chrome 装在哪？"
    os.makedirs(PROFILE, exist_ok=True)
    args = [exe,
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=" + PROFILE,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--hide-crash-restore-bubble",
            "--disable-session-crashed-bubble",
            url]
    ok_launch = False
    for flags in (_DETACHED | _BREAKAWAY, _DETACHED, 0):
        try:
            subprocess.Popen(args, creationflags=flags, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ok_launch = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not ok_launch:
        return False, "启动 Chrome 失败（权限或路径问题）"
    for _ in range(wait * 2):
        if cdp_alive():
            return True, "对标浏览器已启动（以后会一直复用这个窗口）"
        time.sleep(0.5)
    return False, "Chrome 起来了但调试端口没通（可能有别的程序占用 9333）"


# ---------------------------------------------------------------- 页面提取
EXTRACT_JS = r"""
() => {
  const out = [], seen = new Set();
  // 页脚/导航里也有 <a href="/video/xxx"> 的 SEO 推荐链接，别把它们当作品卡片
  const BAD = /用户服务协议|隐私政策|友情链接|站点地图|下载抖音|信息举报|加入我们|账号找回|广告投放|联系我们|京ICP/;
  const pick = (a) => {
    const m = (a.getAttribute('href') || '').match(/\/(video|note)\/(\d+)/);
    if (!m) return null;
    if (a.closest('footer, nav, header')) return null;
    const kind = m[1], id = m[2];
    if (seen.has(id)) return null;
    let card = a.closest('li');
    if (!card) { let p = a; for (let i = 0; i < 3 && p.parentElement; i++) { p = p.parentElement; } card = p; }
    let txt = (card ? card.innerText : a.innerText || '').replace(/\s+/g, ' ').trim();
    if (!txt || txt.length < 3 || txt.length > 300) return null;   // 太长说明整块页面都被圈进来了
    if (BAD.test(txt)) return null;
    seen.add(id);
    return { id: id, url: 'https://www.douyin.com/' + kind + '/' + id, txt: txt.slice(0, 260) };
  };
  document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]').forEach(a => { const o = pick(a); if (o) out.push(o); });
  return out;
}
"""


# 抖音是懒加载 + 内部滚动容器：window 滚不动时，找真正能滚的那个元素继续滚
SCROLL_JS = r"""
() => {
  let best = null, bestLeft = 0;
  const all = document.querySelectorAll('div,main,section,ul');
  for (const el of all) {
    const left = el.scrollHeight - el.clientHeight;
    if (left > bestLeft && el.clientHeight > 200) { bestLeft = left; best = el; }
  }
  if (best) { best.scrollTop = best.scrollHeight; }
  window.scrollTo(0, document.body ? document.body.scrollHeight : 0);
  return bestLeft;
}
"""


def _unit(u):
    """把「万 / 亿」归一化。**不收 w/W**——否则「21 WorkBuddy」会被当成「21万」。"""
    u = (u or "").strip()
    return u if u in ("万", "亿") else ""


def parse_card(txt):
    """从卡片文字里拆出 时长 / 作者 / 点赞 / 标题。

    抖音卡片文字有两种排布，**点赞数的位置完全不同**，必须分开认：

    · 搜索结果页：`[合集 ]<时长 mm:ss> <点赞数> <标题…> @作者 <相对时间>`
        例：`00:45 246 40分钟复现15.6的CHARLS 消耗50亿token… @生信笨小孩 4天前`
        → 点赞数是 **时长后面那一格**，绝不能去正文里找数字（正文里的「50亿」是干扰项！）
    · 作者主页：`<点赞数> <标题…>`
        例：`246 40分钟复现…`

    所以顺序是：先尝试「时长 + 点赞」，再退到「开头就是点赞」，
    **不再**用「正文里找够大的数字」那种兜底——那正是「50亿」「21万」这两个 bug 的来源。
    """
    txt = (txt or "").strip()
    author = ""
    m = re.search(r"@([^\s@·|#]{1,24})", txt)
    if m:
        author = m.group(1)

    dur, like, rest = "", "", txt
    # ① 搜索卡片：可选「合集」→ 时长 mm:ss → 点赞数
    m = re.match(r"^(?:合集\s*)?(\d{1,2}:\d{2})\s+(\d+(?:\.\d+)?)([万亿]?)(?=\s|$)", rest)
    if m:
        dur, like = m.group(1), m.group(2) + _unit(m.group(3))
        rest = rest[m.end():]
    else:
        # ② 作者主页卡片：开头的裸数字就是点赞数（后面必须跟空格/结尾，
        #    且不能是 "12:30" 这种时长——靠后面的 lookahead 排除）
        m = re.match(r"^(\d+(?:\.\d+)?)([万亿]?)(?=\s|$)", rest)
        if m:
            like = m.group(1) + _unit(m.group(2))
            rest = rest[m.end():]

    title = rest
    if author:
        title = title.replace("@" + author, " ")
    title = re.sub(r"\d+\s*(天前|小时前|分钟前|秒前)|昨天|前天|刚刚", " ", title)
    title = re.sub(r"[|·]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -—·|:：")
    return {"author": author, "like": like, "dur": dur, "title": title[:80]}


def like_num(s):
    """把「1.2万」「3439」「3.4w」这类点赞文本转成可比较的数字；没有就返回 -1。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([万亿wW]?)", (s or "").strip())
    if not m:
        return -1.0
    try:
        n = float(m.group(1))
    except ValueError:
        return -1.0
    u = m.group(2)
    if u in ("万", "w", "W"):
        n *= 10000
    elif u == "亿":
        n *= 100000000
    return n


def scroll_load(page, limit, rounds=12, wait=1500):
    """反复往下滚（含页面内部滚动容器），直到抓到 limit 条、或连续 3 轮不再增长。"""
    last, stall = -1, 0
    for _ in range(rounds):
        try:
            page.evaluate(SCROLL_JS)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.mouse.wheel(0, 3200)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(wait)
        try:
            n = len(page.evaluate(EXTRACT_JS))
        except Exception:  # noqa: BLE001
            n = 0
        if n >= limit:
            break
        if n <= last:
            stall += 1
            if stall >= 3:
                break
        else:
            stall = 0
        last = n


def is_captcha(page):
    try:
        t = page.title() or ""
        if "验证码" in t or "captcha" in t.lower():
            return True
        body = page.evaluate("() => document.body ? document.body.innerText.slice(0,400) : ''")
        return any(k in (body or "") for k in ("请完成安全验证", "拖动滑块", "验证码中间页"))
    except Exception:  # noqa: BLE001
        return False


def page_error(page):
    """识别「这不是抖音，是被网关/代理拦了」这类错误页。"""
    try:
        t = (page.title() or "")
    except Exception:  # noqa: BLE001
        t = ""
    if re.search(r"\b(502|503|504)\b|Bad Gateway|Service Unavailable|Gateway Time", t, re.I):
        return t.strip()[:60]
    return ""


def needs_login(page):
    """抖音的搜索页/主页 在未登录时会竖一堵「登录后即可搜索」的墙。"""
    try:
        body = page.evaluate("() => document.body ? document.body.innerText.slice(0,1500) : ''")
        return any(k in (body or "") for k in ("登录后即可搜索", "登录后查看更多"))
    except Exception:  # noqa: BLE001
        return False


def has_login_panel(page):
    try:
        return bool(page.evaluate(
            "() => !!document.querySelector('[id^=\"login-full-panel\"], [id^=\"login-panel\"]')"))
    except Exception:  # noqa: BLE001
        return False


def kill_overlays(page):
    try:
        page.evaluate("""() => {
          document.querySelectorAll('[id^="login-full-panel"], [id^="login-panel"], [id^="login-panel-"]').forEach(e => e.remove());
          document.querySelectorAll('div').forEach(d => {
            const s = getComputedStyle(d);
            if (s.position === 'fixed' && parseInt(s.zIndex || 0) >= 500
                && d.offsetHeight > window.innerHeight * 0.7) d.remove();
          });
        }""")
    except Exception:  # noqa: BLE001
        pass


SESSION_COOKIES = ("sessionid", "sessionid_ss", "sid_tt")

# ---------------------------------------------------------------- 支持的平台
# 登录/状态检测是通用的（都是 CDP 读 cookie）；自动搜索/抓取引擎目前只有抖音。
# cookies = 判登录用的会话 cookie 名（匹配到任意一个且非空 → 已登录）。
PLATFORMS = {
    "douyin":   {"name": "抖音",   "home": "https://www.douyin.com/",
                 "login": "https://www.douyin.com/",
                 "domain": "douyin.com",
                 "cookies": ("sessionid", "sessionid_ss", "sid_tt", "sid_guard")},
    "kuaishou": {"name": "快手",   "home": "https://www.kuaishou.com/",
                 "login": "https://www.kuaishou.com/",
                 "domain": "kuaishou.com",
                 "cookies": ("kuaishou.server.webday7_st", "kuaishou.server.web_st",
                             "passToken", "userId")},
    "xhs":      {"name": "小红书", "home": "https://www.xiaohongshu.com/",
                 "login": "https://www.xiaohongshu.com/",
                 "domain": "xiaohongshu.com",
                 "cookies": ("web_session", "web_sessionid")},
    "bilibili": {"name": "B站",    "home": "https://www.bilibili.com/",
                 "login": "https://passport.bilibili.com/login",
                 "domain": "bilibili.com",
                 "cookies": ("SESSDATA", "DedeUserID")},
    "channels": {"name": "视频号", "home": "https://channels.weixin.qq.com/",
                 "login": "https://channels.weixin.qq.com/",
                 "domain": "channels.weixin.qq.com",
                 "cookies": ("sessionid", "ticket", "finder-username", "data_biz_uid")},
}


def get_platform(key):
    """平台配置：名字不对就退回抖音（别让一个错参数把任务打挂）。"""
    return PLATFORMS.get((key or "").strip(), PLATFORMS["douyin"])


def platform_cookies(ctx, plat_key):
    """从对标浏览器里读某平台的 cookie（只读，不导航）。返回 (cookie列表, 错误)。"""
    plat = get_platform(plat_key)
    try:
        cookies = ctx.cookies(plat["home"]) or []
        # 有些站种在父域上，兜底再按 domain 抓一遍
        if not cookies and plat["domain"] != plat["home"].split("//")[-1].strip("/"):
            cookies = ctx.cookies("https://" + plat["domain"] + "/") or []
        return cookies, ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)[:100]


def probe_logged_in(ctx, plat_key):
    """按平台判登录：cookie 名匹配优先；兜底「该域 cookie 足够多」给疑似已登录。"""
    plat = get_platform(plat_key)
    cookies, err = platform_cookies(ctx, plat_key)
    if err:
        return {"logged_in": False, "by": "", "error": err}
    names_hi = [n.lower() for n in plat["cookies"]]
    for c in cookies:
        n = (c.get("name") or "").lower()
        if n in names_hi and (c.get("value") or ""):
            return {"logged_in": True, "by": "cookie:" + n}
    # 不做「cookie 够多就算登录」的兜底 —— 小红书/快手未登录也会撒一堆
    # 防爬 cookie，轻松过线误判成已登录（2026-10-09 实测踩坑）。只认会话 cookie。
    return {"logged_in": False, "by": "none"}


def login_probe(ctx, page):
    """判断抖音登录态：先看 cookie（最准），再看页面元素兜底。"""
    st0 = probe_logged_in(ctx, "douyin")
    if st0.get("logged_in"):
        return {"logged_in": True, "by": st0.get("by", "cookie")}
    try:
        cookies = ctx.cookies("https://www.douyin.com") or []
    except Exception:  # noqa: BLE001
        cookies = []
    for c in cookies:
        n = (c.get("name") or "").lower()
        if n in SESSION_COOKIES and (c.get("value") or ""):
            return {"logged_in": True, "by": "cookie:" + n}
    try:
        st = page.evaluate("""() => {
          const body = document.body ? document.body.innerText.slice(0,1600) : '';
          return {
            wall: /登录后即可搜索|登录后查看更多|扫码登录/.test(body),
            panel: !!document.querySelector('[id^="login-full-panel"], [id^="login-panel"]'),
            avatar: !!document.querySelector('[data-e2e="user-avatar"], img[src*="aweme-avatar"]'),
          };
        }""")
    except Exception:  # noqa: BLE001
        st = {}
    if st.get("avatar") and not st.get("wall"):
        return {"logged_in": True, "by": "avatar"}
    return {"logged_in": False, "by": ("wall" if (st.get("wall") or st.get("panel")) else "none")}


def goto_login(plat, wait=25):
    """把已在跑的「对标浏览器」窗口导航到平台登录页。

    ensure_chrome 复用已开窗口时不会开新 URL —— 所以选了小红书/快手等
    平台点「打开登录页」却没反应的坑就在这。这里强制导航过去，顺手替
    用户点一下「登录」入口。返回 (ok, msg)。
    """
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
            try:
                ctx = b.contexts[0] if b.contexts else b.new_context()
                page = work_page(ctx)
                page.goto(plat["login"], wait_until="domcontentloaded",
                          timeout=wait * 1000)
                page.wait_for_timeout(3500)
                try:
                    page.bring_to_front()      # 把窗口拽到前面，别让用户以为没反应
                except Exception:  # noqa: BLE001
                    pass
                for sel in ('text=登录', 'button:has-text("登录")',
                            'a:has-text("登录")', '[class*="login-btn"]',
                            'text=登录/注册', 'text=扫码登录'):
                    try:
                        el = page.locator(sel).first
                        if el.count() and el.is_visible():
                            el.click(timeout=3000)
                            page.wait_for_timeout(2000)
                            return True, "已替你点了「登录」，扫码就行"
                    except Exception:  # noqa: BLE001
                        continue
                return True, "请在弹到前面的窗口里点「登录」后扫码"
            finally:
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        return False, "导航失败：%s" % str(exc)[:120]


def xhs_page_login():
    """小红书专用核验：游客也会发 web_session cookie（2026-10-09 实测），
    光看 cookie 必误判。到已开的小红书标签页里看 DOM —— 看得到「登录」
    按钮 = 游客。不导航、不新开页。返回 {"guest": bool}。"""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
            try:
                for ctx in b.contexts:
                    for p in ctx.pages:
                        if "xiaohongshu.com" in (p.url or ""):
                            try:
                                st = p.evaluate("""() => {
                                  const btn = Array.from(
                                    document.querySelectorAll('a,button,div[role=button]'))
                                    .find(e => (e.textContent||'').trim()==='登录' && e.offsetParent);
                                  return {guest: !!btn};
                                }""")
                                return st if isinstance(st, dict) else {"guest": True}
                            except Exception:  # noqa: BLE001
                                return {"guest": True, "err": True}
            finally:
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    return {"guest": True, "no_tab": True}   # 没开小红书页 → 按未登录算，宁严勿假


def surface_login(page):
    """打开抖音并把登录二维码弹出来（有登录入口就替用户点一下）。"""
    page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    if has_login_panel(page):
        return True
    # 精选页可能没有独立登录按钮，试导航里的「登录 / 我的」
    for sel in ('[data-e2e="login-button"]', 'button:has-text("登录")',
                'div[role="button"]:has-text("登录")', 'text=登录',
                'a:has-text("我的")', 'text=我的'):
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.click(timeout=3000)
                page.wait_for_timeout(2500)
                if has_login_panel(page):
                    return True
        except Exception:  # noqa: BLE001
            continue
    # 最后一招：去需要登录的搜索页，未登录时那里会竖登录墙
    try:
        page.goto("https://www.douyin.com/search/AI?type=video",
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4500)
    except Exception:  # noqa: BLE001
        pass
    return bool(has_login_panel(page)) or needs_login(page)


def work_page(ctx):
    """要一个干活用的标签页 —— 优先**复用**已有的，别每次都新开。

    顺序：① 上一次留下的抖音频页 → 直接接管
          ② about:blank 空白页
          ③ 复用最后一页（导航它，而不是新开一个）
    """
    pages = list(ctx.pages)
    for p in pages:
        try:
            if "douyin.com" in (p.url or ""):
                return p
        except Exception:  # noqa: BLE001
            continue
    for p in pages:
        try:
            if (p.url or "").startswith("about:blank"):
                return p
        except Exception:  # noqa: BLE001
            pass
    for p in reversed(pages):
        try:
            u = p.url or ""
            if u.startswith(("chrome://", "devtools://", "edge://", "about:")):
                continue
            return p
        except Exception:  # noqa: BLE001
            continue
    if pages:
        return pages[-1]
    return ctx.new_page()


# ---------------------------------------------------------------- 动作
SORT_NAME = {0: "综合排序", 1: "最多点赞", 2: "最新发布"}
TIME_NAME = {0: "不限", 1: "一天内", 7: "一周内", 180: "半年内", 365: "一年内"}


def _search_once(page, kw, limit, sort_type, publish_time):
    url = ("https://www.douyin.com/search/%s?type=video&sort_type=%d&publish_time=%d"
           % (urllib.parse.quote(kw), int(sort_type), int(publish_time)))
    log("  · 打开抖音搜索页：%s" % url)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(8000)
    pe = page_error(page)
    if pe:
        return None, "need_net"
    if is_captcha(page):
        return None, "need_captcha"
    if needs_login(page):
        return None, "need_login"
    scroll_load(page, limit)          # 滚一滚，多加载几条
    raw = page.evaluate(EXTRACT_JS)
    items = []
    for r in raw[:limit]:
        meta = parse_card(r["txt"])
        items.append({"url": r["url"], "id": r["id"], "txt": r["txt"],
                      "author": meta["author"], "like": meta["like"],
                      "dur": meta.get("dur", ""),
                      "title": meta["title"] or r["txt"][:60]})
    return items, page.url


def do_search(page, kw, limit, sort_type=2, publish_time=0):
    """直接走抖音搜索页 URL（`/jingxuan` 那套精选页的搜索框不会跳转，实测无效）。

    sort_type: 0 综合 / 1 最多点赞 / 2 最新发布（默认最新，抓最近正在起量的）
    publish_time: 0 不限 / 1 一天内 / 7 一周内 / 180 半年内
    """
    log("  · 排序=%s，发布时间=%s" % (SORT_NAME.get(int(sort_type), sort_type),
                                     TIME_NAME.get(int(publish_time), publish_time)))
    items, info = _search_once(page, kw, limit, sort_type, publish_time)
    if items is None:
        if info == "need_net":
            log("  ! 打开抖音搜索页失败（本机代理/网络抽风，等一会重试一次）")
        elif info == "need_captcha":
            log("  ! 抖音弹了验证码。请在「对标浏览器」窗口里手动过一下，再重试。")
        elif info == "need_login":
            log("  ! 抖音要登录才能搜。请回工作台点「打开登录页」扫码登录后再来。")
        return None, info
    # 时间筛选太严导致空结果 → 自动放宽到「不限」再搜一次（默认排序仍按你要的）
    if not items and int(publish_time) != 0:
        log("  ! 限定「%s」没搜到，自动放宽到「时间不限」再试一次。" % TIME_NAME.get(int(publish_time), publish_time))
        items, info = _search_once(page, kw, limit, sort_type, 0)
        if items is None:
            return None, info
    if not items:
        log("  ! 搜索页开了但没解析到视频。可能这词的视频结果为空，或页面结构变了。")
    else:
        log("  · 共解析到 %d 条（排序=%s）" % (len(items), SORT_NAME.get(int(sort_type), sort_type)))
    return items, page.url


def page_kind(url):
    u = (url or "").lower()
    if "/user/" in u:
        return "user"
    if "/video/" in u or "/note/" in u:
        return "item"
    return "other"


# 视频/图文页里找「作者主页」入口：优先 data-e2e，其次第一个带文字的 /user/ 链接
AUTHOR_JS = r"""
() => {
  const self = (h) => /\/user\/self/.test(h || '');
  const grab = (el) => {
    if (!el) return '';
    const a = (el.tagName === 'A') ? el : el.querySelector('a[href*="/user/"]');
    const h = a ? (a.getAttribute('href') || '') : '';
    return self(h) ? '' : h;
  };
  for (const sel of ['[data-e2e="user-info"]', '[data-e2e="video-author-name"]',
                     '[data-e2e="user-name"]', '[data-e2e="author-info"]']) {
    const h = grab(document.querySelector(sel));
    if (h) return h;
  }
  const skip = {我的:1, 关注:1, 朋友:1, 消息:1, 通知:1, 投稿:1, 首页:1, 推荐:1};
  for (const a of document.querySelectorAll('a[href*="/user/"]')) {
    const t = (a.innerText || '').replace(/\s+/g, ' ').trim().split(/\n/)[0];
    const h = a.getAttribute('href') || '';
    if (t && !skip[t] && !self(h)) return h;
  }
  return '';
}
"""


def to_home(page, one):
    """把一行输入落到「账号主页」。

    用户可能粘的是：主页链接 / 视频分享口令 / 短链 / 单条视频链接。
    返回 (主页URL, 问题码)：问题码为 "" 表示成功。
    """
    page.goto(one, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    cur = page.url or ""
    if page_error(page):
        return cur, "net"
    if page_kind(cur) == "user":
        return cur, ""
    # 先看看是不是被抖音跳走了（失效链接会跳回首页/精选）
    try:
        href = page.evaluate(AUTHOR_JS)
    except Exception:  # noqa: BLE001
        href = ""
    if not href:
        return cur, ("dead_link" if page_kind(cur) == "other" else "no_author")
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.douyin.com" + href
    try:
        page.goto(href, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
    except Exception:  # noqa: BLE001
        pass
    if page_kind(page.url or "") == "user":
        return page.url, ""
    return href, ""


def do_user(page, lines, limit):
    """抓对标账号主页的近期作品。支持整段分享口令 / 多个账号。"""
    urls = pick_urls(lines)
    if not urls:
        log("  ! 你粘的内容里没找到抖音链接。")
        log("    正确姿势：抖音里进对方主页 → 点「分享」→「复制链接」，")
        log("    把整段口令原样粘进来就行（我能自己挑出链接），一行一个。")
        return None, "no_url"
    log("  · 从你粘的内容里识别出 %d 个链接" % len(urls))
    all_items, notes = [], []
    for one in urls:
        log("  · 定位账号：%s" % one[:70])
        try:
            home, why = to_home(page, one)
        except Exception as exc:  # noqa: BLE001
            log("    ! 打不开：%s" % str(exc)[:70])
            notes.append("打不开这个链接：%s" % one[:40])
            continue
        if is_captcha(page):
            return None, "need_captcha"
        if needs_login(page):
            return None, "need_login"
        if why == "net":
            log("    ! 页面打不开（被本机代理/网关拦回了错误页）。等一会重试一次。")
            return None, "need_net"
        if why == "dead_link":
            log("    ! 这个链接是失效的：抖音直接把它跳回了首页/精选页。")
            log("      请在抖音里重新「分享 → 复制链接」拿一条新鲜的。")
            notes.append("分享链接已失效（被抖音跳回首页），请重新复制一条")
            continue
        if why == "no_author" or page_kind(home) != "user":
            log("    ! 这个页面里没找到作者主页入口（可能视频被删了 / 页面结构变了）。")
            notes.append("没找到作者主页入口：%s" % one[:40])
            continue
        nick = ""
        try:
            nick = page.evaluate(
                "() => (document.querySelector('h1') || {}).innerText || document.title || ''")
        except Exception:  # noqa: BLE001
            pass
        nick = (nick or "").split("-")[0].strip()[:30]
        log("    → 进入主页：%s" % (nick or "(没读到昵称)"))
        # 想给「点赞最高的 N 条」，样本就得比 N 大：先尽量多抓（2 倍，最多 40 条），再挑
        target = min(max(limit, 1) * 2, 40)
        scroll_load(page, target)
        raw = page.evaluate(EXTRACT_JS)
        seen, uniq = set(), []
        for r in raw:
            key = r.get("id") or r.get("url")
            if key in seen:
                continue
            seen.add(key)
            meta = parse_card(r["txt"])
            uniq.append({"url": r["url"], "id": r["id"], "txt": r["txt"],
                         "account": nick, "author": meta["author"] or nick,
                         "like": meta["like"], "dur": meta.get("dur", ""),
                         "title": meta["title"] or r["txt"][:60]})
        uniq.sort(key=lambda x: like_num(x.get("like")), reverse=True)
        log("    · 主页共读到 %d 条，取点赞最高的 %d 条" % (len(uniq), min(len(uniq), limit)))
        if not uniq:
            log("    ! 这个主页没解析到作品（可能是纯图文号 / 主页结构变了）")
            notes.append("「%s」主页没解析到作品" % (nick or one[:20]))
        all_items.extend(uniq[:limit])       # 每个账号各取前 N 条
    if all_items:
        top = max(all_items, key=lambda x: like_num(x.get("like")))
        log("  · 已按点赞从高到低排好序（最高 %s）" % (top.get("like") or "无数据"))
    return all_items, ("ok" if all_items else "empty"), notes


def do_extract(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    if page_error(page):
        return None, "need_net"
    if is_captcha(page):
        return None, "need_captcha"
    info = page.evaluate("""() => {
      const pick = (s) => { const n = document.querySelector(s); return n ? n.innerText.replace(/\\s+/g,' ').trim() : ''; };
      const h1 = pick('h1');
      let author = pick('[data-e2e="video-author-name"]') || pick('[data-e2e="user-name"]');
      if (!author) {
        const skip = {我的:1, 关注:1, 朋友:1, 消息:1, 通知:1, 投稿:1, 首页:1};
        for (const a of document.querySelectorAll('a[href*="/user/"]')) {
          const t = (a.innerText || '').replace(/\\s+/g,' ').trim().split(/\\n/)[0];
          if (t && !skip[t]) { author = t; break; }
        }
      }
      const e = {};
      ['video-player-digg','feed-comment-icon','video-player-collect','video-player-share']
        .forEach(k => { e[k] = pick('[data-e2e="'+k+'"]'); });
      return {title: document.title.replace(/ - 抖音.*$/,''), h1: h1, author: author,
              desc: pick('[data-e2e="video-desc"]'), eng: e};
    }""")
    return info, "ok"


def do_engage(page, urls, wait_ms=4000):
    """批量进视频详情页，抓互动四件套：赞 / 评论 / 收藏 / 转发。

    给「选题调研」的深挖环节用：列表页卡片只有赞，评论互动必须进详情页读。
    返回 (items, notes)——items 里 url 对得上输入；遇到验证码就停，保住已抓到的。
    """
    ENGAGE_JS = r"""() => {
      const pick = (s) => { const n = document.querySelector(s); return n ? n.innerText.replace(/\s+/g,' ').trim() : ''; };
      const e = {};
      ['video-player-digg','feed-comment-icon','video-player-collect','video-player-share']
        .forEach(k => { e[k] = pick('[data-e2e="'+k+'"]'); });
      return {title: (document.querySelector('h1') || {}).innerText || '', eng: e};
    }"""
    items, notes = [], []
    for i, u in enumerate(urls, 1):
        u = (u or "").strip()
        if not u:
            continue
        try:
            page.goto(u, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(wait_ms)
        except Exception as exc:  # noqa: BLE001
            notes.append("打不开 %s：%s" % (u[:50], str(exc)[:60]))
            continue
        if is_captcha(page):
            notes.append("遇到验证码，深挖在第 %d 条停下（前面 %d 条已保存）" % (i, len(items)))
            break
        if needs_login(page):
            notes.append("登录态失效，深挖在第 %d 条停下" % i)
            break
        try:
            info = page.evaluate(ENGAGE_JS)
        except Exception:  # noqa: BLE001
            info = {}
        eng = info.get("eng") or {}
        items.append({"url": u, "title": (info.get("title") or "")[:80],
                      "digg": eng.get("video-player-digg", ""),
                      "comment": eng.get("feed-comment-icon", ""),
                      "collect": eng.get("video-player-collect", ""),
                      "share": eng.get("video-player-share", "")})
        log("  · [%d/%d] %s 赞%s 评%s 藏%s 转%s" % (
            i, len(urls), (info.get("title") or "")[:18],
            eng.get("video-player-digg", "?"), eng.get("feed-comment-icon", "?"),
            eng.get("video-player-collect", "?"), eng.get("video-player-share", "?")))
    return items, notes


# ---------------------------------------------------------------- 非抖音平台搜索（快手/小红书/B站）
PSEARCH_URLS = {
    "bilibili": "https://search.bilibili.com/all?keyword={kw}",
    "xhs":      "https://www.xiaohongshu.com/search_result?keyword={kw}",
    "kuaishou": "https://www.kuaishou.com/search/video?searchKey={kw}",
}

PSEARCH_JS = {
    "bilibili": """
() => Array.from(document.querySelectorAll('.bili-video-card, .video-list-item')).slice(0,90).map(c => {
  const a = c.querySelector('a[href*="/video/BV"]') || c.querySelector('a[href*="bilibili.com/video"]');
  const t = c.querySelector('.bili-video-card__info--tit') || c.querySelector('h3') || c.querySelector('[title]');
  const au = c.querySelector('.bili-video-card__info--author') || c.querySelector('.up-name');
  const du = c.querySelector('.bili-video-card__stats--duration') || c.querySelector('.time');
  const st = c.querySelector('.bili-video-card__stats');
  return {title: t ? (t.getAttribute('title') || t.innerText) : '', author: au ? au.innerText : '',
          dur: du ? du.innerText : '', like: st ? st.innerText : '', url: a ? a.href : ''};
}).filter(x => x.url)
""",
    "xhs": """
() => {
  let out = Array.from(document.querySelectorAll('section.note-item')).slice(0,90).map(c => {
    const a = c.querySelector('a[href*="/explore/"], a[href*="/search_result/"], a.cover, a[href*="/discovery/"]');
    const t = c.querySelector('.title, a.title, .note-text');
    const au = c.querySelector('.name, .author .name');
    const like = c.querySelector('.like-wrapper .count, .count');
    return {title: t ? t.innerText : '', author: au ? au.innerText : '',
            dur: '', like: like ? like.innerText : '', url: a ? a.href : ''};
  }).filter(x => x.url || x.title);
  if(!out.length){   /* 兜底：从笔记锚点反推卡片 */
    out = Array.from(document.querySelectorAll('a[href*="/explore/"], a[href*="/search_result/"]'))
      .slice(0,90).map(a => {
        const c = a.closest('section') || a.parentElement;
        const t = c && (c.querySelector('.title, .note-text') || a);
        const au = c && c.querySelector('.name');
        const like = c && c.querySelector('.count');
        return {title: t ? t.innerText : '', author: au ? au.innerText : '',
                dur: '', like: like ? like.innerText : '', url: a.href};
      }).filter(x => x.title || x.url);
  }
  return out;
}
""",
    "kuaishou": """
() => {
  let out = Array.from(document.querySelectorAll('.card-item, [class*="video-card"], .search-result-card')).slice(0,90).map(c => {
    const a = c.querySelector('a[href*="/short-video/"]') || c.querySelector('a[href]');
    const t = c.querySelector('[class*="title"], .info-title');
    const au = c.querySelector('[class*="author"], [class*="auth-name"]');
    const du = c.querySelector('[class*="duration"], [class*="time"]');
    const like = c.querySelector('[class*="like"], [class*="count"]');
    return {title: t ? t.innerText : '', author: au ? au.innerText : '',
            dur: du ? du.innerText : '', like: like ? like.innerText : '', url: a ? a.href : ''};
  }).filter(x => x.url);
  if(!out.length){   /* 兜底：从视频锚点反推卡片 */
    out = Array.from(document.querySelectorAll('a[href*="/short-video/"]')).slice(0,90).map(a => {
      const c = a.closest('[class*="card"]') || a.closest('div');
      const t = c && (c.querySelector('[class*="title"]') || a);
      const au = c && c.querySelector('[class*="author"], [class*="auth"]');
      return {title: t ? t.innerText : '', author: au ? au.innerText : '',
              dur: '', like: '', url: a.href};
    }).filter(x => x.url);
  }
  return out;
}
""",
}


def do_psearch(ctx, plat, kw, limit):
    """非抖音平台的关键词搜索：开搜索页 → 关弹窗 → 滚动加载 → 按平台选择器抽卡片。

    返回 (items, err)。items 元素与抖音 parse_card 同构：title/author/like/dur/url，
    like 归一化成「数字或 1.2万」这种短文本，按点赞降序。
    """
    if plat not in PSEARCH_URLS:
        return None, ("视频号的搜索引擎还没接（微信把搜索关在自家客户端里，网页端抓不动）。"
                      "快手 / 小红书 / B站 都能用；视频号先当登录管理 + 手粘链接用。")
    name = get_platform(plat)["name"]
    url = PSEARCH_URLS[plat].format(kw=urllib.parse.quote(kw))
    page = work_page(ctx)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:  # noqa: BLE001
        return None, "打开%s搜索页失败：%s" % (name, str(exc)[:80])
    page.wait_for_timeout(4500)
    kill_overlays(page)
    if is_captcha(page):
        return None, "%s 搜索页弹了验证码：去对标浏览器窗口手动过一下滑块，再回来重搜。" % name
    for _ in range(10):
        try:
            n = len(page.evaluate(PSEARCH_JS[plat]))
        except Exception:  # noqa: BLE001
            n = 0
        if n >= limit:
            break
        try:
            page.evaluate(SCROLL_JS)
            page.mouse.wheel(0, 3200)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1800)
    try:
        raw = page.evaluate(PSEARCH_JS[plat])
    except Exception as exc:  # noqa: BLE001
        return None, "%s 页面结构变了，没抽到卡片：%s" % (name, str(exc)[:80])
    items, seen = [], set()
    for r in raw or []:
        u = (r.get("url") or "").split("?")[0]
        title = re.sub(r"\s+", " ", (r.get("title") or "")).strip()
        if not u or not title or u in seen:
            continue
        seen.add(u)
        like_txt = (r.get("like") or "").strip()
        m = re.search(r"(\d+(?:\.\d+)?\s*[万亿wW]?)", like_txt)
        like = re.sub(r"\s+", "", m.group(1)) if m else ""
        dur = (r.get("dur") or "").strip()
        if dur and not re.match(r"^\d{1,2}:\d{2}", dur):
            dur = ""
        items.append({"title": title[:80], "author": (r.get("author") or "").strip()[:24],
                      "like": like, "dur": dur, "url": u})
        if len(items) >= limit:
            break
    items.sort(key=lambda x: like_num(x.get("like")), reverse=True)
    if not items:
        try:
            body = page.evaluate("() => document.body ? document.body.innerText.slice(0,800) : ''")
        except Exception:  # noqa: BLE001
            body = ""
        if any(k in body for k in ("扫码登录", "请登录", "登录后", "二维码",
                                   "登录即可享受", "立即登录")):
            return None, ("%s 没抓到卡片 —— 搜索要先登录。在上方登录卡选好平台点「打开登录页」扫码，"
                          "登好再回来搜（浏览器会记住登录态）。" % name)
        return None, "%s 没抓到卡片（关键词太冷门，或页面结构变了）" % name
    return items, page.url


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["open", "login", "check", "status", "search",
                                    "psearch", "user", "extract", "engage", "close"])
    ap.add_argument("--kw", default="")
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--sort", type=int, default=2, help="0 综合 / 1 最多点赞 / 2 最新发布")
    ap.add_argument("--time", type=int, default=0, help="0 不限 / 1 一天 / 7 一周 / 180 半年")
    ap.add_argument("--plat", default="douyin",
                    help="登录/检测哪个平台：%s" % "/".join(PLATFORMS))
    ap.add_argument("--out", default="")
    ap.add_argument("--visible", action="store_true", default=True)
    args = ap.parse_args()

    for d in (WA_DIR, RESULT_DIR):
        os.makedirs(d, exist_ok=True)

    result = {"ok": False, "cmd": args.cmd, "error": ""}
    try:
        if args.cmd == "search":
            # eMotionCut 桌面版用内置的无界面浏览器抓取，不弹出抖音窗口，也不依赖系统 Python Playwright。
            url = ("http://127.0.0.1:3119/api/creator/douyin-search?q=%s&n=%d" %
                   (urllib.parse.quote(args.kw), args.n))
            data = json.loads(urllib.request.urlopen(url, timeout=100).read().decode("utf-8"))
            items = data.get("result") or []
            result.update(ok=bool(items), kw=args.kw, count=len(items), items=items,
                          url="headless://douyin-search", sort_type=args.sort,
                          publish_time=args.time, sort_name=SORT_NAME.get(args.sort, ""),
                          time_name=TIME_NAME.get(args.time, ""))
            if not items:
                result["error"] = "后台搜索没有抓到作品，稍后重试或更换关键词"
            log("[后台搜爆款] 关键词：%s · 抓到 %d 条" % (args.kw, len(items)))
        elif args.cmd == "open":
            ok, msg = ensure_chrome(visible=True, url="https://www.douyin.com/")
            result.update(ok=ok, msg=msg, alive=bool(cdp_alive()),
                          profile=PROFILE)
            if not ok:
                result["error"] = msg
            log(msg)
            log("  登录一次就行，之后长期有效。")
            log("  这个窗口会常驻，以后所有『去搜 / 去抓』都在它里面复用，不会再新开窗口。")

        elif args.cmd == "login":
            plat = get_platform(args.plat)
            ok, msg = ensure_chrome(visible=True, url=plat["login"])
            result.update(ok=ok, msg=msg, alive=bool(cdp_alive()), profile=PROFILE,
                          platform=args.plat, platform_name=plat["name"])
            if not ok:
                result["error"] = msg
            log(msg)
            if ok and args.plat == "douyin":
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
                    ctx = b.contexts[0] if b.contexts else b.new_context()
                    page = work_page(ctx)
                    try:
                        shown = surface_login(page)
                    except Exception:  # noqa: BLE001
                        shown = False
                    result["login_shown"] = bool(shown)
                    b.close()   # 只断开，窗口留着给你登录
                log("  登录二维码已弹出（在「对标浏览器」窗口里）。" if result.get("login_shown")
                    else "  浏览器已打开，请在那个窗口里点「登录」后扫码。")
            elif ok:
                nav_ok, nav_msg = goto_login(plat)
                result["login_shown"] = bool(nav_ok)
                if not nav_ok:
                    result["error"] = nav_msg
                log("  %s（%s）：%s" % (plat["name"], plat["login"], nav_msg))
            if ok:
                log("  登完不用回来点任何按钮 —— 工作台会自动检测到，状态变绿。")

        elif args.cmd == "close":
            v = cdp_alive()
            if not v:
                result.update(ok=True, msg="对标浏览器本来就没开")
                log("对标浏览器没在跑，不用关。")
            else:
                closed = False
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as pw:
                        b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
                        try:
                            s = b.new_browser_cdp_session()
                            s.send("Browser.close")
                            closed = True
                        finally:
                            try:
                                b.close()
                            except Exception:  # noqa: BLE001
                                pass
                except Exception as exc:  # noqa: BLE001
                    log("  ! 关闭失败：%s" % str(exc)[:80])
                result.update(ok=True, closed=closed,
                              msg="对标浏览器已关闭" if closed else "没能关掉，请手动关那个窗口")
                log(result["msg"])

        elif args.cmd in ("check", "status"):
            plat = get_platform(args.plat)
            v = cdp_alive()
            result.update(ok=bool(v), alive=bool(v),
                          browser_name=(v or {}).get("Browser", ""),
                          profile=PROFILE, logged_in=False, by="",
                          platform=args.plat, platform_name=plat["name"])
            if not v:
                result["msg"] = "对标浏览器还没启动"
                log("对标浏览器没在跑。先点「打开登录页」。")
            else:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
                    ctx = b.contexts[0] if b.contexts else b.new_context()
                    page = work_page(ctx)
                    if args.plat == "douyin":
                        try:
                            page.goto(plat["home"], wait_until="domcontentloaded",
                                      timeout=45000)
                            page.wait_for_timeout(4500)
                        except Exception:  # noqa: BLE001
                            pass
                        probe = login_probe(ctx, page)
                    else:
                        # 其他平台：只读 cookie 判登录，不导航页面（不打断你正在看的号）
                        probe = probe_logged_in(ctx, args.plat)
                    result["logged_in"] = probe["logged_in"]
                    result["by"] = probe["by"]
                    b.close()
                log("%s登录状态：%s" % (plat["name"],
                    ("✅ 已登录（%s）" % result["by"]
                     if result["logged_in"] else "❌ 未登录")))

        else:
            if not args.url and not args.kw:
                result["error"] = "缺参数"
                log("! 缺参数")
            else:
                ok, msg = ensure_chrome(visible=True, url="about:blank")
                log(msg)
                if not ok:
                    result["error"] = msg
                else:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as pw:
                        b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
                        ctx = b.contexts[0] if b.contexts else b.new_context()
                        page = work_page(ctx)
                        try:
                            if args.cmd == "psearch":
                                plat = args.plat if args.plat in PSEARCH_URLS else "bilibili"
                                log("[搜爆款·%s] 关键词：%s" % (get_platform(plat)["name"], args.kw))
                                items, info = do_psearch(ctx, plat, args.kw, args.n)
                                if items is None:
                                    result["error"] = info
                                else:
                                    result.update(ok=True, kw=args.kw, count=len(items),
                                                  items=items, platform=plat,
                                                  platform_name=get_platform(plat)["name"],
                                                  url=info)
                                    log("  · 抓到 %d 条" % len(items))
                            elif args.cmd == "search":
                                log("[搜爆款] 关键词：%s" % args.kw)
                                items, info = do_search(page, args.kw, args.n,
                                                        sort_type=args.sort,
                                                        publish_time=args.time)
                                if items is None:
                                    result["error"] = info
                                    result["need"] = info
                                else:
                                    result.update(ok=True, kw=args.kw, count=len(items),
                                                  items=items, url=info,
                                                  sort_type=args.sort, publish_time=args.time,
                                                  sort_name=SORT_NAME.get(args.sort, ""),
                                                  time_name=TIME_NAME.get(args.time, ""))
                                    log("  · 抓到 %d 条" % len(items))
                            elif args.cmd == "user":
                                log("[扒对标账号] 收到 %d 行输入" % len(args.url))
                                items, info, notes = do_user(page, args.url, args.n)
                                if items is None:
                                    result["error"] = info
                                    result["need"] = info
                                else:
                                    result.update(ok=True, count=len(items), items=items,
                                                  notes=notes)
                                    log("  · 一共抓到 %d 条" % len(items))
                                    if not items and notes:
                                        log("  ! 一条都没抓到。原因：%s" % notes[0])
                            elif args.cmd == "engage":
                                urls = [u for u in args.url if u][:20]
                                log("[互动深挖] %d 条视频详情页" % len(urls))
                                items, notes = do_engage(page, urls)
                                result.update(ok=bool(items), count=len(items),
                                              items=items, notes=notes)
                                log("  · 抓到 %d 条互动数据" % len(items))
                                if notes:
                                    for n in notes:
                                        log("  ! " + n)
                            else:  # extract
                                log("[抓单条] %s" % args.url[0][:70])
                                info, st = do_extract(page, args.url[0])
                                if info is None:
                                    result["error"] = st
                                    result["need"] = st
                                else:
                                    result.update(ok=True, info=info)
                                    log("  · " + (info.get("title") or "")[:60])
                        finally:
                            b.close()  # 只断开，不关浏览器
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        result["error"] = "%s: %s" % (type(exc).__name__, exc)

    out_path = args.out
    if not out_path:
        out_path = os.path.join(RESULT_DIR, "%s_%s.json" % (args.cmd, time.strftime("%Y%m%d_%H%M%S")))
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass
    log("WA_JSON=" + json.dumps(result, ensure_ascii=False))
    log("WA_OUT=" + out_path)


if __name__ == "__main__":
    main()
