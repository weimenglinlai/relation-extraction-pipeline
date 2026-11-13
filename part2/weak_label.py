# -*- coding: utf-8 -*-
"""
弱监督补充：DuIE2.0 里没有的 5 种关系。

DuIE 覆盖了 参演/执导/编剧/演唱/主持/国籍/籍贯/毕业院校/获得奖项 这 9 种，
但本项目的 schema 还有 出生地、民族、职业、经纪公司、合作 五种没有训练数据。
这里用项目一保存的结构化字段 + 人物名单做「弱监督」标注：

  * 出生地   <- 基本信息.出生地     要求该地名出现在句子里
  * 民族     <- 基本信息.民族
  * 职业     <- 基本信息.职业（按顿号拆成多个职业）
  * 经纪公司 <- 基本信息.经纪公司
  * 合作     <- 句子里同时出现本人与另一位人物名，且含「合作/搭档/联袂/主演」等触发词

产出与 DuIE 转换结果同格式，训练时合并使用。
"""
import sys
from collections import Counter

import config as C
from utils import (clean_text, ensure_utf8_stdout, get_logger, load_json,
                   split_multi, split_sentences, write_jsonl)

log = get_logger()

# 合作关系的触发词（宁可少标，保证精度）
COOP_TRIGGERS = ("合作", "搭档", "联袂", "共同主演", "合演", "同台", "携手")


def load_persons():
    data = load_json(C.PROJ1_PERSONS, [])
    if not data:
        raise SystemExit(f"读不到项目一数据：{C.PROJ1_PERSONS}")
    return data


def build_person_names(persons):
    """人物名 -> 归一后的名字，用于识别句子里的另一位人物（合作）。"""
    names = {}
    for p in persons:
        for key in ("name", "lemma_name"):
            n = clean_text(p.get(key, ""))
            if 2 <= len(n) <= 6:
                names[n] = n
    return names


def _add(spo_out, seen, subj, rel, objv):
    objv = clean_text(objv)
    if not objv or subj == objv:
        return
    key = (subj, rel, objv)
    if key in seen:
        return
    seen.add(key)
    spo_out.append({"subject": subj, "object": {"@value": objv},
                    "predicate": rel, "subject_type": "人物"})


def _work_objs(person):
    """从主要作品里取出 (关系, 标题)，用作弱监督的候选对象。"""
    import rules
    out = []
    for w in person.get("works") or []:
        rel = rules._work_relation(w.get("category", ""))
        rows = w.get("items") or []
        idx = rules._title_column(w.get("header") or [], rows)
        if idx < 0:
            continue
        for r in rows:
            if len(r) > idx:
                t = rules._clean_title(r[idx])
                if 1 < len(t) <= 30:
                    out.append((rel, t))
    return out


def _award_objs(person):
    out = []
    for a in person.get("awards") or []:
        header = a.get("header") or []
        idx = next((i for i, h in enumerate(header) if "奖项" in h), None)
        if idx is None:
            idx = 1 if len(header) > 1 else 0
        for r in a.get("items") or []:
            if len(r) > idx:
                v = clean_text(r[idx])
                if 2 < len(v) <= 30:
                    out.append(v)
    return out


def label_person(p, all_names):
    """对单个人物页产出弱监督样本。"""
    name = clean_text(p.get("name", ""))
    basic = p.get("basic_info") or {}
    if not name:
        return []

    # 待匹配的属性型对象
    attr_objs = []
    for field, rel in (("出生地", "出生地"), ("民族", "民族"), ("经纪公司", "经纪公司")):
        v = clean_text(basic.get(field, ""))
        if v and len(v) <= 30:
            attr_objs.append((rel, v))
    for occ in split_multi(basic.get("职业", "")):
        if 2 <= len(occ) <= 12:
            attr_objs.append(("职业", occ))

    # 作品 / 奖项类对象：让弱监督覆盖全部 14 种关系，
    # 否则测试集只剩 5 种关系，评测会严重失真
    work_objs = _work_objs(p)[:40]
    award_objs = _award_objs(p)[:30]
    attr_objs += work_objs + [("获得奖项", a) for a in award_objs]

    texts = []
    for key in ("summary", "career", "raw_text"):
        texts.append(p.get(key, "") or "")

    samples, counts = [], Counter()
    for text in texts:
        for sent in split_sentences(text):
            if name not in sent:
                continue
            spo, seen = [], set()
            # 1) 属性型关系
            for rel, objv in attr_objs:
                if objv in sent:
                    _add(spo, seen, name, rel, objv)
            # 2) 合作关系：句子里还有另一位人物名 + 触发词
            if any(t in sent for t in COOP_TRIGGERS):
                for other in all_names:
                    if other == name or other not in sent:
                        continue
                    _add(spo, seen, name, "合作", other)
            if spo:
                samples.append({"text": sent, "spo_list": spo, "page": name})
                for s in spo:
                    counts[s["predicate"]] += 1
    return samples


def main():
    ensure_utf8_stdout()
    persons = load_persons()
    all_names = build_person_names(persons)
    log.info("项目一 %d 条记录，人物名 %d 个", len(persons), len(all_names))

    rows, counts, page_hits = [], Counter(), Counter()
    for p in persons:
        ss = label_person(p, all_names)
        if ss:
            page_hits[p.get("name")] += 1
        rows.extend(ss)
        for s in ss:
            for spo in s["spo_list"]:
                counts[spo["predicate"]] += 1

    write_jsonl(rows, C.DATA_DIR / "weak_all.jsonl")
    log.info("弱监督样本 %d 条，来自 %d 个人物页", len(rows), len(page_hits))
    log.info("关系分布：%s", dict(counts.most_common()))
    print("\n产出 ->", C.DATA_DIR / "weak_all.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
