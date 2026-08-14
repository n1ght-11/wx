"""wxread 自动阅读器（Playwright 驱动，页面内签名）。

读取环境变量:
  WXREAD_COOKIES  - weread 登录 cookie JSON（含 wr_skey），也可放同目录 wxread_cookies.json
  WXREAD_BOOK     - 阅读器 URL，如 https://weread.qq.com/web/reader/xxxx
  WXREAD_MINUTES  - 阅读时长（分钟），优先于 WXREAD_PAGES
  WXREAD_PAGES    - 滚动屏数（默认 30，当 WXREAD_MINUTES 未设置时生效）
  WXREAD_STEP_MS  - 每屏间隔毫秒（默认 3000）
  GITHUB_OUTPUT   - CI 内由 Actions 注入，结果写入供后续推送步骤读取

原理: 注入 cookie 后打开书，滚动触发 weread 自身签名的 /web/book/read 请求，
      从而记录阅读进度；周期性调用 window.__WRPA__.sr 续期 wr_skey。

v3: 不再只数请求发出数。监听 /web/book/read 的响应体，按 errCode 统计
    「后台真接受数」；结束后拉 readInfo 拿账号今日实际阅读时长，全部写入
    GITHUB_OUTPUT，供 workflow 成功/失败推送使用。
"""
import os
import sys
import json
import random
import re

from playwright.sync_api import sync_playwright

BOOK_DEFAULT = "https://weread.qq.com/web/reader/2bb32ff0813ab6ffcg014315kbcb32dd02debcbe3365eb9c"
STEP_MS_DEFAULT = 3000


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


def write_output(key, value):
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def main() -> int:
    cookies = load_cookies()
    book_url = os.environ.get("WXREAD_BOOK", BOOK_DEFAULT)
    step = int(os.environ.get("WXREAD_STEP_MS", str(STEP_MS_DEFAULT)))

    # WXREAD_MINUTES 优先于 WXREAD_PAGES
    minutes = os.environ.get("WXREAD_MINUTES")
    if minutes:
        minutes = int(minutes)
        pages = int(minutes * 60 * 1000 / step)
    else:
        pages = int(os.environ.get("WXREAD_PAGES", "30"))
        minutes = round(pages * step / 60000)

    print(f"[reader] book_url={book_url} pages={pages} step={step}ms minutes={minutes}", flush=True)
    write_output("minutes", minutes)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=CalculateNativeWinOcclusion",
            ],
        )
        context = browser.new_context()
        # 关键：headless 下 visibilityState 默认 'hidden'，weread 据此停发阅读心跳 / 不累计时长。
        context.add_init_script(
            "Object.defineProperty(Document.prototype, 'visibilityState', { get: () => 'visible' });"
            "Object.defineProperty(Document.prototype, 'hidden', { get: () => false });"
        )
        context.add_cookies(cookies)
        page = context.new_page()

        sent = {"n": 0}          # 发出的 /web/book/read 请求数
        pending = []             # 待解析的响应对象（事件回调里不能调 Playwright API，先攒后处理）
        err_codes = {}           # errCode -> 次数
        first_bodies = []        # 前几条响应体原文（诊断用）
        book_id = {"v": None}    # 从信标里抓 bookId，结束后拉 readInfo 用

        def on_req(req):
            if "/web/book/read" in req.url:
                sent["n"] += 1
                if book_id["v"] is None:
                    try:
                        m = re.search(r"bookId[=\"':\s]+(\w+)", req.post_data or "")
                        if m:
                            book_id["v"] = m.group(1)
                    except Exception:
                        pass

        def on_resp(resp):
            if "/web/book/read" in resp.url:
                pending.append(resp)

        page.on("request", on_req)
        page.on("response", on_resp)

        def drain_responses():
            """在主循环里安全解析响应体：errCode==0 记接受，否则记拒绝。"""
            accepted = 0
            while pending:
                resp = pending.pop()
                try:
                    body = resp.text()
                except Exception as e:
                    err_codes[f"READ_BODY_FAIL({e.__class__.__name__})"] = err_codes.get(f"READ_BODY_FAIL({e.__class__.__name__})", 0) + 1
                    continue
                if len(first_bodies) < 3:
                    first_bodies.append(body[:500])
                code = "?"
                try:
                    j = json.loads(body)
                    code = j.get("errCode", j.get("err", j.get("code", "?")))
                    if code in (0, "0", None, "success", "SUCCESS"):
                        accepted += 1
                except Exception:
                    # 非 JSON：HTTP 状态 2xx 视为接受
                    if 200 <= resp.status < 300:
                        accepted += 1
                    code = f"HTTP_{resp.status}"
                err_codes[str(code)] = err_codes.get(str(code), 0) + 1
            return accepted

        accepted_total = {"n": 0}

        page.goto(book_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        # 早期检测：若打开阅读器后被弹回首页/登录页，说明 cookie 已失效
        if "/web/reader/" not in page.url and "bookId=" not in page.url:
            print(f"[reader] ❌ COOKIE_EXPIRED: 打开阅读器后被跳转到 {page.url}（weread 登录态可能已失效）")
            write_output("status", "cookie_expired_redirect")
            context.close()
            browser.close()
            return 1

        ck = context.cookies()
        has_skey = any(c["name"] == "wr_skey" and "weread" in c["domain"] for c in ck)
        print(f"[reader] wr_skey 注入: {has_skey}", flush=True)

        for i in range(pages):
            page.mouse.wheel(0, 800)
            # 拟人翻页：每 3 屏按一次 PageDown，触发 weread 进度保存（进度保存才发信标）
            if i % 3 == 2:
                try:
                    page.keyboard.press("PageDown")
                except Exception:
                    pass
            # 拟人节奏：基础间隔 + 抖动，每 15 屏插入一次 3~8 秒长停顿，避免被判定为脚本
            wait = step + random.randint(-800, 1200)
            if i % 15 == 14:
                wait += random.randint(3000, 8000)
            page.wait_for_timeout(max(800, wait))
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
            accepted_total["n"] += drain_responses()
            if i % 50 == 0:
                print(f"[reader] scroll {i+1}/{pages}, sent={sent['n']}, accepted={accepted_total['n']}", flush=True)

        page.wait_for_timeout(2000)
        accepted_total["n"] += drain_responses()

        print(f"[reader] done. 请求发送: {sent['n']}, 后台接受: {accepted_total['n']}", flush=True)
        if first_bodies:
            for b in first_bodies:
                print(f"[reader] 响应体样本: {b}", flush=True)
        print(f"[reader] errCode 分布: {err_codes}", flush=True)

        # 结束后拉账号阅读信息（拿今日实际时长），原样 dump 供诊断
        readinfo_raw = ""
        try:
            readinfo_raw = page.evaluate(
                """async (bookId) => {
                    const url = bookId
                        ? `/web/book/readInfo?bookId=${bookId}&finishedBookCount=1&finishedBookIndex=1&finishedDate=1`
                        : '/web/book/readInfo?finishedBookCount=1&finishedBookIndex=1&finishedDate=1';
                    const r = await fetch(url, {credentials: 'include'});
                    return await r.text();
                }""",
                book_id["v"],
            )
            print(f"[reader] readInfo 原始返回: {str(readinfo_raw)[:1500]}", flush=True)
        except Exception as e:
            print(f"[reader] readInfo 拉取失败: {e}", flush=True)

        write_output("sent", sent["n"])
        write_output("accepted", accepted_total["n"])
        write_output("err_codes", json.dumps(err_codes, ensure_ascii=False))
        write_output("readinfo", str(readinfo_raw).replace("\n", " ")[:1500])

        if sent["n"] == 0:
            print("[reader] ❌ COOKIE_EXPIRED: 阅读请求 0 发出，weread 登录态可能已失效。")
            write_output("status", "cookie_expired_no_beacon")
            ok = False
        elif accepted_total["n"] == 0:
            print(f"[reader] ❌ 后端拒绝: 请求发了 {sent['n']} 次但 0 次被接受（errCode 分布见上），登录态大概率已失效。")
            write_output("status", "backend_rejected")
            ok = False
        else:
            write_output("status", "ok")
            ok = True

        context.close()
        browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
