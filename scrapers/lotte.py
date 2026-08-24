"""Lotte Cinema theater scraper using internal JSON API."""
import json
import requests
import logging
from .base import BaseScraper, Screening

logger = logging.getLogger(__name__)

# Branch configurations: (divisionCode, detailDivisionCode, cinemaID, branchName)
LOTTE_BRANCHES = {
    "2006": {"div": "1", "detail": "101", "name": "센텀시티"},
    "2004": {"div": "1", "detail": "101", "name": "부산본점"},
    "2007": {"div": "1", "detail": "101", "name": "동래"},
}

TICKETING_URL = "https://www.lottecinema.co.kr/LCWS/Ticketing/TicketingData.aspx"


class LotteScraper(BaseScraper):
    """Scraper for Lotte Cinema theaters via internal JSON API."""

    def __init__(self, cinema_id: str):
        branch = LOTTE_BRANCHES[cinema_id]
        self.cinema_id = cinema_id
        self.division_code = branch["div"]
        self.detail_division_code = branch["detail"]
        self.branch_name = branch["name"]
        self.composite_id = f"{self.division_code}|{self.detail_division_code}|{self.cinema_id}"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://www.lottecinema.co.kr/NLCHS/Cinema/Detail"
                       f"?divisionCode={self.division_code}"
                       f"&detailDivisionCode={self.detail_division_code}"
                       f"&cinemaID={self.cinema_id}",
            "Origin": "https://www.lottecinema.co.kr",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })

    def scrape(self, date: str) -> list[Screening]:
        """Scrape showtimes for a given date (YYYY-MM-DD)."""
        param_list = {
            "MethodName": "GetPlaySequence",
            "channelType": "HO",
            "osType": "W",
            "osVersion": self.session.headers["User-Agent"],
            "playDate": date,
            "cinemaID": self.composite_id,
            "representationMovieCode": "",
            "memberOnNo": "0",
        }

        try:
            # Must send as multipart/form-data with paramList field
            resp = self.session.post(
                TICKETING_URL,
                files={"paramList": (None, json.dumps(param_list))},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"Lotte {self.branch_name} request failed: {e}")
            return []
        except ValueError as e:
            logger.error(f"Lotte {self.branch_name} JSON parse failed: {e}")
            return []

        if not data.get("IsOK", False):
            msg = data.get("ResultMessage", "Unknown error")
            logger.error(f"Lotte {self.branch_name} API error: {msg}")
            return []

        items = data.get("PlaySeqs", {}).get("Items", [])
        if items is None:
            items = []

        screenings = []
        booking_url = (
            f"https://www.lottecinema.co.kr/NLCHS/Cinema/Detail"
            f"?divisionCode={self.division_code}"
            f"&detailDivisionCode={self.detail_division_code}"
            f"&cinemaID={self.cinema_id}"
        )

        for item in items:
            movie_title = (item.get("MovieNameKR") or "").strip()
            if not movie_title:
                continue

            total_seats = int(item.get("TotalSeatCount", 0))
            booked_seats = int(item.get("BookingSeatCount", 0))
            remain = max(0, total_seats - booked_seats)
            remaining = f"{remain}/{total_seats}" if total_seats > 0 else ""

            screenings.append(Screening(
                date=date,
                theater_brand="롯데시네마",
                branch_name=self.branch_name,
                movie_title=movie_title,
                screen_name=(item.get("ScreenNameKR") or "").strip(),
                format=(item.get("FilmNameKR") or "2D").strip(),
                start_time=(item.get("StartTime") or "").strip(),
                end_time=(item.get("EndTime") or "").strip(),
                remaining_seats=remaining,
                booking_url=booking_url,
            ))

        logger.info(f"Lotte {self.branch_name} {date}: {len(screenings)} screenings")
        return screenings
