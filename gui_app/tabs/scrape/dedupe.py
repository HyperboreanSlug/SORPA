"""ScrapeDedupeMixin — duplicate scan off the UI thread."""
from __future__ import annotations

from tkinter import messagebox


class ScrapeDedupeMixin:
    def _check_duplicates(self) -> None:
        """Scan DB for duplicate groups (~10–20s on large DBs)."""
        if getattr(self, "_dup_check_running", False):
            messagebox.showinfo("Duplicate check", "Already scanning…")
            return
        self._dup_check_running = True
        if hasattr(self, "integrity_status"):
            try:
                self.integrity_status.configure(text="Scanning duplicates…")
            except Exception:
                pass
        db_path = str(getattr(self, "db_path", None) or "data/offenders.db")

        def work():
            from scraper.database import DEFAULT_DEDUPE_STRATEGIES, Database

            strats = list(DEFAULT_DEDUPE_STRATEGIES)
            db = Database(db_path)
            try:
                summary = db.count_duplicates(strats)
                samples = db.find_duplicate_groups("source_url", limit_groups=8)
                return {"summary": summary, "samples": samples}
            finally:
                db.close()

        def done(result=None, error=None):
            self._dup_check_running = False
            if error or not result or not result.get("summary"):
                messagebox.showerror(
                    "Duplicate check failed", str(error or "no result")
                )
                return
            summary = result["summary"]
            samples = result.get("samples") or []
            lines = [
                f"Total offenders: {summary['total_offenders']:,}",
                "",
                "By match key (safe extras are auto-removable; "
                "portal/CAPTCHA clusters are not):",
            ]
            for s, info in (summary.get("by_strategy") or {}).items():
                lines.append(
                    f"  · {s}: {info.get('safe_extra_rows', 0):,} safe removable "
                    f"/ {info.get('extra_rows', 0):,} raw extra "
                    f"({info.get('unsafe_groups', 0)} unsafe groups)"
                )
            safe_samples = [g for g in samples if g.get("safe", True)][:5]
            unsafe_samples = [g for g in samples if not g.get("safe", True)][:3]
            if safe_samples:
                lines.append("")
                lines.append("Sample safe source_url duplicates:")
                for g in safe_samples:
                    lines.append(
                        f"  · keep #{g['keep_id']} {g['keep_preview']} "
                        f"(×{g['count']}) remove {g['remove_ids'][:4]}"
                    )
            if unsafe_samples:
                lines.append("")
                lines.append("Skipped portal/CAPTCHA URL clusters (not removed):")
                for g in unsafe_samples:
                    lines.append(
                        f"  · ×{g['count']}  {str(g.get('key') or '')[:60]}"
                    )
            lines.append("")
            lines.append(
                "Use Remove duplicates… to delete safe extras. "
                "Details are merged onto the keeper."
            )
            msg = "\n".join(lines)
            try:
                self.log_queue.put("Duplicate check:\n" + msg)
            except Exception:
                pass
            if hasattr(self, "integrity_status"):
                safe_extra = int(summary.get("total_safe_extra_rows") or 0)
                try:
                    self.integrity_status.configure(
                        text=f"Duplicates: {safe_extra:,} safe removable"
                    )
                except Exception:
                    pass
            messagebox.showinfo("Duplicate check", msg)

        if hasattr(self, "run_bg"):
            self.run_bg(work, done, name="dup-check")
        else:
            try:
                done(result=work(), error=None)
            except Exception as e:
                done(result=None, error=e)
