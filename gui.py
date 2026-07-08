#!/usr/bin/env python3
"""
Sex Offender Database Scraper - Modern GUI

A sleek tkinter-based GUI for mass-downloading US sex offender registries,
searching records by name/race/state, and detecting ethnic misclassifications.

Run with: python gui.py
"""

import threading
import queue
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext


# ---------------------------------------------------------------------------
# Modern styling constants
# ---------------------------------------------------------------------------
COLORS = {
    "bg": "#1e1e2e",           # dark background (catppuccin-mocha)
    "fg": "#cdd6f4",           # text
    "panel": "#313244",        # panels / frames
    "accent": "#89b4fa",       # blue accent
    "green": "#a6e3a1",        # success green
    "red": "#f38ba8",          # error red
    "yellow": "#f9e2af",       # warning yellow
    "border": "#585b70",       # borders
    "input_bg": "#45475a",     # input backgrounds
    "hover": "#585b70",        # hover state
}

# Remove thick ttk borders by using plain frames with subtle styling
BORDER_WIDTH = 1

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------
class ModernFrame(tk.Frame):
    """A styled frame with padding and subtle border."""
    def __init__(self, parent, bg=None, **kwargs):
        if bg is None:
            bg = COLORS["bg"]
        tk.Frame.__init__(self, parent, background=bg, **kwargs)


class ModernButton(ttk.Button):
    def __init__(self, parent, text="", **kwargs):
        style = kwargs.pop("style", "Modern.TButton")
        super().__init__(parent, text=text, style=style, **kwargs)


class ModernLabel(ttk.Label):
    def __init__(self, parent, text="", bg=None, fg=None, **kwargs):
        style = kwargs.pop("style", "Modern.TLabel")
        # Prefer style; optional bg/fg only if explicitly provided
        opts = {"text": text, "style": style}
        if bg is not None:
            opts["background"] = bg
        if fg is not None:
            opts["foreground"] = fg
        super().__init__(parent, **opts, **kwargs)


class ModernEntry(ttk.Entry):
    def __init__(self, parent, **kwargs):
        # ttk.Entry styling is controlled via styles, not constructor bg/fg kwargs
        style = kwargs.pop("style", "Modern.TEntry")
        kwargs.pop("bg", None)
        kwargs.pop("fg", None)
        kwargs.pop("insertbackground", None)
        super().__init__(parent, style=style, **kwargs)


class ModernFrameWithBorder(tk.Frame):
    """A Frame styled to look like a LabelFrame but without thick ttk borders."""
    def __init__(self, parent, text="", padding=8, bg=None, fg=None, **kwargs):
        if bg is None:
            bg = COLORS["bg"]
        if fg is None:
            fg = COLORS["fg"]

        # Create a container frame with subtle border
        tk.Frame.__init__(self, parent, background=bg, padx=padding, pady=padding, **kwargs)

        # Add label at top (use positive values only for tkinter compatibility)
        self._label = tk.Label(self, text=text, font=("Segoe UI", 9, "bold"),
                               foreground=COLORS["accent"], background=bg)
        self._label.pack(fill=tk.X, padx=(0, padding), pady=(0, padding))

    def pack(self, **kwargs):
        kwargs.setdefault("fill", tk.BOTH)
        return super().pack(**kwargs)


class ModernTreeview(ttk.Treeview):
    """A Treeview with dark theme styling."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)


# ---------------------------------------------------------------------------
# Main GUI application
# ---------------------------------------------------------------------------
class SexOffenderGUI:
    """Main application window with tabs for scraping, searching, and analysis."""

    def __init__(self, root):
        self.root = root
        self._setup_styles()
        self.root.title("Sex Offender Database Scraper")
        self.root.geometry("1200x750")
        self.root.minsize(900, 600)

        # State
        self.sources = []
        self.selected_states = set()
        self.log_queue = queue.Queue()
        self.is_running = False
        self.db_path = "data/offenders.db"

        # Build UI
        self._build_ui()
        self._load_sources()
        self._poll_log_queue()

    def _setup_styles(self):
        """Configure ttk styles for a modern dark theme."""
        style = ttk.Style(self.root)
        try:
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        # Frame styles
        style.configure("Modern.TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground=COLORS["accent"], background=COLORS["bg"])
        style.configure("Modern.TLabel", font=FONT_BODY, foreground=COLORS["fg"], background=COLORS["bg"])
        style.configure("Status.TLabel", font=("Segoe UI", 9), foreground=COLORS["green"], background=COLORS["panel"])

        # Button styles
        style.configure("Modern.TButton", font=("Segoe UI", 9), padding=6)
        style.map("Modern.TButton",
                  background=[("active", COLORS["hover"]), ("pressed", COLORS["accent"])])

        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), foreground="#fff")
        style.map("Accent.TButton",
                  background=[("active", COLORS["accent"]), ("pressed", "#74a8fc")],
                  foreground=[("active", "#fff"), ("pressed", "#fff")])

        # Treeview styles
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), foreground=COLORS["fg"], background=COLORS["panel"])
        style.configure("Treeview", font=FONT_BODY, fieldbackground=COLORS["bg"], foreground=COLORS["fg"])

        # Entry styles
        style.configure("Modern.TEntry", fieldbackground=COLORS["input_bg"], foreground=COLORS["fg"], insertcolor=COLORS["accent"])

        # LabelFrame with subtle border
        style.configure("Modern.TLabelFrame", background=COLORS["bg"], foreground=COLORS["fg"], borderwidth=1)

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------
    def _build_ui(self):
        """Build the full application layout."""
        root = self.root

        # Top bar with title and stats
        top_bar = ModernFrame(root)
        top_bar.pack(fill=tk.X, padx=10, pady=(8, 4))

        ModernLabel(top_bar, text="🔍 Sex Offender Database Scraper", style="Header.TLabel").pack(side=tk.LEFT)
        self.stats_label = ModernLabel(top_bar, text="Ready", style="Status.TLabel")
        self.stats_label.pack(side=tk.RIGHT, padx=10)

        # Tab control
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 6))

        # --- TAB 1: Scrape ---
        self.tab_scrape = ModernFrame(self.tabs)
        self.tabs.add(self.tab_scrape, text="📥 Scrape")
        self._build_scrape_tab()

        # --- TAB 2: Search ---
        self.tab_search = ModernFrame(self.tabs)
        self.tabs.add(self.tab_search, text="🔎 Search")
        self._build_search_tab()

        # --- TAB 3: Misclassification ---
        self.tab_misclass = ModernFrame(self.tabs)
        self.tabs.add(self.tab_misclass, text="⚠️ Misclassification")
        self._build_misclass_tab()

        # --- TAB 4: NSOPW ethnic search ---
        self.tab_nsopw = ModernFrame(self.tabs)
        self.tabs.add(self.tab_nsopw, text="🌐 NSOPW")
        self._build_nsopw_tab()
        self._nsopw_cancel = False

        # --- TAB 5: Data Viewer ---
        self.tab_viewer = ModernFrame(self.tabs)
        self.tabs.add(self.tab_viewer, text="📊 Data Viewer")
        self._build_data_viewer_tab()

        # Bottom log panel
        log_frame = ModernFrameWithBorder(root, text="Activity Log")
        log_frame.pack(fill=tk.X, padx=10, pady=(4, 8))

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=8, wrap=tk.WORD, state=tk.DISABLED, font=FONT_MONO,
            background=COLORS["bg"], foreground=COLORS["fg"]
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # -----------------------------------------------------------------------
    # Scrape tab
    # -----------------------------------------------------------------------
    def _build_scrape_tab(self):
        frame = self.tab_scrape

        # Top controls
        ctrl = ModernFrame(frame)
        ctrl.pack(fill=tk.X, padx=10, pady=(8, 4))

        ModernLabel(ctrl, text="Select States:").pack(side=tk.LEFT, padx=(0, 6))

        self.scrape_direct_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctrl, text="Direct downloads only", variable=self.scrape_direct_only).pack(side=tk.LEFT, padx=4)

        ttk.Button(ctrl, text="Select All", command=self._scrape_select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="Clear Selection", command=self._scrape_clear_selection).pack(side=tk.LEFT, padx=2)

        # State list (left side)
        left = ModernFrame(frame)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 4), pady=6)

        ModernLabel(left, text="Available States", style="Header.TLabel").pack(anchor=tk.W)

        # Treeview for states
        columns = ("abbr", "method", "notes")
        self.scrape_tree = ttk.Treeview(
            left, columns=columns, show="tree headings", selectmode="extended"
        )
        self.scrape_tree.heading("#0", text="State")
        self.scrape_tree.heading("abbr", text="Abbr")
        self.scrape_tree.heading("method", text="Method")
        self.scrape_tree.heading("notes", text="Notes")

        self.scrape_tree.column("#0", width=260, stretch=True)
        self.scrape_tree.column("abbr", width=50, anchor=tk.CENTER)
        self.scrape_tree.column("method", width=80, anchor=tk.CENTER)
        self.scrape_tree.column("notes", width=300)

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.scrape_tree.yview)
        hsb = ttk.Scrollbar(left, orient="horizontal", command=self.scrape_tree.xview)
        self.scrape_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.scrape_tree.pack(fill=tk.BOTH, expand=True)
        self.scrape_tree.bind("<<TreeviewSelect>>", self._scrape_on_select)

        # Right panel: options + action
        right = ModernFrame(frame)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 10), pady=6)

        opts = ModernFrameWithBorder(right, text="Options")
        opts.pack(fill=tk.X, pady=(0, 8))

        # Output folder
        ModernLabel(opts, text="Output Folder:").pack(anchor=tk.W)
        out_frame = ttk.Frame(opts)
        out_frame.pack(fill=tk.X, pady=2)
        default_out = Path("data/downloads")
        self.scrape_output_var = tk.StringVar(value=str(default_out))
        ModernEntry(out_frame, textvariable=self.scrape_output_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(out_frame, text="Browse", command=self._scrape_browse_output).pack(side=tk.LEFT, padx=4)

        # Delay
        ModernLabel(opts, text="Delay (seconds):").pack(anchor=tk.W, pady=(6, 0))
        self.scrape_delay_var = tk.DoubleVar(value=2.0)
        ttk.Scale(opts, from_=0.5, to=10.0, variable=self.scrape_delay_var, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=2)

        # Action buttons
        act = ttk.Frame(right)
        act.pack(fill=tk.X, pady=(8, 0))

        self.scrape_btn = ModernButton(
            act, text="▶ Start Scraping", style="Accent.TButton"
        )
        self.scrape_btn.config(command=self._start_scrape)
        self.scrape_btn.pack(fill=tk.X, ipady=6)

        ttk.Button(act, text="Open Output Folder", command=self._open_output_folder).pack(fill=tk.X, pady=(4, 0))

        # Progress
        self.scrape_progress = ttk.Progressbar(right, mode="determinate", maximum=100)
        self.scrape_progress.pack(fill=tk.X, pady=(8, 4))

    def _scrape_select_all(self):
        for item in self.scrape_tree.get_children():
            vals = self.scrape_tree.item(item, "values")
            if not vals:
                continue
            # When "direct only" is checked, only select bulk-download states
            if self.scrape_direct_only.get():
                tags = self.scrape_tree.item(item, "tags")
                if "direct" not in tags:
                    continue
            self.scrape_tree.selection_add(item)
        self._update_scrape_selection()

    def _scrape_clear_selection(self):
        self.scrape_tree.selection_remove(*self.scrape_tree.selection())
        self._update_scrape_selection()

    def _scrape_on_select(self, event=None):
        self._update_scrape_selection()

    def _update_scrape_selection(self):
        items = self.scrape_tree.selection()
        self.selected_states.clear()
        for item in items:
            vals = self.scrape_tree.item(item, "values")
            if vals:
                self.selected_states.add(vals[0])
        count = len(self.selected_states)
        self.stats_label.config(text=f"{count} states selected")

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
        delay = self.scrape_delay_var.get()
        direct_only = self.scrape_direct_only.get()

        # Determine targets: honor tree selection when present; otherwise
        # fall back to all (optionally filtered to direct-download sources).
        if states:
            registries = []
            for s in states:
                reg = get_registry_by_abbr(s)
                if reg:
                    registries.append(reg)
            if direct_only:
                registries = [r for r in registries if r.direct_downloads]
        elif direct_only:
            registries = [r for r in REGISTRIES if r.abbr != "US" and r.direct_downloads]
        else:
            messagebox.showwarning(
                "No selection",
                "Select one or more states, or enable 'Direct downloads only' "
                "to scrape all bulk-download sources."
            )
            return

        if not registries:
            messagebox.showwarning("No targets", "No matching registries to scrape.")
            return

        output_dir = Path(self.scrape_output_var.get())
        output_dir.mkdir(parents=True, exist_ok=True)

        self._set_running(True)
        self.scrape_progress["value"] = 0
        total_targets = len(registries)

        def log(msg):
            self.log_queue.put(msg)

        def worker():
            import csv
            try:
                total_records = 0
                for i, reg in enumerate(registries):
                    abbr = reg.abbr
                    log(f"[{abbr}] Scraping {reg.name}...")

                    scraper = ScraperFactory.create(abbr, delay=delay)
                    try:
                        records = scraper.scrape()
                    finally:
                        scraper.close()

                    if records:
                        csv_path = output_dir / f"{abbr.lower()}_offenders.csv"
                        fieldnames = []
                        seen = set()
                        for record in records:
                            for key in record.keys():
                                if key not in seen:
                                    seen.add(key)
                                    fieldnames.append(key)
                        with open(csv_path, "w", newline="", encoding="utf-8") as f:
                            writer = csv.DictWriter(
                                f, fieldnames=fieldnames, extrasaction="ignore"
                            )
                            writer.writeheader()
                            for record in records:
                                writer.writerow(record)

                        log(f"  ✓ Saved {len(records)} records to {csv_path}")
                        total_records += len(records)
                    else:
                        log("  - No records found")

                    pct = int(((i + 1) / max(total_targets, 1)) * 100)
                    self.root.after(0, lambda p=pct: self.scrape_progress.configure(value=p))

                log(f"\n{'='*50}")
                log(f"Total records scraped: {total_records}")
                log(f"Output directory: {output_dir}")
                log(f"{'='*50}")

            except Exception as e:
                log(f"ERROR: {e}")
            finally:
                self.root.after(0, lambda: self._set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    # -----------------------------------------------------------------------
    # Search tab
    # -----------------------------------------------------------------------
    def _build_search_tab(self):
        frame = self.tab_search

        # Controls
        ctrl = ModernFrame(frame)
        ctrl.pack(fill=tk.X, padx=10, pady=(8, 4))

        ModernLabel(ctrl, text="Search:").pack(side=tk.LEFT, padx=(0, 6))

        self.search_name_var = tk.StringVar()
        ModernEntry(ctrl, textvariable=self.search_name_var, width=25).pack(side=tk.LEFT, padx=2)

        ttk.Label(ctrl, text="State:").pack(side=tk.LEFT, padx=(8, 2))
        self.search_state_var = tk.StringVar(value="")
        state_cb = ttk.Combobox(ctrl, textvariable=self.search_state_var, width=10, values=["", "ALL"])
        state_cb.pack(side=tk.LEFT, padx=2)

        ttk.Label(ctrl, text="Race:").pack(side=tk.LEFT, padx=(8, 2))
        self.search_race_var = tk.StringVar(value="")
        race_cb = ttk.Combobox(ctrl, textvariable=self.search_race_var, width=10, values=["", "WHITE", "BLACK", "HISPANIC", "ASIAN", "NATIVE AMERICAN"])
        race_cb.pack(side=tk.LEFT, padx=2)

        ttk.Button(ctrl, text="Search", command=self._do_search).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(ctrl, text="Show All", command=lambda: self._do_search(name="", state="", race="")).pack(side=tk.LEFT, padx=2)

        # Results tree
        res_frame = ModernFrame(frame)
        res_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))

        columns = ("name", "race", "state", "county", "age", "address")
        self.search_tree = ttk.Treeview(res_frame, columns=columns, show="headings")
        for col in columns:
            self.search_tree.heading(col, text=col.upper())
            self.search_tree.column(col, width=120)

        vsb = ttk.Scrollbar(res_frame, orient="vertical", command=self.search_tree.yview)
        hsb = ttk.Scrollbar(res_frame, orient="horizontal", command=self.search_tree.xview)
        self.search_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.search_tree.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.search_status = ModernLabel(frame, text="Enter a name or click Show All", style="Status.TLabel")
        self.search_status.pack(fill=tk.X, padx=10, pady=(4, 0))

    def _do_search(self, name=None, state=None, race=None):
        from scraper.searcher import SexOffenderSearcher

        # Explicit empty-string args from "Show All" must win over widget values
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
                self.search_status.config(
                    text=f"{len(results.records)} matches for '{name}' ({results.query_time_ms:.1f}ms)"
                )
            elif race:
                results = searcher.search_by_race(
                    race=race,
                    state=state if state and state != "ALL" else None,
                    limit=500,
                )
                self._populate_search_tree(results.records)
                self.search_status.config(text=f"{len(results.records)} records with race '{race}'")
            elif state and state != "ALL":
                results = searcher.search_by_state(state=state, limit=500)
                self._populate_search_tree(results.records)
                self.search_status.config(text=f"{len(results.records)} offenders in {state}")
            else:
                dist = searcher.get_race_distribution()
                self._show_race_distribution(dist)
                total = searcher.get_total_count()
                self.search_status.config(text=f"Race distribution ({total} total records)")
        finally:
            searcher.close()

    def _populate_search_tree(self, records):
        for item in self.search_tree.get_children():
            self.search_tree.delete(item)
        for r in records[:500]:
            name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "N/A"
            race = (r.get("race") or "N/A")[:12]
            state = (r.get("state") or "N/A")[:6]
            county = (r.get("county") or "N/A")[:15]
            age = str(r.get("age", ""))
            addr = (r.get("address") or "")[:30]
            self.search_tree.insert("", "end", values=(name, race, state, county, age, addr))

    def _show_race_distribution(self, dist):
        for item in self.search_tree.get_children():
            self.search_tree.delete(item)
        total = sum(d.get("count", 0) for d in dist)
        for d in dist:
            race = d.get("race", "N/A")
            count = d.get("count", 0)
            pct = (count / total * 100) if total else 0
            bar = "#" * int(pct / 2)
            self.search_tree.insert("", "end", values=(f"{race:<15}", str(count).rjust(8), "", f" {pct:6.1f}%", "", bar))

    # -----------------------------------------------------------------------
    # Misclassification tab
    # -----------------------------------------------------------------------
    def _build_misclass_tab(self):
        frame = self.tab_misclass

        ctrl = ModernFrame(frame)
        ctrl.pack(fill=tk.X, padx=10, pady=(8, 4))

        ModernLabel(ctrl, text="Analyze:").pack(side=tk.LEFT, padx=(0, 6))

        self.misclass_ethnicity_var = tk.StringVar(value="all")
        ttk.Combobox(ctrl, textvariable=self.misclass_ethnicity_var, width=15,
                     values=["all", "hispanic", "asian", "african_american"]).pack(side=tk.LEFT, padx=2)

        ModernLabel(ctrl, text="Min Confidence:").pack(side=tk.LEFT, padx=(8, 4))
        self.misclass_conf_var = tk.DoubleVar(value=0.5)
        ttk.Spinbox(ctrl, from_=0.1, to=1.0, increment=0.05, textvariable=self.misclass_conf_var, width=6).pack(side=tk.LEFT, padx=2)

        ModernLabel(ctrl, text="Max Records:").pack(side=tk.LEFT, padx=(8, 4))
        self.misclass_limit_var = tk.IntVar(value=10000)
        ttk.Spinbox(ctrl, from_=1000, to=100000, increment=1000, textvariable=self.misclass_limit_var, width=8).pack(side=tk.LEFT, padx=2)

        ttk.Button(ctrl, text="Analyze", command=self._run_misclassification).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Button(ctrl, text="Export to CSV", command=self._export_misclass).pack(side=tk.LEFT, padx=2)

        # Results tree
        res_frame = ModernFrame(frame)
        res_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))

        columns = ("name", "recorded_race", "likely_ethnicity", "confidence", "matching_names")
        self.misclass_tree = ttk.Treeview(res_frame, columns=columns, show="headings")
        for col in columns:
            self.misclass_tree.heading(col, text=col.upper())
            self.misclass_tree.column(col, width=140)

        vsb = ttk.Scrollbar(res_frame, orient="vertical", command=self.misclass_tree.yview)
        self.misclass_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.misclass_tree.pack(fill=tk.BOTH, expand=True)

        self.misclass_status = ModernLabel(frame, text="Click Analyze to find misclassifications", style="Status.TLabel")
        self.misclass_status.pack(fill=tk.X, padx=10, pady=(4, 0))

    def _run_misclassification(self):
        from scraper.searcher import SexOffenderSearcher

        searcher = SexOffenderSearcher(db_path=self.db_path)

        ethnicity = self.misclass_ethnicity_var.get()
        min_conf = self.misclass_conf_var.get()
        limit = self.misclass_limit_var.get()

        try:
            if ethnicity == "hispanic":
                results = searcher.find_hispanic_misclassifications(min_confidence=min_conf, limit=limit)
            elif ethnicity == "asian":
                results = searcher.find_asian_misclassifications(min_confidence=min_conf, limit=limit)
            elif ethnicity == "african_american":
                results = searcher.find_african_american_misclassifications(min_confidence=min_conf, limit=limit)
            else:
                results = searcher.analyze_ethnicities(min_confidence=min_conf, limit=limit)
        finally:
            searcher.close()

        for item in self.misclass_tree.get_children():
            self.misclass_tree.delete(item)

        for mc in results[:500]:
            name = f"{mc.record.get('first_name', '') or ''} {mc.record.get('last_name', '') or ''}".strip() or "N/A"
            race = (mc.expected_race or "N/A")[:12]
            likely = (mc.likely_ethnicity or "")[:15]
            conf = f"{mc.confidence:.3f}"
            names = "; ".join(mc.matching_names[:3])
            self.misclass_tree.insert("", "end", values=(name, race, likely, conf, names))

        self.misclass_status.config(text=f"Found {len(results)} potential misclassifications")
        self.log_queue.put(f"Misclassification analysis complete: {len(results)} results.")

    def _export_misclass(self):
        from scraper.searcher import SexOffenderSearcher

        filepath = filedialog.asksaveasfilename(defaultextension=".csv", title="Export misclassifications")
        if not filepath:
            return

        searcher = SexOffenderSearcher(db_path=self.db_path)
        ethnicity = self.misclass_ethnicity_var.get()
        eth_filter = None if ethnicity == "all" else ethnicity
        try:
            count = searcher.export_misclassifications(
                filepath,
                min_confidence=self.misclass_conf_var.get(),
                ethnicity_filter=eth_filter,
            )
        finally:
            searcher.close()
        messagebox.showinfo("Exported", f"Exported {count} records to {filepath}")

    # -----------------------------------------------------------------------
    # NSOPW ethnic search tab
    # -----------------------------------------------------------------------
    def _build_nsopw_tab(self):
        frame = self.tab_nsopw

        # Scrollable container for many settings
        canvas = tk.Canvas(frame, background=COLORS["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = ModernFrame(canvas)
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        ModernLabel(
            inner,
            text="NSOPW Ethnic Name Search → Local Database",
            style="Header.TLabel",
        ).pack(anchor=tk.W, padx=10, pady=(8, 2))
        ModernLabel(
            inner,
            text="Uses partial first-name matching (e.g. 'M' + surname) to cut query count. "
                 "Rate limits protect nsopw.gov. Report HTML is archived for validation.",
        ).pack(anchor=tk.W, padx=10, pady=(0, 8))

        # --- Search settings ---
        search_box = ModernFrameWithBorder(inner, text="Search settings")
        search_box.pack(fill=tk.X, padx=10, pady=4)

        row1 = ModernFrame(search_box)
        row1.pack(fill=tk.X, pady=2)
        ModernLabel(row1, text="Ethnicity list:").pack(side=tk.LEFT)
        self.nsopw_ethnicity = tk.StringVar(value="hispanic")
        ttk.Combobox(
            row1, textvariable=self.nsopw_ethnicity, width=18, state="readonly",
            values=[
                "hispanic", "asian", "african_american", "arabic",
                "jewish", "portuguese", "native_american", "european", "all",
            ],
        ).pack(side=tk.LEFT, padx=6)

        ModernLabel(row1, text="Surnames / group:").pack(side=tk.LEFT, padx=(12, 0))
        self.nsopw_surnames = tk.IntVar(value=10)
        ttk.Spinbox(row1, from_=1, to=100, textvariable=self.nsopw_surnames, width=6).pack(
            side=tk.LEFT, padx=4
        )

        row2 = ModernFrame(search_box)
        row2.pack(fill=tk.X, pady=2)
        ModernLabel(row2, text="First-name mode:").pack(side=tk.LEFT)
        self.nsopw_first_mode = tk.StringVar(value="initials")
        ttk.Combobox(
            row2, textvariable=self.nsopw_first_mode, width=12, state="readonly",
            values=["initials", "full", "custom"],
        ).pack(side=tk.LEFT, padx=6)
        ModernLabel(
            row2,
            text="initials = A–Z partial match (fewest queries)",
        ).pack(side=tk.LEFT, padx=4)

        row3 = ModernFrame(search_box)
        row3.pack(fill=tk.X, pady=2)
        ModernLabel(row3, text="Custom first names/prefixes:").pack(side=tk.LEFT)
        self.nsopw_custom_firsts = tk.StringVar(value="")
        ModernEntry(row3, textvariable=self.nsopw_custom_firsts, width=50).pack(
            side=tk.LEFT, padx=6, fill=tk.X, expand=True
        )

        row4 = ModernFrame(search_box)
        row4.pack(fill=tk.X, pady=2)
        ModernLabel(row4, text="Jurisdictions (blank = all):").pack(side=tk.LEFT)
        self.nsopw_jurisdictions = tk.StringVar(value="")
        ModernEntry(row4, textvariable=self.nsopw_jurisdictions, width=40).pack(
            side=tk.LEFT, padx=6, fill=tk.X, expand=True
        )
        ModernLabel(row4, text="e.g. CA,TX,FL,NY").pack(side=tk.LEFT)

        # --- Caps ---
        caps_box = ModernFrameWithBorder(inner, text="Run limits")
        caps_box.pack(fill=tk.X, padx=10, pady=4)

        crow = ModernFrame(caps_box)
        crow.pack(fill=tk.X, pady=2)
        ModernLabel(crow, text="Max searches:").pack(side=tk.LEFT)
        self.nsopw_max_searches = tk.IntVar(value=40)
        ttk.Spinbox(crow, from_=1, to=5000, textvariable=self.nsopw_max_searches, width=8).pack(
            side=tk.LEFT, padx=4
        )
        ModernLabel(crow, text="Max report fetches:").pack(side=tk.LEFT, padx=(12, 0))
        self.nsopw_max_reports = tk.IntVar(value=80)
        ttk.Spinbox(crow, from_=0, to=10000, textvariable=self.nsopw_max_reports, width=8).pack(
            side=tk.LEFT, padx=4
        )

        # --- Rate limits ---
        rate_box = ModernFrameWithBorder(inner, text="Rate limits (protect NSOPW / state sites)")
        rate_box.pack(fill=tk.X, padx=10, pady=4)

        rrow = ModernFrame(rate_box)
        rrow.pack(fill=tk.X, pady=2)
        ModernLabel(rrow, text="Search delay (sec):").pack(side=tk.LEFT)
        self.nsopw_search_delay = tk.DoubleVar(value=2.0)
        ttk.Spinbox(
            rrow, from_=1.5, to=30.0, increment=0.5,
            textvariable=self.nsopw_search_delay, width=8,
        ).pack(side=tk.LEFT, padx=4)
        ModernLabel(rrow, text="Report delay (sec):").pack(side=tk.LEFT, padx=(12, 0))
        self.nsopw_report_delay = tk.DoubleVar(value=2.0)
        ttk.Spinbox(
            rrow, from_=1.5, to=30.0, increment=0.5,
            textvariable=self.nsopw_report_delay, width=8,
        ).pack(side=tk.LEFT, padx=4)
        ModernLabel(rrow, text="(minimum 1.5s enforced)").pack(side=tk.LEFT, padx=6)

        # --- Storage ---
        store_box = ModernFrameWithBorder(inner, text="Storage & enrichment")
        store_box.pack(fill=tk.X, padx=10, pady=4)

        srow = ModernFrame(store_box)
        srow.pack(fill=tk.X, pady=2)
        ModernLabel(srow, text="Database:").pack(side=tk.LEFT)
        self.nsopw_db_path = tk.StringVar(value=self.db_path)
        ModernEntry(srow, textvariable=self.nsopw_db_path, width=40).pack(
            side=tk.LEFT, padx=4, fill=tk.X, expand=True
        )
        ttk.Button(srow, text="Browse…", command=self._nsopw_browse_db).pack(side=tk.LEFT, padx=2)

        srow2 = ModernFrame(store_box)
        srow2.pack(fill=tk.X, pady=2)
        ModernLabel(srow2, text="HTML archive folder:").pack(side=tk.LEFT)
        self.nsopw_html_dir = tk.StringVar(value="data/report_pages")
        ModernEntry(srow2, textvariable=self.nsopw_html_dir, width=40).pack(
            side=tk.LEFT, padx=4, fill=tk.X, expand=True
        )
        ttk.Button(srow2, text="Browse…", command=self._nsopw_browse_html).pack(side=tk.LEFT, padx=2)
        ttk.Button(srow2, text="Open folder", command=self._nsopw_open_html_folder).pack(
            side=tk.LEFT, padx=2
        )

        srow3 = ModernFrame(store_box)
        srow3.pack(fill=tk.X, pady=4)
        self.nsopw_save_html = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            srow3, text="Save report HTML and link to each DB entry",
            variable=self.nsopw_save_html,
        ).pack(side=tk.LEFT, padx=2)
        self.nsopw_enrich = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            srow3, text="Fetch report pages for demographics",
            variable=self.nsopw_enrich,
        ).pack(side=tk.LEFT, padx=8)
        self.nsopw_skip_existing = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            srow3, text="Skip existing report URLs",
            variable=self.nsopw_skip_existing,
        ).pack(side=tk.LEFT, padx=8)

        # --- Actions ---
        act = ModernFrame(inner)
        act.pack(fill=tk.X, padx=10, pady=8)
        self.nsopw_start_btn = ModernButton(
            act, text="▶ Start NSOPW Search", style="Accent.TButton",
            command=self._start_nsopw,
        )
        self.nsopw_start_btn.pack(side=tk.LEFT, ipady=4, padx=(0, 8))
        self.nsopw_cancel_btn = ttk.Button(
            act, text="Cancel", command=self._cancel_nsopw, state=tk.DISABLED
        )
        self.nsopw_cancel_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(act, text="Open Database Folder", command=self._nsopw_open_db_folder).pack(
            side=tk.LEFT, padx=4
        )

        self.nsopw_progress = ttk.Progressbar(inner, mode="indeterminate")
        self.nsopw_progress.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.nsopw_status = ModernLabel(
            inner,
            text="Ready. Prefer 'initials' mode + polite delays.",
            style="Status.TLabel",
        )
        self.nsopw_status.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Recent results preview
        prev = ModernFrameWithBorder(inner, text="Recent inserts (this session)")
        prev.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        cols = ("name", "state", "url", "html")
        self.nsopw_tree = ttk.Treeview(prev, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (160, 50, 320, 200)):
            self.nsopw_tree.heading(c, text=c.upper())
            self.nsopw_tree.column(c, width=w)
        self.nsopw_tree.pack(fill=tk.BOTH, expand=True)
        self.nsopw_tree.bind("<Double-1>", self._nsopw_open_selected)

    def _nsopw_browse_db(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".db",
            filetypes=[("SQLite", "*.db"), ("All", "*.*")],
            initialfile=Path(self.nsopw_db_path.get()).name,
        )
        if path:
            self.nsopw_db_path.set(path)
            self.db_path = path

    def _nsopw_browse_html(self):
        path = filedialog.askdirectory(initialdir=self.nsopw_html_dir.get() or "data")
        if path:
            self.nsopw_html_dir.set(path)

    def _nsopw_open_html_folder(self):
        path = Path(self.nsopw_html_dir.get() or "data/report_pages")
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _nsopw_open_db_folder(self):
        path = Path(self.nsopw_db_path.get() or self.db_path).expanduser().resolve().parent
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _open_path(self, path: Path):
        try:
            import os
            if os.name == "nt":
                os.startfile(str(path))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Cannot open", str(e))

    def _cancel_nsopw(self):
        self._nsopw_cancel = True
        self.log_queue.put("NSOPW cancel requested…")
        self.nsopw_status.config(text="Cancelling…")

    def _start_nsopw(self):
        if self.is_running:
            return

        ethnicity = self.nsopw_ethnicity.get()
        first_mode = self.nsopw_first_mode.get()
        custom = self.nsopw_custom_firsts.get().strip()
        first_names = None
        if first_mode == "custom" or custom:
            first_names = [x.strip() for x in custom.split(",") if x.strip()]
            if not first_names and first_mode == "custom":
                messagebox.showwarning(
                    "Custom first names",
                    "Enter comma-separated first names or prefixes, or switch mode to 'initials'.",
                )
                return
            if first_names:
                first_mode = "custom"

        jurs_raw = self.nsopw_jurisdictions.get().strip()
        jurisdictions = (
            [x.strip().upper() for x in jurs_raw.split(",") if x.strip()]
            if jurs_raw else None
        )

        db_path = self.nsopw_db_path.get().strip() or self.db_path
        html_dir = self.nsopw_html_dir.get().strip() or "data/report_pages"
        search_delay = max(1.5, float(self.nsopw_search_delay.get()))
        report_delay = max(1.5, float(self.nsopw_report_delay.get()))

        self._nsopw_cancel = False
        self._set_running(True)
        self.nsopw_start_btn.config(state=tk.DISABLED)
        self.nsopw_cancel_btn.config(state=tk.NORMAL)
        self.nsopw_progress.start(12)
        self.nsopw_status.config(text="Running NSOPW search…")

        def log(msg):
            self.log_queue.put(msg)

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
                    ethnicity=ethnicity,
                    surnames_limit=int(self.nsopw_surnames.get()),
                    first_names=first_names,
                    first_mode=first_mode,
                    jurisdictions=jurisdictions,
                    max_searches=int(self.nsopw_max_searches.get()),
                    max_report_fetches=int(self.nsopw_max_reports.get()),
                    skip_existing_urls=bool(self.nsopw_skip_existing.get()),
                    enrich_reports=bool(self.nsopw_enrich.get()),
                    save_html=bool(self.nsopw_save_html.get()),
                    log=log,
                )
                # Preview last few rows
                try:
                    rows = builder.db._conn.execute(
                        "SELECT first_name, last_name, state, source_url, report_html_path "
                        "FROM offenders ORDER BY id DESC LIMIT 50"
                    ).fetchall()
                    preview = [dict(r) for r in rows]
                except Exception:
                    preview = []

                def done():
                    self._set_running(False)
                    self.nsopw_start_btn.config(state=tk.NORMAL)
                    self.nsopw_cancel_btn.config(state=tk.DISABLED)
                    self.nsopw_progress.stop()
                    self.nsopw_status.config(
                        text=(
                            f"Done: {stats.inserted} inserted, "
                            f"{stats.reports_fetched} reports, "
                            f"{stats.html_saved} HTML saved, "
                            f"{stats.searches} searches"
                        )
                    )
                    self.db_path = db_path
                    for item in self.nsopw_tree.get_children():
                        self.nsopw_tree.delete(item)
                    for r in preview:
                        name = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
                        self.nsopw_tree.insert(
                            "",
                            "end",
                            values=(
                                name,
                                r.get("state") or "",
                                (r.get("source_url") or "")[:80],
                                (r.get("report_html_path") or "")[:60],
                            ),
                        )
                    messagebox.showinfo(
                        "NSOPW complete",
                        f"Inserted {stats.inserted} records.\n"
                        f"HTML pages saved: {stats.html_saved}\n"
                        f"Database: {db_path}",
                    )

                self.root.after(0, done)
            except Exception as e:
                log(f"NSOPW ERROR: {e}")

                def fail():
                    self._set_running(False)
                    self.nsopw_start_btn.config(state=tk.NORMAL)
                    self.nsopw_cancel_btn.config(state=tk.DISABLED)
                    self.nsopw_progress.stop()
                    self.nsopw_status.config(text=f"Error: {e}")
                    messagebox.showerror("NSOPW error", str(e))

                self.root.after(0, fail)
            finally:
                builder.close()

        threading.Thread(target=worker, daemon=True).start()

    def _nsopw_open_selected(self, event=None):
        sel = self.nsopw_tree.selection()
        if not sel:
            return
        vals = self.nsopw_tree.item(sel[0], "values")
        if len(vals) < 4:
            return
        url, html_path = vals[2], vals[3]
        # Prefer local HTML for validation
        if html_path:
            p = Path(html_path)
            if p.exists():
                self._open_path(p)
                return
        if url:
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception as e:
                messagebox.showerror("Open link", str(e))

    # -----------------------------------------------------------------------
    # Data Viewer tab
    # -----------------------------------------------------------------------
    def _build_data_viewer_tab(self):
        frame = self.tab_viewer

        ctrl = ModernFrame(frame)
        ctrl.pack(fill=tk.X, padx=10, pady=(8, 4))

        ttk.Button(ctrl, text="Load CSV File...", command=self._load_csv_to_viewer).pack(side=tk.LEFT, padx=2)
        ttk.Label(ctrl, text="Search:").pack(side=tk.LEFT, padx=(8, 2))
        self.viewer_search_var = tk.StringVar()
        ModernEntry(ctrl, textvariable=self.viewer_search_var, width=20).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="Filter", command=lambda: self._apply_viewer_filter()).pack(side=tk.LEFT, padx=2)

        # Treeview
        tree_frame = ModernFrame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))

        self.viewer_tree = ttk.Treeview(tree_frame, show="headings")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.viewer_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.viewer_tree.xview)
        self.viewer_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.viewer_tree.pack(fill=tk.BOTH, expand=True)

        self.viewer_status = ModernLabel(frame, text="Load a CSV file to view data", style="Status.TLabel")
        self.viewer_status.pack(fill=tk.X, padx=10, pady=(4, 0))

    def _load_csv_to_viewer(self):
        filepath = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not filepath:
            return

        try:
            import csv
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                headers = list(reader.fieldnames or [])
                rows = list(reader)

            self.viewer_tree.delete(*self.viewer_tree.get_children())
            self.viewer_tree["columns"] = headers
            for col in headers:
                self.viewer_tree.heading(col, text=col, anchor=tk.W)
                self.viewer_tree.column(col, width=120, minwidth=50)

            max_show = 300
            for i, row in enumerate(rows[:max_show]):
                values = [str(row.get(h, ""))[:80] for h in headers]
                self.viewer_tree.insert("", "end", iid=str(i), values=values)

            self._viewer_data = rows
            self._viewer_headers = headers
            self._viewer_filtered = list(rows)
            self.viewer_status.config(text=f"Loaded {len(rows)} records from {Path(filepath).name}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not load CSV: {e}")

    def _apply_viewer_filter(self):
        term = self.viewer_search_var.get().lower().strip()
        if not hasattr(self, '_viewer_data'):
            return

        if not term:
            filtered = list(self._viewer_data)
        else:
            filtered = [r for r in self._viewer_data if any(term in str(v).lower() for v in r.values())]

        self.viewer_tree.delete(*self.viewer_tree.get_children())
        max_show = 300
        for i, row in enumerate(filtered[:max_show]):
            values = [str(row.get(h, ""))[:80] for h in self._viewer_headers]
            self.viewer_tree.insert("", "end", iid=str(i), values=values)

        self.viewer_status.config(text=f"Filtered to {len(filtered)} records")

    # -----------------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------------
    def _log(self, message: str):
        self.log_queue.put(message)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _append_log(self, message: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _set_running(self, running: bool):
        self.is_running = running
        self.scrape_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        if hasattr(self, "nsopw_start_btn"):
            # Keep NSOPW start disabled while any job runs
            if running and not getattr(self, "_nsopw_cancel", False):
                pass  # NSOPW worker manages its own buttons when it is the runner

    def _load_sources(self):
        from scraper.config import REGISTRIES
        try:
            self.sources = REGISTRIES
            self._refresh_scrape_list()
            self.log_queue.put("Loaded registry configurations for all 50 states + DC.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _refresh_scrape_list(self):
        self.scrape_tree.delete(*self.scrape_tree.get_children())
        # Configure tag styles once (tag_configure takes a tag name, not an item id)
        self.scrape_tree.tag_configure("direct", background="#1a3a2a")
        for reg in self.sources:
            if reg.abbr == "US":
                continue  # Skip national
            method = reg.scrape_method.upper()
            notes = (reg.notes or "")[:60]
            tags = ("direct",) if reg.direct_downloads else ()
            self.scrape_tree.insert(
                "", "end", text=reg.name, values=(reg.abbr, method, notes), tags=tags
            )

    def _open_output_folder(self):
        path = Path(self.scrape_output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        try:
            import os
            if os.name == "nt":
                os.startfile(str(path))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Cannot open folder", str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()

    # Try to use a nicer theme if available
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    app = SexOffenderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()