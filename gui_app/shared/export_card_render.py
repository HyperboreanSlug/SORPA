"""Render and save premium shareable offender mugshot cards."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from gui_app.shared.export_card_fields import (
    _BANNER_RED,
    _BANNER_TEXT,
    _BG,
    _CARD_H,
    _CARD_W,
    _CRIME_PANEL,
    _FOIL,
    _LINE,
    _MUTED,
    _WATERMARK,
    _clean_field,
    arrest_datetime,
    crime,
    desktop_dir,
    last_known_location,
    load_font,
    person_name,
    safe_filename,
)
from gui_app.shared.export_card_photo import (
    draw_seal_watermark,
    load_mugshot,
    wrap_text,
)
from scraper.searcher import format_race_label

_PAD = 48
_NAME_SIZE = 52
_CRIME_H = 128
_BANNER_H = 120
# Race banner slightly wider than text stack (extends toward card edges)
_BANNER_INSET = 28
_FOOTER_H = 56
_NUMBER_SIZE = 36  # bottom-right export No. (larger than location text)


def render_export_card(record: Mapping[str, Any]) -> Image.Image:
    """Premium watermarked card: large photo, race banner, crime, location + release No."""
    canvas = Image.new("RGBA", (_CARD_W, _CARD_H), _BG)
    draw = ImageDraw.Draw(canvas)
    _draw_foil_sheen(canvas)

    name = person_name(record) or "—"
    race_raw = _clean_field(record.get("race"))
    race = ""
    if race_raw:
        race = _clean_field(format_race_label(race_raw) or race_raw)
        if race.casefold() == "unknown":
            race = ""
    loc = last_known_location(record)
    cr = crime(record)
    # Footer right: persistent release number (increments per new person only).
    arrest_dt = arrest_datetime(record)

    name_font = load_font(_NAME_SIZE, bold=True)
    # Crime: large bold charge lines
    crime_font = load_font(42, bold=True)
    footer_font = load_font(22)
    number_font = load_font(_NUMBER_SIZE, bold=True)
    # "Reported As" + race value — large display weight
    reported_font = load_font(28, bold=True)
    race_font = _load_display_font(58)

    max_text_w = _CARD_W - _PAD * 2
    banner_on = bool(race)
    stack_h = (
        20
        + _name_block_h(draw, name, name_font, max_text_w)
        + 16
        + (_BANNER_H if banner_on else 0)
        + (16 if banner_on else 0)
        + (_CRIME_H if cr else 0)
        + (16 if cr else 0)
        + _FOOTER_H
        + _PAD
    )
    photo_top = _PAD
    photo_h = max(420, _CARD_H - photo_top - stack_h)
    photo_box = (_CARD_W - _PAD * 2, photo_h)
    photo_rect = (_PAD, photo_top, _PAD + photo_box[0], photo_top + photo_box[1])

    draw.rounded_rectangle(photo_rect, radius=28, fill=(13, 14, 18, 255))
    mug = load_mugshot(record, photo_box).convert("RGBA")
    frame = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    frame.paste(mug, (_PAD, photo_top), mug if mug.mode == "RGBA" else None)
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(photo_rect, radius=28, fill=255)
    canvas.paste(frame, (0, 0), mask)
    draw.rounded_rectangle(photo_rect, radius=28, outline=_LINE, width=2)

    # Seal + @DoDeportations on the mug (always; after frame so nothing covers it)
    draw_seal_watermark(
        canvas,
        photo_box=photo_rect,
        text=_WATERMARK,
        seal_opacity=0.05,
        text_opacity=0.22,
    )

    y = photo_top + photo_h + 20
    y = _draw_name(draw, name, y, _PAD, max_text_w, name_font)
    if race:
        y = _draw_race_banner(
            draw, race, y + 8, _PAD, max_text_w, reported_font, race_font
        )
    if cr:
        y = _draw_crime_panel(draw, cr, y + 12, _PAD, max_text_w, crime_font)
    _draw_footer(
        draw, loc, arrest_dt, y + 14, _PAD, max_text_w, footer_font, number_font
    )
    return canvas


def _load_display_font(size: int) -> ImageFont.ImageFont:
    windir = Path(__import__("os").environ.get("WINDIR", r"C:\Windows"))
    for name in ("impact.ttf", "arialbd.ttf", "segoeuib.ttf"):
        path = windir / "Fonts" / name
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return load_font(size, bold=True)


def _name_block_h(draw, name: str, font, max_w: int) -> int:
    lines = wrap_text(draw, name or "—", font, max_w)[:2]
    return max(56, len(lines) * 58)


def _draw_foil_sheen(canvas: Image.Image) -> None:
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx, cy = _CARD_W - 80, 40
    for r, col in (
        (220, (240, 206, 132, 28)),
        (160, (142, 123, 224, 22)),
        (110, (95, 216, 224, 18)),
        (70, (217, 142, 107, 20)),
    ):
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    canvas.alpha_composite(overlay)


def _draw_name(draw, name: str, y: int, margin: int, max_w: int, font) -> int:
    for line in wrap_text(draw, name or "—", font, max_w)[:2]:
        draw.text((margin, y), line, font=font, fill=_FOIL)
        y += 58
    return y


def _draw_race_banner(
    draw, race: str, y: int, margin: int, max_w: int, label_font, race_font
) -> int:
    top = y
    # Wider than the photo/text margin so WHITE / race fills more of the card
    inset = min(margin, _BANNER_INSET)
    ban_left = inset
    ban_right = _CARD_W - inset
    ban_w = ban_right - ban_left
    draw.rounded_rectangle(
        (ban_left, top, ban_right, top + _BANNER_H),
        radius=16,
        fill=_BANNER_RED,
        outline=(178, 58, 58, 255),
        width=2,
    )
    label = "Reported As"
    race_txt = race.upper()
    lb = draw.textbbox((0, 0), label, font=label_font)
    lw, lh = lb[2] - lb[0], lb[3] - lb[1]
    race_lines = wrap_text(draw, race_txt, race_font, ban_w - 48)[:1]
    rb = draw.textbbox((0, 0), race_lines[0], font=race_font)
    rw, rh = rb[2] - rb[0], rb[3] - rb[1]
    gap = 6
    block = lh + gap + rh
    cy = top + max(0, (_BANNER_H - block) // 2)
    draw.text(
        ((ban_left + ban_right - lw) // 2, cy - lb[1]),
        label,
        font=label_font,
        fill=(245, 217, 217, 255),
    )
    cy += lh + gap
    draw.text(
        ((ban_left + ban_right - rw) // 2, cy - rb[1]),
        race_lines[0],
        font=race_font,
        fill=_BANNER_TEXT,
    )
    return top + _BANNER_H


def _draw_crime_panel(draw, text: str, y: int, margin: int, max_w: int, font) -> int:
    box = (margin, y, _CARD_W - margin, y + _CRIME_H)
    draw.rounded_rectangle(box, radius=18, fill=_CRIME_PANEL, outline=_LINE, width=2)
    lines = wrap_text(draw, text or "", font, max_w - 36)[:3]
    line_h = 36
    ty = y + 20
    for line in lines:
        if line:
            # Brighter + bold font for readability
            draw.text((margin + 18, ty), line, font=font, fill=_BANNER_TEXT)
            ty += line_h
    return y + _CRIME_H


def _draw_footer(
    draw,
    loc: str,
    release_label: str,
    y: int,
    margin: int,
    max_w: int,
    font,
    number_font=None,
) -> None:
    try:
        draw.line((margin, y, _CARD_W - margin, y), fill=_LINE, width=2)
        ty = y + 12
        left = (loc or "")[:40]
        right = (release_label or "")[:28]
        handle = _WATERMARK
        try:
            num_font = number_font or load_font(_NUMBER_SIZE, bold=True)
        except Exception:
            num_font = font
        if left:
            draw.text((margin, ty + 6), left.upper(), font=font, fill=_MUTED)
        if right:
            try:
                rb = draw.textbbox((0, 0), right, font=num_font)
                rw = int(rb[2] - rb[0])
            except Exception:
                rw = max(8, len(right) * 14)
            # Brighter + larger so export No. reads clearly on the card
            draw.text(
                (_CARD_W - margin - rw, ty),
                right,
                font=num_font,
                fill=(235, 235, 240, 255),
            )
        # Brand mark centered in footer (same handle as photo watermark)
        try:
            handle_font = load_font(20, bold=True)
            hb = draw.textbbox((0, 0), handle, font=handle_font)
            hw = int(hb[2] - hb[0])
        except Exception:
            handle_font = font
            hw = max(8, len(handle) * 10)
        draw.text(
            ((_CARD_W - hw) // 2, ty + 6),
            handle,
            font=handle_font,
            fill=(200, 200, 210, 255),
        )
    except Exception:
        # Never let footer paint kill a card export / UI thread
        pass


def export_record_card_to_desktop(record: Mapping[str, Any]) -> Path:
    """Render and save a PNG card to the user's Desktop; return the path."""
    try:
        img = render_export_card(record)
    except Exception as exc:
        try:
            from gui_app.crash_log import log_exception

            log_exception("export_record_card_to_desktop", exc)
        except Exception:
            pass
        raise
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
