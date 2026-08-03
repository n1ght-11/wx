"""wxread 自动阅读器（Playwright 驱动，页面内签名）。

读取环境变量:
  WXREAD_COOKIES  - weread 登录 cookie JSON（含 wr_skey），也可放同目录 wxread_cookies.json
  WXREAD_BOOK     - 阅读器 URL，如 https://weread.qq.com/web/reader/xxxx
  WXREAD_PAGES    - 滚动屏数（可选覆盖；设了 WXREAD_MINUTES 时以分钟为准，留空不生效）
  WXREAD_STEP_MS  - 每屏间隔毫秒（默认 3000）
  WXREAD_MINUTES  - 阅读总时长（分钟）；未设 WXREAD_PAGES 时按此换算屏数（默认 60）

原理: 注入 cookie 后打开书，模拟真实阅读（滚动 + 键盘翻页 + 强制页面可见），
      触发 weread 自身签名的 /web/book/read 请求，从而记录阅读进度/时长；
      周期性调用 window.__WRPA__.sr 续期 wr_skey。

FIX 1 (2026-08-02): 原文件把阅读逻辑写在 `with sync_playwright() as pw:` 块【之外】，
      导致浏览器提前关闭、page.on 抛 TargetClosedError、阅读从未发生。已整体移入 with 块内。

FIX 2 (2026-08-02 晚): 即便浏览器常开、滚动 100 次，5 分钟测试仍 read 命中 0 —— weread 前端
      在 headless 下判定 document.visibilityState==='hidden'，于是节流/抑制了 /web/book/read
      阅读信标（及阅读心跳）。本次修复:
        - add_init_script 覆写 Document.prototype.visibilityState/hidden 恒为 visible；
        - 启动参数加 --disable-background-timer-throttling / --disable-renderer-backgrounding 等；
        - 滚动循环改为「wheel + 键盘 PageDown 翻页 + 主动派发 scroll 事件」三重触发，
          确保 weread 进度保存被真正调用。
"""
import os
import sys
import json

from playwright.sync_api import sync_playwright

BOOK_DEFAULT = "https://weread.qq.com/web/reader/2bb32ff0813ab6ffcg014315kbcb32dd02debcbe3365eb9c"

# 启动参数：去自动化标记 + 反后台节流（headless 默认隐藏页面会被节流，导致阅读信标不发）
CHROME_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,BackForwardCache",
]

# 注入脚本：抹掉 webdriver 标记，并强制 visibilityState 可见（核心修复）
INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
try {
  Object.defineProperty(Document.prototype, 'visibilityState',
    { configurable: true, get() { return 'visible'; } });
  Object.defineProperty(Document.prototype, 'hidden',
    { configurable: true, get() { return false; } });
} catch (e) {}
"""

# 阅读触发脚本：找到真实可滚动容器并滚动 + 主动派发 scroll 事件
SCROLL_SCRIPT = """
() => {
  // 1) 优先滚动 weread 阅读容器
  var rc = document.querySelector('.readerContent')
        || document.querySelector('.app_reader')
        || document.querySelector('[class*="reader"]');
  if (rc && (rc.scrollHeight - rc.clientHeight) > 10) {
    rc.scrollTop = (rc.scrollTop || 0) + 600;
    rc.dispatchEvent(new Event('scroll'));
  }
  // 2) 兜底滚动文档根
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

    # 优先按分钟换算（用户核心诉求：控制阅读总时长）；仅当显式设了 WXREAD_PAGES 时才以屏数覆盖
    if minutes is not None and minutes.strip() != "":
        pages = max(1, int(round(int(minutes) * 60000 / step)))
    elif pages_env is not None and pages_env.strip() != "":
        pages = int(pages_env)
    else:
        pages = 30  # 兜底默认（约 90 秒）；正常由 WXREAD_MINUTES 控制

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=CHROME_ARGS)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        context.add_init_script(INIT_SCRIPT)
        context.add_cookies(cookies)
        page = context.new_page()
        page.bring_to_front()

        # === 以下阅读逻辑必须位于 with 块内部，浏览器才会保持打开 ===
        read_hit = {"n": 0}

        def on_req(req):
            if "/web/book/read" in req.url:
                read_hit["n"] += 1

        page.on("request", on_req)

        print(f"[reader] open {book_url}", flush=True)
        page.goto(book_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        # 早期检测：若打开阅读器后被弹回首页/登录页，说明 cookie 已失效
        if "/web/reader/" not in page.url:
            print(f"[reader] ❌ COOKIE_EXPIRED: 打开阅读器后被跳转到 {page.url}（weread 登录态可能已失效）")
            print("[reader]    请本地重新运行 python export_cookies.py 导出新 cookie，再用 deploy_push.js 更新 Secret WXREAD_COOKIES")
            context.close()
            browser.close()
            return 1

        # 等待阅读容器出现，确认书已真正打开（最多 10s）
        try:
            page.wait_for_selector(".readerContent, [class*='reader'], #app", timeout=10000)
        except Exception:
            print("[reader] ⚠️ 未检测到阅读容器，仍继续滚动尝试")

        ck = context.cookies()
        has_skey = any(c["name"] == "wr_skey" and "weread" in c["domain"] for c in ck)
        print(f"[reader] wr_skey 注入: {has_skey}", flush=True)

        for i in range(pages):
            # 三重触发：真实滚轮 + 键盘翻页 + 主动派发 scroll 事件
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
            # 每 30 屏续期一次 wr_skey（防止 token 过期中断阅读）
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
                                return 'renewal ok';
                            } catch(e) { return 'ERR ' + e; }
                        }"""
                    )
                except Exception:
                    pass
            if i % 5 == 0:
                print(f"[reader] scroll {i+1}/{pages}, read_hit={read_hit['n']}", flush=True)

        page.wait_for_timeout(2000)
        print(f"[reader] done. read 请求命中数: {read_hit['n']}", flush=True)
        if read_hit["n"] == 0:
            print("[reader] ⚠️ 阅读信标 0 命中：weread 前端仍未触发 /web/book/read（非 cookie 问题，需继续排查触发逻辑）")
        context.close()
        browser.close()
        return 0 if read_hit["n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
