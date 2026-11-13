# -*- coding: utf-8 -*-
"""
训练 CasRel 关系抽取模型。

用法：
    python train.py                     # 完整训练（默认 6 epoch）
    python train.py --limit 2000 --epochs 1   # 冒烟测试，几分钟跑完
    python train.py --epochs 8 --batch-size 24
"""
import argparse
import json
import time

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from transformers import get_linear_schedule_with_warmup

import config as C
from casrel_model import CasRel, load_tokenizer
from dataset import CasRelDataset, collate
from evaluate import evaluate_file, render_markdown
from utils import dump_json, ensure_utf8_stdout, get_logger

log = get_logger()


def masked_bce(logits, gold, mask):
    loss = F.binary_cross_entropy_with_logits(logits, gold, reduction="none")
    if logits.dim() == 3:
        m = mask.unsqueeze(-1).expand_as(loss).float()
    else:
        m = mask.float()
    return (loss * m).sum() / m.sum().clamp(min=1.0)


def build_loader(path, tokenizer, mode, batch_size, limit=None, shuffle=True):
    ds = CasRelDataset(path, tokenizer, mode=mode)
    if limit:
        ds.data = ds.data[:limit]
        log.info("%s 截断到 %d 条（调试用）", path.name if hasattr(path, "name") else path, len(ds.data))
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                      collate_fn=collate, num_workers=0, drop_last=False)


def train_one_epoch(model, loader, optimizer, scheduler, scaler, device, epoch):
    model.train()
    total, n = 0.0, 0
    t0 = time.time()
    for step, batch in enumerate(loader, 1):
        ids = batch["ids"].to(device)
        mask = batch["mask"].to(device)
        gold_sub_s = batch["sub_start"].to(device)
        gold_sub_e = batch["sub_end"].to(device)
        gold_obj_s = batch["rel_start"].to(device)
        gold_obj_e = batch["rel_end"].to(device)
        cond_s = batch["sub_start_single"].to(device)
        cond_e = batch["sub_end_single"].to(device)

        with torch.amp.autocast("cuda", dtype=torch.float16,
                                enabled=(device == "cuda")):
            ps, pe, os_, oe = model(ids, mask, cond_s, cond_e)
            loss = (masked_bce(ps, gold_sub_s.float(), mask)
                    + masked_bce(pe, gold_sub_e.float(), mask)
                    + masked_bce(os_, gold_obj_s, mask)
                    + masked_bce(oe, gold_obj_e, mask))

        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        scaler.step(optimizer)
        scaler.update()
        scheduler.step()

        total += float(loss.detach())
        n += 1
        if step % 100 == 0:
            log.info("epoch %d  step %d/%d  loss %.4f  %.1fs",
                     epoch, step, len(loader), total / n, time.time() - t0)
    return total / max(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=C.EPOCHS)
    ap.add_argument("--batch-size", type=int, default=C.BATCH_SIZE)
    ap.add_argument("--lr", type=float, default=C.LR)
    ap.add_argument("--limit", type=int, default=None, help="只用前 N 条训练（调试）")
    ap.add_argument("--eval-limit", type=int, default=600, help="每轮验证用多少条")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()

    ensure_utf8_stdout()
    device = "cpu" if a.cpu or not torch.cuda.is_available() else "cuda"
    log.info("设备：%s", device)

    tokenizer = load_tokenizer()
    train_loader = build_loader(C.DATA_DIR / "train.jsonl", tokenizer, "train",
                                a.batch_size, limit=a.limit, shuffle=True)
    log.info("训练集：%d 条", len(train_loader.dataset))

    model = CasRel().to(device)
    no_decay = ("bias", "LayerNorm.weight")
    params = [
        {"params": [p for n, p in model.named_parameters()
                    if not any(nd in n for nd in no_decay)], "weight_decay": 0.01},
        {"params": [p for n, p in model.named_parameters()
                    if any(nd in n for nd in no_decay)], "weight_decay": 0.0},
    ]
    optimizer = torch.optim.AdamW(params, lr=a.lr)
    total_steps = len(train_loader) * a.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, int(total_steps * C.WARMUP_RATIO), total_steps)
    scaler = torch.amp.GradScaler("cuda", enabled=(device == "cuda"))

    C.CKPT_DIR.mkdir(parents=True, exist_ok=True)
    best_path = C.CKPT_DIR / "best.pt"
    best_f1 = -1.0
    history = []

    for epoch in range(1, a.epochs + 1):
        loss = train_one_epoch(model, train_loader, optimizer, scheduler,
                               scaler, device, epoch)
        log.info("epoch %d 训练完，平均 loss %.4f", epoch, loss)

        # 每轮在 dev 子集上算一次 F1，用来选模型
        m = evaluate_file(model, tokenizer, C.DATA_DIR / "dev.jsonl",
                          device=device, limit=a.eval_limit, keep_errors=0)
        log.info("epoch %d 验证：P %.4f  R %.4f  F1 %.4f（%d 条）",
                 epoch, m["precision"], m["recall"], m["f1"], m["samples"])
        history.append({"epoch": epoch, "loss": round(loss, 4),
                        "val_precision": m["precision"],
                        "val_recall": m["recall"], "val_f1": m["f1"]})
        if m["f1"] > best_f1:
            best_f1 = m["f1"]
            torch.save(model.state_dict(), best_path)
            log.info("  已保存最优模型 -> %s（F1 %.4f）", best_path, best_f1)

    # ---- 训练结束：用最优模型跑完整 dev 与目标领域 test ----
    log.info("加载最优模型做最终评估：%s", best_path)
    model.load_state_dict(torch.load(best_path, map_location=device, weights_only=False))
    dev_m = evaluate_file(model, tokenizer, C.DATA_DIR / "dev.jsonl", device=device)
    test_m = evaluate_file(model, tokenizer, C.DATA_DIR / "test.jsonl", device=device)
    log.info("最终 dev ：P %.4f R %.4f F1 %.4f", dev_m["precision"], dev_m["recall"], dev_m["f1"])
    log.info("目标域 test：P %.4f R %.4f F1 %.4f", test_m["precision"], test_m["recall"], test_m["f1"])

    dump_json({"history": history, "dev": dev_m, "test": test_m,
               "config": {"epochs": a.epochs, "batch_size": a.batch_size,
                          "lr": a.lr, "max_len": C.MAX_LEN,
                          "relations": C.RELATION_NAMES}},
              C.METRICS_JSON)

    md = [render_markdown(dev_m, "CasRel 关系抽取 —— DuIE2.0 验证集评估"), "",
          "## 训练过程", "",
          "| epoch | loss | val P | val R | val F1 |",
          "| ---: | ---: | ---: | ---: | ---: |"]
    for h in history:
        md.append(f"| {h['epoch']} | {h['loss']} | {h['val_precision']} | "
                  f"{h['val_recall']} | {h['val_f1']} |")
    md += ["", render_markdown(test_m, "目标领域测试集（项目一未见人物）评估")]
    C.METRICS_MD.write_text("\n".join(md), encoding="utf-8")
    log.info("指标已写入 %s 与 %s", C.METRICS_JSON.name, C.METRICS_MD.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
