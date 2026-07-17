"""Photo path resolve and async load for RecordSidebar."""
from __future__ import annotations

import io
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import customtkinter as ctk
import requests

from scraper.config import USER_AGENT

# Path → RGB PIL (capped size). Speeds Misclassify review when flipping rows.
_PHOTO_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_PHOTO_CACHE_MAX = 64
_PHOTO_CACHE_EDGE = 720  # keep enough pixels for sidebar refit


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
    return path if path.exists() else None


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return str(path)


def _cache_get(key: str) -> Any:
    img = _PHOTO_CACHE.get(key)
    if img is not None:
        _PHOTO_CACHE.move_to_end(key)
    return img


def _cache_put(key: str, img: Any) -> None:
    if img is None:
        return
    _PHOTO_CACHE[key] = img
    _PHOTO_CACHE.move_to_end(key)
    while len(_PHOTO_CACHE) > _PHOTO_CACHE_MAX:
        _PHOTO_CACHE.popitem(last=False)


def _resample_filter():
    from PIL import Image

    # BILINEAR is far cheaper than LANCZOS for mugshot sidebar previews.
    try:
        return Image.Resampling.BILINEAR
    except AttributeError:
        return Image.BILINEAR  # type: ignore[attr-defined]


def fit_image_to_box(img: Any, box: Tuple[int, int]) -> Any:
    """Return an RGB copy of *img* that fits entirely inside *box* (contain)."""
    max_w = max(16, int(box[0]))
    max_h = max(16, int(box[1]))
    if getattr(img, "mode", None) != "RGB":
        out = img.convert("RGB")
    else:
        out = img.copy()
    out.thumbnail((max_w, max_h), _resample_filter())
    return out


def render_fitted_ctk_image(pil_source: Any, box: Tuple[int, int]) -> Any:
    """Fit *pil_source* into *box* and return a CTkImage (or None)."""
    if pil_source is None:
        return None
    try:
        fitted = fit_image_to_box(pil_source, box)
        size = (fitted.width, fitted.height)
        return ctk.CTkImage(light_image=fitted, dark_image=fitted, size=size)
    except Exception:
        return None


def _cap_for_cache(img: Any) -> Any:
    """Downscale huge sources so the LRU stays light."""
    edge = _PHOTO_CACHE_EDGE
    w, h = int(img.width), int(img.height)
    if max(w, h) <= edge:
        return img
    out = img.copy()
    out.thumbnail((edge, edge), _resample_filter())
    return out


def decode_photo_rgb(
    *,
    path: Optional[Path] = None,
    data: Optional[bytes] = None,
    box: Optional[Tuple[int, int]] = None,
) -> Optional[Any]:
    """Decode local path or bytes to RGB, using LRU cache for files."""
    from PIL import Image

    if path is not None and path.is_file():
        key = _cache_key(path)
        hit = _cache_get(key)
        if hit is not None:
            return hit
        with Image.open(path) as raw:
            # JPEG draft decode when the display box is much smaller than the file.
            if box and raw.format == "JPEG" and hasattr(raw, "draft"):
                try:
                    tw = max(32, int(box[0]) * 2)
                    th = max(32, int(box[1]) * 2)
                    if max(raw.size) > max(tw, th):
                        raw.draft("RGB", (tw, th))
                except Exception:
                    pass
            if getattr(raw, "n_frames", 1) > 1:
                raw.seek(0)
            img = raw.convert("RGB")
        img = _cap_for_cache(img)
        _cache_put(key, img)
        return img

    if data:
        with Image.open(io.BytesIO(data)) as raw:
            if getattr(raw, "n_frames", 1) > 1:
                raw.seek(0)
            img = raw.convert("RGB")
        return _cap_for_cache(img)
    return None


def prefetch_photo_paths(
    paths: List[Any],
    *,
    box: Tuple[int, int] = (340, 340),
    limit: int = 4,
) -> None:
    """Warm the photo LRU for upcoming Misclassify rows (daemon thread)."""
    resolved: List[Path] = []
    seen = set()
    for raw in paths:
        if len(resolved) >= limit:
            break
        p = resolve_photo_path(raw)
        if p is None or not p.is_file():
            continue
        key = _cache_key(p)
        if key in seen or key in _PHOTO_CACHE:
            continue
        seen.add(key)
        resolved.append(p)
    if not resolved:
        return

    def work() -> None:
        for p in resolved:
            try:
                decode_photo_rgb(path=p, box=box)
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


def load_sidebar_photo(
    *,
    record: Dict[str, Any],
    token: int,
    photo_size: Tuple[int, int],
    load_token_fn: Callable[[], int],
    schedule_fn: Callable[[Callable[[], None]], None],
    set_photo_fn: Callable[..., None],
    store_source_fn: Optional[Callable[[Any], None]] = None,
) -> None:
    """Background-load mugshot bytes; fit to *photo_size* and apply on UI thread."""
    path = resolve_photo_path(record.get("photo_path"))
    url = str(record.get("photo_url") or "").strip()
    box = (max(16, int(photo_size[0])), max(16, int(photo_size[1])))
    set_photo_fn(None, "Loading photo…")

    def work() -> None:
        pil_source = None
        pil_fit = None
        message = "No photo"
        try:
            data: Optional[bytes] = None
            if path and path.is_file():
                pil_source = decode_photo_rgb(path=path, box=box)
            elif url:
                resp = requests.get(
                    url,
                    timeout=12,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "image/webp,image/*,*/*;q=0.8",
                        "Referer": "https://www.nsopw.gov/",
                    },
                )
                resp.raise_for_status()
                data = resp.content
                pil_source = decode_photo_rgb(data=data, box=box)
            if pil_source is not None:
                pil_fit = fit_image_to_box(pil_source, box)
            elif not url and not (path and path.is_file()):
                message = "No photo URL" if not path else "No photo"
            elif not url:
                message = "No photo"
        except Exception as exc:
            message = f"Photo unavailable ({type(exc).__name__}: {exc})"

        def apply() -> None:
            if token != load_token_fn():
                return
            if store_source_fn is not None:
                try:
                    store_source_fn(pil_source)
                except Exception:
                    pass
            if pil_fit is None:
                set_photo_fn(None, message)
                return
            try:
                size: Tuple[int, int] = (pil_fit.width, pil_fit.height)
                image = ctk.CTkImage(
                    light_image=pil_fit, dark_image=pil_fit, size=size
                )
                set_photo_fn(image)
            except Exception as exc:
                set_photo_fn(
                    None, f"Photo display failed ({type(exc).__name__})"
                )

        schedule_fn(apply)

    threading.Thread(target=work, daemon=True).start()
