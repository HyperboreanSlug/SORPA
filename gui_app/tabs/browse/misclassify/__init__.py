"""Browse → Misclassify package."""
from __future__ import annotations

from .build import MisclassifyBuildMixin
from .run import MisclassifyRunMixin
from .run_apply import MisclassifyApplyMixin


class MisclassifyTabMixin(
    MisclassifyBuildMixin,
    MisclassifyRunMixin,
    MisclassifyApplyMixin,
):
    """Surname vs race mismatch analysis UI."""


__all__ = ["MisclassifyTabMixin"]
