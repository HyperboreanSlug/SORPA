"""Browse → Statistics sub-tab."""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SECTION,
    FONT_SM,
    FONT_TITLE,
    FONT_UI,
    _style_treeview,
)
from gui_app.widgets import (
    _bind_tree_scroll_isolation,
    _card,
    _enable_tree_column_sort,
    _format_race_display,
    _format_state_display,
    _hpaned,
    _misclass_race_bucket,
    _muted,
    _render_bar_chart,
    _render_pie_chart,
    _section_label,
    _stretch_columns,
    _tree_frame,
    _vpaned,
    _wire_wide_scroll,
)
from gui_app.paths import ROOT



class StatisticsTabMixin:
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
                    " ".join(
                        p for p in (
                            rec.get("first_name") or "",
                            rec.get("middle_name") or "",
                            rec.get("last_name") or "",
                        ) if str(p).strip()
                    )
                    or (rec.get("full_name") or "—")
                )
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
        # "Misclassified as (race)" pie: Black / White / Other (residual bucket).
        by_race: Counter = Counter(
            _misclass_race_bucket(mc.expected_race) for mc in results
        )
        race_n = sum(by_race.values())

        def _fill(tree, counter: Counter, total: Optional[int] = None):
            if tree is None:
                return
            denom = n if total is None else total
            tree.delete(*tree.get_children())
            for label, cnt in counter.most_common():
                pct = (cnt / denom * 100.0) if denom else 0.0
                tree.insert("", "end", values=(str(label), str(cnt), f"{pct:.1f}%"))

        _fill(getattr(self, "mcstat_eth_tree", None), by_eth)
        _fill(getattr(self, "mcstat_race_tree", None), by_race, total=race_n)

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

