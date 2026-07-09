#!/usr/bin/env python3
"""
Public SOR Archiver — desktop GUI (CustomTkinter).

Dark, high-contrast UI for scrape / search / analysis / NSOPW.
Double-click run_gui.bat (recommended) or gui.py.
"""

from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk

# ---------------------------------------------------------------------------
# Bootstrap: path + cwd (double-click often starts in System32 / user home)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    os.chdir(_ROOT)
except OSError:
    pass


def _fatal(msg: str) -> None:
    """Show an error even when launched with pythonw (no console)."""
    text = msg[:1800]
    try:
        (_ROOT / "gui_error.log").write_text(msg, encoding="utf-8")
    except OSError:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "SOR Public Archiver", 0x10)
    except Exception:
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass


def _ensure_dependencies() -> None:
    """Install missing packages into *this* interpreter (fixes double-click)."""
    need = []
    for mod, pip_name in (
        ("customtkinter", "customtkinter"),
        ("bs4", "beautifulsoup4"),
        ("requests", "requests"),
        ("curl_cffi", "curl_cffi"),
    ):
        try:
            __import__(mod)
        except ImportError:
            need.append(pip_name)
    if not need:
        return
    req = _ROOT / "requirements.txt"
    cmd = [sys.executable, "-m", "pip", "install", "--user"]
    if req.is_file():
        cmd += ["-r", str(req)]
    else:
        cmd += need
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        _fatal(
            "Missing packages and auto-install failed.\n\n"
            f"Interpreter:\n{sys.executable}\n\n"
            f"Need: {', '.join(need)}\n\n"
            f"{e}\n\n"
            "Open a terminal in this folder and run:\n"
            "  python -m pip install -r requirements.txt\n\n"
            "Or double-click run_gui.bat"
        )
        raise SystemExit(1) from e


_ensure_dependencies()

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox, ttk
except Exception as e:
    _fatal(f"Failed to import GUI libraries:\n\n{e}\n\n{sys.executable}")
    raise SystemExit(1) from e

# ---------------------------------------------------------------------------
# Theme — zinc / slate, warm accent (no blue-on-white)
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")  # base; we override colors below

C = {
    "bg": "#0c0c0e",
    "surface": "#141418",
    "panel": "#1a1a20",
    "elevated": "#22222a",
    "border": "#2e2e38",
    "text": "#ececf1",
    "muted": "#9b9ba8",
    "dim": "#6b6b78",
    "accent": "#e8a87c",       # warm sand
    "accent_hover": "#f0bc98",
    "accent_dim": "#3d2e24",
    "success": "#7dcea0",
    "danger": "#e07a7a",
    "info": "#8ab4c9",
    "row_alt": "#121216",
    "select": "#3d342c",
    "tree_bg": "#101014",
    "tree_fg": "#e8e8ef",
    "tree_head": "#1c1c24",
}

FONT_UI = ("Segoe UI", 12)
FONT_SM = ("Segoe UI", 11)
FONT_BOLD = ("Segoe UI", 12, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_SECTION = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 11)


def _style_treeview(root: ctk.CTk) -> None:
    """Force dark ttk Treeview (Windows otherwise paints blue-on-white)."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(
        "Dark.Treeview",
        background=C["tree_bg"],
        foreground=C["tree_fg"],
        fieldbackground=C["tree_bg"],
        borderwidth=0,
        relief="flat",
        rowheight=28,
        font=FONT_SM,
    )
    style.configure(
        "Dark.Treeview.Heading",
        background=C["tree_head"],
        foreground=C["muted"],
        relief="flat",
        borderwidth=0,
        font=FONT_BOLD,
        padding=6,
    )
    style.map(
        "Dark.Treeview",
        background=[("selected", C["select"])],
        foreground=[("selected", C["text"])],
    )
    style.map(
        "Dark.Treeview.Heading",
        background=[("active", C["elevated"])],
        foreground=[("active", C["accent"])],
    )
    style.configure(
        "Dark.Vertical.TScrollbar",
        background=C["elevated"],
        troughcolor=C["bg"],
        borderwidth=0,
        arrowsize=12,
    )
    style.configure(
        "Dark.Horizontal.TScrollbar",
        background=C["elevated"],
        troughcolor=C["bg"],
        borderwidth=0,
        arrowsize=12,
    )


def _card(parent, **kwargs) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent,
        fg_color=C["panel"],
        border_color=C["border"],
        border_width=1,
        corner_radius=12,
        **kwargs,
    )


def _section_label(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=text,
        font=FONT_SECTION,
        text_color=C["text"],
        anchor="w",
    )


def _muted(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=text,
        font=FONT_SM,
        text_color=C["muted"],
        anchor="w",
        wraplength=900,
        justify="left",
    )


def _tree_frame(parent) -> tuple[ctk.CTkFrame, ttk.Treeview]:
    """Dark treeview inside a card with scrollbars (fills parent; columns stretch)."""
    wrap = ctk.CTkFrame(parent, fg_color=C["tree_bg"], corner_radius=10, border_width=1, border_color=C["border"])
    tree = ttk.Treeview(wrap, style="Dark.Treeview", show="headings")
    vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
    hsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview, style="Dark.Horizontal.TScrollbar")
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    vsb.pack(side="right", fill="y", padx=(0, 4), pady=4)
    hsb.pack(side="bottom", fill="x", padx=4, pady=(0, 4))
    tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    wrap._tree_vsb = vsb  # type: ignore[attr-defined]
    wrap._tree_hsb = hsb  # type: ignore[attr-defined]
    return wrap, tree


def _vpaned(parent) -> tk.PanedWindow:
    """Vertical drag-sash splitter for resizable data panes."""
    return tk.PanedWindow(
        parent,
        orient=tk.VERTICAL,
        sashwidth=6,
        sashrelief=tk.FLAT,
        bg=C["border"],
        bd=0,
        opaqueresize=True,
    )


def _hpaned(parent) -> tk.PanedWindow:
    """Horizontal drag-sash splitter for resizable data panes."""
    return tk.PanedWindow(
        parent,
        orient=tk.HORIZONTAL,
        sashwidth=6,
        sashrelief=tk.FLAT,
        bg=C["border"],
        bd=0,
        opaqueresize=True,
    )


def _stretch_columns(tree: ttk.Treeview, columns: List[str], widths: Optional[List[int]] = None) -> None:
    """Make tree columns user-resizable and stretch with the window."""
    for i, c in enumerate(columns):
        w = widths[i] if widths and i < len(widths) else 120
        tree.column(c, width=w, minwidth=40, stretch=True)


def _format_race_display(race: Optional[str]) -> str:
    """Display race in normal case (not ALL CAPS), e.g. WHITE → White."""
    raw = (race or "").strip()
    if not raw or raw == "—":
        return "—"
    # Keep short codes as-is
    if len(raw) <= 2:
        return raw.upper()
    # Prefer shared formatter when available
    try:
        from scraper.searcher import format_race_label
        return format_race_label(raw)
    except Exception:
        return raw.title()


_PIE_PALETTE = (
    "#e8a87c", "#8ab4c9", "#7dcea0", "#c39bd3", "#f5b7b1",
    "#76d7c4", "#f9e79f", "#aed6f1", "#d7bde2", "#f0b27a",
    "#85c1e9", "#82e0aa", "#f1948a", "#bb8fce", "#5dade2",
)


def _render_bar_chart(
    items: List[tuple],
    *,
    title: str = "",
    width: int = 900,
    height: Optional[int] = None,
    max_bars: int = 12,
    accent: str = "#e8a87c",
    bg: str = "#141418",
    fg: str = "#ececf1",
    muted: str = "#9b9ba8",
    bar_color: Optional[str] = None,
) -> Any:
    """Horizontal bar chart (Pillow) — used for integrity multi-state view."""
    from PIL import Image, ImageDraw, ImageFont

    bar_color = bar_color or accent
    data = [(str(l), int(v)) for l, v in list(items)[:max_bars]]
    width = max(640, int(width))
    n = max(1, len(data))
    row_h = 26 if n > 12 else 30
    pad_t = 34 if title else 12
    pad_b = 14
    if height is None:
        height = pad_t + pad_b + n * row_h
    height = max(height, pad_t + pad_b + max(n, 4) * row_h)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    try:
        font_sm = ImageFont.truetype("segoeui.ttf", 12)
        font_title = ImageFont.truetype("segoeui.ttf", 14)
    except Exception:
        font_sm = ImageFont.load_default()
        font_title = font_sm

    def _text_w(text: str, font) -> int:
        try:
            return int(draw.textlength(text, font=font))
        except Exception:
            box = draw.textbbox((0, 0), text, font=font)
            return int(box[2] - box[0])

    pad_l, pad_r = 14, 14
    if title:
        draw.text((pad_l, 8), title, fill=fg, font=font_title)

    if not data:
        draw.text((pad_l, height // 2 - 6), "No data — run Analyze", fill=muted, font=font_sm)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(width, height))

    max_v = max(v for _l, v in data) or 1
    label_w = max(_text_w(lab, font_sm) for lab, _ in data) + 12
    label_w = min(max(label_w, 100), max(120, width // 3))
    count_w = max(_text_w(str(max_v), font_sm), 28) + 8
    chart_x0 = pad_l + label_w
    chart_x1 = width - pad_r - count_w
    chart_w = max(60, chart_x1 - chart_x0)
    bar_h = 16

    for i, (lab, val) in enumerate(data):
        y = pad_t + i * row_h
        draw.text((pad_l, y + 2), lab, fill=muted, font=font_sm)
        bw = int(chart_w * (val / max_v))
        x1 = chart_x0 + max(3, bw)
        draw.rounded_rectangle(
            [chart_x0, y + 2, x1, y + 2 + bar_h],
            radius=4,
            fill=bar_color,
        )
        draw.text((x1 + 8, y + 2), str(val), fill=fg, font=font_sm)

    return ctk.CTkImage(light_image=img, dark_image=img, size=(width, height))


def _render_pie_chart(
    items: List[tuple],
    *,
    title: str = "",
    width: int = 360,
    height: int = 320,
    max_slices: int = 8,
    bg: str = "#141418",
    fg: str = "#ececf1",
    muted: str = "#9b9ba8",
    accent: str = "#e8a87c",
    legend_below: bool = True,
) -> Any:
    """
    Circle (pie) chart with full legend labels (Pillow).
    legend_below=True packs legend under the pie (good for side-by-side charts).
    """
    from PIL import Image, ImageDraw, ImageFont

    raw = [(str(l), max(0, int(v))) for l, v in items if int(v) > 0]
    raw.sort(key=lambda t: -t[1])
    if len(raw) > max_slices:
        head = raw[: max_slices - 1]
        other = sum(v for _l, v in raw[max_slices - 1 :])
        raw = head + ([("Other", other)] if other else [])

    width = max(260, int(width))
    n_leg = max(len(raw), 1)
    line_h = 18
    title_h = 28 if title else 8
    pie_size = min(160, width - 24)
    if legend_below:
        height = max(height, title_h + pie_size + 16 + n_leg * line_h + 16)
    else:
        height = max(height, title_h + max(pie_size, n_leg * line_h) + 20)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    try:
        font_sm = ImageFont.truetype("segoeui.ttf", 11)
        font_title = ImageFont.truetype("segoeui.ttf", 13)
    except Exception:
        font_sm = ImageFont.load_default()
        font_title = font_sm

    pad = 10
    if title:
        draw.text((pad, 6), title, fill=fg, font=font_title)

    if not raw:
        draw.text((pad, height // 2 - 6), "No data — run Analyze", fill=muted, font=font_sm)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(width, height))

    total = sum(v for _l, v in raw) or 1
    top = title_h
    if legend_below:
        cx = width // 2
        cy = top + pie_size // 2 + 4
    else:
        cx = pad + pie_size // 2 + 4
        cy = top + pie_size // 2 + 4
    bbox = [cx - pie_size // 2, cy - pie_size // 2, cx + pie_size // 2, cy + pie_size // 2]

    start = -90.0
    for i, (_lab, val) in enumerate(raw):
        extent = 360.0 * (val / total)
        color = _PIE_PALETTE[i % len(_PIE_PALETTE)]
        if extent >= 360:
            draw.ellipse(bbox, fill=color)
        elif extent > 0.15:
            draw.pieslice(bbox, start=start, end=start + extent, fill=color)
        start += extent
    draw.ellipse(bbox, outline="#2e2e38", width=2)

    sw = 11
    if legend_below:
        legend_x = pad
        legend_y = cy + pie_size // 2 + 10
    else:
        legend_x = cx + pie_size // 2 + 16
        legend_y = top + 2

    for i, (lab, val) in enumerate(raw):
        color = _PIE_PALETTE[i % len(_PIE_PALETTE)]
        y = legend_y + i * line_h
        if y + line_h > height - 4:
            break
        draw.rounded_rectangle([legend_x, y + 2, legend_x + sw, y + 2 + sw], radius=2, fill=color)
        pct = 100.0 * val / total
        text = f"{lab}  ·  {val}  ({pct:.1f}%)"
        draw.text((legend_x + sw + 6, y), text, fill=fg, font=font_sm)

    return ctk.CTkImage(light_image=img, dark_image=img, size=(width, height))


def _wire_wide_scroll(tab, scroll_frame) -> None:
    """
    Expand mouse-wheel capture to the whole tab (including margins) and
    pin the scrollbar to the far right edge of the tab.
    """
    try:
        canvas = scroll_frame._parent_canvas  # type: ignore[attr-defined]
        parent_frame = scroll_frame._parent_frame  # type: ignore[attr-defined]
        scrollbar = scroll_frame._scrollbar  # type: ignore[attr-defined]
    except Exception:
        return

    def _wheel(event):
        delta = getattr(event, "delta", 0) or 0
        if delta:
            steps = int(-1 * (delta / 120)) if abs(delta) >= 120 else int(-1 * delta)
            if steps == 0:
                steps = -1 if delta > 0 else 1
            canvas.yview_scroll(steps, "units")
        else:
            num = getattr(event, "num", 0)
            if num == 4:
                canvas.yview_scroll(-3, "units")
            elif num == 5:
                canvas.yview_scroll(3, "units")
        return "break"

    # Capture wheel anywhere on the statistics tab (not only over content)
    for w in (tab, parent_frame, canvas, scroll_frame):
        try:
            w.bind("<MouseWheel>", _wheel, add="+")
            w.bind("<Button-4>", _wheel, add="+")
            w.bind("<Button-5>", _wheel, add="+")
        except Exception:
            pass

    # Scrollbar flush right — remove CTk corner inset padding
    try:
        canvas.grid_configure(padx=(0, 0), pady=0)
        scrollbar.grid_configure(padx=(2, 0), pady=0, sticky="ns")
        parent_frame.grid_columnconfigure(0, weight=1)
        parent_frame.grid_columnconfigure(1, weight=0, minsize=14)
    except Exception:
        pass


def _bind_tree_scroll_isolation(tree: ttk.Treeview, wrap: ctk.CTkFrame) -> None:
    """
    When the pointer is over the inserts tree, wheel scrolls only the tree —
    not a parent CTkScrollableFrame (which uses bind_all MouseWheel).
    """
    def _on_wheel(event):
        delta = getattr(event, "delta", 0) or 0
        if delta:
            # Windows / macOS
            steps = int(-1 * (delta / 120)) if abs(delta) >= 120 else int(-1 * delta)
            if steps == 0:
                steps = -1 if delta > 0 else 1
            tree.yview_scroll(steps, "units")
        else:
            # Linux Button-4/5
            num = getattr(event, "num", 0)
            if num == 4:
                tree.yview_scroll(-3, "units")
            elif num == 5:
                tree.yview_scroll(3, "units")
        return "break"

    targets = [tree, wrap]
    vsb = getattr(wrap, "_tree_vsb", None)
    hsb = getattr(wrap, "_tree_hsb", None)
    if vsb is not None:
        targets.append(vsb)
    if hsb is not None:
        targets.append(hsb)
    for w in targets:
        w.bind("<MouseWheel>", _on_wheel)
        w.bind("<Button-4>", _on_wheel)
        w.bind("<Button-5>", _on_wheel)


def _enable_tree_column_sort(
    tree: ttk.Treeview,
    columns: List[str],
    labels: Optional[Dict[str, str]] = None,
) -> None:
    """Click column headers to sort ascending/descending (toggle)."""
    labels = labels or {c: c.upper() for c in columns}
    state: Dict[str, Any] = {"col": None, "reverse": False}

    def _sort_key(val: str):
        s = (val or "").strip()
        # numeric-ish first for mixed columns
        try:
            return (0, float(s.replace(",", "")))
        except ValueError:
            return (1, s.casefold())

    def apply_sort(col: str, reverse: bool, update_headings: bool = True) -> None:
        rows = [(tree.set(iid, col), iid) for iid in tree.get_children("")]
        rows.sort(key=lambda t: _sort_key(t[0]), reverse=reverse)
        for idx, (_val, iid) in enumerate(rows):
            tree.move(iid, "", idx)
        state["col"] = col
        state["reverse"] = reverse
        tree._sort_state = state  # type: ignore[attr-defined]
        if update_headings:
            for c in columns:
                base = labels.get(c, c.upper())
                if c == col:
                    arrow = " ▼" if reverse else " ▲"
                    tree.heading(
                        c,
                        text=base + arrow,
                        command=lambda cc=c: on_heading(cc),
                    )
                else:
                    tree.heading(c, text=base, command=lambda cc=c: on_heading(cc))

    def on_heading(col: str) -> None:
        reverse = state["col"] == col and not state["reverse"]
        apply_sort(col, reverse)

    def reapply() -> None:
        col = state.get("col")
        if col:
            apply_sort(col, bool(state.get("reverse")), update_headings=False)

    tree._sort_state = state  # type: ignore[attr-defined]
    tree._reapply_sort = reapply  # type: ignore[attr-defined]
    for c in columns:
        tree.heading(c, text=labels.get(c, c.upper()), command=lambda cc=c: on_heading(cc))


# ===========================================================================
# App
# ===========================================================================
class ArchiverApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SOR Public Archiver")
        self.geometry("1280x820")
        self.minsize(980, 640)
        self.configure(fg_color=C["bg"])

        _style_treeview(self)

        # State
        self.sources: list = []
        self.selected_states: set = set()
        self.log_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self._nsopw_cancel = False
        self._misclass_results: list = []
        self._closing = False
        # NSOPW options snapshot (main thread writes; worker reads under lock)
        self._nsopw_runtime_lock = threading.Lock()
        self._nsopw_runtime: Dict[str, Any] = {}

        # Persistent settings (DB path, backups, NSOPW compact search)
        from scraper.app_settings import load_settings

        self.app_settings = load_settings()
        self.db_path = str(self.app_settings.get("db_path") or "data/offenders.db")

        self._build()
        self._load_sources()
        self._poll_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -----------------------------------------------------------------------
    # Shell
    # -----------------------------------------------------------------------
    def _build(self):
        # Compact header (was 64px + large padding → wasted top space)
        header = ctk.CTkFrame(self, fg_color=C["surface"], height=44, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="SOR Public Archiver",
            font=FONT_TITLE,
            text_color=C["text"],
        ).pack(side="left", padx=14, pady=8)

        # DB path indicator (high priority: avoid empty-DB confusion)
        db_row = ctk.CTkFrame(header, fg_color="transparent")
        db_row.pack(side="left", padx=(8, 0), fill="y")
        self.header_db_label = ctk.CTkLabel(
            db_row,
            text="",
            font=FONT_SM,
            text_color=C["muted"],
            anchor="w",
        )
        self.header_db_label.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            db_row, text="Open data", width=88, height=28,
            command=self._open_data_folder_header,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        self.stats_label = ctk.CTkLabel(
            header,
            text="Ready",
            font=FONT_SM,
            text_color=C["accent"],
        )
        self.stats_label.pack(side="right", padx=14)
        self.after(50, self._refresh_header_db_path)

        # Body: tabs always; Activity log only on NSOPW / Scrape
        body = ctk.CTkFrame(self, fg_color=C["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=(4, 6))

        main_split = _vpaned(body)
        main_split.pack(fill="both", expand=True)

        tabs_host = ctk.CTkFrame(main_split, fg_color=C["bg"], corner_radius=0)
        log_host = ctk.CTkFrame(main_split, fg_color=C["bg"], corner_radius=0)
        main_split.add(tabs_host, minsize=280, stretch="always")
        # Log pane added only when NSOPW/Scrape is active
        self._main_split = main_split
        self._tabs_host = tabs_host
        self._log_host = log_host
        self._log_visible = False

        self.tabs = ctk.CTkTabview(
            tabs_host,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["select"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["panel"],
            text_color=C["text"],
            text_color_disabled=C["dim"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
            command=self._on_main_tab_change,
        )
        self.tabs.pack(fill="both", expand=True)

        # Primary: Browse (search + integrity + misclassify). Settings last.
        for name in ("Browse", "NSOPW", "Scrape", "Settings"):
            self.tabs.add(name)

        self._build_browse(self.tabs.tab("Browse"))
        self._build_nsopw(self.tabs.tab("NSOPW"))
        self._build_scrape(self.tabs.tab("Scrape"))
        self._build_settings(self.tabs.tab("Settings"))
        try:
            self.tabs.set("Browse")
        except Exception:
            pass

        # Log (shown only on NSOPW / Scrape via _on_main_tab_change)
        log_card = _card(log_host)
        log_card.pack(fill="both", expand=True, padx=0, pady=(4, 0))
        ctk.CTkLabel(
            log_card, text="Activity  ·  shown on NSOPW & Scrape · drag sash to resize",
            font=FONT_BOLD, text_color=C["muted"], anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 4))
        self.log_text = ctk.CTkTextbox(
            log_card,
            height=100,
            font=FONT_MONO,
            fg_color=C["bg"],
            text_color=C["muted"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
            activate_scrollbars=True,
        )
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")

        self.after(80, self._on_main_tab_change)

    def _on_main_tab_change(self, _name: Optional[str] = None):
        """Show Activity log only on NSOPW and Scrape tabs."""
        try:
            name = _name or self.tabs.get()
        except Exception:
            name = "Browse"
        want = name in ("NSOPW", "Scrape")
        # Refresh settings status when opening the tab
        if name == "Settings" and hasattr(self, "_settings_refresh_status"):
            try:
                self._settings_refresh_status()
            except Exception:
                pass
        if want and not self._log_visible:
            try:
                self._main_split.add(self._log_host, minsize=100, stretch="never")
                self._log_visible = True
                self.after(60, lambda: self._set_sash(self._main_split, 0, 0.78))
            except Exception:
                pass
        elif not want and self._log_visible:
            try:
                self._main_split.forget(self._log_host)
            except Exception:
                try:
                    self._main_split.remove(self._log_host)
                except Exception:
                    pass
            self._log_visible = False

    @staticmethod
    def _set_sash(paned: tk.PanedWindow, index: int, fraction: float) -> None:
        """Place a sash at a fraction of the paned widget size."""
        try:
            paned.update_idletasks()
            orient = str(paned.cget("orient"))
            if orient == tk.VERTICAL or orient == "vertical":
                total = paned.winfo_height()
            else:
                total = paned.winfo_width()
            if total > 40:
                paned.sash_place(index, 0 if orient in (tk.VERTICAL, "vertical") else int(total * fraction),
                                 int(total * fraction) if orient in (tk.VERTICAL, "vertical") else 0)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Browse (Search + Integrity + Misclassify + Statistics)
    # -----------------------------------------------------------------------
    def _build_browse(self, tab):
        """Primary tab: search, integrity, misclassification, deep stats."""
        tab.configure(fg_color=C["surface"])
        sub = ctk.CTkTabview(
            tab,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["select"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["panel"],
            text_color=C["text"],
            corner_radius=10,
            border_width=0,
        )
        sub.pack(fill="both", expand=True, padx=6, pady=6)
        self.browse_tabs = sub
        for name in ("Search", "Integrity", "Misclassify", "Statistics"):
            sub.add(name)
        self._build_search(sub.tab("Search"))
        self._build_integrity(sub.tab("Integrity"))
        self._build_misclass(sub.tab("Misclassify"))
        self._build_misclass_statistics(sub.tab("Statistics"))
        try:
            sub.set("Search")
        except Exception:
            pass

    # ---- Shared detail drawer (photo + fields + open HTML/URL) ----
    def _make_detail_drawer(self, parent) -> ctk.CTkFrame:
        """Right-side detail card used by Search and (optionally) other tables."""
        card = _card(parent)
        _section_label(card, "Detail").pack(anchor="w", padx=12, pady=(12, 4))
        photo = ctk.CTkLabel(
            card,
            text="Select a row",
            font=FONT_SM,
            text_color=C["dim"],
            width=180,
            height=180,
            fg_color=C["tree_bg"],
            corner_radius=8,
        )
        photo.pack(padx=12, pady=(0, 6))
        # Stable host: empty label (no scrollbar) OR textbox when a row is selected
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        empty = ctk.CTkLabel(
            content,
            text="Select a result to view photo, crime, race, and links.",
            font=FONT_SM,
            text_color=C["dim"],
            anchor="nw",
            justify="left",
            wraplength=220,
        )
        empty.pack(fill="x", anchor="nw")
        body = ctk.CTkTextbox(
            content,
            height=200,
            font=FONT_SM,
            fg_color=C["bg"],
            text_color=C["text"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
            activate_scrollbars=True,
            wrap="word",
        )
        # Not packed until a row is selected (avoids empty scrollbar chrome)
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))
        open_html = ctk.CTkButton(
            btns, text="Open HTML", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        open_html.pack(side="left", padx=(0, 6))
        open_url = ctk.CTkButton(
            btns, text="Open URL", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        open_url.pack(side="left", padx=(0, 6))
        open_photo = ctk.CTkButton(
            btns, text="Open photo", width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        )
        open_photo.pack(side="left")
        card._detail_photo = photo  # type: ignore[attr-defined]
        card._detail_content = content  # type: ignore[attr-defined]
        card._detail_empty = empty  # type: ignore[attr-defined]
        card._detail_body = body  # type: ignore[attr-defined]
        card._detail_open_html = open_html  # type: ignore[attr-defined]
        card._detail_open_url = open_url  # type: ignore[attr-defined]
        card._detail_open_photo = open_photo  # type: ignore[attr-defined]
        card._detail_image_ref = None  # type: ignore[attr-defined]
        card._detail_record = None  # type: ignore[attr-defined]
        card._detail_body_packed = False  # type: ignore[attr-defined]
        return card

    @staticmethod
    def _detail_set_body_visible(drawer: ctk.CTkFrame, show_body: bool) -> None:
        """Show textbox (with content) or empty label (no scrollbar)."""
        empty = getattr(drawer, "_detail_empty", None)
        body = getattr(drawer, "_detail_body", None)
        if empty is None or body is None:
            return
        packed = bool(getattr(drawer, "_detail_body_packed", False))
        if show_body and not packed:
            try:
                empty.pack_forget()
            except Exception:
                pass
            body.pack(fill="both", expand=True)
            drawer._detail_body_packed = True  # type: ignore[attr-defined]
        elif not show_body and packed:
            try:
                body.pack_forget()
            except Exception:
                pass
            empty.pack(fill="x", anchor="nw")
            drawer._detail_body_packed = False  # type: ignore[attr-defined]
        elif not show_body and not packed:
            try:
                empty.pack(fill="x", anchor="nw")
            except Exception:
                pass

    @staticmethod
    def _detail_hide_unneeded_scrollbars(body: ctk.CTkTextbox) -> None:
        """Force-hide CTkTextbox scrollbars when content fully fits."""
        try:
            body.update_idletasks()
            tb = getattr(body, "_textbox", None)
            if tb is None:
                return
            y0, y1 = tb.yview()
            x0, x1 = tb.xview()
            hide_y = (y1 - y0) >= 0.999 or (y0, y1) == (0.0, 1.0)
            hide_x = (x1 - x0) >= 0.999 or (x0, x1) == (0.0, 1.0)
            body._hide_y_scrollbar = hide_y  # type: ignore[attr-defined]
            body._hide_x_scrollbar = hide_x  # type: ignore[attr-defined]
            body._create_grid_for_text_and_scrollbars(  # type: ignore[attr-defined]
                re_grid_x_scrollbar=True, re_grid_y_scrollbar=True
            )
        except Exception:
            pass

    @staticmethod
    def _clear_label_image(photo_lbl, drawer: Optional[ctk.CTkFrame] = None) -> None:
        """Detach a CTk/Tk image from a label without leaving a dangling image name.

        CustomTkinter + Tk can raise ``TclError: image "pyimageN" doesn't exist``
        on a later configure() if the PhotoImage is GC'd while the label still
        references it. Clear the image *before* dropping the Python ref.
        """
        # Keep local ref so GC cannot race mid-clear
        old_ref = None
        if drawer is not None:
            old_ref = getattr(drawer, "_detail_image_ref", None)
        try:
            # Empty string is the reliable Tk way to clear -image
            photo_lbl.configure(image="")
        except Exception:
            try:
                inner = getattr(photo_lbl, "_label", None)
                if inner is not None:
                    inner.configure(image="")
            except Exception:
                pass
        if drawer is not None:
            try:
                drawer._detail_image_ref = None  # type: ignore[attr-defined]
            except Exception:
                pass
        # Drop after Tk no longer names it
        del old_ref

    def _fill_detail_drawer(self, drawer: ctk.CTkFrame, record: Optional[Dict[str, Any]]) -> None:
        """Populate a detail drawer from an offender record dict."""
        photo_lbl = drawer._detail_photo  # type: ignore[attr-defined]
        body = drawer._detail_body  # type: ignore[attr-defined]
        btn_html = drawer._detail_open_html  # type: ignore[attr-defined]
        btn_url = drawer._detail_open_url  # type: ignore[attr-defined]
        btn_photo = drawer._detail_open_photo  # type: ignore[attr-defined]
        drawer._detail_record = record  # type: ignore[attr-defined]

        def _clear_photo(placeholder: str = "No photo") -> None:
            self._clear_label_image(photo_lbl, drawer)
            try:
                photo_lbl.configure(text=placeholder)
            except Exception:
                pass

        if not record:
            _clear_photo("Select a row")
            self._detail_set_body_visible(drawer, False)
            empty = getattr(drawer, "_detail_empty", None)
            if empty is not None:
                try:
                    empty.configure(text="Select a result to view details.")
                except Exception:
                    pass
            try:
                body.configure(state="normal")
                body.delete("1.0", "end")
                body.configure(state="disabled")
            except Exception:
                pass
            try:
                btn_html.configure(state="disabled", command=None)
                btn_url.configure(state="disabled", command=None)
                btn_photo.configure(state="disabled", command=None)
            except Exception:
                pass
            return

        name = (
            (record.get("full_name") or "").strip()
            or f"{record.get('first_name') or ''} {record.get('last_name') or ''}".strip()
            or "—"
        )
        crime = (
            record.get("crime")
            or record.get("offense_description")
            or record.get("offense_type")
            or "—"
        )
        lines = [
            f"Name: {name}",
            f"Race: {_format_race_display(record.get('race'))}",
            f"Ethnicity: {record.get('ethnicity') or '—'}",
            f"Gender: {record.get('gender') or '—'}",
            f"Age / DOB: {record.get('age') or '—'} / {record.get('date_of_birth') or '—'}",
            f"State: {record.get('state') or record.get('source_state') or '—'}",
            f"County / City: {record.get('county') or '—'} / {record.get('city') or '—'}",
            f"Address: {record.get('address') or '—'}",
            f"Crime: {crime}",
            f"Risk: {record.get('risk_level') or '—'}",
            f"Likely ethnicity (name): {record.get('likely_ethnicity') or '—'}",
            f"Photo: {record.get('photo_path') or record.get('photo_url') or '—'}",
            f"HTML: {record.get('report_html_path') or '—'}",
            f"URL: {record.get('source_url') or '—'}",
        ]
        self._detail_set_body_visible(drawer, True)
        body.configure(state="normal")
        body.delete("1.0", "end")
        body.insert("1.0", "\n".join(lines))
        body.configure(state="disabled")
        self.after(30, lambda b=body: self._detail_hide_unneeded_scrollbars(b))

        photo_path = (record.get("photo_path") or "").strip()
        if photo_path and Path(photo_path).is_file():
            try:
                from PIL import Image

                # Clear previous image before assigning a new one
                self._clear_label_image(photo_lbl, drawer)
                img = Image.open(photo_path)
                img.thumbnail((200, 240))
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                drawer._detail_image_ref = ctk_img  # type: ignore[attr-defined]
                photo_lbl.configure(image=ctk_img, text="")
            except Exception:
                _clear_photo("Photo error")
        else:
            _clear_photo()

        html_path = (record.get("report_html_path") or "").strip()
        url = (record.get("source_url") or "").strip()

        def _open_html():
            if html_path and Path(html_path).exists():
                self._open_path(Path(html_path))

        def _open_url():
            if url:
                try:
                    webbrowser.open(url)
                except Exception as e:
                    messagebox.showerror("Open URL", str(e))

        def _open_photo():
            if photo_path and Path(photo_path).is_file():
                self._open_path(Path(photo_path))

        btn_html.configure(
            state="normal" if html_path and Path(html_path).exists() else "disabled",
            command=_open_html,
        )
        btn_url.configure(state="normal" if url else "disabled", command=_open_url)
        btn_photo.configure(
            state="normal" if photo_path and Path(photo_path).is_file() else "disabled",
            command=_open_photo,
        )

    # -----------------------------------------------------------------------
    # Scrape
    # -----------------------------------------------------------------------
    def _build_scrape(self, tab):
        tab.configure(fg_color=C["surface"])
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 6))

        self.scrape_direct_only = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            top,
            text="Direct / bulk only",
            variable=self.scrape_direct_only,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            top, text="Select all", width=100, command=self._scrape_select_all,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Clear", width=80, command=self._scrape_clear_selection,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)

        mid_split = _hpaned(tab)
        mid_split.pack(fill="both", expand=True, padx=12, pady=6)
        self._scrape_split = mid_split

        left = _card(mid_split)
        right = _card(mid_split)
        mid_split.add(left, minsize=320, stretch="always")
        mid_split.add(right, minsize=220, stretch="never")
        self.after(150, lambda: self._set_sash(mid_split, 0, 0.72))

        _section_label(left, "Jurisdictions").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            left,
            "INTERACTIVE: the site only offers a web search form (often disclaimer, CAPTCHA, "
            "or session). There is no public bulk download, so automated scrape returns no "
            "records — look up offenders in a browser. Prefer Direct / bulk only for "
            "jurisdictions that publish downloadable data (DIRECT, ARCGIS, etc.).",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        tree_wrap, self.scrape_tree = _tree_frame(left)
        tree_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.scrape_tree.configure(columns=("abbr", "method", "notes"), show="tree headings", selectmode="extended")
        self.scrape_tree.heading("#0", text="Jurisdiction")
        self.scrape_tree.heading("abbr", text="Code")
        self.scrape_tree.heading("method", text="Method")
        self.scrape_tree.heading("notes", text="Notes")
        self.scrape_tree.column("#0", width=220, minwidth=80, stretch=True)
        self.scrape_tree.column("abbr", width=50, anchor="center", minwidth=40, stretch=False)
        self.scrape_tree.column("method", width=90, anchor="center", minwidth=60, stretch=False)
        self.scrape_tree.column("notes", width=280, minwidth=80, stretch=True)
        self.scrape_tree.bind("<<TreeviewSelect>>", self._scrape_on_select)
        self.scrape_tree.tag_configure("direct", background="#1a241c")
        _bind_tree_scroll_isolation(self.scrape_tree, tree_wrap)

        _section_label(right, "Options").pack(anchor="w", padx=14, pady=(12, 8))

        ctk.CTkLabel(right, text="Output folder", font=FONT_SM, text_color=C["muted"]).pack(
            anchor="w", padx=14
        )
        out_row = ctk.CTkFrame(right, fg_color="transparent")
        out_row.pack(fill="x", padx=14, pady=4)
        self.scrape_output_var = ctk.StringVar(value=str(Path("data/downloads")))
        ctk.CTkEntry(
            out_row, textvariable=self.scrape_output_var, fg_color=C["bg"],
            border_color=C["border"], text_color=C["text"],
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            out_row, text="…", width=36, command=self._scrape_browse_output,
            fg_color=C["elevated"], hover_color=C["border"],
        ).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(right, text="Delay (seconds)", font=FONT_SM, text_color=C["muted"]).pack(
            anchor="w", padx=14, pady=(12, 0)
        )
        self.scrape_delay_var = ctk.DoubleVar(value=2.0)
        ctk.CTkSlider(
            right, from_=0.5, to=10.0, variable=self.scrape_delay_var,
            progress_color=C["accent"], button_color=C["accent"],
            button_hover_color=C["accent_hover"], fg_color=C["elevated"],
        ).pack(fill="x", padx=14, pady=8)

        self.scrape_btn = ctk.CTkButton(
            right,
            text="Start scraping",
            font=FONT_BOLD,
            height=42,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=self._start_scrape,
        )
        self.scrape_btn.pack(fill="x", padx=14, pady=(16, 6))

        ctk.CTkButton(
            right, text="Open output folder", height=36,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._open_output_folder,
        ).pack(fill="x", padx=14, pady=4)

        ctk.CTkLabel(
            right, text="Import to database", font=FONT_BOLD, text_color=C["muted"],
        ).pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            right,
            "Scraped rows must be in the SQLite DB for Search, Integrity, and Misclassify. "
            "Auto-import after scrape is on by default; you can also load CSVs manually.",
        ).pack(anchor="w", padx=14, pady=(0, 6))
        self.scrape_auto_import = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            right, text="Import scrape results into DB (for Misclassify)",
            variable=self.scrape_auto_import, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=2)
        self.scrape_import_skip = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            right, text="Skip existing source URLs",
            variable=self.scrape_import_skip, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=2)
        ctk.CTkButton(
            right, text="Import folder → DB", height=36,
            command=self._import_downloads_folder,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(fill="x", padx=14, pady=(8, 4))
        ctk.CTkButton(
            right, text="Import CSV file…", height=32,
            command=self._import_csv_file,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(fill="x", padx=14, pady=4)
        self.scrape_import_status = ctk.CTkLabel(
            right, text="", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.scrape_import_status.pack(fill="x", padx=14, pady=(4, 8))

        self.scrape_progress = ctk.CTkProgressBar(
            right, progress_color=C["accent"], fg_color=C["elevated"], height=8
        )
        self.scrape_progress.pack(fill="x", padx=14, pady=(8, 16))
        self.scrape_progress.set(0)

    def _scrape_select_all(self):
        for item in self.scrape_tree.get_children():
            if self.scrape_direct_only.get():
                if "direct" not in self.scrape_tree.item(item, "tags"):
                    continue
            self.scrape_tree.selection_add(item)
        self._update_scrape_selection()

    def _scrape_clear_selection(self):
        self.scrape_tree.selection_remove(*self.scrape_tree.selection())
        self._update_scrape_selection()

    def _scrape_on_select(self, _event=None):
        self._update_scrape_selection()

    def _update_scrape_selection(self):
        self.selected_states.clear()
        for item in self.scrape_tree.selection():
            vals = self.scrape_tree.item(item, "values")
            if vals:
                self.selected_states.add(vals[0])
        self.stats_label.configure(text=f"{len(self.selected_states)} selected")

    def _scrape_browse_output(self):
        folder = filedialog.askdirectory(initialdir=self.scrape_output_var.get())
        if folder:
            self.scrape_output_var.set(folder)

    def _start_scrape(self):
        if self.is_running:
            return
        from scraper.config import REGISTRIES, get_registry_by_abbr
        from scraper.scrapers.base import ScraperFactory

        states = list(self.selected_states)
        delay = float(self.scrape_delay_var.get())
        direct_only = bool(self.scrape_direct_only.get())

        if states:
            registries = [get_registry_by_abbr(s) for s in states]
            registries = [r for r in registries if r]
            if direct_only:
                registries = [r for r in registries if r.direct_downloads]
        elif direct_only:
            registries = [r for r in REGISTRIES if r.abbr != "US" and r.direct_downloads]
        else:
            messagebox.showwarning(
                "No selection",
                "Select jurisdictions or enable Direct / bulk only.",
            )
            return
        if not registries:
            messagebox.showwarning("No targets", "No matching registries.")
            return

        output_dir = Path(self.scrape_output_var.get())
        output_dir.mkdir(parents=True, exist_ok=True)
        auto_import = bool(self.scrape_auto_import.get()) if hasattr(self, "scrape_auto_import") else True
        skip_urls = bool(self.scrape_import_skip.get()) if hasattr(self, "scrape_import_skip") else True
        db_path = self.db_path
        self._set_running(True)
        self.scrape_progress.set(0)
        total = len(registries)

        def log(msg):
            self.log_queue.put(msg)

        def worker():
            from scraper.database import Database

            try:
                total_records = 0
                total_imported = 0
                total_skipped = 0
                for i, reg in enumerate(registries):
                    log(f"[{reg.abbr}] Scraping {reg.name}…")
                    scraper = ScraperFactory.create(reg.abbr, delay=delay)
                    try:
                        records = scraper.scrape()
                    finally:
                        scraper.close()
                    if records:
                        csv_path = output_dir / f"{reg.abbr.lower()}_offenders.csv"
                        fields: List[str] = []
                        seen = set()
                        for rec in records:
                            for k in rec:
                                if k not in seen:
                                    seen.add(k)
                                    fields.append(k)
                        with open(csv_path, "w", newline="", encoding="utf-8") as f:
                            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                            w.writeheader()
                            w.writerows(records)
                        log(f"  Saved {len(records)} → {csv_path}")
                        total_records += len(records)
                        if auto_import:
                            try:
                                db = Database(db_path)
                                try:
                                    imp = db.import_records(
                                        records,
                                        state=reg.abbr,
                                        skip_existing_urls=skip_urls,
                                    )
                                finally:
                                    db.close()
                                total_imported += int(imp.get("imported") or 0)
                                total_skipped += int(imp.get("skipped") or 0)
                                log(
                                    f"  DB import: +{imp.get('imported', 0)} "
                                    f"(skipped {imp.get('skipped', 0)})"
                                )
                            except Exception as ie:
                                log(f"  DB import error: {ie}")
                    else:
                        log("  No records")
                    pct = (i + 1) / max(total, 1)
                    self.after(0, lambda p=pct: self.scrape_progress.set(p))
                log(
                    f"Done. Scraped {total_records}"
                    + (
                        f" · DB imported {total_imported} (skipped {total_skipped})"
                        if auto_import
                        else " · (DB auto-import off — use Import for Misclassify)"
                    )
                )
                if auto_import and total_imported:
                    self.after(0, self._after_db_data_changed)
            except Exception as e:
                log(f"ERROR: {e}")
            finally:
                self.after(0, lambda: self._set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    def _import_downloads_folder(self):
        from scraper.database import Database

        folder = self.scrape_output_var.get() or "data/downloads"
        if not Path(folder).is_dir():
            messagebox.showwarning("Missing folder", f"Not a directory: {folder}")
            return
        skip = bool(self.scrape_import_skip.get())
        try:
            db = Database(self.db_path)
            try:
                summary = db.import_csv_directory(folder, skip_existing_urls=skip)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        msg = (
            f"Files: {summary['files']} · imported {summary['imported']} · "
            f"skipped {summary['skipped']} · rows {summary['total_rows']}"
        )
        if summary.get("errors"):
            msg += f" · errors: {len(summary['errors'])}"
        self.scrape_import_status.configure(text=msg)
        self.log_queue.put(f"CSV import folder: {msg}")
        for err in summary.get("errors") or []:
            self.log_queue.put(f"  import error: {err}")
        self._after_db_data_changed()

    def _import_csv_file(self):
        from scraper.database import Database

        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialdir=self.scrape_output_var.get() or "data/downloads",
        )
        if not path:
            return
        skip = bool(self.scrape_import_skip.get())
        try:
            db = Database(self.db_path)
            try:
                result = db.import_csv(path, skip_existing_urls=skip)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        msg = (
            f"{Path(path).name}: imported {result['imported']} · "
            f"skipped {result['skipped']} · rows {result['total_rows']}"
        )
        self.scrape_import_status.configure(text=msg)
        self.log_queue.put(f"CSV import: {msg}")
        self._after_db_data_changed()

    def _after_db_data_changed(self) -> None:
        """Refresh Integrity / header; mark Misclassify stats as needing re-Analyze."""
        if hasattr(self, "_refresh_integrity"):
            try:
                self._refresh_integrity()
            except Exception:
                pass
        try:
            self._refresh_header_db_path()
        except Exception:
            pass
        # Misclassify / Statistics are computed on demand — prompt re-run
        note = "DB updated · open Misclassify → Analyze to include new rows"
        if hasattr(self, "misclass_status"):
            try:
                self.misclass_status.configure(text=note)
            except Exception:
                pass
        if hasattr(self, "mcstat_status"):
            try:
                self.mcstat_status.configure(text=note)
            except Exception:
                pass
        self.log_queue.put(note)

    def _check_duplicates(self) -> None:
        """Scan DB for duplicate groups and show a summary dialog."""
        from scraper.database import Database

        try:
            db = Database(self.db_path)
            try:
                summary = db.count_duplicates(
                    ["source_url", "external_id", "name_state_dob"]
                )
                samples = db.find_duplicate_groups("source_url", limit_groups=8)
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Duplicate check failed", str(e))
            return

        lines = [
            f"Total offenders: {summary['total_offenders']:,}",
            "",
            "By match key (safe extras are auto-removable; portal/CAPTCHA clusters are not):",
        ]
        for s, info in (summary.get("by_strategy") or {}).items():
            lines.append(
                f"  · {s}: {info.get('safe_extra_rows', 0):,} safe removable "
                f"/ {info.get('extra_rows', 0):,} raw extra "
                f"({info.get('unsafe_groups', 0)} unsafe groups)"
            )
        safe_samples = [g for g in samples if g.get("safe", True)][:5]
        unsafe_samples = [g for g in samples if not g.get("safe", True)][:3]
        if safe_samples:
            lines.append("")
            lines.append("Sample safe source_url duplicates:")
            for g in safe_samples:
                lines.append(
                    f"  · keep #{g['keep_id']} {g['keep_preview']} "
                    f"(×{g['count']}) remove {g['remove_ids'][:4]}"
                )
        if unsafe_samples:
            lines.append("")
            lines.append("Skipped portal/CAPTCHA URL clusters (not removed):")
            for g in unsafe_samples:
                lines.append(f"  · ×{g['count']}  {str(g.get('key') or '')[:60]}")
        lines.append("")
        lines.append("Use Remove duplicates… to delete safe extras (merges sparse fields first).")
        msg = "\n".join(lines)
        self.log_queue.put("Duplicate check:\n" + msg)
        if hasattr(self, "integrity_status"):
            safe_extra = int(summary.get("total_safe_extra_rows") or 0)
            self.integrity_status.configure(
                text=f"Duplicates: {safe_extra:,} safe removable"
            )
        messagebox.showinfo("Duplicate check", msg)
        try:
            self._refresh_integrity()
        except Exception:
            pass

    def _remove_duplicates(self) -> None:
        """Confirm and remove duplicates (safe strategies, merge fields)."""
        from scraper.database import Database

        try:
            db = Database(self.db_path)
            try:
                preview = db.remove_duplicates_all(
                    ["source_url", "external_id", "name_state_dob"],
                    dry_run=True,
                    merge_fields=True,
                    safe_only=True,
                )
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Duplicate scan failed", str(e))
            return

        would = int(preview.get("total_deleted") or 0)
        skipped_u = int(preview.get("total_skipped_unsafe") or 0)
        if would <= 0:
            messagebox.showinfo(
                "Remove duplicates",
                "No safe duplicates found for source_url / external_id / name+DOB.\n"
                f"(Skipped {skipped_u} portal/CAPTCHA URL clusters.)",
            )
            return

        detail_lines = []
        for r in preview.get("strategies") or []:
            if r.get("deleted"):
                detail_lines.append(
                    f"  · {r['strategy']}: {r['deleted']:,} rows in {r['groups']:,} groups"
                )
        detail = "\n".join(detail_lines) if detail_lines else ""
        ok = messagebox.askyesno(
            "Remove duplicates?",
            (
                f"About to permanently delete {would:,} safe duplicate row(s).\n\n"
                f"{detail}\n\n"
                f"Portal/CAPTCHA URL clusters skipped: {skipped_u}\n\n"
                "Keeps the richest record per group and copies missing fields "
                "from deleted rows onto the keeper.\n\n"
                "Continue?"
            ),
        )
        if not ok:
            return

        try:
            db = Database(self.db_path)
            try:
                result = db.remove_duplicates_all(
                    ["source_url", "external_id", "name_state_dob"],
                    dry_run=False,
                    merge_fields=True,
                    safe_only=True,
                )
            finally:
                db.close()
        except Exception as e:
            messagebox.showerror("Remove duplicates failed", str(e))
            return

        deleted = int(result.get("total_deleted") or 0)
        left = int(result.get("total_offenders") or 0)
        skipped_u = int(result.get("total_skipped_unsafe") or 0)
        msg = (
            f"Deleted {deleted:,} duplicates · {left:,} remain"
            + (f" · skipped {skipped_u} unsafe URL clusters" if skipped_u else "")
        )
        self.log_queue.put(f"Dedupe: {msg}")
        if hasattr(self, "integrity_status"):
            self.integrity_status.configure(text=msg)
        messagebox.showinfo("Duplicates removed", msg)
        self._after_db_data_changed()

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------
    def _build_search(self, tab):
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        self.search_name_var = ctk.StringVar()
        ctk.CTkEntry(
            bar, textvariable=self.search_name_var, placeholder_text="Name…",
            width=200, fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", padx=(0, 8))

        self.search_state_var = ctk.StringVar(value="")
        _US_STATES = [
            "", "ALL",
            "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA",
            "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
            "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM",
            "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
            "UT", "VA", "VT", "WA", "WI", "WV", "WY",
        ]
        ctk.CTkComboBox(
            bar, variable=self.search_state_var, width=90,
            values=_US_STATES,
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            dropdown_hover_color=C["elevated"], text_color=C["text"],
        ).pack(side="left", padx=4)

        self.search_race_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_race_var, width=120,
            values=[
                "", "WHITE", "BLACK", "HISPANIC", "ASIAN", "INDIAN",
                "NATIVE AMERICAN", "OTHER",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=4)

        # Surname-ethnicity lists (name-based; includes indian + high-confidence)
        self.search_ethnicity_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_ethnicity_var, width=170,
            values=[
                "",
                "indian",
                "indian_high_confidence",
                "hispanic",
                "asian",
                "african_american",
                "arabic",
                "jewish",
                "portuguese",
                "native_american",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            bar, text="Search", width=100, command=lambda: self._do_search(),
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bar, text="Show all", width=100,
            command=self._search_show_all,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        mid = _hpaned(tab)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        left = ctk.CTkFrame(mid, fg_color="transparent")
        mid.add(left, minsize=360, stretch="always")
        self.search_detail = self._make_detail_drawer(mid)
        mid.add(self.search_detail, minsize=220, stretch="never")
        self.after(160, lambda: self._set_sash(mid, 0, 0.72))

        wrap, self.search_tree = _tree_frame(left)
        wrap.pack(fill="both", expand=True)
        cols = ["name", "race", "state", "county", "age", "crime", "address"]
        self.search_tree.configure(columns=cols, show="headings")
        _stretch_columns(self.search_tree, cols, [140, 90, 50, 90, 45, 180, 160])
        _enable_tree_column_sort(
            self.search_tree, cols, labels={c: c.upper() for c in cols}
        )
        _bind_tree_scroll_isolation(self.search_tree, wrap)
        self.search_tree.bind("<<TreeviewSelect>>", self._search_on_select)
        self._search_records_by_iid: Dict[str, Dict[str, Any]] = {}

        self.search_status = ctk.CTkLabel(
            tab,
            text="Loading names…",
            font=FONT_SM, text_color=C["muted"],
        )
        self.search_status.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
        # Default view: list of names (not race distribution stats)
        self.after(100, self._search_show_all)

    def _search_show_all(self) -> None:
        """Clear filters in the UI, then list all names."""
        try:
            self.search_name_var.set("")
            self.search_state_var.set("")
            self.search_race_var.set("")
            if hasattr(self, "search_ethnicity_var"):
                self.search_ethnicity_var.set("")
        except Exception:
            pass
        self._do_search(name="", state="", race="", ethnicity="")

    def _do_search(
        self, name=None, state=None, race=None, ethnicity=None, *_args, **_kwargs
    ):
        from scraper.searcher import SexOffenderSearcher

        # Always re-read widgets unless explicit override (avoids stale second-run
        # blanks from leftover kwargs / partial clear).
        try:
            name_ui = (self.search_name_var.get() or "").strip()
            state_ui = (self.search_state_var.get() or "").strip().upper()
            race_ui = (self.search_race_var.get() or "").strip()
            eth_ui = (
                (self.search_ethnicity_var.get() or "").strip()
                if hasattr(self, "search_ethnicity_var")
                else ""
            )
        except Exception:
            name_ui, state_ui, race_ui, eth_ui = "", "", "", ""

        name = name_ui if name is None else (name or "").strip()
        state = state_ui if state is None else (state or "").strip().upper()
        race = race_ui if race is None else (race or "").strip()
        eth = eth_ui if ethnicity is None else (ethnicity or "").strip()
        # Treat blank / ALL as no filter
        state_f = state if state and state != "ALL" else None
        race_f = race or None
        eth_f = eth or None

        searcher = SexOffenderSearcher(db_path=self.db_path)
        try:
            try:
                if name:
                    results = searcher.search_by_name(
                        name=name,
                        state=state_f,
                        race=race_f if race_f and race_f.upper() != "INDIAN" else None,
                        limit=500,
                    )
                    records = list(results.records)
                    # Optional post-filters for Indian race + surname ethnicity
                    if race_f and race_f.upper() == "INDIAN":
                        records = [
                            r for r in records
                            if "indian" in (r.get("race") or "").lower()
                            or "indian" in (r.get("ethnicity") or "").lower()
                            or "indian" in (r.get("likely_ethnicity") or "").lower()
                            or "south asian" in (r.get("race") or "").lower()
                        ]
                    if eth_f:
                        eth_res = searcher.search_by_surname_ethnicity(
                            eth_f, state=state_f, limit=5000
                        )
                        allowed = {
                            (
                                (r.get("last_name") or "").strip().lower(),
                                (r.get("full_name") or "").strip().lower(),
                            )
                            for r in eth_res.records
                        }
                        records = [
                            r for r in records
                            if (
                                (r.get("last_name") or "").strip().lower(),
                                (r.get("full_name") or "").strip().lower(),
                            ) in allowed
                            or (r.get("last_name") or "").strip().lower()
                            in {a[0] for a in allowed if a[0]}
                        ]
                    self._populate_search_tree(records)
                    filt = []
                    if state_f:
                        filt.append(state_f)
                    if race_f:
                        filt.append(race_f)
                    if eth_f:
                        filt.append(eth_f)
                    extra = f" · {', '.join(filt)}" if filt else ""
                    self.search_status.configure(
                        text=(
                            f"{len(records)} name matches{extra} · "
                            f"{results.query_time_ms:.0f} ms"
                        )
                    )
                elif eth_f:
                    results = searcher.search_by_surname_ethnicity(
                        eth_f, state=state_f, limit=500
                    )
                    records = list(results.records)
                    if race_f:
                        if race_f.upper() == "INDIAN":
                            records = [
                                r for r in records
                                if "indian" in (r.get("race") or "").lower()
                                or "indian" in (r.get("ethnicity") or "").lower()
                                or "indian" in (r.get("likely_ethnicity") or "").lower()
                                or "south asian" in (r.get("race") or "").lower()
                                or not (r.get("race") or "").strip()
                            ]
                        else:
                            records = [
                                r for r in records
                                if (r.get("race") or "").strip().upper() == race_f.upper()
                            ]
                    self._populate_search_tree(records)
                    where = f" · {state_f}" if state_f else ""
                    self.search_status.configure(
                        text=(
                            f"{len(records)} with surname ethnicity {eth_f}{where}"
                            + (f" · race {race_f}" if race_f else "")
                            + f" · {results.query_time_ms:.0f} ms"
                        )
                    )
                elif race_f:
                    results = searcher.search_by_race(
                        race=race_f,
                        state=state_f,
                        limit=500,
                    )
                    self._populate_search_tree(results.records)
                    where = f" · {state_f}" if state_f else ""
                    self.search_status.configure(
                        text=f"{len(results.records)} with race {race_f}{where}"
                    )
                elif state_f:
                    results = searcher.search_by_state(state=state_f, limit=500)
                    self._populate_search_tree(results.records)
                    self.search_status.configure(
                        text=f"{len(results.records)} in {state_f}"
                    )
                else:
                    # Default / Show all: list of offenders by name, not race stats
                    results = searcher.search_by_state(state="ALL", limit=500)
                    self._populate_search_tree(results.records)
                    total = searcher.get_total_count()
                    shown = len(results.records)
                    self.search_status.configure(
                        text=(
                            f"{shown} names"
                            + (
                                f" (of {total:,} total)"
                                if total > shown
                                else f" · {total:,} total"
                            )
                            + " · select a row for detail"
                        )
                    )
            except Exception as e:
                try:
                    self._populate_search_tree([])
                except Exception:
                    pass
                try:
                    self.search_status.configure(text=f"Search error: {e}")
                except Exception:
                    pass
                try:
                    self.log_queue.put(f"Search error: {e}")
                except Exception:
                    pass
        finally:
            searcher.close()

    def _populate_search_tree(self, records):
        # Reset sort so a prior column sort cannot leave the tree looking empty
        try:
            st = getattr(self.search_tree, "_sort_state", None)
            if isinstance(st, dict):
                st["col"] = None
                st["reverse"] = False
        except Exception:
            pass
        # Detach selection/bindings side-effects before delete (avoids select storms)
        try:
            self.search_tree.selection_remove(*self.search_tree.selection())
        except Exception:
            pass
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_records_by_iid = {}
        # Insert rows first so a detail-drawer photo glitch cannot blank results
        for r in records[:500] if records else []:
            name = (
                f"{r.get('first_name', '') or ''} {r.get('last_name', '') or ''}".strip()
                or (r.get("full_name") or "—")
            )
            crime = (
                (r.get("crime") or r.get("offense_description") or r.get("offense_type") or "")
                or "—"
            )
            # Prefer state column, fall back to source_state for display
            st = (r.get("state") or r.get("source_state") or "—")
            iid = self.search_tree.insert(
                "",
                "end",
                values=(
                    name,  # full name — not truncated
                    _format_race_display(r.get("race")),
                    st,
                    r.get("county") or "—",
                    str(r.get("age") or ""),
                    crime,  # full crime text
                    r.get("address") or "—",
                ),
            )
            self._search_records_by_iid[iid] = dict(r)
        try:
            self.search_tree.yview_moveto(0)
        except Exception:
            pass
        if getattr(self, "search_detail", None) is not None:
            try:
                self._fill_detail_drawer(self.search_detail, None)
            except Exception as e:
                try:
                    self.log_queue.put(f"Detail drawer: {e}")
                except Exception:
                    pass

    def _search_on_select(self, _event=None):
        sel = self.search_tree.selection()
        if not sel:
            return
        rec = self._search_records_by_iid.get(sel[0])
        if rec and rec.get("id") and not rec.get("photo_path"):
            # Refresh full row from DB for photo/html
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    full = db.get_offender_by_id(int(rec["id"]))
                    if full:
                        rec = full
                        self._search_records_by_iid[sel[0]] = full
                finally:
                    db.close()
            except Exception:
                pass
        self._fill_detail_drawer(self.search_detail, rec)

    def _show_race_distribution(self, dist):
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_records_by_iid = {}
        self._fill_detail_drawer(self.search_detail, None)
        total = sum(d.get("count", 0) for d in dist) or 1
        for d in dist:
            race = d.get("race") or "—"
            count = d.get("count", 0)
            pct = count / total * 100
            bar = "▮" * max(1, int(pct / 4))
            self.search_tree.insert(
                "", "end", values=(race, str(count), f"{pct:.1f}%", bar, "", "", "")
            )

    # -----------------------------------------------------------------------
    # Integrity dashboard + requeue
    # -----------------------------------------------------------------------
    def _build_integrity(self, tab):
        """
        Integrity layout:
          - Management / requeue pinned at bottom (always visible)
          - Middle area scrolls (summary + state table)
        """
        tab.configure(fg_color=C["surface"])

        # --- Pinned management panel (pack bottom first so it stays visible) ---
        right = _card(tab)
        right.pack(side="bottom", fill="x", padx=12, pady=(4, 8))
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 4))
        _section_label(head, "Integrity management · requeue incomplete reports").pack(
            side="left"
        )
        self.requeue_incomplete_label = ctk.CTkLabel(
            head, text="", font=FONT_SM, text_color=C["muted"],
        )
        self.requeue_incomplete_label.pack(side="right")
        _muted(
            right,
            "Re-downloads report pages for DB rows that have a source URL but are missing "
            "selected fields (race / crime / photo / HTML). Updates records in place.",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.requeue_need_race = ctk.BooleanVar(value=True)
        self.requeue_need_crime = ctk.BooleanVar(value=True)
        self.requeue_need_photo = ctk.BooleanVar(value=True)
        self.requeue_need_html = ctk.BooleanVar(value=False)
        chk_row = ctk.CTkFrame(right, fg_color="transparent")
        chk_row.pack(fill="x", padx=12, pady=2)
        for text, var in (
            ("Missing race", self.requeue_need_race),
            ("Missing crime", self.requeue_need_crime),
            ("Missing photo", self.requeue_need_photo),
            ("Missing HTML", self.requeue_need_html),
        ):
            ctk.CTkCheckBox(
                chk_row, text=text, variable=var, font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            ).pack(side="left", padx=(0, 14))

        lim_row = ctk.CTkFrame(right, fg_color="transparent")
        lim_row.pack(fill="x", padx=12, pady=(6, 12))
        ctk.CTkLabel(lim_row, text="Max rows", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 6)
        )
        self.requeue_limit_var = ctk.IntVar(value=50)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_limit_var, width=70,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        ctk.CTkLabel(lim_row, text="Delay (s)", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(12, 6)
        )
        self.requeue_delay_var = ctk.DoubleVar(value=0.75)
        ctk.CTkEntry(
            lim_row, textvariable=self.requeue_delay_var, width=60,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        self.requeue_btn = ctk.CTkButton(
            lim_row, text="Requeue incomplete", height=32, width=150,
            command=self._start_requeue,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        )
        self.requeue_btn.pack(side="left", padx=(16, 8))
        self.requeue_cancel_btn = ctk.CTkButton(
            lim_row, text="Cancel", height=32, width=80, command=self._cancel_requeue,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"], state="disabled",
        )
        self.requeue_cancel_btn.pack(side="left", padx=(0, 10))
        self.requeue_status = ctk.CTkLabel(
            lim_row, text="Idle", font=FONT_SM, text_color=C["dim"],
        )
        self.requeue_status.pack(side="left")
        self.requeue_progress = ctk.CTkProgressBar(
            right, progress_color=C["accent"], fg_color=C["elevated"], height=6,
        )
        self.requeue_progress.pack(fill="x", padx=12, pady=(0, 10))
        self.requeue_progress.set(0)
        self._requeue_cancel = False

        # --- Scrollable body: summary + by-state table ---
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C["surface"])
        scroll.pack(side="top", fill="both", expand=True, padx=4, pady=(4, 0))
        self._integrity_scroll = scroll

        top = ctk.CTkFrame(scroll, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkButton(
            top, text="Refresh", width=100, command=self._refresh_integrity,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            top, text="Export report CSV…", width=140, command=self._export_integrity_csv,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Check duplicates", width=130, command=self._check_duplicates,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="Remove duplicates…", width=140, command=self._remove_duplicates,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)
        self.integrity_status = ctk.CTkLabel(
            top, text="", font=FONT_SM, text_color=C["muted"],
        )
        self.integrity_status.pack(side="right", padx=8)

        summary = _card(scroll)
        summary.pack(fill="x", padx=8, pady=(0, 6))
        _section_label(summary, "Archive integrity").pack(anchor="w", padx=14, pady=(8, 2))
        _muted(
            summary,
            "TOTAL = records in that state.  RACE/CRIME/PHOTO/HTML % = share with that field filled. "
            "Management settings are pinned at the bottom of this tab.",
        ).pack(anchor="w", padx=14, pady=(0, 4))
        self.integrity_summary = ctk.CTkLabel(
            summary, text="Click Refresh to load stats.",
            font=FONT_SM, text_color=C["text"], anchor="w", justify="left",
        )
        self.integrity_summary.pack(fill="x", padx=14, pady=(0, 8))

        table_card = _card(scroll)
        table_card.pack(fill="x", padx=8, pady=(0, 12))
        _section_label(table_card, "By state").pack(anchor="w", padx=14, pady=(10, 4))
        wrap, self.integrity_tree = _tree_frame(table_card)
        wrap.pack(fill="x", padx=10, pady=(0, 12))
        # Fixed tall viewport so many states show; outer frame still scrolls
        wrap.configure(height=420)
        wrap.pack_propagate(False)
        icols = [
            "state", "total", "pct_race", "pct_crime", "pct_photo", "pct_html",
            "with_race", "with_crime", "with_photo", "with_html",
        ]
        self.integrity_tree.configure(columns=icols, show="headings", height=16)
        _stretch_columns(
            self.integrity_tree,
            icols,
            [80, 90, 100, 100, 100, 100, 110, 110, 110, 110],
        )
        _enable_tree_column_sort(
            self.integrity_tree,
            icols,
            labels={
                "state": "STATE",
                "total": "TOTAL",
                "pct_race": "RACE %",
                "pct_crime": "CRIME %",
                "pct_photo": "PHOTO %",
                "pct_html": "HTML %",
                "with_race": "RACE COUNT",
                "with_crime": "CRIME COUNT",
                "with_photo": "PHOTO COUNT",
                "with_html": "HTML COUNT",
            },
        )
        _bind_tree_scroll_isolation(self.integrity_tree, wrap)

        self.after(200, self._refresh_integrity)

    def _refresh_integrity(self):
        from scraper.database import Database

        try:
            db = Database(self.db_path)
            try:
                report = db.get_integrity_report()
                incomplete = db.find_incomplete_reports(
                    need_race=True, need_crime=True, need_photo=True, need_html=False,
                    limit=5000,
                )
                try:
                    dup_summary = db.count_duplicates(
                        ["source_url", "external_id", "name_state_dob"]
                    )
                except Exception:
                    dup_summary = None
            finally:
                db.close()
        except Exception as e:
            self.integrity_summary.configure(text=f"Error: {e}")
            return

        o = report["overall"]
        complete = int(o.get("with_everything") or 0)
        total = int(o.get("total") or 0)
        dup_line = ""
        if dup_summary and isinstance(dup_summary.get("by_strategy"), dict):
            parts = []
            for s, info in dup_summary["by_strategy"].items():
                safe_e = int(info.get("safe_extra_rows") or 0)
                unsafe_g = int(info.get("unsafe_groups") or 0)
                if safe_e or unsafe_g or info.get("extra_rows"):
                    bit = f"{s}: {safe_e:,} safe"
                    if unsafe_g:
                        bit += f" (+{unsafe_g} portal/CAPTCHA clusters skipped)"
                    parts.append(bit)
            if parts:
                dup_line = "\nDuplicates: " + " · ".join(parts)
            else:
                dup_line = "\nDuplicates: none found (source_url / external_id / name+DOB)"
        self.integrity_summary.configure(
            text=(
                f"Total records: {total:,}  ·  "
                f"Complete (race+crime+photo+HTML): {complete:,} "
                f"({o.get('pct_everything', 0)}%)\n"
                f"Race: {o['with_race']:,} ({o.get('pct_race', 0)}%)  ·  "
                f"Crime: {o['with_crime']:,} ({o.get('pct_crime', 0)}%)  ·  "
                f"Photo: {o['with_photo']:,} ({o.get('pct_photo', 0)}%)  ·  "
                f"HTML: {o['with_html']:,} ({o.get('pct_html', 0)}%)"
                f"{dup_line}"
            )
        )
        self.requeue_incomplete_label.configure(
            text=f"Incomplete with URL (race/crime/photo): {len(incomplete):,}"
        )
        self.integrity_tree.delete(*self.integrity_tree.get_children())
        for st in report["by_state"]:
            self.integrity_tree.insert(
                "",
                "end",
                values=(
                    st["state"],
                    st["total"],
                    f"{st['pct_race']:.0f}%",
                    f"{st['pct_crime']:.0f}%",
                    f"{st['pct_photo']:.0f}%",
                    f"{st['pct_html']:.0f}%",
                    st["with_race"],
                    st["with_crime"],
                    st["with_photo"],
                    st["with_html"],
                ),
            )
        n_states = max(8, len(report["by_state"]))
        self.integrity_tree.configure(height=min(24, max(12, n_states + 2)))

        self.integrity_status.configure(
            text=f"Updated · {len(report['by_state'])} states/territories in DB"
        )
        self._last_integrity_report = report

    def _export_integrity_csv(self):
        report = getattr(self, "_last_integrity_report", None)
        if not report:
            self._refresh_integrity()
            report = getattr(self, "_last_integrity_report", None)
        if not report:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "state", "total", "with_race", "pct_race", "with_crime", "pct_crime",
                        "with_photo", "pct_photo", "with_html", "pct_html", "with_url",
                    ],
                )
                w.writeheader()
                for row in report["by_state"]:
                    w.writerow(row)
            messagebox.showinfo("Exported", path)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _refresh_header_db_path(self):
        """Show active SQLite path in the header."""
        try:
            p = Path(self.db_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()
            # Prefer short relative path when under project
            try:
                show = str(p.relative_to(Path.cwd()))
            except ValueError:
                show = str(p)
            if len(show) > 52:
                show = "…" + show[-50:]
            n = ""
            try:
                from scraper.database import Database
                db = Database(self.db_path)
                try:
                    n = f"  ·  {db.get_total_count():,} records"
                finally:
                    db.close()
            except Exception:
                pass
            if hasattr(self, "header_db_label"):
                self.header_db_label.configure(text=f"DB: {show}{n}")
        except Exception:
            if hasattr(self, "header_db_label"):
                self.header_db_label.configure(text=f"DB: {self.db_path}")

    def _open_data_folder_header(self):
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        # Prefer folder containing the DB
        try:
            dbp = Path(self.db_path)
            if dbp.parent.is_dir():
                path = dbp.parent
        except Exception:
            pass
        self._open_path(path)

    def _cancel_requeue(self):
        self._requeue_cancel = True
        try:
            self.requeue_status.configure(text="Cancelling…")
        except Exception:
            pass
        self.requeue_status.configure(text="Cancelling…")

    def _start_requeue(self):
        if self.is_running:
            messagebox.showwarning("Busy", "Wait for the current job to finish.")
            return
        try:
            limit = max(1, int(self.requeue_limit_var.get()))
            delay = max(0.25, float(self.requeue_delay_var.get()))
        except (TypeError, ValueError):
            limit, delay = 50, 0.75

        need_race = bool(self.requeue_need_race.get())
        need_crime = bool(self.requeue_need_crime.get())
        need_photo = bool(self.requeue_need_photo.get())
        need_html = bool(self.requeue_need_html.get())
        if not any((need_race, need_crime, need_photo, need_html)):
            messagebox.showwarning("Nothing selected", "Enable at least one missing field.")
            return

        self._requeue_cancel = False
        self._set_running(True)
        self.requeue_btn.configure(state="disabled")
        self.requeue_cancel_btn.configure(state="normal")
        self.requeue_status.configure(text="Requeue running…")
        self.requeue_progress.set(0)
        self.requeue_progress.configure(mode="determinate")

        def log(msg):
            self.log_queue.put(msg)

        def on_progress(done: int, total: int):
            frac = (done / total) if total else 0.0
            self.after(
                0,
                lambda d=done, t=total, f=frac: (
                    self.requeue_progress.set(min(1.0, max(0.0, f))),
                    self.requeue_status.configure(text=f"Requeue {d}/{t}…"),
                ),
            )

        def worker():
            from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(
                db_path=self.db_path,
                delay=2.0,
                report_delay=delay,
                html_dir="data/report_pages",
                cancel_check=lambda: self._requeue_cancel,
            )
            try:
                summary = builder.requeue_incomplete(
                    need_race=need_race,
                    need_crime=need_crime,
                    need_photo=need_photo,
                    need_html=need_html,
                    limit=limit,
                    save_html=True,
                    log=log,
                    on_progress=on_progress,
                )

                def done():
                    self._set_running(False)
                    self.requeue_btn.configure(state="normal")
                    self.requeue_cancel_btn.configure(state="disabled")
                    self.requeue_progress.set(1.0)
                    self.requeue_status.configure(
                        text=(
                            f"Done · queued {summary.get('queued', 0)} · "
                            f"updated {summary.get('updated', 0)} · "
                            f"errors {summary.get('errors', 0)}"
                        )
                    )
                    self._refresh_integrity()
                    self._refresh_header_db_path()

                self.after(0, done)
            except Exception as e:
                log(f"Requeue ERROR: {e}")

                def fail():
                    self._set_running(False)
                    self.requeue_btn.configure(state="normal")
                    self.requeue_cancel_btn.configure(state="disabled")
                    self.requeue_progress.set(0)
                    self.requeue_status.configure(text=f"Error: {e}")

                self.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()

    # -----------------------------------------------------------------------
    # Misclassify
    # -----------------------------------------------------------------------
    def _misclass_controls_bar(self, parent) -> ctk.CTkFrame:
        """Shared Analyze filters (used by Misclassify + Statistics)."""
        bar = ctk.CTkFrame(parent, fg_color="transparent")

        if not hasattr(self, "misclass_ethnicity_var"):
            self.misclass_ethnicity_var = ctk.StringVar(value="all")
            self.misclass_conf_var = ctk.DoubleVar(value=0.5)
            # 0 = scan entire DB; when capped, Analyze walks newest ids first
            self.misclass_limit_var = ctk.IntVar(value=0)

        ctk.CTkComboBox(
            bar, variable=self.misclass_ethnicity_var, width=160,
            values=[
                "all", "hispanic", "asian", "indian", "indian_high_confidence",
                "african_american",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(bar, text="Min conf.", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(8, 4)
        )
        ctk.CTkEntry(
            bar, textvariable=self.misclass_conf_var, width=60,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")

        ctk.CTkLabel(bar, text="Scan cap (0=all)", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(12, 4)
        )
        ctk.CTkEntry(
            bar, textvariable=self.misclass_limit_var, width=80,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")

        ctk.CTkButton(
            bar, text="Analyze", width=100, command=self._run_misclassification,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=12)
        ctk.CTkButton(
            bar, text="Export CSV", width=100, command=self._export_misclass,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")
        return bar

    def _build_misclass(self, tab):
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = self._misclass_controls_bar(tab)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        # Table | detail drawer (photo + fields) — same pattern as Search
        mid = _hpaned(tab)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 4))
        left = ctk.CTkFrame(mid, fg_color="transparent")
        mid.add(left, minsize=360, stretch="always")
        self.misclass_detail = self._make_detail_drawer(mid)
        mid.add(self.misclass_detail, minsize=220, stretch="never")
        self.after(160, lambda: self._set_sash(mid, 0, 0.72))

        results_card = _card(left)
        results_card.pack(fill="both", expand=True)
        _section_label(results_card, "Potential mismatches").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            results_card,
            "Surname ethnicity does not match recorded race. "
            "Select a row for photo and detail · Statistics for breakdowns.",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        wrap, self.misclass_tree = _tree_frame(results_card)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        cols = ["name", "recorded_race", "likely_ethnicity", "confidence", "matching_names"]
        self.misclass_tree.configure(columns=cols, show="headings")
        _stretch_columns(self.misclass_tree, cols, [160, 110, 130, 90, 200])
        _enable_tree_column_sort(
            self.misclass_tree,
            cols,
            labels={c: c.replace("_", " ").upper() for c in cols},
        )
        _bind_tree_scroll_isolation(self.misclass_tree, wrap)
        self.misclass_tree.bind("<<TreeviewSelect>>", self._misclass_on_select)
        self._misclass_records_by_iid: Dict[str, Dict[str, Any]] = {}

        self.misclass_status = ctk.CTkLabel(
            tab,
            text="Compare recorded race to surname ethnicity lists · click a name for photo",
            font=FONT_SM, text_color=C["muted"],
        )
        self.misclass_status.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

    def _misclass_on_select(self, _event=None):
        """Show photo + detail for the selected mismatch row."""
        sel = self.misclass_tree.selection()
        if not sel:
            return
        rec = self._misclass_records_by_iid.get(sel[0])
        if not rec:
            return
        # Prefer full DB row so photo_path / HTML paths are current
        if rec.get("id"):
            try:
                from scraper.database import Database

                db = Database(self.db_path)
                try:
                    full = db.get_offender_by_id(int(rec["id"]))
                    if full:
                        # Keep analysis labels on the record for display context
                        full = dict(full)
                        for k in ("_misclass_expected_race", "_misclass_likely", "_misclass_conf"):
                            if k in rec:
                                full[k] = rec[k]
                        rec = full
                        self._misclass_records_by_iid[sel[0]] = rec
                finally:
                    db.close()
            except Exception:
                pass
        if getattr(self, "misclass_detail", None) is not None:
            self._fill_detail_drawer(self.misclass_detail, rec)

    def _build_misclass_statistics(self, tab):
        """Statistics: fixed toolbar + metrics; scroll only for charts/tables."""
        tab.configure(fg_color=C["surface"])
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # Fixed top — always visible, no wasted scroll gap above content
        top = ctk.CTkFrame(tab, fg_color=C["surface"])
        top.grid(row=0, column=0, sticky="ew", padx=0, pady=0)

        bar = self._misclass_controls_bar(top)
        bar.pack(fill="x", padx=8, pady=(6, 2))

        # Metrics as a single compact row (no nested "Run summary" card header)
        sum_row = ctk.CTkFrame(top, fg_color="transparent")
        sum_row.pack(fill="x", padx=8, pady=(0, 4))

        def _metric_chip(parent, key: str) -> ctk.CTkLabel:
            chip = ctk.CTkFrame(
                parent, fg_color=C["elevated"], corner_radius=6,
                border_width=1, border_color=C["border"],
            )
            chip.pack(side="left", padx=3, pady=1, fill="x", expand=True)
            lb = ctk.CTkLabel(
                chip, text="—", font=FONT_SM, text_color=C["text"], anchor="center",
            )
            lb.pack(padx=8, pady=5)
            setattr(self, key, lb)
            return lb

        _metric_chip(sum_row, "mcstat_db")
        _metric_chip(sum_row, "mcstat_eth_n")  # selected ethnicity population
        _metric_chip(sum_row, "mcstat_n")      # misclassified count
        _metric_chip(sum_row, "mcstat_rate")   # % of selected ethnicity
        _metric_chip(sum_row, "mcstat_conf")
        self.mcstat_filter = ctk.CTkLabel(
            top, text="Run Analyze to fill charts and tables.",
            font=FONT_SM, text_color=C["dim"], anchor="w",
        )
        self.mcstat_filter.pack(fill="x", padx=10, pady=(0, 4))

        # Scroll only the heavy content
        scroll = ctk.CTkScrollableFrame(
            tab, fg_color=C["surface"], corner_radius=0, border_width=0,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        scroll.grid_columnconfigure(0, weight=1)
        self._mcstat_scroll = scroll
        self.after(30, lambda: _wire_wide_scroll(tab, scroll))

        # Three pie charts side by side — first content in the scroll area
        charts = ctk.CTkFrame(scroll, fg_color="transparent")
        charts.pack(fill="x", padx=4, pady=(2, 6))
        self._mcstat_charts_host = charts
        charts.grid_columnconfigure((0, 1, 2), weight=1, uniform="pies")
        self._mcstat_chart_refs: List[Any] = []
        self.mcstat_chart_labels: List[ctk.CTkLabel] = []
        self._mcstat_chart_cells: List[ctk.CTkFrame] = []
        for i, placeholder in enumerate(
            (
                "By surname ethnicity\n(run Analyze)",
                "Misclassified as\n(run Analyze)",
                "Confidence bands\n(run Analyze)",
            )
        ):
            cell = ctk.CTkFrame(
                charts,
                fg_color=C["tree_bg"],
                corner_radius=8,
                border_width=1,
                border_color=C["border"],
                height=300,
            )
            cell.grid(row=0, column=i, sticky="nsew", padx=3, pady=0)
            cell.grid_propagate(False)
            lab = ctk.CTkLabel(
                cell, text=placeholder, font=FONT_SM, text_color=C["dim"],
            )
            lab.pack(expand=True, fill="both", padx=2, pady=2)
            self.mcstat_chart_labels.append(lab)
            self._mcstat_chart_cells.append(cell)

        # Transition table — full width, stretch columns
        trans = _card(scroll)
        trans.pack(fill="x", padx=6, pady=(0, 6))
        ctk.CTkLabel(
            trans,
            text="Transitions · surname ethnicity → recorded race",
            font=FONT_BOLD, text_color=C["muted"], anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 4))
        tw, self.mcstat_transition_tree = _tree_frame(trans)
        tw.pack(fill="x", padx=8, pady=(0, 8))
        tw.configure(height=220)
        tw.pack_propagate(False)
        tcols = ["surname_ethnicity", "misclassified_as", "count", "pct", "avg_conf", "example"]
        self.mcstat_transition_tree.configure(columns=tcols, show="headings", height=12)
        _stretch_columns(
            self.mcstat_transition_tree, tcols, [200, 180, 80, 70, 90, 260]
        )
        _enable_tree_column_sort(
            self.mcstat_transition_tree,
            tcols,
            labels={
                "surname_ethnicity": "SURNAME ETHNICITY",
                "misclassified_as": "MISCLASSIFIED AS",
                "count": "COUNT",
                "pct": "PERCENT",
                "avg_conf": "AVG CONF",
                "example": "EXAMPLE NAME",
            },
        )
        _bind_tree_scroll_isolation(self.mcstat_transition_tree, tw)

        # Breakdown tables side by side under transition table
        tables = ctk.CTkFrame(scroll, fg_color="transparent")
        tables.pack(fill="x", padx=4, pady=(0, 8))
        tables.grid_columnconfigure((0, 1, 2), weight=1, uniform="bkt")

        def _col_table(parent, col: int, title: str, cols: List[str], labels: Dict[str, str], widths: List[int]):
            cell = _card(parent)
            cell.grid(row=0, column=col, sticky="nsew", padx=3, pady=0)
            ctk.CTkLabel(
                cell, text=title, font=FONT_BOLD, text_color=C["muted"], anchor="w",
            ).pack(fill="x", padx=8, pady=(6, 2))
            w, tree = _tree_frame(cell)
            w.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            w.configure(height=140)
            w.pack_propagate(False)
            tree.configure(columns=cols, show="headings", height=5)
            _stretch_columns(tree, cols, widths)
            _enable_tree_column_sort(tree, cols, labels=labels)
            _bind_tree_scroll_isolation(tree, w)
            return tree

        self.mcstat_eth_tree = _col_table(
            tables, 0, "By surname ethnicity",
            ["ethnicity", "count", "pct"],
            {"ethnicity": "ETHNICITY", "count": "COUNT", "pct": "%"},
            [160, 60, 50],
        )
        self.mcstat_race_tree = _col_table(
            tables, 1, "By recorded race",
            ["race", "count", "pct"],
            {"race": "RECORDED AS", "count": "COUNT", "pct": "%"},
            [160, 60, 50],
        )
        self.mcstat_conf_tree = _col_table(
            tables, 2, "Confidence bands",
            ["band", "count", "pct"],
            {"band": "BAND", "count": "COUNT", "pct": "%"},
            [160, 60, 50],
        )

        self.mcstat_status = ctk.CTkLabel(
            scroll,
            text="Statistics update when you run Analyze (from this tab or Misclassify).",
            font=FONT_SM, text_color=C["muted"],
        )
        self.mcstat_status.pack(anchor="w", padx=8, pady=(0, 8))

    def _update_misclass_stats(
        self,
        results: list,
        *,
        db_total: int,
        scanned_cap: int,
        min_conf: float,
        eth_filter: str,
        eth_base_count: Optional[int] = None,
    ) -> None:
        """Refresh Statistics tab from analysis results.

        *eth_base_count*: how many scanned offenders matched the selected
        surname ethnicity (at min conf). Misclassification rate is
        mismatches / eth_base_count when a specific ethnicity is selected.
        """
        from collections import Counter, defaultdict

        n = len(results)
        eth_label = (eth_filter or "all").strip() or "all"
        # Rate among selected ethnicity when we know the base population
        if eth_base_count is not None and eth_label != "all":
            denom = max(1, int(eth_base_count))
            rate = (n / denom * 100.0) if denom else 0.0
            rate_txt = f"Misclass: {rate:.1f}% of {eth_label}"
            eth_n_txt = f"{eth_label}: {int(eth_base_count):,}"
        else:
            denom = max(1, min(db_total, scanned_cap) if db_total else scanned_cap)
            rate = (n / denom * 100.0) if denom else 0.0
            rate_txt = f"Rate: {rate:.2f}% of scanned"
            eth_n_txt = f"Ethnicity base: — (filter=all)"

        if hasattr(self, "mcstat_db"):
            self.mcstat_db.configure(text=f"DB: {db_total:,}")
            if hasattr(self, "mcstat_eth_n"):
                self.mcstat_eth_n.configure(text=eth_n_txt)
            self.mcstat_n.configure(text=f"Misclassified: {n:,}")
            self.mcstat_rate.configure(text=rate_txt)
            if results:
                confs = [float(mc.confidence) for mc in results]
                self.mcstat_conf.configure(
                    text=f"Conf avg {sum(confs)/len(confs):.3f}  "
                    f"({min(confs):.2f}–{max(confs):.2f})"
                )
            else:
                self.mcstat_conf.configure(text="Conf: —")
            if eth_base_count is not None and eth_label != "all":
                ok_n = max(0, int(eth_base_count) - n)
                self.mcstat_filter.configure(
                    text=(
                        f"Selected ethnicity: {eth_label} · "
                        f"{int(eth_base_count):,} name matches (min conf {min_conf:.2f}) · "
                        f"{n:,} misclassified ({rate:.1f}%) · "
                        f"{ok_n:,} race-compatible · "
                        f"scan cap {scanned_cap:,}"
                    )
                )
            else:
                self.mcstat_filter.configure(
                    text=(
                        f"Filter: {eth_label} · min conf. {min_conf:.2f} · "
                        f"scanned cap {scanned_cap:,} · "
                        f"{'no mismatches' if n == 0 else f'{n:,} rows in transition table'}"
                    )
                )

        # Transitions: surname ethnicity → recorded race
        pair_counts: Counter = Counter()
        pair_conf: Dict[tuple, list] = defaultdict(list)
        pair_example: Dict[tuple, str] = {}
        for mc in results:
            eth = (mc.likely_ethnicity or "—").strip() or "—"
            race = (mc.expected_race or "—").strip() or "—"
            key = (eth, race)
            pair_counts[key] += 1
            pair_conf[key].append(float(mc.confidence))
            if key not in pair_example:
                rec = mc.record or {}
                name = (
                    f"{rec.get('first_name', '') or ''} {rec.get('last_name', '') or ''}"
                ).strip() or (rec.get("full_name") or "—")
                pair_example[key] = name

        if hasattr(self, "mcstat_transition_tree"):
            self.mcstat_transition_tree.delete(*self.mcstat_transition_tree.get_children())
            for (eth, race), cnt in pair_counts.most_common():
                confs = pair_conf[(eth, race)]
                avg = sum(confs) / len(confs) if confs else 0.0
                pct = (cnt / n * 100.0) if n else 0.0
                self.mcstat_transition_tree.insert(
                    "",
                    "end",
                    values=(
                        eth,  # full ethnicity label
                        race,  # full race label
                        str(cnt),
                        f"{pct:.1f}%",
                        f"{avg:.3f}",
                        pair_example.get((eth, race), "—"),
                    ),
                )

        by_eth = Counter((mc.likely_ethnicity or "—") for mc in results)
        by_race = Counter((mc.expected_race or "—") for mc in results)

        def _fill(tree, counter: Counter):
            if tree is None:
                return
            tree.delete(*tree.get_children())
            for label, cnt in counter.most_common():
                pct = (cnt / n * 100.0) if n else 0.0
                tree.insert("", "end", values=(str(label), str(cnt), f"{pct:.1f}%"))

        _fill(getattr(self, "mcstat_eth_tree", None), by_eth)
        _fill(getattr(self, "mcstat_race_tree", None), by_race)

        # Confidence bands (high → low)
        bands = Counter()
        for mc in results:
            c = float(mc.confidence)
            if c >= 0.9:
                bands["0.90 – 1.00 (high)"] += 1
            elif c >= 0.75:
                bands["0.75 – 0.89"] += 1
            elif c >= 0.6:
                bands["0.60 – 0.74"] += 1
            else:
                bands["below 0.60"] += 1

        band_order = [
            "0.90 – 1.00 (high)",
            "0.75 – 0.89",
            "0.60 – 0.74",
            "below 0.60",
        ]
        if hasattr(self, "mcstat_conf_tree"):
            self.mcstat_conf_tree.delete(*self.mcstat_conf_tree.get_children())
            for band in band_order:
                cnt = bands.get(band, 0)
                if cnt == 0 and n > 0:
                    continue
                if n == 0 and band != band_order[0]:
                    continue
                pct = (cnt / n * 100.0) if n else 0.0
                self.mcstat_conf_tree.insert(
                    "", "end", values=(band, str(cnt), f"{pct:.1f}")
                )

        # Side-by-side pie charts (each ~1/3 width)
        if getattr(self, "mcstat_chart_labels", None):
            try:
                host = getattr(self, "_mcstat_charts_host", None)
                if host is not None:
                    host.update_idletasks()
                    host_w = max(720, host.winfo_width())
                else:
                    host_w = 960
            except Exception:
                host_w = 960
            # 3 columns with small gaps
            pie_w = max(220, (host_w - 24) // 3)
            pie_h = 300
            eth_items = by_eth.most_common(8)
            race_items = by_race.most_common(8)
            conf_items = [(b, bands[b]) for b in band_order if bands.get(b, 0) > 0]
            charts_data = [
                (eth_items, "By surname ethnicity"),
                (race_items, "Misclassified as (race)"),
                (conf_items, "Confidence bands"),
            ]
            refs: List[Any] = []
            for i, (items, title) in enumerate(charts_data):
                try:
                    img = _render_pie_chart(
                        items,
                        title=title,
                        width=pie_w,
                        height=pie_h,
                        max_slices=8,
                        bg=C["tree_bg"],
                        fg=C["text"],
                        muted=C["muted"],
                        accent=C["accent"],
                        legend_below=True,
                    )
                    refs.append(img)
                    self.mcstat_chart_labels[i].configure(image=img, text="")
                    if getattr(self, "_mcstat_chart_cells", None) and i < len(self._mcstat_chart_cells):
                        self._mcstat_chart_cells[i].configure(height=pie_h + 8)
                except Exception:
                    self.mcstat_chart_labels[i].configure(
                        image=None, text=f"{title} (chart error)"
                    )
            self._mcstat_chart_refs = refs

        if hasattr(self, "mcstat_status"):
            if n:
                top = pair_counts.most_common(1)
                if top:
                    (eth, race), cnt = top[0]
                    self.mcstat_status.configure(
                        text=(
                            f"Top transition: {eth} → recorded as {race}  ({cnt:,} · "
                            f"{cnt/n*100:.1f}% of mismatches)"
                        )
                    )
                else:
                    self.mcstat_status.configure(text=f"{n:,} mismatches")
            else:
                self.mcstat_status.configure(
                    text="No mismatches for this filter — try lower min conf. or another ethnicity."
                )

    def _run_misclassification(self):
        from scraper.searcher import SexOffenderSearcher

        searcher = SexOffenderSearcher(db_path=self.db_path)
        eth = (self.misclass_ethnicity_var.get() or "all").strip()
        try:
            min_conf = float(self.misclass_conf_var.get())
            limit = int(self.misclass_limit_var.get())
            db_total = searcher.get_total_count()
            eth_filter = None if eth == "all" else eth
            # Always get base_count so Statistics can show % of selected ethnicity
            results, eth_base = searcher.analyze_ethnicities(
                min_confidence=min_conf,
                limit=limit,
                ethnicity_filter=eth_filter,
                return_base_count=True,
            )
        finally:
            searcher.close()

        self._misclass_results = results
        self._misclass_meta = {
            "db_total": db_total,
            "scanned_cap": limit,
            "min_conf": min_conf,
            "eth_filter": eth,
            "eth_base_count": eth_base,
        }

        if hasattr(self, "misclass_tree"):
            self.misclass_tree.delete(*self.misclass_tree.get_children())
            self._misclass_records_by_iid = {}
            if getattr(self, "misclass_detail", None) is not None:
                try:
                    self._fill_detail_drawer(self.misclass_detail, None)
                except Exception:
                    pass
            for mc in results[:500]:
                rec = dict(mc.record or {})
                name = (
                    f"{rec.get('first_name', '') or ''} "
                    f"{rec.get('last_name', '') or ''}"
                ).strip() or (rec.get("full_name") or "—")
                rec["_misclass_expected_race"] = mc.expected_race
                rec["_misclass_likely"] = mc.likely_ethnicity
                rec["_misclass_conf"] = mc.confidence
                iid = self.misclass_tree.insert(
                    "",
                    "end",
                    values=(
                        name,
                        (mc.expected_race or "—")[:14],
                        (mc.likely_ethnicity or "")[:18],
                        f"{mc.confidence:.3f}",
                        "; ".join(mc.matching_names[:3]),
                    ),
                )
                self._misclass_records_by_iid[iid] = rec
            shown = min(500, len(results))
            if hasattr(self, "misclass_status"):
                if eth != "all" and eth_base is not None:
                    rate = (len(results) / eth_base * 100.0) if eth_base else 0.0
                    self.misclass_status.configure(
                        text=(
                            f"{eth}: {eth_base:,} name matches · "
                            f"{len(results):,} misclassified ({rate:.1f}%)"
                            + (f" · showing first {shown}" if len(results) > shown else "")
                            + " · select a row for photo"
                        )
                    )
                else:
                    self.misclass_status.configure(
                        text=f"{len(results)} potential mismatches"
                        + (f" · showing first {shown}" if len(results) > shown else "")
                        + " · select a row for photo · Statistics for transitions"
                    )

        self._update_misclass_stats(
            results,
            db_total=db_total,
            scanned_cap=limit,
            min_conf=min_conf,
            eth_filter=eth,
            eth_base_count=eth_base,
        )
        self.log_queue.put(
            f"Misclassification: {len(results)} mismatches"
            + (f" / {eth_base} {eth}" if eth != "all" else "")
        )

    def _export_misclass(self):
        from scraper.searcher import SexOffenderSearcher

        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if not path:
            return
        searcher = SexOffenderSearcher(db_path=self.db_path)
        eth = (self.misclass_ethnicity_var.get() or "all").strip()
        try:
            n = searcher.export_misclassifications(
                path,
                min_confidence=float(self.misclass_conf_var.get()),
                ethnicity_filter=None if eth == "all" else eth,
            )
        finally:
            searcher.close()
        messagebox.showinfo("Exported", f"{n} rows → {path}")

    # -----------------------------------------------------------------------
    # NSOPW
    # -----------------------------------------------------------------------
    def _build_nsopw(self, tab):
        tab.configure(fg_color=C["surface"])
        # Top: settings (no scroll) · Bottom: live inserts (expands)
        split = _vpaned(tab)
        split.pack(fill="both", expand=True, padx=4, pady=4)
        self._nsopw_split = split

        controls_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        inserts_host = ctk.CTkFrame(split, fg_color=C["surface"], corner_radius=0)
        # Controls keep natural height; inserts take remaining space
        split.add(controls_host, minsize=200, stretch="never")
        split.add(inserts_host, minsize=220, stretch="always")
        self.after(120, lambda: self._set_sash(split, 0, 0.38))

        # Plain frame — not CTkScrollableFrame (settings must stay visible)
        ctrl = ctk.CTkFrame(controls_host, fg_color=C["surface"])
        ctrl.pack(fill="x", padx=6, pady=(6, 2))

        # First-name letters: default full A–Z; Indian abbreviated is optional
        self.nsopw_first_mode_var = ctk.StringVar(value="initials")
        self.nsopw_db_path = self.db_path
        self.nsopw_html_dir = "data/report_pages"
        self._nsopw_insert_count = 0

        # StringVars (blank max = unlimited)
        self.nsopw_max_searches = ctk.StringVar(value="40")
        self.nsopw_max_reports = ctk.StringVar(value="80")
        self.nsopw_search_delay = ctk.DoubleVar(value=3.0)
        self.nsopw_report_delay = ctk.DoubleVar(value=0.75)
        self.nsopw_enrich = ctk.BooleanVar(value=True)
        self.nsopw_save_html = ctk.BooleanVar(value=True)
        self.nsopw_skip_existing = ctk.BooleanVar(value=True)
        # Default: never re-run finished first+last API queries.
        # Check this only when you intentionally want to repeat old searches.
        self.nsopw_repeat_searches = ctk.BooleanVar(value=False)
        self.nsopw_new_files_only = ctk.BooleanVar(value=True)
        self.nsopw_limit_surnames = ctk.BooleanVar(value=False)
        self.nsopw_surnames_limit = ctk.IntVar(value=15)

        panel = _card(ctrl)
        panel.pack(fill="x", padx=2, pady=2)

        # Row 1: ethnicity · subcategory · surname limit
        r1 = ctk.CTkFrame(panel, fg_color="transparent")
        r1.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(r1, text="Surname list", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 6)
        )
        self.nsopw_ethnicity = ctk.StringVar(value="hispanic")
        self.nsopw_eth_combo = ctk.CTkComboBox(
            r1,
            variable=self.nsopw_ethnicity,
            width=160,
            values=[
                "hispanic",
                "asian",
                "indian",
                "indian_high_confidence",
                "african_american",
                "african",
                "arabic",
                "jewish",
                "portuguese",
                "native_american",
                "european",
                "all",
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=self._nsopw_on_ethnicity_change,
        )
        self.nsopw_eth_combo.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(r1, text="Subcategory", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 6)
        )
        self.nsopw_subcategory = ctk.StringVar(value="all")
        self.nsopw_sub_combo = ctk.CTkComboBox(
            r1,
            variable=self.nsopw_subcategory,
            width=140,
            values=["all"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=self._nsopw_on_subcategory_change,
            state="disabled",
        )
        self.nsopw_sub_combo.pack(side="left", padx=(0, 12))
        ctk.CTkCheckBox(
            r1, text="Limit surnames/group",
            variable=self.nsopw_limit_surnames, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
            command=self._nsopw_toggle_surname_cap,
        ).pack(side="left", padx=(0, 6))
        self.nsopw_surnames_entry = ctk.CTkEntry(
            r1, textvariable=self.nsopw_surnames_limit, width=56,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            state="disabled",
        )
        self.nsopw_surnames_entry.pack(side="left")
        self.nsopw_surnames_entry.bind("<KeyRelease>", lambda _e: self._nsopw_update_surname_count())
        self.nsopw_surnames_entry.bind("<FocusOut>", lambda _e: self._nsopw_update_surname_count())

        self.nsopw_surname_count_label = ctk.CTkLabel(
            panel, text="Surnames to search: —", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.nsopw_surname_count_label.pack(fill="x", padx=14, pady=(0, 4))
        self._nsopw_refresh_subcategories()

        # Row 1b: first-name letter set (biggest lever on query count)
        r1b = ctk.CTkFrame(panel, fg_color="transparent")
        r1b.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkLabel(r1b, text="First letters", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 6)
        )
        self.nsopw_first_mode_combo = ctk.CTkComboBox(
            r1b,
            variable=self.nsopw_first_mode_var,
            width=280,
            values=[
                "initials",      # A–Z (default — full coverage)
                "indian",        # ASRPMKVNBD — Indian first-name letters
                "indian_wide",   # +GJHT
            ],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
            command=lambda _c=None: self._nsopw_update_surname_count(),
        )
        self.nsopw_first_mode_combo.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            r1b,
            text="default A–Z+all digraphs · indian = abbreviate firsts AND surname digraphs",
            font=FONT_SM, text_color=C["dim"],
        ).pack(side="left")

        # Row 2: limits + delays
        r2 = ctk.CTkFrame(panel, fg_color="transparent")
        r2.pack(fill="x", padx=12, pady=4)
        for label, var, width in (
            ("Max searches", self.nsopw_max_searches, 64),
            ("Max names", self.nsopw_max_reports, 64),
        ):
            ctk.CTkLabel(r2, text=label, font=FONT_SM, text_color=C["muted"]).pack(
                side="left", padx=(0, 4)
            )
            ctk.CTkEntry(
                r2, textvariable=var, width=width,
                fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
                placeholder_text="∞",
            ).pack(side="left", padx=(0, 12))
        for label, var in (
            ("Search delay", self.nsopw_search_delay),
            ("Report delay", self.nsopw_report_delay),
        ):
            ctk.CTkLabel(r2, text=label, font=FONT_SM, text_color=C["muted"]).pack(
                side="left", padx=(0, 4)
            )
            ctk.CTkEntry(
                r2, textvariable=var, width=56,
                fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            ).pack(side="left", padx=(0, 12))

        # Row 3: option checkboxes in one line (wraps if needed)
        r3 = ctk.CTkFrame(panel, fg_color="transparent")
        r3.pack(fill="x", padx=12, pady=4)
        for text, var in (
            ("Fetch detail sheets", self.nsopw_enrich),
            ("Archive HTML", self.nsopw_save_html),
            ("Repeat old searches", self.nsopw_repeat_searches),
            ("Skip known URLs", self.nsopw_skip_existing),
            ("New HTML only", self.nsopw_new_files_only),
        ):
            ctk.CTkCheckBox(
                r3, text=text, variable=var, font=FONT_SM, text_color=C["text"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
                checkmark_color=C["bg"], border_color=C["border"],
            ).pack(side="left", padx=(0, 12))

        _muted(
            panel,
            "During a run: max searches/names, delays, and the checkboxes above apply "
            "immediately. Ethnicity / subcategory / surname list apply on the next Start.",
        ).pack(anchor="w", padx=14, pady=(0, 4))

        # Push live knobs into a thread-safe snapshot whenever the user edits them
        self._nsopw_bind_live_option_traces()
        self._nsopw_sync_runtime_options()

        # Row 4: actions
        act = ctk.CTkFrame(panel, fg_color="transparent")
        act.pack(fill="x", padx=12, pady=(6, 4))
        self.nsopw_start_btn = ctk.CTkButton(
            act, text="Start NSOPW search", height=36, font=FONT_BOLD,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
            command=self._start_nsopw,
        )
        self.nsopw_start_btn.pack(side="left", padx=(0, 8))
        self.nsopw_cancel_btn = ctk.CTkButton(
            act, text="Cancel", height=36, width=90, state="disabled",
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._cancel_nsopw,
        )
        self.nsopw_cancel_btn.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            act, text="Open data folder", height=36,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_open_data_folder,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            act, text="Clear table", height=36, width=90,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_clear_tree,
        ).pack(side="left")

        # Progress + live stats
        prog_row = ctk.CTkFrame(panel, fg_color="transparent")
        prog_row.pack(fill="x", padx=12, pady=(6, 2))
        self.nsopw_eta_label = ctk.CTkLabel(
            prog_row, text="ETA —", font=FONT_SM, text_color=C["muted"], width=120, anchor="e",
        )
        self.nsopw_eta_label.pack(side="right", padx=(8, 0))
        self.nsopw_progress_label = ctk.CTkLabel(
            prog_row, text="0%", font=FONT_SM, text_color=C["accent"], width=44, anchor="e",
        )
        self.nsopw_progress_label.pack(side="right", padx=(8, 0))
        self.nsopw_progress = ctk.CTkProgressBar(
            prog_row, mode="determinate", progress_color=C["accent"],
            fg_color=C["elevated"], height=10,
        )
        self.nsopw_progress.pack(side="left", fill="x", expand=True)
        self.nsopw_progress.set(0)
        self._nsopw_run_t0: Optional[float] = None
        self._nsopw_eta_samples: List[Tuple[float, float]] = []  # (monotonic, work_done)

        stats_row = ctk.CTkFrame(panel, fg_color=C["elevated"], corner_radius=8)
        stats_row.pack(fill="x", padx=12, pady=(4, 2))
        self._nsopw_stat_vars: Dict[str, ctk.CTkLabel] = {}
        for key, title in (
            ("plan", "Plan"),
            ("searches", "Searches"),
            ("matched", "Matched"),
            ("other", "Other"),
            ("hits", "Hits"),
            ("html", "HTML"),
            ("photos", "Photos"),
            ("race", "Race"),
        ):
            cell = ctk.CTkFrame(stats_row, fg_color="transparent")
            cell.pack(side="left", padx=10, pady=6)
            ctk.CTkLabel(
                cell, text=title, font=FONT_SM, text_color=C["dim"], anchor="w",
            ).pack(anchor="w")
            val = ctk.CTkLabel(
                cell, text="—", font=FONT_BOLD, text_color=C["text"], anchor="w",
            )
            val.pack(anchor="w")
            self._nsopw_stat_vars[key] = val

        # Always-visible current NSOPW query (first + last terms)
        search_row = ctk.CTkFrame(panel, fg_color=C["elevated"], corner_radius=8)
        search_row.pack(fill="x", padx=12, pady=(4, 2))
        ctk.CTkLabel(
            search_row, text="Current search", font=FONT_SM, text_color=C["dim"],
        ).pack(side="left", padx=(12, 8), pady=8)
        self.nsopw_current_search_label = ctk.CTkLabel(
            search_row,
            text="—",
            font=FONT_BOLD,
            text_color=C["accent"],
            anchor="w",
        )
        self.nsopw_current_search_label.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=8)
        self._nsopw_last_search_terms = ""

        self.nsopw_status = ctk.CTkLabel(
            panel,
            text="Ready · A–Z first + short last prefixes (Settings) · blank max = unlimited",
            font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.nsopw_status.pack(fill="x", padx=12, pady=(2, 10))
        self._nsopw_reset_progress_ui()

        # Recent inserts below sash — independent of controls scroll frame.
        prev = _card(inserts_host)
        prev.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        _section_label(
            prev,
            "Recent inserts (live) · select row for photo · double-click HTML/photo/URL",
        ).pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            prev,
            "Primary tab: surnames in the selected ethnicity list. "
            "Other surnames tab: still saved to DB/HTML. "
            "Photos are saved with report HTML (images embedded for offline viewing).",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        # Resizable: tables | detail drawer (photo + crime + links)
        inserts_split = _hpaned(prev)
        inserts_split.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        tables_host = ctk.CTkFrame(inserts_split, fg_color="transparent")
        inserts_split.add(tables_host, minsize=320, stretch="always")
        self.nsopw_detail = self._make_detail_drawer(inserts_split)
        inserts_split.add(self.nsopw_detail, minsize=220, stretch="never")
        self.after(200, lambda: self._set_sash(inserts_split, 0, 0.72))

        insert_tabs = ctk.CTkTabview(
            tables_host,
            fg_color=C["panel"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["border"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["border"],
            text_color=C["text"],
        )
        insert_tabs.pack(fill="both", expand=True, padx=0, pady=0)
        tab_matched = insert_tabs.add("Ethnicity match")
        tab_other = insert_tabs.add("Other surnames")
        self.nsopw_insert_tabs = insert_tabs

        cols = ["name", "state", "race", "crime", "photo", "url", "html"]
        col_labels = {
            "name": "NAME",
            "state": "STATE",
            "race": "RACE",
            "crime": "CRIME",
            "photo": "PHOTO",
            "url": "URL",
            "html": "HTML",
        }
        col_widths = [120, 48, 90, 160, 50, 180, 120]

        def _setup_insert_tree(parent) -> ttk.Treeview:
            wrap, tree = _tree_frame(parent)
            wrap.pack(fill="both", expand=True, padx=4, pady=4)
            tree.configure(columns=cols, show="headings")
            _stretch_columns(tree, cols, col_widths)
            _enable_tree_column_sort(tree, list(cols), labels=col_labels)
            _bind_tree_scroll_isolation(tree, wrap)
            tree.bind("<Double-1>", self._nsopw_open_selected)
            tree.bind("<<TreeviewSelect>>", self._nsopw_on_tree_select)
            return tree

        self.nsopw_tree = _setup_insert_tree(tab_matched)
        self.nsopw_tree_other = _setup_insert_tree(tab_other)
        self._nsopw_insert_count = 0
        self._nsopw_other_count = 0
        # iid -> full record (for detail drawer) + photo path map
        self._nsopw_records_by_iid: Dict[str, Dict[str, Any]] = {}
        self._nsopw_photo_by_iid: Dict[str, str] = {}

    def _nsopw_clear_tree(self):
        self.nsopw_tree.delete(*self.nsopw_tree.get_children())
        if getattr(self, "nsopw_tree_other", None) is not None:
            self.nsopw_tree_other.delete(*self.nsopw_tree_other.get_children())
        self._nsopw_insert_count = 0
        self._nsopw_other_count = 0
        self._nsopw_photo_by_iid = {}
        self._nsopw_records_by_iid = {}
        if getattr(self, "nsopw_detail", None) is not None:
            self._fill_detail_drawer(self.nsopw_detail, None)

    def _nsopw_toggle_surname_cap(self):
        """Enable max-surnames entry only when the limit toggle is on."""
        if self.nsopw_limit_surnames.get():
            self.nsopw_surnames_entry.configure(state="normal")
        else:
            self.nsopw_surnames_entry.configure(state="disabled")
        self._nsopw_update_surname_count()

    def _nsopw_on_ethnicity_change(self, _choice=None):
        self._nsopw_refresh_subcategories()
        self._nsopw_update_surname_count()

    def _nsopw_on_subcategory_change(self, _choice=None):
        self._nsopw_update_surname_count()

    def _nsopw_refresh_subcategories(self):
        """Reload subcategory dropdown for the current ethnicity."""
        from scraper.ethnic_names import get_ethnic_database

        eth = (self.nsopw_ethnicity.get() or "hispanic").strip().lower()
        db = get_ethnic_database()
        subs = db.subcategories(eth)
        if not subs:
            subs = ["all"]
        self.nsopw_sub_combo.configure(values=subs)
        # Default to all when list changes
        self.nsopw_subcategory.set("all" if "all" in subs else subs[0])
        # Enable only when real subgroups exist
        if db.has_subcategories(eth):
            self.nsopw_sub_combo.configure(state="normal")
        else:
            self.nsopw_sub_combo.configure(state="disabled")

    def _nsopw_surname_selection_params(self) -> tuple:
        """Return (ethnicity, subcategory, all_surnames, surnames_limit)."""
        eth = (self.nsopw_ethnicity.get() or "hispanic").strip().lower()
        sub = (self.nsopw_subcategory.get() or "all").strip().lower()
        limit_on = bool(self.nsopw_limit_surnames.get())
        all_surnames = not limit_on
        try:
            surnames_limit = int(self.nsopw_surnames_limit.get()) if limit_on else 0
        except (TypeError, ValueError):
            surnames_limit = 15 if limit_on else 0
        return eth, sub, all_surnames, surnames_limit

    def _nsopw_update_surname_count(self):
        """Show how many unique surnames the current filters select."""
        try:
            from scraper.ethnic_names import get_ethnic_database
            from scraper.nsopw_builder import (
                FIRST_INITIALS,
                NSOPWEthnicDatabaseBuilder,
                describe_first_mode,
                estimate_compact_query_count,
                first_initials_for_mode,
            )

            eth, sub, all_surnames, surnames_limit = self._nsopw_surname_selection_params()
            first_mode = (
                (self.nsopw_first_mode_var.get() or "initials").strip().lower()
                if hasattr(self, "nsopw_first_mode_var")
                else "initials"
            )
            firsts = first_initials_for_mode(first_mode)
            # Avoid full builder init (HTTP clients) — only need ethnic_db for selection
            light = object.__new__(NSOPWEthnicDatabaseBuilder)
            light.ethnic_db = get_ethnic_database()
            pairs = NSOPWEthnicDatabaseBuilder.surnames_for_ethnicity(
                light,
                eth,
                limit_per_group=surnames_limit,
                all_surnames=all_surnames,
                subcategory=sub,
            )
            n = len(pairs)
            naive = n * len(firsts)
            az_naive = n * len(FIRST_INITIALS)
            use_compact = bool(self.app_settings.get("nsopw_compact_prefixes", True))
            if hasattr(self, "settings_compact_prefixes"):
                use_compact = bool(self.settings_compact_prefixes.get())
            try:
                mcl = int(self.app_settings.get("nsopw_min_combined_len", 3))
                if hasattr(self, "settings_min_combined"):
                    mcl = int(str(self.settings_min_combined.get()).strip() or "3")
            except (TypeError, ValueError):
                mcl = 3
            mcl = max(3, min(mcl, 10))
            from scraper.nsopw_builder import (
                is_abbreviated_first_mode,
                last_prefix_whitelist_for,
            )
            last_allow = last_prefix_whitelist_for(
                eth,
                pairs,
                abbreviated=is_abbreviated_first_mode(first_mode),
                mode=first_mode,
            )
            if use_compact:
                est = estimate_compact_query_count(
                    pairs, firsts, min_combined=mcl, allowed_last_prefixes=last_allow
                )
                est_az = estimate_compact_query_count(
                    pairs, FIRST_INITIALS, min_combined=mcl
                )
                mode_txt = (
                    f"Est. queries: {est:,}  ·  {describe_first_mode(first_mode)}"
                )
                if first_mode not in ("initials", "all", "") and est_az != est:
                    mode_txt += f"  (A–Z would be {est_az:,})"
            else:
                est = naive
                mode_txt = (
                    f"Est. queries: {est:,} full surnames × {len(firsts)} firsts"
                )
                if first_mode not in ("initials", "all", "") and az_naive != est:
                    mode_txt += f"  (A–Z would be {az_naive:,})"
            scope = f"{eth}" + (f" / {sub}" if sub and sub != "all" else " / all groups")
            self.nsopw_surname_count_label.configure(
                text=f"Surnames in list: {n:,}  ({scope})  ·  {mode_txt}"
            )
        except Exception as e:
            self.nsopw_surname_count_label.configure(
                text=f"Surnames to search: (error computing count: {e})"
            )

    def _nsopw_append_row(self, record: Dict[str, Any]) -> None:
        """UI-thread: route insert into ethnicity-match or other-surnames table."""
        name = (
            (record.get("full_name") or "").strip()
            or f"{record.get('first_name') or ''} {record.get('last_name') or ''}".strip()
        )
        race = (record.get("race") or "").strip()
        eth = (record.get("ethnicity") or "").strip()
        race_disp = race
        if eth and eth.lower() != race.lower():
            race_disp = f"{race} / {eth}" if race else eth
        if not race_disp:
            race_disp = "—"
        photo_path = (record.get("photo_path") or "").strip()
        photo_mark = "yes" if photo_path and Path(photo_path).is_file() else (
            "url" if (record.get("photo_url") or "").strip() else "—"
        )
        crime = (
            (record.get("crime") or record.get("offense_description") or record.get("offense_type") or "")
            .strip()
            or "—"
        )
        vals = (
            name,
            record.get("state") or record.get("source_state") or "",
            race_disp,
            crime,
            photo_mark,
            record.get("source_url") or "",
            record.get("report_html_path") or "",
        )

        bucket = (record.get("nsopw_result_bucket") or "").strip().lower()
        if not bucket:
            # Fallback from flags JSON if builder field missing
            try:
                flags = record.get("flags")
                fl = json.loads(flags) if isinstance(flags, str) else (flags or [])
                if "other_surname" in fl:
                    bucket = "other"
                else:
                    bucket = "matched"
            except Exception:
                bucket = "matched"
        is_other = bucket == "other"
        tree = self.nsopw_tree_other if is_other else self.nsopw_tree

        sort_state = getattr(tree, "_sort_state", None) or {}
        if sort_state.get("col"):
            iid = tree.insert("", "end", values=vals)
        else:
            iid = tree.insert("", 0, values=vals)
        self._nsopw_records_by_iid[iid] = dict(record)
        if photo_path:
            self._nsopw_photo_by_iid[iid] = photo_path
        # Cap live table size
        kids = tree.get_children()
        if len(kids) > 200:
            for drop in kids[200:]:
                self._nsopw_photo_by_iid.pop(drop, None)
                self._nsopw_records_by_iid.pop(drop, None)
                tree.delete(drop)
        reapply = getattr(tree, "_reapply_sort", None)
        if callable(reapply) and sort_state.get("col"):
            reapply()

        if is_other:
            self._nsopw_other_count += 1
        else:
            self._nsopw_insert_count += 1
        # Keep chip stats in sync with live inserts (progress callback may lag)
        if hasattr(self, "_nsopw_stat_vars"):
            try:
                self._nsopw_stat_vars["matched"].configure(text=str(self._nsopw_insert_count))
                self._nsopw_stat_vars["other"].configure(text=str(self._nsopw_other_count))
            except Exception:
                pass
        # Do not wipe the current-search line — keep last query terms visible
        terms = getattr(self, "_nsopw_last_search_terms", "") or ""
        if terms:
            self.nsopw_status.configure(
                text=(
                    f"Running… {terms} · matched {self._nsopw_insert_count} · "
                    f"other {self._nsopw_other_count} (live)"
                )
            )
        else:
            self.nsopw_status.configure(
                text=(
                    f"Running… matched {self._nsopw_insert_count} · "
                    f"other surnames {self._nsopw_other_count} (live)"
                )
            )

    def _nsopw_on_tree_select(self, event=None):
        tree = event.widget if event is not None else self.nsopw_tree
        sel = tree.selection() if isinstance(tree, ttk.Treeview) else ()
        if not sel:
            return
        iid = sel[0]
        rec = self._nsopw_records_by_iid.get(iid)
        if rec is None:
            rec = {}
        # Attach photo from map / HTML assets if missing
        path = self._nsopw_photo_by_iid.get(iid) or (rec.get("photo_path") or "").strip()
        if not path or not Path(path).is_file():
            vals = tree.item(iid, "values")
            html_path = vals[-1] if len(vals) >= 5 else ""
            if html_path and html_path != "—":
                hp = Path(str(html_path))
                assets = hp.parent / f"{hp.stem}_assets"
                if assets.is_dir():
                    for cand in sorted(assets.iterdir()):
                        if cand.suffix.lower() in (
                            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"
                        ) and cand.stat().st_size > 80:
                            path = str(cand)
                            self._nsopw_photo_by_iid[iid] = path
                            rec = dict(rec)
                            rec["photo_path"] = path
                            self._nsopw_records_by_iid[iid] = rec
                            break
        elif path and not rec.get("photo_path"):
            rec = dict(rec)
            rec["photo_path"] = path
            self._nsopw_records_by_iid[iid] = rec
        if getattr(self, "nsopw_detail", None) is not None:
            self._fill_detail_drawer(self.nsopw_detail, rec or None)

    def _nsopw_open_data_folder(self):
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _open_path(self, path: Path):
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Cannot open", str(e))

    @staticmethod
    def _format_eta(seconds: Optional[float]) -> str:
        """Human ETA string from seconds remaining (None → still calculating)."""
        if seconds is None:
            return "ETA …"
        try:
            s = max(0, int(round(float(seconds))))
        except (TypeError, ValueError):
            return "ETA …"
        if s < 5:
            return "ETA <5s"
        if s < 60:
            return f"ETA ~{s}s"
        mins, sec = divmod(s, 60)
        if mins < 60:
            return f"ETA ~{mins}m {sec:02d}s" if sec else f"ETA ~{mins}m"
        hours, mins = divmod(mins, 60)
        if hours < 48:
            return f"ETA ~{hours}h {mins:02d}m"
        days, hours = divmod(hours, 24)
        return f"ETA ~{days}d {hours}h"

    def _nsopw_estimate_eta_seconds(self, info: Dict[str, Any]) -> Optional[float]:
        """
        Estimate remaining runtime from observed pace.

        Prefers new-search throughput when max_searches is set; otherwise plan
        steps. Blends wall-clock rate with configured search_delay as a floor.
        """
        import time as _time

        t0 = getattr(self, "_nsopw_run_t0", None)
        if t0 is None:
            return None
        now = _time.monotonic()
        elapsed = now - t0
        if elapsed < 1.0:
            return None

        searches = int(info.get("searches") or 0)
        plan_i = int(info.get("plan_i") or 0)
        plan_total = int(info.get("plan_total") or 0)
        search_cap = info.get("search_cap")
        try:
            search_delay = float(info.get("search_delay") or self.nsopw_search_delay.get() or 3.0)
        except (TypeError, ValueError):
            search_delay = 3.0
        search_delay = max(2.0, search_delay)

        # Work unit: new searches under a cap, else plan cursor
        remaining: Optional[float] = None
        rate: Optional[float] = None  # units per second

        if search_cap is not None:
            try:
                cap = int(search_cap)
            except (TypeError, ValueError):
                cap = 0
            if cap > 0:
                remaining = max(0.0, float(cap - searches))
                if searches >= 1:
                    rate = searches / elapsed
        if remaining is None and plan_total > 0:
            remaining = max(0.0, float(plan_total - plan_i))
            if plan_i >= 1:
                rate = plan_i / elapsed

        if remaining is not None and remaining <= 0:
            return 0.0
        if remaining is None:
            return None

        # Pace from recent samples (smoother than whole-run average)
        samples = getattr(self, "_nsopw_eta_samples", None)
        if samples is None:
            self._nsopw_eta_samples = []
            samples = self._nsopw_eta_samples
        work_done = float(searches if search_cap is not None else plan_i)
        samples.append((now, work_done))
        # Keep ~2 minutes of samples
        cutoff = now - 120.0
        self._nsopw_eta_samples = [(t, w) for t, w in samples if t >= cutoff]
        samples = self._nsopw_eta_samples
        if len(samples) >= 2:
            t_a, w_a = samples[0]
            t_b, w_b = samples[-1]
            dt = t_b - t_a
            dw = w_b - w_a
            if dt >= 3.0 and dw > 0:
                recent_rate = dw / dt
                rate = recent_rate if rate is None else (0.4 * rate + 0.6 * recent_rate)

        if rate is None or rate <= 0:
            # Before first completed unit: lower-bound from configured delay
            if search_cap is not None and remaining is not None:
                return remaining * search_delay
            return None

        eta = remaining / rate
        # Don't estimate faster than delay allows for remaining *new* searches
        if search_cap is not None:
            eta = max(eta, remaining * search_delay * 0.85)
        # Cap absurd estimates
        if eta > 7 * 24 * 3600:
            eta = 7 * 24 * 3600
        return eta

    def _nsopw_reset_progress_ui(self) -> None:
        """Zero the progress bar and statistic chips."""
        try:
            self.nsopw_progress.set(0)
            if hasattr(self, "nsopw_progress_label"):
                self.nsopw_progress_label.configure(text="0%")
            if hasattr(self, "nsopw_eta_label"):
                self.nsopw_eta_label.configure(text="ETA —")
            if hasattr(self, "nsopw_current_search_label"):
                self.nsopw_current_search_label.configure(text="—")
            self._nsopw_last_search_terms = ""
            self._nsopw_eta_samples = []
            for key, lbl in getattr(self, "_nsopw_stat_vars", {}).items():
                lbl.configure(text="0" if key != "plan" else "—")
        except Exception:
            pass

    def _nsopw_update_progress(self, info: Dict[str, Any]) -> None:
        """UI-thread: update determinate progress bar + stat chips + ETA."""
        try:
            done = float(info.get("done") or info.get("plan_i") or 0)
            total = float(info.get("total") or info.get("plan_total") or 0)
            # Prefer search-cap progress when available (matches live max searches)
            sc = info.get("search_cap")
            searches = int(info.get("searches") or 0)
            if sc is not None:
                try:
                    sc_n = float(sc)
                    if sc_n > 0:
                        total = sc_n
                        done = float(searches)
                except (TypeError, ValueError):
                    pass
            if total <= 0:
                frac = 0.0
            else:
                frac = min(1.0, max(0.0, done / total))
            self.nsopw_progress.set(frac)
            if hasattr(self, "nsopw_progress_label"):
                self.nsopw_progress_label.configure(text=f"{int(round(frac * 100))}%")

            eta_sec = self._nsopw_estimate_eta_seconds(info)
            eta_txt = self._format_eta(eta_sec)
            if hasattr(self, "nsopw_eta_label"):
                phase0 = (info.get("phase") or "").strip()
                if phase0 == "done":
                    self.nsopw_eta_label.configure(text="ETA done")
                elif phase0 == "cancelled":
                    self.nsopw_eta_label.configure(text="ETA —")
                else:
                    self.nsopw_eta_label.configure(text=eta_txt)

            plan_i = int(info.get("plan_i") or 0)
            plan_total = int(info.get("plan_total") or 0)
            skipped = int(info.get("searches_skipped") or 0)
            matched = int(info.get("inserted_matched") or self._nsopw_insert_count or 0)
            other = int(info.get("inserted_other") or self._nsopw_other_count or 0)
            hits = int(info.get("search_hits") or 0)
            html = int(info.get("html_saved") or 0)
            photos = int(info.get("photos_saved") or 0)
            race = int(info.get("reports_with_race") or 0)

            vars_ = getattr(self, "_nsopw_stat_vars", {})
            if "plan" in vars_:
                vars_["plan"].configure(
                    text=f"{plan_i}/{plan_total}" if plan_total else str(plan_i)
                )
            if "searches" in vars_:
                cap = info.get("search_cap")
                cap_s = f"/{cap}" if cap is not None else ""
                vars_["searches"].configure(
                    text=f"{searches}{cap_s}" + (f" (+{skipped} skip)" if skipped else "")
                )
            if "matched" in vars_:
                vars_["matched"].configure(text=str(matched))
            if "other" in vars_:
                vars_["other"].configure(text=str(other))
            if "hits" in vars_:
                vars_["hits"].configure(text=str(hits))
            if "html" in vars_:
                vars_["html"].configure(text=str(html))
            if "photos" in vars_:
                vars_["photos"].configure(text=str(photos))
            if "race" in vars_:
                vars_["race"].configure(text=str(race))

            phase = (info.get("phase") or "").strip()
            current = (info.get("current") or "").strip()
            # Structured search terms (preferred) or free-text current
            sf = (info.get("search_first") or "").strip()
            sl = (info.get("search_last") or "").strip()
            covers = (info.get("search_covers") or "").strip()
            lab = (info.get("search_label") or "").strip()
            if sf or sl:
                terms = f"first='{sf}' last='{sl}'"
                if covers:
                    terms += f" · covers {covers}"
                if lab:
                    terms += f" · {lab}"
            else:
                terms = current
            if terms and phase not in ("done", "cancelled", "start"):
                self._nsopw_last_search_terms = terms
            if hasattr(self, "nsopw_current_search_label"):
                if phase == "done":
                    self.nsopw_current_search_label.configure(text="complete")
                elif phase == "cancelled":
                    self.nsopw_current_search_label.configure(text="cancelled")
                elif phase == "start" or not terms:
                    self.nsopw_current_search_label.configure(text="starting…")
                elif phase == "resume_skip":
                    self.nsopw_current_search_label.configure(
                        text=f"skip {terms}" if terms else "skip…"
                    )
                else:
                    self.nsopw_current_search_label.configure(text=terms or "—")

            if phase == "done":
                pass  # status set by completion handler
            elif phase == "cancelled":
                self.nsopw_status.configure(text="Cancelled")
            elif terms or current:
                display = terms or current
                self.nsopw_status.configure(
                    text=(
                        f"Running… search {display} · {eta_txt} · "
                        f"matched {matched} · other {other} · "
                        f"plan {plan_i}/{plan_total or '—'}"
                    )
                )
        except Exception:
            pass

    def _cancel_nsopw(self):
        self._nsopw_cancel = True
        self.log_queue.put("NSOPW cancel requested… (stops within ~50ms of delay)")
        try:
            self.nsopw_status.configure(text="Cancelling… stopping ASAP")
            if hasattr(self, "nsopw_current_search_label"):
                self.nsopw_current_search_label.configure(text="cancelling…")
            if hasattr(self, "nsopw_eta_label"):
                self.nsopw_eta_label.configure(text="ETA —")
        except Exception:
            pass

    def _nsopw_parse_optional_limit(self, raw: Any) -> Optional[int]:
        """Blank / 0 / non-numeric → None (unlimited)."""
        text = (str(raw) if raw is not None else "").strip()
        if not text:
            return None
        try:
            n = int(text)
        except (TypeError, ValueError):
            return None
        return None if n <= 0 else n

    def _nsopw_capture_runtime_options(self) -> Dict[str, Any]:
        """Read current NSOPW operational knobs from the UI (main thread)."""
        try:
            search_delay = max(2.0, float(self.nsopw_search_delay.get()))
        except (TypeError, ValueError):
            search_delay = 3.0
        try:
            report_delay = max(0.25, float(self.nsopw_report_delay.get()))
        except (TypeError, ValueError):
            report_delay = 0.75
        repeat_old = bool(self.nsopw_repeat_searches.get())
        return {
            "max_searches": self._nsopw_parse_optional_limit(self.nsopw_max_searches.get()),
            "max_names": self._nsopw_parse_optional_limit(self.nsopw_max_reports.get()),
            "search_delay": search_delay,
            "report_delay": report_delay,
            "enrich_reports": bool(self.nsopw_enrich.get()),
            "save_html": bool(self.nsopw_save_html.get()),
            "skip_existing_urls": bool(self.nsopw_skip_existing.get()),
            "skip_completed_searches": not repeat_old,
            "new_files_only": bool(self.nsopw_new_files_only.get()),
        }

    def _nsopw_sync_runtime_options(self, *_args: Any) -> None:
        """Main-thread: snapshot UI options for the worker."""
        try:
            snap = self._nsopw_capture_runtime_options()
        except Exception:
            return
        with self._nsopw_runtime_lock:
            self._nsopw_runtime = snap

    def _nsopw_live_options(self) -> Dict[str, Any]:
        """Worker-thread: copy of latest operational knobs."""
        with self._nsopw_runtime_lock:
            return dict(self._nsopw_runtime)

    def _nsopw_bind_live_option_traces(self) -> None:
        """Re-sync runtime snapshot whenever the user edits live knobs."""
        if getattr(self, "_nsopw_live_traces_bound", False):
            return
        self._nsopw_live_traces_bound = True
        vars_ = [
            self.nsopw_max_searches,
            self.nsopw_max_reports,
            self.nsopw_search_delay,
            self.nsopw_report_delay,
            self.nsopw_enrich,
            self.nsopw_save_html,
            self.nsopw_repeat_searches,
            self.nsopw_skip_existing,
            self.nsopw_new_files_only,
        ]
        for v in vars_:
            try:
                v.trace_add("write", self._nsopw_sync_runtime_options)
            except Exception:
                try:
                    v.trace("w", self._nsopw_sync_runtime_options)  # type: ignore[attr-defined]
                except Exception:
                    pass

    def _start_nsopw(self):
        if self.is_running:
            return

        db_path = self.nsopw_db_path
        html_dir = self.nsopw_html_dir
        # Snapshot plan + initial knobs (plan is fixed; knobs stay live via callback)
        self._nsopw_sync_runtime_options()
        live0 = self._nsopw_live_options()
        search_delay = float(live0.get("search_delay") or 3.0)
        report_delay = float(live0.get("report_delay") or 0.75)
        eth, sub, all_surnames, surnames_limit = self._nsopw_surname_selection_params()
        # Settings tab: compact 3-letter partials (default on)
        use_compact = bool(self.app_settings.get("nsopw_compact_prefixes", True))
        if hasattr(self, "settings_compact_prefixes"):
            use_compact = bool(self.settings_compact_prefixes.get())
        try:
            min_combined = int(self.app_settings.get("nsopw_min_combined_len", 3))
            if hasattr(self, "settings_min_combined"):
                min_combined = int(str(self.settings_min_combined.get()).strip() or "3")
        except (TypeError, ValueError):
            min_combined = 3
        min_combined = max(3, min(min_combined, 10))

        self._nsopw_cancel = False
        self._nsopw_insert_count = 0
        self._nsopw_other_count = 0
        self._set_running(True)
        self.nsopw_start_btn.configure(state="disabled")
        self.nsopw_cancel_btn.configure(state="normal")
        self._nsopw_reset_progress_ui()
        import time as _time

        self._nsopw_run_t0 = _time.monotonic()
        self._nsopw_eta_samples = []
        if hasattr(self, "nsopw_eta_label"):
            self.nsopw_eta_label.configure(text="ETA …")
        self.nsopw_status.configure(
            text="Running NSOPW search… (edit delays/caps/checkboxes anytime)"
        )
        self.nsopw_tree.delete(*self.nsopw_tree.get_children())
        if getattr(self, "nsopw_tree_other", None) is not None:
            self.nsopw_tree_other.delete(*self.nsopw_tree_other.get_children())
        self._nsopw_photo_by_iid = {}
        self._nsopw_records_by_iid = {}
        if getattr(self, "nsopw_detail", None) is not None:
            self._fill_detail_drawer(self.nsopw_detail, None)

        def log(msg):
            self.log_queue.put(msg)

        def on_insert(record: Dict[str, Any]) -> None:
            # Marshal to UI thread
            self.after(0, lambda r=dict(record): self._nsopw_append_row(r))

        def on_progress(info: Dict[str, Any]) -> None:
            self.after(0, lambda d=dict(info): self._nsopw_update_progress(d))

        def worker():
            from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder

            builder = NSOPWEthnicDatabaseBuilder(
                db_path=db_path,
                delay=search_delay,
                report_delay=report_delay,
                html_dir=html_dir,
                cancel_check=lambda: self._nsopw_cancel,
            )
            try:
                stats = builder.build(
                    ethnicity=eth,
                    surnames_limit=surnames_limit,
                    all_surnames=all_surnames,
                    subcategory=sub,
                    first_names=None,
                    first_mode=(
                        (self.nsopw_first_mode_var.get() or "initials").strip().lower()
                        if hasattr(self, "nsopw_first_mode_var")
                        else "initials"
                    ),
                    jurisdictions=None,
                    max_searches=live0.get("max_searches"),
                    max_names=live0.get("max_names"),
                    skip_existing_urls=bool(live0.get("skip_existing_urls", True)),
                    skip_completed_searches=bool(live0.get("skip_completed_searches", True)),
                    new_files_only=bool(live0.get("new_files_only", True)),
                    enrich_reports=bool(live0.get("enrich_reports", True)),
                    save_html=bool(live0.get("save_html", True)),
                    use_compact_prefixes=use_compact,
                    min_combined_len=min_combined,
                    log=log,
                    on_insert=on_insert,
                    on_progress=on_progress,
                    live_options=self._nsopw_live_options,
                )

                def done():
                    self._set_running(False)
                    self.nsopw_start_btn.configure(state="normal")
                    self.nsopw_cancel_btn.configure(state="disabled")
                    # Final bar + chips from completed stats
                    self._nsopw_update_progress({
                        "plan_i": getattr(stats, "searches", 0) + getattr(stats, "searches_skipped", 0),
                        "plan_total": max(
                            getattr(stats, "searches", 0) + getattr(stats, "searches_skipped", 0),
                            1,
                        ),
                        "done": 1,
                        "total": 1,
                        "searches": stats.searches,
                        "searches_skipped": stats.searches_skipped,
                        "search_hits": stats.search_hits,
                        "inserted_matched": getattr(stats, "inserted_matched", stats.inserted),
                        "inserted_other": getattr(stats, "inserted_other", 0),
                        "html_saved": stats.html_saved,
                        "photos_saved": getattr(stats, "photos_saved", 0),
                        "reports_with_race": stats.reports_with_race,
                        "current": "complete",
                        "phase": "done",
                    })
                    self.nsopw_progress.set(1.0)
                    if hasattr(self, "nsopw_progress_label"):
                        self.nsopw_progress_label.configure(text="100%")
                    if hasattr(self, "nsopw_eta_label"):
                        self.nsopw_eta_label.configure(text="ETA done")
                    matched_n = getattr(stats, "inserted_matched", stats.inserted)
                    other_n = getattr(stats, "inserted_other", 0)
                    self.nsopw_status.configure(
                        text=(
                            f"Done · matched {matched_n} · other {other_n} · "
                            f"{stats.reports_with_race} with race · "
                            f"{stats.html_saved} HTML · "
                            f"{getattr(stats, 'photos_saved', 0)} photos · "
                            f"{stats.searches} new searches · "
                            f"{stats.searches_skipped} skipped (already done)"
                        )
                    )
                    self.db_path = db_path
                    messagebox.showinfo(
                        "NSOPW complete",
                        (
                            f"Inserted {stats.inserted} "
                            f"(ethnicity match {matched_n}, other surnames {other_n})\n"
                            f"New searches: {stats.searches}\n"
                            f"Skipped completed searches: {stats.searches_skipped}\n"
                            f"Reports with race: {stats.reports_with_race}\n"
                            f"HTML saved: {stats.html_saved}\n"
                            f"Photos saved: {getattr(stats, 'photos_saved', 0)}\n"
                            f"HTML skipped (cached): {stats.reports_skipped_existing_file}\n"
                            f"{db_path}"
                        ),
                    )

                self.after(0, done)
            except Exception as e:
                log(f"NSOPW ERROR: {e}")

                def fail():
                    self._set_running(False)
                    self.nsopw_start_btn.configure(state="normal")
                    self.nsopw_cancel_btn.configure(state="disabled")
                    self.nsopw_status.configure(text=f"Error: {e}")
                    messagebox.showerror("NSOPW error", str(e))

                self.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()

    def _nsopw_open_selected(self, event=None):
        tree = event.widget if event is not None else self.nsopw_tree
        if not isinstance(tree, ttk.Treeview):
            tree = self.nsopw_tree
        sel = tree.selection()
        if not sel and getattr(self, "nsopw_tree_other", None) is not None:
            # Fallback: selection on the other tab
            for t in (self.nsopw_tree, self.nsopw_tree_other):
                if t.selection():
                    tree = t
                    sel = t.selection()
                    break
        if not sel:
            return
        iid = sel[0]
        vals = tree.item(iid, "values")
        # columns: name, state, race, crime, photo, url, html  (legacy layouts supported)
        if len(vals) >= 7:
            url, html_path = vals[5], vals[6]
        elif len(vals) >= 6:
            url, html_path = vals[4], vals[5]
        elif len(vals) >= 5:
            url, html_path = vals[3], vals[4]
        elif len(vals) >= 4:
            url, html_path = vals[2], vals[3]
        else:
            return

        photo_path = self._nsopw_photo_by_iid.get(iid)
        # Prefer opening HTML (includes embedded photos offline), then photo, then URL
        if html_path and html_path != "—":
            p = Path(html_path)
            if p.exists():
                self._open_path(p)
                return
        if photo_path and Path(photo_path).is_file():
            self._open_path(Path(photo_path))
            return
        if url:
            try:
                webbrowser.open(url)
            except Exception as e:
                messagebox.showerror("Open link", str(e))

    # -----------------------------------------------------------------------
    # Shared
    # -----------------------------------------------------------------------
    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _append_log(self, message: str):
        self.log_text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool):
        self.is_running = running
        state = "disabled" if running else "normal"
        self.scrape_btn.configure(state=state)

    def _load_sources(self):
        from scraper.config import REGISTRIES

        try:
            self.sources = REGISTRIES
            self.scrape_tree.delete(*self.scrape_tree.get_children())
            for reg in self.sources:
                if reg.abbr == "US":
                    continue
                tags = ("direct",) if reg.direct_downloads else ()
                self.scrape_tree.insert(
                    "",
                    "end",
                    text=reg.name,
                    values=(reg.abbr, reg.scrape_method.upper(), (reg.notes or "")[:70]),
                    tags=tags,
                )
            self.log_queue.put("Loaded registry configs (50 states + DC).")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _open_output_folder(self):
        path = Path(self.scrape_output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    # -----------------------------------------------------------------------
    # Settings (backups, DB path, NSOPW compact search)
    # -----------------------------------------------------------------------
    def _build_settings(self, tab):
        tab.configure(fg_color=C["surface"])
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C["surface"])
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        _wire_wide_scroll(tab, scroll)

        # --- Database ---
        db_card = _card(scroll)
        db_card.pack(fill="x", padx=4, pady=(4, 8))
        _section_label(db_card, "Database").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            db_card,
            "Primary SQLite file used by Browse, Integrity, and NSOPW inserts.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_db_path = ctk.StringVar(value=str(self.db_path))
        db_row = ctk.CTkFrame(db_card, fg_color="transparent")
        db_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkEntry(
            db_row,
            textvariable=self.settings_db_path,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            db_row,
            text="Browse…",
            width=88,
            height=32,
            command=self._settings_browse_db,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left")

        # --- Backups ---
        bak_card = _card(scroll)
        bak_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(bak_card, "Database backups").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            bak_card,
            "Optional timestamped SQLite copies. Off by default — use Backup now, or enable "
            "auto-backup on close below.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_backup_on_close = ctk.BooleanVar(
            value=bool(self.app_settings.get("backup_on_close", False))
        )
        self.settings_backup_dir = ctk.StringVar(
            value=str(self.app_settings.get("backup_dir") or "data/backups")
        )
        self.settings_max_backups = ctk.StringVar(
            value=str(int(self.app_settings.get("max_backups", 10)))
        )

        ctk.CTkCheckBox(
            bak_card,
            text="Backup database when closing the app (optional)",
            variable=self.settings_backup_on_close,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
        ).pack(anchor="w", padx=14, pady=(0, 8))

        dir_row = ctk.CTkFrame(bak_card, fg_color="transparent")
        dir_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(dir_row, text="Backup folder", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkEntry(
            dir_row,
            textvariable=self.settings_backup_dir,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
            width=320,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            dir_row,
            text="Browse…",
            width=88,
            height=32,
            command=self._settings_browse_backup_dir,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            dir_row,
            text="Open folder",
            width=100,
            height=32,
            command=self._settings_open_backup_dir,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left")

        keep_row = ctk.CTkFrame(bak_card, fg_color="transparent")
        keep_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(
            keep_row, text="Keep last N backups (0 = unlimited)", font=FONT_SM, text_color=C["muted"]
        ).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(
            keep_row,
            textvariable=self.settings_max_backups,
            width=72,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
        ).pack(side="left")

        act = ctk.CTkFrame(bak_card, fg_color="transparent")
        act.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(
            act,
            text="Backup now",
            height=36,
            font=FONT_BOLD,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=self._settings_backup_now,
        ).pack(side="left", padx=(0, 8))
        self.settings_backup_status = ctk.CTkLabel(
            act, text="", font=FONT_SM, text_color=C["muted"], anchor="w"
        )
        self.settings_backup_status.pack(side="left", fill="x", expand=True)

        # --- NSOPW search strategy ---
        ns_card = _card(scroll)
        ns_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(ns_card, "NSOPW search strategy").pack(anchor="w", padx=14, pady=(12, 4))
        _muted(
            ns_card,
            "NSOPW accepts partial first and last names. Combined length must be at least 3 "
            "letters (e.g. first=M, last=AH matches Mohamed Ahmed). Compact mode collapses "
            "surnames that share a short prefix so one query covers many list names. "
            "Last prefixes always come from the selected surname list (never brute-force "
            "AA–ZZ). Optional abbreviated mode (NSOPW tab → indian / indian_wide) shortens "
            "BOTH first letters (Indian A/S/R/P/M/K/V/N/B/D) AND surname digraphs (top "
            "Indian-likely combos like RA/CH/KA/PA/SH…). Default is full A–Z + all list digraphs.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_compact_prefixes = ctk.BooleanVar(
            value=bool(self.app_settings.get("nsopw_compact_prefixes", True))
        )
        ctk.CTkCheckBox(
            ns_card,
            text="Use short 3-letter partial prefixes (recommended — far fewer searches)",
            variable=self.settings_compact_prefixes,
            font=FONT_SM,
            text_color=C["text"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            checkmark_color=C["bg"],
            border_color=C["border"],
            command=self._settings_on_compact_toggle,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        mcl_row = ctk.CTkFrame(ns_card, fg_color="transparent")
        mcl_row.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            mcl_row, text="Min combined first+last length", font=FONT_SM, text_color=C["muted"]
        ).pack(side="left", padx=(0, 8))
        self.settings_min_combined = ctk.StringVar(
            value=str(int(self.app_settings.get("nsopw_min_combined_len", 3)))
        )
        ctk.CTkEntry(
            mcl_row,
            textvariable=self.settings_min_combined,
            width=56,
            fg_color=C["bg"],
            border_color=C["border"],
            text_color=C["text"],
        ).pack(side="left")
        ctk.CTkLabel(
            mcl_row, text="(NSOPW API minimum is 3)", font=FONT_SM, text_color=C["dim"]
        ).pack(side="left", padx=(8, 0))

        # --- Access assistance (CAPTCHA / WAF — manual, not automated solvers) ---
        cap_card = _card(scroll)
        cap_card.pack(fill="x", padx=4, pady=(0, 8))
        _section_label(cap_card, "Access assistance (CAPTCHA / WAF)").pack(
            anchor="w", padx=14, pady=(12, 4)
        )
        _muted(
            cap_card,
            "Automated CAPTCHA solving is not supported. When a state site shows a CAPTCHA "
            "or bot wall, the URL is queued. Open it in your browser, complete the challenge, "
            "export cookies for that site, paste them below, then requeue incomplete reports. "
            "Disclaimers/terms gates are still auto-accepted when possible.",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.settings_captcha_status = ctk.CTkLabel(
            cap_card, text="", font=FONT_SM, text_color=C["text"], anchor="w",
        )
        self.settings_captcha_status.pack(fill="x", padx=14, pady=(0, 6))

        cap_btns = ctk.CTkFrame(cap_card, fg_color="transparent")
        cap_btns.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Refresh queue", height=32, width=120,
            command=self._settings_refresh_captcha_queue,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Open next blocked URL", height=32, width=160,
            command=self._settings_open_next_captcha,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Open all blocked (max 5)", height=32, width=160,
            command=self._settings_open_captcha_batch,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cap_btns, text="Clear queue", height=32, width=100,
            command=self._settings_clear_captcha_queue,
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        ctk.CTkLabel(
            cap_card,
            text="Import cookies (JSON list, Netscape cookies.txt, or Cookie: header)",
            font=FONT_SM, text_color=C["muted"], anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 4))
        self.settings_cookie_domain = ctk.StringVar(value="")
        dom_row = ctk.CTkFrame(cap_card, fg_color="transparent")
        dom_row.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(
            dom_row, text="Default domain (if paste has no domain)", font=FONT_SM,
            text_color=C["muted"],
        ).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(
            dom_row, textvariable=self.settings_cookie_domain, width=220,
            placeholder_text="e.g. offender.fdle.state.fl.us",
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")
        self.settings_cookie_text = ctk.CTkTextbox(
            cap_card, height=100, font=FONT_MONO,
            fg_color=C["bg"], text_color=C["text"],
            border_color=C["border"], border_width=1, corner_radius=8,
        )
        self.settings_cookie_text.pack(fill="x", padx=14, pady=(0, 6))
        cookie_btns = ctk.CTkFrame(cap_card, fg_color="transparent")
        cookie_btns.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(
            cookie_btns, text="Import cookies", height=32, width=130,
            command=self._settings_import_cookies,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cookie_btns, text="Load cookies file…", height=32, width=140,
            command=self._settings_load_cookie_file,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            cookie_btns, text="Clear saved cookies", height=32, width=140,
            command=self._settings_clear_cookies,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")
        self.settings_cookie_status = ctk.CTkLabel(
            cookie_btns, text="", font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.settings_cookie_status.pack(side="left", fill="x", expand=True, padx=(10, 0))

        # --- Save ---
        save_card = _card(scroll)
        save_card.pack(fill="x", padx=4, pady=(0, 8))
        save_row = ctk.CTkFrame(save_card, fg_color="transparent")
        save_row.pack(fill="x", padx=14, pady=12)
        ctk.CTkButton(
            save_row,
            text="Save settings",
            height=36,
            font=FONT_BOLD,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color=C["bg"],
            command=self._settings_save,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            save_row,
            text="Reset to defaults",
            height=36,
            command=self._settings_reset_defaults,
            fg_color=C["elevated"],
            hover_color=C["border"],
            text_color=C["text"],
            border_width=1,
            border_color=C["border"],
        ).pack(side="left", padx=(0, 8))
        self.settings_status = ctk.CTkLabel(
            save_row, text="", font=FONT_SM, text_color=C["muted"], anchor="w"
        )
        self.settings_status.pack(side="left", fill="x", expand=True)

        self.after(100, self._settings_refresh_status)
        self.after(120, self._settings_refresh_captcha_queue)

    def _settings_refresh_captcha_queue(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue, CookieJarStore

            q = CaptchaQueue()
            items = q.list_items()
            jar = CookieJarStore()
            hosts = jar.summary()
            host_txt = ", ".join(f"{h}({n})" for h, n in list(hosts.items())[:6]) or "none"
            if not items:
                self.settings_captcha_status.configure(
                    text=f"Queue empty · saved cookie hosts: {host_txt}"
                )
            else:
                last = items[-1]
                self.settings_captcha_status.configure(
                    text=(
                        f"Queued: {len(items)} · latest [{last.get('jurisdiction') or '?'}] "
                        f"{last.get('reason')}: {(last.get('url') or '')[:70]}… · "
                        f"cookies: {host_txt}"
                    )
                )
        except Exception as e:
            if hasattr(self, "settings_captcha_status"):
                self.settings_captcha_status.configure(text=f"Queue error: {e}")

    def _settings_open_next_captcha(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue

            items = CaptchaQueue().list_items()
            if not items:
                messagebox.showinfo("CAPTCHA queue", "No blocked URLs queued.")
                return
            url = items[-1].get("url") or ""
            if url:
                webbrowser.open(url)
                self.settings_captcha_status.configure(
                    text=f"Opened in browser — complete challenge, then import cookies. {url[:60]}…"
                )
        except Exception as e:
            messagebox.showerror("Open URL", str(e))

    def _settings_open_captcha_batch(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue

            items = CaptchaQueue().list_items()
            if not items:
                messagebox.showinfo("CAPTCHA queue", "No blocked URLs queued.")
                return
            opened = 0
            for item in reversed(items[-5:]):
                url = item.get("url") or ""
                if url:
                    webbrowser.open(url)
                    opened += 1
            self.settings_captcha_status.configure(
                text=f"Opened {opened} blocked URL(s) in browser."
            )
        except Exception as e:
            messagebox.showerror("Open URLs", str(e))

    def _settings_clear_captcha_queue(self) -> None:
        try:
            from scraper.cookie_jar import CaptchaQueue

            CaptchaQueue().clear()
            self._settings_refresh_captcha_queue()
        except Exception as e:
            messagebox.showerror("Clear queue", str(e))

    def _settings_import_cookies(self) -> None:
        try:
            from scraper.cookie_jar import CookieJarStore

            raw = self.settings_cookie_text.get("1.0", "end")
            domain = (self.settings_cookie_domain.get() or "").strip()
            n = CookieJarStore().import_cookies(raw, default_domain=domain)
            self.settings_cookie_status.configure(
                text=f"Imported {n} cookie(s). Requeue incomplete reports to retry."
            )
            self._settings_refresh_captcha_queue()
            if n == 0:
                messagebox.showwarning(
                    "No cookies imported",
                    "Paste JSON cookies, Netscape cookies.txt lines, or a Cookie: header.\n"
                    "Set default domain if the paste has no domain field.",
                )
        except Exception as e:
            messagebox.showerror("Import cookies", str(e))

    def _settings_load_cookie_file(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Cookie file",
            filetypes=[
                ("Cookie / JSON / text", "*.txt *.json *.cookies"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            self.settings_cookie_text.delete("1.0", "end")
            self.settings_cookie_text.insert("1.0", text)
            self._settings_import_cookies()
        except Exception as e:
            messagebox.showerror("Load cookies", str(e))

    def _settings_clear_cookies(self) -> None:
        try:
            from scraper.cookie_jar import CookieJarStore

            CookieJarStore().clear()
            self.settings_cookie_status.configure(text="Saved cookies cleared.")
            self._settings_refresh_captcha_queue()
        except Exception as e:
            messagebox.showerror("Clear cookies", str(e))

    def _settings_collect(self) -> Dict[str, Any]:
        try:
            max_b = int(str(self.settings_max_backups.get()).strip() or "10")
        except ValueError:
            max_b = 10
        try:
            mcl = int(str(self.settings_min_combined.get()).strip() or "3")
        except ValueError:
            mcl = 3
        return {
            "db_path": (self.settings_db_path.get() or "data/offenders.db").strip(),
            "backup_on_close": bool(self.settings_backup_on_close.get()),
            "backup_dir": (self.settings_backup_dir.get() or "data/backups").strip(),
            "max_backups": max_b,
            "nsopw_compact_prefixes": bool(self.settings_compact_prefixes.get()),
            "nsopw_min_combined_len": mcl,
        }

    def _settings_apply_to_app(self, settings: Dict[str, Any]) -> None:
        from scraper.app_settings import normalize_settings

        self.app_settings = normalize_settings(settings)
        self.db_path = str(self.app_settings["db_path"])
        self.nsopw_db_path = self.db_path
        self._refresh_header_db_path()
        # Refresh NSOPW estimate if built
        if hasattr(self, "_nsopw_update_surname_count"):
            try:
                self._nsopw_update_surname_count()
            except Exception:
                pass

    def _settings_save(self) -> None:
        from scraper.app_settings import save_settings

        raw = self._settings_collect()
        path = save_settings(raw)
        self._settings_apply_to_app(raw)
        # Reflect normalized values back into widgets
        s = self.app_settings
        self.settings_db_path.set(str(s["db_path"]))
        self.settings_backup_dir.set(str(s["backup_dir"]))
        self.settings_max_backups.set(str(s["max_backups"]))
        self.settings_min_combined.set(str(s["nsopw_min_combined_len"]))
        self.settings_backup_on_close.set(bool(s["backup_on_close"]))
        self.settings_compact_prefixes.set(bool(s["nsopw_compact_prefixes"]))
        self.settings_status.configure(text=f"Saved → {path}")
        self._settings_refresh_status()

    def _settings_reset_defaults(self) -> None:
        from scraper.app_settings import DEFAULTS

        self.settings_db_path.set(str(DEFAULTS["db_path"]))
        self.settings_backup_on_close.set(bool(DEFAULTS["backup_on_close"]))
        self.settings_backup_dir.set(str(DEFAULTS["backup_dir"]))
        self.settings_max_backups.set(str(DEFAULTS["max_backups"]))
        self.settings_compact_prefixes.set(bool(DEFAULTS["nsopw_compact_prefixes"]))
        self.settings_min_combined.set(str(DEFAULTS["nsopw_min_combined_len"]))
        self.settings_status.configure(text="Defaults loaded — click Save settings to keep.")

    def _settings_browse_db(self) -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            title="Database file",
            defaultextension=".db",
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
            initialfile=Path(self.settings_db_path.get() or "offenders.db").name,
        )
        if path:
            self.settings_db_path.set(path)

    def _settings_browse_backup_dir(self) -> None:
        from tkinter import filedialog

        path = filedialog.askdirectory(
            title="Backup folder",
            initialdir=self.settings_backup_dir.get() or "data",
        )
        if path:
            self.settings_backup_dir.set(path)

    def _settings_open_backup_dir(self) -> None:
        path = Path(self.settings_backup_dir.get() or "data/backups")
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _settings_on_compact_toggle(self) -> None:
        if hasattr(self, "_nsopw_update_surname_count"):
            try:
                self._nsopw_update_surname_count()
            except Exception:
                pass

    def _settings_refresh_status(self) -> None:
        bdir = Path(self.settings_backup_dir.get() or "data/backups")
        n = 0
        latest = "—"
        if bdir.is_dir():
            files = sorted(bdir.glob("offenders_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            n = len(files)
            if files:
                latest = files[0].name
        dbp = Path(self.settings_db_path.get() or self.db_path)
        db_info = f"{dbp} ({dbp.stat().st_size // 1024} KB)" if dbp.is_file() else f"{dbp} (not created yet)"
        if hasattr(self, "settings_backup_status"):
            self.settings_backup_status.configure(
                text=f"DB: {db_info}  ·  {n} backup(s)  ·  latest: {latest}"
            )

    def _settings_backup_now(self) -> None:
        """Manual backup using current Settings fields (does not require Save first)."""
        try:
            dest, note = self._run_db_backup(
                db_path=self.settings_db_path.get() or self.db_path,
                backup_dir=self.settings_backup_dir.get() or "data/backups",
                max_backups=self.settings_max_backups.get(),
            )
            msg = f"Backed up → {dest}"
            if note:
                msg += f" ({note})"
            self.settings_backup_status.configure(text=msg)
            self.settings_status.configure(text=msg)
            self.log_queue.put(msg)
        except Exception as e:
            self.settings_backup_status.configure(text=f"Backup failed: {e}")
            messagebox.showerror("Backup failed", str(e))

    def _run_db_backup(
        self,
        db_path: Optional[str] = None,
        backup_dir: Optional[str] = None,
        max_backups: Any = None,
    ):
        from scraper.database import backup_database_file

        src = Path(db_path or self.db_path)
        if not src.exists():
            raise FileNotFoundError(f"Database not found: {src}")
        bdir = Path(backup_dir or self.app_settings.get("backup_dir") or "data/backups")
        try:
            keep = int(
                max_backups
                if max_backups is not None
                else self.app_settings.get("max_backups", 10)
            )
        except (TypeError, ValueError):
            keep = 10

        # backup_database_file opens its own connection + verifies integrity
        return backup_database_file(
            src, bdir, keep=keep, prefix="offenders", verify=True
        )

    def _on_close(self) -> None:
        """Window close: optional DB backup, then destroy."""
        if self._closing:
            return

        # Don't silently abandon a running scrape/NSOPW/requeue
        if getattr(self, "is_running", False):
            try:
                if not messagebox.askyesno(
                    "Job still running",
                    "A scrape or NSOPW job is still running.\n\n"
                    "Close anyway? In-flight work may be incomplete.\n"
                    "(Prefer Cancel on the job first.)",
                ):
                    return
            except Exception:
                pass

        self._closing = True

        # Persist latest Settings UI values if the tab was built
        if hasattr(self, "settings_backup_on_close"):
            try:
                from scraper.app_settings import save_settings, normalize_settings

                raw = self._settings_collect()
                save_settings(raw)
                self.app_settings = normalize_settings(raw)
                self.db_path = str(self.app_settings.get("db_path") or self.db_path)
            except Exception:
                pass

        do_backup = bool(self.app_settings.get("backup_on_close", False))
        if do_backup:
            try:
                dest, note = self._run_db_backup()
                try:
                    extra = f" ({note})" if note else ""
                    self.stats_label.configure(
                        text=f"Backed up → {Path(dest).name}{extra}"
                    )
                    self.update_idletasks()
                except Exception:
                    pass
            except FileNotFoundError:
                # No DB yet — fine
                pass
            except Exception as e:
                try:
                    if not messagebox.askokcancel(
                        "Backup failed",
                        f"Could not backup database:\n{e}\n\nClose anyway?",
                    ):
                        self._closing = False
                        return
                except Exception:
                    pass

        try:
            self.destroy()
        except Exception:
            pass


def main():
    try:
        app = ArchiverApp()
        app.mainloop()
    except Exception:
        err = traceback.format_exc()
        _fatal(f"SOR Public Archiver crashed:\n\n{err}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
