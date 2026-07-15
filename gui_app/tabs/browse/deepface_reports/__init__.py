"""Browse → DeepFace hit review package."""
from __future__ import annotations

from .actions_grid import DfrGridMixin
from .actions_open import DfrOpenMixin
from .build import DfrBuildMixin
from .data_filters import DfrFiltersMixin
from .data_refresh import DfrRefreshMixin
from .ethnicity import DfrEthnicityMixin
from .photo_path import DfrPhotoPathMixin
from .review_select import DfrSelectMixin
from .review_show import DfrShowMixin


class DeepfaceReportsTabMixin(
    DfrBuildMixin,
    DfrRefreshMixin,
    DfrFiltersMixin,
    DfrPhotoPathMixin,
    DfrOpenMixin,
    DfrGridMixin,
    DfrEthnicityMixin,
    DfrSelectMixin,
    DfrShowMixin,
):
    """Review stored DeepFace gross-misclass hits."""

    # Class-level ethnicity override options (shared with Reports where present)
    _DFR_ETHNICITY_OPTIONS = [
        "Asian",
        "Asian (vietnamese)",
        "Asian (chinese)",
        "Asian (korean)",
        "Asian (japanese)",
        "Asian (filipino)",
        "Indian",
        "Indian/MENA (india)",
        "Hispanic",
        "African American",
        "Arabic",
        "Jewish",
        "Portuguese",
        "European",
        "Native American",
        "Unknown",
    ]


__all__ = ["DeepfaceReportsTabMixin"]
