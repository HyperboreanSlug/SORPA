#!/usr/bin/env python3
"""
Public SOR Data Archiver - GUI Version

A simple tkinter GUI for downloading publicly available U.S. sex offender
registry bulk data files.

This only downloads data that government agencies already publish publicly.
Run with: python gui.py
"""

import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import csv

from core import load_sources, get_direct_sources, perform_downloads, DEFAULT_DELAY

# Custom keyword filtering is user-managed via the in-app editor or custom_keywords.txt file.
# All data is loaded at runtime from user-selected public CSV files.


class SorArchiverGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Public Sex Offender Registry Data Archiver")
        root.geometry("900x700")
        root.minsize(800, 600)

        self.sources = []
        self.direct_sources = []
        self.selected = set()  # set of abbrs

        self.log_queue = queue.Queue()
        self.is_running = False

        self._build_ui()
        self._load_sources()
        self._poll_log_queue()

    def _build_ui(self):
        # Main frame
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # Left: Source list
        left_frame = ttk.LabelFrame(main, text="Available Public Sources", padding=8)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        # Filter
        self.show_direct_only = tk.BooleanVar(value=True)
        filter_cb = ttk.Checkbutton(
            left_frame,
            text="Show only sources with direct bulk downloads",
            variable=self.show_direct_only,
            command=self._refresh_list
        )
        filter_cb.pack(anchor=tk.W, pady=(0, 6))

        # Treeview
        columns = ("abbr", "direct", "notes")
        self.tree = ttk.Treeview(
            left_frame,
            columns=columns,
            show="tree headings",
            selectmode="extended"
        )
        self.tree.heading("#0", text="Jurisdiction")
        self.tree.heading("abbr", text="Abbr")
        self.tree.heading("direct", text="Direct?")
        self.tree.heading("notes", text="Notes (short)")

        self.tree.column("#0", width=240, stretch=True)
        self.tree.column("abbr", width=50, anchor=tk.CENTER)
        self.tree.column("direct", width=70, anchor=tk.CENTER)
        self.tree.column("notes", width=280)

        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_change)

        # Selection buttons
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=6)

        ttk.Button(btn_frame, text="Select All Direct", command=self._select_all_direct).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Clear Selection", command=self._clear_selection).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Refresh List", command=self._refresh_list).pack(side=tk.LEFT, padx=2)

        # Right side: Controls
        right_frame = ttk.Frame(main)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y)

        # Options
        opts = ttk.LabelFrame(right_frame, text="Options", padding=10)
        opts.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(opts, text="Output folder:").pack(anchor=tk.W)
        out_frame = ttk.Frame(opts)
        out_frame.pack(fill=tk.X, pady=2)
        # Smart default for output directory
        # In bundled exe, prefer a folder next to the exe or in user's Documents
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            default_out = exe_dir / "SOR_Archives"
        else:
            default_out = Path.cwd() / "archives"
        self.output_var = tk.StringVar(value=str(default_out))
        ttk.Entry(out_frame, textvariable=self.output_var, width=30).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(out_frame, text="Browse...", command=self._browse_output).pack(side=tk.LEFT, padx=4)

        ttk.Label(opts, text="Delay between downloads (seconds):").pack(anchor=tk.W, pady=(8, 0))
        self.delay_var = tk.DoubleVar(value=DEFAULT_DELAY)
        delay_scale = ttk.Scale(opts, from_=0.5, to=10.0, variable=self.delay_var, orient=tk.HORIZONTAL)
        delay_scale.pack(fill=tk.X, pady=2)
        self.delay_label = ttk.Label(opts, text=f"{DEFAULT_DELAY:.1f} s")
        self.delay_label.pack(anchor=tk.W)
        delay_scale.bind("<Motion>", self._update_delay_label)

        # Action buttons
        action_frame = ttk.Frame(right_frame)
        action_frame.pack(fill=tk.X, pady=10)

        self.download_btn = ttk.Button(
            action_frame,
            text="⬇ Start Selected Downloads",
            command=self._start_download,
            style="Accent.TButton"
        )
        self.download_btn.pack(fill=tk.X, ipady=8)

        ttk.Button(
            action_frame,
            text="Open Output Folder",
            command=self._open_output_folder
        ).pack(fill=tk.X, pady=(6, 0))

        ttk.Button(
            action_frame,
            text="Open Local Data Search (for downloaded CSVs)",
            command=self._open_data_viewer
        ).pack(fill=tk.X, pady=(6, 0))

        ttk.Button(
            action_frame,
            text="Exit",
            command=self.root.destroy
        ).pack(fill=tk.X, pady=(12, 0))

        # Status
        self.status_var = tk.StringVar(value="Ready. Select sources with direct downloads and click Start.")
        status_label = ttk.Label(right_frame, textvariable=self.status_var, wraplength=280)
        status_label.pack(fill=tk.X, pady=6)

        # Progress
        self.progress = ttk.Progressbar(right_frame, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=4)

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Activity Log", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=8)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=14,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 9)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _update_delay_label(self, event=None):
        val = self.delay_var.get()
        self.delay_label.config(text=f"{val:.1f} s")

    def _load_sources(self):
        try:
            self.sources = load_sources()
            self.direct_sources = get_direct_sources(self.sources)
            self._refresh_list()
            self._log("Loaded sources. Direct bulk downloads available for a small number of jurisdictions.")
        except Exception as e:
            messagebox.showerror("Error loading sources", str(e))
            self._log(f"ERROR loading sources: {e}")

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())

        to_show = self.direct_sources if self.show_direct_only.get() else self.sources

        for s in to_show:
            has_direct = bool(s.get("direct_downloads"))
            direct_text = "YES" if has_direct else "search only"
            notes = (s.get("notes") or "")[:80]

            item = self.tree.insert(
                "",
                "end",
                text=s["jurisdiction"],
                values=(s["abbr"], direct_text, notes),
                tags=("direct",) if has_direct else ("search",)
            )

            # Pre-select direct ones if filter is on
            if has_direct and self.show_direct_only.get():
                self.tree.selection_add(item)

        # Color coding
        self.tree.tag_configure("direct", background="#e8f5e9")
        self.tree.tag_configure("search", background="#fff8e1")

        self._update_selection_from_tree()

    def _on_selection_change(self, event=None):
        self._update_selection_from_tree()

    def _update_selection_from_tree(self):
        selected_items = self.tree.selection()
        self.selected.clear()
        for item in selected_items:
            values = self.tree.item(item, "values")
            if values:
                abbr = values[0]
                self.selected.add(abbr)

        count = len(self.selected)
        self.status_var.set(f"{count} source(s) selected for download.")

    def _select_all_direct(self):
        self.tree.selection_remove(*self.tree.selection())
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if vals and vals[1] == "YES":
                self.tree.selection_add(item)
        self._update_selection_from_tree()

    def _clear_selection(self):
        self.tree.selection_remove(*self.tree.selection())
        self._update_selection_from_tree()

    def _browse_output(self):
        folder = filedialog.askdirectory(initialdir=self.output_var.get())
        if folder:
            self.output_var.set(folder)

    def _log(self, message: str):
        """Thread-safe log from main or worker thread."""
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
        state = tk.DISABLED if running else tk.NORMAL
        self.download_btn.config(state=state)
        # Disable other controls during run
        for child in self.tree.winfo_children():
            pass  # tree is hard to fully disable, we just control the button

    def _start_download(self):
        if self.is_running:
            return

        # Build list of selected source objects
        selected_abbrs = self.selected
        if not selected_abbrs:
            messagebox.showinfo("No selection", "Please select at least one source with direct downloads.")
            return

        targets = [s for s in self.sources if s["abbr"] in selected_abbrs and s.get("direct_downloads")]
        if not targets:
            messagebox.showwarning("Nothing to download", "Selected sources do not have direct download URLs.")
            return

        output_dir = Path(self.output_var.get())
        delay = self.delay_var.get()

        # Confirm
        if not messagebox.askyesno(
            "Confirm Download",
            f"Download data for {len(targets)} jurisdiction(s) to:\n{output_dir}\n\n"
            "This will fetch publicly available files.\nProceed?"
        ):
            return

        self._set_running(True)
        self.progress["value"] = 0
        self._append_log(f"Starting download of {len(targets)} source(s) with {delay:.1f}s delay...")

        # Run in background thread
        def worker():
            try:
                def log_cb(msg):
                    self.log_queue.put(msg)

                def prog_cb(current, total, msg):
                    pct = int((current / max(total, 1)) * 100)
                    self.root.after(0, lambda: self.progress.configure(value=pct))
                    if msg:
                        self.log_queue.put(msg)

                perform_downloads(
                    targets,
                    output_dir,
                    delay=delay,
                    log_callback=log_cb,
                    progress_callback=prog_cb
                )

                self.log_queue.put("✓ All selected downloads finished.")
                self.root.after(0, lambda: self.progress.configure(value=100))
                self.root.after(0, lambda: messagebox.showinfo("Complete", "Downloads finished. Check the log and output folder."))

            except Exception as e:
                self.log_queue.put(f"ERROR: {e}")
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.root.after(0, lambda: self._set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    def _open_output_folder(self):
        path = Path(self.output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        try:
            import os
            if os.name == "nt":  # Windows
                os.startfile(str(path))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Cannot open folder", str(e))

    def _open_data_viewer(self):
        """Open a separate window for searching loaded CSV data from downloads.
        This is a general purpose local search tool only.
        """
        viewer = tk.Toplevel(self.root)
        viewer.title("Local Data Search Tool - Public Registry CSVs")
        viewer.geometry("1100x650")
        viewer.minsize(800, 500)

        # Controls
        ctrl = ttk.Frame(viewer)
        ctrl.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(ctrl, text="Load CSV File...", command=lambda: self._load_csv(viewer)).pack(side=tk.LEFT, padx=2)

        ttk.Label(ctrl, text="Search / Filter term:").pack(side=tk.LEFT, padx=(10, 2))
        search_var = tk.StringVar()
        ttk.Entry(ctrl, textvariable=search_var, width=25).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="Apply Filter", command=lambda: self._apply_filter(viewer, search_var.get())).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="Clear Filter / Show All", command=lambda: self._apply_filter(viewer, "")).pack(side=tk.LEFT, padx=2)

        ttk.Button(ctrl, text="Export Filtered to CSV", command=lambda: self._export_filtered(viewer)).pack(side=tk.LEFT, padx=10)

        ttk.Label(ctrl, text="Custom keywords (comma sep):").pack(side=tk.LEFT, padx=5)
        custom_keywords_var = tk.StringVar(value="")
        ttk.Entry(ctrl, textvariable=custom_keywords_var, width=30).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="Apply Custom Filter", command=lambda: self._apply_keywords(viewer)).pack(side=tk.LEFT, padx=5)

        # Quick stats and additional filters for nice browsing
        viewer.stats_var = tk.StringVar(value="Load a CSV to see record counts and stats.")
        stats_frame = ttk.Frame(viewer)
        stats_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(stats_frame, textvariable=viewer.stats_var).pack(side=tk.LEFT)
        ttk.Button(stats_frame, text="Show All", command=lambda: self._apply_filter(viewer, "")).pack(side=tk.RIGHT, padx=2)
        ttk.Button(stats_frame, text="Show Filtered Only", command=lambda: self._show_filtered_only(viewer)).pack(side=tk.RIGHT, padx=2)
        ttk.Button(stats_frame, text="Export Filtered to CSV", command=lambda: self._export_filtered_view(viewer)).pack(side=tk.RIGHT, padx=2)
        ttk.Label(viewer, text="Tip: Click any column header to sort the table.", font=("TkDefaultFont", 8)).pack(fill=tk.X, padx=5)

        # Custom keyword editor for filtering
        kw_frame = ttk.LabelFrame(viewer, text="Custom Keywords for Filtering", padding=5)
        kw_frame.pack(fill=tk.X, padx=5, pady=5)

        viewer.kw_text = scrolledtext.ScrolledText(kw_frame, height=3, width=100)
        viewer.kw_text.pack(fill=tk.X)

        kw_btn_frame = ttk.Frame(kw_frame)
        kw_btn_frame.pack(fill=tk.X, pady=2)
        ttk.Button(kw_btn_frame, text="Load from 'custom_keywords.txt'", command=lambda: self._load_keywords(viewer)).pack(side=tk.LEFT)
        ttk.Button(kw_btn_frame, text="Save to 'custom_keywords.txt'", command=lambda: self._save_keywords(viewer)).pack(side=tk.LEFT, padx=5)
        ttk.Button(kw_btn_frame, text="Apply Keywords", command=lambda: self._apply_keywords(viewer)).pack(side=tk.LEFT, padx=5)

        # Initialize with file if present (user must provide their list; no list in source code)
        self._load_keywords_to_text(viewer)

        # Ensure stats_var for stats_frame (initialized early)
        if not hasattr(viewer, 'stats_var'):
            viewer.stats_var = tk.StringVar(value="Load a CSV to see stats.")

        # Treeview for results
        tree_frame = ttk.Frame(viewer)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        tree = ttk.Treeview(tree_frame, show="headings", selectmode="browse")
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.configure(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(viewer, orient="horizontal", command=tree.xview)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        tree.configure(xscrollcommand=hsb.set)

        # Status
        status_var = tk.StringVar(value="Load a CSV from your archives (e.g. from SOR_Archives or archives folder). Then use the search box.")
        ttk.Label(viewer, textvariable=status_var).pack(fill=tk.X, padx=5, pady=2)

        # Attach data to the viewer window for easy access in callbacks
        viewer.tree = tree
        viewer.data_rows = []
        viewer.headers = []
        viewer.status_var = status_var
        viewer.search_var = search_var  # not strictly needed
        viewer.filter_status_var = tk.StringVar(value="")
        viewer.custom_keywords_var = custom_keywords_var
        viewer.sort_col = None
        viewer.sort_reverse = False
        viewer.current_data = []

        ttk.Label(viewer, textvariable=viewer.filter_status_var).pack(fill=tk.X, padx=5)

        # Initial message
        tree.insert("", "end", values=("Load a CSV file to see columns and data here.",))

    def _load_csv(self, viewer):
        filepath = filedialog.askopenfilename(
            title="Select downloaded CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=Path(self.output_var.get()) if hasattr(self, 'output_var') else "."
        )
        if not filepath:
            return

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames or []
                rows = list(reader)

            viewer.headers = headers
            viewer.data_rows = rows

            # Setup tree columns
            tree = viewer.tree
            tree.delete(*tree.get_children())
            tree["columns"] = headers

            for col in headers:
                tree.heading(col, text=col, anchor=tk.W, command=lambda c=col: self._sort_tree(viewer, c))
                # Reasonable width
                tree.column(col, width=120, minwidth=50, stretch=False)

            viewer.current_data = list(rows)
            viewer.current_filtered = list(rows)
            # Insert first 100 rows or all if small (for preview)
            max_preview = 200
            for i, row in enumerate(rows[:max_preview]):
                values = [row.get(h, "")[:100] for h in headers]  # truncate long
                tree.insert("", "end", iid=str(i), values=values)

            # Load keywords into the editor if file exists (user managed, no list in source)
            self._load_keywords_to_text(viewer)

            viewer.status_var.set(f"Loaded {len(rows)} records from {Path(filepath).name}. Showing up to {min(len(rows), max_preview)} preview rows. Use search to filter.")

            # Compute matches using keywords from the editor if present
            kw_content = viewer.kw_text.get("1.0", tk.END).strip() if hasattr(viewer, 'kw_text') else ""
            keywords = []
            for line in kw_content.splitlines():
                for part in line.split(','):
                    kw = part.strip().lower()
                    if kw and not kw.startswith('#'):
                        keywords.append(kw)
            if keywords:
                match_count = sum(1 for row in rows if self._matches_keywords(row, keywords))
                viewer.filter_status_var.set(f"Auto-detected {match_count} matches with current keywords in editor.")
                viewer.stats_var.set(f"Total records: {len(rows)} | Matches with current keywords: {match_count} | Columns: {len(headers)}")
            else:
                viewer.filter_status_var.set("Enter keywords in the editor above, then click 'Apply Keywords'.")
                viewer.stats_var.set(f"Total records: {len(rows)} | Columns: {len(headers)}")

            messagebox.showinfo("Loaded", f"Loaded {len(rows)} records. Use the search box to filter (searches all text fields).")

        except Exception as e:
            messagebox.showerror("Load Error", f"Could not load CSV: {str(e)}")
            viewer.status_var.set(f"Error loading: {e}")

    def _apply_filter(self, viewer, search_term):
        tree = viewer.tree
        rows = viewer.data_rows
        headers = viewer.headers

        if not rows or not headers:
            return

        tree.delete(*tree.get_children())

        term = search_term.lower().strip() if search_term else ""

        filtered = []
        if not term:
            filtered = rows
        else:
            for row in rows:
                # Search across all fields
                if any(term in str(v).lower() for v in row.values()):
                    filtered.append(row)

        viewer.current_filtered = filtered
        # Limit display for performance
        max_show = 500
        for i, row in enumerate(filtered[:max_show]):
            values = [str(row.get(h, ""))[:80] for h in headers]
            tree.insert("", "end", values=values)

        viewer.status_var.set(f"Filtered to {len(filtered)} matching records (showing up to {min(len(filtered), max_show)}). Search term: '{search_term}'")

    def _export_filtered(self, viewer):
        tree = viewer.tree
        headers = viewer.headers
        if not headers:
            messagebox.showinfo("Nothing to export", "Load and filter data first.")
            return

        # Collect currently visible rows from tree (simplified: re-filter or use all if no term)
        # For simplicity, export the current filtered if we have data
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            title="Export filtered results"
        )
        if not filepath:
            return

        try:
            # Export the current filtered view if available, otherwise full data
            export_data = getattr(viewer, 'current_filtered', None) or viewer.data_rows
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(export_data)

            messagebox.showinfo("Exported", f"Exported {len(export_data)} records (current view) to {filepath}.")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    def _matches_keywords(self, row, keywords):
        """Helper to check if record matches any custom keyword in name fields."""
        name_text = ' '.join(str(v).lower() for k, v in row.items() if any(x in k.lower() for x in ['name', 'last', 'first', 'offender']))
        return any(kw in name_text for kw in keywords)

    def _apply_custom_filter(self, viewer, keywords_str):
        """Apply custom keyword filter to name fields and display matching records."""
        if not viewer.data_rows or not viewer.headers:
            return
        keywords = [k.strip().lower() for k in keywords_str.split(',') if k.strip()]
        if not keywords:
            messagebox.showinfo("No keywords", "Please enter keywords in the editor box above.")
            return
        filtered = [row for row in viewer.data_rows if self._matches_keywords(row, keywords)]
        viewer.current_filtered = filtered
        tree = viewer.tree
        tree.delete(*tree.get_children())
        max_show = 500
        for i, row in enumerate(filtered[:max_show]):
            values = [str(row.get(h, ''))[:80] for h in viewer.headers]
            tree.insert("", "end", values=values)
        viewer.filter_status_var.set(f"Filtered {len(filtered)} records matching keywords.")
        viewer.status_var.set(f"Showing filtered records for: {keywords_str}")
        viewer.stats_var.set(f"Showing filtered: {len(filtered)} / {len(viewer.data_rows)}")

    def _show_filtered_only(self, viewer):
        """Filter to only the custom keyword matches."""
        if not hasattr(viewer, 'custom_keywords_var'):
            viewer.custom_keywords_var = tk.StringVar(value="")
        keywords_str = viewer.custom_keywords_var.get()
        self._apply_custom_filter(viewer, keywords_str)

    def _export_filtered_view(self, viewer):
        """Export the currently filtered results to CSV."""
        if not hasattr(viewer, 'current_filtered') or not viewer.current_filtered:
            messagebox.showinfo("Nothing to export", "No filtered results currently displayed. Apply a filter first.")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            title="Export filtered results"
        )
        if not filepath:
            return
        try:
            headers = viewer.headers
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(viewer.current_filtered)
            messagebox.showinfo("Exported", f"Exported {len(viewer.current_filtered)} records to {filepath}.")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    def _load_keywords(self, viewer):
        """Load keywords from custom_keywords.txt next to the app or cwd."""
        try:
            kw_file = Path("custom_keywords.txt")
            if kw_file.exists():
                content = kw_file.read_text(encoding="utf-8")
                viewer.kw_text.delete("1.0", tk.END)
                viewer.kw_text.insert("1.0", content)
                messagebox.showinfo("Loaded", "Keywords loaded from custom_keywords.txt")
            else:
                messagebox.showinfo("No file", "custom_keywords.txt not found. You can create one with your keywords.")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def _load_keywords_to_text(self, viewer):
        """Load keywords into the text editor on CSV load (user-managed)."""
        try:
            kw_file = Path("custom_keywords.txt")
            if kw_file.exists():
                content = kw_file.read_text(encoding="utf-8").strip()
                viewer.kw_text.delete("1.0", tk.END)
                viewer.kw_text.insert("1.0", content)
            else:
                viewer.kw_text.delete("1.0", tk.END)
                viewer.kw_text.insert("1.0", "# Enter custom keywords here (one per line or comma-separated)\n# Click 'Apply Keywords' to filter the data.")
        except Exception:
            pass

    def _save_keywords(self, viewer):
        """Save current keywords to custom_keywords.txt."""
        try:
            content = viewer.kw_text.get("1.0", tk.END).strip()
            Path("custom_keywords.txt").write_text(content, encoding="utf-8")
            messagebox.showinfo("Saved", "Keywords saved to custom_keywords.txt")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def _apply_keywords(self, viewer):
        """Apply the current keywords from the editor to filter the data."""
        content = viewer.kw_text.get("1.0", tk.END).strip()
        keywords = []
        for line in content.splitlines():
            for part in line.split(','):
                kw = part.strip().lower()
                if kw:
                    keywords.append(kw)
        if not keywords:
            messagebox.showinfo("No keywords", "Please enter some keywords in the editor.")
            return
        if hasattr(viewer, 'custom_keywords_var'):
            viewer.custom_keywords_var.set(','.join(keywords))
        self._apply_custom_filter(viewer, ','.join(keywords))

    def _sort_tree(self, viewer, col):
        """Sort the tree by the given column."""
        if not hasattr(viewer, 'current_data') or not viewer.current_data:
            return
        data = viewer.current_data
        # Toggle reverse if same column
        reverse = False
        if viewer.sort_col == col:
            reverse = not viewer.sort_reverse
        viewer.sort_col = col
        viewer.sort_reverse = reverse
        # Sort
        data.sort(key=lambda r: str(r.get(col, '')).lower(), reverse=reverse)
        # Repopulate tree with current filter or all
        tree = viewer.tree
        tree.delete(*tree.get_children())
        search_term = viewer.search_var.get() if hasattr(viewer, 'search_var') else ""
        term = search_term.lower().strip() if search_term else ""
        to_show = data
        if term:
            to_show = [r for r in data if any(term in str(v).lower() for v in r.values())]
        viewer.current_filtered = to_show
        max_show = 500
        for i, row in enumerate(to_show[:max_show]):
            values = [str(row.get(h, ''))[:80] for h in viewer.headers]
            tree.insert("", "end", values=values)
        viewer.status_var.set(f"Sorted by {col} ({'desc' if reverse else 'asc'}). Showing {min(len(to_show), max_show)} / {len(to_show)}")

def main():
    root = tk.Tk()

    # Try to use a slightly nicer theme if available
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    app = SorArchiverGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
