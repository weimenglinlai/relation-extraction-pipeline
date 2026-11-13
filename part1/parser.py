# -*- coding: utf-8 -*-
"""
网页解析与内容提取模块。

百度百科现在有两套页面：
  * 桌面版（baike.baidu.com）—— 语义属性最全：
      章节标题 div[data-tag=header][data-name=...][data-level=...]
      正文段落 div[data-tag=paragraph]
      信息模块 div[data-tag=module][data-module-type=...]
    基本信息是 dl > div.itemWrapper > dt(字段名) / dd(取值)
  * 移动版（wapbaike.baidu.com）—— 类名带哈希后缀但前缀稳定：
      章节标题 div[data-tag=header][data-title=...]
      基本信息 div[class*=cardItem] > cardName / cardValue
      表格仍是标准 <table>

两套都用「语义属性优先、类名前缀兜底」的方式解析，最终产出同一份结构化数据。
"""
import re

from bs4 import BeautifulSoup

import config as C
from utils import clean_text, normalize_field, shorten

# 需要整块删掉的噪音节点
_NOISE_SELECTORS = [
    "script", "style", "noscript", "iframe",
    "sup[data-tag=ref]",                 # 引注角标 [1]
    "[class*=supWrap]", "[class*=refSup]",
    "[class*=referenceTip]", "[class*=lemmaReference]", "[class*=index_reference]",
    "[class*=discussionLine]",           # 移动版「讨论」入口
    "[class*=textTip]",
    "img", "svg",
]


def _strip_noise(soup: BeautifulSoup) -> None:
    for sel in _NOISE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()


def _pick(soup, *selectors):
    """按顺序取第一个命中的元素；selector 用 class 精确匹配（~=）避免误伤后代。"""
    for sel in selectors:
        el = soup.select_one(sel)
        if el is not None:
            return el
    return None


def _text_of(node) -> str:
    if node is None:
        return ""
    return clean_text(node.get_text(" ", strip=True))


# --------------------------------------------------------------------------- #
# 表格
# --------------------------------------------------------------------------- #
# 表头关键词。百度百科很多表的表头用的是 <td> 而非 <th>，
# 只靠标签名认不出来，再用关键词兜一层。
_HEADER_HINTS = ("时间", "名称", "作品", "结果", "序号", "角色", "剧名", "歌名",
                 "专辑", "奖项", "备注", "年份", "类型", "导演", "曲目", "节目",
                 "合作", "荣誉", "主办")


def _looks_like_header(vals) -> bool:
    if len(vals) < 2:
        return False
    hit = sum(1 for v in vals if any(h in v for h in _HEADER_HINTS))
    return hit >= len(vals) - 1


def _parse_table(table) -> dict:
    """
    把一张 <table> 解析成 {group, header, rows}。

    百度百科的获奖记录是「外层表套内层表」：外层单元格写奖项类别，
    内层表才是明细。这里按 tr 的最近 table 祖先过滤，天然跳过被嵌套的行。
    """
    rows, header, group = [], [], ""
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cells = tr.find_all(["th", "td"], recursive=False)
        vals = [_text_of(c) for c in cells]
        if not any(vals):
            continue
        if len(vals) == 1:
            # 单格行 = 分组标题（如「影视类」「音乐类」）
            group = vals[0]
            continue
        if cells and all(c.name == "th" for c in cells):
            header = vals
            continue
        rows.append(vals)
    # <td> 写的表头行在此认领
    if not header and rows and _looks_like_header(rows[0]):
        header = rows.pop(0)
    if header and rows:
        rows = [r for r in rows if r != header]
    return {"group": group, "header": header, "rows": rows[:C.MAX_TABLE_ROWS],
            "total_rows": len(rows),
            "truncated": len(rows) > C.MAX_TABLE_ROWS}


def _module_tables(block) -> list:
    return [_parse_table(t) for t in block.find_all("table")]


def _parse_award_module(block) -> list:
    """
    获奖模块是「包装表套明细表」：外层表每行的唯一单元格里要么是分组标签
    （影视类 / 音乐类 / 社会类…），要么嵌一张明细表。这里按顺序把标签与明细表配对。
    """
    tables = block.find_all("table")
    wrapper = next((t for t in tables if t.find("table")), None)
    leaves = [t for t in tables if not t.find("table")]

    groups = []
    if wrapper is not None:
        label = ""
        for tr in wrapper.find_all("tr"):
            if tr.find_parent("table") is not wrapper:
                continue
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) != 1:
                continue
            if cells[0].find("table"):
                groups.append(label)
            else:
                t = _text_of(cells[0])
                if t:
                    label = t

    out = []
    for i, t in enumerate(leaves):
        item = _parse_table(t)
        out.append({
            "group": groups[i] if i < len(groups) else "",
            "header": item["header"],
            "items": item["rows"],
            "total_rows": item["total_rows"],
            "truncated": item["truncated"],
        })
    return out


def _module_cards(block) -> list:
    """
    没有表格的模块（如桌面版「参演电影」卡片流）退回按 dl 取条目。
    每个 <dl> 视为一个条目：dt 是字段名、dd 是取值。
    """
    items = []
    for dl in block.find_all("dl"):
        kv = {}
        key = None
        for child in dl.find_all(["dt", "dd"], recursive=False):
            val = _text_of(child)
            if child.name == "dt":
                key = val
            elif key:
                kv[key] = val
        if kv:
            items.append(kv)
    return items


# --------------------------------------------------------------------------- #
# 正文分块
# --------------------------------------------------------------------------- #
def _iter_blocks(root) -> list:
    """
    按文档顺序返回 (kind, payload)。

    kind ∈ {header, paragraph, module, list}
    只取「不嵌套在另一个 data-tag 块里」的元素，避免模块内部的段落被重复计入。
    """
    blocks = []
    for el in root.descendants:
        tag = getattr(el, "attrs", None)
        if not tag or "data-tag" not in el.attrs:
            continue
        if el.name in ("script", "style"):
            continue
        # 若某个祖先（在 root 之内）也带 data-tag，说明它是子元素，跳过
        parent = el.parent
        nested = False
        while parent is not None and parent is not root:
            if isinstance(parent, type(el)) and "data-tag" in getattr(parent, "attrs", {}):
                nested = True
                break
            parent = parent.parent
        if nested:
            continue
        kind = el.get("data-tag")
        if kind == "header":
            blocks.append(("header", {
                "name": clean_text(el.get("data-name") or el.get("data-title") or ""),
                "level": int(el.get("data-level") or 1),
            }))
        elif kind == "paragraph":
            txt = _text_of(el)
            if txt:
                blocks.append(("paragraph", txt))
        elif kind == "list":
            lis = [_text_of(li) for li in el.find_all("li")]
            lis = [x for x in lis if x]
            if lis:
                blocks.append(("list", lis))
        elif kind == "module":
            mtype = el.get("data-module-type") or ""
            payload = {
                "module_type": mtype,
                "tables": _module_tables(el),
                "cards": _module_cards(el),
                "text": _text_of(el)[:2000],
            }
            if mtype == "award":
                # 获奖模块的 <table> 是包装表，直接解析会得到一堆垃圾，
                # 改用 _parse_award_module 逐张明细表提取。
                payload["awards"] = _parse_award_module(el)
                payload["tables"] = []
            blocks.append(("module", payload))
    return blocks


def _group_sections(blocks) -> list:
    sections = []
    stack = []

    def new_node(payload):
        return {"name": payload["name"], "level": payload["level"],
                "texts": [], "tables": [], "lists": [], "cards": [], "subs": []}

    for kind, payload in blocks:
        if kind == "header":
            node = new_node(payload)
            lv = payload["level"]
            while stack and stack[-1]["level"] >= lv:
                stack.pop()
            if stack:
                stack[-1]["subs"].append(node)
            else:
                sections.append(node)
            stack.append(node)
            continue
        if not stack:
            continue
        cur = stack[-1]
        if kind == "paragraph":
            cur["texts"].append(payload)
        elif kind == "list":
            cur["lists"].append(payload)
        elif kind == "module":
            if payload["tables"]:
                cur["tables"].extend(payload["tables"])
            if payload["cards"]:
                cur["cards"].extend(payload["cards"])
            if payload.get("awards"):
                cur.setdefault("awards", []).extend(payload["awards"])
    return sections


def _walk(sec):
    """深度优先遍历章节节点（含自身）。"""
    yield sec
    for sub in sec.get("subs", []):
        yield from _walk(sub)


def _section_text(sec) -> str:
    """递归拼接章节全文（标题 + 段落 + 子章节）。"""
    parts = []
    if sec.get("name"):
        parts.append(sec["name"])
    parts.extend(sec.get("texts", []))
    for sub in sec.get("subs", []):
        t = _section_text(sub)
        if t:
            parts.append(t)
    return clean_text(" ".join(parts))


def _collect_tables(sec) -> list:
    out = []
    for node in _walk(sec):
        out.extend(node.get("tables", []))
    return out


def _collect_cards(sec) -> list:
    out = []
    for node in _walk(sec):
        out.extend(node.get("cards", []))
    return out


def _collect_awards(sec) -> list:
    out = []
    for node in _walk(sec):
        out.extend(node.get("awards", []))
    return out


def _find_section(sections, keys):
    """按章节名的关键词命中，优先取名字最短（最贴合）的那个。"""
    hits = []
    for sec in sections:
        n = sec["name"]
        for k in keys:
            if k == n:
                hits.append((0, len(n), sec))
                break
            if k in n:
                hits.append((1, len(n), sec))
                break
    if not hits:
        return None
    hits.sort(key=lambda x: (x[0], x[1]))
    return hits[0][2]


# --------------------------------------------------------------------------- #
# 基本信息
# --------------------------------------------------------------------------- #
def _basic_info_pairs(soup) -> dict:
    """返回 {字段名: 取值}，保持页面顺序。"""
    pairs = {}

    def put(k, v):
        k, v = normalize_field(k), clean_text(v)
        if not k or not v:
            return
        if k in pairs and pairs[k]:
            if v not in pairs[k]:
                pairs[k] = f"{pairs[k]}、{v}"
        else:
            pairs[k] = v

    # 桌面版：dl > div.itemWrapper > dt/dd
    box = _pick(soup, "[class~=J-basic-info]", "div[class*=basicInfo]")
    if box:
        for wrap in box.select("[class*=itemWrapper]"):
            dt = wrap.find("dt")
            dd = wrap.find("dd")
            if dt is not None and dd is not None:
                put(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))
        if pairs:
            return pairs
        # 老式：dl 里直接 dt/dd 平铺
        for dl in box.find_all("dl"):
            key = None
            for child in dl.find_all(["dt", "dd"], recursive=False):
                if child.name == "dt":
                    key = child.get_text(" ", strip=True)
                elif key:
                    put(key, child.get_text(" ", strip=True))

    # 移动版：div.cardItem > cardName / cardValue
    for item in soup.select("[class*=cardItem]"):
        name_el = item.select_one("[class*=cardName]")
        val_el = item.select_one("[class*=cardValue]")
        if name_el is not None and val_el is not None:
            put(name_el.get_text(" ", strip=True), val_el.get_text(" ", strip=True))

    return pairs


def _fallback_basic_info(soup, sections) -> dict:
    """
    参考文档要求：没有「基本信息」标题的网页，取正文开头到目录之前的部分。
    """
    if not sections:
        return {}
    first = sections[0]
    head = _section_text(first)
    return {"正文开头": shorten(head, 500)} if head else {}


_SPLIT_RE = re.compile(r"[，,；;、]")
_BIRTH_RE = re.compile(r"\d{4}\s*年")
_DATE_ONLY_RE = re.compile(r"^[\d年月日\s（）()\-—~至\.]+")


def _split_combined(raw: dict) -> dict:
    """处理移动版把多个字段挤进一个格子里的情况。"""
    out = {}
    for k, v in raw.items():
        k2 = normalize_field(k)
        if k2 in ("出生信息", "出生日期/出生地", "出生年月"):
            parts = [p.strip() for p in _SPLIT_RE.split(v) if p.strip()]
            date, place = "", ""
            for p in parts:
                if not date and _BIRTH_RE.search(p):
                    date = _DATE_ONLY_RE.sub("", p) and p.split("（")[0].strip()
                    date = clean_text(date)
                elif not place:
                    place = p
            if date:
                out["出生日期"] = date
            if place:
                out["出生地"] = place
        elif k2 in ("性别/民族", "性别民族", "性别/种族"):
            parts = [p.strip() for p in _SPLIT_RE.split(v) if p.strip()]
            if parts:
                out["性别"] = parts[0]
            if len(parts) > 1:
                out["民族"] = parts[1]
        else:
            out[k] = v
    return out


def _normalize_basic(raw: dict, lemma_name: str) -> dict:
    """映射到参考文档要求的标准字段。"""
    flat = _split_combined(raw)
    std = {}
    for k, v in flat.items():
        nk = normalize_field(k)
        nk = C.FIELD_ALIASES.get(nk, nk)
        v = clean_text(v)
        if not v:
            continue
        if nk in std and std[nk] and v not in std[nk]:
            std[nk] = f"{std[nk]}、{v}"
        else:
            std[nk] = v
    std.setdefault("本名", lemma_name)
    # 按标准字段顺序重排，未列出的字段附在后面
    ordered = {}
    for f in C.STANDARD_FIELDS:
        ordered[f] = std.get(f, "")
    for k, v in std.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def parse_page(html: str, name: str, url: str = "", engine: str = "",
               crawl_time: str = "") -> dict:
    """把一页 HTML 解析成结构化记录。"""
    soup = BeautifulSoup(html, "lxml")
    _strip_noise(soup)

    h1 = soup.find("h1")
    lemma_name = clean_text(h1.get_text(" ", strip=True)) if h1 else name
    lemma_name = lemma_name or name

    summary_el = _pick(soup, "[class~=J-summary]", "div[class*=lemmaSummary]",
                       "div[class*=index_summary]")
    summary = _text_of(summary_el)
    if summary:
        # 摘要把「演艺经历：」等小标题也串进来了，去掉开头重复
        summary = re.sub(r"^(讨论|播报|编辑)\s*", "", summary)

    raw_basic = _basic_info_pairs(soup)

    # 正文容器：必须是真正挂 data-tag 块的那一层，否则命中后代的小元素
    root = _pick(soup, "div[class~=J-lemma-content]", "div[class*=mainContent]",
                 "div[class*=pageContent]")
    if root is None or root.find(attrs={"data-tag": True}) is None:
        heads = soup.select('[data-tag="header"]')
        if heads:
            root = heads[0].parent
            # 逐级上溯，直到父节点覆盖全部标题块
            while root is not None and len(root.select('[data-tag="header"]')) < len(heads):
                root = root.parent
    if root is None:
        root = soup

    blocks = _iter_blocks(root if root else soup)
    sections = [s for s in _group_sections(blocks) if s["name"]]

    if not raw_basic:
        raw_basic = _fallback_basic_info(soup, sections)

    basic_std = _normalize_basic(raw_basic, lemma_name)

    career_sec = _find_section(sections, C.CAREER_SECTION_KEYS)
    works_sec = _find_section(sections, C.WORKS_SECTION_KEYS)
    award_sec = _find_section(sections, C.AWARD_SECTION_KEYS)

    # 主要作品：每个子章节一类，带上表格或卡片条目
    works = []
    if works_sec:
        groups = works_sec["subs"] or [works_sec]
        for g in groups:
            tables = _collect_tables(g)
            rows = []
            for t in tables:
                rows.extend(t["rows"])
            cards = _collect_cards(g)
            works.append({
                "category": g["name"],
                "header": next((t["header"] for t in tables if t["header"]), []),
                "items": rows[:C.MAX_TABLE_ROWS],
                "cards": cards[:C.MAX_TABLE_ROWS],
                "count": len(rows) or len(cards),
            })
    works = [w for w in works if w["items"] or w["cards"]]

    # 获奖记录
    awards = _collect_awards(award_sec) if award_sec else []
    if award_sec and not awards:
        txt = _section_text(award_sec)
        if txt:
            awards.append({"group": "概述", "header": [], "items": [], "text": shorten(txt, 1000)})

    # 全部章节（供项目二使用，也便于人工核查）
    section_out = []
    for sec in sections:
        section_out.append({
            "name": sec["name"],
            "level": sec["level"],
            "text": shorten(_section_text(sec), C.MAX_SECTION_TEXT),
            "subs": [s["name"] for s in sec["subs"]],
            "table_count": len(sec["tables"]),
        })

    raw_text = clean_text(" ".join(p for _, p in blocks if isinstance(p, str)))
    if not raw_text:
        raw_text = clean_text(root.get_text(" ", strip=True) if root else "")
    raw_text = raw_text[:C.MAX_SECTION_TEXT]

    return {
        "name": name,
        "lemma_name": lemma_name,
        "url": url,
        "crawl_time": crawl_time,
        "engine": engine,
        "summary": summary,
        "basic_info": basic_std,
        "basic_info_raw": raw_basic,
        "career": _section_text(career_sec) if career_sec else "",
        "works": works,
        "awards": awards,
        "sections": section_out,
        "raw_text": raw_text,
        "stats": {
            "html_size": len(html),
            "section_count": len(section_out),
            "work_categories": len(works),
            "award_groups": len(awards),
            "career_len": len(_section_text(career_sec)) if career_sec else 0,
        },
    }


def parse_quality(rec: dict) -> dict:
    """单条记录的完整度自检，供数据质量评估使用。"""
    basic = rec.get("basic_info", {})
    filled = [f for f in C.STANDARD_FIELDS if clean_text(basic.get(f, ""))]
    missing = [f for f in C.STANDARD_FIELDS if not clean_text(basic.get(f, ""))]
    return {
        "name": rec.get("name"),
        "field_total": len(C.STANDARD_FIELDS),
        "field_filled": len(filled),
        "field_rate": round(len(filled) / len(C.STANDARD_FIELDS), 4),
        "missing_fields": missing,
        "has_career": bool(rec.get("career")),
        "has_works": bool(rec.get("works")),
        "has_awards": bool(rec.get("awards")),
    }
