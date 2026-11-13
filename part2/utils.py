# -*- coding: utf-8 -*-
"""公共工具：日志、JSON 读写、文本清洗、进度与终端编码。"""
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import config as C

_LOG = None


def ensure_utf8_stdout():
    """Windows 控制台默认 GBK，中文/符号打印会炸，这里统一切到 UTF-8。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_logger(name: str = "kg") -> logging.Logger:
    global _LOG
    if _LOG is not None:
        return _LOG
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    C.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(C.LOG_DIR / "kg.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    _LOG = logger
    return logger


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
_WS_RE = re.compile(r"[\s\u3000\xa0\u200b\ufeff]+")
_REF_RE = re.compile(r"\[\d+(?:\s*[-–]\s*\d+)?\]")


def clean_text(s) -> str:
    if s is None:
        return ""
    t = _REF_RE.sub("", str(s))
    return _WS_RE.sub(" ", t).strip()


def split_sentences(text: str):
    """按中文标点切句；过短或过长的句子丢掉。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；!?;])|\n+", text)
    out = []
    for p in parts:
        s = clean_text(p)
        if C.MIN_SENT_LEN <= len(s) <= C.MAX_SENT_LEN:
            out.append(s)
    return out


def split_multi(value: str):
    """把「北京大学 、 密苏里大学」这类多值字段拆开。"""
    if not value:
        return []
    return [x.strip() for x in re.split(r"[、,，;；/]", str(value)) if x.strip()]


# --------------------------------------------------------------------------- #
def dump_json(obj, path: Path, indent: int = 2):
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


def read_jsonl(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def write_jsonl(rows, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(obj, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
