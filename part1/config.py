# -*- coding: utf-8 -*-
"""
项目一：人物信息抓取 —— 全局配置

所有可调参数集中在本文件，改这里即可，不用动业务代码。
"""
from pathlib import Path

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
HTML_DIR = DATA_DIR / "html"          # 原始 HTML 归档（可选）
LOG_DIR = BASE_DIR / "logs"
OUT_DIR = BASE_DIR / "output"
CHART_DIR = OUT_DIR / "charts"

NAME_FILE = BASE_DIR / "人物列表.txt"
PERSONS_JSONL = DATA_DIR / "persons.jsonl"   # 逐条追加，断点续爬靠它
PERSONS_JSON = DATA_DIR / "persons.json"     # 汇总结果（交付数据）
FAILED_JSONL = LOG_DIR / "failed.jsonl"      # 失败记录（含失败原因）
CRAWL_INDEX = LOG_DIR / "crawl_index.jsonl"  # 抓取元数据（不含正文）
PROGRESS_JSON = LOG_DIR / "progress.json"    # 进度快照
CRAWL_LOG = LOG_DIR / "crawl.log"

QUALITY_JSON = OUT_DIR / "quality_report.json"
QUALITY_MD = OUT_DIR / "质量评估报告.md"
ACCURACY_SAMPLE = OUT_DIR / "准确率抽查表.csv"

for _d in (DATA_DIR, HTML_DIR, LOG_DIR, OUT_DIR, CHART_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# 爬虫参数
# --------------------------------------------------------------------------- #
# 引擎：
#   selenium —— 无头 Chrome 打开桌面版 baike.baidu.com。反爬穿透力最强，
#               字段最全（与实践参考文档截图一致），缺点是慢、吃内存。
#   requests —— 直接请求移动版 wapbaike.baidu.com。快、轻，但字段名与桌面版
#               略有差异（解析器已做统一映射）。桌面版对纯 HTTP 请求返回
#               403（百度安全验证），所以 requests 引擎必须走移动版域名。
DEFAULT_ENGINE = "selenium"

URL_DESKTOP = "https://baike.baidu.com/item/"
URL_MOBILE = "https://wapbaike.baidu.com/item/"

# 并发线程数。selenium 每线程一个浏览器实例，按内存量力而行（4~6 比较稳）。
DEFAULT_WORKERS = 4

# 单页超时（秒）
PAGE_TIMEOUT = 30

# 请求间隔（秒）。每个线程每抓一页后随机 sleep 在 [MIN, MAX] 之间，
# 既是反爬礼貌，也能避免触发风控。
SLEEP_MIN = 0.8
SLEEP_MAX = 2.0

# 单页重试次数（失败后指数退避）
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

# 是否忽略系统代理。本机直连可访问百度，而若环境里存在 HTTP_PROXY，
# 浏览器/请求会把 localhost 与目标站点都甩给代理，导致「连不上」。
# 需要走代理上网时改成 False。
IGNORE_PROXY = True

# 是否保存原始 HTML（gzip）。2000 页桌面版约 8GB，默认关闭。
SAVE_HTML = False

# 每个线程的浏览器实例在连续抓取多少页后重启，防止内存泄漏。
RECYCLE_EVERY = 300

# 用户代理池（桌面版）
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# 移动版 UA（requests 引擎走移动版域名时必须用移动 UA）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
    "Mobile/15E148 Safari/604.1"
)

# --------------------------------------------------------------------------- #
# 抽取字段
# --------------------------------------------------------------------------- #
# 实践参考文档点名要求的「基本信息」字段（按此顺序输出，也用于完整率统计）
STANDARD_FIELDS = [
    "本名", "外文名", "别名", "国籍", "民族",
    "出生地", "出生日期", "毕业院校", "职业", "代表作品", "主要成就",
]

# 百度百科各版本对同一字段的叫法不完全一致，统一映射到上面这套标准字段。
# 注意：移动版把「出生日期 + 出生地」合并成「出生信息」，把「性别 + 民族」
# 合并成「性别/民族」，解析器会二次拆分，这里先登记别名。
FIELD_ALIASES = {
    "原名": "本名", "": "本名", "中文名": "本名",
    "中文名称": "本名",
    "外文名称": "外文名", "外文": "外文名", "英文名": "外文名",
    "艺名": "别名", "曾用名": "别名", "昵称": "别名", "昵　称": "别名",
    "国籍/地区": "国籍", "国家": "国籍", "国籍地区": "国籍",
    "民族": "民族", "民族族群": "民族",
    "出生地": "出生地", "籍贯": "出生地",
    "出生日期": "出生日期", "出生时间": "出生日期",
    "毕业院校": "毕业院校", "毕业学校": "毕业院校",
    "职业": "职业", "职业信息": "职业", "主要职业": "职业",
    "代表作品": "代表作品", "代表作": "代表作品",
    "主要成就": "主要成就", "成就": "主要成就",
}

# 「演艺经历」在词条里可能叫这些名字
CAREER_SECTION_KEYS = [
    "演艺经历", "人物经历", "早年经历", "从艺经历", "职业生涯",
    "演艺生涯", "演艺事业", "个人经历", "经历",
]

# 「主要作品」章节名
WORKS_SECTION_KEYS = ["主要作品", "代表作品", "作品", "演艺作品", "音乐作品", "影视作品"]

# 「获奖记录」章节名
AWARD_SECTION_KEYS = ["获奖记录", "荣誉记录", "获奖情况", "获得荣誉", "荣誉奖项", "获奖"]

# 「基本信息」前的噪音章节，抽取兜底时用来定位正文起点
NOISE_SECTION_KEYS = ["目录", "相关星图", "人物关系", "秒懂百科", "词条图册"]

# --------------------------------------------------------------------------- #
# 解析规模上限（防止单条记录把 JSON 撑爆）
# --------------------------------------------------------------------------- #
MAX_TABLE_ROWS = 300        # 每张表最多保留多少行
MAX_SECTION_TEXT = 20000    # 单条记录正文最多保留多少字
