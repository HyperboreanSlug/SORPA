from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from scraper.ethnic_names_base import *  # noqa: F401,F403

class EthnicConfidenceMixin:
    def _calculate_confidence(
        self,
        surname: str,
        matches: List[Tuple[str, str]],
        *,
        best_match: str,
        first_name_signal: str,
        is_ambiguous: bool,
        is_high_confidence_surname: bool,
    ) -> float:
        """Confidence from surname specificity + first-name corroboration."""
        if not matches:
            return 0.0

        sources = {src for _eth, src in matches}
        multi_family = self._family_count(matches) > 1

        # Base from surname quality
        if best_match.startswith("Indian"):
            if is_high_confidence_surname and not is_ambiguous:
                base = 0.92 if not multi_family else 0.85
            elif is_ambiguous:
                # Surname alone is weak evidence
                base = 0.38
            else:
                base = 0.72 if not multi_family else 0.58
        elif best_match == "Hispanic":
            base = 0.85 if not multi_family else 0.7
        elif best_match.startswith("Asian"):
            base = 0.85 if not multi_family else 0.7
        elif best_match == "African American":
            base = 0.85 if not multi_family else 0.7
        else:
            base = 0.8 if len(matches) == 1 else 0.65

        # First-name adjustment
        if best_match == "African American":
            if first_name_signal == "african_american":
                base = min(1.0, base + 0.12)  # DeShawn Washington
            elif first_name_signal in ("anglo", "slavic"):
                base = min(base, 0.45)  # John Washington
            elif first_name_signal == "hispanic":
                base = min(base, 0.5)
        elif first_name_signal == "indian":
            if best_match.startswith("Indian"):
                base = min(1.0, base + (0.4 if is_ambiguous else 0.12))
            elif best_match in ("Hispanic", "Portuguese"):
                base = max(0.35, base - 0.15)
        elif first_name_signal == "hispanic":
            if best_match.startswith("Indian"):
                base = min(base, 0.25 if is_ambiguous else 0.4)
            elif best_match in ("Hispanic", "Portuguese"):
                base = min(1.0, base + 0.12)
        elif first_name_signal in ("anglo", "slavic"):
            # Andrey ≈ white Western; Andrei ≈ Slavic — neither supports Indian
            if best_match.startswith("Indian"):
                if is_ambiguous:
                    base = min(base, 0.28)
                elif is_high_confidence_surname:
                    base = min(base, 0.55)
                else:
                    base = min(base, 0.45)
            elif best_match.startswith("European"):
                base = min(1.0, base + (0.15 if first_name_signal == "slavic" else 0.1))

        if multi_family and not best_match.startswith("Indian"):
            base -= 0.08 * (self._family_count(matches) - 1)

        if "indian_high_confidence" in sources and best_match.startswith("Indian"):
            if first_name_signal == "indian":
                base = max(base, 0.9)

        return round(max(0.15, min(base, 1.0)), 2)


    @staticmethod
    def _family_count(matches: List[Tuple[str, str]]) -> int:
        families = set()
        for ethnicity, _source in matches:
            # Indian + MENA/Arabic share one family (merged Indian/MENA bucket)
            if (
                ethnicity.startswith("Indian")
                or ethnicity == "Arabic"
                or ethnicity.lower().startswith("arabic")
            ):
                families.add("indian")
            elif ethnicity.startswith("Asian"):
                families.add("asian")
            elif ethnicity.startswith("European"):
                families.add("european")
            elif ethnicity.startswith("African ("):
                families.add("african")
            else:
                families.add(ethnicity.lower())
        return len(families)


