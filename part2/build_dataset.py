# -*- coding: utf-8 -*-
"""
合并两路训练数据并划分数据集。

* DuIE2.0 转换结果：通用领域、关系覆盖 9 种，量最大；
* 项目一弱监督结果：目标领域、补上 出生地/民族/职业/经纪公司/合作。

划分策略：
  * DuIE 用官方 train/dev；
  * 弱监督样本按「人物页」划分 70/15/15，保证测试集里的人物在训练时没见过。
最终产出：
  data/train.jsonl  训练
  data/dev.jsonl    验证（选模型）
  data/test.jsonl   测试（目标领域，未见过的真实人物词条）
"""
import random
import sys
from collections import Counter

import config as C
from utils import (ensure_utf8_stdout, get_logger, load_json, read_jsonl,
                   write_jsonl)

log = get_logger()

# 单个关系在弱监督数据里的上限，避免「职业」把其他关系压死
WEAK_CAP = {"职业": 700, "出生地": 400, "合作": 120, "民族": 60, "经纪公司": 60}


def rel_counter(rows):
    c = Counter()
    for r in rows:
        for s in r["spo_list"]:
            c[s["predicate"]] += 1
    return c


def cap_weak(rows):
    """按关系上限做降采样（按页整体丢弃，避免同一页信息被切碎）。"""
    kept, counts = [], Counter()
    for r in rows:
        # 一条样本可能含多个关系，只要有一个关系超限就整条跳过
        rels = {s["predicate"] for s in r["spo_list"]}
        if any(counts[x] >= WEAK_CAP.get(x, 10 ** 9) for x in rels):
            continue
        kept.append(r)
        for x in rels:
            counts[x] += 1
    return kept


def main():
    ensure_utf8_stdout()
    duie_train = read_jsonl(C.DATA_DIR / "duie_train.jsonl")
    duie_dev = read_jsonl(C.DATA_DIR / "duie_dev.jsonl")
    if not duie_train:
        log.error("没有 DuIE 训练数据，请先运行 prepare_duie.py")
        return 1

    weak = read_jsonl(C.DATA_DIR / "weak_all.jsonl")
    weak = cap_weak(weak)

    pages = sorted({r.get("page", "") for r in weak if r.get("page")})
    rnd = random.Random(C.SEED)
    rnd.shuffle(pages)
    n = len(pages)
    n_tr = int(n * C.SPLIT_RATIO[0])
    n_dv = int(n * C.SPLIT_RATIO[1])
    train_pages = set(pages[:n_tr])
    dev_pages = set(pages[n_tr:n_tr + n_dv])
    test_pages = set(pages[n_tr + n_dv:])

    weak_tr = [r for r in weak if r.get("page") in train_pages]
    weak_dv = [r for r in weak if r.get("page") in dev_pages]
    weak_te = [r for r in weak if r.get("page") in test_pages]

    train = duie_train + weak_tr
    dev = duie_dev + weak_dv
    test = weak_te                       # 目标领域测试集：纯粹的未见人物

    rnd.shuffle(train)
    write_jsonl(train, C.DATA_DIR / "train.jsonl")
    write_jsonl(dev, C.DATA_DIR / "dev.jsonl")
    write_jsonl(test, C.DATA_DIR / "test.jsonl")

    log.info("人物页划分：训练 %d / 验证 %d / 测试 %d", len(train_pages),
             len(dev_pages), len(test_pages))
    for name, rows in (("train", train), ("dev", dev), ("test", test)):
        c = rel_counter(rows)
        log.info("%-5s %6d 条  关系分布 %s", name, len(rows), dict(c.most_common()))
    print("\n产出：data/train.jsonl / dev.jsonl / test.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
