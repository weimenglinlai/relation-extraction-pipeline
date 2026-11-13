# -*- coding: utf-8 -*-
"""
流水线：抓取 -> 解析 -> 落盘。

爬完即解析、解析完即落盘，HTML 不进内存队列（见 crawler.crawl_all 的注释）。
data/persons.jsonl 是唯一事实来源：断点续爬、汇总、质量评估都基于它。
"""
import config as C
from crawler import crawl_all
from parser import parse_page, parse_quality
from utils import (dump_json, get_logger, load_json, now_str, read_jsonl,
                   load_html, load_names)

log = get_logger()


def _on_result(r):
    """抓取成功的回调：立刻解析并追加落盘。"""
    rec = parse_page(r.html, r.name, r.final_url or r.url,
                     engine=r.engine, crawl_time=r.crawl_time)
    rec["_meta"] = {
        "status": r.status,
        "html_size": len(r.html),
        "elapsed": round(r.elapsed, 2),
        "attempts": r.attempts,
        "title": r.title,
        "resolved_from_disambiguation": r.extra.get("resolved_from_disambiguation", ""),
    }
    from utils import append_jsonl
    append_jsonl(rec, C.PERSONS_JSONL)


def run(engine: str = None, workers: int = None, limit: int = None,
        resume: bool = True, names=None, on_progress=None):
    """执行一轮完整的抓取 + 解析。"""
    results, failed = crawl_all(
        names=names, engine=engine, workers=workers, limit=limit,
        resume=resume, on_result=_on_result, on_progress=on_progress,
    )
    summary = consolidate()
    return results, failed, summary


def reparse():
    """
    不重新爬，仅用归档的 HTML 重跑解析（需要 SAVE_HTML=True）。
    改了 parser.py 的抽取规则后，用它可以离线重算全部数据。
    """
    if not C.SAVE_HTML:
        log.warning("SAVE_HTML=False，没有归档 HTML，无法离线重解析。")
        return 0
    names = [row["name"] for row in read_jsonl(C.PERSONS_JSONL)]
    if not names:
        names = load_names()
    C.PERSONS_JSONL.unlink(missing_ok=True)
    n = 0
    for name in names:
        html = load_html(name)
        if not html:
            continue
        rec = parse_page(html, name, "", engine="archive", crawl_time=now_str())
        from utils import append_jsonl
        append_jsonl(rec, C.PERSONS_JSONL)
        n += 1
    log.info("离线重解析完成：%d 条", n)
    consolidate()
    return n


def consolidate() -> dict:
    """把 persons.jsonl 去重汇总成 persons.json，并给出整体统计。"""
    rows = read_jsonl(C.PERSONS_JSONL)
    latest, order = {}, []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        if name not in latest:
            order.append(name)
        latest[name] = row          # 后写的覆盖先写的

    persons = [latest[n] for n in order]
    q = [parse_quality(p) for p in persons]

    field_stat = {}
    for f in C.STANDARD_FIELDS:
        filled = sum(1 for p in persons if (p.get("basic_info") or {}).get(f))
        field_stat[f] = {
            "filled": filled,
            "total": len(persons),
            "rate": round(filled / len(persons), 4) if persons else 0.0,
        }

    summary = {
        "generated": now_str(),
        "total": len(persons),
        "field_completeness": field_stat,
        "overall_field_rate": round(
            sum(s["rate"] for s in field_stat.values()) / len(C.STANDARD_FIELDS), 4
        ) if persons else 0.0,
        "with_career": sum(1 for x in q if x["has_career"]),
        "with_works": sum(1 for x in q if x["has_works"]),
        "with_awards": sum(1 for x in q if x["has_awards"]),
        "avg_fields": round(sum(x["field_filled"] for x in q) / len(q), 2) if q else 0,
    }

    dump_json(persons, C.PERSONS_JSON)
    dump_json(summary, C.OUT_DIR / "crawl_summary.json")
    log.info("汇总完成：%d 条记录 -> %s", len(persons), C.PERSONS_JSON.name)
    return summary


def status() -> dict:
    """看一眼当前进度。"""
    snap = load_json(C.PROGRESS_JSON, {}) or {}
    names = load_names()
    done = len({r.get("name") for r in read_jsonl(C.PERSONS_JSONL)})
    snap.update({"name_file_total": len(names), "parsed_records": done})
    return snap


if __name__ == "__main__":
    print(status())
