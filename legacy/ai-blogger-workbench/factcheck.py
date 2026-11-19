# -*- coding: utf-8 -*-
r"""
事实核实 · factcheck.py
========================
写稿前的最后一道关：**把「待核实」这件事机器干掉，别再甩给用户。**

以前流程的毛病：写稿模型没材料，就被提示词教着往正文里塞
【待核实：xxx】占位符 —— 稿子看着写完了，其实事实是空的。
这条流水线不认这套：

    选题 → 拆检索式 → 真去搜 → 抓原文 → 提炼「已核实事实卡」 → 才准写稿

铁律（写稿模型的约束在这，不在提示词的善意里）：
  * 事实卡里**没有**的东西，不许写进正文 —— 与其写错，不如不写。
  * 每条事实带来源链接与抓取日期，写到 Run 里可回溯。
  * 核实失败不装成功：返回 notes，让界面明说「这条没查到」。

用法（命令行自测）：
    python factcheck.py --topic "月之暗面 Kimi K3.1 Pro 开源" --q "K3.1 Pro 参数规模"
"""
import argparse
import json
import os
import re
import time
import urllib.parse

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

# 搜索源按可用性排队：一个挂了换下一个。
ENGINES = [
    {"name": "必应", "url": "https://cn.bing.com/search?q={kw}&ensearch=0",
     "wait": "#b_results", "timeout": 9000, "js": """
() => Array.from(document.querySelectorAll('#b_results > li.b_algo')).slice(0,10).map(li => {
  const a = li.querySelector('h2 a');
  const p = li.querySelector('.b_caption p, p, .b_lineclamp2');
  return {title: a ? a.innerText.trim() : '', url: a ? a.href : '',
          snippet: p ? p.innerText.replace(/\\s+/g,' ').trim().slice(0,300) : ''};
}).filter(x => x.url && x.title)
"""},
    {"name": "搜狗", "url": "https://www.sogou.com/web?query={kw}",
     "wait": "#main", "timeout": 14000, "js": """
() => Array.from(document.querySelectorAll('.vrwrap, .rb')).slice(0,10).map(li => {
  const a = li.querySelector('h3 a, a[href^="/link?"]');
  const p = li.querySelector('.text-layout, .space-txt, p');
  return {title: a ? a.innerText.trim() : '', url: a ? a.href : '',
          snippet: p ? p.innerText.replace(/\\s+/g,' ').trim().slice(0,300) : ''};
}).filter(x => x.url && x.title)
"""},
    {"name": "DuckDuckGo", "url": "https://html.duckduckgo.com/html/?q={kw}",
     "wait": "#links", "timeout": 12000, "js": """
() => Array.from(document.querySelectorAll('.result')).slice(0,10).map(li => {
  const a = li.querySelector('.result__a');
  const p = li.querySelector('.result__snippet');
  return {title: a ? a.innerText.trim() : '', url: a ? a.href : '',
          snippet: p ? p.innerText.replace(/\\s+/g,' ').trim().slice(0,300) : ''};
}).filter(x => x.url && x.title)
"""},
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 抓正文时跳过的站点：短视频/社区多半要登录，抓回来也是空壳。
# 注意：微信公众号文章（mp.weixin.qq.com）**不在**黑名单里 ——
# 它不用登录就能读，而且科技类稿件往往它写得最全，是主力来源。
SKIP_HOST = re.compile(
    r"(douyin\.com|kuaishou\.com|xiaohongshu\.com|channels\.weixin\.qq\.com|"
    r"bilibili\.com/video|weibo\.com|zhihu\.com/pin|tieba\.baidu\.com)", re.I)

MAX_TEXT = 2600


def _abs(u, base):
    """搜索结果里的 href 常写成相对路径（搜狗的 `/link?url=…` 就是），补成绝对地址。"""
    u = (u or "").strip()
    if not u or u.startswith(("javascript:", "mailto:")):
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        host = urllib.parse.urlparse(base)
        return "%s://%s%s" % (host.scheme or "https", host.netloc, u)
    return ""


class Fetcher:
    """一个复用的无头浏览器：开一次，搜多次，用完关掉。

    自己起独立的 chromium（不是用户日常那个 Chrome），关掉也不影响任何人。
    """

    def __init__(self, headless=True, timeout=30):
        self.headless = headless
        self.timeout = timeout
        self._pw = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright()
        pw = self._pw.__enter__()
        launch = dict(headless=self.headless, args=["--disable-blink-features=AutomationControlled",
                                                    "--no-sandbox", "--disable-gpu"])
        try:
            self._browser = pw.chromium.launch(**launch)
        except Exception:  # 内置包缺失时退回本机 Chrome
            launch["channel"] = "chrome"
            self._browser = pw.chromium.launch(**launch)
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw:
                self._pw.__exit__(*exc)
        except Exception:  # noqa: BLE001
            pass

    def search(self, query, limit=6):
        """真去搜一轮。返回 [{title,url,snippet,engine}]，全部失败返回 []。"""
        page = self._browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
        try:
            for eng in ENGINES:
                try:
                    page.goto(eng["url"].format(kw=urllib.parse.quote(query)),
                              wait_until="domcontentloaded", timeout=eng.get("timeout", 12000))
                except Exception:  # noqa: BLE001
                    continue
                try:
                    page.wait_for_selector(eng["wait"], timeout=5000)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(800)
                try:
                    raw = page.evaluate(eng["js"]) or []
                except Exception:  # noqa: BLE001
                    raw = []
                base = page.url
                rows = []
                for r in raw:
                    u = _abs(r.get("url"), base)
                    if u and str(r.get("title") or "").strip():
                        rows.append({"title": str(r["title"]).strip(), "url": u,
                                     "snippet": str(r.get("snippet") or "")[:300],
                                     "engine": eng["name"]})
                if rows:
                    return rows[:limit]
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        return []

    def text_of(self, url, chars=MAX_TEXT):
        """抓正文纯文本；抓不动返回空串（不抛）。

        搜狗的结果大都是 `/link?url=xxx` 跳转壳 —— goto 会跟着跳到真实站点，
        所以拿正文要拿落地后的页面，别把跳转壳当成内容。
        """
        if SKIP_HOST.search(url or ""):
            return ""
        page = self._browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            page.wait_for_timeout(1200)
            txt = self._body(page)
            if len(txt) < 200:            # 慢半拍的站点给第二次机会
                page.wait_for_timeout(2200)
                txt = self._body(page)
            # 跳转壳看起来像有内容，实际是 sogou 的过渡页，弃掉
            home = urllib.parse.urlparse(page.url).netloc
            if "sogou.com" in home and len(txt) < 1200:
                return ""
            return txt[:chars]
        except Exception:  # noqa: BLE001
            return ""
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _body(page):
        try:
            txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:  # noqa: BLE001
            return ""
        return re.sub(r"\n{2,}", "\n", txt).strip()


# ------------------------------------------------------------------ 检索式提炼
PLAN_SYSTEM = """你是事实核查员。针对一个口播选题，拆出最该上网核实的检索式。
只输出 JSON：{"queries":["检索式1","检索式2"],"questions":["要确认的关键事实1","关键事实2"]}
要求：
- queries 3 到 5 条，每条是能直接丢进搜索引擎的短句（15字内，带具体产品名/版本/日期，别写"最新消息"这种空词）
- questions 3 到 6 条，必须同时覆盖下面**两类**，缺一类等于没核实：
  A 硬事实：参数、价格、日期、版本号、限制条件
  B 可操作事实（重点）：官方入口网址到底叫什么、在产品里要点哪个菜单/按钮、
    支持不支持某个常见工具（如本地运行工具/客户端）、有没有地区或账号门槛
  —— 稿子是要教人手把手操作的，B 类查不清，写稿时就只能靠编，这是最危险的。
- 不要问开放性问题，不要问观点"""


def plan(chat, cfg, topic, context="", timeout=90):
    """让模型拆出检索式与待查问题；失败给一套保底。"""
    fallback = {"queries": [topic[:24]], "questions": []}
    raw = ""
    try:
        raw = chat(cfg, PLAN_SYSTEM,
                   json.dumps({"topic": topic, "context": context[:1200]}, ensure_ascii=False),
                   timeout=timeout, json_mode=True, max_tokens=800)
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        qs = [str(x).strip()[:60] for x in (data.get("queries") or []) if str(x).strip()][:5]
        qn = [str(x).strip()[:160] for x in (data.get("questions") or []) if str(x).strip()][:6]
        if qs:
            return {"queries": qs, "questions": qn}
    except Exception:  # noqa: BLE001
        pass
    return fallback


CARD_SYSTEM = """你是事实核查员，手里只有下面这些刚从网上抓来的原始材料。
按 questions 逐条回答，产出 JSON：
{"facts":[{"question":"原问题","answer":"已查到的明确答案，含数字/日期/入口，不要含糊",
           "source":"来源标题","url":"来源链接","confidence":"high|mid|low","note":"冲突或口径说明"}]}
铁律：
- 材料里**没有**的，answer 一律写 ""（空串），并在 note 写明"材料未覆盖"。绝不许凭印象补。
- 数字必须原样摘录（如 65.7%、3万亿、100万token、$2/$10），不做换算不四舍五入。
- 官方口径与第三方口径冲突时，note 必须点明这是两套账。
- 只输出 JSON。"""


def cards(chat, cfg, questions, materials, timeout=150):
    """把原始材料压成「已核实事实卡」。没问_questions 就返回 []。"""
    if not questions or not materials:
        return []
    payload = {"questions": questions,
               "materials": [{"title": m["title"], "url": m["url"], "text": m["text"][:2400]}
                             for m in materials[:10]]}
    try:
        raw = chat(cfg, CARD_SYSTEM, json.dumps(payload, ensure_ascii=False),
                   timeout=timeout, json_mode=True, max_tokens=2600)
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        out = []
        for f in (data.get("facts") or [])[:12]:
            ans = str(f.get("answer") or "").strip()
            if not ans:
                continue
            out.append({"question": str(f.get("question") or "")[:160], "answer": ans[:600],
                        "source": str(f.get("source") or "")[:120], "url": str(f.get("url") or "")[:300],
                        "confidence": (str(f.get("confidence") or "mid").lower()[:4]),
                        "note": str(f.get("note") or "")[:300]})
        return out
    except Exception:  # noqa: BLE001
        return []


def research(chat, cfg, topic, context="", max_pages=6, log=None, on_progress=None):
    """跑完整条核实流水线。永远不抛异常，最差返回空事实 + 说明。

    返回 {"facts":[...], "materials":[{title,url,text}], "queries":[...],
          "questions":[...], "notes":[...], "ok":bool}
    """
    say = log or (lambda m: None)
    out = {"facts": [], "materials": [], "queries": [], "questions": [],
           "notes": [], "ok": False, "checked_at": time.strftime("%Y-%m-%d %H:%M")}
    try:
        plan_out = plan(chat, cfg, topic, context)
    except Exception as exc:  # noqa: BLE001
        plan_out = {"queries": [topic[:24]], "questions": []}
        out["notes"].append("检索式生成失败：%s" % str(exc)[:80])
    queries, questions = plan_out["queries"], plan_out["questions"]
    out["queries"], out["questions"] = queries, questions
    say("检索式：%s" % " / ".join(queries[:3]))

    materials, seen = [], set()
    try:
        with Fetcher() as fx:
            for q in queries[:5]:
                say("搜索：%s" % q)
                rows = fx.search(q, limit=4)
                if not rows:
                    out["notes"].append("检索式「%s」没有拿到搜索结果" % q)
                    continue
                for r in rows:
                    if len(materials) >= max_pages:
                        break
                    key = r["url"].split("?")[0]
                    if key in seen:
                        continue
                    seen.add(key)
                    txt = fx.text_of(r["url"])
                    if len(txt) < 200:
                        # 正文抓不到时，搜索摘要也能顶一部分，但标 low
                        if not (r.get("snippet") or "").strip():
                            continue
                        txt = r["snippet"]
                    materials.append({"title": r["title"][:120], "url": r["url"],
                                      "text": txt, "engine": r.get("engine", "")})
                if len(materials) >= max_pages:
                    break
    except Exception as exc:  # noqa: BLE001
        out["notes"].append("联网核实中断：%s（%s）" % (str(exc)[:120], type(exc).__name__))

    out["materials"] = materials
    say("抓到 %d 篇来源" % len(materials))
    if not materials:
        out["notes"].append("所有搜索都没有拿到可用来源，本次稿件不含联网核实过的事实")
        return out

    out["facts"] = cards(chat, cfg, questions, materials)
    out["ok"] = bool(out["facts"])
    if not out["facts"]:
        out["notes"].append("来源抓到了，但没能提炼出可引用的明确答案")
    else:
        say("核实出 %d 条事实" % len(out["facts"]))
    return out


def brief(facts, limit=14):
    """把事实卡压成给写稿模型看的短简报（带来源，逼它只能照抄这些）。"""
    lines = []
    for f in facts[:limit]:
        tail = "｜来源：%s" % (f["source"] or f["url"] or "未标注")
        if f.get("note"):
            tail += "｜备注：%s" % f["note"]
        lines.append("· %s → %s %s" % (f["question"], f["answer"], tail))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--q", action="append", default=[], help="额外检索式")
    ap.add_argument("--ctx", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import aiwrite
    cfg = aiwrite.load_cfg()
    res = research(aiwrite.chat, cfg, args.topic, args.ctx, log=lambda m: print("  " + m, flush=True))
    res["topic"] = args.topic
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("已写入 " + args.out)


if __name__ == "__main__":
    main()
