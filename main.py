"""wxread 自动阅读器（Playwright 驱动，页面内签名）。

读取环境变量:
  WXREAD_COOKIES  - weread 登录 cookie JSON（含 wr_skey），也可放同目录 wxread_cookies.json
  WXREAD_BOOK     - 阅读器 URL，如 https://weread.qq.com/web/reader/xxxx
  WXREAD_PAGES    - 滚动屏数（默认 30）
  WXREAD_STEP_MS  - 每屏间隔毫秒（默认 3000）

原理: 注入 cookie 后打开书，滚动触发 weread 自身签名的 /web/book/read 请求，
      从而记录阅读进度；周期性调用 window.__WRPA__.sr 续期 wr_skey。
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
    pages = int(os.environ.get("WXREAD_PAGES", "30"))
    step = int(os.environ.get("WXREAD_STEP_MS", "3000"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

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
        if read_hit["n"] == 0:
            print("[reader] ❌ COOKIE_EXPIRED: 阅读请求 0 命中，weread 登录态可能已失效。")
            print("[reader]    请本地重新运行 python export_cookies.py 导出新 cookie，再用 deploy_push.js 更新 Secret WXREAD_COOKIES，然后手动 Run workflow 验证。")
        context.close()
        browser.close()
        return 0 if read_hit["n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
