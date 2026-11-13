# -*- coding: utf-8 -*-
"""
CasRel 模型（移植到 torch 2.x / transformers 5.x）。

结构沿用原论文与参考实现：
    1) BERT 编码 -> 主语起止指针（sub_start / sub_end）
    2) 把主语向量加到每个位置的表征上，再用「关系专属」的起止指针抽出宾语
       obj_start / obj_end 的维度是 (seq_len, num_relations)，因此一次前向
       就能同时对多种关系做抽取。

相对原版代码的改动（原版是 torch1.4，跑不起来）：
  * 去掉硬编码的 `cuda:0` 与 collate 内的 `.to(device)`，由调用方控制设备；
  * 用 `binary_cross_entropy_with_logits` 替代「先 sigmoid 再 BCE」，数值更稳；
  * 解码改成「起点 -> 概率最大的合法终点」，并限制最大跨度，
    原版「遇到第一个过阈值的终点就配对」容易切出跨句的怪串；
  * 补齐 `attention_mask`，padding 不再参与编码。
"""
import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer

import config as C


class CasRel(nn.Module):
    def __init__(self, bert_path: str = None, num_rel: int = None,
                 dropout: float = None):
        super().__init__()
        bert_path = bert_path or str(C.BERT_PATH)
        self.num_rel = num_rel or len(C.RELATION_NAMES)
        self.bert = AutoModel.from_pretrained(bert_path)
        hidden = self.bert.config.hidden_size
        self.drop = nn.Dropout(dropout if dropout is not None else C.DROPOUT)
        self.sub_start = nn.Linear(hidden, 1)
        self.sub_end = nn.Linear(hidden, 1)
        self.obj_start = nn.Linear(hidden, self.num_rel)
        self.obj_end = nn.Linear(hidden, self.num_rel)
        self._init_heads()

    def _init_heads(self):
        for m in (self.sub_start, self.sub_end, self.obj_start, self.obj_end):
            nn.init.xavier_uniform_(m.weight)
            nn.init.zeros_(m.bias)

    # ------------------------------------------------------------------ #
    def encode(self, ids, mask):
        out = self.bert(input_ids=ids, attention_mask=mask)
        return out.last_hidden_state              # (B, L, H)

    def forward(self, ids, mask, sub_start_gold, sub_end_gold):
        h = self.drop(self.encode(ids, mask))
        pred_sub_start = self.sub_start(h).squeeze(-1)        # (B, L)
        pred_sub_end = self.sub_end(h).squeeze(-1)

        # 主语向量 = (start 位置表征 + end 位置表征) / 2，广播加到每个位置
        s_vec = torch.matmul(sub_start_gold.float().unsqueeze(1), h)   # (B,1,H)
        e_vec = torch.matmul(sub_end_gold.float().unsqueeze(1), h)
        h_obj = h + (s_vec + e_vec) / 2

        pred_obj_start = self.obj_start(h_obj)                # (B, L, R)
        pred_obj_end = self.obj_end(h_obj)
        return pred_sub_start, pred_sub_end, pred_obj_start, pred_obj_end


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #
def _decode_spans(start_prob, end_prob, thr=0.5, max_span=40, topk=12):
    """
    由起止概率解出一组 span。
    对每个起点取「它之后概率最高且过阈值的终点」，并限制跨度，避免跨句拼接。
    """
    n = len(start_prob)
    starts = [i for i in range(n) if start_prob[i] > thr][:topk]
    spans = []
    for s in starts:
        limit = min(n, s + max_span)
        best_e, best_p = -1, thr
        for e in range(s, limit):
            if end_prob[e] > best_p:
                best_e, best_p = e, end_prob[e]
        if best_e >= s:
            spans.append((s, best_e))
    # 去重并按长度取优（长 span 通常更完整）
    uniq = sorted(set(spans), key=lambda x: (-(x[1] - x[0]), x[0]))
    kept = []
    for s, e in uniq:
        if any(s >= s2 and e <= e2 for s2, e2 in kept):
            continue                                   # 已被更长的 span 覆盖
        kept.append((s, e))
    return sorted(kept)


def _span_text(text, span):
    s, e = span
    return text[s:e + 1]


@torch.no_grad()
def predict_triples(model: CasRel, tokenizer, text: str, id2rel: dict,
                    device="cuda", sub_thr=0.5, obj_thr=0.5,
                    max_len=None, max_sub=8, max_obj=6):
    """
    对一段文本做端到端抽取，返回 [{subject, predicate, object, subject_start, ...}]。
    """
    max_len = max_len or C.MAX_LEN
    text = text[:max_len]
    if not text.strip():
        return []
    ids = tokenizer.convert_tokens_to_ids(list(text))
    ids_t = torch.tensor([ids], dtype=torch.long, device=device)
    mask = torch.ones_like(ids_t)

    h = model.encode(ids_t, mask)
    ps = torch.sigmoid(model.sub_start(h).squeeze(-1))[0].cpu().numpy()
    pe = torch.sigmoid(model.sub_end(h).squeeze(-1))[0].cpu().numpy()

    sub_spans = _decode_spans(ps, pe, thr=sub_thr, max_span=20, topk=max_sub)
    if not sub_spans:
        return []

    # 对所有主语一次性并行算宾语（repeat 编码结果）
    n_sub = len(sub_spans)
    h_rep = h.repeat(n_sub, 1, 1)
    s_map = torch.zeros(n_sub, 1, h.shape[1], device=device)
    e_map = torch.zeros(n_sub, 1, h.shape[1], device=device)
    for i, (s, e) in enumerate(sub_spans):
        s_map[i, 0, s] = 1
        e_map[i, 0, e] = 1
    h_obj = h_rep + (torch.matmul(s_map, h_rep) + torch.matmul(e_map, h_rep)) / 2
    os_ = torch.sigmoid(model.obj_start(h_obj)).cpu().numpy()   # (n_sub, L, R)
    oe_ = torch.sigmoid(model.obj_end(h_obj)).cpu().numpy()

    triples = []
    for i, ss in enumerate(sub_spans):
        subj = _span_text(text, ss)
        if not subj.strip():
            continue
        sub_conf = float((ps[ss[0]] + pe[ss[1]]) / 2)
        for r in range(os_.shape[2]):
            spans = _decode_spans(os_[i, :, r], oe_[i, :, r],
                                  thr=obj_thr, max_span=40, topk=max_obj)
            for os_span in spans:
                obj = _span_text(text, os_span)
                if not obj.strip() or obj == subj:
                    continue
                rel = id2rel.get(r, str(r))
                obj_conf = float((os_[i, os_span[0], r] + oe_[i, os_span[1], r]) / 2)
                triples.append({
                    "subject": subj,
                    "predicate": rel,
                    "object": obj,
                    "confidence": round((sub_conf + obj_conf) / 2, 4),
                    "subject_start": int(ss[0]),
                    "subject_end": int(ss[1]),
                    "object_start": int(os_span[0]),
                    "object_end": int(os_span[1]),
                })
    return _dedup(triples)


def _dedup(triples):
    seen, out = set(), []
    for t in triples:
        k = (t["subject"], t["predicate"], t["object"])
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def load_tokenizer(path=None):
    return AutoTokenizer.from_pretrained(path or str(C.BERT_PATH))


def load_model(ckpt_path=None, device="cuda", num_rel=None):
    model = CasRel(num_rel=num_rel)
    if ckpt_path:
        state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        model.load_state_dict(state)
    return model.to(device)
