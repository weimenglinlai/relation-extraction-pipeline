# -*- coding: utf-8 -*-
"""
项目一命令行入口。

常用：
    python main.py crawl --engine selenium --workers 4        # 全量抓取（可随时 Ctrl+C，重跑自动续）
    python main.py crawl --limit 20                           # 小样本试跑
    python main.py status                                     # 看进度
    python main.py evaluate                                   # 只重跑质量评估
    python main.py charts                                     # 只重画图表
    python main.py all                                        # 抓取 + 汇总 + 评估 + 出图
    python main.py one 刘德华                                  # 抓单人并打印提取结果
    python main.py gui                                        # 启动图形界面

抓取过程中断后，直接重跑同一条命令即可从断点继续（依据 data/persons.jsonl）。
"""
import argparse
import json
import sys

import config as C


def _print_status():
    import pipeline
    s = pipeline.status()
    if not s:
        print("尚无进度快照，可能还没跑过抓取。")
        return
    print("=== 抓取进度 ===")
    for k in ("updated", "engine", "name_total", "ok", "failed", "remaining",
              "ok_rate", "parsed_records", "last_run_seconds"):
        if k in s:
            print(f"  {k:18s}: {s[k]}")
    fd = s.get("failed_detail") or {}
    if fd:
        from collections import Counter
        print("  失败原因分布      :", dict(Counter(fd.values())))


def cmd_crawl(a):
    import pipeline
    res, failed, summary = pipeline.run(
        engine=a.engine, workers=a.workers, limit=a.limit, resume=not a.no_resume)
    print(f"\n本轮成功 {len(res)}，失败 {len(failed)}")
    print(f"累计有效记录 {summary.get('total')} 条")
    print(f"字段总完整率 {summary.get('overall_field_rate', 0) * 100:.1f}%")


def cmd_status(a):
    _print_status()


def cmd_evaluate(a):
    import quality
    rep = quality.evaluate()
    if rep:
        print(f"记录数 {rep['total_records']}")
        print(f"字段总完整率 {rep['overall_field_rate'] * 100:.1f}%"
              f"（全部达标：{'是' if rep['all_fields_pass_85'] else '否'}）")
        print(f"自动格式校验准确率 {rep['auto_accuracy']['rate'] * 100:.1f}%")
        print(f"报告已写入：{C.QUALITY_MD}")
        print(f"人工抽查表：{C.ACCURACY_SAMPLE}")


def cmd_charts(a):
    import visualize
    for p in visualize.run_all():
        print("已生成", p)


def cmd_all(a):
    cmd_crawl(a)
    print()
    cmd_evaluate(a)
    print()
    cmd_charts(a)


def cmd_one(a):
    from crawler import fetch_one
    from parser import parse_page
    r = fetch_one(a.target, engine=a.engine)
    if not r.ok:
        print(f"抓取失败：{r.status} —— {r.error}")
        return 1
    rec = parse_page(r.html, r.name, r.final_url or r.url, engine=r.engine,
                     crawl_time=r.crawl_time)
    print(json.dumps({
        "lemma_name": rec["lemma_name"],
        "url": rec["url"],
        "basic_info": rec["basic_info"],
        "career_len": len(rec["career"]),
        "works": [{"category": w["category"], "count": w["count"]} for w in rec["works"]],
        "awards": [{"group": x.get("group"), "rows": len(x.get("items", []))}
                   for x in rec["awards"]],
        "sections": [s["name"] for s in rec["sections"]],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_gui(a):
    import gui
    gui.main()


def build_parser():
    p = argparse.ArgumentParser(description="人物信息抓取（百度百科）")
    sub = p.add_subparsers(dest="cmd")

    def add_common(sp):
        sp.add_argument("--engine", default=C.DEFAULT_ENGINE,
                        choices=["selenium", "requests"], help="抓取引擎")
        sp.add_argument("--workers", type=int, default=C.DEFAULT_WORKERS, help="线程数")
        sp.add_argument("--limit", type=int, default=None, help="只抓前 N 个（试跑用）")

    sp = sub.add_parser("crawl", help="批量抓取 + 汇总")
    add_common(sp)
    sp.add_argument("--no-resume", action="store_true", help="不跳过已完成，从头抓")
    sp.set_defaults(func=cmd_crawl)

    sub.add_parser("status", help="查看进度").set_defaults(func=cmd_status)
    sub.add_parser("evaluate", help="数据质量评估").set_defaults(func=cmd_evaluate)
    sub.add_parser("charts", help="生成可视化图表").set_defaults(func=cmd_charts)
    sub.add_parser("gui", help="启动 GUI").set_defaults(func=cmd_gui)

    sp = sub.add_parser("all", help="抓取 + 汇总 + 评估 + 出图")
    add_common(sp)
    sp.add_argument("--no-resume", action="store_true")
    sp.set_defaults(func=cmd_all)

    sp = sub.add_parser("one", help="抓单个并打印提取结果")
    sp.add_argument("target", help="人名或百度百科 URL")
    sp.add_argument("--engine", default=C.DEFAULT_ENGINE, choices=["selenium", "requests"])
    sp.set_defaults(func=cmd_one)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
