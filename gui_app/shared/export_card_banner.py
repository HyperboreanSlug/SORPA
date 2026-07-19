"""Race banner drawing for export cards (Reported As + wide race value)."""
from __future__ import annotations

from gui_app.shared.export_card_fields import (
    _BANNER_RED,
    _BANNER_TEXT,
    _CARD_W,
)
from gui_app.shared.export_card_photo import wrap_text

# Nearly full width; tall enough for large "Reported As" + spaced WHITE
BANNER_H = 168
BANNER_INSET = 12


def draw_race_banner(
    draw,
    race: str,
    y: int,
    margin: int,
    max_w: int,
    label_font,
    race_font,
) -> int:
    """Red banner with large label and letter-spaced race (e.g. W H I T E)."""
    top = y
    inset = min(margin, BANNER_INSET)
    ban_left = inset
    ban_right = _CARD_W - inset
    ban_w = ban_right - ban_left
    draw.rounded_rectangle(
        (ban_left, top, ban_right, top + BANNER_H),
        radius=18,
        fill=_BANNER_RED,
        outline=(210, 72, 72, 255),
        width=3,
    )
    label = "Reported As"
    race_txt = (race or "").upper().strip()
    # Prefer "WHITE - DEPORTED" as race line + optional second DEPORTED emphasis
    # when the value already includes DEPORTED (from format_export_race_label).
    lb = draw.textbbox((0, 0), label, font=label_font)
    lw, lh = lb[2] - lb[0], lb[3] - lb[1]
    race_lines = wrap_text(draw, race_txt, race_font, ban_w - 36)[:2]
    race_line = race_lines[0] if race_lines else race_txt
    rb = draw.textbbox((0, 0), race_line, font=race_font)
    rh = rb[3] - rb[1]
    gap = 10
    extra_h = 0
    if len(race_lines) > 1:
        rb2 = draw.textbbox((0, 0), race_lines[1], font=race_font)
        extra_h = gap + (rb2[3] - rb2[1])
    block = lh + gap + rh + extra_h
    cy = top + max(0, (BANNER_H - block) // 2)
    draw.text(
        ((ban_left + ban_right - lw) // 2, cy - lb[1]),
        label,
        font=label_font,
        fill=(255, 236, 236, 255),
    )
    cy += lh + gap
    _draw_race_value_wide(
        draw,
        race_line,
        race_font,
        ban_left + 24,
        ban_right - 24,
        cy - rb[1],
        fill=_BANNER_TEXT,
    )
    if len(race_lines) > 1:
        cy += rh + gap
        rb2 = draw.textbbox((0, 0), race_lines[1], font=race_font)
        _draw_race_value_wide(
            draw,
            race_lines[1],
            race_font,
            ban_left + 24,
            ban_right - 24,
            cy - rb2[1],
            fill=_BANNER_TEXT,
        )
    return top + BANNER_H


def _draw_race_value_wide(
    draw,
    text: str,
    font,
    left: int,
    right: int,
    y: int,
    *,
    fill,
) -> None:
    """Draw race in ALL CAPS, stretched across the banner when short."""
    txt = (text or "").strip()
    if not txt:
        return
    avail = max(40, right - left)
    tight = draw.textbbox((0, 0), txt, font=font)
    tight_w = max(1, tight[2] - tight[0])
    if len(txt) >= 2 and tight_w < avail * 0.92:
        chars = list(txt)
        widths = []
        for ch in chars:
            bb = draw.textbbox((0, 0), ch, font=font)
            widths.append(max(1, bb[2] - bb[0]))
        base = sum(widths)
        gaps = len(chars) - 1
        extra = max(0, avail - base)
        gap = min(extra / gaps, max(14.0, avail * 0.08)) if gaps else 0.0
        total = base + gap * gaps
        x = left + max(0, (avail - total) / 2)
        for i, ch in enumerate(chars):
            for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                draw.text((int(x) + ox, y + oy), ch, font=font, fill=(60, 10, 10, 220))
            draw.text((int(x), y), ch, font=font, fill=fill)
            x += widths[i] + gap
        return
    rw = tight_w
    x = left + max(0, (avail - rw) // 2)
    for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        draw.text((x + ox, y + oy), txt, font=font, fill=(60, 10, 10, 220))
    draw.text((x, y), txt, font=font, fill=fill)
