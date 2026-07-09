"""Ethnic name database for misclassification detection.

Methodology (important):
  * Surname alone is NEVER enough for high confidence on ambiguous names
    (e.g. Gill, Perera, Silva) that appear across multiple ethnic groups.
  * First names are scored together with surnames. Anglo first names
    (Amy, John, …) tank confidence for weak/ambiguous Indian surnames.
  * Hispanic first names (Alberto, Carlos, …) with Luso/Hispanic-overlapping
    surnames (Perera, Silva, …) prefer Hispanic / low Indian confidence.
  * Distinctive high-confidence Indian surnames (Patel, Singh, …) stay strong
    unless the first name strongly contradicts.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class EthnicNameDatabase:
    """Loads and queries ethnic surname + first-name databases."""

    def __init__(self):
        self.hispanic_surnames: Set[str] = set()
        self.asian_surnames: Dict[str, Set[str]] = {}
        self.indian_surnames: Set[str] = set()
        self.indian_surnames_by_group: Dict[str, Set[str]] = {}
        self.indian_high_confidence_surnames: Set[str] = set()
        self.indian_surname_exclusions: Set[str] = set()
        self.indian_ambiguous_surnames: Set[str] = set()
        self.indian_first_names: Set[str] = set()
        self.hispanic_first_names: Set[str] = set()
        self.anglo_western_first_names: Set[str] = set()
        self.african_american_surnames: Set[str] = set()
        self.native_american_surnames: Set[str] = set()
        self.european_surnames: Dict[str, Set[str]] = {}
        self.jewish_surnames: Set[str] = set()
        self.portuguese_surnames: Set[str] = set()
        self.arabic_surnames: Set[str] = set()
        self.african_surnames: Dict[str, Set[str]] = {}

        self._lookups_ready = False
        self._load_ethnic_names()

    def _load_ethnic_names(self):
        """Load ethnic names from the JSON file."""
        json_path = Path(__file__).parent / "ethnic_names.json"

        if not json_path.exists():
            self._use_defaults()
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.hispanic_surnames = set(data.get("hispanic_surnames", []))

        asian_data = data.get("asian_surnames", {})
        for group, names in asian_data.items():
            if group.lower() in ("indian", "south_asian", "southasian"):
                if isinstance(names, list):
                    self.indian_surnames.update(n.strip() for n in names if n and n.strip())
                continue
            if isinstance(names, list):
                self.asian_surnames[group] = set(n.strip() for n in names)

        top_indian = data.get("indian_surnames", [])
        if isinstance(top_indian, dict):
            for group, names in top_indian.items():
                if not isinstance(names, list):
                    continue
                cleaned = {n.strip() for n in names if n and str(n).strip()}
                self.indian_surnames_by_group[group] = cleaned
                self.indian_surnames.update(cleaned)
        elif isinstance(top_indian, list):
            self.indian_surnames.update(n.strip() for n in top_indian if n and n.strip())

        hc = data.get("indian_high_confidence_surnames", [])
        if isinstance(hc, list):
            self.indian_high_confidence_surnames = {
                n.strip() for n in hc if n and str(n).strip()
            }
            self.indian_surnames.update(self.indian_high_confidence_surnames)
            if self.indian_high_confidence_surnames:
                self.indian_surnames_by_group.setdefault(
                    "high_confidence", set()
                ).update(self.indian_high_confidence_surnames)

        # Hard exclusions — never Indian
        excl_raw = data.get("indian_surname_exclusions", [])
        self.indian_surname_exclusions = {
            n.strip() for n in (excl_raw or []) if n and str(n).strip()
        }
        if self.indian_surname_exclusions:
            excl_lc = {n.lower() for n in self.indian_surname_exclusions}
            self.indian_surnames = {
                n for n in self.indian_surnames if n.lower() not in excl_lc
            }
            self.indian_high_confidence_surnames = {
                n for n in self.indian_high_confidence_surnames
                if n.lower() not in excl_lc
            }
            for group, names in list(self.indian_surnames_by_group.items()):
                self.indian_surnames_by_group[group] = {
                    n for n in names if n.lower() not in excl_lc
                }

        amb = data.get("indian_ambiguous_surnames", [])
        self.indian_ambiguous_surnames = {
            n.strip() for n in (amb or []) if n and str(n).strip()
        }

        self.indian_first_names = {
            n.strip() for n in (data.get("indian_first_names") or []) if n and str(n).strip()
        }
        self.hispanic_first_names = {
            n.strip()
            for n in (data.get("hispanic_first_names") or [])
            if n and str(n).strip()
        }
        self.anglo_western_first_names = {
            n.strip()
            for n in (data.get("anglo_western_first_names") or [])
            if n and str(n).strip()
        }

        self.african_american_surnames = set(data.get("african_american_surnames", []))
        self.native_american_surnames = set(data.get("native_american_surnames", []))

        european_data = data.get("european_surnames", {})
        for country, names in european_data.items():
            if isinstance(names, list):
                self.european_surnames[country] = set(n.strip() for n in names)

        self.jewish_surnames = set(data.get("jewish_surnames", []))
        self.portuguese_surnames = set(data.get("portuguese_surnames", []))
        self.arabic_surnames = set(data.get("arabic_surnames", []))

        african_data = data.get("african_surnames", {})
        for region, names in african_data.items():
            if isinstance(names, list):
                self.african_surnames[region] = set(n.strip() for n in names)

    def _use_defaults(self):
        """Use default embedded name lists."""
        self.hispanic_surnames = {
            "Garcia", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
            "Perez", "Sanchez", "Ramirez", "Torres", "Flores", "Rivera", "Gomez",
            "Diaz", "Cruz", "Morales", "Ortiz", "Ramos", "Gutierrez", "Alvarez",
        }
        self.asian_surnames = {
            "chinese": {"Chen", "Wang", "Li", "Zhang", "Liu"},
            "korean": {"Kim", "Park", "Choi"},
            "japanese": {"Tanaka", "Suzuki", "Yamamoto"},
        }
        self.indian_surnames = {
            "Patel", "Shah", "Singh", "Kumar", "Gupta", "Sharma", "Reddy", "Nair",
        }
        self.indian_high_confidence_surnames = set(self.indian_surnames)
        self.indian_surnames_by_group = {"high_confidence": set(self.indian_surnames)}
        self.indian_first_names = {"Rahul", "Priya", "Amit", "Neha", "Raj"}
        self.hispanic_first_names = {"Alberto", "Carlos", "Maria", "Jose"}
        self.anglo_western_first_names = {"Amy", "John", "Robert", "Emily"}
        self.indian_ambiguous_surnames = {"Gill", "Perera", "Silva"}

    def _build_lookup_sets(self) -> None:
        """Cache lowercased sets for O(1) membership checks."""
        if getattr(self, "_lookups_ready", False):
            return
        self._hispanic_lc = {n.lower() for n in self.hispanic_surnames}
        self._african_american_lc = {n.lower() for n in self.african_american_surnames}
        self._native_american_lc = {n.lower() for n in self.native_american_surnames}
        self._jewish_lc = {n.lower() for n in self.jewish_surnames}
        self._portuguese_lc = {n.lower() for n in self.portuguese_surnames}
        self._arabic_lc = {n.lower() for n in self.arabic_surnames}
        self._indian_excl_lc = {
            n.lower() for n in (self.indian_surname_exclusions or set())
        }
        self._indian_amb_lc = {
            n.lower() for n in (self.indian_ambiguous_surnames or set())
        }
        self._indian_lc = {
            n.lower() for n in self.indian_surnames
            if n.lower() not in self._indian_excl_lc
        }
        self._indian_hc_lc = {
            n.lower() for n in (self.indian_high_confidence_surnames or set())
            if n.lower() not in self._indian_excl_lc
        }
        self._indian_group_lc = {
            group: {
                n.lower() for n in names if n.lower() not in self._indian_excl_lc
            }
            for group, names in (self.indian_surnames_by_group or {}).items()
        }
        def _fold_set(names) -> set:
            out = set()
            for n in names or set():
                out.add(self._fold_accents(str(n)).lower())
            return out

        self._indian_first_lc = _fold_set(self.indian_first_names)
        self._hispanic_first_lc = _fold_set(self.hispanic_first_names)
        self._anglo_first_lc = _fold_set(self.anglo_western_first_names)
        self._asian_lc = {
            group: {n.lower() for n in names}
            for group, names in self.asian_surnames.items()
        }
        self._european_lc = {
            country: {n.lower() for n in names}
            for country, names in self.european_surnames.items()
        }
        self._african_lc = {
            region: {n.lower() for n in names}
            for region, names in self.african_surnames.items()
        }
        self._lookups_ready = True

    @staticmethod
    def _fold_accents(text: str) -> str:
        """CRISTÓBAL → cristobal for list matching."""
        if not text:
            return ""
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    @classmethod
    def _normalize_given_name(cls, first_name: Optional[str]) -> str:
        """First token of given name, letters only (handles 'MARY-ANN', 'J.')."""
        if not first_name:
            return ""
        raw = str(first_name).strip()
        if not raw:
            return ""
        # Take first whitespace token; strip punctuation
        token = raw.replace(",", " ").split()[0]
        token = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ\-']", "", token)
        token = token.strip("-'")
        return cls._fold_accents(token).lower()

    def _first_name_signal(self, first_name: Optional[str]) -> str:
        """
        Return one of: indian | hispanic | anglo | unknown
        """
        self._build_lookup_sets()
        fn = self._normalize_given_name(first_name)
        if not fn or len(fn) < 2:
            return "unknown"
        # Prefer more specific lists; allow dual membership to favor indian
        # only when explicitly in indian list
        if fn in self._indian_first_lc:
            return "indian"
        if fn in self._hispanic_first_lc:
            return "hispanic"
        if fn in self._anglo_first_lc:
            return "anglo"
        return "unknown"

    def classify_by_name(
        self,
        surname: str,
        first_name: Optional[str] = None,
    ) -> Tuple[str, float, List[str]]:
        """
        Classify a person by surname + optional first name.

        Returns (ethnicity, confidence, matching_labels).
        Confidence is intentionally conservative for multi-ethnic surnames.
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
                matches.append(("Indian (high_confidence)", "indian_high_confidence"))
            if self.indian_surnames_by_group:
                for group, names in self._indian_group_lc.items():
                    if group == "high_confidence":
                        continue
                    if surname_lc in names:
                        matches.append((f"Indian ({group})", f"indian_{group}"))
            if surname_lc in self._indian_lc and not any(
                m[0].startswith("Indian") for m in matches
            ):
                matches.append(("Indian", "indian_surnames"))

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

        if surname_lc in self._arabic_lc:
            matches.append(("Arabic", "arabic_surnames"))

        for region, names in self._african_lc.items():
            if surname_lc in names:
                matches.append((f"African ({region})", f"african_{region}"))

        if not matches:
            return ("Unknown", 0.0, [])

        fn_signal = self._first_name_signal(first_name)
        is_amb = surname_lc in self._indian_amb_lc
        is_hc = surname_lc in self._indian_hc_lc
        has_indian = any(m[0].startswith("Indian") for m in matches)
        has_hispanic = any(m[0] == "Hispanic" for m in matches)
        has_portuguese = any(m[0] == "Portuguese" for m in matches)
        has_european = any(m[0].startswith("European") for m in matches)

        # ---- Choose best ethnicity (first-name aware) ----
        def sort_key(item: Tuple[str, str]) -> float:
            ethnicity, _source = item
            score = 0.0
            if ethnicity == "Indian" or ethnicity.startswith("Indian ("):
                score = 1.05
                if is_amb:
                    score = 0.55  # weak until first name helps
                if is_hc and not is_amb:
                    score = 1.15
                if fn_signal == "indian":
                    score += 0.45
                elif fn_signal == "anglo":
                    score -= 0.55 if is_amb else (0.25 if not is_hc else 0.15)
                elif fn_signal == "hispanic":
                    # Alberto Perera / Carlos Silva — not Indian primary
                    score -= 0.75 if is_amb or has_portuguese or has_hispanic else 0.35
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
                score = 0.9
            elif ethnicity in ("Jewish", "Portuguese", "Arabic"):
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
            is_ambiguous=is_amb,
            is_high_confidence_surname=is_hc,
        )

        if forced_by_first and best_match in ("Hispanic", "Portuguese"):
            # First-name-driven reclass: solid but not overconfident
            confidence = min(confidence, 0.62)
            confidence = max(confidence, 0.52)

        # Hard floors: ambiguous Indian surname without Indic first name
        if best_match.startswith("Indian") and is_amb:
            if fn_signal == "anglo":
                confidence = min(confidence, 0.28)
            elif fn_signal == "hispanic":
                confidence = min(confidence, 0.25)
            elif fn_signal == "unknown":
                confidence = min(confidence, 0.42)

        return (best_match, confidence, [m[0] for m in matches])

    def get_likely_ethnicity(
        self,
        surname: str,
        first_name: Optional[str] = None,
    ) -> Tuple[str, float]:
        """Get the most likely ethnicity for a name."""
        ethnicity, confidence, _ = self.classify_by_name(surname, first_name=first_name)
        return (ethnicity, confidence)

    def is_hispanic_surname(self, surname: str) -> bool:
        self._build_lookup_sets()
        return surname.strip().lower() in self._hispanic_lc

    def is_asian_surname(self, surname: str) -> Tuple[bool, str]:
        self._build_lookup_sets()
        surname_lc = surname.strip().lower()
        for group, names in self._asian_lc.items():
            if surname_lc in names:
                return True, group
        return False, ""

    def is_indian_surname(self, surname: str) -> bool:
        self._build_lookup_sets()
        lc = surname.strip().lower()
        if lc in self._indian_excl_lc:
            return False
        return lc in self._indian_lc

    def is_indian_high_confidence_surname(self, surname: str) -> bool:
        self._build_lookup_sets()
        lc = surname.strip().lower()
        if lc in self._indian_excl_lc:
            return False
        return lc in self._indian_hc_lc

    def is_indian_ambiguous_surname(self, surname: str) -> bool:
        self._build_lookup_sets()
        return surname.strip().lower() in self._indian_amb_lc

    def subcategories(self, ethnicity: str) -> List[str]:
        eth = (ethnicity or "").lower().strip()
        if eth in (
            "indian_high_confidence",
            "high_confidence_indian",
            "high-confidence indian",
            "indian_hc",
        ):
            return ["all"]
        if eth == "asian":
            return ["all"] + sorted(self.asian_surnames.keys(), key=str.lower)
        if eth == "indian":
            groups = sorted((self.indian_surnames_by_group or {}).keys(), key=str.lower)
            if "high_confidence" in groups:
                groups = ["high_confidence"] + [g for g in groups if g != "high_confidence"]
            return ["all"] + groups if groups else ["all"]
        if eth == "european":
            return ["all"] + sorted(self.european_surnames.keys(), key=str.lower)
        if eth == "african":
            return ["all"] + sorted(self.african_surnames.keys(), key=str.lower)
        return ["all"]

    def has_subcategories(self, ethnicity: str) -> bool:
        return len(self.subcategories(ethnicity)) > 1

    def is_african_american_surname(self, surname: str) -> bool:
        self._build_lookup_sets()
        return surname.strip().lower() in self._african_american_lc

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
        else:
            base = 0.8 if len(matches) == 1 else 0.65

        # First-name adjustment
        if first_name_signal == "indian":
            if best_match.startswith("Indian"):
                base = min(1.0, base + (0.4 if is_ambiguous else 0.12))
            elif best_match in ("Hispanic", "Portuguese"):
                base = max(0.35, base - 0.15)
        elif first_name_signal == "hispanic":
            if best_match.startswith("Indian"):
                base = min(base, 0.25 if is_ambiguous else 0.4)
            elif best_match in ("Hispanic", "Portuguese"):
                base = min(1.0, base + 0.12)
        elif first_name_signal == "anglo":
            if best_match.startswith("Indian"):
                if is_ambiguous:
                    base = min(base, 0.28)
                elif is_high_confidence_surname:
                    # Possible intermarriage / diaspora given name — still dampen
                    base = min(base, 0.62)
                else:
                    base = min(base, 0.45)
            elif best_match.startswith("European"):
                base = min(1.0, base + 0.1)

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
            if ethnicity == "Indian" or ethnicity.startswith("Indian ("):
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


_ethnic_db = None


def get_ethnic_database() -> EthnicNameDatabase:
    """Get the singleton ethnic name database."""
    global _ethnic_db
    if _ethnic_db is None:
        _ethnic_db = EthnicNameDatabase()
    return _ethnic_db
