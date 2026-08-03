"""Command-line entry point for wxread automation."""

from __future__ import annotations

import logging
import random
import threading

import config
from reader import ReaderEvent, ReaderConfig, run_reading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)-8s - %(message)s",
)
logger = logging.getLogger(__name__)

# 固定只刷毛选（毛泽东选集 全四卷）
MAO_BOOK_ID = "3300024284"
MAO_APP_ID = "wb182564874663h490269584"


def _fetch_blocking(holder: dict) -> None:
    """在子线程里执行联网取进度；主线程用 join(timeout) 兜底，杜绝 DNS/网络卡死。"""
    try:
        import requests

        resp = requests.get(
            "https://weread.qq.com/web/shelf/sync",
            headers=config.headers,
            cookies=config.cookies,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("bookProgress") or []:
            if entry.get("bookId") == MAO_BOOK_ID:
                holder["state"] = {
                    "pr": int(entry.get("progress") or 0),
                    "c": str(entry.get("chapterUid") or "222"),
                    "ci": int(entry.get("chapterIdx") or 4),
                    "co": int(entry.get("chapterOffset") or 5111),
                    "appId": entry.get("appId") or MAO_APP_ID,
                }
                return
    except Exception as exc:  # noqa: BLE001 - 联网失败则兜底，不阻断主流程
        logger.warning("获取毛选实时进度失败，使用快照兜底: %s", exc)
    holder["state"] = None


def fetch_mao_state() -> dict | None:
    """线程硬超时兜底：即使 getaddrinfo/DNS 卡死，主流程最多等 20s 必继续。"""
    holder: dict = {}
    t = threading.Thread(target=_fetch_blocking, args=(holder,), daemon=True)
    t.start()
    t.join(timeout=20)
    if t.is_alive():
        logger.warning("取毛选进度超时(20s)，用快照兜底")
        return None
    return holder.get("state")


def apply_mao_state(state: dict | None) -> None:
    if state is None:
        # 兜底：取不到实时进度时，用伪随机非零 pr，避免 pr=0 导致 weread 返回 {} 死循环
        state = {
            "pr": random.randint(1, 99),
            "c": "222",
            "ci": 4,
            "co": 5111,
            "appId": MAO_APP_ID,
        }
        logger.warning("未取到毛选实时进度，使用伪随机 pr=%s 兜底", state["pr"])
    else:
        # 真实进度理论上非零；仍为 0 则兜底到伪随机，避免 weread 空转
        if not state.get("pr"):
            state["pr"] = random.randint(1, 99)
            logger.warning("毛选实时进度为 0，改用伪随机 pr=%s 兜底", state["pr"])
        state.setdefault("appId", MAO_APP_ID)
    # 固定只刷毛选（原地修改，保证 reader.py 里 `from config import` 的绑定能看到）
    config.book[:] = [MAO_BOOK_ID]
    config.chapter[:] = [state["c"]]
    # 用真实进度/章节覆盖 data 模板（pr 不再写死 74%）
    config.data["b"] = MAO_BOOK_ID
    config.data["appId"] = state["appId"]
    config.data["c"] = state["c"]
    config.data["ci"] = state["ci"]
    config.data["co"] = state["co"]
    config.data["pr"] = state["pr"]
    logger.info(
        "毛选阅读基准 -> pr=%s c=%s ci=%s co=%s",
        state["pr"],
        state["c"],
        state["ci"],
        state["co"],
    )


def log_event(event: ReaderEvent) -> None:
    level = getattr(logging, event.level.upper(), logging.INFO)
    logger.log(level, event.message)


def main() -> int:
    apply_mao_state(fetch_mao_state())
    result = run_reading(ReaderConfig(), progress_callback=log_event)
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
