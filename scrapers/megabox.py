"""Megabox theater scraper using internal JSON API."""
import requests
import logging
from datetime import datetime
from .base import BaseScraper, Screening

logger = logging.getLogger(__name__)

# Branch configurations
MEGABOX_BRANCHES = {
    "0079": "해운대(장산)",
    "0082": "서면대한",
}

SCHEDULE_URL = "https://www.megabox.co.kr/on/oh/ohc/Brch/schedulePage.do"


class MegaboxScraper(BaseScraper):
    """Scraper for Megabox theaters via internal JSON API."""

    def __init__(self, brch_no: str):
        self.brch_no = brch_no
        self.branch_name = MEGABOX_BRANCHES.get(brch_no, brch_no)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
            "Referer": f"https://www.megabox.co.kr/theater/time?brchNo={brch_no}",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        })

    def scrape(self, date: str) -> list[Screening]:
        """Scrape showtimes for a given date (YYYY-MM-DD)."""
        play_de = date.replace("-", "")
        payload = {
            "masterType": "brch",
            "detailType": "spcl",
            "brchNo": self.brch_no,
            "brchNo1": self.brch_no,
            "firstAt": "N",
            "playDe": play_de,
            "crtDe": datetime.now().strftime("%Y%m%d"),
        }

        try:
            resp = self.session.post(SCHEDULE_URL, data=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"Megabox {self.branch_name} request failed: {e}")
            return []
        except ValueError as e:
            logger.error(f"Megabox {self.branch_name} JSON parse failed: {e}")
            return []

        movie_list = data.get("megaMap", {}).get("movieFormList", [])
        if movie_list is None:
            movie_list = []

        screenings = []
        for item in movie_list:
            movie_title = (item.get("movieNm") or "").strip()
            if not movie_title:
                continue

            rest_seats = item.get("restSeatCnt")
            tot_seats = item.get("totSeatCnt")
            if rest_seats is not None and tot_seats is not None:
                remaining = f"{rest_seats}/{tot_seats}"
            else:
                remaining = ""

            screenings.append(Screening(
                date=date,
                theater_brand="메가박스",
                branch_name=self.branch_name,
                movie_title=movie_title,
                screen_name=(item.get("theabExpoNm") or "").strip(),
                format=(item.get("playKindNm") or "").strip(),
                start_time=(item.get("playStartTime") or "").strip(),
                end_time=(item.get("playEndTime") or "").strip(),
                remaining_seats=remaining,
                booking_url=f"https://www.megabox.co.kr/theater/time?brchNo={self.brch_no}",
            ))

        logger.info(f"Megabox {self.branch_name} {date}: {len(screenings)} screenings")
        return screenings
