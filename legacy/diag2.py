"""diag2: 探测 weread 对 pr 的接受规则，并取毛选真实进度。
不循环、不刷新死循环；逐次发送并打印原始响应。
"""
import config
import copy, json, random, time, hashlib, logging, requests, urllib.parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag2")

KEY = "3c5c8717f3daf09iop3423zafeqoi"
READ_URL = "https://weread.qq.com/web/book/read"
RENEW_URL = "https://weread.qq.com/web/login/renewal"
COOKIE_DATA = {"rq": "%2Fweb%2Fbook%2Fread"}
REQUEST_TIMEOUT = 15
BOOK = "3300024284"


def cal_hash(input_string):
    hash_a = 0x15051505
    hash_b = hash_a
    length = len(input_string)
    index = length - 1
    while index > 0:
        hash_a = 0x7FFFFFFF & (hash_a ^ ord(input_string[index]) << (length - index) % 30)
        hash_b = 0x7FFFFFFF & (hash_b ^ ord(input_string[index - 1]) << index % 30)
        index -= 2
    return hex(hash_a + hash_b)[2:].lower()


def encode_data(data):
    return "&".join(
        f"{k}={urllib.parse.quote(str(data[k]), safe='')}" for k in sorted(data.keys())
    )


def get_wr_skey():
    resp = requests.post(RENEW_URL, headers=config.headers, cookies=config.cookies,
                         data=json.dumps(COOKIE_DATA, separators=(",", ":")), timeout=REQUEST_TIMEOUT)
    for c in resp.headers.get("Set-Cookie", "").split(";"):
        if "wr_skey" in c:
            return c.split("=")[-1][:8]
    return None


# 1) 刷新密钥
new = get_wr_skey()
if new:
    config.cookies["wr_skey"] = new
    log.info("skey refreshed: %s", new)
else:
    log.error("skey refresh FAILED")

# 2) 取毛选真实进度
real = None
real_ch = ("222", 4, 5111)
try:
    r = requests.get("https://weread.qq.com/web/shelf/sync", headers=config.headers,
                     cookies=config.cookies, timeout=15)
    log.info("shelf sync HTTP %s", r.status_code)
    j = r.json()
    log.info("shelf keys: %s", list(j.keys())[:15])
    for b in (j.get("bookProgress") or j.get("books") or []):
        if isinstance(b, dict) and str(b.get("bookId")) == BOOK:
            real = int(b.get("progress") or 0)
            real_ch = (str(b.get("chapterUid") or "222"), int(b.get("chapterIdx") or 4), int(b.get("chapterOffset") or 5111))
            log.info("毛选 real: progress=%s chapterUid=%s chapterIdx=%s readSeconds=%s",
                     real, b.get("chapterUid"), b.get("chapterIdx"), b.get("readSeconds"))
            break
    if real is None:
        log.warning("毛选未在 shelf 中找到")
except Exception as e:
    log.warning("shelf sync EXC: %r", e)


def send(pr, c="222", ci=4, co=5111):
    payload = copy.deepcopy(config.data)
    payload["b"] = BOOK
    payload["c"] = c
    payload["ci"] = ci
    payload["co"] = co
    this = int(time.time())
    payload["ct"] = this
    payload["rt"] = 30
    payload["ts"] = int(this * 1000) + random.randint(0, 1000)
    payload["rn"] = random.randint(0, 1000)
    payload["sg"] = hashlib.sha256(f"{payload['ts']}{payload['rn']}{KEY}".encode()).hexdigest()
    payload["s"] = cal_hash(encode_data(payload))
    payload["pr"] = pr
    resp = requests.post(READ_URL, headers=config.headers, cookies=config.cookies,
                         data=json.dumps(payload, separators=(",", ":")), timeout=REQUEST_TIMEOUT)
    return resp.status_code, resp.text


probes = [1, 10, 30, 50, 70, 90, 99]
if real is not None:
    probes = [real] + probes
    log.info("优先用真实进度 pr=%s 探测", real)
log.info("PROBE pr 列表: %s", probes)
for pr in probes:
    try:
        sc, txt = send(pr, *real_ch)
        log.info("pr=%s -> HTTP %s | %s", pr, sc, txt[:160])
    except Exception as e:
        log.error("pr=%s EXC %r", pr, e)
log.info("DIAG2 DONE")
