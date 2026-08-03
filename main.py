"""wxread 自动阅读器（Playwright 驱动，页面内签名）。

读取环境变量:
  WXREAD_COOKIES  - weread 登录 cookie JSON（含 wr_skey），也可放同目录 wxread_cookies.json
  WXREAD_BOOK     - 阅读器 URL，如 https://weread.qq.com/web/reader/xxxx
  WXREAD_PAGES    - 滚动屏数（可选覆盖；设了 WXREAD_MINUTES 时以分钟为准，留空不生效）
  WXREAD_STEP_MS  - 每屏间隔毫秒（默认 3000）
  WXREAD_MINUTES  - 阅读总时长（分钟）；未设 WXREAD_PAGES 时按此换算屏数（默认 60）
  WXREAD_DIAG     - 设为 1 开启诊断（DOM 快照 + 全请求路径汇总 + console/page 错误），便于排查

原理: 注入 cookie 后打开书，模拟真实阅读（滚动 + 键盘翻页 + 强制页面可见），
      触发 weread 自身签名的 /web/book/read 请求，从而记录阅读进度/时长；
      周期性调用 /web/login/renewal 续期 wr_skey。

FIX 1 (2026-08-02): 阅读逻辑移入 with sync_playwright() 块内，避免浏览器提前关闭崩溃。
FIX 2 (2026-08-02 晚): 强制 visibilityState=visible + 反后台节流参数 + 三重滚动触发。
       实测仍 read 命中 0；进一步日志发现「未检测到阅读容器」——reader 根本没渲染出书，
       故 weread 从未进入阅读态、不发信标。本版加入诊断以定位 reader 不渲染的根因。
"""
import os
import sys
import json
from collections import Counter
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

BOOK_DEFAULT = "https://weread.qq.com/web/reader/2bb32ff0813ab6ffcg014315kbcb32dd02debcbe3365eb9c"

CHROME_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,BackForwardCache",
]

INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
try {
  Object.defineProperty(Document.prototype, 'visibilityState',
    { configurable: true, get() { return 'visible'; } });
  Object.defineProperty(Document.prototype, 'hidden',
    { configurable: true, get() { return false; } });
} catch (e) {}
"""

SCROLL_SCRIPT = """
() => {
  var rc = document.querySelector('.readerContent')
        || document.querySelector('.app_reader')
        || document.querySelector('[class*="reader"]');
  if (rc && (rc.scrollHeight - rc.clientHeight) > 10) {
    rc.scrollTop = (rc.scrollTop || 0) + 600;
    rc.dispatchEvent(new Event('scroll'));
  }
  var el = document.scrollingElement || document.documentElement;
  if (el && (el.scrollHeight - el.clientHeight) > 10) {
    el.scrollTop = (el.scrollTop || 0) + 600;
  }
  window.dispatchEvent(new Event('scroll'));
  document.dispatchEvent(new Event('scroll'));
  return true;
}
"""


def load_cookies():
    raw = os.environ.get("WXREAD_COOKIES")
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            print("ERROR: WXREAD_COOKIES 不是合法 JSON:", e)
            sys.exit(2)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wxread_cookies.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    print("ERROR: 未找到 WXREAD_COOKIES 环境变量或同目录 wxread_cookies.json")
    sys.exit(2)


def main() -> int:
    cookies = load_cookies()
    book_url = os.environ.get("WXREAD_BOOK", BOOK_DEFAULT)
    minutes = os.environ.get("WXREAD_MINUTES")
    pages_env = os.environ.get("WXREAD_PAGES")
    step = int(os.environ.get("WXREAD_STEP_MS", "3000"))
    diag = os.environ.get("WXREAD_DIAG", "") == "1"

    if minutes is not None and minutes.strip() != "":
        pages = max(1, int(round(int(minutes) * 60000 / step)))
    elif pages_env is not None and pages_env.strip() != "":
        pages = int(pages_env)
    else:
        pages = 30

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=CHROME_ARGS)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        context.add_init_script(INIT_SCRIPT)
        context.add_cookies(cookies)
        page = context.new_page()
        page.bring_to_front()

        # 诊断收集器
        req_paths = Counter()
        book_reqs = []
        console_errs = []
        page_errs = []

        if diag:
            page.on("request", lambda r: _rec_req(r, req_paths, book_reqs))
            page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errs.append(str(e)))

        read_hit = {"n": 0}

        def on_req(req):
            if "/web/book/read" in req.url:
                read_hit["n"] += 1

        page.on("request", on_req)

        print(f"[reader] open {book_url}", flush=True)
        page.goto(book_url, wait_until="domcontentloaded", timeout=60000)

        if diag:
            page.wait_for_timeout(8000)
            try:
                snap = page.evaluate(
                    """() => {
                        var sels = ['.readerContent','.app_reader','[class*=\"reader\"]','#app','main','article'];
                        var found = {};
                        sels.forEach(function(s){ found[s] = document.querySelectorAll(s).length; });
                        var txt = (document.body && document.body.innerText) ? document.body.innerText : '';
                        return {
                            url: document.URL,
                            title: document.title,
                            bodyLen: txt.length,
                            sels: found,
                            snippet: txt.slice(0, 400)
                        };
                    }"""
                )
                print(f"[diag] url={snap['url']}", flush=True)
                print(f"[diag] title={snap['title']}", flush=True)
                print(f"[diag] bodyTextLen={snap['bodyLen']}", flush=True)
                print(f"[diag] selectors={json.dumps(snap['sels'], ensure_ascii=False)}", flush=True)
                print(f"[diag] bodySnippet={snap['snippet']!r}", flush=True)
            except Exception as e:
                print(f"[diag] DOM 快照失败: {e}", flush=True)
        else:
            page.wait_for_timeout(4000)

        if "/web/reader/" not in page.url:
            print(f"[reader] ❌ COOKIE_EXPIRED: 打开阅读器后被跳转到 {page.url}（weread 登录态可能已失效）")
            context.close(); browser.close()
            return 1

        # 等待阅读容器（最多 30s，每 3s 重试），记录命中的选择器
        matched = None
        for _ in range(10):
            try:
                matched = page.evaluate(
                    """() => {
                        var cands = ['.readerContent','.app_reader','[class*=\"reader\"]','#app','main','article'];
                        for (var i=0;i<cands.length;i++){ if(document.querySelector(cands[i])) return cands[i]; }
                        return null;
                    }"""
                )
            except Exception:
                matched = None
            if matched:
                break
            page.wait_for_timeout(3000)
        if matched:
            print(f"[reader] ✅ 阅读容器已渲染，选择器命中点: {matched}", flush=True)
        else:
            print("[reader] ⚠️ 未检测到阅读容器（reader 未渲染书内容）", flush=True)

        ck = context.cookies()
        has_skey = any(c["name"] == "wr_skey" and "weread" in c["domain"] for c in ck)
        print(f"[reader] wr_skey 注入: {has_skey}", flush=True)

        for i in range(pages):
            page.mouse.wheel(0, 600)
            try:
                page.keyboard.press("PageDown")
            except Exception:
                pass
            try:
                page.evaluate(SCROLL_SCRIPT)
            except Exception:
                pass
            page.wait_for_timeout(step)
            if i % 30 == 29:
                try:
                    page.evaluate(
                        """async () => {
                            try {
                                await fetch('/web/login/renewal', {
                                    method:'POST',
                                    headers:{'Content-Type':'application/json'},
                                    body: JSON.stringify({rq:'/web/book/read'})
                                });
                            } catch(e) {}
                        }"""
                    )
                except Exception:
                    pass
            if i % 5 == 0:
                print(f"[reader] scroll {i+1}/{pages}, read_hit={read_hit['n']}", flush=True)

        page.wait_for_timeout(2000)
        print(f"[reader] done. read 请求命中数: {read_hit['n']}", flush=True)
        if read_hit["n"] == 0:
            print("[reader] ⚠️ 阅读信标 0 命中：weread 前端仍未触发 /web/book/read")

        if diag:
            print("[diag] 请求路径汇总(top20):", flush=True)
            for p, c in req_paths.most_common(20):
                print(f"    {c:4d}  {p}", flush=True)
            if book_reqs:
                print(f"[diag] /web/book/ 相关请求({len(book_reqs)}):", flush=True)
                for u in book_reqs[:20]:
                    print(f"    {u}", flush=True)
            else:
                print("[diag] 全程无任何 /web/book/ 请求", flush=True)
            if console_errs:
                print(f"[diag] console.error({len(console_errs)}):", flush=True)
                for e in console_errs[:10]:
                    print(f"    {e[:200]}", flush=True)
            if page_errs:
                print(f"[diag] pageerror({len(page_errs)}):", flush=True)
                for e in page_errs[:5]:
                    print(f"    {e[:200]}", flush=True)

        context.close()
        browser.close()
        return 0 if read_hit["n"] > 0 else 1


def _rec_req(req, req_paths: Counter, book_reqs: list):
    try:
        u = urlparse(req.url)
        key = (u.netloc or "?") + (("/" + u.path.lstrip("/").split("/")[0]) if u.path else "")
        req_paths[key] += 1
        if "web/book" in u.path:
            book_reqs.append(req.method + " " + req.url[:160])
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
