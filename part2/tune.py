# -*- coding: utf-8 -*-
"""
阈值调优：CasRel 解码时主语/宾语各有一个人工阈值，直接影响 P/R 的平衡。

训练完在 dev 子集上网格搜索，把最优组合写进 data/thresholds.json，
extract.py 会自动读取；找不到就用 config 里的默认 0.5。
"""
import itertools
import json
import sys

import torch

import config as C
from casrel_model import load_model, load_tokenizer
from evaluate import evaluate_file, render_markdown
from utils import dump_json, ensure_utf8_stdout, load_json

GRID = [0.30, 0.40, 0.50, 0.60, 0.70]


def main():
    import argparse
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="在多少条 dev 上搜索")
    ap.add_argument("--ckpt", default=str(C.CKPT_DIR / "best.pt"))
    a = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = load_tokenizer()
    model = load_model(a.ckpt, device=device)
    model.eval()

    best = {"f1": -1}
    rows = []
    for sub_t, obj_t in itertools.product(GRID, GRID):
        m = evaluate_file(model, tok, C.DATA_DIR / "dev.jsonl", device=device,
                          limit=a.limit, sub_thr=sub_t, obj_thr=obj_t, keep_errors=0)
        rows.append({"sub_thr": sub_t, "obj_thr": obj_t,
                     "precision": m["precision"], "recall": m["recall"],
                     "f1": m["f1"]})
        print(f"sub={sub_t:.1f} obj={obj_t:.1f}  P={m['precision']:.4f} "
              f"R={m['recall']:.4f}  F1={m['f1']:.4f}")
        if m["f1"] > best["f1"]:
            best = {"sub_thr": sub_t, "obj_thr": obj_t,
                    "precision": m["precision"], "recall": m["recall"],
                    "f1": m["f1"]}

    print(f"\n最优：sub={best['sub_thr']} obj={best['obj_thr']} F1={best['f1']:.4f}")
    dump_json({"best": best, "grid": rows}, C.DATA_DIR / "thresholds.json")

    # 用最优阈值重跑完整评估，刷新 metrics.json / 模型评估报告.md
    print("用最优阈值重新评估 dev 与 test …")
    dev_m = evaluate_file(model, tok, C.DATA_DIR / "dev.jsonl", device=device,
                          sub_thr=best["sub_thr"], obj_thr=best["obj_thr"])
    test_m = evaluate_file(model, tok, C.DATA_DIR / "test.jsonl", device=device,
                           sub_thr=best["sub_thr"], obj_thr=best["obj_thr"])
    old = load_json(C.METRICS_JSON, {}) or {}
    dump_json({"history": old.get("history", []), "dev": dev_m, "test": test_m,
               "thresholds": {"sub_thr": best["sub_thr"], "obj_thr": best["obj_thr"]},
               "config": old.get("config", {})}, C.METRICS_JSON)

    md = ["# 关系抽取模型评估报告", "",
          f"解码阈值：主语 {best['sub_thr']:.2f} / 宾语 {best['obj_thr']:.2f}"
          f"（在 dev 上网格搜索得到）", "",
          render_markdown(dev_m, "一、DuIE2.0 验证集"), "",
          render_markdown(test_m, "二、目标领域测试集（项目一未见人物）")]
    C.METRICS_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"dev  : P {dev_m['precision']:.4f} R {dev_m['recall']:.4f} F1 {dev_m['f1']:.4f}")
    print(f"test : P {test_m['precision']:.4f} R {test_m['recall']:.4f} F1 {test_m['f1']:.4f}")

    md2 = ["# 解码阈值调优", "",
           f"在 dev 的 {a.limit} 条样本上网格搜索主客体阈值：", "",
           "| 主语阈值 | 宾语阈值 | P | R | F1 |", "| ---: | ---: | ---: | ---: | ---: |"]
    for r in rows:
        star = " **←最优**" if (r["sub_thr"] == best["sub_thr"]
                                and r["obj_thr"] == best["obj_thr"]) else ""
        md2.append(f"| {r['sub_thr']:.1f} | {r['obj_thr']:.1f} | "
                   f"{r['precision']*100:.2f}% | {r['recall']*100:.2f}% | "
                   f"{r['f1']*100:.2f}%{star} |")
    (C.OUT_DIR / "阈值调优.md").write_text("\n".join(md2), encoding="utf-8")
    print("已写入 data/thresholds.json、output/metrics.json、output/模型评估报告.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
