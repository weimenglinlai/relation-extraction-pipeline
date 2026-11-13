# -*- coding: utf-8 -*-
"""
把 DuIE2.0 转换成本项目 schema 的关系空间，供 CasRel 训练。

DuIE2.0 原始格式（JSON Lines）：
    {"text": "...", "spo_list": [{"predicate": "导演", "subject": "辣手神探",
      "object": {"@value": "吴宇森"}, "subject_type": "影视作品",
      "object_type": {"@value": "人物"}}]}

本项目的 schema 以「人物」为中心（人物 -> 作品/奖项/机构/地点…），
所以要做两件事：
  1. 谓词映射：DuIE 的 48 个谓词里只有一部分落在我们的 14 种关系内；
  2. 方向归一：DuIE 的「主演/导演/编剧/歌手/主持人」是「作品 -> 人物」，
     与我们的「人物 -> 作品」相反，需要把主客体对调。

命令：
    python prepare_duie.py
"""
import json
import sys
from collections import Counter

import config as C
from utils import ensure_utf8_stdout

# DuIE 谓词 -> (本项目关系, 是否对调主客体)
# 「获奖」的 object 是复杂结构体，取其中的 @value（奖项名）
DUIE_MAP = {
    "国籍": ("国籍", False),
    "祖籍": ("籍贯", False),
    "毕业院校": ("毕业院校", False),
    "主演": ("参演", True),
    "导演": ("执导", True),
    "编剧": ("编剧", True),
    "歌手": ("演唱", True),
    "主持人": ("主持", True),
    "获奖": ("获得奖项", False),
}

DUIE_DIR = C.BASE_DIR / "DuIE2.0"
SRC_TRAIN = DUIE_DIR / "duie_train.json" / "duie_train.json"
SRC_DEV = DUIE_DIR / "duie_dev.json" / "duie_dev.json"
SRC_TEST = DUIE_DIR / "duie_test2.json" / "duie_test2.json"

OUT_TRAIN = C.DATA_DIR / "duie_train.jsonl"
OUT_DEV = C.DATA_DIR / "duie_dev.jsonl"
OUT_TEST = C.DATA_DIR / "duie_test.jsonl"
OUT_R2I = C.DATA_DIR / "relation2idx.json"


def _obj_value(obj):
    """DuIE 的 object 可能是字符串，也可能是 {'@value': ...} 结构体。"""
    if isinstance(obj, dict):
        v = obj.get("@value", "")
        return v if isinstance(v, str) else ""
    return obj if isinstance(obj, str) else ""


def _sub_value(sub):
    if isinstance(sub, dict):
        v = sub.get("@value", "")
        return v if isinstance(v, str) else ""
    return sub if isinstance(sub, str) else ""


def convert(src, dst) -> dict:
    kept, drop_clip, drop_span, drop_noent = 0, 0, 0, 0
    rel_counter = Counter()
    with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            text = rec.get("text") or ""
            if len(text) < 4:
                drop_noent += 1
                continue
            # 与原始 CasRel 一致：超长文本截断，保证字符位置与 token 位置一一对应
            if len(text) > 512:
                text = text[:512]
                drop_clip += 1

            spo_out, seen = [], set()
            for spo in rec.get("spo_list") or []:
                pred = spo.get("predicate")
                if pred not in DUIE_MAP:
                    continue
                rel, swap = DUIE_MAP[pred]
                subj = _sub_value(spo.get("subject"))
                objv = _obj_value(spo.get("object"))
                if not subj or not objv:
                    continue
                if swap:
                    subj, objv = objv, subj        # 作品 -> 人物 翻成 人物 -> 作品

                # 主客体必须真实出现在文本里，否则无法定位黄金标注位
                if subj not in text or objv not in text:
                    drop_span += 1
                    continue
                key = (subj, rel, objv)
                if key in seen:
                    continue
                seen.add(key)
                spo_out.append({
                    "subject": subj,
                    "object": {"@value": objv},
                    "predicate": rel,
                    "subject_type": "人物",
                })
                rel_counter[rel] += 1

            if not spo_out:
                drop_noent += 1
                continue
            fout.write(json.dumps({"text": text, "spo_list": spo_out},
                                  ensure_ascii=False) + "\n")
            kept += 1

    print(f"{dst.name}: 保留 {kept} 条；丢弃(无可用关系/过短) {drop_noent}，"
          f"超长截断 {drop_clip}，主客体不在文本内 {drop_span}")
    print("  关系分布：", dict(rel_counter.most_common()))
    return {"kept": kept, "rels": dict(rel_counter)}


def write_relation2idx():
    """CasRel 需要 relation2idx.json；这里用本项目的关系表（不含 NA）。"""
    mapping = {r: i for i, r in enumerate(C.RELATION_NAMES)}
    with open(OUT_R2I, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"relation2idx: {len(mapping)} 个关系 -> {OUT_R2I}")
    return mapping


def main():
    ensure_utf8_stdout()
    for p in (SRC_TRAIN, SRC_DEV):
        if not p.exists():
            print(f"缺少 DuIE 数据：{p}")
            return 1
    print("=== 转换 DuIE2.0 -> 本项目关系空间 ===")
    convert(SRC_TRAIN, OUT_TRAIN)
    convert(SRC_DEV, OUT_DEV)
    if SRC_TEST.exists():
        convert(SRC_TEST, OUT_TEST)
    write_relation2idx()
    print("\n完成。下一步：python weak_label.py  （补充 DuIE 未覆盖的关系）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
