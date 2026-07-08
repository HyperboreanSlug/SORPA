#!/usr/bin/env python3
"""
Public SOR Archiver — desktop GUI (CustomTkinter).

Dark, high-contrast UI for scrape / search / analysis / NSOPW.
Double-click run_gui.bat (recommended) or gui.py.
"""

from __future__ import annotations

import csv
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

FONT_UI = ("Segoe UI", 13)
FONT_SM = ("Segoe UI", 12)
FONT_BOLD = ("Segoe UI", 13, "bold")
FONT_TITLE = ("Segoe UI", 20, "bold")
FONT_SECTION = ("Segoe UI", 14, "bold")
FONT_MONO = ("Consolas", 12)


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
    """Dark treeview inside a card with scrollbars."""
    wrap = ctk.CTkFrame(parent, fg_color=C["tree_bg"], corner_radius=10, border_width=1, border_color=C["border"])
    tree = ttk.Treeview(wrap, style="Dark.Treeview", show="headings")
    vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
    hsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview, style="Dark.Horizontal.TScrollbar")
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    vsb.pack(side="right", fill="y", padx=(0, 4), pady=4)
    hsb.pack(side="bottom", fill="x", padx=4, pady=(0, 4))
    tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    return wrap, tree


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
        self.db_path = "data/offenders.db"
        self._nsopw_cancel = False
        self._viewer_data: List[Dict[str, Any]] = []
        self._viewer_headers: List[str] = []

        self._build()
        self._load_sources()
        self._poll_log()

    # -----------------------------------------------------------------------
    # Shell
    # -----------------------------------------------------------------------
    def _build(self):
        # Header
        header = ctk.CTkFrame(self, fg_color=C["surface"], height=64, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="SOR Public Archiver",
            font=FONT_TITLE,
            text_color=C["text"],
        ).pack(side="left", padx=24, pady=16)

        self.stats_label = ctk.CTkLabel(
            header,
            text="Ready",
            font=FONT_SM,
            text_color=C["accent"],
        )
        self.stats_label.pack(side="right", padx=24)

        # Body: tabs + log
        body = ctk.CTkFrame(self, fg_color=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(12, 8))

        self.tabs = ctk.CTkTabview(
            body,
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
        )
        self.tabs.pack(fill="both", expand=True)

        for name in ("Scrape", "Search", "Misclassify", "NSOPW", "Viewer"):
            self.tabs.add(name)

        self._build_scrape(self.tabs.tab("Scrape"))
        self._build_search(self.tabs.tab("Search"))
        self._build_misclass(self.tabs.tab("Misclassify"))
        self._build_nsopw(self.tabs.tab("NSOPW"))
        self._build_viewer(self.tabs.tab("Viewer"))

        # Log
        log_card = _card(self)
        log_card.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkLabel(
            log_card, text="Activity", font=FONT_BOLD, text_color=C["muted"], anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 4))
        self.log_text = ctk.CTkTextbox(
            log_card,
            height=120,
            font=FONT_MONO,
            fg_color=C["bg"],
            text_color=C["muted"],
            border_color=C["border"],
            border_width=1,
            corner_radius=8,
            activate_scrollbars=True,
        )
        self.log_text.pack(fill="x", padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")

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

        mid = ctk.CTkFrame(tab, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=12, pady=6)

        left = _card(mid)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
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
        self.scrape_tree.column("#0", width=220)
        self.scrape_tree.column("abbr", width=50, anchor="center")
        self.scrape_tree.column("method", width=90, anchor="center")
        self.scrape_tree.column("notes", width=280)
        self.scrape_tree.bind("<<TreeviewSelect>>", self._scrape_on_select)
        self.scrape_tree.tag_configure("direct", background="#1a241c")

        right = _card(mid, width=300)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
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

        self.scrape_progress = ctk.CTkProgressBar(
            right, progress_color=C["accent"], fg_color=C["elevated"], height=8
        )
        self.scrape_progress.pack(fill="x", padx=14, pady=(16, 16))
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
        self._set_running(True)
        self.scrape_progress.set(0)
        total = len(registries)

        def log(msg):
            self.log_queue.put(msg)

        def worker():
            try:
                total_records = 0
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
                    else:
                        log("  No records")
                    pct = (i + 1) / max(total, 1)
                    self.after(0, lambda p=pct: self.scrape_progress.set(p))
                log(f"Done. Total records: {total_records}")
            except Exception as e:
                log(f"ERROR: {e}")
            finally:
                self.after(0, lambda: self._set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------
    def _build_search(self, tab):
        tab.configure(fg_color=C["surface"])
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=12)

        self.search_name_var = ctk.StringVar()
        ctk.CTkEntry(
            bar, textvariable=self.search_name_var, placeholder_text="Name…",
            width=200, fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", padx=(0, 8))

        self.search_state_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_state_var, width=90,
            values=["", "ALL", "AL", "AK", "AZ", "CA", "DC", "FL", "GA", "NY", "TX"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            dropdown_hover_color=C["elevated"], text_color=C["text"],
        ).pack(side="left", padx=4)

        self.search_race_var = ctk.StringVar(value="")
        ctk.CTkComboBox(
            bar, variable=self.search_race_var, width=140,
            values=["", "WHITE", "BLACK", "HISPANIC", "ASIAN", "NATIVE AMERICAN"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            button_hover_color=C["border"], dropdown_fg_color=C["panel"],
            text_color=C["text"],
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            bar, text="Search", width=100, command=self._do_search,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bar, text="Show all", width=100,
            command=lambda: self._do_search(name="", state="", race=""),
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left")

        wrap, self.search_tree = _tree_frame(tab)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        cols = ("name", "race", "state", "county", "age", "address")
        self.search_tree.configure(columns=cols, show="headings")
        for c in cols:
            self.search_tree.heading(c, text=c.upper())
            self.search_tree.column(c, width=120)

        self.search_status = ctk.CTkLabel(
            tab, text="Query the local SQLite database", font=FONT_SM, text_color=C["muted"]
        )
        self.search_status.pack(anchor="w", padx=14, pady=(0, 10))

    def _do_search(self, name=None, state=None, race=None):
        from scraper.searcher import SexOffenderSearcher

        name = self.search_name_var.get() if name is None else name
        state = self.search_state_var.get() if state is None else state
        race = self.search_race_var.get() if race is None else race
        searcher = SexOffenderSearcher(db_path=self.db_path)
        try:
            if name:
                results = searcher.search_by_name(
                    name=name,
                    state=state if state and state != "ALL" else None,
                    race=race or None,
                    limit=500,
                )
                self._populate_search_tree(results.records)
                self.search_status.configure(
                    text=f"{len(results.records)} matches · {results.query_time_ms:.0f} ms"
                )
            elif race:
                results = searcher.search_by_race(
                    race=race,
                    state=state if state and state != "ALL" else None,
                    limit=500,
                )
                self._populate_search_tree(results.records)
                self.search_status.configure(text=f"{len(results.records)} with race {race}")
            elif state and state != "ALL":
                results = searcher.search_by_state(state=state, limit=500)
                self._populate_search_tree(results.records)
                self.search_status.configure(text=f"{len(results.records)} in {state}")
            else:
                dist = searcher.get_race_distribution()
                self._show_race_distribution(dist)
                self.search_status.configure(
                    text=f"Race distribution · {searcher.get_total_count()} total"
                )
        finally:
            searcher.close()

    def _populate_search_tree(self, records):
        self.search_tree.delete(*self.search_tree.get_children())
        for r in records[:500]:
            name = f"{r.get('first_name', '') or ''} {r.get('last_name', '') or ''}".strip() or "—"
            self.search_tree.insert(
                "",
                "end",
                values=(
                    name,
                    (r.get("race") or "—")[:14],
                    (r.get("state") or "—")[:6],
                    (r.get("county") or "—")[:16],
                    str(r.get("age") or ""),
                    (r.get("address") or "")[:36],
                ),
            )

    def _show_race_distribution(self, dist):
        self.search_tree.delete(*self.search_tree.get_children())
        total = sum(d.get("count", 0) for d in dist) or 1
        for d in dist:
            race = d.get("race") or "—"
            count = d.get("count", 0)
            pct = count / total * 100
            bar = "▮" * max(1, int(pct / 4))
            self.search_tree.insert(
                "", "end", values=(race, str(count), f"{pct:.1f}%", bar, "", "")
            )

    # -----------------------------------------------------------------------
    # Misclassify
    # -----------------------------------------------------------------------
    def _build_misclass(self, tab):
        tab.configure(fg_color=C["surface"])
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=12)

        self.misclass_ethnicity_var = ctk.StringVar(value="all")
        ctk.CTkComboBox(
            bar, variable=self.misclass_ethnicity_var, width=160,
            values=["all", "hispanic", "asian", "african_american"],
            fg_color=C["bg"], border_color=C["border"], button_color=C["elevated"],
            text_color=C["text"], dropdown_fg_color=C["panel"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(bar, text="Min conf.", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(8, 4)
        )
        self.misclass_conf_var = ctk.DoubleVar(value=0.5)
        ctk.CTkEntry(
            bar, textvariable=self.misclass_conf_var, width=60,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left")

        ctk.CTkLabel(bar, text="Max rows", font=FONT_SM, text_color=C["muted"]).pack(
            side="left", padx=(12, 4)
        )
        self.misclass_limit_var = ctk.IntVar(value=10000)
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

        wrap, self.misclass_tree = _tree_frame(tab)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        cols = ("name", "recorded_race", "likely_ethnicity", "confidence", "matching_names")
        self.misclass_tree.configure(columns=cols, show="headings")
        for c in cols:
            self.misclass_tree.heading(c, text=c.replace("_", " ").upper())
            self.misclass_tree.column(c, width=140)

        self.misclass_status = ctk.CTkLabel(
            tab, text="Compare recorded race to surname ethnicity lists",
            font=FONT_SM, text_color=C["muted"],
        )
        self.misclass_status.pack(anchor="w", padx=14, pady=(0, 10))

    def _run_misclassification(self):
        from scraper.searcher import SexOffenderSearcher

        searcher = SexOffenderSearcher(db_path=self.db_path)
        eth = self.misclass_ethnicity_var.get()
        try:
            min_conf = float(self.misclass_conf_var.get())
            limit = int(self.misclass_limit_var.get())
            if eth == "hispanic":
                results = searcher.find_hispanic_misclassifications(min_confidence=min_conf, limit=limit)
            elif eth == "asian":
                results = searcher.find_asian_misclassifications(min_confidence=min_conf, limit=limit)
            elif eth == "african_american":
                results = searcher.find_african_american_misclassifications(
                    min_confidence=min_conf, limit=limit
                )
            else:
                results = searcher.analyze_ethnicities(min_confidence=min_conf, limit=limit)
        finally:
            searcher.close()

        self.misclass_tree.delete(*self.misclass_tree.get_children())
        for mc in results[:500]:
            name = (
                f"{mc.record.get('first_name', '') or ''} "
                f"{mc.record.get('last_name', '') or ''}"
            ).strip() or "—"
            self.misclass_tree.insert(
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
        self.misclass_status.configure(text=f"{len(results)} potential mismatches")
        self.log_queue.put(f"Misclassification: {len(results)} results")

    def _export_misclass(self):
        from scraper.searcher import SexOffenderSearcher

        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if not path:
            return
        searcher = SexOffenderSearcher(db_path=self.db_path)
        eth = self.misclass_ethnicity_var.get()
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
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C["surface"])
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        _section_label(scroll, "NSOPW ethnic name search").pack(anchor="w", padx=8, pady=(4, 2))
        _muted(
            scroll,
            "Searches the selected ethnic surname list with A–Z first-name prefixes (partial match). "
            "Race comes from each jurisdiction detail sheet (not NSOPW search itself). "
            "Use Resume + New HTML only to continue a long run without redoing work.",
        ).pack(anchor="w", padx=8, pady=(0, 12))

        self.nsopw_first_mode = "initials"
        self.nsopw_db_path = self.db_path
        self.nsopw_html_dir = "data/report_pages"
        self._nsopw_insert_count = 0

        # Ethnicity + surname scope
        eth_card = _card(scroll)
        eth_card.pack(fill="x", padx=4, pady=6)
        _section_label(eth_card, "Ethnicity & surnames").pack(anchor="w", padx=14, pady=(12, 6))
        eth_row = ctk.CTkFrame(eth_card, fg_color="transparent")
        eth_row.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            eth_row, text="Surname list", font=FONT_SM, text_color=C["muted"], width=100, anchor="w"
        ).pack(side="left")
        self.nsopw_ethnicity = ctk.StringVar(value="hispanic")
        ctk.CTkComboBox(
            eth_row,
            variable=self.nsopw_ethnicity,
            width=220,
            values=[
                "hispanic",
                "asian",
                "indian",
                "african_american",
                "arabic",
                "jewish",
                "portuguese",
                "native_american",
                "european",
                "all",
            ],
            fg_color=C["bg"],
            border_color=C["border"],
            button_color=C["elevated"],
            text_color=C["text"],
            dropdown_fg_color=C["panel"],
        ).pack(side="left", padx=6)
        ctk.CTkLabel(
            eth_card,
            text=(
                "asian = East/SE Asian (Chinese, Korean, Japanese, Vietnamese, Thai, Filipino…) · "
                "indian = South Asian (India, Pakistan, Bangladesh, Sri Lanka, Nepal…)"
            ),
            font=FONT_SM, text_color=C["dim"],
        ).pack(anchor="w", padx=14, pady=(0, 6))

        # Cap toggle: off = search entire selected surname list
        self.nsopw_limit_surnames = ctk.BooleanVar(value=False)
        self.nsopw_surnames_limit = ctk.IntVar(value=15)
        sn_row = ctk.CTkFrame(eth_card, fg_color="transparent")
        sn_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkCheckBox(
            sn_row, text="Limit max surnames / group",
            variable=self.nsopw_limit_surnames, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
            command=self._nsopw_toggle_surname_cap,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            sn_row, text="Max", font=FONT_SM, text_color=C["muted"]
        ).pack(side="left", padx=(0, 4))
        self.nsopw_surnames_entry = ctk.CTkEntry(
            sn_row, textvariable=self.nsopw_surnames_limit, width=64,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            state="disabled",
        )
        self.nsopw_surnames_entry.pack(side="left")

        # Limits & rate control (granular: search vs report/HTML)
        lim = _card(scroll)
        lim.pack(fill="x", padx=4, pady=6)
        _section_label(lim, "Limits & rate control").pack(anchor="w", padx=14, pady=(12, 6))
        _muted(
            lim,
            "Search delay = NSOPW API (Cloudflare — keep ≥2s). "
            "Report delay = state detail sheets + HTML archive (same request; can be much lower).",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.nsopw_max_searches = ctk.IntVar(value=40)
        self.nsopw_max_reports = ctk.IntVar(value=80)
        self.nsopw_search_delay = ctk.DoubleVar(value=3.0)
        self.nsopw_report_delay = ctk.DoubleVar(value=0.75)
        self.nsopw_enrich = ctk.BooleanVar(value=True)
        self.nsopw_save_html = ctk.BooleanVar(value=True)
        self.nsopw_skip_existing = ctk.BooleanVar(value=True)
        self.nsopw_resume = ctk.BooleanVar(value=True)
        self.nsopw_new_files_only = ctk.BooleanVar(value=True)

        row1 = ctk.CTkFrame(lim, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=2)
        for label, var, width in (
            ("Max new searches", self.nsopw_max_searches, 72),
            ("Max reports", self.nsopw_max_reports, 72),
        ):
            ctk.CTkLabel(row1, text=label, font=FONT_SM, text_color=C["muted"]).pack(
                side="left", padx=(0, 4)
            )
            ctk.CTkEntry(
                row1, textvariable=var, width=width,
                fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            ).pack(side="left", padx=(0, 14))

        row2 = ctk.CTkFrame(lim, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=6)
        for label, var in (
            ("Search delay (s)", self.nsopw_search_delay),
            ("Report/HTML delay (s)", self.nsopw_report_delay),
        ):
            ctk.CTkLabel(row2, text=label, font=FONT_SM, text_color=C["muted"]).pack(
                side="left", padx=(0, 4)
            )
            ctk.CTkEntry(
                row2, textvariable=var, width=72,
                fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
            ).pack(side="left", padx=(0, 14))

        tog = ctk.CTkFrame(lim, fg_color="transparent")
        tog.pack(fill="x", padx=14, pady=(2, 2))
        ctk.CTkCheckBox(
            tog, text="Fetch detail sheets (race / demos)",
            variable=self.nsopw_enrich, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(side="left", padx=(0, 16))
        ctk.CTkCheckBox(
            tog, text="Archive report HTML",
            variable=self.nsopw_save_html, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(side="left")

        # Resume / skip options
        opt = ctk.CTkFrame(lim, fg_color="transparent")
        opt.pack(fill="x", padx=14, pady=(8, 2))
        ctk.CTkCheckBox(
            opt, text="Resume — skip completed searches",
            variable=self.nsopw_resume, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", pady=2)
        ctk.CTkCheckBox(
            opt, text="Skip known offender URLs already in database",
            variable=self.nsopw_skip_existing, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", pady=2)
        ctk.CTkCheckBox(
            opt, text="New HTML files only — do not re-download existing report pages",
            variable=self.nsopw_new_files_only, font=FONT_SM, text_color=C["text"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            checkmark_color=C["bg"], border_color=C["border"],
        ).pack(anchor="w", pady=2)

        ctk.CTkLabel(
            lim,
            text="Resume logs queries in the DB. Floors: search ≥2.0s · report ≥0.25s · data/offenders.db",
            font=FONT_SM, text_color=C["dim"],
        ).pack(anchor="w", padx=14, pady=(6, 12))

        # Actions
        act = ctk.CTkFrame(scroll, fg_color="transparent")
        act.pack(fill="x", padx=4, pady=10)
        self.nsopw_start_btn = ctk.CTkButton(
            act, text="Start NSOPW search", height=42, font=FONT_BOLD,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
            command=self._start_nsopw,
        )
        self.nsopw_start_btn.pack(side="left", padx=(4, 8))
        self.nsopw_cancel_btn = ctk.CTkButton(
            act, text="Cancel", height=42, width=100, state="disabled",
            fg_color=C["elevated"], hover_color=C["danger"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._cancel_nsopw,
        )
        self.nsopw_cancel_btn.pack(side="left", padx=4)
        ctk.CTkButton(
            act, text="Open data folder", height=42,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_open_data_folder,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            act, text="Clear table", height=42, width=100,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
            command=self._nsopw_clear_tree,
        ).pack(side="left", padx=4)

        self.nsopw_progress = ctk.CTkProgressBar(
            scroll, mode="indeterminate", progress_color=C["accent"], fg_color=C["elevated"], height=6
        )
        self.nsopw_progress.pack(fill="x", padx=8, pady=6)
        self.nsopw_progress.set(0)

        self.nsopw_status = ctk.CTkLabel(
            scroll, text="Ready",
            font=FONT_SM, text_color=C["muted"], anchor="w",
        )
        self.nsopw_status.pack(fill="x", padx=10, pady=(0, 8))

        prev = _card(scroll)
        prev.pack(fill="both", expand=True, padx=4, pady=(4, 12))
        _section_label(prev, "Recent inserts (live) · race from detail sheet · double-click HTML/URL").pack(
            anchor="w", padx=14, pady=(12, 6)
        )
        wrap, self.nsopw_tree = _tree_frame(prev)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.nsopw_tree.configure(
            columns=("name", "state", "race", "url", "html"), show="headings"
        )
        for c, w in zip(
            ("name", "state", "race", "url", "html"),
            (150, 50, 110, 280, 180),
        ):
            self.nsopw_tree.heading(c, text=c.upper())
            self.nsopw_tree.column(c, width=w)
        self.nsopw_tree.bind("<Double-1>", self._nsopw_open_selected)

    def _nsopw_clear_tree(self):
        self.nsopw_tree.delete(*self.nsopw_tree.get_children())
        self._nsopw_insert_count = 0

    def _nsopw_toggle_surname_cap(self):
        """Enable max-surnames entry only when the limit toggle is on."""
        if self.nsopw_limit_surnames.get():
            self.nsopw_surnames_entry.configure(state="normal")
        else:
            self.nsopw_surnames_entry.configure(state="disabled")

    def _nsopw_append_row(self, record: Dict[str, Any]) -> None:
        """UI-thread: prepend a live insert into the Recent inserts table."""
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
        vals = (
            name[:80],
            (record.get("state") or record.get("source_state") or "")[:8],
            race_disp[:40],
            (record.get("source_url") or "")[:90],
            (record.get("report_html_path") or "")[:70],
        )
        self.nsopw_tree.insert("", 0, values=vals)
        # Cap live table size
        kids = self.nsopw_tree.get_children()
        if len(kids) > 200:
            for iid in kids[200:]:
                self.nsopw_tree.delete(iid)
        self._nsopw_insert_count += 1
        self.nsopw_status.configure(
            text=f"Running… {self._nsopw_insert_count} inserted (live)"
        )

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

    def _cancel_nsopw(self):
        self._nsopw_cancel = True
        self.log_queue.put("NSOPW cancel requested…")
        self.nsopw_status.configure(text="Cancelling…")

    def _start_nsopw(self):
        if self.is_running:
            return

        db_path = self.nsopw_db_path
        html_dir = self.nsopw_html_dir
        try:
            search_delay = max(2.0, float(self.nsopw_search_delay.get()))
        except (TypeError, ValueError):
            search_delay = 3.0
        try:
            report_delay = max(0.25, float(self.nsopw_report_delay.get()))
        except (TypeError, ValueError):
            report_delay = 0.75
        enrich = bool(self.nsopw_enrich.get())
        save_html = bool(self.nsopw_save_html.get())
        # Limit off → search entire list (all_surnames=True)
        limit_on = bool(self.nsopw_limit_surnames.get())
        all_surnames = not limit_on
        try:
            surnames_limit = int(self.nsopw_surnames_limit.get()) if limit_on else 0
        except (TypeError, ValueError):
            surnames_limit = 15 if limit_on else 0
        resume = bool(self.nsopw_resume.get())
        skip_existing = bool(self.nsopw_skip_existing.get())
        new_files_only = bool(self.nsopw_new_files_only.get())

        self._nsopw_cancel = False
        self._nsopw_insert_count = 0
        self._set_running(True)
        self.nsopw_start_btn.configure(state="disabled")
        self.nsopw_cancel_btn.configure(state="normal")
        self.nsopw_progress.start()
        self.nsopw_status.configure(text="Running NSOPW search…")
        self.nsopw_tree.delete(*self.nsopw_tree.get_children())

        def log(msg):
            self.log_queue.put(msg)

        def on_insert(record: Dict[str, Any]) -> None:
            # Marshal to UI thread
            self.after(0, lambda r=dict(record): self._nsopw_append_row(r))

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
                    ethnicity=self.nsopw_ethnicity.get(),
                    surnames_limit=surnames_limit,
                    all_surnames=all_surnames,
                    first_names=None,
                    first_mode=self.nsopw_first_mode,
                    jurisdictions=None,
                    max_searches=int(self.nsopw_max_searches.get()),
                    max_report_fetches=int(self.nsopw_max_reports.get()),
                    skip_existing_urls=skip_existing,
                    skip_completed_searches=resume,
                    new_files_only=new_files_only,
                    enrich_reports=enrich,
                    save_html=save_html,
                    log=log,
                    on_insert=on_insert,
                )

                def done():
                    self._set_running(False)
                    self.nsopw_start_btn.configure(state="normal")
                    self.nsopw_cancel_btn.configure(state="disabled")
                    self.nsopw_progress.stop()
                    self.nsopw_progress.set(0)
                    self.nsopw_status.configure(
                        text=(
                            f"Done · {stats.inserted} inserted · "
                            f"{stats.reports_with_race} with race · "
                            f"{stats.html_saved} HTML · {stats.searches} searches · "
                            f"{stats.searches_skipped} resumed"
                        )
                    )
                    self.db_path = db_path
                    messagebox.showinfo(
                        "NSOPW complete",
                        (
                            f"Inserted {stats.inserted}\n"
                            f"New searches: {stats.searches}\n"
                            f"Skipped completed searches: {stats.searches_skipped}\n"
                            f"Reports with race: {stats.reports_with_race}\n"
                            f"HTML saved: {stats.html_saved}\n"
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
                    self.nsopw_progress.stop()
                    self.nsopw_status.configure(text=f"Error: {e}")
                    messagebox.showerror("NSOPW error", str(e))

                self.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()

    def _nsopw_open_selected(self, _event=None):
        sel = self.nsopw_tree.selection()
        if not sel:
            return
        vals = self.nsopw_tree.item(sel[0], "values")
        # columns: name, state, race, url, html
        if len(vals) < 5:
            # backward-compatible if old 4-col rows somehow present
            if len(vals) >= 4:
                url, html_path = vals[2], vals[3]
            else:
                return
        else:
            url, html_path = vals[3], vals[4]
        if html_path and html_path != "—":
            p = Path(html_path)
            if p.exists():
                self._open_path(p)
                return
        if url:
            try:
                webbrowser.open(url)
            except Exception as e:
                messagebox.showerror("Open link", str(e))

    # -----------------------------------------------------------------------
    # Viewer
    # -----------------------------------------------------------------------
    def _build_viewer(self, tab):
        tab.configure(fg_color=C["surface"])
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(
            bar, text="Load CSV…", width=110, command=self._load_csv_to_viewer,
            fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["bg"],
        ).pack(side="left", padx=(0, 8))
        self.viewer_search_var = ctk.StringVar()
        ctk.CTkEntry(
            bar, textvariable=self.viewer_search_var, placeholder_text="Filter…", width=220,
            fg_color=C["bg"], border_color=C["border"], text_color=C["text"],
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            bar, text="Apply", width=80, command=self._apply_viewer_filter,
            fg_color=C["elevated"], hover_color=C["border"], text_color=C["text"],
            border_width=1, border_color=C["border"],
        ).pack(side="left", padx=4)

        wrap, self.viewer_tree = _tree_frame(tab)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.viewer_tree.configure(show="headings")

        self.viewer_status = ctk.CTkLabel(
            tab, text="Load a CSV to browse", font=FONT_SM, text_color=C["muted"]
        )
        self.viewer_status.pack(anchor="w", padx=14, pady=(0, 10))

    def _load_csv_to_viewer(self):
        filepath = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if not filepath:
            return
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                headers = list(reader.fieldnames or [])
                rows = list(reader)
            self.viewer_tree.delete(*self.viewer_tree.get_children())
            self.viewer_tree["columns"] = headers
            for col in headers:
                self.viewer_tree.heading(col, text=col)
                self.viewer_tree.column(col, width=110, minwidth=50)
            for i, row in enumerate(rows[:400]):
                self.viewer_tree.insert(
                    "", "end", iid=str(i),
                    values=[str(row.get(h, ""))[:80] for h in headers],
                )
            self._viewer_data = rows
            self._viewer_headers = headers
            self.viewer_status.configure(
                text=f"{len(rows)} rows · {Path(filepath).name}"
            )
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _apply_viewer_filter(self):
        if not self._viewer_data:
            return
        term = self.viewer_search_var.get().lower().strip()
        filtered = (
            self._viewer_data
            if not term
            else [r for r in self._viewer_data if any(term in str(v).lower() for v in r.values())]
        )
        self.viewer_tree.delete(*self.viewer_tree.get_children())
        for i, row in enumerate(filtered[:400]):
            self.viewer_tree.insert(
                "", "end", iid=str(i),
                values=[str(row.get(h, ""))[:80] for h in self._viewer_headers],
            )
        self.viewer_status.configure(text=f"{len(filtered)} rows after filter")

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
