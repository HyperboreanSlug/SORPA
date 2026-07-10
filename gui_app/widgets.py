"""Reusable GUI widgets, charts, and tree helpers."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from gui_app.theme import (
    C,
    FONT_BOLD,
    FONT_SECTION,
    FONT_SM,
)

def card(parent, **kwargs) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent,
        fg_color=C["panel"],
        border_color=C["border"],
        border_width=1,
        corner_radius=12,
        **kwargs,
    )


def section_label(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=text,
        font=FONT_SECTION,
        text_color=C["text"],
        anchor="w",
    )


def muted(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=text,
        font=FONT_SM,
        text_color=C["muted"],
        anchor="w",
        wraplength=900,
        justify="left",
    )


def tree_frame(parent) -> tuple[ctk.CTkFrame, ttk.Treeview]:
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


def vpaned(parent) -> tk.PanedWindow:
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


def hpaned(parent) -> tk.PanedWindow:
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


def stretch_columns(tree: ttk.Treeview, columns: List[str], widths: Optional[List[int]] = None) -> None:
    """Make tree columns user-resizable and stretch with the window."""
    for i, c in enumerate(columns):
        w = widths[i] if widths and i < len(widths) else 120
        tree.column(c, width=w, minwidth=40, stretch=True)


def format_state_display(record: Optional[Dict[str, Any]]) -> str:
    """Prefer a real US/territory code; ignore NSOPW junk like 'YY'."""
    if not record:
        return "—"
    try:
        from scraper.nsopw_client import normalize_jurisdiction_code

        code = normalize_jurisdiction_code(
            record.get("state"),
            record.get("source_state"),
        )
        if code:
            return code
    except Exception:
        pass
    for key in ("state", "source_state"):
        raw = (record.get(key) or "").strip().upper()
        if raw and raw not in ("YY", "XX", "ZZ", "NA", "N/A", "UN", "UK", "US"):
            return raw
    return "—"


def format_race_display(race: Optional[str]) -> str:
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


def render_bar_chart(
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


def render_pie_chart(
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


def wire_wide_scroll(tab, scroll_frame) -> None:
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


def bind_tree_scroll_isolation(tree: ttk.Treeview, wrap: ctk.CTkFrame) -> None:
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


def misclass_race_bucket(recorded_race: Optional[str]) -> str:
    """Map a recorded race label to Black / White / Other for Statistics pie."""
    key = (recorded_race or "").strip().upper()
    if key in ("WHITE", "W", "CAUCASIAN", "CAUCASION"):
        return "White"
    if key in (
        "BLACK", "B", "AFRICAN AMERICAN", "AFRICAN-AMERICAN",
        "BLACK OR AFRICAN AMERICAN",
    ):
        return "Black"
    return "Other"


def tree_cell_sort_key(val: Any):
    """
    Sort key for tree cells: numeric 0→100 (and 0%→100%) before text.

    Handles "45%", "45.2 %", "1,234", bare floats, and leading numbers in bands.
    Empty / em-dash sort last in ascending order.
    """
    s = str(val if val is not None else "").strip()
    if not s or s in ("—", "–", "-", "N/A", "n/a", "None"):
        return (2, 0.0, "")

    # Strip thousands separators and trailing percent / whitespace
    cleaned = s.replace(",", "").replace("\u00a0", " ").strip()
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()

    try:
        return (0, float(cleaned), "")
    except ValueError:
        pass

    # Leading number: "0.90 – 1.00 (high)", "12 items", etc.
    m = re.match(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", cleaned)
    if m:
        try:
            return (0, float(m.group(1)), s.casefold())
        except ValueError:
            pass

    return (1, 0.0, s.casefold())


def enable_tree_column_sort(
    tree: ttk.Treeview,
    columns: List[str],
    labels: Optional[Dict[str, str]] = None,
) -> None:
    """Click column headers to sort ascending/descending (toggle)."""
    labels = labels or {c: c.upper() for c in columns}
    state: Dict[str, Any] = {"col": None, "reverse": False}

    def apply_sort(col: str, reverse: bool, update_headings: bool = True) -> None:
        rows = [(tree.set(iid, col), iid) for iid in tree.get_children("")]
        # Ascending: 0 → 100 (and 0% → 100%); empty last via key tier 2
        rows.sort(key=lambda t: tree_cell_sort_key(t[0]), reverse=reverse)
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




# Underscore aliases (match original gui.py call sites; avoid shadowing locals named card)
_card = card
_section_label = section_label
_muted = muted
_tree_frame = tree_frame
_vpaned = vpaned
_hpaned = hpaned
_stretch_columns = stretch_columns
_format_state_display = format_state_display
_format_race_display = format_race_display
_render_bar_chart = render_bar_chart
_render_pie_chart = render_pie_chart
_wire_wide_scroll = wire_wide_scroll
_bind_tree_scroll_isolation = bind_tree_scroll_isolation
_misclass_race_bucket = misclass_race_bucket
_enable_tree_column_sort = enable_tree_column_sort
