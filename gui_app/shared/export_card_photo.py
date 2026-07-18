"""Mugshot loading and seal preparation for export cards."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

from gui_app.shared.export_card_fields import _SEAL_PATH, load_font


def resolve_photo_path(raw: Any) -> Optional[Path]:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        return path
    alt = Path.cwd() / path
    if alt.is_file():
        return alt
    # Project-root relative (Reports often stores data\report_pages\…)
    try:
        from gui_app.paths import ROOT

        root_p = ROOT / text
        if root_p.is_file():
            return root_p
        root_p2 = ROOT / text.replace("\\", "/")
        if root_p2.is_file():
            return root_p2
    except Exception:
        pass
    return path if path.exists() else None


def is_usable_mugshot_path(raw: Any, *, min_side: int = 40, min_bytes: int = 400) -> bool:
    """True only if the path is a loadable mugshot (not missing/truncated/tiny).

    Reports used to treat any existing file as a photo — corrupt JPEGs then
    passed the Photos-only filter but rendered as empty tiles.
    """
    path = resolve_photo_path(raw)
    if path is None:
        return False
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < int(min_bytes):
            return False
    except OSError:
        return False
    try:
        with Image.open(path) as img:
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            # Force full decode — truncated files fail here
            img.load()
            w, h = img.size
            if w < int(min_side) or h < int(min_side):
                return False
            # Touch RGB conversion the way thumbs/export do
            img.convert("RGB")
        return True
    except Exception:
        return False


def load_mugshot(record: Mapping[str, Any], box: Tuple[int, int]) -> Image.Image:
    path = resolve_photo_path(record.get("photo_path"))
    img: Optional[Image.Image] = None
    if path and path.is_file():
        try:
            img = Image.open(path)
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            img = img.convert("RGB")
        except Exception:
            img = None
    if img is None:
        url = str(record.get("photo_url") or "").strip()
        if url and "mugshot-placeholder" not in url.lower():
            try:
                import requests
                from scraper.config import USER_AGENT

                resp = requests.get(
                    url,
                    timeout=25,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "image/webp,image/*,*/*;q=0.8",
                        "Referer": "https://www.nsopw.gov/",
                    },
                )
                resp.raise_for_status()
                import io

                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            except Exception:
                img = None
    if img is None:
        placeholder = Image.new("RGB", box, (34, 34, 42))
        draw = ImageDraw.Draw(placeholder)
        font = load_font(42, bold=True)
        msg = "NO PHOTO"
        bbox = draw.textbbox((0, 0), msg, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((box[0] - tw) / 2, (box[1] - th) / 2),
            msg,
            font=font,
            fill=(120, 120, 130),
        )
        return placeholder
    # Full photo inside the box — no zoom/crop (letterbox if aspect differs)
    return contain_photo(img, box)


def contain_photo(img: Image.Image, box: Tuple[int, int]) -> Image.Image:
    """Scale photo to fit entirely inside *box* (no crop/zoom); pad with card bg.

    Used for **export** name cards so the full mugshot is visible.
    """
    tw, th = int(box[0]), int(box[1])
    if tw < 1 or th < 1:
        return img
    # ImageOps.contain keeps aspect ratio and fits inside the box
    fitted = ImageOps.contain(img, (tw, th), method=Image.Resampling.LANCZOS)
    # Dark pad matches export card background (not zoomed crop)
    out = Image.new("RGB", (tw, th), (12, 12, 14))
    x = (tw - fitted.width) // 2
    y = (th - fitted.height) // 2
    if fitted.mode == "RGBA":
        out.paste(fitted, (x, y), fitted)
    else:
        out.paste(fitted.convert("RGB"), (x, y))
    return out


def cover_photo(img: Image.Image, box: Tuple[int, int]) -> Image.Image:
    """Scale/crop photo to *fill* the box (no letterbox bars). For Reports UI tiles."""
    tw, th = int(box[0]), int(box[1])
    if tw < 1 or th < 1:
        return img
    # Face-biased crop (slightly above center) matches mugshot framing
    return ImageOps.fit(
        img.convert("RGB") if img.mode != "RGB" else img,
        (tw, th),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.35),
    )


def is_backdrop(r: int, g: int, b: int) -> bool:
    if r <= 28 and g <= 28 and b <= 28:
        return True
    mx, mn = max(r, g, b), min(r, g, b)
    avg = (r + g + b) / 3.0
    if mx - mn <= 70 and 120 <= avg <= 230 and r >= b - 5 and g >= b - 10:
        return True
    return False


def is_rope_gold(r: int, g: int, b: int) -> bool:
    if b > 110:
        return False
    if r < 70 and g < 60:
        return False
    if r >= g >= b and (r - b) >= 35 and (g - b) >= 15:
        return True
    if r >= 90 and g >= 60 and b <= 100 and (r + g) > 2.2 * (b + 1):
        return True
    return False


@lru_cache(maxsize=4)
def prepared_seal(path_str: str, mtime_ns: int) -> Image.Image:
    import math

    im = Image.open(path_str).convert("RGBA")
    w, h = im.size
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    max_r = min(cx, cy)
    rope_start = max_r * 0.875
    px = im.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            dist = math.hypot(x - cx, y - cy)
            if dist > rope_start or is_backdrop(r, g, b):
                px[x, y] = (r, g, b, 0)
                continue
            if dist > max_r * 0.82 and is_rope_gold(r, g, b):
                px[x, y] = (r, g, b, 0)
    return im


def load_seal() -> Optional[Image.Image]:
    if not _SEAL_PATH.is_file():
        return None
    try:
        st = _SEAL_PATH.stat()
        return prepared_seal(
            str(_SEAL_PATH.resolve()), int(getattr(st, "st_mtime_ns", 0))
        ).copy()
    except Exception:
        try:
            return Image.open(_SEAL_PATH).convert("RGBA")
        except Exception:
            return None


def with_opacity(img: Image.Image, opacity: float) -> Image.Image:
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    factor = max(0.0, min(1.0, float(opacity)))
    a = a.point(lambda p: int(round(p * factor)))
    return Image.merge("RGBA", (r, g, b, a))


def wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap; hard-break very long tokens so crime lines stay in the box."""
    text = " ".join((text or "").split())
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = ""

    def _break_token(token: str) -> list[str]:
        if draw.textlength(token, font=font) <= max_width:
            return [token]
        parts: list[str] = []
        chunk = ""
        for ch in token:
            trial = chunk + ch
            if chunk and draw.textlength(trial, font=font) > max_width:
                parts.append(chunk)
                chunk = ch
            else:
                chunk = trial
        if chunk:
            parts.append(chunk)
        return parts or [token]

    for word in words:
        for piece in _break_token(word):
            trial = f"{current} {piece}".strip() if current else piece
            if current and draw.textlength(trial, font=font) > max_width:
                lines.append(current)
                current = piece
            else:
                current = trial
    if current:
        lines.append(current)
    return lines or [""]


def draw_seal_watermark(
    canvas: Image.Image,
    *,
    photo_box: Tuple[int, int, int, int],
    text: str = "@DoDeportations",
    seal_opacity: float = 0.03,
    text_opacity: float = 0.15,
) -> None:
    from gui_app.shared.export_card_fields import _WATERMARK, load_font

    if text == "@DoDeportations":
        text = _WATERMARK
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    left, top, right, bottom = photo_box
    photo_w = max(1, right - left)
    photo_h = max(1, bottom - top)

    seal = load_seal()
    if seal is not None:
        seal = ImageOps.fit(
            seal, (photo_w, photo_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )
        seal = with_opacity(seal, seal_opacity)
        overlay.alpha_composite(seal, dest=(left, top))
        text_top = top + photo_h - max(48, photo_h // 12)
    else:
        text_top = top + photo_h // 2

    draw = ImageDraw.Draw(overlay)
    font = load_font(max(36, photo_w // 14), bold=True)
    alpha = max(1, int(round(255 * text_opacity)))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = left + (photo_w - tw) // 2
    ty = min(bottom - th - 12, max(top + 8, text_top))
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, alpha))
    canvas.alpha_composite(overlay)
