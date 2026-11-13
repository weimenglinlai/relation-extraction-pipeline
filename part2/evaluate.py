# -*- coding: utf-8 -*-
"""
关系抽取评估：按 (subject, predicate, object) 三元组严格匹配计算
Precision / Recall / F1，并给出分关系指标与错误样例。
"""
import json
from collections import Counter, defaultdict

import torch

import config as C
from casrel_model import load_model, load_tokenizer, predict_triples
from utils import dump_json, read_jsonl


def _gold_set(rec, id2rel, max_len):
    text = (rec.get("text") or "")[:max_len]
    out = set()
    for spo in rec.get("spo_list", []):
        sub = spo.get("subject", "")
        obj = spo.get("object", {})
        obj = obj.get("@value", "") if isinstance(obj, dict) else str(obj)
        rel = spo.get("predicate", "")
        if not sub or not obj:
            continue
        if sub in text and obj in text:
            out.add((sub, rel, obj))
    return out


@torch.no_grad()
def evaluate_file(model, tokenizer, path, device="cuda", limit=None,
                  sub_thr=0.5, obj_thr=0.5, keep_errors=30):
    rows = read_jsonl(path)
    if limit:
        rows = rows[:limit]

    with open(C.DATA_DIR / "relation2idx.json", encoding="utf-8") as f:
        r2i = json.load(f)
    id2rel = {v: k for k, v in r2i.items()}

    tp = pred_n = gold_n = 0
    per_rel = defaultdict(lambda: {"tp": 0, "pred": 0, "gold": 0})
    errors = []

    for rec in rows:
        text = (rec.get("text") or "")[: C.MAX_LEN]
        gold = _gold_set(rec, id2rel, C.MAX_LEN)
        triples = predict_triples(model, tokenizer, text, id2rel, device=device,
                                  sub_thr=sub_thr, obj_thr=obj_thr)
        pred = {(t["subject"], t["predicate"], t["object"]) for t in triples}

        tp += len(pred & gold)
        pred_n += len(pred)
        gold_n += len(gold)
        for (s, r, o) in pred:
            per_rel[r]["pred"] += 1
        for (s, r, o) in gold:
            per_rel[r]["gold"] += 1
        for (s, r, o) in (pred & gold):
            per_rel[r]["tp"] += 1

        if len(errors) < keep_errors and pred != gold:
            errors.append({
                "text": text,
                "gold": [list(x) for x in sorted(gold)],
                "pred": [list(x) for x in sorted(pred)],
                "missing": [list(x) for x in sorted(gold - pred)],
                "spurious": [list(x) for x in sorted(pred - gold)],
            })

    eps = 1e-9
    p = tp / (pred_n + eps)
    r = tp / (gold_n + eps)
    f1 = 2 * p * r / (p + r + eps)

    rel_metrics = {}
    for rel, c in per_rel.items():
        rp = c["tp"] / (c["pred"] + eps)
        rr = c["tp"] / (c["gold"] + eps)
        rel_metrics[rel] = {
            "precision": round(rp, 4), "recall": round(rr, 4),
            "f1": round(2 * rp * rr / (rp + rr + eps), 4),
            "pred": c["pred"], "gold": c["gold"], "tp": c["tp"],
        }

    # macro：先按关系求平均再总平均。样本极不均衡时（参演几万、民族只有几十），
    # micro 会被大类主导，macro 更能反映「每种关系学得怎么样」。
    scored = [v for v in rel_metrics.values() if v["gold"] > 0]
    macro = {
        "precision": round(sum(v["precision"] for v in scored) / len(scored), 4) if scored else 0.0,
        "recall": round(sum(v["recall"] for v in scored) / len(scored), 4) if scored else 0.0,
        "f1": round(sum(v["f1"] for v in scored) / len(scored), 4) if scored else 0.0,
        "relations": len(scored),
    }

    return {
        "file": str(path),
        "samples": len(rows),
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f1, 4),
        "macro": macro,
        "tp": tp, "pred": pred_n, "gold": gold_n,
        "per_relation": dict(sorted(rel_metrics.items(),
                                    key=lambda x: -x[1]["gold"])),
        "errors": errors,
    }


def render_markdown(m: dict, title: str = "关系抽取模型评估报告") -> str:
    L = [f"# {title}", ""]
    L.append(f"- 评估数据：`{m['file']}`")
    L.append(f"- 样本数：{m['samples']}")
    L.append(f"- 三元组：预测 {m['pred']} 个，标注 {m['gold']} 个，正确 {m['tp']} 个")
    L.append("")
    L.append("## 总体指标")
    L.append("")
    L.append("| 口径 | Precision | Recall | F1 |")
    L.append("| --- | ---: | ---: | ---: |")
    L.append(f"| micro（按三元组） | {m['precision']*100:.2f}% | "
             f"{m['recall']*100:.2f}% | {m['f1']*100:.2f}% |")
    mac = m.get("macro") or {}
    if mac:
        L.append(f"| macro（按关系平均，{mac['relations']} 种） | "
                 f"{mac['precision']*100:.2f}% | {mac['recall']*100:.2f}% | "
                 f"{mac['f1']*100:.2f}% |")
    L.append("")
    L.append("## 分关系指标")
    L.append("")
    L.append("| 关系 | 标注数 | 预测数 | 正确数 | P | R | F1 |")
    L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for rel, s in m["per_relation"].items():
        L.append(f"| {rel} | {s['gold']} | {s['pred']} | {s['tp']} | "
                 f"{s['precision']*100:.1f}% | {s['recall']*100:.1f}% | {s['f1']*100:.1f}% |")
    L.append("")
    if m.get("errors"):
        L.append("## 错误样例（前 5 条）")
        L.append("")
        for e in m["errors"][:5]:
            L.append(f"- 文本：{e['text'][:80]}")
            if e["missing"]:
                L.append(f"  - 漏抽：{e['missing'][:3]}")
            if e["spurious"]:
                L.append(f"  - 误抽：{e['spurious'][:3]}")
        L.append("")
    return "\n".join(L)


def main():
    import argparse
    from utils import ensure_utf8_stdout
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(C.CKPT_DIR / "best.pt"))
    ap.add_argument("--data", default=str(C.DATA_DIR / "dev.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(C.METRICS_JSON))
    a = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = load_tokenizer()
    model = load_model(a.ckpt, device=device)
    m = evaluate_file(model, tok, a.data, device=device, limit=a.limit)
    dump_json(m, a.out)
    print(render_markdown(m))


if __name__ == "__main__":
    main()
