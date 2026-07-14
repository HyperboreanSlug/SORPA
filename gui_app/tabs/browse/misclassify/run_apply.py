"""Apply misclassification analysis results to trees / stats / status."""
from __future__ import annotations


class MisclassifyApplyMixin:
    def _apply_misclass_results(self, payload: dict) -> None:
        results = payload.get("results") or []
        eth_base = payload.get("eth_base")
        db_total = int(payload.get("db_total") or 0)
        limit = int(payload.get("limit") or 0)
        min_conf = float(payload.get("min_conf") or 0)
        eth = str(payload.get("eth") or "all")

        self._misclass_results = results
        self._misclass_meta = {
            "db_total": db_total,
            "scanned_cap": limit,
            "min_conf": min_conf,
            "eth_filter": eth,
            "eth_base_count": eth_base,
        }
        stats_results = self._results_excluding_correct(results)
        n_correct = len(results) - len(stats_results)

        if getattr(self, "misclass_detail", None) is not None:
            try:
                self._fill_detail_drawer(self.misclass_detail, None)
            except Exception:
                pass
        self._populate_misclass_tree(stats_results)
        shown = min(500, len(stats_results))
        if hasattr(self, "misclass_status"):
            if eth != "all" and eth_base is not None:
                rate = (len(stats_results) / eth_base * 100.0) if eth_base else 0.0
                self.misclass_status.configure(
                    text=(
                        f"{eth}: {eth_base:,} name matches · "
                        f"{len(stats_results):,} misclassified ({rate:.1f}%)"
                        + (f" · {n_correct} correct excluded" if n_correct else "")
                        + (f" · showing first {shown}" if len(stats_results) > shown else "")
                        + " · select a row for photo · Ctrl+C copies row"
                    )
                )
            else:
                self.misclass_status.configure(
                    text=(
                        f"{len(stats_results)} potential mismatches"
                        + (f" · {n_correct} correct excluded" if n_correct else "")
                        + (f" · showing first {shown}" if len(stats_results) > shown else "")
                        + " · select a row for photo · Statistics for transitions"
                    )
                )

        self._update_misclass_stats(
            stats_results,
            db_total=db_total,
            scanned_cap=limit,
            min_conf=min_conf,
            eth_filter=eth,
            eth_base_count=eth_base,
        )
        self.log_queue.put(
            f"Misclassification: {len(stats_results)} mismatches"
            + (f" ({n_correct} correct excluded)" if n_correct else "")
            + (f" / {eth_base} {eth}" if eth != "all" else "")
        )
        if hasattr(self, "report_status"):
            self.report_status.configure(
                text=(
                    f"Analyze ready · {len(stats_results):,} mismatches"
                    + (f" · {n_correct} correct excluded" if n_correct else "")
                    + " · Reports → Analyze & build for photo review"
                )
            )
