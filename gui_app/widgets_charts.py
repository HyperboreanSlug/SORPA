"""Pillow bar/pie chart renderers for GUI statistics."""
from __future__ import annotations

from typing import Any, List, Optional

import customtkinter as ctk

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
    bg: str = "#101a2e",
    fg: str = "#ececf1",
    muted: str = "#9b9ba8",
    bar_color: Optional[str] = None,
) -> Any:
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
            [chart_x0, y + 2, x1, y + 2 + bar_h], radius=4, fill=bar_color,
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
    bg: str = "#101a2e",
    fg: str = "#ececf1",
    muted: str = "#9b9ba8",
    accent: str = "#e8a87c",
    legend_below: bool = True,
) -> Any:
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
