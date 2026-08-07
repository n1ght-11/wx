# config.py 自定义配置,包括阅读次数、推送token的填写
import os
import re

"""
可修改区域
默认使用本地值如果不存在从环境变量中获取值
"""

# 阅读次数 默认40次/20分钟
READ_NUM = int(os.getenv('READ_NUM') or 40)
# 需要推送时可选，可选pushplus、wxpusher、telegram
PUSH_METHOD = "" or os.getenv('PUSH_METHOD')
# pushplus推送时需填
PUSHPLUS_TOKEN = "" or os.getenv("PUSHPLUS_TOKEN")
# telegram推送时需填
TELEGRAM_BOT_TOKEN = "" or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "" or os.getenv("TELEGRAM_CHAT_ID")
# wxpusher推送时需填
WXPUSHER_SPT = "" or os.getenv("WXPUSHER_SPT")
# SeverChan推送时需填
SERVERCHAN_SPT = "" or os.getenv("SERVERCHAN_SPT")


# read接口的bash命令，本地部署时可对应替换headers、cookies
curl_str = os.getenv('WXREAD_CURL_BASH')

# headers、cookies是一个省略模版，本地或者docker部署时对应替换
# 注意：真实 cookie 切勿提交到仓库，统一通过环境变量 WXREAD_COOKIES / WXREAD_CURL_BASH 注入
cookies = {}

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ko;q=0.5',
    'baggage': 'sentry-environment=production,sentry-release=dev-1730698697208,sentry-public_key=ed67ed71f7804a038e898ba54bd66e44,sentry-trace_id=1ff5a0725f8841088b42f97109c45862',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
}


# 书籍（固定只刷毛选，杜绝随机换书弄乱书架）
book = [
    "3300024284"
]

# 章节（毛选当前阅读章节；main.py 会按实时进度覆盖）
chapter = [
    "222"
]

"""
建议保留区域|固定读毛选（bookId=3300024284）
pr 不再写死 74%——main.py 会覆盖为毛选真实进度（取不到则伪随机非零值）。
关键约束：pr 必须为非零。weread 对 pr=0 返回 {} 且不记录阅读时长，
会让阅读循环空转（之前卡 30~60 分钟、手机端时长不涨就是它引起的）。
"""
data = {
    "appId": "wb182564874663h490269584",
    "b": "3300024284",
    "c": "222",
    "ci": 4,
    "co": 5111,
    "sm": "谁是我们的敌人？谁是我们的朋友？这个问题是革命的首要问题。",
    "pr": 50,
    "rt": 15,
    "ts": 1744264311434,
    "rn": 466,
    "sg": "2b2ec618394b99deea35104168b86381da9f8946d4bc234e062fa320155409fb",
    "ct": 1744264311,
    "ps": "221",
    "pc": "223",
    "s": "36cc0815"
}


def convert(curl_command):
    """提取bash接口中的headers与cookies
    支持 -H 'Cookie: xxx' 和 -b 'xxx' 两种方式的cookie提取
    """
    # 提取 headers
    headers_temp = {}
    for match in re.findall(r"-H '([^:]+): ([^']+)'", curl_command):
        headers_temp[match[0]] = match[1]

    # 提取 cookies
    cookies = {}

    # 从 -H 'Cookie: xxx' 提取
    cookie_header = next((v for k, v in headers_temp.items()
                         if k.lower() == 'cookie'), '')

    # 从 -b 'xxx' 提取
    cookie_b = re.search(r"-b '([^']+)'", curl_command)
    cookie_string = cookie_b.group(1) if cookie_b else cookie_header

    # 解析 cookie 字符串
    if cookie_string:
        for cookie in cookie_string.split('; '):
            if '=' in cookie:
                key, value = cookie.split('=', 1)
                cookies[key.strip()] = value.strip()

    # 移除 headers 中的 Cookie/cookie
    headers = {k: v for k, v in headers_temp.items()
              if k.lower() != 'cookie'}

    return headers, cookies


if curl_str:
    headers, cookies = convert(curl_str)
else:
    # 桥接回退：未提供 curl_bash 时，复用现有 WXREAD_COOKIES（Playwright 版本的有效会话 cookie 列表）
    cookies_env = os.getenv('WXREAD_COOKIES')
    if cookies_env:
        try:
            import json
            clist = json.loads(cookies_env)
            cookies = {c['name']: c['value'] for c in clist if isinstance(c, dict) and 'name' in c and 'value' in c}
            print(f"[config] 已从 WXREAD_COOKIES 载入 {len(cookies)} 个 cookie，复用现有有效会话")
        except Exception as e:
            print(f"[config] WXREAD_COOKIES 解析失败，沿用占位 cookie: {e}")
