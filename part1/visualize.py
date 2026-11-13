# -*- coding: utf-8 -*-
"""
可视化展示。

产出 4~5 张图，覆盖参考文档建议的三类：
  1) 字段完整率条形图       （对应「字段完整率热力图/柱状图」）
  2) 人物领域分布饼图       （对应「人物领域分布饼图」）
  3) 抓取数量随时间变化折线图（对应「抓取数量随时间变化折线图」）
  4) 失败原因分类条形图
  5) 字段完整率热力图（抽样若干人 × 各字段）

配色统一走灰度（默认不使用彩色），仅用深浅灰阶区分层次，
阈值参考线用深色虚线突出。
"""
import re
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

import config as C
from quality import classify_domain, load_records
from utils import get_logger, read_jsonl

log = get_logger()

# 灰度阶（由深到浅），满足「默认无颜色」的偏好
GRAYS = ["#2b2b2b", "#4a4a4a", "#666666", "#828282", "#9e9e9e",
         "#b8b8b8", "#cccccc", "#dedede", "#ebebeb", "#f2f2f2"]
INK = "#1a1a1a"
GRID = "#d9d9d9"


def _setup_font():
    """找一个可用的中文字体，避免图上出现方块。"""
    candidates = ["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "Noto Sans CJK SC"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 130
    plt.rcParams["savefig.bbox"] = "tight"


_setup_font()


def _save(fig, name: str) -> str:
    C.CHART_DIR.mkdir(parents=True, exist_ok=True)
    path = C.CHART_DIR / name
    fig.savefig(path)
    plt.close(fig)
    log.info("图表已保存：%s", path.name)
    return str(path)


def _title(ax, title: str, sub: str = ""):
    """主标题在上、副标题紧贴其下，留足间距避免互相压字。"""
    ax.set_title(title, fontsize=14, color=INK, pad=30, fontweight="bold")
    if sub:
        ax.text(0.5, 1.02, sub, transform=ax.transAxes, ha="center",
                va="bottom", fontsize=9, color="#707070")


def _gray_luma(hexcolor: str) -> float:
    """灰度明度（0 深 ~ 1 浅），用来决定文字用深色还是浅色。"""
    h = hexcolor.lstrip("#")
    return int(h[0:2], 16) / 255


# --------------------------------------------------------------------------- #
def chart_field_completeness(persons: list) -> str:
    """字段完整率横向条形图，带 85% 达标线。"""
    fields = list(C.STANDARD_FIELDS)
    rates = []
    for f in fields:
        filled = sum(1 for p in persons
                     if str((p.get("basic_info") or {}).get(f, "")).strip())
        rates.append(filled / len(persons) if persons else 0.0)

    order = sorted(range(len(fields)), key=lambda i: rates[i])
    labels = [fields[i] for i in order]
    vals = [rates[i] * 100 for i in order]

    fig, ax = plt.subplots(figsize=(8.6, max(3.6, 0.5 * len(fields) + 1.6)))
    bars = ax.barh(labels, vals, color=GRAYS[1], edgecolor=INK, linewidth=0.6, height=0.62)
    # 未达标的高亮为浅灰以便一眼区分
    for b, v in zip(bars, vals):
        if v < 85:
            b.set_color(GRAYS[7])
            b.set_hatch("//")
    for b, v in zip(bars, vals):
        ax.text(v + 0.8, b.get_y() + b.get_height() / 2, f"{v:.1f}%",
                va="center", fontsize=9, color=INK)
    ax.axvline(85, color=INK, linestyle="--", linewidth=1.2)
    ax.text(85, len(labels) - 0.2, " 达标线 85%", color=INK, fontsize=9, va="center")
    ax.set_xlim(0, 108)
    ax.set_xlabel("字段完整率（%）", color=INK)
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    _title(ax, "各字段完整率", f"共 {len(persons)} 条记录，虚线为 85% 达标线")
    return _save(fig, "01_字段完整率.png")


def chart_domain_pie(persons: list) -> str:
    """人物领域分布饼图（灰度）。"""
    c = Counter(classify_domain(p) for p in persons)
    items = c.most_common()
    labels = [k for k, _ in items]
    sizes = [v for _, v in items]
    colors = [GRAYS[i % len(GRAYS)] for i in range(len(labels))]

    fig, ax = plt.subplots(figsize=(7.6, 6.0))
    wedges, _, autotexts = ax.pie(
        sizes, labels=None, colors=colors, autopct=lambda p: f"{p:.1f}%",
        startangle=90, counterclock=False,
        wedgeprops={"edgecolor": "#ffffff", "linewidth": 1.2},
        textprops={"fontsize": 9},
    )
    # 扇区颜色浅时用深色文字，深色时用白字，保证都能看清
    for t, c in zip(autotexts, colors):
        t.set_color("#1a1a1a" if _gray_luma(c) > 0.55 else "#ffffff")
    ax.legend(wedges, [f"{l}（{s}）" for l, s in zip(labels, sizes)],
              loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=9, frameon=False)
    ax.set_aspect("equal")
    _title(ax, "人物领域分布", f"按「职业」字段归类，共 {len(persons)} 人")
    return _save(fig, "02_领域分布.png")


def chart_crawl_timeline() -> str:
    """抓取数量随时间变化折线图，数据来自 logs/crawl_index.jsonl。"""
    rows = read_jsonl(C.CRAWL_INDEX)
    buckets = Counter()
    for r in rows:
        t = r.get("crawl_time") or r.get("time") or ""
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", t)
        if m:
            buckets[m.group(1)] += 1

    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    if buckets:
        keys = sorted(buckets)
        ys = [buckets[k] for k in keys]
        # 用类目 x 轴而不是 datetime：只有 1 个数据点时 datetime 会把
        # 坐标轴自动撑到年级别，图会变得没法看
        xs = list(range(len(keys)))
        ax.plot(xs, ys, color=GRAYS[1], linewidth=1.8, marker="o",
                markersize=3.5, markerfacecolor=INK, markeredgecolor=INK)
        if len(xs) > 1:
            ax.fill_between(xs, ys, color=GRAYS[7], alpha=0.9)
        cum = []
        s = 0
        for y in ys:
            s += y
            cum.append(s)
        ax2 = ax.twinx()
        ax2.plot(xs, cum, color=GRAYS[3], linewidth=1.4, linestyle="--")
        ax2.set_ylabel("累计抓取量", color=INK)
        ax2.tick_params(colors=INK, labelsize=9)
        ax2.spines["top"].set_visible(False)
        step = max(1, len(keys) // 12)
        ax.set_xticks(xs[::step])
        ax.set_xticklabels([keys[i] for i in xs[::step]], rotation=30, ha="right")
        # 单点时 matplotlib 会把 y 轴缩到 11.x~12.x，必须钉住从 0 起
        ax.set_ylim(0, max(ys) * 1.25 if max(ys) else 1)
        ax2.set_ylim(0, max(cum) * 1.25 if cum else 1)
        ax.margins(x=0.06)
        ax.set_ylabel("每分钟抓取量", color=INK)
        _title(ax, "抓取数量随时间变化",
               f"每分钟成功页数（实线）与累计量（虚线），合计 {len(rows)} 页")
    else:
        ax.text(0.5, 0.5, "暂无抓取日志（logs/crawl_index.jsonl 为空）",
                ha="center", va="center", color="#808080", fontsize=12)
        ax.axis("off")
        _title(ax, "抓取数量随时间变化")
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _save(fig, "03_抓取时间趋势.png")


def chart_failure() -> str:
    """失败原因分类条形图。"""
    rows = read_jsonl(C.FAILED_JSONL)
    c = Counter(r.get("status", "unknown") for r in rows)
    fig, ax = plt.subplots(figsize=(7.8, 4.0))
    if c:
        items = c.most_common()
        labels = [k for k, _ in items]
        vals = [v for _, v in items]
        bars = ax.bar(labels, vals, color=GRAYS[2], edgecolor=INK, linewidth=0.6, width=0.55)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, str(v), ha="center",
                    va="bottom", fontsize=9, color=INK)
        ax.set_ylabel("条数", color=INK)
        _title(ax, "抓取失败原因分类", f"失败合计 {len(rows)} 条")
    else:
        ax.text(0.5, 0.5, "本轮没有失败记录", ha="center", va="center",
                color="#808080", fontsize=12)
        ax.axis("off")
        _title(ax, "抓取失败原因分类")
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _save(fig, "04_失败原因.png")


def chart_completeness_heatmap(persons: list, top: int = 30) -> str:
    """字段完整率热力图：抽样若干人 × 各字段（有值=深，缺失=浅）。"""
    import numpy as np
    sample = persons[:top]
    fields = list(C.STANDARD_FIELDS)
    mat = np.zeros((len(sample), len(fields)))
    for i, p in enumerate(sample):
        basic = p.get("basic_info") or {}
        for j, f in enumerate(fields):
            mat[i, j] = 1.0 if str(basic.get(f, "")).strip() else 0.0

    fig, ax = plt.subplots(figsize=(8.4, max(4.0, 0.28 * len(sample) + 1.8)))
    ax.imshow(mat, cmap="Greys", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(fields)))
    ax.set_xticklabels(fields, rotation=40, ha="right", fontsize=9, color=INK)
    ax.set_yticks(range(len(sample)))
    ax.set_yticklabels([p.get("name", "") for p in sample], fontsize=8, color=INK)
    ax.set_xticks([x - 0.5 for x in range(1, len(fields))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(sample))], minor=True)
    ax.grid(which="minor", color="#ffffff", linewidth=0.8)
    ax.tick_params(which="minor", length=0)
    for s in ax.spines.values():
        s.set_color(GRID)
    _title(ax, "字段完整率热力图", "深色=有值，浅色=缺失（前 %d 条记录）" % len(sample))
    return _save(fig, "05_字段完整率热力图.png")


# --------------------------------------------------------------------------- #
def run_all() -> list:
    persons = load_records()
    if not persons:
        log.warning("没有数据可画图，请先抓取。")
        return []
    paths = [
        chart_field_completeness(persons),
        chart_domain_pie(persons),
        chart_crawl_timeline(),
        chart_failure(),
        chart_completeness_heatmap(persons),
    ]
    log.info("可视化完成，共 %d 张图 -> %s", len(paths), C.CHART_DIR)
    return paths


if __name__ == "__main__":
    for p in run_all():
        print(p)
