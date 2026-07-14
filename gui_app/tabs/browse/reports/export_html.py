"""EHtml"""
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
from typing import Any, Dict, List, Optional, Set, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from gui_app.paths import ROOT
from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_MONO,
    FONT_SECTION,
    FONT_SM,
    FONT_TITLE,
    FONT_UI,
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


class ReportsExportHtmlMixin:
    def _reports_open_html(self):
        """Build HTML gallery (grid or list) for the current report pool."""
        source = self._reports_export_source()
        if not source:
            messagebox.showinfo("Open HTML", "Build a report list first.")
            return

        races = (
            f"listed={self._reports_listed_filter_value()}, "
            f"actual={self._reports_actual_filter_value()}"
        )
        only = messagebox.askyesnocancel(
            "Open HTML",
            "Include only Confirmed incorrect rows?\n\n"
            f"Filters: {races} · Show: "
            f"{(self.report_verdict_filter.get() or 'Unconfirmed').strip()}\n"
            "(full pool for that Show/race filter, not just this page)\n\n"
            "Yes = confirmed incorrect only\n"
            "No = everyone in the current Show pool\n"
            "Cancel = abort",
        )
        if only is None:
            return
        verdict_filter = {"confirmed"} if only else None

        # Layout: follow Reports Grid/List control; still confirm so HTML can differ
        prefer_grid = self._reports_is_grid()
        compact = messagebox.askyesno(
            "HTML layout",
            "Use multi-column photo grid?\n\n"
            f"(Reports view is currently {'Grid' if prefer_grid else 'List'})\n\n"
            "Yes = compact mugshot grid (recommended)\n"
            "No = full-width list cards",
        )
        # askyesno: Yes=True → grid; No=False → list

        rows = list(self._reports_iter_export_rows(verdicts=verdict_filter))
        if not rows:
            messagebox.showinfo("Open HTML", "No rows for that selection.")
            return

        out_dir = Path("data") / "reports"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            out_dir = Path(".")
        path = out_dir / (
            f"misclass_report_{'grid' if compact else 'list'}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )

        meta = getattr(self, "_misclass_meta", {}) or {}
        eth_f = meta.get("eth_filter", "all")
        min_c = meta.get("min_conf", "")
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        layout = "grid" if compact else "list"

        def _esc(s: Any) -> str:
            t = str(s if s is not None else "")
            return (
                t.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        def _file_uri(p: str) -> str:
            try:
                return Path(p).resolve().as_uri()
            except Exception:
                return ""

        cards_html: List[str] = []
        for i, (mc, verdict, rec) in enumerate(rows, 1):
            first = (rec.get("first_name") or "").strip()
            middle = (rec.get("middle_name") or "").strip()
            last = (rec.get("last_name") or "").strip()
            full = str(rec.get("full_name") or "")
            if compact:
                name = self._reports_grid_display_name(
                    first, middle, last, full, max_len=40
                )
            else:
                name = (
                    " ".join(p for p in (first, middle, last) if p)
                    or full
                    or "—"
                )
            state = _format_state_display(rec)
            photo = (rec.get("photo_path") or "").strip()
            has_photo = photo and Path(photo).is_file()
            img_html = (
                f'<img src="{_esc(_file_uri(photo))}" alt="{_esc(name)}" loading="lazy">'
                if has_photo
                else '<div class="nophoto">No photo</div>'
            )
            url = (rec.get("source_url") or "").strip()
            link = (
                f'<a class="ext" href="{_esc(url)}" target="_blank" rel="noopener">Source</a>'
                if url else ""
            )
            vclass = _esc(verdict)
            race_disp = _format_race_display(mc.expected_race) or (mc.expected_race or "—")
            race = _esc(str(race_disp).upper())
            eth = _esc(mc.likely_ethnicity)
            conf = f"{float(mc.confidence or 0):.2f}"
            crime = self._reports_crime_text(rec)
            # Reports HTML always uses summarized crime; full text only in Misclassify
            crime_short = self._reports_summarize_crime(
                crime, max_len=140 if compact else 180
            )
            crime_html = (
                f'<p class="crime" title="{_esc(crime)}">'
                f'<span class="crime-label">Crime</span> {_esc(crime_short)}</p>'
                if crime_short
                else '<p class="crime"><span class="crime-label">Crime</span> —</p>'
            )
            df = rec.get("_deepface") or {}
            face_bit = ""
            if df:
                flab = df.get("predicted_label") or df.get("top_label") or ""
                fconf = df.get("top_confidence")
                if flab:
                    try:
                        face_bit = f" · face {flab}@{float(fconf):.0%}"
                    except (TypeError, ValueError):
                        face_bit = f" · face {flab}"
            badge = _esc(self._reports_verdict_label_short(verdict))
            if compact:
                cards_html.append(
                    f"""
<article class="card v-{vclass}">
  <div class="photo">{img_html}</div>
  <div class="body">
    <h2 title="{_esc(name)}">{_esc(name)}</h2>
    <div class="listed-as" title="Registry-listed race">
      <span class="listed-label">LISTED AS</span>
      <span class="listed-race">{race}</span>
    </div>
    {crime_html}
    <p class="meta">{_esc(state)} · {conf}{ _esc(face_bit) } · #{i}</p>
    <p class="badge-line">{badge}</p>
    {link}
  </div>
</article>"""
                )
            else:
                cards_html.append(
                    f"""
<article class="card v-{vclass}">
  <div class="photo">{img_html}</div>
  <div class="body">
    <header>
      <h2>{_esc(name)}</h2>
      <span class="idx">#{i} / {len(rows)}</span>
      <span class="badge">{badge}</span>
    </header>
    <div class="listed-as" title="Registry-listed race">
      <span class="listed-label">LISTED AS</span>
      <span class="listed-race">{race}</span>
    </div>
    {crime_html}
    <p class="vs-eth">vs surname ethnicity: <strong>{eth}</strong></p>
    <p class="meta">Confidence {conf} · State {_esc(state)}{(' · Middle: ' + _esc(middle)) if middle else ''}{_esc(face_bit)}</p>
    <p class="names">Matched: {_esc('; '.join(mc.matching_names[:5]) if mc.matching_names else '—')}</p>
    {link}
  </div>
</article>"""
                )

        n_conf = sum(1 for _, v, _ in rows if v == "confirmed")
        layout_css = (
            """
  main {
    max-width: 1500px; margin: 0 auto; padding: .85rem 1rem 2.5rem;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: .75rem;
  }
  .card {
    display: flex; flex-direction: column; gap: .4rem;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: .55rem; align-items: stretch;
    min-width: 0;
  }
  .card.v-confirmed { border-color: #8a4040; box-shadow: inset 0 0 0 1px #8a404055; }
  .card.v-correct { border-color: #3a6a50; }
  .card.v-skip { border-color: #4a4a55; opacity: .9; }
  .photo { width: 100%; }
  .photo img {
    width: 100%; aspect-ratio: 4/5; height: auto; object-fit: cover;
    border-radius: 8px; background: #0c1526; display: block;
  }
  .nophoto {
    width: 100%; aspect-ratio: 4/5; border-radius: 8px; background: #0c1526;
    display: flex; align-items: center; justify-content: center;
    color: var(--dim); font-size: .75rem;
  }
  .body { min-width: 0; }
  .body h2 {
    margin: 0; font-size: .9rem; font-weight: 650; line-height: 1.25;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .listed-as {
    margin: .3rem 0 .15rem; padding: .4rem .5rem .45rem;
    background: #5c1f1f; border: 2px solid var(--danger);
    border-radius: 8px; text-align: center;
  }
  .listed-label {
    display: block; font-size: .62rem; font-weight: 700;
    letter-spacing: .08em; color: #f0b0b0; margin-bottom: .1rem;
  }
  .listed-race {
    display: block; font-size: 1.05rem; font-weight: 800;
    line-height: 1.15; color: #fff; letter-spacing: .02em;
    word-break: break-word;
  }
  .crime {
    margin: .2rem 0 0; color: var(--text); font-size: .72rem;
    line-height: 1.3; white-space: normal; overflow: visible;
    word-break: break-word;
  }
  .crime-label {
    display: block; font-size: .62rem; font-weight: 700;
    letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
    margin-bottom: .08rem;
  }
  .meta { margin: .15rem 0 0; color: var(--dim); font-size: .72rem; }
  .badge-line { margin: .1rem 0 0; color: var(--muted); font-size: .72rem; }
  .v-confirmed .badge-line { color: var(--danger); }
  .v-correct .badge-line { color: var(--success); }
  a.ext { color: var(--accent); font-size: .72rem; }
  @media (min-width: 1200px) {
    main { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
  }
  @media (max-width: 520px) {
    main { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: .5rem; }
  }
  @media print {
    header.page { position: static; }
    main { grid-template-columns: repeat(4, 1fr); gap: .4rem; }
    .card { break-inside: avoid; }
    .listed-as { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
"""
            if compact
            else """
  main {
    max-width: 920px; margin: 0 auto; padding: 1.25rem 1rem 3rem;
    display: flex; flex-direction: column; gap: .85rem;
  }
  .card {
    display: grid; grid-template-columns: 120px 1fr; gap: 1rem;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 1rem; align-items: start;
  }
  .card.v-confirmed { border-color: #8a4040; }
  .card.v-correct { border-color: #3a6a50; }
  .photo img {
    width: 112px; height: 140px; object-fit: cover; border-radius: 10px;
    background: #0c1526; display: block;
  }
  .nophoto {
    width: 112px; height: 140px; border-radius: 10px; background: #0c1526;
    display: flex; align-items: center; justify-content: center;
    color: var(--dim); font-size: .85rem;
  }
  .body header { display: flex; flex-wrap: wrap; align-items: baseline; gap: .5rem .75rem; }
  .body h2 { margin: 0; font-size: 1.2rem; font-weight: 650; }
  .idx { color: var(--dim); font-size: .85rem; }
  .badge {
    margin-left: auto; font-size: .75rem; text-transform: uppercase;
    letter-spacing: .04em; color: var(--muted); border: 1px solid var(--border);
    border-radius: 999px; padding: .15rem .55rem;
  }
  .v-confirmed .badge { color: var(--danger); border-color: #8a4040; }
  .v-correct .badge { color: var(--success); border-color: #3a6a50; }
  .listed-as {
    margin: .85rem 0 .45rem; padding: .65rem 1rem .75rem;
    background: #5c1f1f; border: 2px solid var(--danger);
    border-radius: 12px;
  }
  .listed-label {
    display: block; font-size: .78rem; font-weight: 700;
    letter-spacing: .1em; color: #f0b0b0; margin-bottom: .15rem;
  }
  .listed-race {
    display: block; font-size: 2rem; font-weight: 800;
    line-height: 1.1; color: #fff; letter-spacing: .03em;
  }
  .vs-eth {
    margin: .15rem 0 .35rem; color: var(--muted); font-size: .95rem;
  }
  .vs-eth strong { color: var(--text); font-weight: 650; }
  .crime {
    margin: .35rem 0 .45rem; padding: .5rem .75rem;
    background: #152238; border-radius: 8px; border: 1px solid var(--border);
    color: var(--text); font-size: .92rem; line-height: 1.35;
  }
  .crime-label {
    display: block; font-size: .72rem; font-weight: 700;
    letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
    margin-bottom: .2rem;
  }
  .meta, .names { margin: .2rem 0; color: var(--muted); font-size: .9rem; }
  a.ext { color: var(--accent); font-size: .88rem; }
  @media print {
    header.page { position: static; }
    .card { break-inside: avoid; }
    .listed-as { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
"""
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Misclassification report · {_esc(generated)}</title>
<style>
  :root {{
    --bg: #0a1020; --panel: #152238; --elev: #1c2c48; --border: #2a3d5c;
    --text: #e8eef8; --muted: #8fa3bf; --dim: #5e7394; --accent: #e8a87c;
    --danger: #e07a7a; --success: #7dcea0;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.45;
  }}
  header.page {{
    position: sticky; top: 0; z-index: 10;
    background: rgba(10,16,32,.92); backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 1rem 1.5rem 1.1rem;
  }}
  header.page h1 {{ margin: 0 0 .35rem; font-size: 1.35rem; font-weight: 650; }}
  header.page p {{ margin: 0; color: var(--muted); font-size: .92rem; }}
{layout_css}
</style>
</head>
<body class="layout-{layout}">
<header class="page">
  <h1>Misclassification review</h1>
  <p>
    Generated {_esc(generated)} · filter {_esc(eth_f)} · min conf {_esc(min_c)}
    · race {_esc(races)} · {len(rows)} people · {n_conf} confirmed
    · layout: {_esc(layout)}
  </p>
</header>
<main>
{"".join(cards_html)}
</main>
</body>
</html>
"""
        try:
            Path(path).write_text(html, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Open HTML", f"Could not write report:\n{e}")
            return

        self.log_queue.put(
            f"Reports HTML open ({layout}): {len(rows)} cards (race: {races}) → {path}"
        )
        if hasattr(self, "report_status"):
            try:
                self.report_status.configure(
                    text=f"Opened HTML ({layout}) · {len(rows):,} cards · {path}"
                )
            except Exception:
                pass

        opened = False
        try:
            if hasattr(self, "_open_path"):
                self._open_path(Path(path))
                opened = True
        except Exception:
            opened = False
        if not opened:
            try:
                webbrowser.open(Path(path).resolve().as_uri())
                opened = True
            except Exception:
                pass
        if not opened:
            try:
                os.startfile(str(Path(path).resolve()))  # type: ignore[attr-defined]
                opened = True
            except Exception as e:
                messagebox.showerror(
                    "Open HTML",
                    f"Wrote {path} but could not open the browser:\n{e}",
                )


