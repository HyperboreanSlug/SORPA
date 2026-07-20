"""Direct download scraper — bulk CSV/JSON files from public registry sources."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

import requests

from .base import BaseScraper
from .normalize import normalize_records

# Browser UA for hosts that 403 custom bot strings (e.g. iCrimewatch AZ CSV).
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class DirectDownloadScraper(BaseScraper):
    """Download and parse published bulk files (CSV/JSON)."""

    def get_direct_download_urls(self) -> List[str]:
        from ..config import get_registry_by_abbr

        registry = get_registry_by_abbr(self.state_abbr)
        return list(registry.direct_downloads) if registry else []

    def scrape(self) -> List[Dict[str, Any]]:
        urls = self.get_direct_download_urls()
        if not urls:
            print(f"  [{self.state_abbr}] No direct download URLs configured.")
            return []

        records: List[Dict[str, Any]] = []
        errors: List[str] = []

        for url in urls:
            try:
                batch = self._download_and_parse(url)
                if batch:
                    records.extend(batch)
                    print(f"  [{self.state_abbr}] Parsed {len(batch)} records from {url}")
                else:
                    print(f"  [{self.state_abbr}] No records parsed from {url}")
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                msg = f"HTTP {status} for {url}"
                if status == 403:
                    msg += " (blocked — site may require a browser download)"
                errors.append(msg)
                print(f"  [{self.state_abbr}] {msg}")
            except requests.RequestException as e:
                msg = f"Request failed for {url}: {e}"
                errors.append(msg)
                print(f"  [{self.state_abbr}] {msg}")
            except Exception as e:
                msg = f"Error for {url}: {e}"
                errors.append(msg)
                print(f"  [{self.state_abbr}] {msg}")

        if not records and errors:
            print(f"  [{self.state_abbr}] All direct downloads failed.")

        return normalize_records(records, state=self.state_abbr)

    @staticmethod
    def _decode_bytes(content: bytes) -> str:
        """Decode bulk file bytes, detecting UTF-8 vs legacy cp1252/latin-1.

        A blind utf-8-sig decode with errors='replace' injects U+FFFD into
        latin-1/cp1252 names (e.g. Jose -> Jos\\ufffd), corrupting identity.
        """
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        for enc in ("cp1252", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="replace")

    def _download_and_parse(self, url: str) -> List[Dict[str, Any]]:
        content, content_type = self._fetch_bytes(url)
        content_type = (content_type or "").lower()
        # Detect HTML block pages even on 200
        body_start = content[:200].lstrip().lower()
        if body_start.startswith(b"<!doctype") or body_start.startswith(b"<html"):
            raise requests.HTTPError(
                "Received HTML instead of data file (likely blocked)",
                response=None,
            )

        url_lower = url.lower()
        text = self._decode_bytes(content)

        if "json" in content_type or url_lower.endswith(".json"):
            return self._parse_json(json.loads(text))
        if (
            "csv" in content_type
            or url_lower.endswith(".csv")
            or "text/plain" in content_type
            or "octet-stream" in content_type
            or "text/" in content_type
        ):
            return self._parse_csv_text(text)

        try:
            return self._parse_json(json.loads(text))
        except json.JSONDecodeError:
            return self._parse_csv_text(text)

    def _fetch_bytes(self, url: str) -> tuple[bytes, str]:
        """
        Fetch bulk file bytes. Prefer curl_cffi Chrome TLS for bot-gated hosts
        (iCrimewatch / sheriffalerts AZ CSV returns 403 to plain requests).
        """
        referer = self._referer_for(url)
        headers = {
            "Accept": "text/csv,application/json,application/octet-stream,text/plain,*/*;q=0.8",
            "Referer": referer,
            "Accept-Language": "en-US,en;q=0.9",
        }
        host = (urlparse(url).netloc or "").lower()
        bot_gated = any(
            h in host
            for h in ("icrimewatch.net", "sheriffalerts.com", "communitynotification.com")
        )

        # 1) curl_cffi Chrome impersonation (bypasses many 403 bot walls)
        try:
            from curl_cffi import requests as creq  # type: ignore

            s = creq.Session(impersonate="chrome")
            # Warm homepage so cookies / bot score look organic
            try:
                home = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
                s.get(home, timeout=30, allow_redirects=True)
            except Exception:
                pass
            if "icrimewatch" in host or "sheriffalerts" in host:
                try:
                    s.get(
                        "https://www.icrimewatch.net/",
                        timeout=30,
                        allow_redirects=True,
                    )
                except Exception:
                    pass
            resp = s.get(
                url,
                headers=headers,
                timeout=90,
                allow_redirects=True,
            )
            if resp.status_code == 200 and resp.content:
                ct = resp.headers.get("Content-Type") or ""
                body = resp.content[:80].lstrip().lower()
                if not (body.startswith(b"<!doctype") or body.startswith(b"<html")):
                    return resp.content, ct
            if resp.status_code == 403 and bot_gated:
                print(
                    f"  [{self.state_abbr}] curl_cffi got {resp.status_code}; "
                    "retrying alternate hosts…"
                )
            elif resp.status_code >= 400:
                # Fall through to requests path / raise
                pass
            else:
                # 200 but HTML block page — try alts
                pass
        except Exception as e:
            print(f"  [{self.state_abbr}] curl_cffi fetch note: {e}")

        # 2) Alternate URL mirrors for AZ / OffenderWatch
        alts = self._alternate_urls(url)
        for alt in alts:
            try:
                from curl_cffi import requests as creq  # type: ignore

                s = creq.Session(impersonate="chrome")
                resp = s.get(
                    alt,
                    headers={**headers, "Referer": self._referer_for(alt)},
                    timeout=90,
                    allow_redirects=True,
                )
                if resp.status_code == 200 and resp.content:
                    body = resp.content[:80].lstrip().lower()
                    if not (body.startswith(b"<!doctype") or body.startswith(b"<html")):
                        print(f"  [{self.state_abbr}] Fetched via {alt}")
                        return resp.content, resp.headers.get("Content-Type") or ""
            except Exception:
                continue

        # 3) Plain requests (may 403 on bot-gated hosts)
        resp = self._get(url, headers=headers)
        return resp.content, resp.headers.get("Content-Type") or ""

    def _alternate_urls(self, url: str) -> List[str]:
        """Known mirrors for OffenderWatch bulk files."""
        alts: List[str] = []
        lower = url.lower()
        if "az_offenders.csv" in lower or self.state_abbr == "AZ":
            alts.extend(
                [
                    "https://www.icrimewatch.net/az_offenders.csv",
                    "https://icrimewatch.net/az_offenders.csv",
                    "https://sheriffalerts.com/az_offenders.csv",
                    "http://www.icrimewatch.net/az_offenders.csv",
                ]
            )
        # de-dupe, skip original
        seen = {url.rstrip("/").lower()}
        out: List[str] = []
        for a in alts:
            k = a.rstrip("/").lower()
            if k not in seen:
                seen.add(k)
                out.append(a)
        return out

    def _referer_for(self, url: str) -> str:
        from ..config import get_registry_by_abbr

        registry = get_registry_by_abbr(self.state_abbr)
        if registry and registry.registry_url:
            return registry.registry_url
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "icrimewatch" in host or "sheriffalerts" in host:
            return "https://www.icrimewatch.net/"
        return f"{parsed.scheme}://{parsed.netloc}/"

    def _parse_json(self, data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            for key in ("results", "data", "records", "offenders", "items", "features"):
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    # ArcGIS-style features
                    if items and isinstance(items[0], dict) and "attributes" in items[0]:
                        return [f["attributes"] for f in items if "attributes" in f]
                    return [r for r in items if isinstance(r, dict)]
            return [data]
        return []

    def _parse_csv_text(self, text: str) -> List[Dict[str, Any]]:
        # utf-8-sig already applied by caller; DictReader handles headers
        reader = csv.DictReader(io.StringIO(text))
        records: List[Dict[str, Any]] = []
        for row in reader:
            cleaned: Dict[str, Any] = {}
            for k, v in row.items():
                if k is None:
                    continue
                key = str(k).replace("\ufeff", "").strip()
                if not key:
                    continue
                cleaned[key] = str(v).strip() if v is not None else None
            if any(cleaned.values()):
                records.append(cleaned)
        return records

    def scrape_to_file(
        self, output_dir: Path, filename: Optional[str] = None
    ) -> List[Path]:
        """Download raw files without re-parsing."""
        urls = self.get_direct_download_urls()
        paths: List[Path] = []
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, url in enumerate(urls):
            try:
                content, _ct = self._fetch_bytes(url)
                raw_name = Path(unquote(urlparse(url).path)).name
                if filename and len(urls) == 1:
                    fname = filename
                elif raw_name and raw_name not in (".", "/"):
                    fname = raw_name
                else:
                    fname = f"{self.state_abbr.lower()}_data_{i + 1}.csv"
                dest = output_dir / fname
                dest.write_bytes(content)
                paths.append(dest)
            except Exception as e:
                print(f"  [{self.state_abbr}] Error downloading {url}: {e}")

        return paths
