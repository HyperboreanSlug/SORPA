"""NSOPW harvest tab package (composed mixins)."""
from __future__ import annotations

from .build import NsopwBuildMixin
from .enrich_build import NsopwEnrichBuildMixin
from .enrich_integrity import NsopwEnrichIntegrityMixin
from .enrich_list import NsopwEnrichListMixin
from .enrich_run import NsopwEnrichRunMixin
from .options_ethnicity import NsopwEthnicityMixin
from .options_runtime import NsopwRuntimeMixin
from .options_state import NsopwStateMixin
from .progress_eta import NsopwEtaMixin
from .progress_ui import NsopwProgressUiMixin
from .run_cancel import NsopwCancelMixin
from .run_start import NsopwStartMixin
from .tree_open import NsopwTreeOpenMixin
from .tree_rows import NsopwTreeRowsMixin


class NsopwTabMixin(
    NsopwBuildMixin,
    NsopwEnrichBuildMixin,
    NsopwEnrichIntegrityMixin,
    NsopwEnrichListMixin,
    NsopwEnrichRunMixin,
    NsopwEthnicityMixin,
    NsopwStateMixin,
    NsopwRuntimeMixin,
    NsopwEtaMixin,
    NsopwProgressUiMixin,
    NsopwTreeRowsMixin,
    NsopwTreeOpenMixin,
    NsopwCancelMixin,
    NsopwStartMixin,
):
    """NSOPW Search harvest + Enrich (state-scoped report backfill + integrity)."""


__all__ = ["NsopwTabMixin"]
