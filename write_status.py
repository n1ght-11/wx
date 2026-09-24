"""把本次运行的结论写回仓库 STATUS.md（异步状态板）。

设计目的：微信推送有每日限额，且一轮阅读长达 1-3 小时，agent 无法全程值守。
把结论落到仓库文件后，用户手机打开一个固定链接即可查看，不依赖推送、不需要问任何人。

输入：全部来自环境变量（由 workflow 的 needs.*.outputs 注入）
输出：仓库根目录 STATUS.md
"""

import datetime
import os
import re


def beijing_now():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%m-%d %H:%M")


def to_int(x):
    try:
        return int(x or 0)
    except Exception:
        return 0


def verdict(result, status, sent, acc):
    if result != "success":
        return "❌ 失败"
    if status and status not in ("ok", ""):
        return "❌ 失败"
    s, a = to_int(sent), to_int(acc)
    if s >= 3 and a / s < 0.5:
        return "❌ 失败"
    return "✅ 成功"


def block(no, result, status, sent, acc, err, mins, start):
    if result == "skipped":
        return f"## 第{no}轮\n\n_未执行（第①轮未通过，按设计跳过）_\n"
    v = verdict(result, status, sent, acc)
    rate = ""
    s, a = to_int(sent), to_int(acc)
    if s:
        rate = f"　接受率 {a / s * 100:.0f}%"
    return (
        f"## 第{no}轮\n\n"
        f"| 项目 | 值 |\n|---|---|\n"
        f"| 结果 | {v} |\n"
        f"| 启动 | {start or '-'}（北京） |\n"
        f"| 目标时长 | {mins or '-'} 分钟 |\n"
        f"| 预计计入 | **约 {a} 分钟**（手机端时长应与此一致） |\n"
        f"| 发送 / 接受 | {sent or '-'} / {acc or '-'}{rate} |\n"
        f"| status | `{status or '-'}` |\n"
        f"| errCode | `{err or 'n/a'}` |\n"
    )


def main():
    e = os.environ.get
    now = beijing_now()
    run = e("RUN_ID", "")
    link = f"https://github.com/n1ght-11/wx/actions/runs/{run}"

    ok1 = verdict(e("R1_RESULT"), e("R1_STATUS"), e("R1_SENT"), e("R1_ACC"))
    ok2 = verdict(e("R2_RESULT"), e("R2_STATUS"), e("R2_SENT"), e("R2_ACC"))
    total = to_int(e("R1_ACC")) + to_int(e("R2_ACC"))
    if ok1.startswith("✅") and ok2.startswith("✅"):
        head = "✅ 两轮均正常"
    elif ok1.startswith("✅") or ok2.startswith("✅"):
        head = "⚠️ 部分成功"
    else:
        head = "❌ 本轮未计入"

    r1 = block("①", e("R1_RESULT"), e("R1_STATUS"), e("R1_SENT"), e("R1_ACC"),
               e("R1_ERR"), e("R1_MIN"), e("R1_START"))
    r2 = block("②", e("R2_RESULT"), e("R2_STATUS"), e("R2_SENT"), e("R2_ACC"),
               e("R2_ERR"), e("R2_MIN"), e("R2_START"))

    # 历史表：保留最近 12 条
    hist = []
    try:
        old = open("STATUS.md", encoding="utf-8").read()
        m = re.search(r"<!-- HISTORY:START -->(.*?)<!-- HISTORY:END -->", old, re.S)
        if m:
            for line in m.group(1).splitlines():
                t = line.strip()
                if t.startswith("|") and "---" not in t and "时间(北京)" not in t:
                    hist.append(t)
    except Exception:
        pass
    hist.insert(0, f"| {now} | {head} | {total} 分钟 | "
                   f"{to_int(e('R1_SENT')) + to_int(e('R2_SENT'))} / {total} | [run]({link}) |")
    hist = hist[:12]

    doc = (
        "# 📚 wxread 自动阅读 · 状态板\n\n"
        "> 每次运行结束**自动更新**。手机直接打开本文件即可，不依赖微信推送、不需要问任何人。\n\n"
        f"**最近更新：** {now}（北京） ｜ **本次结论：{head}** ｜ **本次预计计入：约 {total} 分钟**\n"
        f"**运行号：** [{run}]({link})\n\n"
        f"{r1}\n{r2}\n"
        "## 怎么判读\n\n"
        "| 现象 | 含义 | 该做什么 |\n|---|---|---|\n"
        "| 接受率 ≥ 95% | 正常 | 不用管，手机端时长 ≈ 接受数 |\n"
        "| 接受率 < 50% 且 errCode 含 `-2010` | read 信标被后端拒（风控），**换 cookie 无效** | 见下方「当前根因」 |\n"
        "| 预检就 `-2012` | cookie 真的过期了 | 重新采集 cookie |\n"
        "| 第二轮 skipped | 第①轮未通过，按设计跳过 | 看第①轮原因 |\n\n"
        "## 当前根因（2026-09-24 定稿）\n\n"
        "五批 cookie（y2E5CgF1 / Xqf908gD / s2eQtIgm / rFXWxNER / MMFVuB4v）字段级对比："
        "**10 项中 9 项完全相同，只有 `wr_skey` 轮换**，`wr_vid` / `wr_fp` / `wr_localvid` 恒定不变。"
        "五批同样死法：shelf/sync 通过，read 信标开局 2 个 succ 后永久 `-2010`。\n\n"
        "已排除：账号被封（用户浏览器正常）、cookie 过期（预检通过）、"
        "IP 被拉黑（预检从同一 GitHub 出口 IP 发出且通过）。\n"
        "**主要嫌疑：headless Chromium 被 weread 识别**（真实浏览器正常、headless 被拒）。"
        "修复方向：headful + 反检测参数。\n\n"
        "<!-- HISTORY:START -->\n"
        "| 时间(北京) | 结论 | 预计计入 | 发送/接受 | 链接 |\n|---|---|---|---|---|\n"
        + "\n".join(hist) + "\n"
        "<!-- HISTORY:END -->\n"
    )

    with open("STATUS.md", "w", encoding="utf-8") as f:
        f.write(doc)
    print("STATUS.md 已更新：", head, "| 预计计入", total, "分钟")


if __name__ == "__main__":
    main()
