"""Ethnic name database for misclassification detection."""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class EthnicNameDatabase:
    """Loads and queries ethnic surname databases."""

    def __init__(self):
        self.hispanic_surnames = set()
        # East / Southeast Asian only (Chinese, Korean, Japanese, Vietnamese, Thai, Filipino, …)
        self.asian_surnames = {}  # nested dict by sub-group
        # South Asian / Indian subcontinent (India, Pakistan, Bangladesh, Sri Lanka, Nepal, …)
        self.indian_surnames = set()
        self.indian_surnames_by_group = {}  # optional nested: india, pakistani, …
        self.african_american_surnames = set()
        self.native_american_surnames = set()
        self.european_surnames = {}  # nested dict by country
        self.jewish_surnames = set()
        self.portuguese_surnames = set()
        self.arabic_surnames = set()
        self.african_surnames = {}  # nested dict by region

        self._load_ethnic_names()

    def _load_ethnic_names(self):
        """Load ethnic names from the JSON file."""
        json_path = Path(__file__).parent / "ethnic_names.json"

        if not json_path.exists():
            # Fallback: use embedded defaults
            self._use_defaults()
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Hispanic surnames (also includes some common ones that overlap)
        self.hispanic_surnames = set(data.get("hispanic_surnames", []))

        # Asian surnames by sub-group (East/Southeast only — no South Asian)
        asian_data = data.get("asian_surnames", {})
        for group, names in asian_data.items():
            if group.lower() in ("indian", "south_asian", "southasian"):
                # Legacy key: fold into indian list
                if isinstance(names, list):
                    self.indian_surnames.update(n.strip() for n in names if n and n.strip())
                continue
            if isinstance(names, list):
                self.asian_surnames[group] = set(n.strip() for n in names)

        # Indian / South Asian — list or nested dict by region
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

        # African-American surnames
        self.african_american_surnames = set(data.get("african_american_surnames", []))

        # Native American surnames
        self.native_american_surnames = set(data.get("native_american_surnames", []))

        # European surnames by country
        european_data = data.get("european_surnames", {})
        for country, names in european_data.items():
            if isinstance(names, list):
                self.european_surnames[country] = set(n.strip() for n in names)

        # Jewish surnames
        self.jewish_surnames = set(data.get("jewish_surnames", []))

        # Portuguese surnames
        self.portuguese_surnames = set(data.get("portuguese_surnames", []))

        # Arabic surnames
        self.arabic_surnames = set(data.get("arabic_surnames", []))

        # African surnames by region
        african_data = data.get("african_surnames", {})
        for region, names in african_data.items():
            if isinstance(names, list):
                self.african_surnames[region] = set(n.strip() for n in names)

    def _use_defaults(self):
        """Use default embedded name lists."""
        self.hispanic_surnames = {
            "Garcia", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
            "Perez", "Sanchez", "Ramirez", "Torres", "Flores", "Rivera", "Gomez",
            "Diaz", "Cruz", "Morales", "Ortiz", "Ramos", "Gutierrez", "Alvarez"
        }
        self.asian_surnames = {
            "chinese": {"Chen", "Wang", "Li", "Zhang", "Liu"},
            "korean": {"Kim", "Park", "Choi"},
            "japanese": {"Tanaka", "Suzuki", "Yamamoto"},
        }
        self.indian_surnames = {
            "Patel", "Shah", "Singh", "Kumar", "Gupta", "Sharma", "Reddy", "Nair"
        }

    def _build_lookup_sets(self) -> None:
        """Cache lowercased sets for O(1) surname membership checks."""
        if getattr(self, "_lookups_ready", False):
            return
        self._hispanic_lc = {n.lower() for n in self.hispanic_surnames}
        self._african_american_lc = {n.lower() for n in self.african_american_surnames}
        self._native_american_lc = {n.lower() for n in self.native_american_surnames}
        self._jewish_lc = {n.lower() for n in self.jewish_surnames}
        self._portuguese_lc = {n.lower() for n in self.portuguese_surnames}
        self._arabic_lc = {n.lower() for n in self.arabic_surnames}
        self._indian_lc = {n.lower() for n in self.indian_surnames}
        self._indian_group_lc = {
            group: {n.lower() for n in names}
            for group, names in (self.indian_surnames_by_group or {}).items()
        }
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

    def classify_by_name(self, surname: str) -> Tuple[str, float, List[str]]:
        """Classify a surname by ethnicity. Returns (ethnicity, confidence, matching_names)."""
        if not surname:
            return ("Unknown", 0.0, [])

        self._build_lookup_sets()
        surname_lc = surname.strip().lower()
        if not surname_lc:
            return ("Unknown", 0.0, [])

        matches: List[Tuple[str, str]] = []

        if surname_lc in self._hispanic_lc:
            matches.append(("Hispanic", "hispanic_surnames"))

        # South Asian / Indian before generic Asian so lists stay distinct
        if getattr(self, "indian_surnames_by_group", None):
            for group, names in getattr(self, "_indian_group_lc", {}).items():
                if surname_lc in names:
                    matches.append((f"Indian ({group})", f"indian_{group}"))
        if surname_lc in self._indian_lc and not any(m[0].startswith("Indian") for m in matches):
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

        # Prefer distinctive ethnic matches over broad/overlapping ones.
        def sort_key(item: Tuple[str, str]) -> float:
            ethnicity, _source = item
            if ethnicity == "Indian" or ethnicity.startswith("Indian ("):
                return -1.05
            # East Asian groups before Hispanic; Filipino Spanish surnames after Hispanic
            if ethnicity.startswith("Asian (filipino)"):
                return -0.9
            if ethnicity.startswith("Asian"):
                return -1.0
            if ethnicity == "Hispanic":
                return -0.95
            if ethnicity == "African American":
                return -0.9
            if ethnicity in ("Jewish", "Portuguese", "Arabic"):
                return -0.85
            if ethnicity.startswith("African ("):
                return -0.8
            if ethnicity == "Native American":
                return -0.55  # many generic nature/English overlaps
            if ethnicity.startswith("European"):
                return -0.4
            return -0.3

        matches.sort(key=sort_key)
        best_match, _ = matches[0]
        confidence = self._calculate_confidence(surname_lc, matches)

        return (best_match, confidence, [m[0] for m in matches])

    def get_likely_ethnicity(self, surname: str) -> Tuple[str, float]:
        """Get the most likely ethnicity for a surname."""
        ethnicity, confidence, _ = self.classify_by_name(surname)
        return (ethnicity, confidence)

    def is_hispanic_surname(self, surname: str) -> bool:
        """Check if a surname is commonly Hispanic."""
        self._build_lookup_sets()
        return surname.strip().lower() in self._hispanic_lc

    def is_asian_surname(self, surname: str) -> Tuple[bool, str]:
        """Check if a surname is East/Southeast Asian. Returns (is_asian, sub_group)."""
        self._build_lookup_sets()
        surname_lc = surname.strip().lower()
        for group, names in self._asian_lc.items():
            if surname_lc in names:
                return True, group
        return False, ""

    def is_indian_surname(self, surname: str) -> bool:
        """Check if a surname is commonly South Asian / Indian-subcontinent."""
        self._build_lookup_sets()
        return surname.strip().lower() in self._indian_lc

    def subcategories(self, ethnicity: str) -> List[str]:
        """
        Subcategory keys for a top-level ethnicity list.
        Always includes 'all' first when subgroups exist; flat lists return ['all'] only.
        """
        eth = (ethnicity or "").lower().strip()
        if eth == "asian":
            return ["all"] + sorted(self.asian_surnames.keys(), key=str.lower)
        if eth == "indian":
            groups = sorted((self.indian_surnames_by_group or {}).keys(), key=str.lower)
            return ["all"] + groups if groups else ["all"]
        if eth == "european":
            return ["all"] + sorted(self.european_surnames.keys(), key=str.lower)
        if eth == "african":
            return ["all"] + sorted(self.african_surnames.keys(), key=str.lower)
        # Flat lists (hispanic, african_american, …) or "all" top-level
        return ["all"]

    def has_subcategories(self, ethnicity: str) -> bool:
        subs = self.subcategories(ethnicity)
        return len(subs) > 1

    def is_african_american_surname(self, surname: str) -> bool:
        """Check if a surname is commonly African-American."""
        self._build_lookup_sets()
        return surname.strip().lower() in self._african_american_lc

    def _calculate_confidence(self, surname: str, matches: List[Tuple[str, str]]) -> float:
        """Calculate confidence score based on match specificity and ambiguity."""
        if not matches:
            return 0.0

        # Single clean match → high confidence
        base = 0.85 if len(matches) == 1 else 0.7

        # Distinct family groups that also matched reduce confidence
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

        if len(families) > 1:
            base -= 0.1 * (len(families) - 1)

        # Round to avoid float noise (e.g. 0.4999999999 < 0.5 thresholds)
        return round(max(0.4, min(base, 1.0)), 2)


# Singleton instance
_ethnic_db = None

def get_ethnic_database() -> EthnicNameDatabase:
    """Get the singleton ethnic name database."""
    global _ethnic_db
    if _ethnic_db is None:
        _ethnic_db = EthnicNameDatabase()
    return _ethnic_db
