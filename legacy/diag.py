"""一次性诊断脚本：对毛选做一次 read POST，打印 weread 原始返回，用于定位卡死原因。"""
import json
import logging
import copy
import hashlib
import time
import random

import config
from reader import (
    ReaderConfig,
    REQUEST_TIMEOUT,
    KEY,
    READ_URL,
    encode_data,
    cal_hash,
    get_wr_skey,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag")

cfg = ReaderConfig(read_num=1)
print("READ_NUM:", cfg.read_num, flush=True)
print("book:", cfg.books, "chapter:", cfg.chapters, flush=True)

nk = get_wr_skey(cfg)
print("new skey:", nk, flush=True)
if nk:
    cfg.cookies["wr_skey"] = nk

payload = copy.deepcopy(config.data)
payload["b"] = cfg.books[0]
payload["c"] = cfg.chapters[0]
this = int(time.time())
payload["ct"] = this
payload["rt"] = 30
payload["ts"] = int(this * 1000) + random.randint(0, 1000)
payload["rn"] = random.randint(0, 1000)
payload["sg"] = hashlib.sha256(f"{payload['ts']}{payload['rn']}{KEY}".encode()).hexdigest()
payload["s"] = cal_hash(encode_data(payload))
print("PAYLOAD:", json.dumps(payload, ensure_ascii=False)[:700], flush=True)

import requests
try:
    resp = requests.post(
        READ_URL,
        headers=cfg.headers,
        cookies=cfg.cookies,
        data=json.dumps(payload, separators=(",", ":")),
        timeout=REQUEST_TIMEOUT,
    )
    print("HTTP", resp.status_code, flush=True)
    print("RESP TEXT:", resp.text[:1000], flush=True)
except Exception as exc:
    print("POST ERROR:", repr(exc)[:300], flush=True)
print("DIAG DONE", flush=True)
