"""Busan Cinema Center (영화의전당) scraper via server-rendered HTML."""
import re
import requests
import logging
from bs4 import BeautifulSoup
from .base import BaseScraper, Screening

logger = logging.getLogger(__name__)

BASE_URL = "https://www.dureraum.org"
MAIN_URL = f"{BASE_URL}/bcc/main/main.do?rbsIdx=1"
CALE_LIST_URL = f"{BASE_URL}/bcc/mcontents/caleList.do"
BOOKING_URL = f"{CALE_LIST_URL}?rbsIdx=37"


class DureraumScraper(BaseScraper):
    """Scraper for Busan Cinema Center (영화의전당) via HTML parsing.

    The site requires an active JSESSIONID cookie obtained by first visiting
    the main page. Each date is fetched via searchDate=YYYY-MM-DD query param.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;"
                      "q=0.9,*/*;q=0.8",
        })
        self._session_initialized = False

    def _init_session(self):
        """Visit the main page to obtain a JSESSIONID cookie."""
        if self._session_initialized:
            return
        try:
            self.session.get(MAIN_URL, timeout=10)
            self._session_initialized = True
        except requests.RequestException as e:
            logger.warning(f"Dureraum session init failed: {e}")

    def scrape(self, date: str) -> list[Screening]:
        """Scrape showtimes for a given date (YYYY-MM-DD)."""
        self._init_session()

        params = {
            "rbsIdx": "37",
            "searchDate": date,
        }

        try:
            resp = self.session.get(
                CALE_LIST_URL, params=params, timeout=15,
                headers={"Referer": BOOKING_URL},
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            logger.error(f"Dureraum request failed: {e}")
            return []

        if len(resp.text) < 100:
            logger.warning(f"Dureraum empty response for {date} "
                           f"({len(resp.text)} bytes)")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        return self._parse_screenings(soup, date)

    def _parse_screenings(self, soup: BeautifulSoup, date: str) -> list[Screening]:
        """Parse screening data from the HTML.

        Structure:
          div.list > ul > (li.title + li.place + li.time)

        li.title contains:
          - <a title="MOVIE_TITLE"> with the clean title
          - <h4 class="hidden">AGE_RATING</h4>
          - text body: "TITLE | RUNTIME"

        li.place contains: hall name (중극장, 소극장, 시네마테크, etc.)
        li.time contains: <a> with time text (HH:MM)
        """
        screenings = []
        list_div = soup.select_one("div.list")

        if not list_div:
            logger.debug(f"Dureraum: no div.list found for {date}")
            return []

        # Each <ul> (direct child of div.list, not .list_tab) is one screening
        for ul in list_div.find_all("ul", recursive=False):
            # Skip the tab navigation UL
            if ul.get("class") and "list_tab" in ul.get("class", []):
                continue

            title_li = ul.select_one("li.title")
            place_li = ul.select_one("li.place")
            time_li = ul.select_one("li.time")

            if not title_li or not time_li:
                continue

            # Extract movie title from the <a title="..."> attribute
            title_link = title_li.select_one("a[title]")
            if title_link:
                movie_title = title_link.get("title", "").strip()
                detail_href = title_link.get("href", "")
            else:
                movie_title = title_li.get_text(strip=True)
                detail_href = ""

            if not movie_title:
                continue

            # Extract hall/place
            screen_name = place_li.get_text(strip=True) if place_li else ""

            # Extract time
            time_text = time_li.get_text(strip=True)
            time_match = re.search(r'(\d{1,2}:\d{2})', time_text)
            start_time = time_match.group(1) if time_match else time_text.strip()
            # Pad single-digit hours
            if len(start_time) == 4:
                start_time = "0" + start_time

            # Extract format from title text body (e.g., "오디세이(SCOPE) | 172min")
            title_full_text = title_li.get_text(" ", strip=True)
            format_info = ""
            # Look for format indicators in title
            for fmt in ["SCOPE", "FLAT", "IMAX", "Atmos", "자막", "더빙",
                         "DCP", "35mm", "GV", "배리어프리", "디지털"]:
                if fmt.lower() in title_full_text.lower() or fmt in title_full_text:
                    format_info = fmt if not format_info else f"{format_info}, {fmt}"

            # Build booking URL with detail link if available
            booking_url = BOOKING_URL
            if detail_href and not detail_href.startswith("http"):
                booking_url = f"{BASE_URL}/bcc/mcontents/{detail_href}"

            screenings.append(Screening(
                date=date,
                theater_brand="영화의전당",
                branch_name="영화의전당",
                movie_title=movie_title,
                screen_name=screen_name,
                format=format_info,
                start_time=start_time,
                end_time="",
                remaining_seats="",
                booking_url=booking_url,
            ))

        logger.info(f"Dureraum {date}: {len(screenings)} screenings")
        return screenings
