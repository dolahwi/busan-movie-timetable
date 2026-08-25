"""CGV Centum City scraper.

Technical context
-----------------
CGV operates behind Cloudflare Bot Management.  As of 2026-08 the situation is:

  * iframeTheater.aspx  → HTTP 403 (Cloudflare blocks all non-browser requests)
  * api.cgv.co.kr JSON  → HTTP 401 (requires HMAC-SHA256 X-SIGNATURE with a
                           server-side secret that is NOT exposed in JS bundles)
  * Playwright headless  → Cloudflare JS challenge redirects to homepage

Strategy implemented here (three-tier fallback)
------------------------------------------------
1. **Playwright stealth** – The only reliable path.  We launch a headless
   Chromium browser with playwright-stealth evasions, navigate to the
   CGV theater schedule page, wait for the server-rendered HTML to load,
   and parse the timetable table.  Works in real sessions; may break if
   Cloudflare tightens its ruleset.

2. **requests + lxml fallback** – If Playwright is unavailable (e.g., CI
   runners without Chromium) we attempt a plain HTTP GET against
   iframeTheater.aspx.  This succeeds occasionally from Korean residential
   IPs but fails from cloud IPs.

3. **Graceful empty list** – If both attempts fail we log a warning and
   return [], so the rest of the pipeline continues normally.

CGV Centum City identifiers
----------------------------
  * Old iframe code : theatercode=0089, areacode=05,207
  * New JSON API    : coCd=A420, siteNo (mapped below from theater page)
  * Schedule URL    : https://www.cgv.co.kr/theaters/detail?theater=0089
"""

import re
import logging
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Screening

logger = logging.getLogger(__name__)

CENTUM_THEATER_CODE = "0089"
CENTUM_AREA_CODE    = "05,207"
IFRAME_URL = "https://www.cgv.co.kr/common/showtimes/iframeTheater.aspx"
BOOKING_URL = (
    "https://www.cgv.co.kr/theaters/detail?theater=0089"
)


# ---------------------------------------------------------------------------
# HTML parsing helper (shared by both Playwright and requests paths)
# ---------------------------------------------------------------------------

def _parse_iframe_html(html: str, date: str) -> list[Screening]:
    """Parse the HTML returned by iframeTheater.aspx or Playwright render."""
    soup = BeautifulSoup(html, "lxml")
    screenings: list[Screening] = []

    # ── Each <li> under div.sect-showtimes > ul is one movie ──────────────
    movie_items = soup.select("div.sect-showtimes > ul > li")
    if not movie_items:
        # Some page versions use a different wrapper
        movie_items = soup.select("ul.movie-list > li, .col-movies li")

    for item in movie_items:
        # Movie title
        title_el = (
            item.select_one("div.info-movie a strong")
            or item.select_one(".info-movie strong")
            or item.select_one("a strong")
        )
        if not title_el:
            continue
        movie_title = title_el.get_text(strip=True)
        if not movie_title:
            continue

        # Each hall / screen type group is inside div.type-hall
        hall_divs = item.select("div.type-hall")
        if not hall_divs:
            # Flatten fallback: treat the whole item as one hall
            hall_divs = [item]

        for hall in hall_divs:
            # Screen format (2D, IMAX, 4DX, ScreenX, …)
            fmt_el = hall.select_one("span.round.theater, .ico-hall")
            screen_format = fmt_el.get_text(strip=True) if fmt_el else ""

            # Screen / hall name (e.g. "1관 7층", "센텀 3관")
            hall_info_el = hall.select_one("div.info-hall li:nth-child(2), .hall-name")
            screen_name = hall_info_el.get_text(strip=True) if hall_info_el else ""

            # Total seats from hall info line 3
            total_el = hall.select_one("div.info-hall li:nth-child(3)")
            total_seats_str = total_el.get_text(strip=True) if total_el else ""
            total_match = re.search(r'(\d+)', total_seats_str)
            total_seats = int(total_match.group(1)) if total_match else 0

            # Individual screenings within this hall
            for slot in hall.select("div.info-timetable ul li, .timetable-wrap li"):
                link = slot.select_one("a")
                if not link:
                    continue

                # Start time
                time_el = link.select_one("em")
                if not time_el:
                    continue
                start_time = time_el.get_text(strip=True)
                if not re.match(r'\d{1,2}:\d{2}', start_time):
                    continue

                # Remaining seats / sold-out indicator
                seat_el = (
                    link.select_one("span.txt-lightblue")
                    or link.select_one("span.count")
                    or link.select_one(".seat-info")
                )
                sold_out_el = link.select_one("span.txt-gray, .ico-sold")

                if sold_out_el and "마감" in sold_out_el.get_text():
                    remaining_seats = "0"
                elif seat_el:
                    raw = seat_el.get_text(strip=True)          # e.g. "45석"
                    num = re.search(r'(\d+)', raw)
                    remain = num.group(1) if num else raw
                    remaining_seats = (
                        f"{remain}/{total_seats}" if total_seats else remain
                    )
                else:
                    remaining_seats = ""

                # data-* attributes for booking URL
                screen_code = link.get("data-screencode", "")
                play_num    = link.get("data-playnum", "")
                play_ymd    = link.get("data-playymd", date.replace("-", ""))

                if screen_code and play_num:
                    direct_url = (
                        f"https://ticket.cgv.co.kr/Reservation/Ticketing.aspx"
                        f"?cinemaID={CENTUM_THEATER_CODE}"
                        f"&screenCode={screen_code}"
                        f"&playDate={play_ymd}&playNum={play_num}"
                    )
                else:
                    direct_url = BOOKING_URL

                screenings.append(Screening(
                    date=date,
                    theater_brand="CGV",
                    branch_name="센텀시티",
                    movie_title=movie_title,
                    screen_name=screen_name or screen_format,
                    format=screen_format,
                    start_time=start_time,
                    end_time="",          # not in iframe HTML
                    remaining_seats=remaining_seats,
                    booking_url=direct_url,
                ))

    return screenings


# ---------------------------------------------------------------------------
# Path 1 – Playwright stealth (primary)
# ---------------------------------------------------------------------------

def _scrape_with_playwright(date: str) -> list[Screening] | None:
    """Navigate the real CGV theater page with a stealthy headless browser.

    Returns a list of Screening objects, or None if Playwright is unavailable
    or the page is blocked.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("Playwright not installed — skipping")
        return None

    play_date = date.replace("-", "")
    schedule_url = (
        f"https://www.cgv.co.kr/theaters/detail"
        f"?theater={CENTUM_THEATER_CODE}&date={play_date}"
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
            )

            # Apply stealth patches if available
            try:
                from playwright_stealth import stealth_sync
                page = context.new_page()
                stealth_sync(page)
            except ImportError:
                page = context.new_page()

            # Hide webdriver flag
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            logger.debug(f"CGV Playwright → {schedule_url}")
            resp = page.goto(
                schedule_url, wait_until="domcontentloaded", timeout=30_000
            )

            if resp is None or resp.status >= 400:
                logger.warning(f"CGV Playwright got status {resp and resp.status}")
                browser.close()
                return None

            # Wait for timetable section to appear (or 8 s)
            try:
                page.wait_for_selector(
                    "div.sect-showtimes, .movie-list, .timetable-wrap",
                    timeout=8_000,
                )
            except Exception:
                pass  # parse whatever rendered

            html = page.content()
            browser.close()

        screenings = _parse_iframe_html(html, date)
        logger.info(f"CGV Playwright 센텀시티 {date}: {len(screenings)} screenings")
        return screenings

    except Exception as e:
        logger.warning(f"CGV Playwright failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Path 2 – Plain requests against iframeTheater.aspx (fallback)
# ---------------------------------------------------------------------------

def _scrape_with_requests(date: str) -> list[Screening]:
    """Fetch the CGV schedule iframe page using plain requests.

    Works from Korean residential IPs; fails from most cloud/CI IPs due to
    Cloudflare Bot Management (returns homepage redirect instead of schedule).
    """
    play_date = date.replace("-", "")
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": (
            f"https://www.cgv.co.kr/theaters/?areacode="
            f"{CENTUM_AREA_CODE}&theaterCode={CENTUM_THEATER_CODE}"
        ),
    })

    try:
        resp = session.get(
            IFRAME_URL,
            params={
                "areacode": CENTUM_AREA_CODE,
                "theatercode": CENTUM_THEATER_CODE,
                "date": play_date,
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"CGV requests error: {e}")
        return []

    # Detect Cloudflare redirect to homepage
    if resp.url.rstrip("/") in (
        "https://www.cgv.co.kr",
        "https://cgv.co.kr",
    ) or "깊이 빠져 보다, CGV" in resp.text[:2000]:
        logger.debug(f"CGV requests: Cloudflare redirect detected for {date}")
        return []

    screenings = _parse_iframe_html(resp.text, date)
    logger.info(f"CGV requests 센텀시티 {date}: {len(screenings)} screenings")
    return screenings


# ---------------------------------------------------------------------------
# Public scraper class
# ---------------------------------------------------------------------------

class CGVScraper(BaseScraper):
    """Scraper for CGV Centum City (센텀시티, theater code 0089).

    Uses a three-tier strategy:
      1. Playwright stealth browser (primary, works from local/residential IPs)
      2. Plain requests against iframeTheater.aspx (occasional bypass)
      3. Empty list with warning (graceful degradation in CI/cloud)
    """

    def __init__(self):
        self.branch_name = "센텀시티"

    def scrape(self, date: str) -> list[Screening]:
        """Scrape showtimes for *date* (YYYY-MM-DD)."""

        # ── Playwright primary path ────────────────────────────────────────
        result = _scrape_with_playwright(date)
        if result is not None:
            return result

        # ── requests fallback ──────────────────────────────────────────────
        result = _scrape_with_requests(date)
        if result:
            return result

        # ── graceful empty ─────────────────────────────────────────────────
        logger.warning(
            f"CGV 센텀시티 {date}: all scraping paths failed "
            "(Cloudflare blocks cloud IPs — data not available in this environment)"
        )
        return []
