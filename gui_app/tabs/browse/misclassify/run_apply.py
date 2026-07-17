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
        # New Analyze set → invalidate cached DeepFace column map.
        self._misclass_df_map_cache = None

        stats_results = self._results_excluding_correct(results)
        n_correct = len(results) - len(stats_results)
        if hasattr(self, "_misclass_filter_breakdown"):
            tree_results, filt_bits = self._misclass_filter_breakdown(stats_results)
        elif hasattr(self, "_misclass_apply_display_filters"):
            tree_results = self._misclass_apply_display_filters(stats_results)
            filt_bits = []
        else:
            tree_results = stats_results
            filt_bits = []

        if getattr(self, "misclass_sidebar", None) is not None:
            try:
                self.misclass_sidebar.clear("Select a row for photo and review.")
            except Exception:
                pass
        elif getattr(self, "misclass_detail", None) is not None:
            try:
                self._fill_detail_drawer(self.misclass_detail, None)
            except Exception:
                pass
        # Tree re-applies listed-as + photo filters inside populate
        self._populate_misclass_tree(stats_results)
        shown = min(500, len(tree_results))
        filt_note = (" · " + " · ".join(filt_bits)) if filt_bits else ""
        photo_hint = ""
        if (
            hasattr(self, "_misclass_photo_filter_on")
            and self._misclass_photo_filter_on()
            and len(tree_results) < len(stats_results)
        ):
            photo_hint = " · uncheck Photos only to show rows without mugshots"

        if hasattr(self, "misclass_status"):
            if eth != "all" and eth_base is not None:
                rate = (len(stats_results) / eth_base * 100.0) if eth_base else 0.0
                self.misclass_status.configure(
                    text=(
                        f"{eth}: {eth_base:,} name matches · "
                        f"{len(results):,} misclassified · "
                        f"{len(stats_results):,} unreviewed ({rate:.1f}% of names)"
                        + (f" · {n_correct:,} already confirmed" if n_correct else "")
                        + filt_note
                        + (
                            f" · tree shows first {shown} of {len(tree_results):,}"
                            if len(tree_results) > 500
                            else f" · tree shows {shown:,}"
                        )
                        + photo_hint
                    )
                )
            else:
                self.misclass_status.configure(
                    text=(
                        f"{len(results):,} misclassified · "
                        f"{len(stats_results):,} unreviewed"
                        + (f" · {n_correct:,} already confirmed" if n_correct else "")
                        + filt_note
                        + (
                            f" · tree shows first {shown} of {len(tree_results):,}"
                            if len(tree_results) > 500
                            else f" · tree shows {shown:,}"
                        )
                        + photo_hint
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
            f"Misclassification: {len(results)} raw · {len(stats_results)} unreviewed"
            + (f" · {n_correct} confirmed excluded" if n_correct else "")
            + (f" · tree {len(tree_results)}" if tree_results is not None else "")
            + (f" / {eth_base} {eth}" if eth != "all" else "")
        )
        if hasattr(self, "report_status"):
            self.report_status.configure(
                text=(
                    f"Analyze ready · {len(stats_results):,} unreviewed"
                    + (f" · {n_correct:,} already confirmed" if n_correct else "")
                    + " · Reports → Analyze & build for photo review"
                )
            )
