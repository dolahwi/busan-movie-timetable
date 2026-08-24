"""Base scraper class and data models."""
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod


@dataclass
class Screening:
    """Represents a single movie screening."""
    date: str              # YYYY-MM-DD
    theater_brand: str     # '영화의전당' | '롯데시네마' | '메가박스'
    branch_name: str       # e.g., '센텀시티', '해운대(장산)'
    movie_title: str       # Title of the film
    screen_name: str       # Hall / Room name
    format: str            # e.g., 2D, Atmos, 자막/더빙
    start_time: str        # HH:MM
    end_time: str          # HH:MM
    remaining_seats: str   # e.g., '85/150'
    booking_url: str       # Direct ticketing URL

    def to_dict(self) -> dict:
        return asdict(self)


class BaseScraper(ABC):
    """Abstract base class for theater scrapers."""

    @abstractmethod
    def scrape(self, date: str) -> list[Screening]:
        """Scrape showtimes for a given date (YYYY-MM-DD).

        Returns a list of Screening objects.
        """
        ...
