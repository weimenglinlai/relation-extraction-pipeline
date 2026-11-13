# -*- coding: utf-8 -*-
"""
数据质量评估。

对照实践参考文档「5、数据质量评估」的四项要求逐条给出结果：
  1) 字段完整率 —— 每个字段的非空比例（要求不低于 85%）
  2) 准确率     —— 人工抽查 100 条；本模块生成抽查表，并另外跑一遍
                   「格式规则自动校验」作为参考基线
  3) 重复率     —— 重复记录比例（按 / URL / 词条名 / 内容指纹四种口径）
  4) 失败原因分类 —— 读取 logs/failed.jsonl，按状态归类

输出：
  output/quality_report.json   机器可读的完整报告
  output/质量评估报告.md        人读的报告
  output/准确率抽查表.csv       供人工核验的 100 条抽样
"""
import csv
import hashlib
import random
import re
from collections import Counter, defaultdict

import config as C
from utils import (clean_text, dump_json, get_logger, now_str, read_jsonl)

log = get_logger()

# 人工抽查条数（参考文档要求 100 条）
SAMPLE_SIZE = 100

# 领域判定的关键词 → 领域名。取「职业」字段里第一个命中的关键词。
_DOMAIN_RULES = [
    ("演员", "影视演员"), ("影视", "影视演员"), ("配音", "影视演员"),
    ("歌手", "歌手/音乐人"), ("音乐", "歌手/音乐人"), ("作词", "歌手/音乐人"),
    ("作曲", "歌手/音乐人"), ("演唱", "歌手/音乐人"),
    ("导演", "导演/编剧"), ("编剧", "导演/编剧"), ("监制", "导演/编剧"),
    ("主持人", "主持人"), ("主播", "主持人"),
    ("作家", "作家/编剧"), ("小说家", "作家/编剧"), ("诗人", "作家/编剧"),
    ("运动员", "体育运动员"), ("球", "体育运动员"), ("教练", "体育运动员"),
    ("画家", "艺术家"), ("书法", "艺术家"), ("舞蹈", "艺术家"), ("艺术家", "艺术家"),
    ("教授", "学者/教育"), ("学者", "学者/教育"), ("教师", "学者/教育"),
    ("科学家", "科学技术"), ("工程师", "科学技术"), ("院士", "科学技术"),
    ("企业家", "商界"), ("董事长", "商界"), ("总裁", "商界"), ("商人", "商界"),
    ("政治家", "政界"), ("官员", "政界"), ("总统", "政界"),
    ("相声", "曲艺/喜剧"), ("喜剧", "曲艺/喜剧"), ("小品", "曲艺/喜剧"),
    ("模特", "时尚/模特"), ("设计", "设计/创意"),
]

# 自动校验规则：字段 -> 判定函数（返回 True 表示该字段格式合规）
_DATE_RE = re.compile(r"(\d{4}\s*年|\d{4}\s*[-/]\s*\d{1,2})")
_LATIN_RE = re.compile(r"[A-Za-z]")

_AUTO_RULES = {
    "本名": lambda v: 1 <= len(v) <= 20,
    "外文名": lambda v: bool(_LATIN_RE.search(v)),
    "别名": lambda v: 1 <= len(v) <= 60,
    "国籍": lambda v: 1 <= len(v) <= 15,
    "民族": lambda v: 1 <= len(v) <= 10,
    "出生地": lambda v: 1 <= len(v) <= 40,
    "出生日期": lambda v: bool(_DATE_RE.search(v)),
    "毕业院校": lambda v: 1 <= len(v) <= 60,
    "职业": lambda v: 1 <= len(v) <= 60,
    "代表作品": lambda v: 1 <= len(v) <= 800,
    "主要成就": lambda v: 1 <= len(v) <= 800,
}


def classify_domain(rec: dict) -> str:
    """按「职业」字段归纳人物领域，用于领域分布图。"""
    text = (rec.get("basic_info") or {}).get("职业", "") or ""
    if not text:
        text = rec.get("summary", "")[:80]
    for kw, name in _DOMAIN_RULES:
        if kw in text:
            return name
    # 从「主要作品」的分类里再碰一次运气
    for w in rec.get("works", []):
        if "电影" in w.get("category", "") or "电视" in w.get("category", ""):
            return "影视演员"
        if "音乐" in w.get("category", "") or "专辑" in w.get("category", ""):
            return "歌手/音乐人"
    return "其他/未分类"


# --------------------------------------------------------------------------- #
def load_records() -> list:
    """优先用汇总后的 persons.json，没有再退回 persons.jsonl。"""
    from utils import load_json
    persons = load_json(C.PERSONS_JSON, None)
    if persons:
        return persons
    return read_jsonl(C.PERSONS_JSONL)


def field_completeness(persons: list) -> dict:
    stat = {}
    for f in C.STANDARD_FIELDS:
        filled = sum(1 for p in persons
                     if clean_text((p.get("basic_info") or {}).get(f, "")))
        stat[f] = {
            "filled": filled,
            "total": len(persons),
            "rate": round(filled / len(persons), 4) if persons else 0.0,
        }
    return stat


def duplicate_stats(persons: list) -> dict:
    """四种口径的重复率。/URL 重复说明抓取有重；内容指纹重复说明页面相同。"""
    n = len(persons)

    def _dups(keys):
        c = Counter(k for k in keys if k)
        dup_keys = {k: v for k, v in c.items() if v > 1}
        return {
            "duplicate_keys": len(dup_keys),
            "duplicate_records": sum(v - 1 for v in dup_keys.values()),
            "rate": round(sum(v - 1 for v in dup_keys.values()) / n, 4) if n else 0.0,
            "samples": list(dup_keys)[:10],
        }

    def _content_hash(p):
        basic = p.get("basic_info") or {}
        seed = "|".join([p.get("lemma_name", "")] + [f"{k}:{basic.get(k, '')}"
                                                     for k in C.STANDARD_FIELDS])
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()

    return {
        "by_name": _dups([p.get("name") for p in persons]),
        "by_url": _dups([p.get("url") for p in persons]),
        "by_lemma_name": _dups([p.get("lemma_name") for p in persons]),
        "by_content": _dups([_content_hash(p) for p in persons]),
    }


def extraction_stats(persons: list) -> dict:
    n = len(persons)
    has_basic = sum(1 for p in persons if any(
        clean_text(v) for v in (p.get("basic_info") or {}).values()))
    has_career = sum(1 for p in persons if clean_text(p.get("career", "")))
    has_works = sum(1 for p in persons if p.get("works"))
    has_awards = sum(1 for p in persons if p.get("awards"))
    return {
        "with_basic_info": has_basic,
        "with_career": has_career,
        "with_works": has_works,
        "with_awards": has_awards,
        "basic_rate": round(has_basic / n, 4) if n else 0.0,
        "career_rate": round(has_career / n, 4) if n else 0.0,
        "works_rate": round(has_works / n, 4) if n else 0.0,
        "awards_rate": round(has_awards / n, 4) if n else 0.0,
    }


def failure_stats() -> dict:
    """失败原因分类。失败记录来自 logs/failed.jsonl。"""
    rows = read_jsonl(C.FAILED_JSONL)
    by_status = Counter(r.get("status", "unknown") for r in rows)
    by_engine = Counter(r.get("engine", "") for r in rows)
    # 同类原因给一句话解释，写进报告
    explain = {
        "not_found": "词条不存在或尚未收录",
        "disambiguation_failed": "多义词页面无可用义项",
        "blocked": "命中百度安全验证（反爬拦截）",
        "timeout": "页面加载超时",
        "error": "浏览器/网络异常",
        "unknown": "未分类",
    }
    return {
        "total": len(rows),
        "by_status": {k: {"count": v, "desc": explain.get(k, "")}
                      for k, v in by_status.most_common()},
        "by_engine": dict(by_engine),
        "samples": [{"name": r.get("name"), "status": r.get("status"),
                     "error": r.get("error", "")[:120]} for r in rows[:30]],
    }


def auto_accuracy(persons: list) -> dict:
    """
    自动格式校验（参考基线，不能替代人工抽查）。

    对每条记录逐字段套用 _AUTO_RULES：字段为空记「缺失」，非空但格式不合规记「错误」。
    准确率 = 合规字段数 / 非空字段数。
    """
    total_nonempty = total_ok = 0
    per_field = defaultdict(lambda: {"checked": 0, "ok": 0})
    bad = []
    for p in persons:
        basic = p.get("basic_info") or {}
        for f, rule in _AUTO_RULES.items():
            v = clean_text(basic.get(f, ""))
            if not v:
                continue
            total_nonempty += 1
            per_field[f]["checked"] += 1
            try:
                good = bool(rule(v))
            except Exception:
                good = True
            if good:
                total_ok += 1
                per_field[f]["ok"] += 1
            elif len(bad) < 40:
                bad.append({"name": p.get("name"), "field": f, "value": v[:60]})
    return {
        "nonempty_fields": total_nonempty,
        "passed": total_ok,
        "rate": round(total_ok / total_nonempty, 4) if total_nonempty else 0.0,
        "per_field": {k: {**v, "rate": round(v["ok"] / v["checked"], 4)
                          if v["checked"] else 0.0}
                      for k, v in per_field.items()},
        "abnormal_samples": bad,
    }


def make_manual_sample(persons: list, size: int = SAMPLE_SIZE) -> str:
    """
    生成人工抽查表（CSV，utf-8-sig 便于 Excel 打开）。

    每行一条记录，列出所有标准字段的取值，并留出「查验结论」列供人工填写：
      正确 / 部分正确 / 错误 / 空
    """
    rnd = random.Random(20261012)          # 固定种子，保证可复现
    sample = persons if len(persons) <= size else rnd.sample(persons, size)
    sample = sorted(sample, key=lambda p: p.get("name", ""))

    cols = ["序号", "", "词条名", "URL"] + C.STANDARD_FIELDS + ["查验结论", "备注"]
    with open(C.ACCURACY_SAMPLE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, p in enumerate(sample, 1):
            basic = p.get("basic_info") or {}
            w.writerow([i, p.get("name", ""), p.get("lemma_name", ""), p.get("url", "")]
                       + [clean_text(basic.get(k, "")) for k in C.STANDARD_FIELDS]
                       + ["", ""])
    log.info("人工抽查表已生成：%s（%d 条）", C.ACCURACY_SAMPLE.name, len(sample))
    return str(C.ACCURACY_SAMPLE)


def manual_sample_stats() -> dict:
    """若用户已填写抽查表的「查验结论」列，则统计人工准确率。"""
    if not C.ACCURACY_SAMPLE.exists():
        return {"filled": 0, "rate": None, "note": "抽查表尚未填写"}
    ok = part = bad = blank = 0
    with open(C.ACCURACY_SAMPLE, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            v = (row.get("查验结论") or "").strip()
            if not v:
                blank += 1
            elif v in ("正确", "对", "√", "是"):
                ok += 1
            elif v in ("部分正确", "部分"):
                part += 1
            else:
                bad += 1
    filled = ok + part + bad
    if not filled:
        return {"filled": 0, "rate": None, "note": "抽查表尚未填写"}
    # 部分正确按 0.5 计
    return {
        "filled": filled, "ok": ok, "partial": part, "wrong": bad,
        "rate": round((ok + 0.5 * part) / filled, 4),
        "note": "人工抽查（部分正确按 0.5 计）",
    }


# --------------------------------------------------------------------------- #
def evaluate(save: bool = True) -> dict:
    persons = load_records()
    if not persons:
        log.warning("没有可评估的数据，请先抓取。")
        return {}

    fc = field_completeness(persons)
    report = {
        "generated": now_str(),
        "total_records": len(persons),
        "field_completeness": fc,
        "overall_field_rate": round(
            sum(v["rate"] for v in fc.values()) / len(fc), 4) if fc else 0.0,
        "all_fields_pass_85": all(v["rate"] >= 0.85 for v in fc.values()),
        "failed_fields": [k for k, v in fc.items() if v["rate"] < 0.85],
        "extraction": extraction_stats(persons),
        "duplicates": duplicate_stats(persons),
        "failures": failure_stats(),
        "auto_accuracy": auto_accuracy(persons),
        "domain_distribution": dict(
            Counter(classify_domain(p) for p in persons).most_common()),
    }
    sample_file = make_manual_sample(persons)
    report["manual_sample"] = {
        "file": sample_file,
        "size": min(len(persons), SAMPLE_SIZE),
        **manual_sample_stats(),
    }

    if save:
        dump_json(report, C.QUALITY_JSON)
        C.QUALITY_MD.write_text(render_markdown(report), encoding="utf-8")
        log.info("质量评估报告已生成：%s", C.QUALITY_MD.name)
    return report


def render_markdown(r: dict) -> str:
    """把报告渲染成 Markdown。"""
    L = []
    L.append("# 数据质量评估报告")
    L.append("")
    L.append(f"生成时间：{r['generated']}")
    L.append(f"有效记录数：**{r['total_records']}** 条")
    L.append("")

    L.append("## 一、字段完整率")
    L.append("")
    L.append("| 字段 | 非空条数 | 总数 | 完整率 | 是否达标(≥85%) |")
    L.append("| --- | ---: | ---: | ---: | :---: |")
    for f in C.STANDARD_FIELDS:
        s = r["field_completeness"].get(f)
        if not s:
            continue
        flag = "达标" if s["rate"] >= 0.85 else "**不达标**"
        L.append(f"| {f} | {s['filled']} | {s['total']} | {s['rate']*100:.1f}% | {flag} |")
    L.append("")
    L.append(f"字段总完整率（各字段平均）：**{r['overall_field_rate']*100:.1f}%**")
    if r["all_fields_pass_85"]:
        L.append("结论：所有字段完整率均达到 85% 要求。")
    else:
        L.append("结论：以下字段未达 85%：" + "、".join(r["failed_fields"]))
    L.append("")

    L.append("## 二、内容抽取覆盖")
    L.append("")
    e = r["extraction"]
    L.append("| 抽取项 | 命中条数 | 占比 |")
    L.append("| --- | ---: | ---: |")
    L.append(f"| 基本信息 | {e['with_basic_info']} | {e['basic_rate']*100:.1f}% |")
    L.append(f"| 演艺经历 | {e['with_career']} | {e['career_rate']*100:.1f}% |")
    L.append(f"| 主要作品 | {e['with_works']} | {e['works_rate']*100:.1f}% |")
    L.append(f"| 获奖记录 | {e['with_awards']} | {e['awards_rate']*100:.1f}% |")
    L.append("")

    L.append("## 三、重复率")
    L.append("")
    L.append("| 口径 | 重复组数 | 冗余条数 | 重复率 |")
    L.append("| --- | ---: | ---: | ---: |")
    for k, label in (("by_name", ""), ("by_url", "URL"),
                     ("by_lemma_name", "词条名"), ("by_content", "内容指纹")):
        d = r["duplicates"][k]
        L.append(f"| {label} | {d['duplicate_keys']} | {d['duplicate_records']} | {d['rate']*100:.2f}% |")
    L.append("")

    L.append("## 四、准确率")
    L.append("")
    a = r["auto_accuracy"]
    L.append(f"自动格式校验（参考基线）：非空字段 {a['nonempty_fields']} 个，"
             f"合规 {a['passed']} 个，合规率 **{a['rate']*100:.1f}%**。")
    L.append("")
    ms = r.get("manual_sample", {})
    rate = ms.get("rate")
    rate_txt = "—（尚未填写）" if rate is None else f"{rate * 100:.1f}%"
    L.append(f"人工抽查：抽查表 `{ms.get('file', '')}`，共 {ms.get('size', 0)} 条。")
    L.append(f"填表进度：{ms.get('filled', 0)} 条；人工准确率：{rate_txt}")
    L.append("")
    if a["abnormal_samples"]:
        L.append("格式异常样例（前 10 条）：")
        L.append("")
        L.append("|  | 字段 | 取值 |")
        L.append("| --- | --- | --- |")
        for s in a["abnormal_samples"][:10]:
            L.append(f"| {s['name']} | {s['field']} | {s['value']} |")
        L.append("")

    L.append("## 五、失败原因分类")
    L.append("")
    f = r["failures"]
    L.append(f"失败总数：{f['total']}")
    L.append("")
    if f["by_status"]:
        L.append("| 状态 | 条数 | 说明 |")
        L.append("| --- | ---: | --- |")
        for k, v in f["by_status"].items():
            L.append(f"| {k} | {v['count']} | {v['desc']} |")
    else:
        L.append("本轮没有失败记录。")
    L.append("")

    L.append("## 六、领域分布")
    L.append("")
    L.append("| 领域 | 人数 |")
    L.append("| --- | ---: |")
    for k, v in r["domain_distribution"].items():
        L.append(f"| {k} | {v} |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    rep = evaluate()
    if rep:
        print(render_markdown(rep))
