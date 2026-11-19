# -*- coding: utf-8 -*-
"""
对标拆解引擎 · bench.py
=======================
把「找对标 → 扒文案 → 拆结构」变成一条命令。

输入：一条抖音/小红书 分享链接（或你手动粘的文案）
输出：一份拆解报告 md，落到 工作区\对标库\

纯本地跑：解析链接走公开通道，视频下载到本地后由本地 whisper 转写，
不登录、不注入 Cookie、不上传你的账号。

用法：
  python bench.py --url "https://v.douyin.com/xxxx/"
  python bench.py --text "把别人的文案整段粘进来"
  python bench.py --url "..." --note "AI 工具赛道，想学他的开头"
  python bench.py --out "E:\\...\\对标库"
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
KOUBAO = os.path.join(PROJ, "全自动生产线", "口播一键成片")
BENCH_DIR = os.path.join(HERE, "工作区", "对标库")
CACHE = os.path.join(HERE, "工作区", "缓存")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- 抓链接
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
UA_DESK = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url, referer="", timeout=25, raw=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_MOBILE if raw else UA_DESK,
        "Referer": referer or "https://www.douyin.com/",
    })
    return _OPENER.open(req, timeout=timeout).read()


def guess_platform(url):
    if "douyin" in url or "iesdouyin" in url:
        return "抖音"
    if "xiaohongshu" in url or "xhslink" in url:
        return "小红书"
    if "bilibili" in url or "b23.tv" in url:
        return "B站"
    return "未知"


def parse_douyin_browser(url, real=""):
    """第三方解析通道挂了就走这条：用对标浏览器（CDP）打开视频页，
    拦 detail 接口拿标题/作者/数据，拦媒体流拿下载地址。需要对标浏览器开着。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        raise RuntimeError("第三方解析通道不可用，且本机没装 playwright。"
                           "先双击工作台里的「对标浏览器」，再重试一次。")
    try:
        from webaccess import PORT as CDP_PORT
    except Exception:  # noqa: BLE001
        CDP_PORT = 9333
    vid_m = re.search(r"/video/(\d+)", real or url)
    target = ("https://www.douyin.com/video/%s" % vid_m.group(1)) if vid_m else url
    detail, media = {}, []
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % CDP_PORT)
        try:
            ctx = b.contexts[0] if b.contexts else b.new_context()
            cookies = ctx.cookies("https://www.douyin.com") or []
            page = ctx.new_page()

            def on_resp(resp):
                u = resp.url
                if "aweme/v1/web/aweme/detail" in u and "detail" not in detail:
                    try:
                        detail.update(resp.json())
                    except Exception:  # noqa: BLE001
                        pass
                if any(k in u for k in ("douyinvod", "mime_type=video", "/video/tos/")):
                    if u not in media:
                        media.append(u)

            page.on("response", on_resp)
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            for sel in ("[data-e2e='guide-close']", ".dy-account-close"):
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=500):
                        el.click(timeout=800)
                except Exception:  # noqa: BLE001
                    pass
            # 让视频播几秒，媒体分片才会过网络层
            page.evaluate("() => { const v = document.querySelector('video'); "
                          "if (v) { v.muted = true; v.play().catch(()=>{}); } }")
            page.wait_for_timeout(8000)
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                b.close()
            except Exception:  # noqa: BLE001
                pass
    item = detail.get("aweme_detail") or {}
    if not item:
        if media:   # 接口没拦到但媒体流有 → 退化成无标题模式
            return {"platform": "抖音", "title": "", "author": "", "desc": "",
                    "cover": "", "duration": 0, "media": media[:3], "kind": "视频",
                    "stats": {}, "link": url,
                    "cookie": "; ".join("%s=%s" % (c["name"], c.get("value", ""))
                                        for c in cookies)}
        raise RuntimeError("浏览器也没解析到这条视频（页面没加载出来？）"
                           "——把文案直接粘进来（「手粘文案」模式），效果一样。")
    stat = item.get("statistics") or {}
    play = ((item.get("video") or {}).get("play_addr") or {}).get("url_list") or []
    return {
        "platform": "抖音",
        "title": (item.get("desc") or "").strip(),
        "author": ((item.get("author") or {}).get("nickname") or ""),
        "desc": (item.get("desc") or "").strip(),
        "cover": ((((item.get("video") or {}).get("cover") or {}).get("url_list")) or [""])[0],
        "duration": int(((item.get("video") or {}).get("duration") or 0) / 1000),
        "media": play[:1] or media[:2],
        "kind": "视频" if (play or media) else "图文",
        "stats": {
            "赞": stat.get("digg_count", 0), "评论": stat.get("comment_count", 0),
            "收藏": stat.get("collect_count", 0), "转发": stat.get("share_count", 0),
        },
        "link": url,
        "cookie": "; ".join("%s=%s" % (c["name"], c.get("value", "")) for c in cookies),
    }


def parse_share(url):
    """把分享链接换成结构化信息。返回 dict 或抛异常。"""
    plat = guess_platform(url)
    # 1) 先跟随短链，把真实地址拿到（顺便识别平台）
    real = url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA_MOBILE})
        real = _OPENER.open(req, timeout=20).geturl() or url
    except Exception:  # noqa: BLE001
        pass
    if real != url:
        plat = guess_platform(real) if guess_platform(real) != "未知" else plat

    if plat == "抖音":
        # 首选：第三方公开通道（快）；挂了就走自己的浏览器（稳）
        api = "https://api.xingzhige.com/API/douyin/?url=" + urllib.parse.quote(url)
        try:
            d = json.loads(http_get(api).decode("utf-8", "ignore"))
            if d.get("code") == 0 and d.get("data"):
                data = d["data"]
                item = data.get("item") or {}
                author = (data.get("author") or {}).get("name", "")
                stat = data.get("stat") or {}
                return {
                    "platform": "抖音",
                    "title": (item.get("title") or "").strip(),
                    "author": author,
                    "desc": (item.get("title") or "").strip(),
                    "cover": item.get("cover", ""),
                    "duration": item.get("duration", 0) or 0,
                    "media": [item.get("ury") or item.get("url") or ""],
                    "kind": "视频" if (item.get("url") or item.get("ury")) else "图文",
                    "stats": {
                        "赞": stat.get("like", 0), "评论": stat.get("comment", 0),
                        "收藏": stat.get("collect", 0), "转发": stat.get("share", 0),
                    },
                    "link": url,
                }
        except Exception:  # noqa: BLE001
            pass
        log("  · 第三方解析通道不可用，切换成走对标浏览器解析（要开着浏览器）…")
        return parse_douyin_browser(url, real)

    if plat == "小红书":
        for api in ("https://api.xingzhige.com/API/xhs/?url=",
                    "https://api.xingzhige.com/API/redbook/?url="):
            try:
                d = json.loads(http_get(api + urllib.parse.quote(url)).decode("utf-8", "ignore"))
            except Exception:  # noqa: BLE001
                continue
            if d.get("code") == 0 and d.get("data"):
                data = d["data"]
                item = data.get("item") or data
                stat = data.get("stat") or {}
                imgs = item.get("images") or item.get("image_list") or []
                if isinstance(imgs, str):
                    imgs = [imgs]
                return {
                    "platform": "小红书",
                    "title": (item.get("title") or data.get("title") or "").strip(),
                    "author": (data.get("author") or {}).get("name", "")
                              or item.get("nickname", ""),
                    "desc": (item.get("desc") or item.get("title") or "").strip(),
                    "cover": (imgs[0] if imgs else ""),
                    "duration": item.get("duration", 0) or 0,
                    "media": imgs,
                    "kind": "视频" if item.get("video") or item.get("url") else "图文",
                    "stats": {
                        "赞": stat.get("like", 0), "评论": stat.get("comment", 0),
                        "收藏": stat.get("collect", 0), "转发": stat.get("share", 0),
                    },
                    "link": url,
                }
        raise RuntimeError("小红书解析通道暂不可用。"
                           "你可以把文案直接粘进来（改用「手粘文案」模式），效果一样。")

    raise RuntimeError("暂时只支持抖音 / 小红书 链接，其他平台请用「手粘文案」模式。")


# ---------------------------------------------------------------- 转写
def transcribe(media_path):
    sys.path.insert(0, KOUBAO)
    import pipeline  # noqa: E402
    txt, words = pipeline.transcribe(media_path, size="small")
    return txt


def download(media_urls, stem, cookie=""):
    os.makedirs(CACHE, exist_ok=True)
    got = []
    for i, u in enumerate(media_urls):
        if not u:
            continue
        ext = "jpg" if ("image" in u or u.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) else "mp4"
        p = os.path.join(CACHE, "%s_%d.%s" % (stem, i, ext))
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA_DESK,
                "Referer": "https://www.douyin.com/",
                **({"Cookie": cookie} if cookie else {}),
            })
            data = _OPENER.open(req, timeout=120).read()
            with open(p, "wb") as f:
                f.write(data)
            got.append(p)
            log("  · 已下载素材 %d（%s）" % (i + 1, _hsize(len(data))))
        except Exception as exc:  # noqa: BLE001
            log("  ! 素材 %d 下载失败：%s" % (i + 1, exc))
    return got


def _hsize(n):
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n


# ---------------------------------------------------------------- 拆解规则
SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")

HOOK_RULES = [
    ("反常识", ["错了", "不是", "别再", "千万别", "其实", "真相", "根本不", "反过来"]),
    ("提问", ["为什么", "怎么", "是不是", "你知道吗", "有没有", "凭什么", "凭啥"]),
    ("利益前置", ["分钟", "搞定", "秒", "免费", "不用", "一条", "三个", "方法", "技巧", "白嫖"]),
    ("身份认同", ["普通人", "小白", "新手", "打工人", "宝妈", "学生", "零基础"]),
    ("悬念", ["最后", "偷偷", "终于", "居然", "没想到", "结果", "我发现"]),
]

CTA_RULES = ["评论区", "关注", "点赞", "收藏", "扣", "领取", "私信", "主页", "链接", "下一期", "记得"]


def split_sentences(text):
    out = []
    for raw in SENT_SPLIT.split(text or ""):
        s = raw.strip()
        if s:
            out.append(s)
    return out


def detect_hook(first_line):
    hits = []
    for name, kws in HOOK_RULES:
        if any(k in first_line for k in kws):
            hits.append(name)
    return hits or ["平铺直叙"]


def slice_structure(sents):
    """把句子均分成 起 / 承 / 转 / 合 四段（不猜语义，只按位置切）。"""
    n = len(sents)
    if n == 0:
        return []
    marks = [("起", 0.0), ("承", 0.25), ("转", 0.5), ("合", 0.8)]
    out = []
    for i, (name, ratio) in enumerate(marks):
        a = int(n * ratio)
        b = int(n * marks[i + 1][1]) if i + 1 < len(marks) else n
        seg = "".join(sents[a:b])
        out.append((name, seg))
    return out


def keyword_hits(text, bank):
    return [k for k in bank if k in (text or "")]


# ---------------------------------------------------------------- 评论区抓取
COMMENT_JS = r"""() => {
  const out = [];
  document.querySelectorAll('[data-e2e="comment-item"]').forEach((it) => {
    const a = it.querySelector('[data-e2e="comment-author"], .author, a[href*="/user/"]');
    const author = ((a && a.innerText) || '').trim().split('\n')[0];
    // 评论正文：从整条 innerText 里剥掉作者行、角标、时间、赞数、操作行
    const raw = ((it.innerText) || '').trim();
    if (!raw) return;
    const drop = /^(作者(赞过)?$|回复$|置顶$|删除$|举报$|展开|收起|来自|[\d秒分钟小时天周月年前]+$|点赞?|踩$)/;
    const body = raw.split('\n')
      .map(s => s.trim())
      .filter(s => s && s !== author && !drop.test(s))
      .join(' ')
      .slice(0, 300);
    if (!body) return;
    // 赞数：优先找点赞计数元素，兜底找纯数字/万的小节点
    let likes = '';
    const lk = it.querySelector('[data-e2e="comment-digg-count"], [data-e2e="comment-item-like"], [class*="digg"], [class*="like"]');
    if (lk) {
      const t = (lk.innerText || '').trim().split('\n')[0];
      if (/^[0-9]+(\.[0-9]+)?[万亿]?$/.test(t)) likes = t;
    }
    if (!likes) {
      for (const sp of it.querySelectorAll('span, div')) {
        const t = (sp.innerText || '').trim();
        if (/^[0-9]+(\.[0-9]+)?[万亿]?$/.test(t) && sp.children.length === 0) { likes = t; break; }
      }
    }
    out.push({author: author.slice(0, 24), text: body, likes: likes});
  });
  return out;
}"""


def _like_num(s):
    s = (s or "").strip().replace("万", "e4").replace("亿", "e8").replace("+", "")
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def fetch_comments(link, limit=60):
    """连对标浏览器抓这条视频的评论区。失败不影响拆解，返回空列表。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        log("  ! 没 playwright，跳过评论抓取")
        return []
    try:
        from webaccess import PORT as CDP_PORT
    except Exception:  # noqa: BLE001
        CDP_PORT = 9333
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % CDP_PORT)
            try:
                ctx = b.contexts[0] if b.contexts else b.new_context()
                page = ctx.new_page()
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(5000)
                    # 视频页右栏一般直接有评论列表；feed 弹层则先点评论图标
                    try:
                        ic = page.locator('[data-e2e="feed-comment-icon"]').first
                        if ic.is_visible(timeout=1200):
                            ic.click(timeout=1500)
                            page.wait_for_timeout(1500)
                    except Exception:  # noqa: BLE001
                        pass
                    # 滚动加载更多评论
                    for _ in range(6):
                        page.evaluate("""() => {
                          document.querySelectorAll('[data-e2e="comment-list"], .comment-mainContent, [class*="commentList"]')
                            .forEach(e => { e.scrollTop = e.scrollHeight; });
                        }""")
                        page.mouse.wheel(0, 1600)
                        page.wait_for_timeout(1100)
                    items = page.evaluate(COMMENT_JS)
                finally:
                    try:
                        page.close()
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass
        # 按点赞降序 + 去重
        seen, uniq = set(), []
        for c in sorted(items, key=lambda x: _like_num(x.get("likes")), reverse=True):
            k = (c.get("text") or "")[:60]
            if k and k not in seen:
                seen.add(k)
                uniq.append(c)
        return uniq[:limit]
    except Exception as exc:  # noqa: BLE001
        log("  ! 评论抓取失败（不影响拆解）：%s" % str(exc)[:100])
        return []


# ---------------------------------------------------------------- 报告
def build_report(info, transcript, note="", comments=None):
    now = time.strftime("%Y-%m-%d %H:%M")
    stats = info.get("stats") or {}
    like = stats.get("赞") or 0
    coll = stats.get("收藏") or 0
    cmt = stats.get("评论") or 0
    shr = stats.get("转发") or 0
    ratio = ("%.2f" % (coll / like)) if like else "-"

    sents = split_sentences(transcript)
    hook_line = (transcript or "")[:80].replace("\n", " ")
    hooks = detect_hook(hook_line)
    struct = slice_structure(sents)
    ctas = keyword_hits(transcript, CTA_RULES)
    kws = keyword_hits(transcript, ["AI", "工具", "提示词", "教程", "免费", "效率", "副业",
                                    "变现", "涨粉", "选题", "文案", "剪辑", "对标", "普通人"])

    # 数据判型
    if like and coll / max(like, 1) >= 0.8:
        genre = "教程型 / 收藏型（观众当资料存，适合做「步骤教学」）"
    elif like and cmt / max(like, 1) >= 0.08:
        genre = "观点型 / 争议型（评论高，观点有抓手，适合做「反常识判断」）"
    elif like and shr / max(like, 1) >= 0.2:
        genre = "情绪型 / 共鸣型（转发高，替观众说了心里话）"
    else:
        genre = "泛流量型（数据偏平，重点看它的选题和开头）"

    lines = []
    A = lines.append
    A("# 对标拆解 · %s" % (info.get("title") or "(无标题)"))
    A("")
    A("> 拆解时间：%s  " % now)
    A("> 平台：%s　作者：%s  " % (info.get("platform", "?"), info.get("author") or "(未知)"))
    A("> 原链接：%s" % info.get("link", ""))
    if note:
        A("> 我的备注：%s" % note)
    A("")
    A("---")
    A("")
    A("## 一、这条为什么值得拆（先看数据）")
    A("")
    A("| 指标 | 数值 |")
    A("|---|---|")
    A("| 点赞 | %s |" % like)
    A("| 评论 | %s |" % cmt)
    A("| 收藏 | %s |" % coll)
    A("| 转发 | %s |" % shr)
    A("| **藏赞比** | **%s** |" % ratio)
    A("")
    A("**判型**：%s" % genre)
    A("> 藏赞比 > 0.8 说明观众在“存资料”，这种最适合你转成工具教学；评论高说明观点有争议，适合做认知层。")
    A("")
    A("## 二、逐字稿（扒文案）")
    A("")
    A(transcript.strip() or "（没抓到口播文本。图文作品没声音，请手动粘文案。）")
    A("")
    A("## 三、前三秒钩子（拆开头）")
    A("")
    A("**原句**：%s…" % hook_line[:60])
    A("")
    A("**判定**：%s" % "、".join(hooks))
    A("")
    A("**可以怎么改**：把它的钩子结构保住，内容换成你的赛道。")
    A("例：`%s` → `（你的领域 + 同样的钩子结构）`" % hook_line[:24])
    A("")
    A("## 四、结构切片（他是怎么排的）")
    A("")
    A("| 段 | 作用 | 原文（摘） |")
    A("|---|---|---|")
    role = {"起": "立人设 / 抛话题", "承": "给例证 / 讲现象",
            "转": "上观点 / 给方法", "合": "收尾 / 引导互动"}
    for name, seg in struct:
        A("| %s | %s | %s |" % (name, role.get(name, ""), (seg[:60] or "—")))
    A("")
    A("## 五、可复用点（能抄的）")
    A("")
    if hooks and hooks != ["平铺直叙"]:
        A("- **开头钩子**：%s 型。抄结构不抄字。" % "、".join(hooks))
    if ctas:
        A("- **收尾引导**：出现 %s。可以把实物换成你的（评论区扣关键词领工具）。" % "、".join(ctas))
    if kws:
        A("- **关键词**：%s。说明这条打的是这些标签，你的选题可以蹭同一批搜索词。" % "、".join(kws[:8]))
    A("- **结构**：上面第四节的 起承转合，就是你写稿时的骨架参照。")
    A("")
    comments = comments or []
    if comments:
        qs = [c for c in comments if ("？" in c["text"] or "?" in c["text"])]
        A("## 六、评论区反复在讨论什么（观众自己打出来的需求）")
        A("")
        A("共抓到 %d 条（按赞排序）；其中 %d 条是提问——**提问就是现成的下一条选题**。" % (len(comments), len(qs)))
        A("")
        A("| 赞 | 评论内容 |")
        A("|---|---|")
        for c in comments[:15]:
            A("| %s | %s |" % (c.get("likes") or "-", c["text"].replace("\n", " ")[:80]))
        if qs:
            A("")
            A("**高频提问**：")
            for c in qs[:8]:
                A("- %s" % c["text"].replace("\n", " ")[:100])
        A("")
        A("> 深挖方法：把这段评论丢给 AI 或点「生成我们的初稿」，让评论需求直接进稿子。")
        A("")
    else:
        A("## 六、评论区（本次没抓到）")
        A("")
        A("> 浏览器没开或加载失败。评论是观众真实需求，下一条选题经常就藏在热评里——重跑一次拆解会再试。")
        A("")
    A("## 七、不能抄的（红线）")
    A("")
    A("- 不要整段搬运文案，平台会判重；只借「结构」和「钩子类型」。")
    A("- 不要照搬他的个人经历 / 数据（你没有那些事，硬编会被评论区拆穿）。")
    A("- 他的粉丝量级和你有差距时，他的“权威感”你复制不了，要靠具体细节补。")
    A("")
    A("## 七、转成我的选题（归位到三支柱）")
    A("")
    A("- **栏目建议**：① AI 工具上手教学 / ② AI 知识·防坑 / ③ 我的开源 skill 实战")
    A("- **改编角度**：同一个话题，你回答“普通人今晚能做什么”，别重复他的结论，给认知增量。")
    A("- **固定收尾**：评论区扣关键词领工具（不站外引流）。")
    A("")
    A("## 九、复制给 AI 深挖（提示词）")
    A("")
    A("```text")
    A("你是短视频对标拆解专家。下面是一条对标博主的完整口播稿，请帮我：")
    A("1) 逐句标注它在做什么（钩子/例证/观点/反驳/收尾）；")
    A("2) 提炼 3 个可直接复用的句式模板；")
    A("3) 按我的账号定位（大白话教普通人了解/用上 AI）改写成 3 个不同角度的选题。")
    A("")
    A("原文：")
    A(transcript.strip()[:3000] if transcript.strip() else "（把文案粘这里）")
    A("```")
    A("")
    A("---")
    A("> 由「AI 博主工作台 · 对标拆解」生成。规则拆解只做结构归位，"
      "更深的内容判断请把上面的提示词丢给你的 AI。")
    A("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", default=BENCH_DIR)
    ap.add_argument("--keep", action="store_true",
                    help="保留下载到本机的视频素材（默认拆完就删，省磁盘）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    info = {"platform": "手动", "title": "", "author": "", "stats": {}, "link": ""}
    transcript = ""
    media = []
    comments = []

    if args.url.strip():
        url = args.url.strip()
        log("[1/4] 解析链接 …")
        info = parse_share(url)
        log("  · 平台 %s / 作者 %s" % (info["platform"], info.get("author") or "?"))
        log("  · 标题 %s" % (info.get("title") or "(无)"))
        st = info.get("stats") or {}
        log("  · 数据 赞%s 评%s 藏%s 转%s" % (st.get("赞", 0), st.get("评论", 0),
                                              st.get("收藏", 0), st.get("转发", 0)))
        log("[2/4] 下载素材 …（下到本机临时目录，拆完自动删）")
        if info.get("kind") == "视频" and info.get("media"):
            media = download(info["media"][:1], "bench_" + time.strftime("%H%M%S"),
                             cookie=info.get("cookie", ""))
        if media and media[0].lower().endswith((".mp4", ".mov", ".mkv")):
            log("[3/4] 本地转写（whisper，可能要 1-2 分钟）…")
            try:
                transcript = transcribe(media[0])
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                log("  ! 转写失败：%s" % exc)
        else:
            log("[3/4] 没拿到视频（可能是图文），跳过转写。")
        if not transcript and info.get("desc"):
            transcript = info["desc"]
            log("  · 用作品描述兜底当文案。")
    elif args.text.strip():
        log("[1/4] 手粘文案模式。")
        transcript = args.text.strip()
        first = transcript.split("\n")[0][:30]
        info["title"] = first or "手粘文案"
    else:
        log("! 要么给 --url，要么给 --text。")
        raise SystemExit(2)

    log("[4/4] 生成拆解报告 …")
    md = build_report(info, transcript, args.note, comments=comments)
    stem = re.sub(r'[\\/:*?"<>|\r\n\t]', "_",
                  "%s_%s" % (info.get("author") or info.get("platform") or "对标",
                             (info.get("title") or "拆解")[:24])).strip("_ .") or "对标拆解"
    path = os.path.join(args.out, "%s_%s.md" % (time.strftime("%Y-%m-%d"), stem))
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    log("")
    log("拆解完成 → %s" % path)

    # 拆完就把下到本机的视频删掉，不然缓存目录会越堆越大
    if media and not args.keep:
        freed = 0
        for m in media:
            try:
                freed += os.path.getsize(m)
                os.remove(m)
            except OSError:
                pass
        if freed:
            log("  · 已清理临时素材，腾出 %s（想留着加 --keep）" % _hsize(freed))
    print("BENCH_OUT=" + path, flush=True)


if __name__ == "__main__":
    main()
