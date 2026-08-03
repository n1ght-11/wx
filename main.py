"""wxread 自动阅读器（Playwright 驱动，页面内签名）。

读取环境变量:
  WXREAD_COOKIES  - weread 登录 cookie JSON（含 wr_skey），也可放同目录 wxread_cookies.json
  WXREAD_BOOK     - 阅读器 URL，如 https://weread.qq.com/web/reader/xxxx
  WXREAD_PAGES    - 滚动屏数（可选覆盖；设了 WXREAD_MINUTES 时以分钟为准，留空不生效）
  WXREAD_STEP_MS  - 每屏间隔毫秒（默认 3000）
  WXREAD_MINUTES  - 阅读总时长（分钟）；未设 WXREAD_PAGES 时按此换算屏数（默认 60）

原理: 注入 cookie 后打开书，滚动触发 weread 自身签名的 /web/book/read 请求，
      从而记录阅读进度；周期性调用 window.__WRPA__.sr 续期 wr_skey。

FIX (2026-08-02): 原文件把阅读逻辑（read_hit / on_req / page.on / goto / 滚动循环 /
readdetail 回查）写在了 `with sync_playwright() as pw:` 块【之外】，导致 with 块退出时
浏览器已被关闭，随后 `page.on("request", on_req)` 抛 TargetClosedError 崩溃、阅读从未发生。
本修复把这些逻辑整体缩进到 with 块【内部】，浏览器在整个阅读过程中保持打开。
"""
import os
import sys
import json

from playwright.sync_api import sync_playwright

BOOK_DEFAULT = "https://weread.qq.com/web/reader/2bb32ff0813ab6ffcg014315kbcb32dd02debcbe3365eb9c"


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
        # 按分钟换算滚动屏数：每分钟 = 60000 / step 屏（四舍五入，保底 1）
        pages = max(1, int(round(int(minutes) * 60000 / step)))
    elif pages_env is not None and pages_env.strip() != "":
        pages = int(pages_env)
    else:
        pages = 30  # 兜底默认（约 90 秒）；正常由 WXREAD_MINUTES 控制

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                # 抹掉 Playwright 自动化标记，避免 weread 阅读计时被风控截断
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        # 进一步抹掉 navigator.webdriver，使 weread 认为这是真实浏览器
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        context.add_cookies(cookies)
        page = context.new_page()

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

        ck = context.cookies()
        has_skey = any(c["name"] == "wr_skey" and "weread" in c["domain"] for c in ck)
        print(f"[reader] wr_skey 注入: {has_skey}", flush=True)

        for i in range(pages):
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(step)
            if i % 10 == 9:
                try:
                    page.evaluate(
                        """async () => {
                            try {
                                const r = await fetch('/web/login/renewal', {
                                    method:'POST',
                                    headers:{'Content-Type':'application/json'},
                                    body: JSON.stringify({rq:'/web/book/read'})
                                });
                                return await r.text();
                            } catch(e) { return 'ERR ' + e; }
                        }"""
                    )
                except Exception:
                    pass
            if i % 5 == 0:
                print(f"[reader] scroll {i+1}/{pages}, read_hit={read_hit['n']}", flush=True)

        page.wait_for_timeout(2000)
        print(f"[reader] done. read 请求命中数: {read_hit['n']}", flush=True)

        # 诊断：回查 weread 实际记录的阅读时长（毫秒），验证计时是否真的生效
        # 若 readingTime 远小于 60*60*1000，说明 weread 仍判定为非真实阅读
        try:
            bid = book_url.split("/reader/")[1].split("#")[0].split("?")[0]
            detail = page.evaluate(
                """async (bookId) => {
                    try {
                        const r = await fetch('/api/book/readdetail?bookId=' + bookId + '&readingDetailType=0');
                        const j = await r.json();
                        return JSON.stringify(j);
                    } catch(e) { return 'ERR ' + e; }
                }""",
                bid,
            )
            print(f"[reader] readdetail(raw): {detail}", flush=True)
        except Exception as e:
            print(f"[reader] readdetail query failed: {e}", flush=True)
        if read_hit["n"] == 0:
            print("[reader] ❌ COOKIE_EXPIRED: 阅读请求 0 命中，weread 登录态可能已失效。")
            print("[reader]    请本地重新运行 python export_cookies.py 导出新 cookie，再用 deploy_push.js 更新 Secret WXREAD_COOKIES，然后手动 Run workflow 验证。")
        context.close()
        browser.close()
        return 0 if read_hit["n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
