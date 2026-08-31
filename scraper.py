"""CLI scraping engine — fetches 7 days of screenings from all 7 branches.

Run directly:  python scraper.py
Import API:    from scraper import scrape_today, run_scraper
"""
import time
import logging
from datetime import datetime, timedelta, timezone

from scrapers.megabox import MegaboxScraper
from scrapers.lotte import LotteScraper
from scrapers.dureraum import DureraumScraper
from scrapers.cgv import CGVScraper
from database import init_db, save_screenings, clear_old_data, get_connection

# ── Timezone ─────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Scraper registry ─────────────────────────────────────────────────────────
SCRAPERS = [
    MegaboxScraper("0079"),   # 메가박스 해운대(장산)
    MegaboxScraper("0082"),   # 메가박스 서면대한
    LotteScraper("2006"),     # 롯데시네마 센텀시티
    LotteScraper("2004"),     # 롯데시네마 부산본점
    LotteScraper("2007"),     # 롯데시네마 동래
    DureraumScraper(),        # 영화의전당
    CGVScraper(),             # CGV 센텀시티
]

DAYS_AHEAD = 7
REQUEST_DELAY = 1.0   # seconds between requests (rate limiting)
SCRAPER_TIMEOUT = 30  # seconds — individual scraper call hard timeout


# ── Core scraping logic ───────────────────────────────────────────────────────

def _scrape_one(scraper, date: str) -> list:
    """Call a single scraper with a hard wall-clock timeout guard."""
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Scraper exceeded {SCRAPER_TIMEOUT}s timeout")

    # SIGALRM is only available on Unix
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(SCRAPER_TIMEOUT)
        result = scraper.scrape(date)
        signal.alarm(0)
        return result or []
    except TimeoutError:
        signal.alarm(0)
        raise
    except AttributeError:
        # Windows — no SIGALRM, just call directly
        return scraper.scrape(date) or []


def run_scraper(dates: list[str] | None = None) -> dict:
    """Run all scrapers for the given dates (default: next 7 days in KST).

    Returns a summary dict:
      {
        "total_screenings": int,
        "total_errors": int,
        "branch_results": {branch_name: {date: count}},
        "started_at": str,
        "finished_at": str,
      }
    """
    init_db()

    now_kst = datetime.now(KST)
    if dates is None:
        dates = [
            (now_kst + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(DAYS_AHEAD)
        ]

    # Remove screenings older than today
    clear_old_data(now_kst.strftime("%Y-%m-%d"))

    total_screenings = 0
    total_errors = 0
    branch_results: dict[str, dict] = {}

    started_at = datetime.now(KST).isoformat()
    sep = "=" * 65

    logger.info(sep)
    logger.info(
        f"Scrape started | {len(SCRAPERS)} branches × {len(dates)} dates"
    )
    logger.info(f"Date range : {dates[0]} → {dates[-1]}")
    logger.info(f"KST now    : {now_kst.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(sep)

    for scraper in SCRAPERS:
        brand = getattr(scraper, "branch_name", scraper.__class__.__name__)
        theater = getattr(scraper, "theater_brand", scraper.__class__.__name__)
        label = f"{theater} {brand}".strip()
        branch_results[label] = {}

        for date in dates:
            t0 = time.time()
            try:
                screenings = _scrape_one(scraper, date)
                elapsed = time.time() - t0

                if screenings:
                    save_screenings(screenings)
                    total_screenings += len(screenings)
                    branch_results[label][date] = len(screenings)
                    logger.info(
                        f"✓ {label:<30} {date}  {len(screenings):>3} screenings"
                        f"  ({elapsed:.1f}s)"
                    )
                else:
                    branch_results[label][date] = 0
                    logger.info(
                        f"· {label:<30} {date}  — no data ({elapsed:.1f}s)"
                    )

            except TimeoutError:
                total_errors += 1
                branch_results[label][date] = -1
                logger.error(
                    f"✗ {label:<30} {date}  TIMEOUT (>{SCRAPER_TIMEOUT}s)"
                )
            except Exception as exc:
                total_errors += 1
                branch_results[label][date] = -1
                logger.error(
                    f"✗ {label:<30} {date}  ERROR: {exc!r}"
                )

            time.sleep(REQUEST_DELAY)

    finished_at = datetime.now(KST).isoformat()

    # Final summary
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM screenings").fetchone()
        db_total = row["cnt"]
    finally:
        conn.close()

    logger.info(sep)
    logger.info(
        f"Scrape finished | {total_screenings} new rows "
        f"| {total_errors} errors | DB total: {db_total}"
    )
    logger.info(sep)

    return {
        "total_screenings": total_screenings,
        "total_errors": total_errors,
        "db_total": db_total,
        "branch_results": branch_results,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def scrape_today() -> dict:
    """Convenience wrapper: scrape only today (KST). Used by app.py on startup."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logger.info(f"scrape_today() called for {today}")
    return run_scraper(dates=[today])


if __name__ == "__main__":
    run_scraper()
