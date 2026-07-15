from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from scraper.ethnic_names_base import *  # noqa: F401,F403

class EthnicClassifyNameMixin:
    def classify_by_name(
        self,
        surname: str,
        first_name: Optional[str] = None,
        middle_name: Optional[str] = None,
    ) -> Tuple[str, float, List[str]]:
        """
        Classify a person by surname + optional first/middle names.

        Returns (ethnicity, confidence, matching_labels).
        Confidence is intentionally conservative for multi-ethnic surnames.
        Middle names are used like first names for corroboration / dampening.
        """
        if not surname:
            return ("Unknown", 0.0, [])

        self._build_lookup_sets()
        surname_lc = surname.strip().lower()
        if not surname_lc:
            return ("Unknown", 0.0, [])

        matches: List[Tuple[str, str]] = []
        indian_blocked = surname_lc in self._indian_excl_lc

        if surname_lc in self._hispanic_lc:
            matches.append(("Hispanic", "hispanic_surnames"))

        if not indian_blocked:
            if surname_lc in self._indian_hc_lc:
                matches.append(
                    ("Indian/MENA (high_confidence)", "indian_high_confidence")
                )
            if self.indian_surnames_by_group:
                for group, names in self._indian_group_lc.items():
                    if group == "high_confidence":
                        continue
                    if surname_lc in names:
                        matches.append(
                            (f"Indian/MENA ({group})", f"indian_{group}")
                        )
            if surname_lc in self._indian_lc and not any(
                m[0].startswith("Indian") for m in matches
            ):
                matches.append(("Indian/MENA", "indian_surnames"))

        for group, names in self._asian_lc.items():
            if surname_lc in names:
                matches.append((f"Asian ({group})", f"asian_{group}"))

        if surname_lc in self._african_american_lc:
            matches.append(("African American", "african_american_surnames"))

        if surname_lc in self._native_american_lc:
            matches.append(("Native American", "native_american_surnames"))

        for country, names in self._european_lc.items():
            if surname_lc in names:
                matches.append((f"European ({country})", f"european_{country}"))

        if surname_lc in self._jewish_lc:
            matches.append(("Jewish", "jewish_surnames"))

        if surname_lc in self._portuguese_lc:
            matches.append(("Portuguese", "portuguese_surnames"))

        # MENA / Arabic surnames share the Indian/MENA family (merged bucket)
        if surname_lc in self._arabic_lc:
            matches.append(("Indian/MENA (arabic)", "arabic_surnames"))

        for region, names in self._african_lc.items():
            if surname_lc in names:
                matches.append((f"African ({region})", f"african_{region}"))

        if not matches:
            return ("Unknown", 0.0, [])

        fn_signal = self._resolve_given_name_signal(first_name, middle_name)
        is_amb = surname_lc in self._indian_amb_lc
        is_hc = surname_lc in self._indian_hc_lc
        # Very short surnames (Dey, Rai, …) are easy false positives with Western
        # given names even when they also appear on Indian lists.
        is_short_surname = len(surname_lc) <= 3
        # Distinctive short forms that are almost only South Asian
        _strong_short = frozenset({
            "jha", "rao", "rai", "kaur", "nair", "jain", "bose", "modi",
            "iyer", "kaul", "goel", "saha", "das", "dev", "lal", "pal",
        })
        is_weak_with_western = is_amb or (
            is_short_surname and surname_lc not in _strong_short
        )
        has_indian = any(m[0].startswith("Indian") for m in matches)
        has_hispanic = any(m[0] == "Hispanic" for m in matches)
        has_portuguese = any(m[0] == "Portuguese" for m in matches)
        has_european = any(m[0].startswith("European") for m in matches)

        # ---- Choose best ethnicity (first-name aware) ----
        def sort_key(item: Tuple[str, str]) -> float:
            ethnicity, _source = item
            score = 0.0
            if ethnicity.startswith("Indian"):
                # Indian/MENA (arabic): slightly below South Asian HC, above other families
                if "(arabic)" in ethnicity.lower():
                    score = 0.95
                else:
                    score = 1.05
                    if is_amb or is_weak_with_western:
                        score = 0.55  # weak until first name helps
                    if is_hc and not is_amb and not is_weak_with_western:
                        score = 1.15
                if fn_signal == "indian":
                    score += 0.45
                elif fn_signal in ("anglo", "slavic"):
                    # Andrey (white) / Andrei (Slavic) both contradict South Asian
                    if "(arabic)" in ethnicity.lower():
                        score -= 0.25
                    elif is_amb or is_weak_with_western:
                        score -= 0.65
                    elif is_hc:
                        score -= 0.2
                    else:
                        score -= 0.35
                elif fn_signal == "hispanic":
                    # Alberto Perera / Carlos Silva — not Indian primary
                    score -= 0.75 if (
                        is_amb or is_weak_with_western or has_portuguese or has_hispanic
                    ) else 0.35
            elif ethnicity == "Hispanic":
                score = 0.95
                if fn_signal == "hispanic":
                    score += 0.5
                if fn_signal == "indian":
                    score -= 0.2
            elif ethnicity.startswith("Asian (filipino)"):
                score = 0.9
            elif ethnicity.startswith("Asian"):
                score = 1.0
            elif ethnicity == "African American":
                score = 0.95
                if fn_signal == "african_american":
                    score += 0.5  # DeShawn Washington, Jamal Jefferson
                elif fn_signal in ("anglo", "slavic"):
                    score -= 0.35  # John Washington — anglo given name dampens
                elif fn_signal == "hispanic":
                    score -= 0.2
            elif ethnicity in ("Jewish", "Portuguese", "Arabic"):
                # Arabic legacy label (pre Indian/MENA merge)
                score = 0.85
                if ethnicity == "Portuguese" and fn_signal == "hispanic":
                    score += 0.35  # Iberian cluster
                if ethnicity == "Portuguese" and fn_signal == "indian":
                    score += 0.15  # Goan Christians exist
            elif ethnicity.startswith("African ("):
                score = 0.8
            elif ethnicity == "Native American":
                score = 0.55
            elif ethnicity.startswith("European"):
                score = 0.4
                if fn_signal == "anglo":
                    score += 0.25
                if fn_signal == "slavic":
                    score += 0.4  # Andrei, Ivan, Dmitri, …
            else:
                score = 0.3
            return -score  # sort ascending → highest score first

        matches.sort(key=sort_key)
        best_match, _ = matches[0]

        # If first name is Hispanic and surname is ambiguous Luso/Indic overlap,
        # prefer Hispanic/Portuguese label over Indian when available.
        forced_by_first = False
        if (
            fn_signal == "hispanic"
            and best_match.startswith("Indian")
            and (is_amb or has_portuguese or has_hispanic)
        ):
            for eth, _src in matches:
                if eth in ("Hispanic", "Portuguese"):
                    best_match = eth
                    forced_by_first = True
                    break
            else:
                # Surname not on Hispanic list (e.g. Perera) — still not Indian
                best_match = "Hispanic"
                forced_by_first = True
                matches = list(matches) + [("Hispanic", "first_name_signal")]

        confidence = self._calculate_confidence(
            surname_lc,
            matches,
            best_match=best_match,
            first_name_signal=fn_signal,
            is_ambiguous=is_amb or is_weak_with_western,
            is_high_confidence_surname=is_hc and not is_weak_with_western,
        )

        if forced_by_first and best_match in ("Hispanic", "Portuguese"):
            # First-name-driven reclass: solid but not overconfident
            confidence = min(confidence, 0.62)
            confidence = max(confidence, 0.52)

        # Hard floors: weak/ambiguous Indian surname without Indic first name
        if best_match.startswith("Indian") and (is_amb or is_weak_with_western):
            if fn_signal in ("anglo", "slavic"):
                # Adam Dey, Andrey Lele, Andrei Lele — below default 0.5 Analyze floor
                confidence = min(confidence, 0.32)
            elif fn_signal == "hispanic":
                confidence = min(confidence, 0.25)
            elif fn_signal == "unknown":
                confidence = min(confidence, 0.42)
        elif best_match.startswith("Indian") and fn_signal in ("anglo", "slavic") and is_hc:
            # Amy Patel / Andrei Singh: still Indian label, damped confidence
            confidence = min(confidence, 0.55)

        return (best_match, confidence, [m[0] for m in matches])


