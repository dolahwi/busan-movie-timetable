"""CLI scraping engine — fetches 7 days of screenings from all 7 branches."""
import sys
import time
import logging
from datetime import datetime, timedelta

from scrapers.megabox import MegaboxScraper
from scrapers.lotte import LotteScraper
from scrapers.dureraum import DureraumScraper
from scrapers.cgv import CGVScraper
from database import init_db, save_screenings, clear_old_data, get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# All 7 target scrapers
SCRAPERS = [
    # Megabox branches
    MegaboxScraper("0079"),   # 해운대(장산)
    MegaboxScraper("0082"),   # 서면대한
    # Lotte Cinema branches
    LotteScraper("2006"),     # 센텀시티
    LotteScraper("2004"),     # 부산본점
    LotteScraper("2007"),     # 동래
    # Busan Cinema Center
    DureraumScraper(),        # 영화의전당
    # CGV
    CGVScraper(),             # CGV 센텀시티
]

DAYS_AHEAD = 7
REQUEST_DELAY = 1.0  # seconds between requests


def run_scraper():
    """Run all scrapers for the next 7 days."""
    init_db()

    today = datetime.now()
    dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS_AHEAD)]

    # Clean up old data
    clear_old_data(today.strftime("%Y-%m-%d"))

    total_screenings = 0
    total_errors = 0

    logger.info(f"Starting scrape for {len(SCRAPERS)} branches × {len(dates)} days")
    logger.info(f"Date range: {dates[0]} → {dates[-1]}")
    print("=" * 60)

    for scraper in SCRAPERS:
        brand = getattr(scraper, "branch_name", scraper.__class__.__name__)
        for date in dates:
            try:
                screenings = scraper.scrape(date)
                if screenings:
                    save_screenings(screenings)
                    total_screenings += len(screenings)
                    print(f"  ✓ {brand} | {date} | {len(screenings)} screenings")
                else:
                    print(f"  · {brand} | {date} | no data")
            except Exception as e:
                total_errors += 1
                logger.error(f"Error scraping {brand} {date}: {e}")
                print(f"  ✗ {brand} | {date} | ERROR: {e}")

            time.sleep(REQUEST_DELAY)

    print("=" * 60)
    print(f"Done. Total: {total_screenings} screenings, {total_errors} errors")

    # Print summary from DB
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM screenings").fetchone()
        print(f"Database total: {row['cnt']} rows in timetable.db")
    finally:
        conn.close()


if __name__ == "__main__":
    run_scraper()
