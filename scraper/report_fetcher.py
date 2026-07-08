"""
Fetch jurisdiction offender report pages linked from NSOPW, extract demographics,
and optionally archive the raw HTML next to the database for offline validation.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup

from .config import USER_AGENT, DEFAULT_DELAY, REQUEST_TIMEOUT

_LABEL_MAP = {
    "race": "race",
    "racial": "race",
    "ethnicity": "ethnicity",
    "ethnic origin": "ethnicity",
    "sex": "gender",
    "gender": "gender",
    "height": "height",
    "weight": "weight",
    "eye color": "eye_color",
    "eyes": "eye_color",
    "hair color": "hair_color",
    "hair": "hair_color",
    "skin tone": "skin_tone",
    "complexion": "skin_tone",
    "build": "build",
    "age": "age",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "birth date": "date_of_birth",
    "county": "county",
    "city": "city",
    "address": "address",
    "risk level": "risk_level",
    "offense": "offense_type",
    "offense description": "offense_description",
    "conviction": "conviction_date",
}


class ReportFetcher:
    """HTTP client that scrapes demographic fields from report URLs."""

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: float = REQUEST_TIMEOUT):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def close(self) -> None:
        self.session.close()

    def fetch_demographics(
        self,
        report_url: str,
        save_html: bool = False,
        html_dir: Optional[Path] = None,
        jurisdiction: str = "UNK",
    ) -> Dict[str, Any]:
        """
        Fetch a report URL and return extracted fields.

        When save_html=True, writes the response body under html_dir and sets
        result['report_html_path'] to the relative/local path.
        """
        result: Dict[str, Any] = {
            "report_url": report_url,
            "report_fetch_ok": False,
            "report_fetch_status": None,
        }
        if not report_url or not report_url.startswith("http"):
            result["report_fetch_status"] = "invalid_url"
            return result

        report_url = html_lib.unescape(report_url).strip()
        result["report_url"] = report_url

        try:
            # Texas SOR: try JSON detail endpoint when rapsheet HTML is a JS shell
            tx_json = self._try_texas_json(report_url)
            if tx_json is not None:
                # Still fetch HTML for archival if requested
                if save_html and html_dir is not None:
                    try:
                        resp = self.session.get(
                            report_url, timeout=self.timeout, allow_redirects=True
                        )
                        path = self._save_html(
                            resp.content, report_url, html_dir, jurisdiction, resp.url
                        )
                        if path:
                            result["report_html_path"] = path
                            result["report_final_url"] = resp.url
                    except requests.RequestException:
                        pass
                result.update(tx_json)
                result["report_fetch_ok"] = bool(
                    tx_json.get("race") or tx_json.get("ethnicity") or tx_json.get("gender")
                )
                result["report_fetch_status"] = result.get("report_fetch_status") or 200
                time.sleep(self.delay)
                return result

            resp = self.session.get(report_url, timeout=self.timeout, allow_redirects=True)
            result["report_fetch_status"] = resp.status_code
            result["report_final_url"] = resp.url
            time.sleep(self.delay)
            if resp.status_code >= 400:
                return result

            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw_bytes = resp.content

            if save_html and html_dir is not None:
                path = self._save_html(raw_bytes, report_url, html_dir, jurisdiction, resp.url)
                if path:
                    result["report_html_path"] = path

            if "json" in content_type:
                try:
                    data = resp.json()
                    result.update(self._from_json_blob(data))
                    result["report_fetch_ok"] = True
                    return result
                except ValueError:
                    pass

            text = raw_bytes.decode("utf-8", errors="replace")
            extracted = self._from_html(text, base_url=resp.url)
            result.update(extracted)
            result["report_fetch_ok"] = bool(
                extracted.get("race")
                or extracted.get("ethnicity")
                or extracted.get("gender")
                or extracted.get("height")
                or extracted.get("hair_color")
            )
            if resp.status_code == 200 and len(text) > 500:
                result["report_page_fetched"] = True
            return result
        except requests.RequestException as e:
            result["report_fetch_status"] = f"error:{type(e).__name__}"
            result["report_error"] = str(e)[:300]
            time.sleep(self.delay)
            return result

    def _save_html(
        self,
        content: bytes,
        report_url: str,
        html_dir: Path,
        jurisdiction: str,
        final_url: str = "",
    ) -> Optional[str]:
        """Write report HTML to disk; return path relative to cwd if possible."""
        try:
            jur = re.sub(r"[^A-Za-z0-9_-]", "", (jurisdiction or "UNK").upper())[:12] or "UNK"
            digest = sha1((final_url or report_url).encode("utf-8", errors="replace")).hexdigest()[:16]
            folder = Path(html_dir) / jur
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{digest}.html"

            # Prepend a small comment header with source URL for human validation
            header = (
                f"<!-- archived_from: {html_lib.escape(final_url or report_url)} -->\n"
                f"<!-- archived_at_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -->\n"
            ).encode("utf-8")
            # Avoid double-writing huge files if already present with same size
            if dest.exists() and dest.stat().st_size > 100:
                pass
            else:
                dest.write_bytes(header + content)

            # Prefer project-relative path
            try:
                return str(dest.relative_to(Path.cwd()))
            except ValueError:
                return str(dest)
        except OSError:
            return None

    def _from_html(self, html: str, base_url: str = "") -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        found: Dict[str, Any] = {}

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        for dt in soup.find_all("dt"):
            label = dt.get_text(" ", strip=True).lower().rstrip(":")
            dd = dt.find_next_sibling("dd")
            if dd and label in _LABEL_MAP:
                found.setdefault(_LABEL_MAP[label], dd.get_text(" ", strip=True))

        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                label = cells[0].get_text(" ", strip=True).lower().rstrip(":")
                value = cells[1].get_text(" ", strip=True)
                if label in _LABEL_MAP and value:
                    found.setdefault(_LABEL_MAP[label], value)

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

            label = raw.lower().rstrip(":")
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
                r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|Age|Date of Birth|DOB)\s*[:\-]\s*(.+)$",
                line.strip(),
                flags=re.I,
            )
            if m:
                key = _LABEL_MAP.get(m.group(1).lower())
                if key:
                    found.setdefault(key, m.group(2).strip()[:120])

        for script in BeautifulSoup(html, "html.parser").find_all("script"):
            content = script.string or ""
            if "race" in content.lower() and len(content) < 500_000:
                for m in re.finditer(
                    r'"(race|ethnicity|gender|sex|height|weight|eyeColor|hairColor)"\s*:\s*"([^"]{1,80})"',
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
                    }.get(raw_key)
                    if key:
                        found.setdefault(key, m.group(2))

        if "age" in found:
            try:
                found["age"] = int(re.sub(r"[^\d]", "", str(found["age"])) or 0) or found["age"]
            except (TypeError, ValueError):
                pass

        if base_url:
            found["report_final_url"] = base_url
        return found

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
                resp = self.session.get(url, timeout=min(30, self.timeout), allow_redirects=True)
                if resp.status_code != 200:
                    continue
                ct = (resp.headers.get("Content-Type") or "").lower()
                if "json" in ct:
                    return self._from_json_blob(resp.json())
                if "race" in resp.text.lower():
                    return self._from_html(resp.text, base_url=resp.url)
            except requests.RequestException:
                continue
        return None
