# -*- coding: utf-8 -*-
"""
生成属性图 schema 文档：表格（docs/属性图Schema.md）+ 示意图（docs/schema.png）。

参考文档要求：
  * 用表格列出每种关系的名称、Subject 类型、Object 类型、说明；
  * 画 Schema 图，展示节点、关系、属性的对应关系。
本脚本保证两者都由 config.py 的 RELATIONS 单点生成，改 schema 只需改配置。
"""
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch

import config as C
from palette import TYPE_COLORS, INK, EDGE, WHITE_TEXT, type_color
from utils import ensure_utf8_stdout


def _font():
    for n in ("Microsoft YaHei", "SimHei", "SimSun"):
        if n in {f.name for f in font_manager.fontManager.ttflist}:
            plt.rcParams["font.sans-serif"] = [n]
            break
    plt.rcParams["axes.unicode_minus"] = False


def draw_schema(path=None):
    _font()
    path = path or (C.DOCS_DIR / "schema.png")

    # 圆心 = 人物，其余实体类型按关系出现顺序环绕排布
    others, order = {}, []
    for name, subj, obj, desc, etype in C.RELATIONS:
        if subj == "人物" and obj != "人物" and obj not in others:
            others[obj] = len(order)
            order.append(obj)

    fig, ax = plt.subplots(figsize=(11.5, 8.6))
    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-1.25, 1.25)
    ax.axis("off")

    # 人物节点
    ax.add_patch(plt.Circle((0, 0), 0.15, facecolor=TYPE_COLORS["人物"],
                            edgecolor=WHITE_TEXT, linewidth=2.0, zorder=3))
    ax.text(0, 0, "人物\nPerson", ha="center", va="center", color=WHITE_TEXT,
            fontsize=11, fontweight="bold", zorder=4)

    # 环绕节点
    pos = {}
    n = len(order)
    for t, i in others.items():
        ang = math.pi / 2 - 2 * math.pi * i / n
        x, y = 1.15 * math.cos(ang), 0.95 * math.sin(ang)
        pos[t] = (x, y)
        ax.add_patch(plt.Circle((x, y), 0.135, facecolor=type_color(t),
                                edgecolor=WHITE_TEXT, linewidth=1.8, zorder=3))
        ax.text(x, y, f"{t}\n{C.ENTITY_TYPES.get(t,'Entity')}", ha="center",
                va="center", color=WHITE_TEXT, fontsize=9.5, zorder=4)

    # 关系边（同名多条关系合并成一条标签）
    per_target = {}
    for name, subj, obj, desc, etype in C.RELATIONS:
        if obj == "人物":
            continue
        per_target.setdefault(obj, []).append(name)

    for obj, rels in per_target.items():
        x, y = pos[obj]
        arr = FancyArrowPatch((0.15 * x / max(0.001, math.hypot(x, y)),
                               0.15 * y / max(0.001, math.hypot(x, y))),
                              (x - 0.14 * x / math.hypot(x, y),
                               y - 0.14 * y / math.hypot(x, y)),
                              arrowstyle="-|>", mutation_scale=13,
                              linewidth=1.35, color=EDGE, zorder=2,
                              shrinkA=2, shrinkB=2)
        ax.add_patch(arr)
        mx, my = x * 0.56, y * 0.56
        ax.text(mx, my, "、".join(rels), ha="center", va="center",
                fontsize=9, color=INK, zorder=5,
                bbox=dict(boxstyle="round,pad=0.22", fc="white",
                          ec=type_color(obj), linewidth=0.9))

    # 自环关系（合作）：画在正下方，避开周围节点的连线
    if any(r[2] == "人物" for r in C.RELATIONS):
        ax.add_patch(FancyArrowPatch((-0.055, -0.14), (0.055, -0.14),
                                     connectionstyle="arc3,rad=1.9",
                                     arrowstyle="-|>", mutation_scale=13,
                                     linewidth=1.35, color=EDGE, zorder=2))
        ax.text(0, -0.365, "合作", ha="center", fontsize=9, color=INK, zorder=5,
                bbox=dict(boxstyle="round,pad=0.22", fc="white",
                          ec=TYPE_COLORS["人物"], linewidth=0.9))

    ax.set_title("人物知识图谱 · 属性图 Schema", fontsize=15, color=INK,
                 fontweight="bold", pad=16)
    ax.text(0, -1.20, "节点：8 类实体　关系：14 种（节点-节点）+ 11 个节点属性",
            ha="center", fontsize=9.5, color="#6b7280")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def draw_markdown(path=None):
    path = path or (C.DOCS_DIR / "属性图Schema.md")
    L = ["# 属性图 Schema", ""]
    L.append("本图谱以「人物」为中心，共 **8 类节点**、**14 种节点-节点关系**、"
             "**11 个节点属性**（属性也计入关系种类，合计 25 种，满足参考文档「不少于 10 种」）。")
    L.append("")
    L.append("## 一、节点类型")
    L.append("")
    L.append("| 节点类型 | Neo4j 标签 | 含义 | 主要属性 |")
    L.append("| --- | --- | --- | --- |")
    for cn, en in C.ENTITY_TYPES.items():
        attrs = "、".join(a for a, _ in C.NODE_ATTRIBUTES.get(en, [])) or "name"
        L.append(f"| {cn} | `{en}` | {_desc_of(cn)} | {attrs} |")
    L.append("")
    L.append("## 二、关系（节点-节点）")
    L.append("")
    L.append("| 关系名称 | Subject 类型 | Object 类型 | Neo4j 关系类型 | 说明 |")
    L.append("| --- | --- | --- | --- | --- |")
    for name, subj, obj, desc, etype in C.RELATIONS:
        L.append(f"| {name} | {subj} | {obj} | `{etype}` | {desc} |")
    L.append("")
    L.append("## 三、节点属性")
    L.append("")
    L.append("| 所属节点 | 属性名 | 数据类型 |")
    L.append("| --- | --- | --- |")
    for label, attrs in C.NODE_ATTRIBUTES.items():
        cn = {v: k for k, v in C.ENTITY_TYPES.items()}.get(label, label)
        for a, dt in attrs:
            L.append(f"| {cn} | {a} | {dt} |")
    L.append("")
    L.append("## 四、Schema 图")
    L.append("")
    L.append("![属性图 Schema](schema.png)")
    L.append("")
    L.append("## 五、设计说明")
    L.append("")
    L.append("1. **以人物为中心**：项目一的语料是人物词条，所有关系的 Subject 都是「人物」，"
             "天然形成以人物为枢纽的星型结构，查询「某人演过什么/拿过什么奖」只需一跳。")
    L.append("2. **关系与来源对齐**：`参演 / 执导 / 编剧 / 演唱 / 主持 / 获得奖项 / 国籍 / 籍贯 / 毕业院校` "
             "这 9 种能直接映射到 DuIE2.0 的谓词，用 CasRel 训练；"
             "`出生地 / 民族 / 职业 / 经纪公司 / 合作` 不在 DuIE 中，用项目一的结构化字段做弱监督补齐。")
    L.append("3. **方向统一**：DuIE 的「主演/导演/编剧/歌手/主持人」是「作品→人物」，"
             "训练前统一翻转为「人物→作品」，保证图上所有边都从人物出发。")
    L.append("4. **合作是唯一的人物-人物关系**：由「同句共现 + 合作类触发词」抽取，"
             "用它把不同人物连成连通分量，避免图谱碎裂成一堆孤立的星。")
    L.append("")
    C.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _desc_of(cn):
    return {
        "人物": "词条主体，图谱的枢纽节点",
        "作品": "电影、电视剧、歌曲、专辑、节目等",
        "奖项": "各类奖项与其届次",
        "机构": "学校、经纪公司等组织",
        "地点": "出生地、籍贯等地理实体",
        "国家": "国籍所属国家",
        "民族": "民族",
        "职业": "演员、导演、歌手等职业标签",
    }.get(cn, "")


def main():
    ensure_utf8_stdout()
    p1 = draw_markdown()
    p2 = draw_schema()
    print("已生成：", p1)
    print("已生成：", p2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
