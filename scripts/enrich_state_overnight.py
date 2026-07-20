"""Overnight state enrich: re-fetch a state's flyers, fill data, flag dead links.

Usage: python scripts/enrich_state_overnight.py FL

Resumable — skips rows already flagged dead (blocked:http_404) or already
HTML-verified, so it can be re-run nightly to keep working through the queue.
Progress is appended to data/reports/enrich_<STATE>_overnight.log.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252; force UTF-8 so names/details log cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from scraper.nsopw_builder import NSOPWEthnicDatabaseBuilder


def main() -> int:
    state = (sys.argv[1] if len(sys.argv) > 1 else "FL").strip().upper()
    log_path = ROOT / "data" / "reports" / f"enrich_{state}_overnight.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = open(log_path, "a", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        try:
            lf.write(line + "\n")
            lf.flush()
        except Exception:
            pass

    log(f"=== Starting overnight enrich for {state} ===")
    builder = NSOPWEthnicDatabaseBuilder(
        db_path=str(ROOT / "data" / "offenders.db"),
        report_delay=1.5,
        report_threads=1,
        html_dir=str(ROOT / "data" / "report_pages"),
    )
    try:
        stats = builder.enrich_state(state, save_html=True, log=log)
        log(f"=== Finished {state}: {stats} ===")
    except Exception as e:
        log(f"=== ERROR: {type(e).__name__}: {e} ===")
        return 1
    finally:
        try:
            builder.close()
        except Exception:
            pass
        try:
            lf.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
