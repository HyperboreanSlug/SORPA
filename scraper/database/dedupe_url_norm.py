from __future__ import annotations

import re

from typing import Any, Dict, List, Optional, Set, Tuple

from scraper.database.dedupe_keys import *  # noqa: F401,F403
from scraper.database.constants import (
    SCHEMA_VERSION,
    DUPLICATE_STRATEGIES,
    DEFAULT_DEDUPE_STRATEGIES,
    _VOLATILE_URL_PARAMS,
    _MERGE_SEP,
    _MERGE_UNION_FIELDS,
    DEFAULT_DB_PATH,
    _OFFENDER_INSERT_COLUMNS,
    _OFFENDER_INSERT_SQL,
    _record_to_insert_tuple,
    _utc_now_iso,
    _escape_like,
)

class DedupeUrlNormMixin:
    @staticmethod
    def normalize_identity_url(url: Optional[str]) -> str:
        """
        Canonical URL for dedupe.

        Strips session/uid/token query params so the same offender page with
        different NSOPW ``uid`` values groups together. Keeps stable ids
        (``Id``, ``ImageId``, path segments).

        Florida FDLE: preserve camelCase ``personId=`` (all-lowercase
        ``personid=`` opens an empty/invalid flyer in the browser).
        """
        raw = (url or "").strip()
        if not raw:
            return ""
        # Multi-jurisdiction merges: normalize each segment separately
        if " | " in raw or (raw.count("http") > 1 and "|" in raw):
            try:
                from scraper.public_links import split_source_urls, resolve_public_source_url

                parts = split_source_urls(raw)
                if parts:
                    # Keep a single canonical public link for identity (prefer FDLE fix)
                    return resolve_public_source_url(raw).lower().replace(
                        "personid=", "personId="
                    )
            except Exception:
                pass
        try:
            p = urlparse(raw)
        except Exception:
            return raw.rstrip("/").lower()
        # Relative paths / non-http: still normalize query if present
        kept = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k and k.lower() not in _VOLATILE_URL_PARAMS
        ]
        # FDLE: force camelCase personId key (JSF is case-sensitive)
        host_l = (p.netloc or "").lower()
        if "fdle.state.fl.us" in host_l:
            fixed_kept = []
            for k, v in kept:
                if k.lower() == "personid":
                    fixed_kept.append(("personId", v))
                else:
                    fixed_kept.append((k, v))
            kept = fixed_kept
        kept.sort(key=lambda kv: (kv[0].lower(), kv[1]))
        host = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/") or "/"
        scheme = (p.scheme or "https").lower()
        if not host and not p.query and not p.path:
            return raw.rstrip("/").lower()
        # urlencode will emit personId as personId; do NOT lowercase query keys for FDLE
        query = urlencode(kept)
        out = urlunparse((scheme, host, path, "", query, ""))
        if "fdle.state.fl.us" in host:
            # Lowercase scheme/host/path only — keep personId casing
            out = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
            # Safety: rewrite any personid= that slipped through
            out = re.sub(r"(?i)([?&])personid=", r"\1personId=", out)
            return out
        # MA SORB: action paths are case-sensitive (lowercase → 404)
        if "sorb.chs.state.ma.us" in host:
            try:
                from scraper.public_links import normalize_ma_sorb_url

                return normalize_ma_sorb_url(
                    f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
                )
            except Exception:
                out = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
                out = re.sub(
                    r"(?i)/viewnsoproffenderdetails\.action",
                    "/viewNsoprOffenderDetails.action",
                    out,
                )
                out = re.sub(
                    r"(?i)/viewnsoproffenderimage\.action",
                    "/viewNsoprOffenderImage.action",
                    out,
                )
                return out
        return out.lower()


    @classmethod
    def stable_external_key(
        cls,
        record: Dict[str, Any],
        *,
        state_hint: Optional[str] = None,
    ) -> str:
        """
        Stable person/listing key for external_id strategy.

        Prefers explicit registry Id query params (e.g. GA ``Id=50604``), then
        normalized URL, then raw external_id text.
        """
        ext = str(record.get("external_id") or "").strip()
        url = str(record.get("source_url") or "").strip()
        state = (
            state_hint
            or record.get("state")
            or record.get("source_state")
            or ""
        )
        state_u = str(state).strip().upper()

        def _id_from(s: str) -> str:
            if not s:
                return ""
            try:
                qs = dict(parse_qsl(urlparse(s).query, keep_blank_values=True))
            except Exception:
                return ""
            for key in (
                "Id", "ID", "id", "OffenderId", "offenderId", "offender_id",
                "personId", "personid", "PersonId",
            ):
                # parse_qsl is case-sensitive; also scan case-insensitively
                if key in qs and str(qs[key]).strip():
                    return str(qs[key]).strip()
            for k, v in qs.items():
                if k.lower() == "personid" and str(v).strip():
                    return str(v).strip()
            # path tail numeric id: /offenders/12345
            try:
                path = urlparse(s).path or ""
            except Exception:
                path = ""
            m = re.search(r"/(\d{3,})/?$", path)
            if m:
                return m.group(1)
            return ""

        for candidate in (ext, url):
            oid = _id_from(candidate)
            if oid:
                return f"{state_u}|reg:{oid}".lower()

        norm = cls.normalize_identity_url(ext or url)
        if norm:
            return f"{state_u}|url:{norm}".lower()
        if ext:
            return f"{state_u}|raw:{ext.casefold()}"
        return ""


