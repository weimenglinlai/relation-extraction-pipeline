# -*- coding: utf-8 -*-
"""
批量抽取三元组：把项目一的全部人物词条跑一遍，融合三路结果。

三路来源与优先级（去重时保留置信度最高的那条）：
  1. 结构化字段  0.95  基本信息 / 主要作品 / 获奖记录，最准
  2. CasRel 模型  动态  在正文句子上端到端抽，覆盖结构化里没有的表述
  3. 句式模板    0.70  兜底，模型漏抽时补上

产出：
  data/triples.json     最终三元组
  data/triples.csv      便于 Excel 查看
  output/三元组统计.md   分布统计
"""
import csv
import re
import sys
from collections import Counter, defaultdict

import torch

import config as C
import rules
from casrel_model import load_model, load_tokenizer, predict_triples
from utils import (clean_text, dump_json, ensure_utf8_stdout, get_logger,
                   load_json, split_sentences)

log = get_logger()

_BAD_OBJ = re.compile(r"^[\d\s\-/年月日\.%~]+$")
_BOOK_MARK = re.compile(r"[《》〈〉]")
# 只有「作品类」关系才做长度/谓语过滤：
# 奖项名本身就长，而且常含「最佳编剧」「最佳作词」，不能一起套用。
_WORK_RELS = ("参演", "执导", "编剧", "演唱", "主持")
_GARBLED = re.compile(r"(演唱|主演|执导|参演|饰演|编剧|采样|前奏|作词|作曲)")


def normalize_object(obj: str, relation: str) -> str:
    obj = clean_text(obj)
    # 书名号整体去掉：「《新大头儿子和小头爸爸》第二季」->「新大头儿子和小头爸爸第二季」
    obj = _BOOK_MARK.sub("", obj).strip("，。、；：,.;:【】[]（）()\"“”")
    if not obj:
        return ""
    if _BAD_OBJ.match(obj):
        return ""
    if relation in _WORK_RELS:
        # 作品名不该这么长，也不该自带谓语动词——那是整段被误切
        if len(obj) > 20 or _GARBLED.search(obj):
            return ""
    if len(obj) > 40:
        return ""
    # 作品/奖项类不应该带时间
    if relation in _WORK_RELS or relation == "获得奖项":
        obj = re.sub(r"^(\d{4}年?|\d{4}-\d{1,2}(-\d{1,2})?)\s*", "", obj)
    return obj


class Extractor:
    def __init__(self, use_model=True, ckpt=None, device=None, thresholds=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.use_model = use_model
        if thresholds is None:
            # 训练后由 tune.py 搜索出的最优阈值；没有就用默认 0.5
            th = load_json(C.DATA_DIR / "thresholds.json", {}) or {}
            b = th.get("best") or {}
            thresholds = (b.get("sub_thr", 0.5), b.get("obj_thr", 0.5))
        self.sub_thr, self.obj_thr = thresholds
        log.info("解码阈值：主语 %.2f / 宾语 %.2f", self.sub_thr, self.obj_thr)
        self.id2rel = {}
        self.model = self.tokenizer = None
        if use_model:
            ckpt = ckpt or (C.CKPT_DIR / "best.pt")
            if not ckpt.exists():
                log.warning("找不到模型权重 %s，本次只用规则", ckpt)
                self.use_model = False
            else:
                self.tokenizer = load_tokenizer()
                self.model = load_model(ckpt, device=self.device)
                self.model.eval()
                r2i = load_json(C.DATA_DIR / "relation2idx.json", {})
                self.id2rel = {v: k for k, v in r2i.items()}
                log.info("模型已加载：%s（%d 类关系）", ckpt.name, len(self.id2rel))

    # ------------------------------------------------------------------ #
    def by_model(self, text: str, max_sents: int = 120):
        if not self.use_model:
            return []
        out = []
        for sent in split_sentences(text)[:max_sents]:
            for t in predict_triples(self.model, self.tokenizer, sent, self.id2rel,
                                     device=self.device, sub_thr=self.sub_thr,
                                     obj_thr=self.obj_thr):
                t["source"] = "model"
                out.append(t)
        return out

    def extract_person(self, person: dict, known_names=None) -> list:
        name = clean_text(person.get("name", ""))
        if not name:
            return []
        merged = {}

        def put(t, page):
            obj = normalize_object(t["object"], t["predicate"])
            subj = clean_text(t["subject"])
            if not obj or not subj:
                return
            # 主语白名单：模型常把「该片」「中国内地」这类片段当成主语，
            # 只保留本项目语料里真实存在的人物名（含本期词条本人）。
            if known_names is not None and subj not in known_names:
                return
            key = (subj, t["predicate"], obj)
            conf = float(t.get("confidence", 0.6))
            old = merged.get(key)
            if old is None or conf > old["confidence"]:
                merged[key] = {"subject": subj, "predicate": t["predicate"],
                               "object": obj, "confidence": round(conf, 4),
                               "source": t.get("source", ""), "page": page}

        for t in rules.triples_from_structured(person):
            put(t, name)
        for key in ("summary", "career", "raw_text"):
            txt = person.get(key, "") or ""
            if not txt:
                continue
            for t in rules.triples_from_text(txt, name):
                put(t, name)
            for t in self.by_model(txt):
                put(t, name)
        return list(merged.values())


def derive_cooperation(triples, max_per_work=10):
    """
    由「同作品」推导合作关系。

    合作关系从单句文本里抽不稳（一句话往往只提一个人），但图上有很确定的信号：
    两位人物都参与了同一部作品。这里按作品聚合后两两连边，
    让图谱从「一堆孤立星」变成有横线的连通图。
    """
    works = {}
    for t in triples:
        if t["predicate"] in ("参演", "执导", "编剧", "演唱", "主持"):
            works.setdefault(t["object"], set()).add(t["subject"])

    out = []
    for work, people in works.items():
        ps = sorted(people)
        if len(ps) < 2 or len(ps) > max_per_work:
            continue
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                out.append({"subject": ps[i], "predicate": "合作", "object": ps[j],
                            "confidence": 0.75, "source": "derived", "page": ps[i]})
    return out


def run(limit=None, use_model=True, min_conf=None, out=None):
    persons = load_json(C.PROJ1_PERSONS, [])
    if not persons:
        raise SystemExit(f"读不到项目一数据：{C.PROJ1_PERSONS}")
    if limit:
        persons = persons[:limit]

    ex = Extractor(use_model=use_model)
    # 主语白名单：全部词条的人名 / 词条名（模型可能抽到别人的名字，那些是正常的）
    known = set()
    for p in persons:
        for k in ("name", "lemma_name"):
            v = clean_text(p.get(k, ""))
            if v:
                known.add(v)
    log.info("主语白名单 %d 个名字", len(known))

    all_triples, by_source = [], Counter()
    for i, p in enumerate(persons, 1):
        ts = ex.extract_person(p, known_names=known)
        all_triples.extend(ts)
        for t in ts:
            by_source[t["source"]] += 1
        if i % 20 == 0:
            log.info("已处理 %d/%d 人，累计 %d 条三元组", i, len(persons), len(all_triples))

    # 全局去重
    best = {}
    for t in all_triples:
        k = (t["subject"], t["predicate"], t["object"])
        if k not in best or t["confidence"] > best[k]["confidence"]:
            best[k] = t
    triples = sorted(best.values(), key=lambda x: (-x["confidence"], x["subject"]))

    # 由同作品共现推导「合作」，并在去重后追加
    extra = 0
    for t in derive_cooperation(triples):
        k = (t["subject"], t["predicate"], t["object"])
        if k not in best:
            best[k] = t
            extra += 1
    if extra:
        log.info("由同作品共现推导出 %d 条「合作」关系", extra)
    triples = sorted(best.values(), key=lambda x: (-x["confidence"], x["subject"]))

    thr = C.MIN_CONFIDENCE if min_conf is None else min_conf
    triples = [t for t in triples if t["confidence"] >= thr]

    out = out or C.TRIPLES_JSON
    dump_json(triples, out)
    _write_csv(triples)
    write_stats(triples, len(persons))
    log.info("三元组 %d 条 -> %s", len(triples), out)
    log.info("来源分布：%s", dict(by_source))
    return triples


def _write_csv(triples):
    with open(C.TRIPLES_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Subject", "Predicate", "Object", "Confidence", "Source", "Page"])
        for t in triples:
            w.writerow([t["subject"], t["predicate"], t["object"],
                        t["confidence"], t["source"], t["page"]])


def write_stats(triples, n_pages):
    rel = Counter(t["predicate"] for t in triples)
    src = Counter(t["source"] for t in triples)
    subj = Counter(t["subject"] for t in triples)
    L = ["# 三元组抽取统计", ""]
    L.append(f"- 处理人物词条：{n_pages} 个")
    L.append(f"- 三元组总数：{len(triples)}")
    L.append(f"- 涉及主体（人物）：{len(subj)} 位")
    L.append(f"- 平均每人：{len(triples)/max(1,len(subj)):.1f} 条")
    L.append("")
    L.append("## 按关系分布")
    L.append("")
    L.append("| 关系 | 数量 | 占比 |")
    L.append("| --- | ---: | ---: |")
    for r, c in rel.most_common():
        L.append(f"| {r} | {c} | {c/len(triples)*100:.1f}% |")
    L.append("")
    L.append("## 按来源分布")
    L.append("")
    L.append("| 来源 | 数量 | 占比 |")
    L.append("| --- | ---: | ---: |")
    name = {"structured": "结构化字段", "model": "CasRel 模型", "rule": "句式模板"}
    for s, c in src.most_common():
        L.append(f"| {name.get(s, s)} | {c} | {c/len(triples)*100:.1f}% |")
    L.append("")
    L.append("## 三元组最多的人物 Top 20")
    L.append("")
    L.append("| 人物 | 三元组数 |")
    L.append("| --- | ---: |")
    for s, c in subj.most_common(20):
        L.append(f"| {s} | {c} |")
    L.append("")
    C.TRIPLE_STATS_MD.write_text("\n".join(L), encoding="utf-8")


def main():
    import argparse
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--min-conf", type=float, default=None)
    a = ap.parse_args()
    run(limit=a.limit, use_model=not a.no_model, min_conf=a.min_conf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
