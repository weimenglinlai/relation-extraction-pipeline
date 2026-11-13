# -*- coding: utf-8 -*-
"""
图谱可视化。

* 交互式网页（PyVis）：可拖拽、可缩放，节点按实体类型分色 + 形状区分；
* 静态统计图（matplotlib）：节点类型分布、关系分布、度数 Top10。

配色统一由 palette.py 提供：同一实体类型在 Schema 图、统计图、交互网页、
人物子图里颜色一致，读者不需要图例就能对应上。
"""
import json
import os
from collections import Counter
from pathlib import Path

import networkx as nx

import config as C
from palette import TYPE_COLORS, INK, GRID, EDGE, WHITE_TEXT, type_color
from utils import get_logger, load_json

log = get_logger()

# 兼容旧名：仍按类型取色
GRAYS = [TYPE_COLORS[k] for k in C.ENTITY_TYPES]

# 不同实体类型用不同形状，避免只能靠颜色区分
SHAPES = {
    "人物": "dot", "作品": "box", "奖项": "star", "机构": "triangle",
    "地点": "diamond", "国家": "hexagon", "民族": "square", "职业": "ellipse",
}


def build_graph(triples, max_nodes=600):
    """把三元组转成 networkx 有向图；节点过多时保留度数最高的若干个。"""
    G = nx.DiGraph()
    for t in triples:
        G.add_node(t["subject"], type="人物")
        G.add_edge(t["subject"], t["object"], relation=t["predicate"],
                   confidence=t.get("confidence", 1.0))
        G.nodes[t["object"]].setdefault("type", _obj_type(t["predicate"]))
    if G.number_of_nodes() > max_nodes:
        keep = sorted(dict(G.degree()).items(), key=lambda x: -x[1])[:max_nodes]
        G = G.subgraph([n for n, _ in keep]).copy()
    return G


def _obj_type(relation):
    for r in C.RELATIONS:
        if r[0] == relation:
            return r[2]
    return "作品"


# 力导向布局的默认参数。页面右上角有调节面板，这几个值只是「打开时」的初值，
# 具体松紧由使用者拖滑块实时调，不必改代码。
# 注意 avoidOverlap 保持 0：它靠持续推开节点来防重叠，推力一直在，速度就降不下来，
# 表现为图一直抖、停不住。防重叠交给 springLength 留白。
PHYSICS = dict(
    gravitationalConstant=-7000,   # 斥力强度；负得越少，节点越不外扩
    centralGravity=0.40,           # 向心力；越大越抱团
    springLength=160,              # 边的自然长度（px）
    springConstant=0.10,           # 弹簧硬度；越大越往 springLength 收
    damping=0.50,                  # 阻尼；越大停得越快
    avoidOverlap=0.0,              # 保持 0，见上面的说明
)

# 页面内的布局调节面板：滑块实时改 barnesHut 参数，不冻结物理。
# 注入位置在 network 创建之后，和 pyvis 自己的脚本同一个作用域，可直接用 network 变量。
_PANEL_JS = r"""
(function () {
  var DEFAULTS = __DEFAULTS__;
  var cur = Object.assign({}, DEFAULTS);
  var SPECS = [
    ["springLength", "边长", 40, 400, 10],
    ["springConstant", "弹簧硬度", 0.01, 0.50, 0.01],
    ["centralGravity", "向心力", 0.05, 1.50, 0.05],
    ["gravitationalConstant", "斥力", -20000, -500, 500],
    ["damping", "阻尼", 0.10, 0.90, 0.05]
  ];
  var box = document.createElement("div");
  box.style.cssText = "position:fixed;top:12px;right:12px;width:272px;background:#fff;" +
    "border:1px solid #d8d8d8;border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.13);" +
    "font:12px/1.6 'Microsoft YaHei',Arial,sans-serif;color:#333;z-index:99999;";
  var rows = "";
  for (var i = 0; i < SPECS.length; i++) {
    var s = SPECS[i];
    rows += '<label style="display:block;margin:7px 0 0;">' + s[1] +
            ' <b id="lpv-' + s[0] + '" style="float:right;color:#1a6fc4;">' + cur[s[0]] + '</b>' +
            '<input id="lps-' + s[0] + '" type="range" min="' + s[2] + '" max="' + s[3] +
            '" step="' + s[4] + '" value="' + cur[s[0]] +
            '" style="width:100%;margin:3px 0 0;cursor:pointer;"></label>';
  }
  box.innerHTML =
    '<div id="lph" style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:700;' +
    'cursor:pointer;user-select:none;">布局调节 <span id="lpt" style="float:right;">−</span></div>' +
    '<div id="lpb" style="padding:6px 12px 10px;">' + rows +
    '<div style="display:flex;gap:6px;margin-top:10px;">' +
    '<button id="lpa" style="flex:1;padding:5px 0;cursor:pointer;">应用并冷却</button>' +
    '<button id="lpr" style="flex:1;padding:5px 0;cursor:pointer;">恢复默认</button>' +
    '</div>' +
    '<div id="lpo" style="margin-top:8px;padding:6px;background:#f6f6f6;border-radius:4px;' +
    'font:10px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-all;"></div>' +
    '</div>';
  document.body.appendChild(box);

  function refresh() {
    document.getElementById("lpo").textContent = JSON.stringify(cur);
  }
  function apply() {
    network.setOptions({
      physics: { enabled: true, barnesHut: Object.assign({}, cur) }
    });
    network.stabilize(180);
    refresh();
  }
  (function bind() {
    for (var i = 0; i < SPECS.length; i++) {
      (function (s) {
        var el = document.getElementById("lps-" + s[0]);
        el.addEventListener("input", function (e) {
          cur[s[0]] = parseFloat(e.target.value);
          document.getElementById("lpv-" + s[0]).textContent = cur[s[0]];
        });
        el.addEventListener("change", apply);
      })(SPECS[i]);
    }
    document.getElementById("lpa").addEventListener("click", apply);
    document.getElementById("lpr").addEventListener("click", function () {
      cur = Object.assign({}, DEFAULTS);
      for (var i = 0; i < SPECS.length; i++) {
        var s = SPECS[i];
        document.getElementById("lps-" + s[0]).value = cur[s[0]];
        document.getElementById("lpv-" + s[0]).textContent = cur[s[0]];
      }
      apply();
    });
    document.getElementById("lph").addEventListener("click", function () {
      var b = document.getElementById("lpb");
      var t = document.getElementById("lpt");
      if (b.style.display === "none") { b.style.display = ""; t.textContent = "−"; }
      else { b.style.display = "none"; t.textContent = "+"; }
    });
    refresh();
  })();
})();
"""


def _physics_options():
    return {
        "physics": {
            "enabled": True,
            "barnesHut": dict(PHYSICS),
            "stabilization": {"enabled": True, "iterations": 400,
                              "updateInterval": 25, "fit": True},
            "minVelocity": 0.8,
        },
        "edges": {
            # 长边用直线，弧线在长边上更显乱
            "smooth": {"enabled": True, "type": "dynamic", "roundness": 0.2},
            "width": 1.0,
            "selectionWidth": 1.6,
        },
        "interaction": {"hover": True, "tooltipDelay": 120,
                        "hideEdgesOnDrag": True},
    }


def _inject_layout_panel(path):
    """在生成的 HTML 里挂上布局调节面板。"""
    import re
    html = Path(path).read_text(encoding="utf-8")
    pat = re.compile(r"(network = new vis\.Network\(container, data, options\);)")
    if "var DEFAULTS = " in html or not pat.search(html):
        return False
    js = _PANEL_JS.replace("__DEFAULTS__", json.dumps(PHYSICS))
    html = pat.sub(lambda m: m.group(1) + js, html, count=1)
    Path(path).write_text(html, encoding="utf-8")
    return True


def save_pyvis(triples, path=None, max_nodes=420, height="760px"):
    """交互式图谱网页。"""
    from pyvis.network import Network

    path = Path(path or C.VIZ_HTML)
    G = build_graph(triples, max_nodes=max_nodes)
    net = Network(height=height, width="100%", directed=True,
                  bgcolor="#ffffff", font_color=INK, notebook=False)
    net.set_options(json.dumps(_physics_options()))

    types = sorted({d.get("type", "作品") for _, d in G.nodes(data=True)})
    shade = {t: type_color(t) for t in types}

    for n, d in G.nodes(data=True):
        etype = d.get("type", "作品")
        deg = G.degree(n)
        net.add_node(
            n, label=n, title=f"{etype}：{n}（度数 {deg}）",
            shape=SHAPES.get(etype, "dot"),
            size=10 + min(deg, 20) * 1.6,
            color={"background": shade[etype], "border": WHITE_TEXT,
                   "highlight": {"background": shade[etype], "border": INK}},
            font={"color": WHITE_TEXT, "size": 13},
        )
    for s, o, d in G.edges(data=True):
        net.add_edge(s, o, label=d.get("relation", ""),
                     title=f"{d.get('relation')}（置信度 {d.get('confidence', 0):.2f}）",
                     color=EDGE, arrows="to",
                     font={"size": 10, "color": "#6b7280"})

    # PyVis 会把离线资源 lib/ 写到「当前工作目录」，而不是 html 所在目录。
    # 不切目录的话，html 里引用的 lib/vis-9.1.2/... 会 404，打开就是空白页。
    path.parent.mkdir(parents=True, exist_ok=True)
    cwd = os.getcwd()
    try:
        os.chdir(path.parent)
        net.write_html(path.name, notebook=False, open_browser=False)
    finally:
        os.chdir(cwd)
    _inject_layout_panel(path)
    log.info("交互图谱已生成：%s（%d 节点 / %d 边）", path.name,
             G.number_of_nodes(), G.number_of_edges())
    return str(path)


# --------------------------------------------------------------------------- #
def save_stats_png(triples, path=None):
    """三张统计图：节点类型 / 关系分布 / 度数 Top10（按实体类型分色）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for name in ("Microsoft YaHei", "SimHei", "SimSun"):
        if name in {f.name for f in font_manager.fontManager.ttflist}:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False

    path = path or C.VIZ_PNG
    G = build_graph(triples, max_nodes=100000)
    node_types = Counter(d.get("type", "作品") for _, d in G.nodes(data=True))
    rels = Counter(t["predicate"] for t in triples)
    deg = Counter(dict(G.degree()))
    rel_obj = {name: obj for name, _, obj, _, _ in C.RELATIONS}
    ntype = {n: d.get("type", "作品") for n, d in G.nodes(data=True)}

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.0))

    ax = axes[0]
    items = node_types.most_common()
    ax.bar([k for k, _ in items], [v for _, v in items],
           color=[type_color(k) for k, _ in items],
           edgecolor="white", linewidth=1.0, width=0.6)
    ax.set_title("节点类型分布", fontsize=13, color=INK, pad=12)
    ax.tick_params(axis="x", rotation=30, labelsize=9, colors=INK)
    ax.grid(axis="y", color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)

    ax = axes[1]
    items = rels.most_common()
    ax.barh([k for k, _ in items][::-1], [v for _, v in items][::-1],
            color=[type_color(rel_obj.get(k, "作品")) for k, _ in items][::-1],
            edgecolor="white", linewidth=0.8, height=0.62)
    ax.set_title("关系分布", fontsize=13, color=INK, pad=12)
    ax.tick_params(labelsize=9, colors=INK)
    ax.grid(axis="x", color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)

    ax = axes[2]
    items = deg.most_common(10)[::-1]
    ax.barh([k for k, _ in items], [v for _, v in items],
            color=[type_color(ntype.get(k, "作品")) for k, _ in items],
            edgecolor="white", linewidth=0.8, height=0.62)
    ax.set_title("度数 Top 10 节点", fontsize=13, color=INK, pad=12)
    ax.tick_params(labelsize=9, colors=INK)
    ax.grid(axis="x", color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)

    for ax in axes:
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    log.info("统计图已生成：%s", path.name)
    return str(path)


def save_person_subgraph(triples, person, path=None, max_nodes=80):
    """以某个人物为中心的子图网页。"""
    sub = [t for t in triples if t["subject"] == person or t["object"] == person]
    if not sub:
        return None
    path = path or (C.OUT_DIR / f"person_{person}.html")
    return save_pyvis(sub, path=path, max_nodes=max_nodes)


def main():
    triples = load_json(C.TRIPLES_JSON, [])
    if not triples:
        print("还没有三元组，请先运行 python extract.py")
        return 1
    save_pyvis(triples)
    save_stats_png(triples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
