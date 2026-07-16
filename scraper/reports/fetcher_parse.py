from __future__ import annotations

from bs4 import BeautifulSoup

import re

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


from scraper.reports.fetcher_types import *  # noqa: F401,F403
from scraper.reports.util import (  # noqa: F401
    _CAPTCHA_MARKERS,
    _DISCLAIMER_MARKERS,
    _LABEL_MAP,
    _LONG_VALUE_KEYS,
    _MAX_CRIME_LEN,
    _PHOTO_HOST_STATE,
    _clean_value,
    _normalize_label,
    _normalize_url,
    photo_state_from_url,
    photo_url_variants,
    extract_dedicated_photo_urls,
)
from scraper.reports.race_value import is_plausible_race_value
from scraper.reports.fetcher_crime import (
    extract_crime_from_tables,
    is_demographic_crime_junk,
)

class FetcherParseMixin:
    def _from_html(self, html: str, base_url: str = "") -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        found: Dict[str, Any] = {}

        # Keep a raw copy for regex patterns before tag stripping
        raw_html = html

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        # --- Header-row tables (OK Apex, many report grids) ---
        # Require multiple <th> headers (not label|value rows that use th/td pairs).
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            ths = rows[0].find_all("th")
            if len(ths) < 2:
                continue
            headers = [_normalize_label(c.get_text(" ", strip=True)) for c in ths]
            # Nested outer wrappers glue many header groups — skip those
            if len(headers) > 10 or any(len(h) > 60 for h in headers):
                continue
            mapped_n = sum(1 for h in headers if h in _LABEL_MAP)
            if mapped_n < 2:
                continue
            for data_row in rows[1:]:
                tds = data_row.find_all("td")
                if not tds:
                    continue
                values = [_clean_value(td.get_text(" ", strip=True)) for td in tds]
                crime_parts = []
                for h, v in zip(headers, values):
                    key = _LABEL_MAP.get(h)
                    if not key or not v or _normalize_label(v) in _LABEL_MAP:
                        continue
                    if key == "crime":
                        crime_parts.append(v)
                    else:
                        found.setdefault(key, v)
                if crime_parts:
                    joined = " — ".join(crime_parts)[:_MAX_CRIME_LEN]
                    prev = found.get("crime") or ""
                    if not prev or is_demographic_crime_junk(prev):
                        found["crime"] = joined
                    elif joined not in prev:
                        found["crime"] = f"{prev}; {joined}"[:_MAX_CRIME_LEN]
                if any(k in found for k in ("race", "gender", "height", "crime")):
                    break

        # PrimeFaces / FL style: alternating label/value cells
        cells = soup.select("div.borderPanelCell, div.ui-g-12.borderPanelCell")
        if len(cells) >= 2:
            i = 0
            while i < len(cells) - 1:
                lab = _normalize_label(cells[i].get_text(" ", strip=True))
                val = _clean_value(cells[i + 1].get_text(" ", strip=True))
                if lab in _LABEL_MAP and val and len(val) < 120 and lab != _normalize_label(val):
                    found.setdefault(_LABEL_MAP[lab], val)
                    i += 2
                else:
                    i += 1

        # Bootstrap / CA Megans Law: Label:</div><div class="col-…">VALUE</div>
        # Word boundaries required — bare "Race" otherwise matches TERRACE / HORACE.
        for m in re.finditer(
            r"(?<![A-Za-z])(Race|Ethnicity|Sex|Gender|Height|Weight|Hair Color|Eye Color|"
            r"Eyes|Hair|Date of Birth|DOB|Age)(?![A-Za-z])\s*:?\s*"
            r"</(?:div|span|label|strong|b|dt|th)>\s*"
            r"<(?:div|span|dd|td)[^>]*>\s*([^<]{1,80}?)\s*</(?:div|span|dd|td)>",
            raw_html,
            flags=re.I,
        ):
            key = _LABEL_MAP.get(m.group(1).lower())
            if key:
                found.setdefault(key, _clean_value(m.group(2)))

        # DE / label + sibling span (possibly empty if Knockout not rendered)
        for lab in soup.find_all("label"):
            label = _normalize_label(lab.get_text(" ", strip=True))
            if label not in _LABEL_MAP:
                continue
            val = ""
            fid = lab.get("for")
            if fid:
                target = soup.find(id=fid)
                if target is not None:
                    val = _clean_value(target.get_text(" ", strip=True))
            if not val:
                for sib in lab.find_next_siblings(["span", "div", "p", "td"]):
                    val = _clean_value(sib.get_text(" ", strip=True))
                    if val and _normalize_label(val) not in _LABEL_MAP:
                        break
                    val = ""
            if not val and lab.parent is not None:
                # parent row: "Race: | Black"
                ptext = lab.parent.get_text(" ", strip=True)
                rest = re.sub(
                    re.escape(lab.get_text(" ", strip=True)),
                    "",
                    ptext,
                    count=1,
                    flags=re.I,
                ).strip(" :-|")
                if rest and len(rest) < 80:
                    val = _clean_value(rest)
            if val and val not in ("(unknown)", "—", "-") and not val.startswith("("):
                found.setdefault(_LABEL_MAP[label], val)

        for dt in soup.find_all("dt"):
            label = _normalize_label(dt.get_text(" ", strip=True))
            dd = dt.find_next_sibling("dd")
            if dd and label in _LABEL_MAP:
                found.setdefault(_LABEL_MAP[label], _clean_value(dd.get_text(" ", strip=True)))

        _header_like_values = frozenset({
            "type", "row", "jurisdiction", "chapter/section", "live", "work",
            "address", "name", "level", "age", "sex", "race", "height", "weight",
            "no. of convictions", "conviction/adjudication date",
        })
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            # Skip pure header rows (e.g. Address | Type → would set address="Type")
            if cells and all(getattr(c, "name", "") == "th" for c in cells):
                continue
            # Pair adjacent cells: [label, value, label, value, ...]
            # (iCrimeWatch rows often pack two fields per <tr>)
            i = 0
            while i < len(cells) - 1:
                # Do not pair a data label with a <th> column header
                if getattr(cells[i + 1], "name", "") == "th":
                    i += 1
                    continue
                label = _normalize_label(cells[i].get_text(" ", strip=True))
                value = _clean_value(cells[i + 1].get_text(" ", strip=True))
                key = _LABEL_MAP.get(label)
                max_len = _MAX_CRIME_LEN if key in _LONG_VALUE_KEYS else 200
                val_norm = _normalize_label(value)
                if (
                    key
                    and value
                    and len(value) <= max_len
                    and val_norm not in _LABEL_MAP
                    and val_norm not in _header_like_values
                ):
                    found.setdefault(key, value[:max_len])
                    i += 2
                else:
                    i += 1

        # Offense / crime tables (column headers like Offense, Charge, Statute)
        crime_bits = extract_crime_from_tables(soup)
        if crime_bits:
            prev = (found.get("crime") or "").strip()
            if not prev or is_demographic_crime_junk(prev):
                found["crime"] = crime_bits
            elif len(crime_bits) > len(prev):
                found["crime"] = crime_bits
            elif crime_bits not in prev:
                found["crime"] = f"{prev}; {crime_bits}"[:_MAX_CRIME_LEN]

        # Label-only node → next sibling element holds value (common grid layouts)
        for lab_el in soup.find_all(["span", "div", "label", "strong", "b", "th", "td", "p"]):
            raw = lab_el.get_text(" ", strip=True)
            if not raw or len(raw) > 60:
                continue
            label = _normalize_label(raw)
            if label not in _LABEL_MAP:
                # "Race: White" on same node
                m = re.match(
                    r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|"
                    r"Eyes|Hair|Age|DOB|Date of Birth)\s*[:\-]\s*(.+)$",
                    raw,
                    flags=re.I,
                )
                if m:
                    key = _LABEL_MAP.get(m.group(1).lower())
                    if key:
                        found.setdefault(key, m.group(2).strip()[:120])
                continue
            # Never pair table header cells with each other (Address | Type)
            if getattr(lab_el, "name", "") == "th":
                continue
            # empty value on label node → look next
            if ":" in raw and not re.search(r":\s*\S", raw):
                # parent then next sibling
                parent = lab_el.parent
                candidates = []
                if parent is not None:
                    candidates.append(parent.find_next_sibling())
                candidates.append(lab_el.find_next_sibling())
                for nxt in candidates:
                    if not nxt or not hasattr(nxt, "get_text"):
                        continue
                    if getattr(nxt, "name", "") == "th":
                        continue
                    val = nxt.get_text(" ", strip=True)
                    if val and len(val) < 80 and _normalize_label(val) not in _LABEL_MAP:
                        found.setdefault(_LABEL_MAP[label], val)
                        break

        for lab in soup.find_all(["label", "strong", "b", "span", "div", "p"]):
            raw = lab.get_text(" ", strip=True)
            if not raw or len(raw) > 80:
                continue
            m = re.match(
                r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|Age|DOB|Date of Birth)\s*[:\-]\s*(.+)$",
                raw,
                flags=re.I,
            )
            if m:
                key = _LABEL_MAP.get(m.group(1).lower())
                if key:
                    found.setdefault(key, m.group(2).strip())
                continue

            label = _normalize_label(raw)
            if label in _LABEL_MAP:
                nxt = lab.find_next_sibling(string=True)
                if nxt and str(nxt).strip():
                    found.setdefault(_LABEL_MAP[label], str(nxt).strip())
                    continue
                parent = lab.parent
                if parent:
                    ptext = parent.get_text(" ", strip=True)
                    rest = re.sub(re.escape(raw), "", ptext, count=1, flags=re.I).strip(" :-")
                    if rest and len(rest) < 80:
                        found.setdefault(_LABEL_MAP[label], rest)

        body_text = soup.get_text("\n", strip=True)
        for line in body_text.splitlines():
            m = re.match(
                r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|Age|"
                r"Date of Birth|DOB|Offense|Offense Description|Charge|Charges|Crime|"
                r"Qualifying Offense|Registerable Offense|Statute)\s*[:\-]\s*(.+)$",
                line.strip(),
                flags=re.I,
            )
            if m:
                key = _LABEL_MAP.get(_normalize_label(m.group(1)))
                if key:
                    lim = _MAX_CRIME_LEN if key in _LONG_VALUE_KEYS else 120
                    found.setdefault(key, m.group(2).strip()[:lim])

        # Re-parse scripts from original HTML for embedded JSON
        for script in BeautifulSoup(html, "html.parser").find_all("script"):
            content = script.string or ""
            if len(content) >= 500_000:
                continue
            low = content.lower()
            if "race" in low or "offense" in low or "charge" in low:
                for m in re.finditer(
                    r'"(race|ethnicity|gender|sex|height|weight|eyeColor|hairColor|'
                    r'offense|offenseDescription|offenseType|charge|charges|crime|'
                    r'statute|qualifyingOffense)"\s*:\s*"([^"]{1,400})"',
                    content,
                    flags=re.I,
                ):
                    raw_key = m.group(1).lower()
                    key = {
                        "race": "race",
                        "ethnicity": "ethnicity",
                        "gender": "gender",
                        "sex": "gender",
                        "height": "height",
                        "weight": "weight",
                        "eyecolor": "eye_color",
                        "haircolor": "hair_color",
                        "offense": "crime",
                        "offensedescription": "crime",
                        "offensetype": "offense_type",
                        "charge": "crime",
                        "charges": "crime",
                        "crime": "crime",
                        "statute": "crime",
                        "qualifyingoffense": "crime",
                    }.get(raw_key)
                    if key:
                        found.setdefault(key, m.group(2)[:_MAX_CRIME_LEN])

        if "age" in found:
            try:
                found["age"] = int(re.sub(r"[^\d]", "", str(found["age"])) or 0) or found["age"]
            except (TypeError, ValueError):
                pass

        # Drop bogus gender values
        g = str(found.get("gender") or "").strip().lower()
        if g in ("minor", "description", "status", "yes", "no"):
            found.pop("gender", None)

        # Clean all string fields
        for k, v in list(found.items()):
            if isinstance(v, str):
                found[k] = _clean_value(v)

        # Drop alias/address false-positives (e.g. "PAUL, JOHN K", "REX, GA 30273")
        race_val = str(found.get("race") or "").strip()
        if race_val and not is_plausible_race_value(race_val):
            found.pop("race", None)

        # Person name for identity gate (must match DB record before attach)
        if not found.get("full_name"):
            try:
                from scraper.reports.identity_gate import extract_person_name_from_html

                pn = extract_person_name_from_html(raw_html)
                if pn:
                    found["full_name"] = pn
            except Exception:
                pass

        # CA and others often use Ethnicity where Race is expected
        if not found.get("race") and found.get("ethnicity"):
            eth = str(found["ethnicity"]).strip()
            if (
                eth
                and eth.lower() not in ("unknown", "undetermined", "n/a", "none")
                and is_plausible_race_value(eth)
            ):
                found["race"] = eth

        # Synthesize primary crime field from any offense pieces
        self._finalize_crime_fields(found)

        if base_url:
            found["report_final_url"] = base_url
        return found


    @staticmethod
    def _extract_crime_from_tables(soup: BeautifulSoup) -> str:
        """Pull offense/charge text from multi-row offense tables."""
        return extract_crime_from_tables(soup)

    @staticmethod
    def _finalize_crime_fields(found: Dict[str, Any]) -> None:
        """Ensure `crime` is set for display; keep offense_* in sync when possible."""
        crime = (found.get("crime") or "").strip()
        otype = (found.get("offense_type") or "").strip()
        odesc = (found.get("offense_description") or "").strip()
        if crime and is_demographic_crime_junk(crime):
            crime = ""
            found.pop("crime", None)
        if otype and is_demographic_crime_junk(otype):
            otype = ""
            found.pop("offense_type", None)
        if odesc and is_demographic_crime_junk(odesc):
            odesc = ""
            found.pop("offense_description", None)
        if not crime:
            if odesc and otype and odesc.lower() != otype.lower():
                crime = f"{otype}: {odesc}"
            else:
                crime = odesc or otype
        if crime and is_demographic_crime_junk(crime):
            found.pop("crime", None)
            found.pop("offense_type", None)
            found.pop("offense_description", None)
            return
        if crime:
            found["crime"] = crime[:_MAX_CRIME_LEN]
            if not odesc:
                found["offense_description"] = crime[:_MAX_CRIME_LEN]
            if not otype and len(crime) < 120:
                found["offense_type"] = crime


    def _from_json_blob(self, data: Any, prefix: str = "") -> Dict[str, Any]:
        found: Dict[str, Any] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                kl = str(k).lower().replace("_", "")
                mapped = {
                    "race": "race",
                    "ethnicity": "ethnicity",
                    "gender": "gender",
                    "sex": "gender",
                    "height": "height",
                    "weight": "weight",
                    "offense": "crime",
                    "offensedescription": "crime",
                    "offensetype": "offense_type",
                    "charge": "crime",
                    "charges": "crime",
                    "crime": "crime",
                    "statute": "crime",
                    "qualifyingoffense": "crime",
                    "eyecolor": "eye_color",
                    "haircolor": "hair_color",
                    "skintone": "skin_tone",
                    "build": "build",
                    "age": "age",
                    "dateofbirth": "date_of_birth",
                    "dob": "date_of_birth",
                    "county": "county",
                    "city": "city",
                    "address": "address",
                    "risklevel": "risk_level",
                }.get(kl)
                if mapped and isinstance(v, (str, int, float)) and str(v).strip():
                    found.setdefault(mapped, v)
                elif isinstance(v, (dict, list)) and len(str(v)) < 10000:
                    found.update(self._from_json_blob(v))
        elif isinstance(data, list):
            for item in data[:50]:
                found.update(self._from_json_blob(item))
        return found


    def _try_texas_json(self, report_url: str) -> Optional[Dict[str, Any]]:
        if "sor.dps.texas.gov" not in report_url.lower():
            return None
        m = re.search(r"[?&]sid=([^&]+)", report_url, flags=re.I)
        if not m:
            return None
        sid = m.group(1)
        candidates = [
            f"https://sor.dps.texas.gov/Search/Rapsheet/Index?sid={sid}&handler=GetRapsheet",
            f"https://publicsite.dps.texas.gov/SexOffenderRegistry/Search/Rapsheet?sid={sid}",
        ]
        for url in candidates:
            try:
                resp = self._get(
                    url,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": report_url,
                    },
                )
                if resp.status_code != 200:
                    continue
                ct = (resp.headers.get("Content-Type") or "").lower()
                if "json" in ct:
                    return self._from_json_blob(resp.json())
                # Sometimes returns JSON with wrong content-type
                text = resp.text.strip()
                if text.startswith("{") or text.startswith("["):
                    try:
                        import json as _json

                        return self._from_json_blob(_json.loads(text))
                    except ValueError:
                        pass
                if "race" in resp.text.lower():
                    return self._from_html(resp.text, base_url=resp.url)
            except Exception:
                continue
        return None


