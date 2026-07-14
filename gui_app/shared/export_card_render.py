"""Render and save shareable offender mugshot cards."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Tuple

from PIL import Image, ImageDraw, ImageFont

from gui_app.shared.export_card_fields import (
    _ACCENT,
    _BANNER_RED,
    _BANNER_TEXT,
    _BG,
    _CARD_H,
    _CARD_W,
    _MUTED,
    _PHOTO_H,
    _TEXT,
    _WATERMARK,
    _clean_field,
    crime,
    desktop_dir,
    last_known_location,
    load_font,
    person_name,
    safe_filename,
)
from gui_app.shared.export_card_fit import draw_crime_block, draw_labeled_block
from gui_app.shared.export_card_photo import (
    draw_seal_watermark,
    load_mugshot,
    wrap_text,
)
from scraper.searcher import format_race_label


def render_export_card(record: Mapping[str, Any]) -> Image.Image:
    """Watermarked card: full photo (no zoom), city+state only, crime at bottom."""
    canvas = Image.new("RGBA", (_CARD_W, _CARD_H), _BG)
    draw = ImageDraw.Draw(canvas)

    margin = 48
    footer_reserve = 56
    photo_box = (_CARD_W - margin * 2, _PHOTO_H)
    photo_rect = (margin, margin, margin + photo_box[0], margin + photo_box[1])
    # Photo box: dark frame, full image letterboxed inside (never cropped/zoomed)
    draw.rectangle(photo_rect, fill=(12, 12, 14, 255))
    mug = load_mugshot(record, photo_box).convert("RGBA")
    canvas.paste(mug, (margin, margin), mug if mug.mode == "RGBA" else None)

    draw_seal_watermark(
        canvas,
        photo_box=photo_rect,
        text=_WATERMARK,
        seal_opacity=0.03,
        text_opacity=0.15,
    )

    bar_y = margin + _PHOTO_H + 18
    draw.rounded_rectangle(
        (margin, bar_y, _CARD_W - margin, bar_y + 8), radius=4, fill=_ACCENT
    )

    name = person_name(record) or "—"
    race_raw = _clean_field(record.get("race"))
    race = ""
    if race_raw:
        race = _clean_field(format_race_label(race_raw) or race_raw)
        if race.casefold() == "unknown":
            race = ""
    loc = last_known_location(record)
    cr = crime(record)

    name_font = load_font(54, bold=True)
    label_font = load_font(26)
    value_font = load_font(34, bold=True)
    banner_font = load_font(48, bold=True)

    y = bar_y + 28
    max_text_w = _CARD_W - margin * 2
    crime_bottom = _CARD_H - margin - footer_reserve

    for line in wrap_text(draw, name, name_font, max_text_w)[:2]:
        draw.text((margin, y), line, font=name_font, fill=_TEXT)
        y += 62

    if race:
        y = _draw_race_banner(draw, race, y, margin, max_text_w, banner_font)

    # City + state only (never address/county)
    if loc:
        y = draw_labeled_block(
            draw,
            "Location",
            loc,
            y,
            margin,
            max_text_w,
            label_font,
            value_font,
            max_lines=2,
        )

    # Crime always at the bottom of the card (above watermark handle)
    if cr:
        draw_crime_block(
            draw,
            cr,
            y + 12,
            margin=margin,
            max_text_w=max_text_w,
            bottom_limit=crime_bottom,
            label_font=label_font,
            anchor_bottom=True,
        )

    handle = _WATERMARK or "@DoDeportations"
    handle_font = load_font(28, bold=True)
    hb = draw.textbbox((0, 0), handle, font=handle_font)
    hw, hh = hb[2] - hb[0], hb[3] - hb[1]
    draw.text(
        (_CARD_W - margin - hw, _CARD_H - margin - hh),
        handle,
        font=handle_font,
        fill=(255, 255, 255, 255),
    )
    return canvas


def _draw_race_banner(draw, race, y, margin, max_text_w, banner_font) -> int:
    banner_h = 108
    banner_pad_x = 28
    banner_top = y + 12
    draw.rounded_rectangle(
        (margin, banner_top, _CARD_W - margin, banner_top + banner_h),
        radius=14,
        fill=_BANNER_RED,
    )
    banner_label_font = load_font(24, bold=True)
    label = "RACE MARKED"
    race_lines = wrap_text(
        draw, race.upper(), banner_font, max_text_w - banner_pad_x * 2
    )[:2]
    gap = 6

    def line_metrics(text: str, font: ImageFont.ImageFont) -> Tuple[int, int, int]:
        b = draw.textbbox((0, 0), text, font=font)
        return b[2] - b[0], b[3] - b[1], b[1]

    label_w, label_h, label_top = line_metrics(label, banner_label_font)
    race_metrics = [line_metrics(line, banner_font) for line in race_lines]
    race_block_h = sum(m[1] for m in race_metrics) + max(0, len(race_metrics) - 1) * 4
    block_h = label_h + gap + race_block_h
    cursor_y = banner_top + max(0, (banner_h - block_h) // 2)

    draw.text(
        ((_CARD_W - label_w) // 2, cursor_y - label_top),
        label,
        font=banner_label_font,
        fill=(255, 220, 220, 255),
    )
    cursor_y += label_h + gap
    for line, (lw, lh, ltop) in zip(race_lines, race_metrics):
        draw.text(
            ((_CARD_W - lw) // 2, cursor_y - ltop),
            line,
            font=banner_font,
            fill=_BANNER_TEXT,
        )
        cursor_y += lh + 4
    return banner_top + banner_h + 22


def export_record_card_to_desktop(record: Mapping[str, Any]) -> Path:
    """Render and save a PNG card to the user's Desktop; return the path."""
    img = render_export_card(record)
    desktop = desktop_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = safe_filename(person_name(record) or "offender")
    out = desktop / f"{name}_{stamp}.png"
    n = 1
    while out.exists():
        out = desktop / f"{name}_{stamp}_{n}.png"
        n += 1
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out
