"""Text fitting helpers for export cards (crime block stays inside the frame)."""
from __future__ import annotations

from typing import List

from gui_app.shared.export_card_fields import _MUTED, _TEXT, load_font
from gui_app.shared.export_card_photo import wrap_text


def draw_labeled_block(
    draw,
    label: str,
    value: str,
    top: int,
    margin: int,
    max_text_w: int,
    label_font,
    value_font,
    *,
    max_lines: int = 3,
) -> int:
    draw.text((margin, top), label.upper(), font=label_font, fill=_MUTED)
    top += 34
    all_lines = wrap_text(draw, value, value_font, max_text_w)
    lines = all_lines[:max_lines]
    if len(all_lines) > max_lines and lines:
        lines[-1] = ellipsize(draw, " ".join(all_lines[max_lines - 1 :]), value_font, max_text_w)
    for line in lines:
        draw.text((margin, top), line, font=value_font, fill=_TEXT)
        top += 42
    return top + 10


def plan_crime_block(
    draw,
    cr: str,
    *,
    max_text_w: int,
    max_height: int,
) -> tuple:
    """Return (font, line_h, lines, total_h) that fits in *max_height*."""
    label_h = 34
    body_avail = max_height - label_h - 8
    if body_avail < 24:
        return None
    for size in (34, 30, 26, 22, 18):
        font = load_font(size, bold=True)
        line_h = max(28, size + 8)
        max_lines = max(1, body_avail // line_h)
        lines = fit_lines(draw, cr, font, max_text_w, max_lines)
        body_h = len(lines) * line_h
        if body_h <= body_avail:
            return font, line_h, lines, label_h + body_h + 6
    font = load_font(18, bold=True)
    line_h = 26
    lines = [ellipsize(draw, cr, font, max_text_w)]
    return font, line_h, lines, label_h + line_h + 6


def draw_crime_block(
    draw,
    cr: str,
    top: int,
    *,
    margin: int,
    max_text_w: int,
    bottom_limit: int,
    label_font,
    anchor_bottom: bool = False,
) -> int:
    """Draw CRIME. If *anchor_bottom*, pin the block to just above *bottom_limit*."""
    label_h = 34
    max_height = bottom_limit - top
    if max_height < 40:
        return top
    plan = plan_crime_block(
        draw, cr, max_text_w=max_text_w, max_height=max_height
    )
    if plan is None:
        return top
    font, line_h, lines, total_h = plan
    if anchor_bottom:
        top = max(top, bottom_limit - total_h)
    draw.text((margin, top), "CRIME", font=label_font, fill=_MUTED)
    y = top + label_h
    for line in lines:
        draw.text((margin, y), line, font=font, fill=_TEXT)
        y += line_h
    return y + 6


def fit_lines(draw, text: str, font, max_width: int, max_lines: int) -> List[str]:
    lines = wrap_text(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    kept[-1] = ellipsize(draw, " ".join(lines[max_lines - 1 :]), font, max_width)
    return kept


def ellipsize(draw, text: str, font, max_width: int) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if draw.textlength(cand, font=font) <= max_width:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best
