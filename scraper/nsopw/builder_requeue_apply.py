"""Rank, prepare, and apply patches for incomplete report requeue."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

_STATE_TOKEN_RE = re.compile(r"[A-Za-z]{2}")


class BuilderRequeueApplyMixin:
    @staticmethod
    def _requeue_state_tokens(rec: Dict[str, Any]) -> List[str]:
        """2-letter state codes from state / source_state (handles ``FL | GA``)."""
        raw = f"{rec.get('state') or ''} {rec.get('source_state') or ''}".upper()
        out: List[str] = []
        for tok in _STATE_TOKEN_RE.findall(raw):
            t = tok.upper()
            if t not in out:
                out.append(t)
        return out

    @classmethod
    def _requeue_rank_key(cls, rec: Dict[str, Any]) -> Tuple[int, int, int, int]:
        """
        Prefer same-registry hosts, skip captcha walls, fill photos first.

        Multi-state rows like ``FL | GA`` must not require the full string
        ``fl | ga`` to appear in the URL (that broke FDLE prioritization).
        """
        url = (rec.get("source_url") or "").lower()
        tokens = cls._requeue_state_tokens(rec)
        jur = ""
        try:
            from scraper.database.sources import jurisdiction_from_url

            jur = (jurisdiction_from_url(url) or "").upper()
        except Exception:
            jur = ""
        if jur and jur in tokens:
            same = 0
        elif any(t.lower() in url for t in tokens):
            same = 0
        else:
            same = 1
        captcha = 1 if "captcha" in url else 0
        need_photo = 0 if not str(rec.get("photo_path") or "").strip() else 1
        return (same, captcha, need_photo, int(rec.get("id") or 0))

    def _requeue_prepare(
        self, rec: Dict[str, Any]
    ) -> Optional[tuple]:
        url = (rec.get("source_url") or "").strip()
        if not url:
            return None
        rid = rec.get("id")
        # Prefer registry host from URL (FDLE → FL) over multi-state residence
        # labels like "FL | GA" which otherwise create FLGA/ archive folders.
        st = ""
        try:
            from scraper.database.sources import jurisdiction_from_url

            st = (jurisdiction_from_url(url) or "").upper()
        except Exception:
            st = ""
        if not st:
            tokens = self._requeue_state_tokens(rec)
            st = tokens[0] if tokens else "UNK"
        name = (
            (rec.get("full_name") or "").strip()
            or f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}".strip()
            or f"id={rid}"
        )
        fetch_url = self._primary_fetch_url(url, st)
        if not fetch_url:
            return None
        return rid, st, name, fetch_url

    def _requeue_apply_patch(
        self,
        rec: Dict[str, Any],
        record: Dict[str, Any],
        demo: Dict[str, Any],
        summary: Dict[str, Any],
        *,
        log: Callable[[str], None],
        on_update: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        rid = rec.get("id")
        patch: Dict[str, Any] = {}
        for key in (
            "race", "ethnicity", "gender", "height", "weight",
            "eye_color", "hair_color", "crime", "offense_type",
            "offense_description", "report_html_path", "photo_path", "photo_url",
            "county", "city", "address", "risk_level",
            "sources_json", "flags", "raw_data_json",
        ):
            new_v = record.get(key)
            old_v = rec.get(key)
            if new_v is None or new_v == "":
                continue
            if key in ("sources_json", "flags", "race", "raw_data_json"):
                if new_v != old_v:
                    patch[key] = new_v
                continue
            if new_v and (not old_v or (key in ("crime",) and new_v != old_v)):
                if not old_v or key in (
                    "ethnicity", "crime", "photo_path", "report_html_path"
                ):
                    if new_v != old_v:
                        patch[key] = new_v

        if patch and rid is not None:
            try:
                from scraper.database.db_retry import retry_on_db_lock

                lock = getattr(self, "_db_write_lock", None)

                def _write() -> bool:
                    if lock is not None:
                        with lock:
                            return self.db.update_offender(int(rid), patch)
                    return self.db.update_offender(int(rid), patch)

                ok = retry_on_db_lock(
                    _write,
                    attempts=8,
                    base_delay=0.5,
                    max_delay=10.0,
                    log=log,
                    what=f"requeue apply id={rid}",
                )
            except Exception as e:
                summary["errors"] += 1
                log(
                    f"    ↳ DB update failed id={rid} after retries: {e} "
                    "(continuing with next record)"
                )
                return
            if ok:
                summary["updated"] += 1
                merged = dict(rec)
                merged.update(patch)
                if merged.get("race"):
                    summary["with_race"] += 1
                if (
                    merged.get("crime")
                    or merged.get("offense_description")
                    or merged.get("offense_type")
                ):
                    summary["with_crime"] += 1
                if merged.get("photo_path"):
                    summary["with_photo"] += 1
                if merged.get("report_html_path"):
                    summary["with_html"] += 1
                gained: List[str] = []
                if patch.get("race"):
                    gained.append(f"race={patch.get('race')}")
                if patch.get("crime"):
                    gained.append(f"crime={(patch.get('crime') or '')[:40]}")
                elif patch.get("offense_description"):
                    gained.append(
                        f"crime={(patch.get('offense_description') or '')[:40]}"
                    )
                if patch.get("photo_path"):
                    gained.append("photo")
                if patch.get("report_html_path"):
                    gained.append("html")
                meta_only = not gained and any(
                    k in patch for k in ("sources_json", "flags", "raw_data_json")
                )
                if gained:
                    detail = " ".join(gained)
                elif meta_only:
                    detail = "sources/flags only (fields already set)"
                else:
                    detail = "metadata"
                log(f"    ↳ updated id={rid} +{detail}")
                if on_update:
                    try:
                        on_update(merged)
                    except Exception:
                        pass
            else:
                log(f"    ↳ no DB change for id={rid}")
        else:
            id_reason = ""
            try:
                import json as _json

                raw = _json.loads(record.get("raw_data_json") or "{}")
                enr = raw.get("report_enrichment") or {}
                if enr.get("identity_ok") is False:
                    id_reason = str(enr.get("identity_reason") or "identity")
            except Exception:
                id_reason = ""
            status = demo.get("report_fetch_status")
            block = demo.get("report_block_reason") or id_reason or ""
            has_demo = bool(
                demo.get("race")
                or demo.get("crime")
                or demo.get("photo_path")
                or demo.get("offense_description")
            )
            if id_reason:
                why = f"identity blocked ({id_reason})"
            elif block:
                why = f"blocked:{block}"
            elif not has_demo:
                why = "empty page / no parseable race·crime·photo"
            else:
                why = "fields already present or not applied"
            log(f"    ↳ no new fields (status={status} · {why})")

    @staticmethod
    def _requeue_progress(
        on_progress: Optional[Callable[..., None]],
        done: int,
        total: int,
        *,
        updated: int = 0,
    ) -> None:
        if not on_progress:
            return
        try:
            on_progress(done, total or 1, updated=updated)
        except TypeError:
            try:
                on_progress(done, total or 1)
            except Exception:
                pass
        except Exception:
            pass
