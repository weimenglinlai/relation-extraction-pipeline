# -*- coding: utf-8 -*-
"""
GUI 界面（Tkinter，标准库自带，无需额外安装）。

必做功能：
  * 输入一个百度百科 URL（自动纠错、也可只写人名、域名非法会提示）
  * 展示该 URL 对应的网页 HTML
  * 展示从网页中提取到的文本
加分功能：
  * 导出结果（JSON / TXT）
  * 批量输入 + 进度条 + 多线程
  * 历史记录（持久化到 output/gui_history.json）
辅助设置：
  * 界面字号 / 正文字号实时可调（设置面板与工具栏 +/- 都能改）
  * 抓取引擎、线程数、请求间隔、重试次数、超时、是否归档 HTML、是否忽略系统代理
  * 自动换行、源码展示上限、抓取后自动导出、历史记录条数
  * 快捷键：Ctrl+Enter 抓取、Ctrl+S 导出、F5 出报告、Ctrl+滚轮 缩放字号

线程模型：抓取与解析全部放在后台线程，通过 queue 回传主线程刷新界面，
保证窗口在抓取期间不假死（解析 4MB 页面约 1s，放在主线程会明显卡顿）。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import config as C
from crawler import fetch_one, _name_from_url
from parser import parse_page
from utils import dump_json, get_logger, load_json, now_str

log = get_logger()

HISTORY_FILE = C.OUT_DIR / "gui_history.json"
CONFIG_FILE = C.OUT_DIR / "gui_config.json"

BAIKE_HOSTS = ("baike.baidu.com", "wapbaike.baidu.com", "m.baike.baidu.com")

# 浅色中性配色（不引入色相，与工程内图表保持一致的灰度审美）
BG = "#f4f5f7"
CARD = "#ffffff"
LINE = "#dcdfe4"
TEXT = "#1f2328"
SUB = "#6b7280"
ACCENT = "#24292f"
ACCENT_HOVER = "#3a4149"
DISABLED = "#c3c8ce"
SEL_BG = "#cdd5e0"

DEFAULT_CFG = {
    "ui_font": "",              # 留空则在启动时自动挑选一个可用中文字体
    "ui_size": 11,
    "mono_font": "",            # 留空则自动挑选等宽字体
    "mono_size": 12,
    "engine": C.DEFAULT_ENGINE,
    "workers": C.DEFAULT_WORKERS,
    "sleep_min": C.SLEEP_MIN,
    "sleep_max": C.SLEEP_MAX,
    "max_retries": C.MAX_RETRIES,
    "page_timeout": C.PAGE_TIMEOUT,
    "save_html": C.SAVE_HTML,
    "ignore_proxy": C.IGNORE_PROXY,
    "wrap": False,
    "max_show": 400_000,
    "auto_export": False,
    "history_limit": 100,
}

_UI_FONT_CANDIDATES = ["Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                       "SimHei", "SimSun", "Courier New"]
_MONO_FONT_CANDIDATES = ["Consolas", "Cascadia Mono", "Cascadia Code",
                         "DejaVu Sans Mono", "Courier New"]

TAB_HTML, TAB_TEXT, TAB_SECTIONS, TAB_JSON, TAB_WORKS, TAB_LOG = range(6)

PLACEHOLDER = "输入人名（如：刘德华），或粘贴百度百科链接"


def _pick_font(candidates, fallback="TkDefaultFont"):
    try:
        fams = set(tkfont.families())
    except Exception:                                  # pragma: no cover
        return fallback
    for c in candidates:
        if c in fams:
            return c
    return fallback


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    saved = load_json(CONFIG_FILE, {}) or {}
    cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CFG})
    return cfg


def apply_cfg_to_config(cfg: dict) -> None:
    """把界面里的设置写回 config 模块，抓取时立即生效（当前会话）。"""
    C.DEFAULT_ENGINE = cfg["engine"]
    C.DEFAULT_WORKERS = int(cfg["workers"])
    C.SLEEP_MIN = float(cfg["sleep_min"])
    C.SLEEP_MAX = float(cfg["sleep_max"])
    C.MAX_RETRIES = int(cfg["max_retries"])
    C.PAGE_TIMEOUT = int(cfg["page_timeout"])
    C.SAVE_HTML = bool(cfg["save_html"])
    C.IGNORE_PROXY = bool(cfg["ignore_proxy"])


def normalize_url(text: str) -> str:
    """把用户输入统一成百度百科 URL：支持整条 URL，也支持只写人名。"""
    from urllib.parse import quote, urlparse

    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("http"):
        host = urlparse(text).netloc
        if not any(host.endswith(h) for h in BAIKE_HOSTS):
            raise ValueError(f"仅支持百度百科的 URL，当前域名：{host or '未知'}")
        return text
    return C.URL_DESKTOP + quote(text)


def enable_dpi_awareness() -> None:
    """
    让进程感知显示缩放（必须在创建 Tk 之前调用）。

    不设的话，在 125%/150% 缩放的屏幕上系统会把窗口位图放大，界面发虚；
    设了之后字号按点排版、窗口按物理像素给尺寸，文字是清晰的原生渲染。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # SYSTEM_DPI_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:                                        # pragma: no cover
        pass


def open_path(path) -> None:
    """用系统默认程序打开文件/目录（Windows）。"""
    p = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)                            # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:
        messagebox.showwarning("打开失败", f"{p}\n{e}")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_cfg()
        self.cfg["ui_font"] = self.cfg["ui_font"] or _pick_font(_UI_FONT_CANDIDATES, "TkDefaultFont")
        self.cfg["mono_font"] = self.cfg["mono_font"] or _pick_font(_MONO_FONT_CANDIDATES, "Courier New")
        apply_cfg_to_config(self.cfg)

        self.q = queue.Queue()
        self.current = None            # 最近一次 FetchResult
        self.record = None             # 最近一次解析结果
        self.busy = False
        self.history = load_json(HISTORY_FILE, []) or []
        self._texts = []
        self._lists = []

        self.root.title("人物信息抓取与提取 · 百度百科")
        # 按显示缩放给窗口尺寸，保证在高分屏上比例正常（字号本身按点排版，
        # Tk 会按 DPI 自动换算，所以不需要手动放大字号）
        try:
            self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except Exception:                                   # pragma: no cover
            self.scale = 1.0
        w = min(int(1280 * self.scale), self.root.winfo_screenwidth() - 60)
        h = min(int(820 * self.scale), self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(int(1040 * self.scale), int(660 * self.scale))
        self.root.configure(bg=BG)

        self._build_style()
        self._build_menu()
        self._build_header()
        self._build_body()
        self._build_status()
        self._bind_keys()
        self._refresh_history()
        self._log("就绪。输入人名或百度百科 URL 后按 Ctrl+Enter 抓取。")
        self._log(f"界面字体：{self.cfg['ui_font']} {self.cfg['ui_size']}pt；"
                  f"正文字体：{self.cfg['mono_font']} {self.cfg['mono_size']}pt")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._drain)
        # 左栏固定宽度，右栏吃掉剩余空间
        self.root.after(220, lambda: self.body.sashpos(0, int(336 * self.scale)))

    # ---- 外观 ----
    def _build_style(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self._apply_style()

    def _apply_style(self):
        """按当前字号重设全部 ttk 样式（改字号后重新调用即可全局生效）。"""
        ui = (self.cfg["ui_font"], self.cfg["ui_size"])
        st = self.style
        st.configure(".", background=BG, foreground=TEXT, font=ui)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Sub.TLabel", background=BG, foreground=SUB)
        st.configure("Title.TLabel", background=BG, foreground=TEXT,
                     font=(self.cfg["ui_font"], self.cfg["ui_size"] + 6, "bold"))
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("CardSub.TLabel", background=CARD, foreground=SUB)
        st.configure("CardTitle.TLabel", background=CARD, foreground=TEXT,
                     font=(self.cfg["ui_font"], self.cfg["ui_size"] + 2, "bold"))

        st.configure("TButton", padding=(10, 6), background=CARD,
                     foreground=TEXT, bordercolor=LINE, focuscolor=BG)
        st.map("TButton",
               background=[("active", "#eceef1"), ("disabled", "#f0f1f3")],
               foreground=[("disabled", DISABLED)])
        st.configure("Primary.TButton", padding=(14, 6), background=ACCENT,
                     foreground="#ffffff", bordercolor=ACCENT)
        st.map("Primary.TButton",
               background=[("active", ACCENT_HOVER), ("disabled", DISABLED)],
               foreground=[("disabled", "#ffffff")])
        st.configure("Mini.TButton", padding=(6, 3))

        st.configure("TEntry", fieldbackground=CARD, bordercolor=LINE,
                     foreground=TEXT, insertcolor=TEXT, padding=6)
        st.configure("Placeholder.TEntry", fieldbackground=CARD, bordercolor=LINE,
                     foreground=SUB, insertcolor=TEXT, padding=6)
        st.configure("TCombobox", fieldbackground=CARD, background=CARD,
                     bordercolor=LINE, arrowcolor=TEXT, padding=4)
        st.configure("TSpinbox", fieldbackground=CARD, bordercolor=LINE, padding=4)

        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(2, 6, 2, 0))
        st.configure("TNotebook.Tab", padding=(16, 8), background="#e7e9ec",
                     foreground=SUB, bordercolor=LINE)
        st.map("TNotebook.Tab",
               background=[("selected", CARD), ("active", "#eef0f2")],
               foreground=[("selected", TEXT)])

        st.configure("TLabelframe", background=BG, bordercolor=LINE, relief="solid",
                     borderwidth=1)
        st.configure("TLabelframe.Label", background=BG, foreground=SUB)

        st.configure("TProgressbar", background=ACCENT, troughcolor="#e7e9ec",
                     bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT)
        st.configure("TPanedwindow", background=BG)
        st.configure("TSeparator", background=LINE)

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)

        f = tk.Menu(m, tearoff=0)
        f.add_command(label="抓取并提取\tCtrl+Enter", command=self.on_fetch)
        f.add_command(label="批量抓取…", command=self.on_batch)
        f.add_separator()
        f.add_command(label="导出结果…\tCtrl+S", command=self.on_export)
        f.add_command(label="复制提取文本", command=lambda: self._copy_tab(TAB_TEXT))
        f.add_separator()
        f.add_command(label="退出", command=self._on_close)
        m.add_cascade(label="文件", menu=f)

        s = tk.Menu(m, tearoff=0)
        s.add_command(label="参数设置…\tCtrl+,", command=self.on_settings)
        s.add_separator()
        s.add_command(label="字号增大\tCtrl+=", command=lambda: self._bump_font(1))
        s.add_command(label="字号减小\tCtrl+-", command=lambda: self._bump_font(-1))
        s.add_command(label="恢复默认设置", command=self.on_reset_settings)
        s.add_separator()
        self.var_wrap = tk.BooleanVar(value=bool(self.cfg["wrap"]))
        s.add_checkbutton(label="自动换行", variable=self.var_wrap,
                          command=self._toggle_wrap)
        m.add_cascade(label="设置", menu=s)

        t = tk.Menu(m, tearoff=0)
        t.add_command(label="质量评估 + 出图\tF5", command=self.on_report)
        t.add_separator()
        t.add_command(label="打开输出目录", command=lambda: open_path(C.OUT_DIR))
        t.add_command(label="打开图表目录", command=lambda: open_path(C.CHART_DIR))
        t.add_command(label="打开日志目录", command=lambda: open_path(C.LOG_DIR))
        t.add_command(label="打开数据目录", command=lambda: open_path(C.DATA_DIR))
        t.add_separator()
        t.add_command(label="重新载入人物列表", command=self._reload_names)
        m.add_cascade(label="工具", menu=t)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="使用说明（README）", command=self.on_help)
        h.add_command(label="关于", command=self.on_about)
        m.add_cascade(label="帮助", menu=h)

        self.root.config(menu=m)

    def _build_header(self):
        head = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        head.pack(fill="x")

        ttk.Label(head, text="人物信息抓取与提取", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="　百度百科 · 基本信息 / 演艺经历 / 主要作品 / 获奖记录",
                  style="Sub.TLabel").pack(side="left", pady=(8, 0))

        bar = ttk.Frame(self.root, padding=(14, 0, 14, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text="URL / 人名").pack(side="left", padx=(0, 8))
        self.url_var = tk.StringVar()
        self.entry = ttk.Entry(bar, textvariable=self.url_var, font=(self.cfg["mono_font"],
                                                                    self.cfg["ui_size"] + 1))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.on_fetch())
        self._init_placeholder()

        self.fetch_btn = ttk.Button(bar, text="抓取并提取", style="Primary.TButton",
                                    command=self.on_fetch)
        self.fetch_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="批量抓取", command=self.on_batch).pack(side="left", padx=4)
        ttk.Button(bar, text="导出", command=self.on_export).pack(side="left", padx=4)
        ttk.Button(bar, text="打开网页", command=self.on_open).pack(side="left", padx=4)
        ttk.Button(bar, text="出报告/图表", command=self.on_report).pack(side="left", padx=4)
        ttk.Button(bar, text="设置", command=self.on_settings).pack(side="left", padx=(4, 0))

    def _build_body(self):
        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14)
        self.body = body

        # 左栏
        left = ttk.Frame(body)
        body.add(left, weight=0)

        lf1 = ttk.Labelframe(left, text=" 历史记录（双击载入，Delete 删除） ",
                             padding=(8, 6))
        lf1.pack(fill="both", expand=True)
        self.hist_list = self._make_list(lf1, height=9)
        self.hist_list.bind("<Double-Button-1>", self._on_hist_pick)
        self.hist_list.bind("<Return>", self._on_hist_pick)
        self.hist_list.bind("<Delete>", lambda e: self._del_history())

        lf2 = ttk.Labelframe(left, text=" 章节大纲 ", padding=(8, 6))
        lf2.pack(fill="both", expand=True, pady=(8, 0))
        self.outline = self._make_list(lf2, height=9)
        self.outline.bind("<Double-Button-1>", self._on_outline_pick)

        row = ttk.Frame(left)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="清空历史", command=self._clear_history).pack(
            side="left", fill="x", expand=True)

        # 右栏
        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        self.txt_html = self._make_tab("网页 HTML 源码")
        self.txt_text = self._make_tab("提取文本")
        self.txt_sections = self._make_tab("章节正文")
        self.txt_json = self._make_tab("结构化结果 JSON")
        self.txt_works = self._make_tab("主要作品 / 获奖记录")
        self.txt_log = self._make_tab("运行日志")

    def _build_status(self):
        card = ttk.Frame(self.root, style="Card.TFrame", padding=(14, 8))
        card.pack(fill="x", side="bottom")
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", side="bottom")

        self.pbar = ttk.Progressbar(card, mode="determinate", length=240, maximum=100)
        self.pbar.pack(side="left")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(card, textvariable=self.status, style="Card.TLabel",
                  anchor="w").pack(side="left", padx=12, fill="x", expand=True)
        self.meta = tk.StringVar(value="记录 0")
        ttk.Label(card, textvariable=self.meta, style="CardSub.TLabel").pack(side="right")

    def _make_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.nb, style="Card.TFrame", padding=1)
        self.nb.add(frame, text=title)
        t = tk.Text(frame, wrap="none", font=(self.cfg["mono_font"], self.cfg["mono_size"]),
                    bg=CARD, fg=TEXT, insertbackground=TEXT, selectbackground=SEL_BG,
                    relief="flat", borderwidth=0, highlightthickness=0,
                    padx=8, pady=6)
        y = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        t.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._attach_menu(t)
        self._texts.append(t)
        return t

    def _make_list(self, parent, height: int) -> tk.Listbox:
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        lb = tk.Listbox(wrap, height=height, activestyle="none",
                        font=(self.cfg["ui_font"], self.cfg["ui_size"]),
                        bg=CARD, fg=TEXT, selectbackground=SEL_BG, selectforeground=TEXT,
                        relief="flat", borderwidth=0, highlightthickness=0,
                        selectmode="browse")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._lists.append(lb)
        return lb

    def _attach_menu(self, widget: tk.Text):
        m = tk.Menu(widget, tearoff=0)
        m.add_command(label="复制", command=lambda: widget.event_generate("<<Copy>>"))
        m.add_command(label="全选", command=lambda: widget.tag_add("sel", "1.0", "end"))
        m.add_separator()
        m.add_command(label="清空", command=lambda: widget.delete("1.0", "end"))
        widget.bind("<Button-3>", lambda e: m.tk_popup(e.x_root, e.y_root))

    def _bind_keys(self):
        self.root.bind("<Control-Return>", lambda e: self.on_fetch())
        self.root.bind("<Control-s>", lambda e: self.on_export())
        self.root.bind("<Control-comma>", lambda e: self.on_settings())
        self.root.bind("<Control-equal>", lambda e: self._bump_font(1))
        self.root.bind("<Control-minus>", lambda e: self._bump_font(-1))
        self.root.bind("<F5>", lambda e: self.on_report())
        self.root.bind("<Control-MouseWheel>", self._on_ctrl_wheel)

    # ------------------------------------------------------- 字体 / 设置 ---
    def _on_ctrl_wheel(self, event):
        self._bump_font(1 if event.delta > 0 else -1)

    def _bump_font(self, delta: int):
        size = max(7, min(28, int(self.cfg["ui_size"]) + delta))
        self.cfg["ui_size"] = size
        self.cfg["mono_size"] = max(7, min(28, int(self.cfg["mono_size"]) + delta))
        self._apply_fonts()

    def _apply_fonts(self):
        """字号变化后立刻全量生效，并落盘。"""
        self._apply_style()
        mono = (self.cfg["mono_font"], self.cfg["mono_size"])
        ui = (self.cfg["ui_font"], self.cfg["ui_size"])
        for t in self._texts:
            t.configure(font=mono)
        for lb in self._lists:
            lb.configure(font=ui)
        self.entry.configure(font=(self.cfg["mono_font"], self.cfg["ui_size"] + 1))
        dump_json(self.cfg, CONFIG_FILE)
        self._set_status(f"字号已调整为 {self.cfg['ui_size']}pt（正文 {self.cfg['mono_size']}pt）")

    def _toggle_wrap(self):
        self.cfg["wrap"] = bool(self.var_wrap.get())
        for t in self._texts:
            t.configure(wrap="word" if self.cfg["wrap"] else "none")
        dump_json(self.cfg, CONFIG_FILE)

    def on_settings(self):
        SettingsDialog(self.root, self)

    def on_reset_settings(self):
        if not messagebox.askyesno("确认", "恢复默认设置？（字号、引擎、并发等全部还原）"):
            return
        keep = {"ui_font": self.cfg["ui_font"], "mono_font": self.cfg["mono_font"]}
        self.cfg = dict(DEFAULT_CFG)
        self.cfg.update(keep)
        apply_cfg_to_config(self.cfg)
        dump_json(self.cfg, CONFIG_FILE)
        self._apply_fonts()
        self.var_wrap.set(bool(self.cfg["wrap"]))
        self._toggle_wrap()

    def _init_placeholder(self):
        """输入框灰字提示：获得焦点即清空，失焦且为空时恢复。"""
        self._ph_on = False

        def show(_evt=None):
            if not self.url_var.get().strip():
                self._ph_on = True
                self.entry.configure(style="Placeholder.TEntry")
                self.url_var.set(PLACEHOLDER)

        def hide(_evt=None):
            if self._ph_on:
                self._ph_on = False
                self.url_var.set("")
                self.entry.configure(style="TEntry")

        self.entry.bind("<FocusIn>", hide)
        self.entry.bind("<FocusOut>", show)
        self.entry.bind("<Button-1>", hide, add="+")
        show()
        self._ph_show, self._ph_hide = show, hide

    def _entry_text(self) -> str:
        """取输入框里的真实文本（把占位提示当作空）。"""
        t = self.url_var.get().strip()
        return "" if (self._ph_on or t == PLACEHOLDER) else t

    # ------------------------------------------------------------- 工具 ----
    def _set_text(self, widget: tk.Text, text: str):
        widget.delete("1.0", "end")
        if text is None:
            return
        limit = int(self.cfg["max_show"])
        if len(text) > limit:
            widget.insert("end", text[:limit]
                          + f"\n\n…（已截断：全文 {len(text)} 字符，仅显示前 {limit} 字符；"
                            f"可在「设置 - 参数设置」里调大展示上限）")
        else:
            widget.insert("end", text)

    def _copy_tab(self, idx: int):
        t = self._texts[idx]
        self.root.clipboard_clear()
        self.root.clipboard_append(t.get("1.0", "end").rstrip())
        self._set_status("已复制到剪贴板")

    def _log(self, msg: str):
        self.txt_log.insert("end", f"[{now_str()}] {msg}\n")
        self.txt_log.see("end")

    def _set_status(self, msg: str):
        self.status.set(msg)

    def _set_meta(self):
        eng = self.cfg["engine"]
        n = len(self.history)
        self.meta.set(f"引擎 {eng} ｜ 历史 {n} 条")

    def _set_busy(self, busy: bool, determinate: bool = False):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.fetch_btn.configure(state=state)
        if busy and not determinate:
            self.pbar.configure(mode="indeterminate")
            self.pbar.start(12)
        else:
            self.pbar.stop()
            self.pbar.configure(mode="determinate")
            if not busy:
                self.pbar["value"] = 0

    # -------------------------------------------------------------- 抓取 ---
    def on_fetch(self):
        if self.busy:
            self._set_status("正在抓取中，请稍候…")
            return
        try:
            url = normalize_url(self._entry_text())
        except ValueError as e:
            messagebox.showwarning("URL 不合法", str(e))
            return
        if not url:
            messagebox.showinfo("提示", "请输入百度百科 URL 或人名")
            return

        self.record = None
        self._set_busy(True)
        self._set_status(f"抓取中：{url}")
        self._log(f"开始抓取 {url}（引擎 {self.cfg['engine']}）")
        threading.Thread(target=self._worker_fetch, args=(url,), daemon=True).start()

    def _worker_fetch(self, url: str):
        """后台线程：抓取 + 解析（解析也放这里，避免主线程卡住）。"""
        try:
            r = fetch_one(url, engine=self.cfg["engine"])
            if not r.ok:
                self.q.put(("fetch_failed", url, r))
                return
            rec = parse_page(r.html, r.name, r.final_url or r.url,
                             engine=r.engine, crawl_time=r.crawl_time)
            self.q.put(("fetch", url, r, rec))
        except Exception as e:                          # pragma: no cover
            self.q.put(("error", url, f"{type(e).__name__}: {e}"))

    def _render(self, url: str, r, rec):
        self.current = r
        self.record = rec
        self._set_text(self.txt_html, r.html)
        self._set_text(self.txt_text, self._render_text(rec))
        self._set_text(self.txt_sections, self._render_sections(rec))
        self._set_text(self.txt_json, json.dumps(rec, ensure_ascii=False, indent=2))
        self._set_text(self.txt_works, self._render_works(rec))

        self.outline.delete(0, "end")
        for s in rec["sections"]:
            self.outline.insert("end", s["name"])
            for sub in s["subs"]:
                self.outline.insert("end", "    └ " + sub)

        filled = sum(1 for v in rec["basic_info"].values() if v)
        self._set_status(
            f"完成：{rec['lemma_name']}　字段 {filled}/{len(rec['basic_info'])}　"
            f"章节 {rec['stats']['section_count']}　作品类 {rec['stats']['work_categories']}　"
            f"获奖组 {rec['stats']['award_groups']}　耗时 {r.elapsed:.1f}s")
        self._log(f"解析完成 {rec['lemma_name']}：HTML {len(r.html)} 字符，"
                  f"{filled}/{len(rec['basic_info'])} 个字段，{r.elapsed:.1f}s")
        self._add_history(url, rec.get("lemma_name") or r.name)
        self.nb.select(TAB_TEXT)
        if self.cfg.get("auto_export"):
            self._auto_export(rec)

    def _auto_export(self, rec: dict):
        name = rec.get("lemma_name") or rec.get("name") or "result"
        p = C.OUT_DIR / "single" / f"{name}.json"
        dump_json(rec, p)
        self._log(f"已自动导出：{p}")

    @staticmethod
    def _render_text(rec: dict) -> str:
        L = [f"词条名：{rec['lemma_name']}", f"URL：{rec['url']}",
             f"抓取时间：{rec['crawl_time']}　引擎：{rec['engine']}", ""]
        L.append("【基本信息】")
        for k, v in rec["basic_info"].items():
            if v:
                L.append(f"  {k}：{v}")
        L += ["", "【摘要】", "  " + (rec["summary"] or "（无）")]
        L += ["", "【演艺经历】", "  " + (rec["career"] or "（无）")]
        L += ["", "【主要作品】"]
        if rec["works"]:
            for w in rec["works"]:
                L.append(f"  · {w['category']}（{w['count']} 项）")
        else:
            L.append("  （无）")
        L += ["", "【获奖记录】"]
        if rec["awards"]:
            for a in rec["awards"]:
                L.append(f"  · {a.get('group') or '未分组'}（{len(a.get('items', []))} 项）")
        else:
            L.append("  （无）")
        return "\n".join(L)

    @staticmethod
    def _render_sections(rec: dict) -> str:
        """把每个章节的正文完整铺开，供逐节阅读与大纲跳转。"""
        L = [f"词条名：{rec['lemma_name']}　共 {len(rec['sections'])} 个章节", ""]
        if not rec["sections"]:
            L.append("（未解析出章节）")
        for i, s in enumerate(rec["sections"], 1):
            L.append(f"{i}. {s['name']}")
            if s.get("subs"):
                L.append("   子章节：" + "、".join(s["subs"]))
            L.append("   " + (s.get("text") or "（空）"))
            L.append("")
        return "\n".join(L)

    @staticmethod
    def _render_works(rec: dict) -> str:
        L = ["【主要作品】", ""]
        if not rec["works"]:
            L.append("（无）")
        for w in rec["works"]:
            L.append(f"{w['category']}（{w['count']} 项）")
            if w["header"]:
                L.append("   " + " ｜ ".join(w["header"]))
            for row in w["items"][:40]:
                L.append("   " + " ｜ ".join(row))
            for c in w["cards"][:40]:
                L.append("   " + " ｜ ".join(f"{k}：{v}" for k, v in c.items()))
            L.append("")
        L += ["", "【获奖记录】", ""]
        if not rec["awards"]:
            L.append("（无）")
        for a in rec["awards"]:
            L.append(f"{a.get('group') or '未分组'}（{len(a.get('items', []))} 项）")
            if a.get("header"):
                L.append("   " + " ｜ ".join(a["header"]))
            for row in a.get("items", [])[:60]:
                L.append("   " + " ｜ ".join(row))
            if a.get("text"):
                L.append("   " + a["text"])
            L.append("")
        return "\n".join(L)

    # --------------------------------------------------------- 历史记录 ---
    def _add_history(self, url: str, name: str):
        self.history = [h for h in self.history if h.get("url") != url]
        self.history.insert(0, {"url": url, "name": name, "time": now_str()})
        self.history = self.history[: int(self.cfg["history_limit"])]
        dump_json(self.history, HISTORY_FILE)
        self._refresh_history()

    def _refresh_history(self):
        from urllib.parse import unquote
        self.hist_list.delete(0, "end")
        for h in self.history:
            url = unquote(h.get("url", ""))
            self.hist_list.insert("end", f"{h.get('name', '')}   {url}")
        self.hist_list.configure(height=max(6, min(14, len(self.history) or 6)))
        self._set_meta()

    def _on_hist_pick(self, _evt=None):
        sel = self.hist_list.curselection()
        if not sel:
            return
        h = self.history[sel[0]]
        self._ph_on = False
        self.entry.configure(style="TEntry")
        self.url_var.set(h.get("url", ""))
        self.on_fetch()

    def _del_history(self):
        sel = self.hist_list.curselection()
        if not sel:
            return
        self.history.pop(sel[0])
        dump_json(self.history, HISTORY_FILE)
        self._refresh_history()

    def _clear_history(self):
        if messagebox.askyesno("确认", "确定清空历史记录？"):
            self.history = []
            dump_json(self.history, HISTORY_FILE)
            self._refresh_history()

    def _on_outline_pick(self, _evt=None):
        """双击章节名 → 在「章节正文」里定位到该章节。"""
        import re as _re
        sel = self.outline.curselection()
        if not sel or self.record is None:
            return
        name = self.outline.get(sel[0]).strip().lstrip("└").strip()
        t = self.txt_sections
        self.nb.select(TAB_SECTIONS)
        pos = t.search(r"^\s*(\d+\.\s*)?" + _re.escape(name) + r"\s*$",
                       "1.0", "end", regexp=True)
        if not pos:
            pos = t.search(_re.escape(name), "1.0", "end", regexp=True)
        if pos:
            t.see(pos)
            t.tag_remove("hl", "1.0", "end")
            t.tag_add("hl", pos, f"{pos} lineend")
            t.tag_configure("hl", background="#fff3b0")
            self._set_status(f"已定位到章节「{name}」")
        else:
            self._set_status(f"「{name}」在章节正文里没有找到")

    def _reload_names(self):
        from utils import load_names
        n = load_names()
        self._set_status(f"人物列表已重新载入：{len(n)} 人")
        self._log(f"人物列表共 {len(n)} 人")

    # ------------------------------------------------------------- 其它 ----
    def on_open(self):
        try:
            url = normalize_url(self._entry_text())
        except ValueError as e:
            messagebox.showwarning("URL 不合法", str(e))
            return
        if url:
            webbrowser.open(url)

    def on_export(self):
        if not self.record:
            messagebox.showinfo("提示", "还没有可导出的结果，请先抓取。")
            return
        rec = self.record
        name = rec.get("lemma_name") or rec.get("name") or "result"
        path = filedialog.asksaveasfilename(
            title="导出结果", initialdir=str(C.OUT_DIR), initialfile=f"{name}.json",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() == ".txt":
            p.write_text(self._render_text(rec) + "\n\n" + self._render_works(rec),
                         encoding="utf-8")
        else:
            dump_json(rec, p)
        self._log(f"已导出：{p}")
        self._set_status(f"已导出 {p.name}")
        messagebox.showinfo("导出成功", str(p))

    def on_report(self):
        if self.busy:
            self._set_status("有任务在跑，稍后再试")
            return
        self._set_busy(True)
        self._set_status("正在生成质量评估与图表…")
        threading.Thread(target=self._worker_report, daemon=True).start()

    def _worker_report(self):
        try:
            import quality
            import visualize
            rep = quality.evaluate()
            charts = visualize.run_all()
            self.q.put(("report", rep, charts))
        except Exception as e:                          # pragma: no cover
            self.q.put(("error", "", f"{type(e).__name__}: {e}"))

    def on_batch(self):
        BatchDialog(self.root, self)

    def on_help(self):
        readme = C.BASE_DIR / "README.md"
        if readme.exists():
            open_path(readme)
        else:
            messagebox.showinfo("使用说明", "未找到 README.md")

    def on_about(self):
        messagebox.showinfo(
            "关于",
            "人物信息抓取与提取（百度百科）\n\n"
            "智能计算综合实践2 · 项目一\n"
            f"引擎：{self.cfg['engine']}　并发：{self.cfg['workers']}\n"
            "数据来源：百度百科")

    def _on_close(self):
        if self.busy and not messagebox.askyesno("确认", "任务尚未结束，仍要退出？"):
            return
        try:
            dump_json(self.cfg, CONFIG_FILE)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------- 队列 ----
    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                self._handle(item)
        except queue.Empty:
            pass
        except Exception as e:                          # pragma: no cover
            self._log(f"界面刷新异常：{type(e).__name__}: {e}")
        finally:
            # 必须放在 finally：一旦中途抛异常，after 链断了界面就再也不刷新了
            self.root.after(120, self._drain)

    def _handle(self, item):
        kind = item[0]
        if kind == "fetch":
            _, url, r, rec = item
            self._set_busy(False)
            self._render(url, r, rec)
        elif kind == "fetch_failed":
            _, url, r = item
            self._set_busy(False)
            self._set_text(self.txt_html, r.html or "")
            self._set_status(f"抓取失败：{r.status} —— {r.error}")
            self._log(f"失败 {r.status}：{r.error}")
            messagebox.showwarning("抓取失败", f"{r.status}\n{r.error}")
        elif kind == "error":
            _, url, msg = item
            self._set_busy(False)
            self._set_status("出错：" + msg)
            self._log("ERROR " + msg)
            messagebox.showerror("出错", msg)
        elif kind == "report":
            _, rep, charts = item
            self._set_busy(False)
            n = rep.get("total_records", 0)
            self._set_status(f"质量评估完成：{n} 条记录，{len(charts)} 张图表")
            self._log(f"质量评估完成：{n} 条；图表 {len(charts)} 张 -> {C.CHART_DIR}")
            messagebox.showinfo("完成",
                                f"已更新：\n{C.QUALITY_MD}\n{len(charts)} 张图表在 {C.CHART_DIR}")
        elif kind == "batch_progress":
            _, done, total, name = item
            self.pbar["value"] = done / total * 100 if total else 0
            self._set_status(f"批量抓取 {done}/{total}：{name}")
        elif kind == "batch_done":
            _, ok, fail, names = item
            self._set_busy(False)
            for url, nm in names:
                self._add_history(url, nm)
            self._set_status(f"批量完成：成功 {ok}，失败 {fail}")
            self._log(f"批量抓取完成：成功 {ok}，失败 {fail}")
            messagebox.showinfo("批量抓取完成", f"成功 {ok} 条，失败 {fail} 条")
        elif kind == "log":
            self._log(item[1])


class SettingsDialog:
    """参数设置面板：字号 + 抓取参数 + 展示选项。"""

    def __init__(self, parent, app: App):
        self.app = app
        self.cfg = dict(app.cfg)
        self.win = tk.Toplevel(parent)
        self.win.title("参数设置")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.configure(bg=BG)
        self.win.resizable(False, False)

        pad = {"padx": 10, "pady": 5}
        f1 = ttk.Labelframe(self.win, text=" 显示 ", padding=10)
        f1.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        self.v_ui_size = tk.IntVar(value=int(self.cfg["ui_size"]))
        self.v_mono_size = tk.IntVar(value=int(self.cfg["mono_size"]))
        self.v_wrap = tk.BooleanVar(value=bool(self.cfg["wrap"]))
        self.v_max_show = tk.IntVar(value=int(self.cfg["max_show"]) // 1000)
        self._row(f1, 0, "界面字号 (pt)", ttk.Spinbox, self.v_ui_size, 7, 28)
        self._row(f1, 1, "正文字号 (pt)", ttk.Spinbox, self.v_mono_size, 7, 28)
        self._row(f1, 2, "展示上限 (千字符)", ttk.Spinbox, self.v_max_show, 10, 5000)
        ttk.Checkbutton(f1, text="文本框自动换行", variable=self.v_wrap).grid(
            row=3, column=1, sticky="w", **pad)

        f2 = ttk.Labelframe(self.win, text=" 抓取参数 ", padding=10)
        f2.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        self.v_engine = tk.StringVar(value=self.cfg["engine"])
        self.v_workers = tk.IntVar(value=int(self.cfg["workers"]))
        self.v_smin = tk.DoubleVar(value=float(self.cfg["sleep_min"]))
        self.v_smax = tk.DoubleVar(value=float(self.cfg["sleep_max"]))
        self.v_retry = tk.IntVar(value=int(self.cfg["max_retries"]))
        self.v_timeout = tk.IntVar(value=int(self.cfg["page_timeout"]))
        ttk.Label(f2, text="抓取引擎").grid(row=0, column=0, sticky="e", **pad)
        ttk.Combobox(f2, textvariable=self.v_engine, width=16, state="readonly",
                     values=["selenium", "requests"]).grid(row=0, column=1, sticky="w", **pad)
        self._row(f2, 1, "并发线程数", ttk.Spinbox, self.v_workers, 1, 16)
        self._row(f2, 2, "最小请求间隔 (s)", ttk.Spinbox, self.v_smin, 0, 30, inc=0.1)
        self._row(f2, 3, "最大请求间隔 (s)", ttk.Spinbox, self.v_smax, 0, 60, inc=0.1)
        self._row(f2, 4, "单页重试次数", ttk.Spinbox, self.v_retry, 0, 10)
        self._row(f2, 5, "单页超时 (s)", ttk.Spinbox, self.v_timeout, 5, 120)
        self.v_save_html = tk.BooleanVar(value=bool(self.cfg["save_html"]))
        self.v_ignore_proxy = tk.BooleanVar(value=bool(self.cfg["ignore_proxy"]))
        ttk.Checkbutton(f2, text="归档原始 HTML（gzip，占空间大）",
                        variable=self.v_save_html).grid(row=6, column=1, sticky="w", **pad)
        ttk.Checkbutton(f2, text="忽略系统代理（推荐开启）",
                        variable=self.v_ignore_proxy).grid(row=7, column=1, sticky="w", **pad)

        f3 = ttk.Labelframe(self.win, text=" 其它 ", padding=10)
        f3.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        self.v_auto_export = tk.BooleanVar(value=bool(self.cfg["auto_export"]))
        self.v_hist = tk.IntVar(value=int(self.cfg["history_limit"]))
        ttk.Checkbutton(f3, text="抓取后自动导出单条 JSON", variable=self.v_auto_export).grid(
            row=0, column=1, sticky="w", **pad)
        self._row(f3, 1, "历史记录上限", ttk.Spinbox, self.v_hist, 10, 2000)

        btns = ttk.Frame(self.win)
        btns.grid(row=3, column=0, sticky="e", padx=12, pady=(6, 12))
        ttk.Button(btns, text="取消", command=self.win.destroy).pack(side="right")
        ttk.Button(btns, text="应用", style="Primary.TButton",
                   command=self.apply).pack(side="right", padx=8)

    def _row(self, parent, r, label, widget_cls, var, lo, hi, inc=1):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="e", padx=10, pady=5)
        widget_cls(parent, from_=lo, to=hi, increment=inc, textvariable=var,
                   width=10).grid(row=r, column=1, sticky="w", padx=10, pady=5)

    def apply(self):
        c = self.app.cfg
        c["ui_size"] = int(self.v_ui_size.get())
        c["mono_size"] = int(self.v_mono_size.get())
        c["wrap"] = bool(self.v_wrap.get())
        c["max_show"] = max(1000, int(self.v_max_show.get()) * 1000)
        c["engine"] = self.v_engine.get()
        c["workers"] = int(self.v_workers.get())
        c["sleep_min"] = float(self.v_smin.get())
        c["sleep_max"] = float(self.v_smax.get())
        c["max_retries"] = int(self.v_retry.get())
        c["page_timeout"] = int(self.v_timeout.get())
        c["save_html"] = bool(self.v_save_html.get())
        c["ignore_proxy"] = bool(self.v_ignore_proxy.get())
        c["auto_export"] = bool(self.v_auto_export.get())
        c["history_limit"] = int(self.v_hist.get())
        # 间隔上下限不能反
        if c["sleep_min"] > c["sleep_max"]:
            c["sleep_min"], c["sleep_max"] = c["sleep_max"], c["sleep_min"]

        apply_cfg_to_config(c)
        self.app.var_wrap.set(c["wrap"])
        self.app._apply_fonts()
        self.app._toggle_wrap()
        self.app._set_status(
            f"设置已保存：{c['engine']} / {c['workers']} 线程 / 间隔 "
            f"{c['sleep_min']}~{c['sleep_max']}s / 字号 {c['ui_size']}pt")
        self.app._log("设置已更新并写入 output/gui_config.json")
        self.win.destroy()


class BatchDialog:
    """批量抓取对话框：多行输入人名 / URL，多线程 + 真实进度条。"""

    def __init__(self, parent, app: App):
        self.app = app
        self.win = tk.Toplevel(parent)
        self.win.title("批量抓取")
        self.win.geometry("640x500")
        self.win.transient(parent)
        self.win.grab_set()
        self.win.configure(bg=BG)

        ttk.Label(self.win, text="每行一个人名或百度百科 URL（结果追加到数据文件，"
                                "支持断点续爬）", style="Sub.TLabel").pack(
            anchor="w", padx=12, pady=(12, 4))

        wrap = ttk.Frame(self.win, style="Card.TFrame", padding=1)
        wrap.pack(fill="both", expand=True, padx=12)
        self.text = tk.Text(wrap, height=16, wrap="none",
                            font=(app.cfg["mono_font"], app.cfg["mono_size"]),
                            bg=CARD, fg=TEXT, relief="flat", borderwidth=0,
                            highlightthickness=0, padx=8, pady=6)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        opts = ttk.Frame(self.win)
        opts.pack(fill="x", padx=12, pady=8)
        ttk.Label(opts, text="线程数").pack(side="left")
        self.workers = tk.IntVar(value=int(app.cfg["workers"]))
        ttk.Spinbox(opts, from_=1, to=16, textvariable=self.workers, width=4).pack(
            side="left", padx=(6, 14))
        ttk.Label(opts, text="引擎").pack(side="left")
        self.engine = tk.StringVar(value=app.cfg["engine"])
        ttk.Combobox(opts, textvariable=self.engine, values=["selenium", "requests"],
                     width=12, state="readonly").pack(side="left", padx=6)
        ttk.Label(opts, text="　（引擎与并发取「参数设置」里的值）",
                  style="Sub.TLabel").pack(side="left")

        btns = ttk.Frame(self.win)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="取消", command=self.win.destroy).pack(side="right")
        ttk.Button(btns, text="开始抓取", style="Primary.TButton",
                   command=self.start).pack(side="right", padx=8)

    def start(self):
        lines = [l.strip() for l in self.text.get("1.0", "end").splitlines()]
        lines = [l for l in lines if l]
        if not lines:
            messagebox.showinfo("提示", "请输入至少一行")
            return

        # 批量走的是「人名 -> 拼 URL」这条链，URL 输入先还原成人名
        plain = []
        for l in lines:
            if l.startswith("http"):
                nm = _name_from_url(l)
                if nm:
                    plain.append(nm)
            else:
                plain.append(l)
        if not plain:
            messagebox.showinfo("提示", "没有解析出可用的人名")
            return

        self.win.destroy()
        app = self.app
        engine = self.engine.get()
        workers = int(self.workers.get())
        app.cfg["engine"], app.cfg["workers"] = engine, workers
        apply_cfg_to_config(app.cfg)

        app._set_busy(True, determinate=True)     # 进度条要能显示真实百分比
        app.pbar["value"] = 0
        app._set_status(f"批量抓取中，共 {len(plain)} 条…")
        app._log(f"批量抓取开始：{len(plain)} 条，引擎 {engine}，{workers} 线程")

        def on_progress(done, total, r):
            app.q.put(("batch_progress", done, total, r.name))

        def worker():
            try:
                import pipeline
                from utils import read_jsonl
                before = {x.get("name") for x in read_jsonl(C.PERSONS_JSONL)}
                res, failed, _ = pipeline.run(
                    engine=engine, workers=workers, names=plain,
                    resume=False, on_progress=on_progress)
                # 批量结果也记进历史，便于回看
                after = read_jsonl(C.PERSONS_JSONL)
                seen, pairs = set(), []
                for row in after:
                    nm = row.get("name")
                    if nm and nm not in before and nm not in seen:
                        seen.add(nm)
                        pairs.append((row.get("url", ""), nm))
                app.q.put(("batch_done", len(res), len(failed), pairs[:500]))
            except Exception as e:                      # pragma: no cover
                app.q.put(("error", "", f"{type(e).__name__}: {e}"))

        threading.Thread(target=worker, daemon=True).start()


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
