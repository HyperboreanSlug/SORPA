"""Full identity audit: HTML name/DOB, links, photos vs DB record.

NUCLEAR-level: wrong-person attach is a security threat. Prefer clear over keep.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scraper.database.identity import dobs_compatible, normalize_dob
from scraper.public_links import extract_fdle_person_id, split_source_urls
from scraper.reports.identity_gate import (
    extract_person_name_from_html,
    record_name_matches_html,
    strip_wrong_person_html,
)

# Cache parsed HTML fields by absolute path string
_HTML_CACHE: Dict[str, Dict[str, Optional[str]]] = {}

_DOB_PATTERNS = (
    re.compile(
        r"(?i)Date\s+of\s+Birth\s*:?\s*</span>\s*</div>\s*"
        r"<div[^>]*>\s*<span[^>]*>\s*([^<]{4,40})\s*<",
    ),
    re.compile(
        r"(?i)(?:Date\s+of\s+Birth|DOB|Birth\s+Date)\s*[:\-]\s*"
        r"(\d{1,2}[/=-]\d{1,2}[/=-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    ),
    re.compile(
        r"(?i)(?:Date\s+of\s+Birth|DOB)\s*[:\-]\s*</[^>]+>\s*"
        r"<[^>]+>\s*(\d{1,2}[/=-]\d{1,2}[/=-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    ),
)


@dataclass
class IdentityFinding:
    offender_id: int
    full_name: str
    severity: str  # nuclear | high | medium | info
    code: str
    detail: str
    html_path: str = ""
    html_name: str = ""
    html_dob: str = ""
    record_dob: str = ""
    source_url: str = ""


@dataclass
class AuditSummary:
    scanned: int = 0
    with_html: int = 0
    with_photo: int = 0
    name_ok: int = 0
    name_mismatch: int = 0
    name_unparsed: int = 0
    dob_ok: int = 0
    dob_mismatch: int = 0
    dob_unparsed: int = 0
    photo_orphan: int = 0
    photo_ok: int = 0
    fl_link_suspect: int = 0
    repaired: int = 0
    findings: List[IdentityFinding] = field(default_factory=list)

    def add(self, f: IdentityFinding) -> None:
        self.findings.append(f)


def extract_dob_from_html(html: str) -> Optional[str]:
    if not html:
        return None
    for pat in _DOB_PATTERNS:
        m = pat.search(html)
        if m:
            raw = re.sub(r"\s+", " ", m.group(1)).strip()
            # Strip age suffixes
            raw = re.split(r"\s+Age\s*:", raw, maxsplit=1, flags=re.I)[0].strip()
            if normalize_dob(raw):
                return raw
    return None


def parse_html_identity(path: str) -> Dict[str, Optional[str]]:
    """Return {name, dob, exists} cached by path."""
    key = str(Path(path).resolve()) if path else ""
    if key in _HTML_CACHE:
        return _HTML_CACHE[key]
    p = Path(path) if path else None
    out: Dict[str, Optional[str]] = {
        "name": None,
        "dob": None,
        "exists": "0",
    }
    if not p or not p.is_file():
        _HTML_CACHE[key or path or ""] = out
        return out
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _HTML_CACHE[key] = out
        return out
    out["exists"] = "1"
    out["name"] = extract_person_name_from_html(text)
    out["dob"] = extract_dob_from_html(text)
    _HTML_CACHE[key] = out
    return out


def photo_belongs_to_html(photo_path: str, html_path: str) -> Optional[bool]:
    """
    True if photo is under the HTML asset tree or same state photos folder.
    False if photo is clearly from another report hash. None if inconclusive.
    """
    photo = (photo_path or "").replace("\\", "/").strip()
    html = (html_path or "").replace("\\", "/").strip()
    if not photo:
        return None
    if not Path(photo).is_file() and not Path(photo_path or "").is_file():
        return False  # missing file
    if not html:
        return None
    stem = Path(html).stem
    # FDLE archive layout: FL/<hash>.html + FL/<hash>_assets/ or FL/photos/
    if stem and stem in photo:
        return True
    # Same jurisdiction folder
    try:
        h_parent = Path(html).parent.name.upper()
        # photo .../report_pages/FL/photos/x.jpg
        parts = photo.upper().split("/")
        if "REPORT_PAGES" in parts:
            i = parts.index("REPORT_PAGES")
            if i + 1 < len(parts) and parts[i + 1] == h_parent:
                return True
    except Exception:
        pass
    return None


def audit_record(rec: Dict[str, Any]) -> List[IdentityFinding]:
    """Audit one offender; does not mutate."""
    findings: List[IdentityFinding] = []
    oid = int(rec.get("id") or 0)
    name = str(rec.get("full_name") or "").strip()
    html_path = str(rec.get("report_html_path") or "").strip()
    photo = str(rec.get("photo_path") or "").strip()
    url = str(rec.get("source_url") or "").strip()
    rec_dob = str(rec.get("date_of_birth") or "").strip()

    def _f(
        severity: str,
        code: str,
        detail: str,
        **extra: str,
    ) -> None:
        findings.append(
            IdentityFinding(
                offender_id=oid,
                full_name=name,
                severity=severity,
                code=code,
                detail=detail,
                html_path=html_path,
                record_dob=rec_dob,
                source_url=url[:120],
                **extra,
            )
        )

    # --- HTML present ---
    if html_path:
        parsed = parse_html_identity(html_path)
        if parsed.get("exists") != "1":
            _f("high", "html_missing_file", f"report_html_path not on disk: {html_path}")
        else:
            hn = parsed.get("name") or ""
            hd = parsed.get("dob") or ""
            if not hn:
                _f(
                    "medium",
                    "html_name_unparsed",
                    "Could not extract person name from HTML",
                    html_name="",
                    html_dob=hd,
                )
            elif not record_name_matches_html(rec, hn):
                # Only NUCLEAR when extracted HTML text is a real person name.
                # Chrome/place strings ("Not Available", "Kansas City") are
                # unparsed/medium — not proof of wrong person.
                from scraper.reports.identity_gate import _looks_like_person_name

                rec_last = str(rec.get("last_name") or "").strip()
                rec_full = str(rec.get("full_name") or "").strip()
                if not rec_last and not rec_full:
                    # Record has no name to compare — cannot confirm a mismatch.
                    _f(
                        "medium",
                        "html_name_unparsed",
                        f"Record has no name to compare against HTML {hn!r}",
                        html_name=hn,
                        html_dob=hd,
                    )
                elif _looks_like_person_name(hn):
                    _f(
                        "nuclear",
                        "html_name_mismatch",
                        f"HTML name {hn!r} ≠ record {name!r}",
                        html_name=hn,
                        html_dob=hd,
                    )
                else:
                    _f(
                        "medium",
                        "html_name_unparsed",
                        f"Could not extract person name from HTML (got {hn!r})",
                        html_name=hn,
                        html_dob=hd,
                    )
            else:
                # name OK — check DOB when both present
                if rec_dob and hd:
                    dc = dobs_compatible(rec_dob, hd)
                    if dc is False:
                        _f(
                            "nuclear",
                            "html_dob_mismatch",
                            f"HTML DOB {hd!r} ≠ record DOB {rec_dob!r}",
                            html_name=hn,
                            html_dob=hd,
                        )
                    elif dc is True:
                        pass  # ok
                elif rec_dob and not hd:
                    _f(
                        "info",
                        "html_dob_unparsed",
                        "Record has DOB but HTML DOB not extracted",
                        html_name=hn,
                    )

    # --- Photo ---
    if photo:
        p_ok = Path(photo)
        if not p_ok.is_file():
            # try relative to cwd
            if not Path(photo.replace("\\", "/")).is_file():
                _f("high", "photo_missing_file", f"photo_path missing: {photo}")
            else:
                bel = photo_belongs_to_html(photo, html_path)
                if bel is False and html_path:
                    _f(
                        "nuclear",
                        "photo_html_mismatch",
                        "Photo path does not belong to this report HTML tree",
                    )
        else:
            bel = photo_belongs_to_html(photo, html_path)
            if bel is False and html_path:
                _f(
                    "nuclear",
                    "photo_html_mismatch",
                    "Photo path does not belong to this report HTML tree",
                )

    # --- FL synthetic personId risk: URL personId + HTML name mismatch already nuclear ---
    for u in split_source_urls(url):
        pid = extract_fdle_person_id(u)
        if not pid:
            continue
        # If we have HTML for this row and name mismatches, link is poison
        if html_path:
            parsed = parse_html_identity(html_path)
            hn = parsed.get("name")
            from scraper.reports.identity_gate import _looks_like_person_name

            if (
                hn
                and _looks_like_person_name(hn)
                and not record_name_matches_html(rec, hn)
            ):
                _f(
                    "nuclear",
                    "fl_link_wrong_person",
                    f"FDLE personId={pid} flyer is {hn!r}, record is {name!r}",
                    html_name=hn or "",
                )

    return findings


def repair_nuclear_findings(
    rec: Dict[str, Any],
    findings: List[IdentityFinding],
) -> bool:
    """Strip wrong-person HTML/photo/links for nuclear findings. Mutates rec."""
    nuclear = [f for f in findings if f.severity == "nuclear"]
    if not nuclear:
        return False
    changed = strip_wrong_person_html(rec, reason="audit_nuclear")
    codes = {f.code for f in nuclear}
    if codes & {"html_name_mismatch", "html_dob_mismatch", "fl_link_wrong_person"}:
        # Clear poisoned FL flyer segments (PERSON_NBR ≠ personId)
        url = str(rec.get("source_url") or "")
        if "fdle" in url.lower() and "personid=" in url.lower():
            parts = [p.strip() for p in url.split(" | ") if p.strip()]
            kept = []
            dropped_pids: List[str] = []
            for p in parts:
                if "fdle" in p.lower() and extract_fdle_person_id(p):
                    dropped_pids.append(extract_fdle_person_id(p) or "")
                    changed = True
                    continue
                kept.append(p)
            rec["source_url"] = " | ".join(kept) if kept else None
            ext = str(rec.get("external_id") or "").strip()
            if ext and ext in dropped_pids:
                rec["external_id"] = None
                changed = True
        if rec.get("report_html_path"):
            # strip_wrong_person_html should have cleared; force if name mismatch
            rec["report_html_path"] = None
            changed = True
        if any(
            c in codes
            for c in (
                "photo_html_mismatch",
                "html_name_mismatch",
                "html_dob_mismatch",
                "fl_link_wrong_person",
            )
        ):
            if rec.get("photo_path"):
                rec["photo_path"] = None
                changed = True
            if rec.get("photo_url"):
                rec["photo_url"] = None
                changed = True
    # Photo-only nuclear mismatch: clear the wrong-person photo even when the
    # HTML name/DOB/link are fine (the block above only runs for those codes).
    if "photo_html_mismatch" in codes:
        if rec.get("photo_path"):
            rec["photo_path"] = None
            changed = True
        if rec.get("photo_url"):
            rec["photo_url"] = None
            changed = True
    return changed


def run_full_audit(
    db: Any,
    *,
    repair: bool = False,
    limit: int = 0,
    only_html: bool = True,
    progress_every: int = 2000,
    log: Optional[Any] = None,
) -> AuditSummary:
    """Scan offenders with HTML/photo/FL links; optionally repair nuclear hits."""
    def _log(msg: str) -> None:
        if log:
            log(msg)
        else:
            print(msg, flush=True)

    summary = AuditSummary()
    sql = "SELECT * FROM offenders WHERE 1=1"
    if only_html:
        sql = (
            "SELECT * FROM offenders WHERE "
            "(report_html_path IS NOT NULL AND TRIM(report_html_path) != '') "
            "OR (photo_path IS NOT NULL AND TRIM(photo_path) != '') "
            "OR (source_url LIKE '%personId=%' OR source_url LIKE '%personid=%')"
        )
    sql += " ORDER BY id ASC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"

    rows = db._conn.execute(sql).fetchall()
    _log(f"Identity audit: {len(rows):,} rows (repair={repair})")

    for i, row in enumerate(rows, 1):
        rec = dict(row)
        summary.scanned += 1
        if rec.get("report_html_path"):
            summary.with_html += 1
        if rec.get("photo_path"):
            summary.with_photo += 1

        findings = audit_record(rec)
        for f in findings:
            summary.add(f)
            if f.code == "html_name_mismatch":
                summary.name_mismatch += 1
            elif f.code == "html_name_unparsed":
                summary.name_unparsed += 1
            elif f.code == "html_dob_mismatch":
                summary.dob_mismatch += 1
            elif f.code == "html_dob_unparsed":
                summary.dob_unparsed += 1
            elif f.code == "photo_missing_file":
                summary.photo_orphan += 1
            elif f.code == "photo_html_mismatch":
                summary.photo_orphan += 1
            elif f.code == "fl_link_wrong_person":
                summary.fl_link_suspect += 1

        # Count name/dob OK when HTML exists and no nuclear name/dob issue
        if rec.get("report_html_path"):
            codes = {f.code for f in findings}
            if "html_name_mismatch" not in codes and "html_name_unparsed" not in codes:
                if parse_html_identity(str(rec.get("report_html_path") or "")).get("name"):
                    summary.name_ok += 1
            if "html_dob_mismatch" not in codes:
                hd = parse_html_identity(str(rec.get("report_html_path") or "")).get("dob")
                rd = rec.get("date_of_birth")
                if hd and rd and dobs_compatible(rd, hd) is True:
                    summary.dob_ok += 1
            if rec.get("photo_path") and "photo_missing_file" not in codes:
                summary.photo_ok += 1

        if repair and findings:
            if repair_nuclear_findings(rec, findings):
                summary.repaired += 1
                patch = {
                    k: rec.get(k)
                    for k in (
                        "race",
                        "flags",
                        "sources_json",
                        "report_html_path",
                        "photo_path",
                        "photo_url",
                        "source_url",
                        "external_id",
                    )
                }
                db.update_offender(int(rec["id"]), patch)

        if progress_every and i % progress_every == 0:
            _log(
                f"  … {i:,}/{len(rows):,} scanned · "
                f"name_mismatch={summary.name_mismatch} "
                f"dob_mismatch={summary.dob_mismatch} "
                f"repaired={summary.repaired}"
            )

    _log(
        f"Audit done: scanned={summary.scanned} html={summary.with_html} "
        f"name_ok={summary.name_ok} name_mismatch={summary.name_mismatch} "
        f"dob_ok={summary.dob_ok} dob_mismatch={summary.dob_mismatch} "
        f"photo_issues={summary.photo_orphan} fl_wrong={summary.fl_link_suspect} "
        f"repaired={summary.repaired}"
    )
    return summary
