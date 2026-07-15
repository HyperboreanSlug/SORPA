"""NSOPW Enrich — incomplete list load/fill for selected state."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


class NsopwEnrichListMixin:
    def _nsopw_enrich_on_state_change(self, *_args) -> None:
        if getattr(self, "_nsopw_enrich_busy", False):
            return
        self._nsopw_enrich_reload_list()

    def _nsopw_enrich_on_select(self, _event=None) -> None:
        tree = getattr(self, "nsopw_enrich_tree", None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        rec = (getattr(self, "_nsopw_enrich_records", {}) or {}).get(sel[0])
        if not rec:
            return
        drawer = getattr(self, "nsopw_detail", None) or getattr(
            self, "nsopw_enrich_detail", None
        )
        if drawer is not None and hasattr(self, "_fill_detail_drawer"):
            self._fill_detail_drawer(drawer, rec)

    def _nsopw_enrich_reload_list(self) -> None:
        """Load incomplete rows for the selected state into the tree."""
        if not hasattr(self, "nsopw_enrich_tree"):
            return
        if getattr(self, "_nsopw_enrich_busy", False):
            return
        db_path = str(
            getattr(self, "nsopw_db_path", None)
            or getattr(self, "db_path", None)
            or "data/offenders.db"
        )
        state = (
            self._nsopw_selected_state_code()
            if hasattr(self, "_nsopw_selected_state_code")
            else None
        )
        need_race = bool(
            getattr(self, "nsopw_enrich_need_race", None)
            and self.nsopw_enrich_need_race.get()
        )
        need_crime = bool(
            getattr(self, "nsopw_enrich_need_crime", None)
            and self.nsopw_enrich_need_crime.get()
        )
        need_photo = bool(
            getattr(self, "nsopw_enrich_need_photo", None)
            and self.nsopw_enrich_need_photo.get()
        )
        need_html = bool(
            getattr(self, "nsopw_enrich_need_html", None)
            and self.nsopw_enrich_need_html.get()
        )
        if not any((need_race, need_crime, need_photo, need_html)):
            need_race = need_crime = need_photo = True

        try:
            self.nsopw_enrich_status.configure(
                text=f"Loading incomplete for {state or 'all states'}…"
            )
        except Exception:
            pass

        def work():
            from scraper.database import Database

            db = Database(db_path)
            try:
                # Preview list stays capped; total uses unlimited query so
                # Limit 0 enrich matches the real pending count.
                preview = db.find_incomplete_reports(
                    need_race=need_race,
                    need_crime=need_crime,
                    need_photo=need_photo,
                    need_html=need_html,
                    require_url=True,
                    limit=500,
                    state=state,
                )
                if len(preview) < 500:
                    total = len(preview)
                else:
                    total = db.count_incomplete_reports(
                        need_race=need_race,
                        need_crime=need_crime,
                        need_photo=need_photo,
                        need_html=need_html,
                        require_url=True,
                        state=state,
                    )
                return {
                    "rows": [dict(r) for r in preview],
                    "total": int(total),
                }
            finally:
                db.close()

        def done(result=None, error=None):
            if error is not None:
                try:
                    self.nsopw_enrich_status.configure(text=f"Load error: {error}")
                except Exception:
                    pass
                return
            payload = result or {}
            rows = payload.get("rows") if isinstance(payload, dict) else (result or [])
            rows = rows or []
            total = (
                int(payload.get("total") or len(rows))
                if isinstance(payload, dict)
                else len(rows)
            )
            self._nsopw_enrich_fill_tree(rows)
            try:
                listed = len(rows)
                if total > listed:
                    stats = (
                        f"Incomplete: {total:,} pending · "
                        f"list shows {listed:,} · state={state or 'all'}"
                    )
                    status = (
                        f"Ready · {total:,} incomplete for {state or 'all'} "
                        f"(list preview {listed:,}; Limit 0 processes all)"
                    )
                else:
                    stats = (
                        f"Incomplete: {total:,} pending · state={state or 'all'}"
                    )
                    status = (
                        f"Ready · {total:,} incomplete with URL for "
                        f"{state or 'all'}"
                    )
                self.nsopw_enrich_stats_label.configure(text=stats)
                self.nsopw_enrich_status.configure(text=status)
            except Exception:
                pass

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="nsopw-enrich-list")
        else:
            try:
                done(result=work())
            except Exception as e:
                done(error=e)

    def _nsopw_enrich_fill_tree(self, rows: List[Dict[str, Any]]) -> None:
        tree = getattr(self, "nsopw_enrich_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self._nsopw_enrich_records = {}
        for rec in rows[:500]:
            name = (
                " ".join(
                    p
                    for p in (
                        rec.get("first_name") or "",
                        rec.get("middle_name") or "",
                        rec.get("last_name") or "",
                    )
                    if str(p).strip()
                )
                or (rec.get("full_name") or "—")
            )
            photo = (rec.get("photo_path") or "").strip()
            photo_mark = "yes" if photo and Path(photo).is_file() else "no"
            url = (rec.get("source_url") or "").strip()
            if " | " in url:
                url = url.split(" | ", 1)[0].strip()
            iid = tree.insert(
                "",
                "end",
                values=(
                    name,
                    (rec.get("state") or rec.get("source_state") or "—")[:12],
                    (rec.get("race") or "—")[:14],
                    (rec.get("crime") or rec.get("offense_description") or "—")[:40],
                    photo_mark,
                    url[:80],
                ),
            )
            self._nsopw_enrich_records[iid] = rec
