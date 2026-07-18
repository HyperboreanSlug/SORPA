"""Rebuild scraper/statute_labels.json from DB co-occurrence + curated overrides.

Usage:
  python scripts/build_statute_labels.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "offenders.db"
OUT = ROOT / "scraper" / "statute_labels.json"

# Hand-curated titles win over mined majority labels
CURATED = {
    "28-201": "Criminal attempt",
    "28-314": "Kidnapping",
    "28-319": "First degree sexual assault",
    "28-319.01": "Sexual assault of a child in the first degree",
    "28-320": "Sexual assault",
    "28-320.01": "Sexual assault of a child",
    "28-320.02": "Sexual assault of a child by a school worker",
    "28-703": "Incest",
    "28-707": "Child abuse",
    "28-813.01": "Visual depiction of sexually explicit conduct involving a child",
    "28-833": "Enticement by electronic communication device",
    "28-1463.03": "Creation of visual depiction of sexually explicit conduct",
    "18-3-402": "Sexual assault",
    "18-3-403": "Sexual assault in the second degree",
    "18-3-404": "Unlawful sexual contact",
    "18-3-405": "Sexual assault on a child",
    "18-3-405.3": "Sexual assault on a child by one in a position of trust",
    "18-3-203": "Second degree assault",
    "18-6-403": "Sexual exploitation of a child",
    "800.04": "Lewd or lascivious offense upon or in presence of person under 16",
    "794.011": "Sexual battery",
    "943.0435": "Fail to register",
    "18.2-61": "Rape",
    "18.2-63": "Carnal knowledge of child 13 to 15",
    "18.2-67.1": "Forcible sodomy",
    "18.2-67.2": "Object sexual penetration",
    "18.2-67.3": "Aggravated sexual battery",
    "18.2-67.4": "Sexual battery",
    "18.2-370": "Taking indecent liberties with children",
    "18.2-370.1": "Indecent liberties with child by custodian",
    "18.2-472.1": "Fail to register",
    "709.1": "Sexual abuse",
    "709.2": "Sexual abuse in the first degree",
    "709.3": "Sexual abuse in the second degree",
    "709.4": "Sexual abuse in the third degree",
    "709.8": "Lascivious acts with a child",
    "709.11": "Assault with intent to commit sexual abuse",
    "709.12": "Indecent contact with a child",
    "22.011": "Sexual assault",
    "22.021": "Aggravated sexual assault",
    "21.11": "Indecency with a child",
    "43.25": "Sexual performance by a child",
    "43.26": "Possession of child pornography",
    "18USC2251": "Sexual exploitation of children",
    "18USC2252": "Certain activities relating to material involving sexual exploitation of minors",
    "18USC2252A": "Certain activities relating to material constituting or containing child pornography",
}


def _sentence(s: str) -> str:
    t = " ".join(s.split()).strip()
    if not t:
        return t
    t = t.lower()
    return t[0].upper() + t[1:]


def main() -> int:
    if not DB.is_file():
        print(f"missing db: {DB}", file=sys.stderr)
        return 1
    con = sqlite3.connect(str(DB))
    by_code: dict[str, Counter] = defaultdict(Counter)
    pat = re.compile(
        r"(?ix)(?:F\.?S\.?\s*|S\.?C\.?\s*Code\s*(?:Ann\.?)?\s*§?\s*|U\.?S\.?C\.?\s*§?\s*)?"
        r"("
        r"18\s*U\.?S\.?C\.?\s*§?\s*\d{3,4}[A-Z]?(?:\([a-z0-9]+\))*(?:\([a-z0-9]+\))*"
        r"|\d{1,2}\.\d{1,2}(?:-\d+(?:\.\d+)*)+(?:\([a-z0-9]+\))*"
        r"|\d{2,3}-\d{1,4}(?:\.\d+)?(?:\([a-z0-9]+\))*"
        r"|\d{3}\.\d{1,4}(?:\([a-z0-9]+\))*"
        r")\s*[—\-‑–:]+\s*"
        r"([A-Za-z][^\n;|]{3,80})"
    )
    for (crime,) in con.execute(
        "SELECT crime FROM offenders WHERE crime IS NOT NULL"
    ):
        for m in pat.finditer(crime or ""):
            code = re.sub(r"\s+", "", m.group(1).upper())
            code = code.replace("U.S.C.", "USC").replace("§", "")
            lab = " ".join(m.group(2).split()).strip(" .;,)(")
            lab = re.sub(
                r"(?i)\s*(guilty|convict|adjudication|includes\b).*$", "", lab
            ).strip()
            if len(lab) < 4 or re.search(
                r"(?i)^(statute|section|code|chapter|unknown|\.)$", lab
            ):
                continue
            by_code[code][lab] += 1
    con.close()

    out: dict[str, str] = {}
    for code, ctr in by_code.items():
        best, n = ctr.most_common(1)[0]
        total = sum(ctr.values())
        if n >= 2 and n / total >= 0.35:
            out[code] = _sentence(best)
    out.update(CURATED)
    OUT.write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT} ({len(out)} codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
