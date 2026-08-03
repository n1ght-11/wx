"""Command-line entry point for wxread automation."""

from __future__ import annotations

import logging

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
# 无法联网取实时进度时使用的兜底快照
MAO_SNAPSHOT = {"pr": 0, "c": "222", "ci": 4, "co": 5111}


def fetch_mao_state() -> dict | None:
    """从 weread 书架同步取毛选实时阅读进度，避免回退用户手动阅读进度。"""
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
                return {
                    "pr": int(entry.get("progress") or 0),
                    "c": str(entry.get("chapterUid") or "222"),
                    "ci": int(entry.get("chapterIdx") or 4),
                    "co": int(entry.get("chapterOffset") or 5111),
                    "appId": entry.get("appId") or MAO_APP_ID,
                }
    except Exception as exc:  # noqa: BLE001 - 联网失败则兜底，不阻断主流程
        logger.warning("获取毛选实时进度失败，使用快照兜底: %s", exc)
    return None


def apply_mao_state(state: dict | None) -> None:
    if state is None:
        state = dict(MAO_SNAPSHOT)
        state["appId"] = MAO_APP_ID
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
