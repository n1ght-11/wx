"""diag3: 找 shelf 里 progress>0 的书，用真实 pr/章节发一次 read，看 weread 是否返回 succ。
判断 takukaiyo POST 路线是否对整个账户都失效，还是仅对 progress=0 的毛选失效。
"""
import config
import copy, json, random, time, hashlib, logging, requests, urllib.parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag3")

KEY = "3c5c8717f3daf09iop3423zafeqoi"
READ_URL = "https://weread.qq.com/web/book/read"
RENEW_URL = "https://weread.qq.com/web/login/renewal"
COOKIE_DATA = {"rq": "%2Fweb%2Fbook%2Fread"}
REQUEST_TIMEOUT = 15


def cal_hash(s):
    a = 0x15051505; b = a; n = len(s); i = n - 1
    while i > 0:
        a = 0x7FFFFFFF & (a ^ ord(s[i]) << (n - i) % 30)
        b = 0x7FFFFFFF & (b ^ ord(s[i - 1]) << i % 30)
        i -= 2
    return hex(a + b)[2:].lower()


def encode_data(d):
    return "&".join(f"{k}={urllib.parse.quote(str(d[k]), safe='')}" for k in sorted(d.keys()))


def get_wr_skey():
    r = requests.post(RENEW_URL, headers=config.headers, cookies=config.cookies,
                      data=json.dumps(COOKIE_DATA, separators=(",", ":")), timeout=REQUEST_TIMEOUT)
    for c in r.headers.get("Set-Cookie", "").split(";"):
        if "wr_skey" in c:
            return c.split("=")[-1][:8]
    return None


new = get_wr_skey()
if new:
    config.cookies["wr_skey"] = new
    log.info("skey: %s", new)
else:
    log.error("skey FAILED")

# 列出 shelf 里 progress>0 的书
r = requests.get("https://weread.qq.com/web/shelf/sync", headers=config.headers, cookies=config.cookies, timeout=15)
j = r.json()
cands = []
for b in (j.get("bookProgress") or []):
    if isinstance(b, dict):
        pr = int(b.get("progress") or 0)
        cands.append((pr, b.get("bookId"), b.get("chapterUid"), b.get("chapterIdx"), b.get("chapterOffset"), b.get("title")))
cands.sort(reverse=True)
log.info("progress>0 的书(前5): %s", [(c[0], c[1], c[5]) for c in cands[:5]])
log.info("总书数: %d, 有进度的: %d", len(j.get("bookProgress") or []), len(cands))


def send(book, pr, c, ci, co):
    p = copy.deepcopy(config.data)
    p["b"] = book; p["c"] = str(c); p["ci"] = int(ci); p["co"] = int(co)
    t = int(time.time()); p["ct"] = t; p["rt"] = 30
    p["ts"] = int(t * 1000) + random.randint(0, 1000); p["rn"] = random.randint(0, 1000)
    p["sg"] = hashlib.sha256(f"{p['ts']}{p['rn']}{KEY}".encode()).hexdigest()
    p["s"] = cal_hash(encode_data(p)); p["pr"] = pr
    resp = requests.post(READ_URL, headers=config.headers, cookies=config.cookies,
                         data=json.dumps(p, separators=(",", ":")), timeout=REQUEST_TIMEOUT)
    return resp.status_code, resp.text


if cands:
    pr, bid, c, ci, co, title = cands[0]
    log.info("测试有进度的书: %s %s pr=%s c=%s", bid, title, pr, c)
    try:
        sc, txt = send(bid, pr, c, ci, co)
        log.info("RESULT 有进度书 pr=%s -> HTTP %s | %s", pr, sc, txt[:160])
    except Exception as e:
        log.error("EXC %r", e)
else:
    log.warning("没有任何 progress>0 的书，无法测试")

# 复测毛选(pr=0)
try:
    sc, txt = send("3300024284", 0, 222, 4, 5111)
    log.info("RESULT 毛选 pr=0 -> HTTP %s | %s", sc, txt[:160])
except Exception as e:
    log.error("毛选 EXC %r", e)

log.info("DIAG3 DONE")
