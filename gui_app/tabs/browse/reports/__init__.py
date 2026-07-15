"""Browse → Reports package (composed mixins)."""
from __future__ import annotations

from .build import ReportsBuildMixin
from .cards_add import ReportsCardsAddMixin
from .cards_layout import ReportsCardsLayoutMixin
from .export_csv import ReportsExportCsvMixin
from .export_grid import ReportsExportGridMixin
from .export_html import ReportsExportHtmlMixin
from .filter_actual import ReportsFilterActualMixin
from .filter_page import ReportsFilterPageMixin
from .filter_stats import ReportsFilterStatsMixin
from .grid_meta import ReportsGridMetaMixin
from .grid_tile import ReportsGridTileMixin
from .source_confirm import ReportsSourceConfirmMixin
from .source_load import ReportsSourceLoadMixin
from .verdict_filter import ReportsVerdictFilterMixin
from .verdict_store import ReportsVerdictStoreMixin


class ReportsTabMixin(
    ReportsBuildMixin,
    ReportsVerdictStoreMixin,
    ReportsVerdictFilterMixin,
    ReportsFilterStatsMixin,
    ReportsFilterActualMixin,
    ReportsFilterPageMixin,
    ReportsSourceLoadMixin,
    ReportsSourceConfirmMixin,
    ReportsCardsLayoutMixin,
    ReportsCardsAddMixin,
    ReportsGridTileMixin,
    ReportsGridMetaMixin,
    ReportsExportCsvMixin,
    ReportsExportGridMixin,
    ReportsExportHtmlMixin,
):
    """Photo report review, verdicts, grid/list export."""

    _ETHNICITY_OPTIONS = [
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


__all__ = ["ReportsTabMixin"]
