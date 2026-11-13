# -*- coding: utf-8 -*-
"""生成报告用的三张彩色图。

与 viz.py / make_schema_doc.py 的区别：这三张是**报告插图**，版式按报告要求定制
（宽扁横版、无内嵌标题，标题由 Word 图题给），所以单独一个脚本。
数据一律从 data/triples.json 与 data/graph_local.json 读，不写死任何数字。
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Circle, Polygon, Rectangle

import config as C
from palette import TYPE_COLORS, INK, GRID, EDGE, WHITE_TEXT, type_color

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"

REL_OBJ = {name: obj for name, _, obj, _, _ in C.RELATIONS}


def _font():
    for n in ("Microsoft YaHei", "SimHei", "SimSun"):
        if n in {f.name for f in font_manager.fontManager.ttflist}:
            plt.rcParams["font.sans-serif"] = [n]
            break
    plt.rcParams["axes.unicode_minus"] = False


def load():
    triples = json.loads((BASE / "data" / "triples.json").read_text(encoding="utf-8"))
    graph = json.loads((BASE / "data" / "graph_local.json").read_text(encoding="utf-8"))
    nodes = graph["nodes"] if isinstance(graph, dict) else graph
    return triples, nodes


# --------------------------------------------------------------------------- #
def draw_schema(path):
    """属性图 Schema：人物居中在上，七类实体平铺在下，关系名标在目标节点上方。"""
    _font()
    W, H = 14.72, 4.0
    fig = plt.figure(figsize=(W, H), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.axis("off")

    targets = ["地点", "国家", "民族", "机构", "职业", "作品", "奖项"]
    rels = defaultdict(list)
    for name, subj, obj, desc, etype in C.RELATIONS:
        if obj != "人物":
            rels[obj].append(name)

    cx, cy, cr = W / 2, 2.92, 0.44
    ax.add_patch(Circle((cx, cy), cr, facecolor=TYPE_COLORS["人物"],
                        edgecolor=WHITE_TEXT, linewidth=2.2, zorder=5))
    ax.text(cx, cy + 0.09, "人物", ha="center", va="center", color=WHITE_TEXT,
            fontsize=12.5, fontweight="bold", zorder=6)
    ax.text(cx, cy - 0.19, "Person", ha="center", va="center", color=WHITE_TEXT,
            fontsize=8.5, zorder=6)

    n = len(targets)
    left, right = 1.55, W - 1.55
    step = (right - left) / (n - 1)
    ny = 0.72
    bw, bh = 1.52, 0.66

    for i, t in enumerate(targets):
        x = left + step * i
        col = type_color(t)
        if t == "奖项":
            # 奖项用菱形，与其他实体区分
            pts = [(x, ny - 0.62), (x + 0.86, ny), (x, ny + 0.62), (x - 0.86, ny)]
            ax.add_patch(Polygon(pts, closed=True, facecolor=col,
                                 edgecolor=WHITE_TEXT, linewidth=1.8, zorder=4))
        else:
            ax.add_patch(FancyBboxPatch((x - bw / 2, ny - bh / 2), bw, bh,
                                        boxstyle="round,pad=0.04,rounding_size=0.16",
                                        facecolor=col, edgecolor=WHITE_TEXT,
                                        linewidth=1.8, zorder=4))
        ax.text(x, ny + 0.10, t, ha="center", va="center", color=WHITE_TEXT,
                fontsize=10.5, fontweight="bold", zorder=6)
        ax.text(x, ny - 0.16, C.ENTITY_TYPES.get(t, t), ha="center", va="center",
                color=WHITE_TEXT, fontsize=7.8, zorder=6)

        # 边
        rad = 0.12 if x < cx else -0.12
        ax.add_patch(FancyArrowPatch((cx, cy - cr - 0.02), (x, ny + 0.70),
                                     connectionstyle=f"arc3,rad={rad}",
                                     arrowstyle="-|>", mutation_scale=14,
                                     linewidth=1.25, color=EDGE, zorder=2,
                                     shrinkA=0, shrinkB=0))
        # 关系名（标在目标节点上方，天然平铺不重叠）
        ax.text(x, ny + 0.98, "、".join(rels[t]), ha="center", va="center",
                fontsize=8.8, color=INK, zorder=7,
                bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=col,
                          linewidth=0.9))

    # 自环：合作。放在人物右侧而不是正上方，避免贴到画布顶边被裁。
    ax.add_patch(FancyArrowPatch((cx + 0.26, cy + 0.20), (cx + 0.26, cy - 0.20),
                                 connectionstyle="arc3,rad=-1.1",
                                 arrowstyle="-|>", mutation_scale=14,
                                 linewidth=1.25, color=EDGE, zorder=2))
    ax.text(cx + 0.92, cy, "合作", ha="center", va="center", fontsize=8.8,
            color=INK, zorder=7,
            bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=EDGE,
                      linewidth=0.9))

    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
def draw_stats(triples, nodes, path):
    """三联图：节点类型 / 关系类型 / 度数 Top10。"""
    _font()
    fig, axes = plt.subplots(1, 3, figsize=(15.07, 5.0),
                             gridspec_kw={"width_ratios": [1.0, 1.28, 1.18]})

    # (a) 节点类型
    nt = Counter(n.get("type") for n in nodes)
    items = nt.most_common()
    ax = axes[0]
    xs = range(len(items))
    ax.bar(xs, [v for _, v in items],
           color=[type_color(k) for k, _ in items],
           edgecolor="white", linewidth=1.2, width=0.68, zorder=3)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([k for k, _ in items], fontsize=9, color=INK, rotation=28)
    for x, (k, v) in zip(xs, items):
        ax.text(x, v + max(nt.values()) * 0.02, f"{v:,}", ha="center",
                va="bottom", fontsize=8.6, color=INK, zorder=4)
    ax.set_title("(a) 节点类型分布", fontsize=12.5, color=INK, pad=10)
    ax.set_ylim(0, max(nt.values()) * 1.16)

    # (b) 关系类型
    rc = Counter(t["predicate"] for t in triples)
    items = rc.most_common()
    ax = axes[1]
    ys = range(len(items))
    ax.barh(list(ys), [v for _, v in items],
            color=[type_color(REL_OBJ.get(k, "作品")) for k, _ in items],
            edgecolor="white", linewidth=1.0, height=0.68, zorder=3)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([k for k, _ in items], fontsize=9, color=INK)
    ax.invert_yaxis()
    for y, (k, v) in zip(ys, items):
        ax.text(v + max(rc.values()) * 0.015, y, f"{v:,}", va="center",
                ha="left", fontsize=8.6, color=INK, zorder=4)
    ax.set_title("(b) 关系类型分布", fontsize=12.5, color=INK, pad=10)
    ax.set_xlim(0, max(rc.values()) * 1.16)

    # (c) 度数 Top10
    # 用 networkx 的图度数：重复的三元组视为同一条边（与 Neo4j 的 MERGE 语义一致），
    # 直接按三元组条数累加会把重复边重复计数，度数会偏高。
    import networkx as nx

    G = nx.DiGraph()
    ntype = {}
    for t in triples:
        G.add_edge(t["subject"], t["object"])
        ntype[t["subject"]] = "人物"
        ntype.setdefault(t["object"], REL_OBJ.get(t["predicate"], "作品"))
    for n in nodes:
        ntype.setdefault(n.get("name"), n.get("type"))
    top = sorted(G.degree(), key=lambda x: -x[1])[:10]
    ax = axes[2]
    ys = range(len(top))
    ax.barh(list(ys), [v for _, v in top],
            color=[type_color(ntype.get(k, "作品")) for k, _ in top],
            edgecolor="white", linewidth=1.0, height=0.66, zorder=3)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([k for k, _ in top], fontsize=9, color=INK)
    ax.invert_yaxis()
    for y, (k, v) in zip(ys, top):
        ax.text(v + max(d for _, d in top) * 0.015, y, f"{v:,}", va="center",
                ha="left", fontsize=8.6, color=INK, zorder=4)
    ax.set_title("(c) 度数 Top10 节点", fontsize=12.5, color=INK, pad=10)
    ax.set_xlim(0, max(d for _, d in top) * 1.16)

    for ax in axes:
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color("#cccccc")
        ax.spines["bottom"].set_color("#cccccc")
        ax.grid(axis="x" if ax is not axes[0] else "y", color=GRID,
                linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(colors=INK, length=3)

    fig.tight_layout(pad=1.1)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
PERSON = "英达"
PICK = [
    ("执导", "我爱我家", "作品"),
    ("执导", "东北一家人", "作品"),
    ("参演", "围城", "作品"),
    ("获得奖项", "政府飞天奖", "奖项"),
    ("获得奖项", "第25届大众电影百花奖\n最佳男配角", "奖项"),
    ("毕业院校", "北京大学", "机构"),
    ("出生地", "北京", "地点"),
]


def draw_person_subgraph(triples, path, person=PERSON):
    """以某个人物为中心的一跳子图，节点按真实三元组取。"""
    _font()
    have = {(t["predicate"], t["object"]) for t in triples if t["subject"] == person}
    picked = []
    for r, o, ty in PICK:
        key = (r, o.replace("\n", ""))
        if key in have:
            picked.append((r, o, ty))
        else:
            print(f"  [警告] {person} 没有 {r}→{key}，改用真实数据补齐")

    if len(picked) < len(PICK):
        used = {(r, o.replace("\n", "")) for r, o, _ in picked}
        for t in triples:
            if t["subject"] != person or t["object"] == person:
                continue
            if (t["predicate"], t["object"]) in used:
                continue
            picked.append((t["predicate"], t["object"],
                           REL_OBJ.get(t["predicate"], "作品")))
            if len(picked) >= len(PICK):
                break

    W, H = 14.98, 4.0
    fig = plt.figure(figsize=(W, H), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.axis("off")

    cx, cy = W / 2, 3.18
    ax.add_patch(FancyBboxPatch((cx - 0.62, cy - 0.34), 1.24, 0.68,
                                boxstyle="round,pad=0.05,rounding_size=0.30",
                                facecolor=TYPE_COLORS["人物"],
                                edgecolor=WHITE_TEXT, linewidth=2.0, zorder=5))
    ax.text(cx, cy, person, ha="center", va="center", color=WHITE_TEXT,
            fontsize=13, fontweight="bold", zorder=6)

    n = len(picked)
    left, right = 1.62, W - 1.62
    step = (right - left) / max(1, n - 1)
    ny = 0.86

    for i, (rel, obj, ty) in enumerate(picked):
        x = left + step * i
        col = type_color(ty)
        if ty == "奖项":
            # 奖项用六边形：比菱形能容纳更多文字，长奖项名不会溢出
            hw, hh, k = 0.90, 0.54, 0.52
            pts = [(x - hw, ny), (x - hw * k, ny + hh), (x + hw * k, ny + hh),
                   (x + hw, ny), (x + hw * k, ny - hh), (x - hw * k, ny - hh)]
            ax.add_patch(Polygon(pts, closed=True, facecolor=col,
                                 edgecolor=WHITE_TEXT, linewidth=1.8, zorder=4))
            ax.text(x, ny, obj, ha="center", va="center", color=WHITE_TEXT,
                    fontsize=7.0, fontweight="bold", linespacing=1.5, zorder=6)
        else:
            w, h = 1.62, 0.62
            style = ("round,pad=0.04,rounding_size=0.14" if ty in ("作品", "地点")
                     else "round,pad=0.02,rounding_size=0.03")
            ax.add_patch(FancyBboxPatch((x - w / 2, ny - h / 2), w, h,
                                        boxstyle=style, facecolor=col,
                                        edgecolor=WHITE_TEXT, linewidth=1.8,
                                        zorder=4))
            ax.text(x, ny, obj, ha="center", va="center", color=WHITE_TEXT,
                    fontsize=8.8, fontweight="bold", zorder=6)

        ax.add_patch(FancyArrowPatch((cx, cy - 0.40), (x, ny + 0.66),
                                     connectionstyle="arc3,rad=0.10",
                                     arrowstyle="-|>", mutation_scale=14,
                                     linewidth=1.25, color=EDGE, zorder=2))
        ax.text(x, ny + 0.92, rel, ha="center", va="center", fontsize=9,
                color=INK, zorder=7,
                bbox=dict(boxstyle="round,pad=0.24", fc="white", ec=col,
                          linewidth=0.9))

    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def main():
    triples, nodes = load()
    OUT.mkdir(parents=True, exist_ok=True)
    p1 = draw_schema(OUT / "schema_color.png")
    p2 = draw_stats(triples, nodes, OUT / "graph_overview_color.png")
    p3 = draw_person_subgraph(triples, OUT / "person_subgraph_color.png")
    for p in (p1, p2, p3):
        from PIL import Image
        im = Image.open(p)
        print(f"{p.relative_to(BASE)}  {im.size[0]}x{im.size[1]}  {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
