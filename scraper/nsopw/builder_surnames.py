from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


from scraper.nsopw.builder_types import *  # noqa: F401,F403
from scraper.database import Database
from scraper.ethnic_names import get_ethnic_database
from scraper.reports.fetcher import ReportFetcher
from scraper.nsopw.client import (
    DEFAULT_JURISDICTIONS,
    NSOPWClient,
    NSOPWOffender,
    normalize_jurisdiction_code,
)
from scraper.nsopw.parallel import JurisdictionReportPool, ReportJob

class BuilderSurnamesMixin:
    def surnames_for_ethnicity(
        self,
        ethnicity: str = "all",
        limit_per_group: int = 15,
        all_surnames: bool = False,
        subcategory: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        """
        Return list of (surname, ethnicity_label) from the ethnic name DB.

        subcategory: when set (and not 'all'), only that nested group is used
        for asian / indian / european / african lists.
        """
        eth = (ethnicity or "all").lower().strip()
        sub = (subcategory or "all").lower().strip()
        if sub in ("", "all", "(all)", "none", "*"):
            sub = ""
        pairs: List[Tuple[str, str]] = []
        # all_surnames / limit<=0 → no per-group cap
        unlimited = all_surnames or limit_per_group is None or int(limit_per_group) <= 0
        cap = 10**9 if unlimited else max(1, int(limit_per_group))

        def take(names: Iterable[str], label: str, n: int) -> None:
            for name in sorted(names, key=lambda x: x.lower())[:n]:
                if name and name.strip():
                    pairs.append((name.strip(), label))

        def group_cap() -> int:
            return cap if unlimited else max(3, cap // 3)

        if eth in ("all", "hispanic"):
            if not sub:  # flat list — no subcategory filter
                take(self.ethnic_db.hispanic_surnames, "Hispanic", cap)
        # East / Southeast Asian only (not Indian / South Asian)
        if eth in ("all", "asian"):
            for group, names in sorted(self.ethnic_db.asian_surnames.items()):
                if sub and group.lower() != sub:
                    continue
                take(names, f"Asian ({group})", group_cap())
        # Curated high-confidence Indians (own ethnicity OR indian → high_confidence sub)
        hc_names = getattr(self.ethnic_db, "indian_high_confidence_surnames", None) or set()
        if eth in (
            "indian_high_confidence",
            "high_confidence_indian",
            "high-confidence indian",
            "indian_hc",
        ) or (
            eth in ("indian", "indian/mena", "indian_mena", "mena")
            and sub == "high_confidence"
        ):
            take(
                hc_names,
                "Indian/MENA (high_confidence)",
                cap if eth != "all" else group_cap(),
            )
        # Indian subcontinent / South Asian (separate list; optional regional groups)
        # Note: high_confidence is a curated subset — only when subcategory selects it
        # (handled above); never merge into eth=indian "all" (avoids dupes + noise).
        if eth in ("all", "indian", "indian/mena", "indian_mena", "mena") and sub != "high_confidence":
            by_group = getattr(self.ethnic_db, "indian_surnames_by_group", None) or {}
            if by_group:
                for group, names in sorted(by_group.items()):
                    if group.lower() == "high_confidence":
                        continue
                    if sub and group.lower() != sub:
                        continue
                    take(names, f"Indian/MENA ({group})", group_cap())
            elif not sub:
                take(self.ethnic_db.indian_surnames, "Indian/MENA", cap)
        if eth in ("all", "african_american") and not sub:
            take(self.ethnic_db.african_american_surnames, "African American", cap)
        # MENA/Arabic merged into Indian/MENA (also when eth=arabic for back-compat)
        if eth in ("all", "indian", "indian/mena", "indian_mena", "mena", "arabic") and not sub:
            take(self.ethnic_db.arabic_surnames, "Indian/MENA (arabic)", cap)
        if eth in ("all", "jewish") and not sub:
            take(self.ethnic_db.jewish_surnames, "Jewish", cap)
        if eth in ("all", "portuguese") and not sub:
            take(self.ethnic_db.portuguese_surnames, "Portuguese", cap)
        if eth in ("all", "native_american") and not sub:
            take(self.ethnic_db.native_american_surnames, "Native American", cap)
        if eth in ("all", "european"):
            for country, names in sorted(self.ethnic_db.european_surnames.items()):
                if sub and country.lower() != sub:
                    continue
                n = cap if unlimited else max(2, cap // 4)
                take(names, f"European ({country})", n)
        if eth in ("all", "african"):
            for region, names in sorted(self.ethnic_db.african_surnames.items()):
                if sub and region.lower() != sub:
                    continue
                take(names, f"African ({region})", group_cap())

        # When eth is a grouped family but subcategory was set under eth="all",
        # only the matching nested branch above contributes names.

        seen: Set[str] = set()
        unique: List[Tuple[str, str]] = []
        for surname, label in pairs:
            key = surname.lower()
            if key not in seen:
                seen.add(key)
                unique.append((surname, label))
        return unique


