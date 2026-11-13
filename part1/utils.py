# -*- coding: utf-8 -*-
"""
公共工具：日志、文本清洗、人名/URL 处理、JSON 读写、重试装饰器。
"""
import gzip
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import config as C

# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
_LOGGER = None


def get_logger(name: str = "crawl", to_file: bool = True) -> logging.Logger:
    """返回一个既输出到控制台、又写文件的 logger。"""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if to_file:
        C.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(C.CRAWL_LOG, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    _LOGGER = logger
    return logger


# --------------------------------------------------------------------------- #
# 文本清洗
# --------------------------------------------------------------------------- #
# 形如 [1] [12] [32-33] 的引注标记
_REF_RE = re.compile(r"\[\d+(?:\s*[-‑–]\s*\d+)?\]")
# 连续空白（含全角空格 U+3000）
_WS_RE = re.compile(r"[\s\u3000\xa0]+")


def clean_text(s) -> str:
    """去引注、去多余空白、去掉零宽字符。"""
    if s is None:
        return ""
    t = str(s)
    t = t.replace("\u200b", "").replace("\ufeff", "")
    t = _REF_RE.sub("", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def normalize_field(name: str) -> str:
    """字段名规整：去掉对齐用的全角空格，例如「本　　名」-> 「本名」。"""
    if not name:
        return ""
    return _WS_RE.sub("", str(name)).strip()


def shorten(text: str, limit: int = 200) -> str:
    text = clean_text(text)
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------- #
# 人名 / URL
# --------------------------------------------------------------------------- #
_TAIL_NOISE_RE = re.compile(r"\s*\[\d+\]\s*$")


def load_names(path: Path = None) -> list:
    """读取人物列表：去空行、去尾部引注标记、按原顺序去重。"""
    path = path or C.NAME_FILE
    seen, names = set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            n = _TAIL_NOISE_RE.sub("", line).strip()
            if not n or n in seen:
                continue
            seen.add(n)
            names.append(n)
    return names


def build_url(name: str, mobile: bool = False) -> str:
    """把人名拼成百度百科词条 URL。"""
    base = C.URL_MOBILE if mobile else C.URL_DESKTOP
    return base + quote(name)


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def dump_json(obj, path: Path, indent: int = 2) -> None:
    """写 JSON（UTF-8 无 BOM、不转义中文）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def load_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(obj, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # 进程被杀时最后一行可能残缺，跳过即可
                continue
    return rows


# --------------------------------------------------------------------------- #
# HTML 归档
# --------------------------------------------------------------------------- #
def save_html(name: str, html: str) -> None:
    if not C.SAVE_HTML:
        return
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)
    p = C.HTML_DIR / f"{safe}.html.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(html)


def load_html(name: str):
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)
    p = C.HTML_DIR / f"{safe}.html.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# 其它
# --------------------------------------------------------------------------- #
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sleep_a_bit() -> None:
    time.sleep(random.uniform(C.SLEEP_MIN, C.SLEEP_MAX))


def ignore_proxy() -> None:
    """按配置清掉进程内的代理环境变量（浏览器与 requests 都会读它们）。"""
    if not C.IGNORE_PROXY:
        return
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    # 让不上代理的名单包含本机，避免 localhost 也被甩给代理
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def human_size(n) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB"):
        if n < step:
            return f"{n:.1f}{unit}"
        n /= step
    return f"{n:.1f}TB"
