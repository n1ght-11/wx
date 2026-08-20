"""wxread 自动阅读器 — 草稿 v4（防卡末页 · 信标停滞跳章）。

在稳定版 v3（响应级 succ:1 统计 + readInfo + GITHUB_OUTPUT 回写）基础上，
新增「防卡末页」机制：会员到期 / 读完一本书时，weread 进度卡在末页、翻页无下一页
→ 阅读信标停发 → 时长封顶（之前实测卡 ~33 分钟）。

本草稿的防卡末页以「信标停滞」为唯一判据：
  连续 N 屏（默认 25）无任何新信标被后台接受 → 判定真卡住 → 主动跳回第一章重置。
（早期版本用「文档滚动位置」判断，但 weread 阅读器滚动在 iframe 内、document.scrollY
恒为 0，导致第 1 屏就误跳章，已废弃。）

其余与稳定版一致：注入 cookie、滚动触发 weread 自身签名信标、周期续期 wr_skey。
"""
import os
import sys
import json
import random
import re

from playwright.sync_api import sync_playwright

BOOK_DEFAULT = "https://weread.qq.com/web/reader/2bb32ff0813ab6ffcg014315kbcb32dd02debcbe3365eb9c"
STEP_MS_DEFAULT = 3000
STALL_SCREENS = 25   # 连续多少屏无新接受信标 → 判定卡住跳章


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


def jump_to_chapter(page, book_url, attempt):
    """卡住时跳回第一章（或轮流 chapterUid 兜底）。返回是否执行了动作。"""
    selectors = [
        ".readerCatalog", "[class*='readerCatalog']",
        "button[aria-label*='目录']", ".catalogButton",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_timeout(1500)
                first = page.query_selector(".chapterItem, [class*='chapterItem']")
                if first:
                    first.click()
                    page.wait_for_timeout(3000)
                    return True
        except Exception:
            continue
    # 兜底：goto 带 chapterUid 轮流，避免反复跳同一章又卡
    uid = 1 + (attempt % 5)
    sep = "&" if "?" in book_url else "?"
    page.goto(f"{book_url}{sep}chapterUid={uid}", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    return True


def main() -> int:
    cookies = load_cookies()
    book_url = os.environ.get("WXREAD_BOOK", BOOK_DEFAULT)
    step = int(os.environ.get("WXREAD_STEP_MS", str(STEP_MS_DEFAULT)))

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
        context.add_init_script(
            "Object.defineProperty(Document.prototype, 'visibilityState', { get: () => 'visible' });"
            "Object.defineProperty(Document.prototype, 'hidden', { get: () => false });"
        )
        context.add_cookies(cookies)
        page = context.new_page()

        sent = {"n": 0}
        pending = []
        err_codes = {}
        first_bodies = []
        book_id = {"v": None}

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
                    if j.get("succ") == 1:
                        accepted += 1
                        code = "succ:1"
                    else:
                        code = j.get("errCode", j.get("err", j.get("code", "?")))
                        if code in (0, "0", None, "success", "SUCCESS"):
                            accepted += 1
                except Exception:
                    if 200 <= resp.status < 300:
                        accepted += 1
                    code = f"HTTP_{resp.status}"
                err_codes[str(code)] = err_codes.get(str(code), 0) + 1
            return accepted

        accepted_total = {"n": 0}

        page.goto(book_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        if "/web/reader/" not in page.url and "bookId=" not in page.url:
            print(f"[reader] ❌ COOKIE_EXPIRED: 打开阅读器后被跳转到 {page.url}（weread 登录态可能已失效）")
            write_output("status", "cookie_expired_redirect")
            context.close()
            browser.close()
            return 1

        # 注：Playwright 导航后 context.cookies() 偶发返回空，改用注入前的原始 cookie 列表判定
        has_skey = any(c.get("name") == "wr_skey" and "weread" in c.get("domain", "") for c in cookies)
        print(f"[reader] wr_skey 注入: {has_skey}", flush=True)

        prev_accepted = 0
        stall = 0
        jump_attempt = 0

        for i in range(pages):
            page.mouse.wheel(0, 800)
            if i % 3 == 2:
                try:
                    page.keyboard.press("PageDown")
                except Exception:
                    pass
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

            # —— v4 防卡末页：以「信标停滞」为判据 ——
            if accepted_total["n"] > prev_accepted:
                prev_accepted = accepted_total["n"]
                stall = 0
            else:
                stall += 1
            if stall >= STALL_SCREENS:
                print(f"[reader] ⚠️ 连续 {stall} 屏无新接受信标，判定卡住，跳章(第 {jump_attempt+1} 次) 防卡末页", flush=True)
                try:
                    jump_to_chapter(page, book_url, jump_attempt)
                    jump_attempt += 1
                    stall = 0
                    prev_accepted = accepted_total["n"]
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"[reader] 跳章失败: {e}", flush=True)

            if i % 50 == 0:
                print(f"[reader] scroll {i+1}/{pages}, sent={sent['n']}, accepted={accepted_total['n']}, stall={stall}", flush=True)

        page.wait_for_timeout(2000)
        accepted_total["n"] += drain_responses()

        print(f"[reader] done. 请求发送: {sent['n']}, 后台接受: {accepted_total['n']}, 跳章次数: {jump_attempt}", flush=True)
        if first_bodies:
            for b in first_bodies:
                print(f"[reader] 响应体样本: {b}", flush=True)
        print(f"[reader] errCode 分布: {err_codes}", flush=True)

        readinfo_raw = ""
        try:
            readinfo_raw = page.evaluate(
                """async (bookId) => {
                    const tryFetch = async (url) => {
                        const r = await fetch(url, {credentials: 'include'});
                        return await r.text();
                    };
                    let t = '';
                    if (bookId) {
                        t = await tryFetch(`/web/book/readInfo?bookId=${bookId}&finishedBookCount=1&finishedBookIndex=1&finishedDate=1`);
                        if (t.includes('-2003')) t = '';
                    }
                    if (!t || t.includes('-2003')) {
                        t = await tryFetch('/web/book/readInfo?finishedBookCount=1&finishedBookIndex=1&finishedDate=1');
                    }
                    return t;
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
            print(f"[reader] ❌ 后端拒绝: 请求发了 {sent['n']} 次但 0 次被接受，登录态大概率已失效。")
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
