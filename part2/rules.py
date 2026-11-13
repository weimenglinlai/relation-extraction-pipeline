# -*- coding: utf-8 -*-
"""
规则 / 模板抽取。

两个来源：
  1. 结构化字段（项目一 JSON 里的 basic_info / works / awards）—— 精度最高，
     用来补模型训练数据里缺失的关系（出生地、民族、职业、经纪公司）；
  2. 正文句式模板 —— 作为 CasRel 的对照基线，也用于模型漏抽时兜底。

本模块只负责「产出三元组」，不关心存储。
"""
import re

import config as C
from utils import clean_text, split_multi

# --------------------------------------------------------------------------- #
# 一、结构化字段
# --------------------------------------------------------------------------- #
_BASIC_REL = {
    "出生地": "出生地",
    "籍贯": "籍贯",
    "国籍": "国籍",
    "民族": "民族",
    "毕业院校": "毕业院校",
    "经纪公司": "经纪公司",
}

_STRIP_TITLE = re.compile(r"^[《〈【\[\(（]+|[》〉】\]\)）]+$")


def _clean_title(s: str) -> str:
    s = clean_text(s)
    s = _STRIP_TITLE.sub("", s).strip()
    return s


def _looks_like_title(cell: str) -> bool:
    c = clean_text(cell)
    if not c or len(c) > 40:
        return False
    if re.fullmatch(r"[\d\s\-/年月日\.]+", c):          # 纯日期
        return False
    if c in ("电视剧", "电影", "纪录片", "综艺", "小品", "相声",
             "网络剧", "动画", "话剧", "短片", "MV", "音乐", "单曲", "专辑"):
        return False
    return True


def _title_column(header, rows):
    """
    作品表的列名五花八门（作品名/歌曲名称/节目名称…），先按关键词认，
    认不出来就按「哪一列的取值最像标题」打分。
    """
    ncol = max(len(r) for r in rows) if rows else 0
    if ncol == 0:
        return -1
    if header:
        for i, h in enumerate(header):
            if any(k in h for k in C.WORK_TITLE_HEADERS):
                return i
    best_idx, best_score = -1, -1.0
    for i in range(ncol):
        vals = [r[i] for r in rows if len(r) > i]
        if not vals:
            continue
        score = sum(1 for v in vals if _looks_like_title(v)) / len(vals)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx if best_score >= 0.6 else -1


def _work_relation(category: str) -> str:
    for key, rel in C.WORK_CATEGORY_REL:
        if key in category:
            return rel
    return "参演"


def triples_from_structured(person: dict) -> list:
    """从项目一保存的结构化字段直接生成高置信三元组。"""
    name = clean_text(person.get("name", ""))
    if not name:
        return []
    out, seen = [], set()

    def add(rel, obj, conf=0.95, source="structured"):
        obj = clean_text(obj)
        if not obj or obj == name or len(obj) > 40:
            return
        k = (name, rel, obj)
        if k in seen:
            return
        seen.add(k)
        out.append({"subject": name, "predicate": rel, "object": obj,
                    "confidence": conf, "source": source})

    basic = person.get("basic_info") or {}
    for field, rel in _BASIC_REL.items():
        for v in split_multi(basic.get(field, "")):
            add(rel, v)
    for occ in split_multi(basic.get("职业", "")):
        if 1 < len(occ) <= 12:
            add("职业", occ)

    # 主要作品：按分类映射到关系，标题列自动识别
    for w in person.get("works") or []:
        rel = _work_relation(w.get("category", ""))
        rows = w.get("items") or []
        idx = _title_column(w.get("header") or [], rows)
        if idx >= 0:
            for r in rows:
                if len(r) > idx:
                    t = _clean_title(r[idx])
                    if 1 < len(t) <= 40:
                        add(rel, t, conf=0.9)

    # 获奖记录：取「奖项名称」列
    for a in person.get("awards") or []:
        header = a.get("header") or []
        idx = next((i for i, h in enumerate(header) if "奖项" in h or "名称" in h), None)
        if idx is None and header:
            idx = 1 if len(header) > 1 else 0
        for r in a.get("items") or []:
            if idx is not None and len(r) > idx:
                v = clean_text(r[idx])
                if 2 < len(v) <= 40:
                    add("获得奖项", v, conf=0.85)
    return out


# --------------------------------------------------------------------------- #
# 二、正文句式模板
# --------------------------------------------------------------------------- #
_RE_BORN = re.compile(r"(?:出生于|生于)([\u4e00-\u9fa5]{2,12}?)(?=[，。；、,;]|$)")
_RE_NATIVE = re.compile(r"祖籍([\u4e00-\u9fa5]{2,12}?)(?=[，。；、,;]|$)")
_RE_GRAD = re.compile(r"毕业于([\u4e00-\u9fa5A-Za-z·\s]{2,25}?)(?=[，。；、,;（(]|$)")
_RE_NATION = re.compile(r"([\u4e00-\u9fa5]{2,6}族)")

# 动词 -> 关系
_VERB_REL = {
    "执导": "执导", "导演": "执导",
    "编剧": "编剧", "执笔": "编剧",
    "主演": "参演", "出演": "参演", "参演": "参演", "饰演": "参演",
    "演唱": "演唱", "献唱": "演唱",
    "主持": "主持",
}
_WORK_RE = re.compile(
    r"(执导|导演|编剧|执笔|主演|出演|参演|饰演|演唱|献唱|主持)"
    r"[了过]?[^。；，]{0,12}?《([^》]{1,40})》")
# 单独识别动词与书名号，用于一句里出现多部作品的情况
_VERB_RE = re.compile(r"(执导|导演|编剧|执笔|主演|出演|参演|饰演|演唱|献唱|主持)")
_BOOK_RE = re.compile(r"《([^》]{1,40})》")

# 职业关键词（自由文本模式下从「是……」的谓语里认）
OCC_WORDS = ("演员", "导演", "歌手", "主持人", "编剧", "制片人",
             "作词人", "作曲家", "配音演员", "相声演员", "模特")


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _guess_subject(sent: str, pos: int, max_len: int = 6) -> str:
    """
    自由文本里没有已知人物时猜主语：
      1) 优先取动词前紧邻的一段连续中文（「…英达执导…」-> 英达）；
      2) 动词前是标点时，退回句首的第一段 2~4 字中文（「刘德华出生于…，主演…」）。
    """
    j = pos
    while j > 0 and _is_cjk(sent[j - 1]) and pos - j < max_len:
        j -= 1
    cand = sent[j:pos]
    if 2 <= len(cand) <= max_len:
        return cand
    # 句首：必须在常见谓语/标点处收尾，否则会把「刘德华出」这种半截词当人名
    m = re.match(r"[^\u4e00-\u9fff]{0,8}?([\u4e00-\u9fff]{2,4}?)"
                 r"(?=出生|生于|毕业|主演|执导|出演|参演|演唱|献唱|主持|编剧|"
                 r"凭借|获得|是|，|,|。|、)", sent)
    return m.group(1) if m else ""


def triples_from_text(text: str, name: str = "", conf: float = 0.7) -> list:
    """
    基于句式模板抽三元组（作对照基线 / 模型兜底）。

    name 为空时退化为「从动词前猜主语」，供 GUI 的自由文本抽取使用。
    """
    text = clean_text(text)
    if not text:
        return []
    out, seen = [], set()

    def add(rel, obj, subj):
        obj = _clean_title(obj)
        subj = clean_text(subj)
        if not obj or not subj or obj == subj or len(obj) > 40:
            return
        k = (subj, rel, obj)
        if k in seen:
            return
        seen.add(k)
        out.append({"subject": subj, "predicate": rel, "object": obj,
                    "confidence": conf, "source": "rule"})

    for sent in re.split(r"[。！？；\n]", text):
        if not sent:
            continue
        # 有已知人物名时只处理提到他的句子；没有就整句处理并猜主语
        if name and name not in sent:
            continue
        subj_fixed = name

        def subject_at(pos):
            return subj_fixed or _guess_subject(sent, pos)

        for m in _RE_BORN.finditer(sent):
            add("出生地", m.group(1), subject_at(m.start()))
        for m in _RE_NATIVE.finditer(sent):
            add("籍贯", m.group(1), subject_at(m.start()))
        for m in _RE_GRAD.finditer(sent):
            add("毕业院校", m.group(1).strip(), subject_at(m.start()))
        for m in _VERB_RE.finditer(sent):
            # 动词后 40 字内可能连着出现多个《作品》，如「主演《无间道》《暗战》」
            seg = sent[m.end(): m.end() + 40]
            rel = _VERB_REL.get(m.group(1), "参演")
            for wm in _BOOK_RE.finditer(seg):
                add(rel, wm.group(1), subject_at(m.start()))
        # 「X 是中国内地男演员、导演」这类职业陈述（只在自由文本模式下用）
        if not subj_fixed:
            m = re.search(r"是([^。；]{0,14})", sent)
            if m:
                subj = _guess_subject(sent, m.start())
                for w in OCC_WORDS:
                    if w in m.group(1):
                        add("职业", w, subj)
    return out


def gazetteer_from_structured(persons: list) -> dict:
    """由全部结构化数据汇总出实体词典（供 GUI 自由文本抽取时识别实体）。"""
    gaz = {t: set() for t in C.ENTITY_TYPES}
    for p in persons:
        for t in triples_from_structured(p):
            obj = t["object"]
            rel = t["predicate"]
            etype = dict((r[0], r[2]) for r in C.RELATIONS).get(rel, "作品")
            if 1 < len(obj) <= 40:
                gaz.setdefault(etype, set()).add(obj)
    return {k: sorted(v) for k, v in gaz.items()}
