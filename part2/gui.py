# -*- coding: utf-8 -*-
"""
项目二 GUI（Tkinter）。

三项必做功能：
  1. 输入一段文本 -> 抽取三元组并展示
  2. 展示训练好的关系抽取模型的 Precision / Recall / F1
  3. 输入 Cypher 命令 -> 展示查询结果

加分项：历史记录、结果导出、图谱浏览（生成子图网页）、预设查询。

线程模型：模型加载与抽取都在后台线程，界面不假死。
"""
import csv
import json
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config as C
from utils import get_logger, load_json, now_str

log = get_logger()

BG, CARD, LINE, TEXT, SUB = "#f4f5f7", "#ffffff", "#dcdfe4", "#1f2328", "#6b7280"
ACCENT, ACCENT_HOVER, DISABLED, SEL_BG = "#24292f", "#3a4149", "#c3c8ce", "#cdd5e0"

HISTORY = C.OUT_DIR / "gui_history.json"

PRESETS = [
    ("人物 -> 作品（参演/执导/编剧/演唱/主持）", "person_works"),
    ("人物 -> 奖项", "person_awards"),
    ("度数最高的节点 Top 30", "top_nodes"),
]


def enable_dpi_awareness():
    import sys
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class App:
    def __init__(self, root: tk.Tk, extractor=None):
        self.root = root
        self.q = queue.Queue()
        self.extractor = extractor      # 由 main() 在窗口创建之前预加载
        self.store = None
        self.triples = load_json(C.TRIPLES_JSON, []) or []
        self.metrics = load_json(C.METRICS_JSON, {}) or {}
        self.history = load_json(HISTORY, []) or []
        self._texts = []

        self.root.title("人物知识图谱构建 · 关系抽取与查询")
        try:
            scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except Exception:
            scale = 1.0
        self.scale = scale
        w = min(int(1240 * scale), self.root.winfo_screenwidth() - 60)
        h = min(int(820 * scale), self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(int(1000 * scale), int(640 * scale))
        self.root.configure(bg=BG)

        self.ui_size = 11
        self.mono_size = 12
        self.ui_font = self._pick(["Microsoft YaHei UI", "Microsoft YaHei", "SimHei"], "TkDefaultFont")
        self.mono_font = self._pick(["Consolas", "Cascadia Mono", "Courier New"], "Courier New")

        self._style()
        self._build_header()
        self._build_body()
        self._build_status()
        # 模型加载（约 5~10 秒）必须在建窗口之前完成，见 main()。
        # 图存储只涉及网络 I/O，放子线程没问题，不会触发 torch 的线程问题。
        self.root.after(200, self._load_store_async)
        self.root.after(120, self._drain)

    # ------------------------------------------------------------- 外观 ----
    def _pick(self, cands, fallback):
        from tkinter import font as tkfont
        fams = set(tkfont.families())
        for c in cands:
            if c in fams:
                return c
        return fallback

    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        ui = (self.ui_font, self.ui_size)
        st.configure(".", background=BG, foreground=TEXT, font=ui)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Sub.TLabel", background=BG, foreground=SUB)
        st.configure("Title.TLabel", background=BG, foreground=TEXT,
                     font=(self.ui_font, self.ui_size + 7, "bold"))
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("CardSub.TLabel", background=CARD, foreground=SUB)
        st.configure("TButton", padding=(10, 6), background=CARD, foreground=TEXT,
                     bordercolor=LINE)
        st.map("TButton", background=[("active", "#eceef1"), ("disabled", "#f0f1f3")],
               foreground=[("disabled", DISABLED)])
        st.configure("Primary.TButton", padding=(14, 6), background=ACCENT,
                     foreground="#ffffff", bordercolor=ACCENT)
        st.map("Primary.TButton", background=[("active", ACCENT_HOVER)],
               foreground=[("disabled", "#ffffff")])
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(2, 6, 2, 0))
        st.configure("TNotebook.Tab", padding=(16, 8), background="#e7e9ec",
                     foreground=SUB, bordercolor=LINE)
        st.map("TNotebook.Tab", background=[("selected", CARD)],
               foreground=[("selected", TEXT)])
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, rowheight=int(24 * self.scale), borderwidth=0)
        st.configure("Treeview.Heading", background="#eef0f2", foreground=TEXT,
                     relief="flat", font=(self.ui_font, self.ui_size, "bold"))
        st.configure("TCombobox", fieldbackground=CARD, background=CARD,
                     bordercolor=LINE, padding=4)
        st.configure("TProgressbar", background=ACCENT, troughcolor="#e7e9ec",
                     bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT)
        self.style = st

    def _build_header(self):
        head = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        head.pack(fill="x")
        ttk.Label(head, text="人物知识图谱", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="　关系抽取 · Neo4j 图存储 · Cypher 查询",
                  style="Sub.TLabel").pack(side="left", pady=(8, 0))
        ttk.Button(head, text="打开交互图谱", command=self.open_viz).pack(side="right")
        ttk.Button(head, text="重新抽取", command=self.rebuild).pack(side="right", padx=8)

    def _build_body(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 6))
        self.nb = nb

        self.tab_extract = ttk.Frame(nb, style="Card.TFrame", padding=10)
        self.tab_metrics = ttk.Frame(nb, style="Card.TFrame", padding=10)
        self.tab_cypher = ttk.Frame(nb, style="Card.TFrame", padding=10)
        self.tab_browse = ttk.Frame(nb, style="Card.TFrame", padding=10)
        nb.add(self.tab_extract, text="文本抽取")
        nb.add(self.tab_metrics, text="模型指标")
        nb.add(self.tab_cypher, text="Cypher 查询")
        nb.add(self.tab_browse, text="图谱浏览")

        self._build_extract()
        self._build_metrics()
        self._build_cypher()
        self._build_browse()

    # --- 文本抽取 ------------------------------------------------------- #
    def _build_extract(self):
        f = self.tab_extract
        ttk.Label(f, text="输入一句话或一段文本（可点「示例」看看效果）",
                  style="CardSub.TLabel").pack(anchor="w")
        wrap = ttk.Frame(f, style="Card.TFrame")
        wrap.pack(fill="x", pady=(6, 8))
        self.txt_in = tk.Text(wrap, height=5, wrap="word",
                              font=(self.mono_font, self.mono_size),
                              bg=CARD, fg=TEXT, relief="flat", borderwidth=0,
                              highlightthickness=1, highlightbackground=LINE,
                              padx=8, pady=6)
        self.txt_in.pack(fill="x", expand=True)
        self.txt_in.insert("1.0", "1993年，英达执导中国大陆首部情景喜剧《我爱我家》，"
                                  "开创中国情景喜剧的先河。他毕业于北京大学心理学系。")
        self._texts.append(self.txt_in)

        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="抽取三元组", style="Primary.TButton",
                   command=self.on_extract).pack(side="left")
        ttk.Button(bar, text="示例 1", command=lambda: self._demo(0)).pack(side="left", padx=6)
        ttk.Button(bar, text="示例 2", command=lambda: self._demo(1)).pack(side="left")
        ttk.Button(bar, text="导出结果", command=self.export_extract).pack(side="right")
        ttk.Button(bar, text="存入图谱", command=self.save_to_graph).pack(side="right", padx=8)

        self.tv_extract = self._tree(f, ["主体", "关系", "客体", "置信度", "来源"])

    # --- 模型指标 ------------------------------------------------------- #
    def _build_metrics(self):
        f = self.tab_metrics
        top = ttk.Frame(f, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="关系抽取模型（CasRel）评估", style="Card.TLabel",
                  font=(self.ui_font, self.ui_size + 2, "bold")).pack(anchor="w")
        self.lbl_metrics = ttk.Label(top, text="正在读取指标…", style="CardSub.TLabel")
        self.lbl_metrics.pack(anchor="w", pady=(4, 8))

        self.tv_metric = self._tree(f, ["关系", "标注数", "预测数", "正确数", "P", "R", "F1"])
        self.tv_metric.configure(height=14)

    # --- Cypher --------------------------------------------------------- #
    def _build_cypher(self):
        f = self.tab_cypher
        self.lbl_backend = ttk.Label(f, text="图存储：加载中…", style="CardSub.TLabel")
        self.lbl_backend.pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(f, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="预设查询", style="CardSub.TLabel").pack(side="left")
        self.var_preset = tk.StringVar(value=PRESETS[0][0])
        cb = ttk.Combobox(row, textvariable=self.var_preset, width=38, state="readonly",
                          values=[p[0] for p in PRESETS])
        cb.pack(side="left", padx=8)
        ttk.Button(row, text="执行预设", command=self.on_preset).pack(side="left")
        ttk.Label(row, text="   参数（人名，可空）", style="CardSub.TLabel").pack(side="left")
        self.var_param = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_param, width=14).pack(side="left", padx=6)

        wrap = ttk.Frame(f, style="Card.TFrame")
        wrap.pack(fill="x", pady=(0, 6))
        self.txt_cypher = tk.Text(wrap, height=5, wrap="word",
                                  font=(self.mono_font, self.mono_size),
                                  bg=CARD, fg=TEXT, relief="flat", borderwidth=0,
                                  highlightthickness=1, highlightbackground=LINE,
                                  padx=8, pady=6)
        self.txt_cypher.pack(fill="x")
        self.txt_cypher.insert("1.0", "MATCH (p:Person)-[r]->(o) RETURN p.name, r.relation, o.name LIMIT 50")
        self._texts.append(self.txt_cypher)

        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="执行 Cypher", style="Primary.TButton",
                   command=self.on_cypher).pack(side="left")
        ttk.Button(bar, text="清空", command=lambda: self._clear_tree(self.tv_cypher)).pack(side="left", padx=8)

        self.tv_cypher = self._tree(f, ["结果"], height=12)

    # --- 图谱浏览 ------------------------------------------------------- #
    def _build_browse(self):
        f = self.tab_browse
        row = ttk.Frame(f, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="人物名", style="CardSub.TLabel").pack(side="left")
        self.var_person = tk.StringVar()
        ent = ttk.Entry(row, textvariable=self.var_person, width=22)
        ent.pack(side="left", padx=8)
        ent.bind("<Return>", lambda e: self.on_browse())
        ttk.Button(row, text="查询该人物", style="Primary.TButton",
                   command=self.on_browse).pack(side="left")
        ttk.Button(row, text="生成子图网页", command=self.open_person_graph).pack(side="left", padx=8)
        ttk.Button(row, text="打开全图", command=self.open_viz).pack(side="left")

        self.lbl_browse = ttk.Label(f, text="输入人物名后回车，可看到他的全部关系",
                                    style="CardSub.TLabel")
        self.lbl_browse.pack(anchor="w", pady=(0, 6))
        self.tv_browse = self._tree(f, ["关系", "客体", "置信度", "来源"])

    # ------------------------------------------------------------- 工具 ----
    def _tree(self, parent, cols, height=12):
        wrap = ttk.Frame(parent, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        tv = ttk.Treeview(wrap, columns=cols, show="headings", height=height)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=150, anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tv

    def _fill_tree(self, tv, rows):
        self._clear_tree(tv)
        cols = tv["columns"]
        for r in rows:
            vals = [("" if v is None else str(v)) for v in r][: len(cols)]
            vals += [""] * (len(cols) - len(vals))
            tv.insert("", "end", values=vals)

    @staticmethod
    def _clear_tree(tv):
        tv.delete(*tv.get_children())

    def _status(self, msg):
        self.status.set(msg)

    def _log(self, msg):
        self.q.put(("log", msg))

    # ------------------------------------------------------------- 异步 ----
    def _load_store_async(self):
        """
        图存储只做网络 I/O / 读 JSON，放子线程没问题。
        关键是 Neo4j 驱动要设短超时：服务没起时默认会挂 30 秒，
        期间主线程不处理消息，窗口就是「未响应」。
        """
        self._status("正在连接图存储…")

        def work():
            try:
                import store as S
                self.q.put(("store_ready", S.get_store()))
            except Exception as e:
                self.q.put(("store_failed", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "store_ready":
                    self.store = rest[0]
                    self._update_backend_label()
                    try:
                        self.pbar.stop()
                        self.pbar.configure(mode="determinate", value=100)
                    except Exception:
                        pass
                    if self.extractor is not None:
                        self._status("就绪")
                elif kind == "store_failed":
                    try:
                        self.pbar.stop()
                        self.pbar.configure(mode="determinate", value=0)
                    except Exception:
                        pass
                    self._status(f"图存储不可用（已降级）：{rest[0]}")
                elif kind == "extract":
                    self._render_extract(rest[0])
                elif kind == "cypher":
                    self._render_cypher(rest[0], rest[1])
                elif kind == "error":
                    self._status(str(rest[0]))
                    messagebox.showerror("出错", str(rest[0]))
                elif kind == "log":
                    self._status(str(rest[0]))
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._drain)

    # ------------------------------------------------------------- 动作 ----
    def _demo(self, idx):
        texts = [
            "1993年，英达执导中国大陆首部情景喜剧《我爱我家》，开创中国情景喜剧的先河。"
            "他毕业于北京大学心理学系，是中国内地男演员、导演。",
            "刘德华出生于中国香港，1981年考入无线电视艺员训练班，主演《无间道》《暗战》等电影，"
            "凭借《暗战》获得第19届香港电影金像奖最佳男主角。",
        ]
        self.txt_in.delete("1.0", "end")
        self.txt_in.insert("1.0", texts[idx % len(texts)])

    def on_extract(self):
        text = self.txt_in.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("提示", "请输入文本")
            return
        if self.extractor is None:
            messagebox.showinfo("稍等", "模型还在加载，请几秒后再试")
            return
        self._status("抽取中…")
        try:
            import rules
            from utils import split_sentences
            out = []
            for sent in (split_sentences(text) or [text]):
                out.extend(rules.triples_from_text(sent, ""))
                for t in self.extractor.by_model(sent, max_sents=1):
                    t["source"] = "model"
                    out.append(t)
            best = {}
            for t in out:
                k = (t["subject"], t["predicate"], t["object"])
                if k not in best or t.get("confidence", 0) > best[k].get("confidence", 0):
                    best[k] = t
            self._render_extract(sorted(best.values(),
                                        key=lambda x: -x.get("confidence", 0)))
        except Exception as e:
            self._status(f"抽取失败：{e}")
            messagebox.showerror("抽取失败", f"{type(e).__name__}: {e}")

    def _render_extract(self, triples):
        rows = [[t["subject"], t["predicate"], t["object"],
                 f"{t.get('confidence', 0):.3f}", t.get("source", "")] for t in triples]
        self._fill_tree(self.tv_extract, rows)
        self.last_extract = triples
        if triples:
            self._status(f"抽到 {len(triples)} 条三元组")
            self.history.insert(0, {"time": now_str(), "n": len(triples),
                                    "text": triples[0]["subject"]})
            self.history = self.history[:50]
            from utils import dump_json
            dump_json(self.history, HISTORY)
        else:
            self._status("没有抽到三元组，可以换一句表述试试")

    def export_extract(self):
        rows = [self.tv_extract.item(i)["values"] for i in self.tv_extract.get_children()]
        if not rows:
            messagebox.showinfo("提示", "还没有结果")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            initialdir=str(C.OUT_DIR),
                                            initialfile="抽取结果.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Subject", "Predicate", "Object", "Confidence", "Source"])
            w.writerows(rows)
        self._status(f"已导出 {path}")

    def save_to_graph(self):
        triples = getattr(self, "last_extract", None)
        if not triples:
            messagebox.showinfo("提示", "先抽一次再存")
            return
        if self.store is None:
            messagebox.showinfo("提示", "图存储还没准备好")
            return
        try:
            if self.store.backend == "neo4j":
                n = self.store.import_triples(triples)
            else:
                n = self.store.add_triples(triples)
                self.store.save()
            self._status(f"已写入 {n} 条关系")
        except Exception as e:
            messagebox.showerror("写入失败", str(e))

    def on_preset(self):
        if self.store is None:
            messagebox.showinfo("稍等", "图存储还在加载")
            return
        key = dict(PRESETS)[self.var_preset.get()]
        param = self.var_param.get().strip()
        headers = {
            "person_works": ["关系", "人物", "作品"],
            "person_awards": ["人物", "奖项"],
            "top_nodes": ["节点", "度数"],
        }.get(key, [])
        if self.store.backend == "neo4j":
            cyphers = {
                "person_works": (
                    "MATCH (p:Person)-[r]->(o) WHERE r.relation IN "
                    "['参演','执导','编剧','演唱','主持'] "
                    + (f"AND p.name = '{param}' " if param else "")
                    + "RETURN r.relation AS 关系, p.name AS 人物, o.name AS 作品 LIMIT 200"),
                "person_awards": (
                    "MATCH (p:Person)-[r {relation:'获得奖项'}]->(a) "
                    + (f"WHERE p.name = '{param}' " if param else "")
                    + "RETURN p.name AS 人物, a.name AS 奖项 LIMIT 200"),
                "top_nodes": ("MATCH (n) RETURN n.name AS 节点, size((n)--()) AS 度数 "
                              "ORDER BY 度数 DESC LIMIT 30"),
            }
            rows = self.store.query(cyphers[key])
            if isinstance(rows, dict):
                rows, headers = rows["rows"], (rows["keys"] or headers)
        else:
            rows = self.store.preset_query(key, param)
        self._render_cypher(key, rows, headers)

    def on_cypher(self):
        if self.store is None:
            messagebox.showinfo("稍等", "图存储还在加载")
            return
        q = self.txt_cypher.get("1.0", "end").strip()
        if not q:
            return
        if self.store.backend != "neo4j":
            self.tv_cypher.delete(*self.tv_cypher.get_children())
            self.tv_cypher["columns"] = ["提示"]
            self.tv_cypher.heading("提示", text="提示")
            self.tv_cypher.insert("", "end", values=(
                "当前是本地图存储，暂不支持任意 Cypher。请先用上方「预设查询」，"
                "或在 config.py 里配置 Neo4j 后重启。",))
            self._status("本地模式：仅支持预设查询")
            return
        try:
            res = self.store.query(q)
            if isinstance(res, dict):
                self._render_cypher(q, res["rows"], res["keys"])
            else:
                self._render_cypher(q, res)
        except Exception as e:
            messagebox.showerror("Cypher 执行失败", str(e))

    def _render_cypher(self, _q, rows, headers=None):
        self.tv_cypher.delete(*self.tv_cypher.get_children())
        ncol = max((len(r) for r in rows), default=1)
        cols = (headers or [])[:ncol]
        cols += [f"列{i+1}" for i in range(len(cols), ncol)]
        self.tv_cypher["columns"] = cols
        self.tv_cypher["show"] = "headings"
        for i, c in enumerate(cols):
            self.tv_cypher.heading(c, text=c)
            self.tv_cypher.column(c, width=160, anchor="w")
        for r in rows:
            self.tv_cypher.insert("", "end", values=[str(x) for x in r])
        self._status(f"查询返回 {len(rows)} 行")

    def reload_metrics(self):
        """把 metrics.json 里的指标灌到「模型指标」页（训练完/切回窗口时都可调用）。"""
        m = load_json(C.METRICS_JSON, {}) or {}
        self.metrics = m
        dev = m.get("dev") or {}
        if not dev:
            self.lbl_metrics.configure(text="还没有指标文件，请先运行 python train.py")
            return
        mac = dev.get("macro") or {}
        mac_txt = (f"　macro-F1 {mac['f1']*100:.2f}%（按 {mac['relations']} 种关系平均）"
                   if mac else "")
        self.lbl_metrics.configure(
            text=f"验证集：P {dev['precision']*100:.2f}%　R {dev['recall']*100:.2f}%　"
                 f"F1 {dev['f1']*100:.2f}%　（{dev['samples']} 条样本）{mac_txt}")
        rows = [[r, s["gold"], s["pred"], s["tp"],
                 f"{s['precision']*100:.1f}%", f"{s['recall']*100:.1f}%",
                 f"{s['f1']*100:.1f}%"]
                for r, s in (dev.get("per_relation") or {}).items()]
        self._fill_tree(self.tv_metric, rows)

    def _update_backend_label(self):
        if self.store is None:
            return
        st = self.store.stats()
        backend = "Neo4j" if self.store.backend == "neo4j" else "本地图存储"
        self.lbl_backend.configure(
            text=f"图存储：{backend}　节点 {st['nodes']}　关系 {st['edges']}")
        if self.store.backend != "neo4j":
            self.txt_cypher.delete("1.0", "end")
            self.txt_cypher.insert("1.0", "# 当前为本地图存储模式，请使用上方「预设查询」\n"
                                        "# 在 config.py 配置 NEO4J_URI 后可执行任意 Cypher")

    def on_browse(self):
        name = self.var_person.get().strip()
        if not name:
            return
        rows = [t for t in self.triples if t["subject"] == name]
        if not rows:
            rows = [t for t in self.triples if name in t["subject"]][:200]
        self._fill_tree(self.tv_browse, [[t["predicate"], t["object"],
                                          f"{t['confidence']:.2f}", t["source"]]
                                         for t in rows])
        self.lbl_browse.configure(text=f"{name}：{len(rows)} 条关系")

    def open_person_graph(self):
        name = self.var_person.get().strip()
        if not name:
            messagebox.showinfo("提示", "先输入人物名")
            return
        import viz
        p = viz.save_person_subgraph(self.triples, name)
        if p:
            webbrowser.open(Path(p).as_uri())
            self._status(f"已生成子图：{p}")

    def open_viz(self):
        if not C.VIZ_HTML.exists():
            import viz
            viz.save_pyvis(self.triples)
        webbrowser.open(C.VIZ_HTML.as_uri())

    def rebuild(self):
        if not messagebox.askyesno("确认", "重新抽取全部三元组？（约几分钟）"):
            return
        self._status("正在重新抽取…")

        def work():
            try:
                import extract
                ts = extract.run()
                self.triples = ts
                self.q.put(("log", f"重新抽取完成：{len(ts)} 条三元组"))
            except Exception as e:
                self.q.put(("error", f"抽取失败：{e}"))

        threading.Thread(target=work, daemon=True).start()

    def _build_status(self):
        card = ttk.Frame(self.root, style="Card.TFrame", padding=(14, 8))
        card.pack(fill="x", side="bottom")
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", side="bottom")
        self.pbar = ttk.Progressbar(card, mode="indeterminate", length=180)
        self.pbar.pack(side="left")
        self.pbar.start(12)
        self.status = tk.StringVar(value="正在初始化…")
        ttk.Label(card, textvariable=self.status, style="Card.TLabel").pack(
            side="left", padx=12)
        ttk.Label(card, text=f"三元组 {len(self.triples)} 条",
                  style="CardSub.TLabel").pack(side="right")


def main():
    """
    启动顺序很重要：

    1. 先把模型加载完（import torch/transformers + 读 400MB BERT 权重，约 5~10 秒），
    2. 再创建主窗口。

    反过来（先建窗口、在主线程里加载）会让 Tk 的消息循环停 5 秒以上，
    Windows 会把标题栏改成「未响应」，用户以为卡死了。
    放子线程加载又会和 Tk 主循环并发触发段错误（exit 139），所以只能前置。
    """
    enable_dpi_awareness()
    # 关掉 transformers 加载时的 LOAD REPORT 和进度条，控制台只留自己的提示
    try:
        from transformers.utils import logging as _tfl
        _tfl.set_verbosity_error()
        _tfl.disable_progress_bar()
    except Exception:
        pass

    print("[1/2] 正在加载关系抽取模型（约 5~10 秒，请稍候）…", flush=True)
    extractor = None
    try:
        import extract as E
        extractor = E.Extractor(use_model=True)
        print("[1/2] 模型就绪", flush=True)
    except Exception as e:
        print(f"[1/2] 模型加载失败，将以「仅规则」模式运行：{e}", flush=True)

    print("[2/2] 正在打开界面…", flush=True)
    root = tk.Tk()
    app = App(root, extractor=extractor)
    root.after(600, app.reload_metrics)
    root.mainloop()


if __name__ == "__main__":
    main()
