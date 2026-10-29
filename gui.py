# -*- coding: utf-8 -*-
"""抖音下载器 · 图形界面

设计目标
--------
1. 双击即用，不依赖任何额外 GUI 库（纯标准库 Tkinter + Pillow）
2. 每一步可视化：识别 → 会话 → 接口 → 解析 → 下载，全程有状态和日志
3. 平台可扩展：下拉框从 platforms.py 读，未实现的平台显示「即将支持」
4. 线程安全：所有网络/解析在后台线程，UI 更新走队列
"""
from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from douyin_dl.models import Aweme  # noqa: E402
from douyin_dl import platforms  # noqa: E402
from douyin_dl.downloader import DownloadCancelled, DownloadControl  # noqa: E402
from douyin_dl.history import History, HistoryRecord, open_in_explorer  # noqa: E402

# ----------------------------------------------------------------------
# 主题
# ----------------------------------------------------------------------
BG = "#0E1117"
CARD = "#171A21"
CARD2 = "#1D212A"
BORDER = "#262A33"
TEXT = "#E6E8EB"
DIM = "#8B919C"
FAINT = "#5A6270"
ACCENT = "#FE2C55"
ACCENT_DIM = "#B01F3D"
OK = "#3FB950"
WARN = "#D29922"
ERR = "#F85149"
BLUE = "#58A6FF"

FONT = "Microsoft YaHei UI"
F_TITLE = (FONT, 13, "bold")
F_BODY = (FONT, 10)
F_SMALL = (FONT, 9)
F_MONO = ("Consolas", 9)

STATUS_TEXT = {
    "pending": ("等待中", FAINT, "○"),
    "parsing": ("解析中", BLUE, "◌"),
    "parsed": ("已解析", OK, "●"),
    "downloading": ("下载中", ACCENT, "◐"),
    "paused": ("已暂停", WARN, "‖"),
    "done": ("已完成", OK, "✓"),
    "cancelled": ("已取消", DIM, "⊘"),
    "failed": ("失败", ERR, "✗"),
    "skip": ("已跳过", DIM, "−"),
}


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def human_size(n: int) -> str:
    if not n:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def human_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    for unit in ("B/s", "KB/s", "MB/s"):
        if bps < 1024:
            return f"{bps:.1f}{unit}"
        bps /= 1024
    return f"{bps:.1f}GB/s"


def _alive(task: Any) -> bool:
    """任务卡片还在不在（用户可能已经删掉了）。"""
    try:
        return task.row is not None and task.row.winfo_exists()
    except Exception:  # noqa: BLE001
        return False


class ScrollFrame(tk.Frame):
    """可滚动容器，内部用 .body 放子控件。"""

    def __init__(self, master: tk.Misc, bg: str = BG, **kw: Any) -> None:
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._vbar_shown = False

        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _sync_bar(self) -> None:
        """内容超出才显示滚动条，否则隐藏（避免常驻白条）。"""
        try:
            need = self.body.winfo_height() > self.canvas.winfo_height() + 4
        except Exception:  # noqa: BLE001
            return
        if need and not self._vbar_shown:
            self.vbar.pack(side="right", fill="y", before=self.canvas)
            self._vbar_shown = True
        elif not need and self._vbar_shown:
            self.vbar.pack_forget()
            self._vbar_shown = False

    def _on_body(self, _e: Any = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_bar()

    def _on_canvas(self, e: Any) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)
        self._sync_bar()

    def _on_wheel(self, e: Any) -> None:
        # 只有指针在容器内才滚动，避免抢别的区域
        try:
            x, y = self.winfo_pointerxy()
            wx, wy = self.winfo_rootx(), self.winfo_rooty()
            if wx <= x <= wx + self.winfo_width() and wy <= y <= wy + self.winfo_height():
                self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------
# 任务
# ----------------------------------------------------------------------
@dataclass
class Task:
    url: str
    idx: int
    status: str = "pending"
    aweme: Optional[Aweme] = None
    progress: float = 0.0
    received: int = 0
    total: int = 0
    speed: float = 0.0
    error: str = ""
    selected_media: int = 0
    quality_label: str = ""
    files: List[Path] = field(default_factory=list)
    control: Any = None          # DownloadControl
    cover_img: Any = None
    row: Optional["TaskRow"] = None
    _last_bytes: int = 0
    _last_t: float = field(default_factory=time.time)


class TaskRow(tk.Frame):
    """一个任务的卡片。"""

    def __init__(self, master: tk.Misc, task: Task, app: "App") -> None:
        super().__init__(master, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.task = task
        self.app = app
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        pad = tk.Frame(self, bg=CARD, padx=12, pady=10)
        pad.pack(fill="x")

        top = tk.Frame(pad, bg=CARD)
        top.pack(fill="x")

        self.lbl_icon = tk.Label(top, text="○", bg=CARD, fg=FAINT, font=(FONT, 13))
        self.lbl_icon.pack(side="left", padx=(0, 8))

        # 注意：右侧控件必须先 pack，否则左侧长标题会把它们挤出可视区
        right = tk.Frame(top, bg=CARD)
        right.pack(side="right", padx=(8, 0))

        self.lbl_status = tk.Label(right, text="等待中", bg=CARD, fg=FAINT, font=F_SMALL)
        self.lbl_status.pack(side="right", padx=(10, 0))

        # 按钮组：从左到右 清晰度 | 主操作 | 取消 | 删除
        btns = tk.Frame(right, bg=CARD)
        btns.pack(side="right")

        self.quality_var = tk.StringVar(value="")
        self.cb_quality = ttk.Combobox(
            btns, textvariable=self.quality_var, width=13, state="readonly",
            style="Dark.TCombobox", font=F_SMALL,
        )
        self.cb_quality.pack(side="left", padx=(0, 4))
        self.cb_quality.bind("<<ComboboxSelected>>", self._on_quality)

        self.btn_main = tk.Button(
            btns, text="下载", command=self._on_main,
            bg=ACCENT, fg="white", font=F_SMALL, relief="flat",
            activebackground=ACCENT_DIM, activeforeground="white",
            bd=0, padx=14, pady=4, cursor="hand2", state="disabled", width=6,
        )
        self.btn_main.pack(side="left", padx=2)

        self.btn_cancel = tk.Button(
            btns, text="取消", command=self._on_cancel,
            bg=CARD2, fg=DIM, font=F_SMALL, relief="flat",
            activebackground=BORDER, activeforeground=TEXT,
            bd=0, padx=10, pady=4, cursor="hand2",
        )
        # 初始隐藏，下载中/暂停时才出现

        self.btn_del = tk.Button(
            btns, text="✕", command=self._on_delete,
            bg=CARD, fg=FAINT, font=F_SMALL, relief="flat",
            activebackground=CARD2, activeforeground=ERR,
            bd=0, padx=6, pady=4, cursor="hand2",
        )
        self.btn_del.pack(side="left", padx=(2, 0))

        info = tk.Frame(top, bg=CARD)
        info.pack(side="left", fill="x", expand=True)

        self.lbl_title = tk.Label(
            info, text="等待解析…", bg=CARD, fg=TEXT, font=F_BODY,
            anchor="w", justify="left", wraplength=620,
        )
        self.lbl_title.pack(fill="x")

        self.lbl_sub = tk.Label(
            info, text=self.task.url[:90], bg=CARD, fg=DIM, font=F_SMALL,
            anchor="w", justify="left", wraplength=620,
        )
        self.lbl_sub.pack(fill="x")

        # 进度条
        self.prog_holder = tk.Frame(pad, bg=CARD)
        self.prog_holder.pack(fill="x", pady=(8, 0))
        self.prog = ttk.Progressbar(
            self.prog_holder, style="Accent.Horizontal.TProgressbar",
            orient="horizontal", length=200, mode="determinate",
        )
        self.prog.pack(side="left", fill="x", expand=True)

        self.lbl_prog = tk.Label(
            self.prog_holder, text="", bg=CARD, fg=DIM, font=F_SMALL, width=22, anchor="e"
        )
        self.lbl_prog.pack(side="right", padx=(8, 0))
        self._hide_progress()

    def _hide_progress(self) -> None:
        self.prog_holder.pack_forget()

    def _show_progress(self) -> None:
        if not self.prog_holder.winfo_ismapped():
            self.prog_holder.pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------------
    def set_status(self, status: str, error: str = "") -> None:
        self.task.status = status
        text, color, icon = STATUS_TEXT.get(status, ("?", DIM, "?"))
        self.lbl_icon.configure(text=icon, fg=color)
        self.lbl_status.configure(text=text, fg=color)
        if error:
            self.lbl_sub.configure(text=error[:110], fg=ERR)
        self._render_actions()

    def _render_actions(self) -> None:
        """按状态切换按钮：主操作文字 + 是否显示取消。"""
        st = self.task.status
        show_cancel = st in ("downloading", "paused")
        if show_cancel:
            if not self.btn_cancel.winfo_ismapped():
                self.btn_cancel.pack(side="left", padx=2, before=self.btn_del)
        elif self.btn_cancel.winfo_ismapped():
            self.btn_cancel.pack_forget()

        spec = {
            "pending":     ("…",        "disabled", CARD2,  FAINT),
            "parsing":     ("…",        "disabled", CARD2,  FAINT),
            "parsed":      ("下载",      "normal",   ACCENT, "white"),
            "downloading": ("暂停",      "normal",   CARD2,  TEXT),
            "paused":      ("继续",      "normal",   ACCENT, "white"),
            "done":        ("打开",      "normal",   CARD2,  TEXT),
            "failed":      ("重试",      "normal",   ACCENT, "white"),
            "cancelled":   ("重新下载",  "normal",   ACCENT, "white"),
        }
        text, state, bg, fg = spec.get(st, ("下载", "disabled", CARD2, FAINT))
        self.btn_main.configure(text=text, state=state, bg=bg, fg=fg)

        maps = getattr(self, "media_map", [])
        self.cb_quality.configure(
            state="readonly" if (st == "parsed" and len(maps) > 1) else "disabled"
        )

    def set_aweme(self, aweme: Aweme) -> None:
        self.task.aweme = aweme
        self.lbl_title.configure(text=f"{aweme.author_name} · {aweme.title(34)}")
        kind = "图集" if aweme.is_image_post else "视频"
        stats = aweme.statistics
        parts = [kind]
        if aweme.duration_ms:
            parts.append(aweme.duration_text)
        if stats.get("digg"):
            parts.append(f"赞 {stats['digg']}")
        parts.append(f"后端 {aweme.backend}")
        self.lbl_sub.configure(text=" · ".join(str(p) for p in parts), fg=DIM)

        # 清晰度列表：把 aweme.medias 里每一档列出来
        labels: List[str] = []
        self.media_map: List[tuple[str, int]] = []
        if aweme.is_image_post:
            labels.append(f"图集 {len(aweme.images)}P")
            self.media_map.append((labels[0], 0))
        else:
            for i, m in enumerate(aweme.medias):
                if m.kind != "video":
                    continue
                label = m.quality or "default"
                bitrate = m.extra.get("bitrate", 0) if isinstance(m.extra, dict) else 0
                if bitrate:
                    label = f"{label} · {bitrate // 1000}k"
                labels.append(label)
                self.media_map.append((label, i))

        if not labels:
            labels = ["default"]
            self.media_map = [("default", 0)]

        self.cb_quality.configure(values=labels)
        self.quality_var.set(labels[0])
        self.task.selected_media = self.media_map[0][1]
        self.task.quality_label = labels[0]
        self._render_actions()

    def _on_quality(self, _e: Any = None) -> None:
        sel = self.quality_var.get()
        for label, idx in getattr(self, "media_map", []):
            if label == sel:
                self.task.selected_media = idx
                self.task.quality_label = label
                break

    def on_progress(self, received: int, total: int) -> None:
        self._show_progress()
        self.task.received = received
        self.task.total = total or self.task.total
        pct = (received / total * 100) if total else 0
        self.task.progress = pct
        self.prog.configure(value=pct)

        now = time.time()
        dt = now - self.task._last_t
        if dt >= 0.5:
            speed = (received - self.task._last_bytes) / dt
            self.task.speed = speed
            self.task._last_t = now
            self.task._last_bytes = received

        if self.task.total:
            self.lbl_prog.configure(
                text=f"{pct:.0f}%  {human_size(received)}/{human_size(self.task.total)}  "
                     f"{human_speed(self.task.speed)}"
            )
        else:
            self.lbl_prog.configure(text=f"{human_size(received)}  {human_speed(self.task.speed)}")

    def finish(self, status: str, msg: str = "") -> None:
        self.set_status(status, msg if status == "failed" else "")
        if status == "done":
            self.prog.configure(value=100)
            self.lbl_prog.configure(text="完成")
        elif status == "cancelled":
            self.lbl_prog.configure(text="已取消")
        elif status == "failed":
            self.lbl_prog.configure(text="失败")

    # ------------------------------------------------------------------
    # 按钮分发
    def _on_main(self) -> None:
        st = self.task.status
        if st == "downloading":
            self.app.pause_task(self.task)
        elif st == "paused":
            self.app.resume_task(self.task)
        elif st == "done":
            self.app.open_task_file(self.task)
        else:
            self.app.download_task(self.task)

    def _on_cancel(self) -> None:
        self.app.cancel_task(self.task)

    def _on_delete(self) -> None:
        self.app.delete_task(self.task)


# ----------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("抖音下载器")
        self.geometry("1080x820")
        self.minsize(900, 640)
        self.configure(bg=BG)

        self.tasks: List[Task] = []
        self.parser: Any = None
        self.out_dir = ROOT / "downloads"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.q: queue.Queue = queue.Queue()
        self._busy = False
        self.history = History()
        self._current_task: Optional[Task] = None
        self.rate_limit = 0          # 字节/秒，0 = 不限速

        self._setup_style()
        self._build_ui()
        self.after(80, self._drain)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    def _setup_style(self) -> None:
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        s.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=CARD2, background=ACCENT, bordercolor=CARD2,
            lightcolor=ACCENT, darkcolor=ACCENT, thickness=6,
        )
        s.configure(
            "Dark.TCombobox",
            fieldbackground=CARD2, background=CARD2, foreground=TEXT,
            arrowcolor=DIM, bordercolor=BORDER, lightcolor=CARD2, darkcolor=CARD2,
            selectbackground=CARD2, selectforeground=TEXT,
        )
        s.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", CARD2)],
            foreground=[("readonly", TEXT)],
            background=[("readonly", CARD2)],
        )
        self.option_add("*TCombobox*Listbox.background", CARD2)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")

        s.configure(
            "Dark.Vertical.TScrollbar",
            background=CARD2, troughcolor=BG, bordercolor=BG,
            arrowcolor=DIM, darkcolor=CARD2, lightcolor=CARD2,
        )
        # 历史窗口的表格
        s.configure(
            "Dark.Treeview",
            background=CARD, fieldbackground=CARD, foreground=TEXT,
            borderwidth=0, rowheight=27, font=F_SMALL,
        )
        s.configure(
            "Dark.Treeview.Heading",
            background=CARD2, foreground=DIM, relief="flat", font=F_SMALL,
        )
        s.map(
            "Dark.Treeview",
            background=[("selected", ACCENT_DIM)],
            foreground=[("selected", "white")],
        )
        s.map("Dark.Treeview.Heading", background=[("active", BORDER)])

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # ===== 顶栏 =====
        header = tk.Frame(self, bg=BG, padx=18, pady=12)
        header.pack(fill="x")

        dot = tk.Label(header, text="●", bg=BG, fg=ACCENT, font=(FONT, 14))
        dot.pack(side="left")
        tk.Label(header, text=" 抖音下载器", bg=BG, fg=TEXT, font=(FONT, 14, "bold")).pack(side="left")

        self.platform_var = tk.StringVar(value="douyin")
        self.cb_platform = ttk.Combobox(
            header, textvariable=self.platform_var, width=14, state="readonly",
            style="Dark.TCombobox", font=F_BODY,
        )
        self.cb_platform["values"] = [f"{p.name}" + ("" if p.ready else "  ·即将支持")
                                      for p in platforms.PLATFORMS.values()]
        self.cb_platform.current(0)
        self.cb_platform.pack(side="left", padx=(18, 0))
        self.cb_platform.bind("<<ComboboxSelected>>", self._on_platform)

        self.lbl_platform_hint = tk.Label(
            header, text="", bg=BG, fg=FAINT, font=F_SMALL
        )
        self.lbl_platform_hint.pack(side="left", padx=(10, 0))

        # 右侧按钮
        for text, cmd in (
            ("打开目录", self._open_outdir),
            ("设置", self._open_settings),
            ("历史", self._show_history),
            ("清空日志", lambda: self._clear_log()),
        ):
            tk.Button(
                header, text=text, command=cmd, bg=CARD, fg=DIM, font=F_SMALL,
                relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
                activebackground=CARD2, activeforeground=TEXT,
            ).pack(side="right", padx=4)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ===== 输入区 =====
        input_card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        input_card.pack(fill="x", padx=18, pady=(14, 10))

        # 静态提示条，比 Text 内占位更可靠
        hint_bar = tk.Frame(input_card, bg=CARD)
        hint_bar.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(
            hint_bar,
            text="粘贴分享链接 / 分享文案 / 作品 ID，一行一个（支持批量）",
            bg=CARD, fg=FAINT, font=F_SMALL, anchor="w",
        ).pack(side="left")

        self.txt_input = tk.Text(
            input_card, height=3, bg=CARD, fg=TEXT, font=F_BODY,
            insertbackground=ACCENT, relief="flat", bd=0, padx=12, pady=8,
            wrap="word",
        )
        self.txt_input.pack(fill="x")
        self.txt_input.bind("<<Modified>>", self._on_text_change)

        bar = tk.Frame(input_card, bg=CARD, padx=12, pady=10)
        bar.pack(fill="x")

        tk.Button(
            bar, text="粘贴", command=self._paste, bg=CARD2, fg=TEXT, font=F_SMALL,
            relief="flat", bd=0, padx=14, pady=5, cursor="hand2",
            activebackground=BORDER, activeforeground=TEXT,
        ).pack(side="left")

        tk.Button(
            bar, text="清空", command=self._clear_input, bg=CARD2, fg=DIM, font=F_SMALL,
            relief="flat", bd=0, padx=14, pady=5, cursor="hand2",
            activebackground=BORDER, activeforeground=TEXT,
        ).pack(side="left", padx=6)

        self.lbl_count = tk.Label(bar, text="", bg=CARD, fg=FAINT, font=F_SMALL)
        self.lbl_count.pack(side="left", padx=12)

        self.btn_parse = tk.Button(
            bar, text="开始解析 ▶", command=self._on_parse, bg=ACCENT, fg="white",
            font=(FONT, 10, "bold"), relief="flat", bd=0, padx=22, pady=6,
            cursor="hand2", activebackground=ACCENT_DIM, activeforeground="white",
        )
        self.btn_parse.pack(side="right")

        # ===== 中部分栏 =====
        body = tk.PanedWindow(self, orient="vertical", bg=BG, sashwidth=6,
                              sashrelief="flat", bd=0)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 6))

        # 上：任务列表
        upper = tk.Frame(body, bg=BG)
        tbar = tk.Frame(upper, bg=BG)
        tbar.pack(fill="x", pady=(0, 6))

        self.lbl_tasks = tk.Label(tbar, text="任务", bg=BG, fg=TEXT, font=F_TITLE)
        self.lbl_tasks.pack(side="left")

        tk.Button(
            tbar, text="全部下载", command=self._download_all, bg=CARD, fg=DIM,
            font=F_SMALL, relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
            activebackground=CARD2, activeforeground=TEXT,
        ).pack(side="right")

        tk.Button(
            tbar, text="移除已完成", command=self._remove_done, bg=CARD, fg=DIM,
            font=F_SMALL, relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
            activebackground=CARD2, activeforeground=TEXT,
        ).pack(side="right", padx=6)

        self.task_scroll = ScrollFrame(upper, bg=BG)
        self.task_scroll.pack(fill="both", expand=True)
        self.task_empty = tk.Label(
            self.task_scroll.body, text="还没有任务。粘贴链接后点「开始解析」。",
            bg=BG, fg=FAINT, font=F_BODY, pady=30,
        )
        self.task_empty.pack(fill="x")

        body.add(upper, minsize=200, stretch="always")

        # 下：日志
        lower = tk.Frame(body, bg=BG)
        lbar = tk.Frame(lower, bg=BG)
        lbar.pack(fill="x", pady=(6, 4))
        tk.Label(lbar, text="运行日志", bg=BG, fg=TEXT, font=F_TITLE).pack(side="left")
        self.lbl_step = tk.Label(lbar, text="", bg=BG, fg=BLUE, font=F_SMALL)
        self.lbl_step.pack(side="left", padx=12)

        self.log = tk.Text(
            lower, height=8, bg="#0A0C10", fg=DIM, font=F_MONO,
            relief="flat", bd=0, padx=10, pady=8, wrap="word", state="disabled",
        )
        self.log.pack(fill="both", expand=True)
        for tag, color in (
            ("info", DIM), ("ok", OK), ("warn", WARN), ("err", ERR), ("time", FAINT),
        ):
            self.log.tag_configure(tag, foreground=color)

        body.add(lower, minsize=140, stretch="always")

        # ===== 状态栏 =====
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        status = tk.Frame(self, bg=BG, padx=18, pady=6)
        status.pack(fill="x")
        self.lbl_status = tk.Label(status, text="就绪", bg=BG, fg=DIM, font=F_SMALL)
        self.lbl_status.pack(side="left")
        self.lbl_outdir = tk.Label(
            status, text=f"输出目录  {self.out_dir}", bg=BG, fg=FAINT, font=F_SMALL
        )
        self.lbl_outdir.pack(side="right")

        # 初始分栏：任务区约 340px，其余给日志
        self.after(80, lambda: body.sash_place(0, 0, 340))

    # ------------------------------------------------------------------
    # 输入辅助
    def _on_text_change(self, _e: Any = None) -> None:
        try:
            self.txt_input.edit_modified(False)
        except Exception:  # noqa: BLE001
            pass
        lbl = getattr(self, "lbl_count", None)
        if lbl is None:
            return
        raw = self.txt_input.get("1.0", "end").strip()
        n = len([ln for ln in raw.splitlines() if ln.strip()])
        lbl.configure(text=f"{n} 条" if n else "")

    def _input_text(self) -> str:
        return self.txt_input.get("1.0", "end").strip()

    # ------------------------------------------------------------------
    def _log(self, msg: str, tag: str = "info") -> None:
        self.q.put(("log", (msg, tag)))

    def _drain(self) -> None:
        """主线程消费队列。"""
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(*payload)
                elif kind == "step":
                    self.lbl_step.configure(text=payload)
                elif kind == "status":
                    self.lbl_status.configure(text=payload)
                elif kind == "task_refresh":
                    payload()
                elif kind == "parsed":
                    task, aweme = payload
                    if _alive(task):
                        task.row.set_aweme(aweme)
                        task.row.set_status("parsed")
                elif kind == "task_status":
                    task, status, err = payload
                    if _alive(task):
                        task.row.set_status(status, err)
                elif kind == "task_sub":
                    task, msg = payload
                    if _alive(task) and task.status == "parsing":
                        task.row.lbl_sub.configure(text=msg[:100], fg=BLUE)
                elif kind == "progress":
                    task, recv, total = payload
                    if _alive(task):
                        task.row.on_progress(recv, total)
                elif kind == "finish":
                    task, status, msg = payload
                    if _alive(task):
                        task.row.finish(status, msg)
                elif kind == "parse_done":
                    self._on_parse_done()
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _append_log(self, msg: str, tag: str) -> None:
        self.log.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] ", "time")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    def _on_platform(self, _e: Any = None) -> None:
        names = list(platforms.PLATFORMS.values())
        p = names[self.cb_platform.current()]
        self.lbl_platform_hint.configure(text=p.status_text)
        if not p.ready:
            self.btn_parse.configure(state="disabled", bg=CARD2, fg=FAINT)
            self._log(f"{p.name} 尚未实现：{p.note}", "warn")
        else:
            self.btn_parse.configure(state="normal", bg=ACCENT, fg="white")

    def _paste(self) -> None:
        try:
            data = self.clipboard_get().strip()
        except Exception:  # noqa: BLE001
            self._log("剪贴板是空的", "warn")
            return
        if not data:
            return
        cur = self._input_text()
        prefix = "\n" if cur else ""
        self.txt_input.insert("end", prefix + data)
        self.txt_input.tag_remove("sel", "1.0", "end")
        self.txt_input.see("end")
        self._on_text_change()
        self._log(f"已粘贴 {len(data)} 字符", "info")

    def _clear_input(self) -> None:
        self.txt_input.delete("1.0", "end")
        self._on_text_change()

    def _open_outdir(self) -> None:
        try:
            os.startfile(str(self.out_dir))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("打开失败", str(e))

    def _open_settings(self) -> None:
        win = tk.Toplevel(self)
        win.title("设置")
        win.configure(bg=BG)
        win.geometry("520x300")
        win.transient(self)

        tk.Label(win, text="输出目录", bg=BG, fg=TEXT, font=F_TITLE).pack(anchor="w", padx=20, pady=(18, 6))
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=20)
        var = tk.StringVar(value=str(self.out_dir))
        tk.Entry(
            row, textvariable=var, bg=CARD, fg=TEXT, insertbackground=ACCENT,
            relief="flat", font=F_BODY,
        ).pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

        def browse() -> None:
            d = filedialog.askdirectory(initialdir=str(self.out_dir))
            if d:
                var.set(d)

        tk.Button(row, text="浏览", command=browse, bg=CARD2, fg=TEXT, font=F_SMALL,
                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2").pack(side="left")

        tk.Label(win, text="限速（MB/s，0 = 不限）", bg=BG, fg=TEXT, font=F_TITLE).pack(
            anchor="w", padx=20, pady=(16, 6)
        )
        rate_row = tk.Frame(win, bg=BG)
        rate_row.pack(fill="x", padx=20)
        rate_var = tk.StringVar(
            value=f"{self.rate_limit / 1024 / 1024:g}" if self.rate_limit else "0"
        )
        tk.Entry(
            rate_row, textvariable=rate_var, bg=CARD, fg=TEXT, insertbackground=ACCENT,
            relief="flat", font=F_BODY, width=10,
        ).pack(side="left", ipady=6)
        tk.Label(
            rate_row, text="网络慢就设 2，别设太小否则下载会很磨", bg=BG, fg=FAINT, font=F_SMALL
        ).pack(side="left", padx=10)

        tk.Label(
            win,
            text=f"解析缓存：{Path.home() / '.douyin_dl' / 'detail_cache'}（删掉不影响）",
            bg=BG, fg=FAINT, font=F_SMALL, wraplength=460, justify="left",
        ).pack(anchor="w", padx=20, pady=(14, 0))

        def save() -> None:
            p = Path(var.get().strip() or "downloads")
            p.mkdir(parents=True, exist_ok=True)
            self.out_dir = p
            self.lbl_outdir.configure(text=f"输出目录  {p}")
            try:
                mb = float(rate_var.get().strip() or "0")
                self.rate_limit = int(max(0.0, mb) * 1024 * 1024)
            except ValueError:
                self.rate_limit = 0
            self._log(
                f"输出目录 → {p}；限速 → "
                f"{'不限' if not self.rate_limit else f'{self.rate_limit / 1024 / 1024:g} MB/s'}",
                "ok",
            )
            win.destroy()

        tk.Button(
            win, text="保存", command=save, bg=ACCENT, fg="white", font=(FONT, 10, "bold"),
            relief="flat", bd=0, padx=24, pady=8, cursor="hand2",
        ).pack(pady=22)

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def _get_parser(self) -> Any:
        if self.parser is None:
            p = platforms.get("douyin")
            assert p and p.factory
            self.parser = p.factory(on_step=self._step_cb)
        return self.parser

    def _step_cb(self, level: str, msg: str) -> None:
        """解析器上报的每一步：进日志，也写到当前任务卡片上。"""
        self.q.put(("step", msg))
        task = getattr(self, "_current_task", None)
        if task is not None:
            self.q.put(("task_sub", (task, msg)))

    def _on_parse(self) -> None:
        if self._busy:
            self._log("正在处理中，请稍候", "warn")
            return
        raw = self._input_text()
        if not raw:
            messagebox.showinfo("提示", "先粘贴链接再解析")
            return

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return

        # 平台判断
        p = platforms.detect(raw)
        if p and not p.ready:
            messagebox.showinfo(
                "暂不支持", f"识别到 {p.name} 链接。\n\n{p.note}\n\n架构已预留，后续可直接接入。"
            )
            return

        self._busy = True
        self.btn_parse.configure(state="disabled", text="解析中…", bg=CARD2, fg=DIM)
        if self.task_empty.winfo_ismapped():
            self.task_empty.pack_forget()

        start = len(self.tasks)
        for i, line in enumerate(lines):
            task = Task(url=line, idx=start + i + 1)
            self.tasks.append(task)
            row = TaskRow(self.task_scroll.body, task, self)
            row.pack(fill="x", pady=(0, 8), padx=(0, 4))
            task.row = row
            task.row.set_status("pending")

        self._refresh_task_count()
        self._log(f"开始处理 {len(lines)} 条", "info")
        threading.Thread(target=self._parse_worker, args=(lines, start), daemon=True).start()

    def _parse_worker(self, lines: List[str], start: int) -> None:
        try:
            parser = self._get_parser()
            for i, line in enumerate(lines):
                task = self.tasks[start + i]
                self._current_task = task
                self.q.put(("task_status", (task, "parsing", "")))
                try:
                    aweme = parser.parse(line)
                    self.q.put(("parsed", (task, aweme)))
                    self._log(f"✓ {aweme.author_name} · {aweme.title(30)}", "ok")
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    self.q.put(("task_status", (task, "failed", msg)))
                    self._log(f"✗ {line[:40]} → {msg}", "err")
                finally:
                    self._current_task = None
        except Exception:  # noqa: BLE001
            self._log("解析线程异常:\n" + traceback.format_exc(), "err")
        finally:
            self.q.put(("parse_done", None))

    def _on_parse_done(self) -> None:
        self._busy = False
        self.btn_parse.configure(state="normal", text="开始解析 ▶", bg=ACCENT, fg="white")
        self.lbl_step.configure(text="")
        self.q.put(("status", "解析完成"))

    # ------------------------------------------------------------------
    # 下载控制：开始 / 暂停 / 继续 / 取消 / 删除
    # ------------------------------------------------------------------
    def download_task(self, task: Task) -> None:
        if not task.aweme:
            return
        if task.status in ("downloading", "paused"):
            return

        task.control = DownloadControl()
        task.files = []
        task.received = task.total = 0
        task.progress = 0.0
        task._last_t = time.time()
        task._last_bytes = 0
        if task.row and task.row.winfo_exists():
            task.row.prog.configure(value=0)
            task.row.set_status("downloading")
        threading.Thread(target=self._download_worker, args=(task,), daemon=True).start()

    def pause_task(self, task: Task) -> None:
        if task.control and task.status == "downloading":
            task.control.pause()
            if task.row and task.row.winfo_exists():
                task.row.set_status("paused")
            self._log("‖ 已暂停", "warn")

    def resume_task(self, task: Task) -> None:
        if task.control and task.status == "paused":
            task.control.resume()
            task._last_t = time.time()
            task._last_bytes = task.received
            if task.row and task.row.winfo_exists():
                task.row.set_status("downloading")
            self._log("▶ 继续下载", "info")

    def cancel_task(self, task: Task) -> None:
        if task.control:
            task.control.cancel()
            self._log("⊘ 正在取消…", "warn")

    def open_task_file(self, task: Task) -> None:
        if task.files:
            open_in_explorer(task.files[0])
            return
        if task.aweme:
            for rec in self.history.all():
                if rec.aweme_id == task.aweme.aweme_id and rec.primary_file:
                    open_in_explorer(rec.primary_file)
                    return
        self._log("找不到已下载的文件", "warn")

    def delete_task(self, task: Task) -> None:
        """移除任务；若已有文件，问一句要不要一起删。"""
        if task.status in ("downloading", "paused") and task.control:
            task.control.cancel()

        files: List[Path] = list(task.files)
        if not files and task.aweme:
            for rec in self.history.all():
                if rec.aweme_id == task.aweme.aweme_id:
                    files = [Path(f) for f in rec.files]
                    break

        existing = [f for f in files if f.exists()]
        if existing:
            ans = messagebox.askyesnocancel(
                "删除任务",
                f"同时删除已下载的 {len(existing)} 个文件？\n\n"
                "是 = 删文件并移除任务\n"
                "否 = 只移除任务（文件保留）\n"
                "取消 = 什么都不做",
            )
            if ans is None:
                return
            if ans:
                for f in existing:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception as e:  # noqa: BLE001
                        self._log(f"删除失败 {f.name}: {e}", "err")
                for f in existing:
                    try:
                        if f.parent != self.out_dir and f.parent.is_dir() and not any(f.parent.iterdir()):
                            f.parent.rmdir()
                    except Exception:  # noqa: BLE001
                        pass
                if task.aweme:
                    self.history.remove(task.aweme.aweme_id)
                self._log(f"已删除 {len(existing)} 个文件", "ok")

        if task.row is not None and task.row.winfo_exists():
            task.row.destroy()
        task.row = None
        if task in self.tasks:
            self.tasks.remove(task)
        if not self.tasks and not self.task_empty.winfo_ismapped():
            self.task_empty.pack(fill="x")
        self._refresh_task_count()

    def _download_worker(self, task: Task) -> None:
        from douyin_dl.downloader import Downloader

        try:
            aweme = task.aweme
            assert aweme is not None

            dl = Downloader(
                out_dir=self.out_dir,
                show_progress=False,
                rate_limit=self.rate_limit,
                on_progress=lambda name, recv, total: self.q.put(
                    ("progress", (task, recv, total))
                ),
            )
            files = dl.save(aweme, task.selected_media, control=task.control)
            task.files = files
            if task.progress == 0 and files:
                self.q.put(("progress", (task, 1, 1)))

            size = sum(f.stat().st_size for f in files if f.exists())
            self.history.add_from_aweme(
                aweme, files, quality=task.quality_label, url=task.url
            )
            self.q.put(("finish", (task, "done", "")))
            self._log(f"✓ 完成 {aweme.title(24)} · {human_size(size)} · {len(files)} 文件", "ok")
        except DownloadCancelled:
            self.q.put(("finish", (task, "cancelled", "")))
            self._log("⊘ 已取消下载（.part 保留，可续传）", "warn")
        except Exception as e:  # noqa: BLE001
            self.q.put(("finish", (task, "failed", str(e))))
            self._log(f"✗ 下载失败: {e}", "err")

    def _download_all(self) -> None:
        n = 0
        for t in self.tasks:
            if t.status in ("parsed", "failed", "cancelled"):
                self.download_task(t)
                n += 1
                time.sleep(0.05)
        if n == 0:
            self._log("没有可下载的任务", "warn")
        else:
            self._log(f"已提交 {n} 个下载任务", "info")

    def _remove_done(self) -> None:
        keep: List[Task] = []
        for t in self.tasks:
            if t.status in ("done", "skip", "cancelled") and t.row is not None:
                if t.row.winfo_exists():
                    t.row.destroy()
                t.row = None
            else:
                keep.append(t)
        self.tasks = keep
        if not self.tasks and not self.task_empty.winfo_ismapped():
            self.task_empty.pack(fill="x")
        self._refresh_task_count()

    # ------------------------------------------------------------------
    # 下载历史
    # ------------------------------------------------------------------
    def _show_history(self) -> None:
        recs = self.history.all()

        win = tk.Toplevel(self)
        win.title(f"下载历史 · {len(recs)} 条")
        win.configure(bg=BG)
        win.geometry("960x600")
        win.transient(self)

        top = tk.Frame(win, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 8))

        tk.Label(top, text="下载历史", bg=BG, fg=TEXT, font=F_TITLE).pack(side="left")
        self._hist_count = tk.Label(
            top, text=f"{len(recs)} 条", bg=BG, fg=FAINT, font=F_SMALL
        )
        self._hist_count.pack(side="left", padx=10)

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        cols = ("time", "title", "author", "kind", "quality", "size", "state")
        tree = ttk.Treeview(
            body, columns=cols, show="headings", style="Dark.Treeview", selectmode="browse"
        )
        heads = {
            "time": ("时间", 130), "title": ("标题", 320), "author": ("作者", 110),
            "kind": ("类型", 60), "quality": ("清晰度", 100), "size": ("大小", 80),
            "state": ("文件", 60),
        }
        for c in cols:
            label, width = heads[c]
            tree.heading(c, text=label)
            tree.column(c, width=width, anchor="w", stretch=(c == "title"))

        vsb = ttk.Scrollbar(body, orient="vertical", command=tree.yview,
                            style="Dark.Vertical.TScrollbar")
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        rec_map: Dict[str, HistoryRecord] = {}
        for r in recs:
            iid = tree.insert(
                "", "end",
                values=(
                    r.time_text,
                    (r.title[:60] or "(无标题)"),
                    r.author,
                    "图集" if r.kind == "image" else "视频",
                    r.quality,
                    r.size_text,
                    "✓ 在" if r.exists else "✗ 丢失",
                ),
                tags=("ok" if r.exists else "missing",),
            )
            rec_map[iid] = r

        tree.tag_configure("ok", foreground=TEXT)
        tree.tag_configure("missing", foreground=FAINT)

        def selected() -> Optional[HistoryRecord]:
            sel = tree.selection()
            return rec_map.get(sel[0]) if sel else None

        def do_open(_e: Any = None) -> None:
            r = selected()
            if r and r.primary_file:
                open_in_explorer(r.primary_file)

        def do_remove() -> None:
            r = selected()
            if not r:
                return
            if messagebox.askyesno("移除记录", f"从历史里移除这条？\n\n{r.title[:50]}"):
                self.history.remove_record(r)
                tree.delete(tree.selection()[0])
                self._hist_count.configure(text=f"{len(self.history.all())} 条")

        def do_clear() -> None:
            n = len(self.history.all())
            if not n:
                return
            ans = messagebox.askyesnocancel(
                "清空历史",
                f"共 {n} 条记录。\n\n"
                "是 = 清空记录 + 删除所有文件\n"
                "否 = 只清空记录（文件保留）\n"
                "取消 = 不操作",
            )
            if ans is None:
                return
            self.history.clear(delete_files=bool(ans))
            for iid in tree.get_children():
                tree.delete(iid)
            self._hist_count.configure(text="0 条")
            self._log(f"历史已清空（{'含文件' if ans else '仅记录'}）", "ok")

        tree.bind("<Double-1>", do_open)

        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=16, pady=(0, 14))

        def btn(text: str, cmd: Any, bg: str = CARD, fg: str = DIM) -> None:
            tk.Button(
                bar, text=text, command=cmd, bg=bg, fg=fg, font=F_SMALL,
                relief="flat", bd=0, padx=16, pady=6, cursor="hand2",
                activebackground=CARD2, activeforeground=TEXT,
            ).pack(side="left", padx=(0, 8))

        btn("打开文件", do_open)
        btn("在目录中显示", lambda: (selected() and open_in_explorer(
            Path(selected().primary_file).parent  # type: ignore[union-attr]
        )))
        btn("移除记录", do_remove)
        btn("清空历史", do_clear, CARD2, ERR)

        tk.Label(
            bar, text="双击记录可打开文件", bg=BG, fg=FAINT, font=F_SMALL
        ).pack(side="right")

    def _refresh_task_count(self) -> None:
        self.lbl_tasks.configure(text=f"任务 ({len(self.tasks)})")

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        try:
            if self.parser is not None:
                self.parser.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def main() -> int:
    try:
        app = App()
    except Exception:
        # 起不来时把错误写到文件，避免双击闪退什么都看不到
        err_file = ROOT / "gui_error.log"
        err_file.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            import tkinter.messagebox as mb

            mb.showerror("启动失败", f"错误已写入:\n{err_file}")
        except Exception:  # noqa: BLE001
            pass
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
