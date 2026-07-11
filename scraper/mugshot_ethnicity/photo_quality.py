"""Detect non-mugshot assets: registry silhouettes, 1×1 spacers, seals, banners.

Colorado (and some other SORs) often serve a white-background black-line
silhouette JPEG when no photo is on file. State HTML pages also embed seals,
skip-navigation spacers, and site chrome that must never become photo_path
or waste disk in ``*_assets/``.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, Set, Tuple, Union

# Byte-identical CO "no photo" silhouette (299×289 L JPEG, ~7.2 KB).
KNOWN_PLACEHOLDER_MD5: Set[str] = {
    "5030072b8b5ad8f44f389eb77b3d3d70",
}

# Known site-chrome / stub hashes from archive audits (not real faces).
KNOWN_CHROME_MD5: Set[str] = {
    # NV "Seal" img that is actually a 1×1 transparent GIF (~3 KB)
    "5241a2d8ac75f34e0373765f6249194f",
    # Tiny multi-state stub 59×78 (~1.6 KB)
    "0a668c6c65c40b293dff33a81c6849ae",
    # KS 16×16 icon
    "3085230ce03a9a93a074669e4c194432",
    # DE 2-color 59×60 stub
    "cfe6816b60b267b6734a16f5614d8a41",
    # MN ultra-wide banner strip (513×61)
    "8404669e8feb8303f78d34008ab4eab5",
    # CO banner strip (278×61)
    "d8c95963fee283a4ad2a87bb1b5620f7",
}

# Silhouette heuristic thresholds (white bg + dark outline).
_WHITE_FRAC_MIN = 0.70
_BLACK_FRAC_MIN = 0.05
_MID_FRAC_MAX = 0.10
_STUB_SIZE_MIN = 2_000
_STUB_SIZE_MAX = 25_000

# URL / path tokens that almost never refer to offender mugshots.
_CHROME_URL_RE = re.compile(
    r"(?:logo|icon|sprite|pixel|tracking|1x1|spacer|banner|button|"
    r"header|footer|nav|seal|badge|favicon|clear\.gif|blank\.gif|"
    r"/offices/|app_themes|webresource\.axd)",
    re.I,
)


def file_md5(path: Union[str, Path], *, chunk: int = 1 << 16) -> Optional[str]:
    """MD5 hex digest of a file, or None if unreadable."""
    try:
        p = Path(path)
        h = hashlib.md5()
        with p.open("rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def url_looks_like_chrome(url: str) -> bool:
    """True when a remote image URL is almost certainly site chrome."""
    u = (url or "").strip()
    if not u:
        return True
    low = u.lower()
    if low.startswith("data:"):
        return True
    if _CHROME_URL_RE.search(low):
        # Dedicated mugshot endpoints still win even if path is noisy
        if any(
            k in low
            for k in (
                "displayimage",
                "callimage",
                "/pictures/",
                "sorimage",
                "imgid=",
                "imageid=",
                "offender",
                "mug",
            )
        ):
            # DisplayImage is fine; seal/spacer paths are not
            if any(k in low for k in ("seal", "spacer", "logo", "1x1", "pixel", "favicon")):
                return True
            return False
        return True
    return False


def _heuristic_silhouette(path: Path) -> bool:
    """True if image looks like a white-bg black-outline silhouette stub."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < _STUB_SIZE_MIN or size > _STUB_SIZE_MAX:
        return False
    try:
        from PIL import Image
    except Exception:
        return False
    try:
        with Image.open(path) as im:
            gray = im.convert("L")
            gray.thumbnail((160, 160))
            try:
                import numpy as np

                arr = np.asarray(gray, dtype=np.uint8)
                n = float(arr.size) or 1.0
                white = float((arr > 240).sum()) / n
                black = float((arr < 40).sum()) / n
                mid = 1.0 - white - black
            except Exception:
                hist = gray.histogram()
                total = float(sum(hist)) or 1.0
                white = sum(hist[241:256]) / total
                black = sum(hist[0:40]) / total
                mid = 1.0 - white - black
        return (
            white >= _WHITE_FRAC_MIN
            and black >= _BLACK_FRAC_MIN
            and mid <= _MID_FRAC_MAX
        )
    except Exception:
        return False


def _image_dims(path: Path) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            return int(w), int(h)
    except Exception:
        return None


def _dims_from_bytes(data: bytes) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image
        import io

        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            return int(w), int(h)
    except Exception:
        return None


def _geometry_reason(w: int, h: int, size: int, *, ext: str = "") -> Optional[str]:
    """Classify non-mugshot geometry. Returns reason or None if OK."""
    if w < 1 or h < 1:
        return "invalid image dimensions"
    # Tracking / spacer / seal stubs
    if min(w, h) <= 2:
        return "1×1 or spacer pixel"
    # Tiny icons (not face photos)
    if min(w, h) < 40 and max(w, h) < 120:
        return "tiny icon (not a mugshot)"
    # Ultra-wide banner strips (office headers, nav chrome)
    ratio = max(w, h) / float(min(w, h))
    if ratio >= 2.4 and min(w, h) < 120:
        return "banner / strip chrome"
    # Small pure-GIF stubs
    if ext == ".gif" and size < 4_000 and min(w, h) < 80:
        return "small GIF stub"
    return None


@lru_cache(maxsize=16384)
def _classify_cached(resolved: str, mtime_ns: int, size: int) -> Optional[str]:
    """Return reason if non-mugshot / placeholder, else None."""
    path = Path(resolved)
    digest = file_md5(path)
    if digest and digest in KNOWN_PLACEHOLDER_MD5:
        return "registry silhouette placeholder (known stub)"
    if digest and digest in KNOWN_CHROME_MD5:
        return "site chrome (known non-mugshot)"
    if _heuristic_silhouette(path):
        return "registry silhouette placeholder (white bg + outline)"
    dims = _image_dims(path)
    if dims is None:
        return None
    w, h = dims
    return _geometry_reason(w, h, size, ext=path.suffix.lower())


def non_mugshot_reason(path: Union[str, Path, None]) -> Optional[str]:
    """
    If *path* is not a usable mugshot (placeholder, 1×1, icon, banner…),
    return a short reason. Otherwise return None.
    """
    if path is None:
        return None
    raw = str(path).strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_file():
        return None
    try:
        st = p.stat()
        resolved = str(p.resolve())
        return _classify_cached(
            resolved, int(getattr(st, "st_mtime_ns", 0)), int(st.st_size)
        )
    except OSError:
        return None


def is_non_mugshot(path: Union[str, Path, None]) -> bool:
    """True when the file is chrome / placeholder / not a real mugshot."""
    return non_mugshot_reason(path) is not None


# Back-compat aliases used by scanner / UI
def placeholder_reason(path: Union[str, Path, None]) -> Optional[str]:
    """Reason if silhouette placeholder *or* other non-mugshot stub."""
    return non_mugshot_reason(path)


def is_placeholder_photo(path: Union[str, Path, None]) -> bool:
    """True when the file is a SOR placeholder or other non-mugshot stub."""
    return is_non_mugshot(path)


def bytes_non_mugshot_reason(data: bytes, *, url: str = "", ext: str = "") -> Optional[str]:
    """
    Classify raw image bytes before writing to disk.
    Used by the report fetcher so chrome is never saved.
    """
    if not data:
        return "empty image body"
    if len(data) < 40:
        return "image too small"
    digest = md5_bytes(data)
    if digest in KNOWN_PLACEHOLDER_MD5:
        return "registry silhouette placeholder (known stub)"
    if digest in KNOWN_CHROME_MD5:
        return "site chrome (known non-mugshot)"
    if url_looks_like_chrome(url):
        # Still allow dedicated mugshot URLs (handled inside url_looks_like_chrome)
        return "chrome URL pattern"
    # Sniff ext from magic if needed
    e = (ext or "").lower()
    if not e:
        if data[:3] == b"\xff\xd8\xff":
            e = ".jpg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            e = ".png"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            e = ".gif"
        elif data[:4] == b"RIFF" and len(data) > 12 and data[8:12] == b"WEBP":
            e = ".webp"
    dims = _dims_from_bytes(data)
    if dims is None:
        # Can't decode — let caller keep if other checks pass
        return None
    w, h = dims
    geo = _geometry_reason(w, h, len(data), ext=e)
    if geo:
        return geo
    # Silhouette on in-memory JPEG stubs (write temp-less via dims + histogram)
    if _STUB_SIZE_MIN <= len(data) <= _STUB_SIZE_MAX:
        try:
            from PIL import Image
            import io

            with Image.open(io.BytesIO(data)) as im:
                gray = im.convert("L")
                gray.thumbnail((160, 160))
                hist = gray.histogram()
                total = float(sum(hist)) or 1.0
                white = sum(hist[241:256]) / total
                black = sum(hist[0:40]) / total
                mid = 1.0 - white - black
                if (
                    white >= _WHITE_FRAC_MIN
                    and black >= _BLACK_FRAC_MIN
                    and mid <= _MID_FRAC_MAX
                ):
                    return "registry silhouette placeholder (white bg + outline)"
        except Exception:
            pass
    return None


def clear_placeholder_cache() -> None:
    """Drop the path classification cache (tests / after photo re-download)."""
    _classify_cached.cache_clear()
