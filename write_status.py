"""把本次运行的结论写回仓库 STATUS.md（异步状态板）。

设计目的：微信推送有每日限额，且一轮阅读长达 1-3 小时，agent 无法全程值守。
把结论落到仓库文件后，用户手机打开一个固定链接即可查看，不依赖推送、不需要问任何人。

输入：全部来自环境变量（由 workflow 的 needs.*.outputs 注入）
输出：仓库根目录 STATUS.md
"""

import base64
import datetime
import os
import re


def decode_err(plain, b64):
    """取 errCode 分布。GitHub 会屏蔽疑似密钥的 job output（实测 err_codes 被 skip 成空），
    因此优先用 base64 旁路还原，取不到再退回明文。"""
    if b64:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except Exception:
            return f"<base64 解码失败: {str(b64)[:60]}>"
    return plain or "n/a"


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
               decode_err(e("R1_ERR"), e("R1_ERR_B64")), e("R1_MIN"), e("R1_START"))
    r2 = block("②", e("R2_RESULT"), e("R2_STATUS"), e("R2_SENT"), e("R2_ACC"),
               decode_err(e("R2_ERR"), e("R2_ERR_B64")), e("R2_MIN"), e("R2_START"))

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
        "## 根因与修复（2026-09-24 结案）\n\n"
        "**已定位并修复，当前运行正常。**\n\n"
        "- **真凶**：`main.py` 每 10 屏手动 `fetch('/web/login/renewal')` 试图续期 skey。"
        "该接口需 `x-wrpa-0` 动态签名，JS 手动 fetch 拿不到合法签名 → 后端判异常 → "
        "后续 read 信标全部 `-2010` → 最终整个 skey 被踢成 `-2012`。\n"
        "- **决定性对照**（同机 / 同 IP / 同 cookie，唯一变量就是这个调用）："
        "带 renewal → 2/5、-2010×2、75 秒中止；**去掉 renewal → 142 发 141 中、退出码 0**。\n"
        "- **修复（v14）**：`if i % 10 == 9 and os.environ.get(\"WXREAD_RENEWAL\",\"0\") == \"1\":` —— 默认关闭。\n"
        "- **被证伪的假设**：cookie 问题（换 6 批均无效，10 项中仅 skey 轮换）、"
        "headless 被识别（CI 上 xvfb + headful 结果完全相同）、"
        "GitHub IP 被拉黑（关闭 renewal 后 CI 两轮均成功）。\n"
        "- **副作用解释**：cookie 寿命曾逐批缩短（30 天 → 1.5 小时），"
        "是因为**每一轮 renewal 都会亲手打死一条 cookie**，并非自然过期。\n\n"
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
