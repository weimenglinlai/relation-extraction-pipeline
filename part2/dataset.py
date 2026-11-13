# -*- coding: utf-8 -*-
"""
CasRel 数据管道（字符级）。

沿用原版 CasRel 的做法：把句子按「字」切成 token，字符下标与 token 下标一一对应，
这样黄金标注里的字符位置可以直接当 token 位置用，解码时也能原样切回子串。

与原版的差异：
  * 不把 batch 搬上 GPU 写死在 collate 里（由训练循环统一 `.to(device)`）；
  * 每个样本可指定要标注哪个 subject（训练时随机取一个，避免同一句子在
    一个 batch 里重复计入多个 subject 造成梯度偏置）。
"""
import json
import random

import torch
from torch.utils.data import Dataset
from transformers import BertTokenizer

import config as C


class CasRelDataset(Dataset):
    def __init__(self, path, tokenizer: BertTokenizer, mode="train",
                 max_len: int = None, with_text: bool = False):
        self.mode = mode
        self.max_len = max_len or C.MAX_LEN
        self.tokenizer = tokenizer
        self.with_text = with_text
        self.data = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.data.append(json.loads(line))
        with open(C.DATA_DIR / "relation2idx.json", encoding="utf-8") as f:
            self.relation2idx = json.load(f)
        self.num_rel = len(self.relation2idx)

    def __len__(self):
        return len(self.data)

    def _encode(self, text):
        text = text[: self.max_len]
        ids = self.tokenizer.convert_tokens_to_ids(list(text))
        return text, ids

    def __getitem__(self, idx):
        rec = self.data[idx]
        text, ids = self._encode(rec["text"])
        n = len(text)

        sub_start = [0] * n
        sub_end = [0] * n
        rel_start = [[0] * self.num_rel for _ in range(n)]
        rel_end = [[0] * self.num_rel for _ in range(n)]

        # subject -> [(obj_span, rel_idx), ...]
        s2ro = {}
        for spo in rec.get("spo_list", []):
            sub = spo.get("subject", "")
            obj = spo.get("object", {})
            obj = obj.get("@value", "") if isinstance(obj, dict) else str(obj)
            rel = spo.get("predicate", "")
            if rel not in self.relation2idx or not sub or not obj:
                continue
            sp = text.find(sub)
            op = text.find(obj)
            if sp < 0 or op < 0:
                continue
            s2ro.setdefault((sp, sp + len(sub)), []).append(
                ((op, op + len(obj)), self.relation2idx[rel]))

        if not s2ro:
            # 整句的标注都被截断/对不上，退化成纯负样本仍可训练主语识别
            s2ro[(0, min(1, n - 1))] = []

        for (s, e) in s2ro:
            if 0 <= s < n:
                sub_start[s] = 1
            if 0 <= e - 1 < n:
                sub_end[e - 1] = 1

        # 选一个 subject 作为「本次要标注的对象」
        if self.mode == "train":
            sub_pos = random.choice(list(s2ro.keys()))
        else:
            sub_pos = next(iter(s2ro.keys()))

        sub_start_single = [0] * n
        sub_end_single = [0] * n
        if 0 <= sub_pos[0] < n:
            sub_start_single[sub_pos[0]] = 1
        if 0 <= sub_pos[1] - 1 < n:
            sub_end_single[sub_pos[1] - 1] = 1

        for (op, rel_idx) in s2ro.get(sub_pos, []):
            if 0 <= op[0] < n:
                rel_start[op[0]][rel_idx] = 1
            if 0 <= op[1] - 1 < n:
                rel_end[op[1] - 1][rel_idx] = 1

        item = {
            "ids": ids,
            "sub_start": sub_start,
            "sub_end": sub_end,
            "rel_start": rel_start,
            "rel_end": rel_end,
            "sub_start_single": sub_start_single,
            "sub_end_single": sub_end_single,
        }
        if self.with_text:
            item["text"] = text
            item["spo_list"] = rec.get("spo_list", [])
        return item


def collate(batch):
    """补齐到 batch 内最大长度，并给出 attention mask。"""
    max_len = max(len(b["ids"]) for b in batch)
    out = {"ids": [], "mask": [], "sub_start": [], "sub_end": [],
           "rel_start": [], "rel_end": [],
           "sub_start_single": [], "sub_end_single": []}
    texts, golds = [], []

    for b in batch:
        n = len(b["ids"])
        pad = max_len - n
        out["ids"].append(b["ids"] + [0] * pad)
        out["mask"].append([1] * n + [0] * pad)
        out["sub_start"].append(b["sub_start"] + [0] * pad)
        out["sub_end"].append(b["sub_end"] + [0] * pad)
        out["sub_start_single"].append(b["sub_start_single"] + [0] * pad)
        out["sub_end_single"].append(b["sub_end_single"] + [0] * pad)
        # 关系标签要按 (seq_len, num_rel) 补零
        num_rel = len(b["rel_start"][0]) if b["rel_start"] else 0
        rs = b["rel_start"] + [[0] * num_rel for _ in range(pad)]
        re_ = b["rel_end"] + [[0] * num_rel for _ in range(pad)]
        out["rel_start"].append(rs)
        out["rel_end"].append(re_)
        if "text" in b:
            texts.append(b["text"])
            golds.append(b["spo_list"])

    tensor_keys = ["ids", "mask", "sub_start", "sub_end",
                   "sub_start_single", "sub_end_single"]
    batch_out = {k: torch.tensor(out[k], dtype=torch.long) for k in tensor_keys}
    batch_out["rel_start"] = torch.tensor(out["rel_start"], dtype=torch.float)
    batch_out["rel_end"] = torch.tensor(out["rel_end"], dtype=torch.float)
    if texts:
        batch_out["texts"] = texts
        batch_out["gold"] = golds
    return batch_out
