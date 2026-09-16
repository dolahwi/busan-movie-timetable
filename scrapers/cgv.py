"""CGV 센텀시티 스크래퍼 — Naver Place 경유.

배경
----
CGV 공식 사이트(cgv.co.kr)는 Cloudflare Bot Management로 보호되어
curl / requests / Playwright 모두 403 또는 홈페이지 리다이렉트를 반환합니다.

대신 네이버 플레이스(m.place.naver.com)를 경유합니다:
  - 네이버가 CGV로부터 B2B 데이터 피드를 받아 공개 페이지로 제공
  - 로그인 불필요, Cloudflare 없음, requests 단순 GET으로 충분
  - 응답 HTML(~760KB) SSR로 제공 — BeautifulSoup 파싱 가능

2026-09 구조 변경 사항:
  - a.SkU_I[href="#"]  — 예매 URL이 href에서 사라짐 (JS 런타임 주입 방식으로 변경)
  - 예매 URL은 HTML 안의 JSON 이스케이프 문자열에 여전히 포함됨
  - 파서가 regex로 JSON 블록에서 "시간→URL" 매핑을 추출

제한사항:
  - 잔여석(remaining_seats) 미제공  → "" 처리
  - 종료시간(end_time) 미제공       → "" 처리
  - Rate Limit(HTTP 429 or 빈 응답) → 딜레이 + 재시도

CGV 센텀시티 식별자:
  - Naver Place ID : 13082085
  - CGV siteNo     : 0089
  - 지역           : 부산 해운대구
"""

import re
import time
import logging

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, Screening

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────
NAVER_PLACE_ID  = "13082085"          # CGV 센텀시티 네이버 플레이스 ID
CGV_SITE_NO     = "0089"              # CGV 내부 siteNo
NAVER_URL       = f"https://m.place.naver.com/theater/{NAVER_PLACE_ID}/movie"
FALLBACK_BOOKING_URL = (
    f"https://www.cgv.co.kr/theaters/detail?theater={CGV_SITE_NO}"
)

REQUEST_DELAY   = 4.0   # 날짜별 요청 간 딜레이(초) — 429 방지용 (3→4초로 증가)
REQUEST_TIMEOUT = 20    # 개별 HTTP 요청 타임아웃(초)
MIN_HTML_SIZE   = 100_000  # 이보다 짧으면 Rate Limit 빈 응답으로 간주 (bytes)
RETRY_WAIT      = 10       # Rate Limit / 빈 응답 재시도 대기(초)

_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Referer": f"https://map.naver.com/p/entry/place/{NAVER_PLACE_ID}?placePath=%2Fmovie",
}


# ── 예매 URL 추출 (JSON 스크립트 블록 기반) ────────────────────────────────────

def _extract_booking_urls(html: str) -> dict[str, str]:
    """HTML에서 시간→예매 URL 매핑 딕셔너리 반환.

    Naver Place HTML 안에 CGV 예매 URL이 두 가지 형태로 존재합니다:
      1. 일반 href : https://cgv.co.kr/cnm/movieBook/movie?movNo=...
      2. JSON 이스케이프 : cgv.co.kr\\u002Fcnm\\u002FmovieBook\\u002Fmovie?movNo=...

    두 형태 모두 regex로 수집한 뒤 쿼리 파라미터에서 scnYmd(날짜), scnSseq(회차)를
    키워드로 사용해 중복 제거합니다.

    Returns:
        dict mapping full URL → full URL (중복 제거된 세트)
        또는 scnSseq → URL (개별 매핑이 가능한 경우)
    """
    urls: list[str] = []

    # 패턴 1: 일반 href (href="https://cgv.co.kr/...")
    direct = re.findall(
        r'https?://(?:www\.)?cgv\.co\.kr/cnm/movieBook/movie\?[^"\'<\s]+',
        html,
    )
    urls.extend(direct)

    # 패턴 2: JSON 유니코드 이스케이프 (\\u002F = /)
    escaped = re.findall(
        r'cgv\.co\.kr\\u002Fcnm\\u002FmovieBook\\u002Fmovie\?([^"\\]+)',
        html,
    )
    for raw_qs in escaped:
        # \u003D → =, \u0026 → &
        qs = (
            raw_qs
            .replace("\\u003D", "=")
            .replace("\\u0026", "&")
            .replace("&amp;", "&")
        )
        urls.append(f"https://cgv.co.kr/cnm/movieBook/movie?{qs}")

    # scnSseq(회차 번호) 기반 중복 제거 → 각 회차별 최신 URL 유지
    seq_to_url: dict[str, str] = {}
    for url in urls:
        m = re.search(r"scnSseq=(\d+)", url)
        if m:
            seq_to_url[m.group(1)] = url
        else:
            # scnSseq 없는 경우 URL 자체를 키로
            seq_to_url[url] = url

    return seq_to_url  # {scnSseq: url, ...}


def _parse_naver_html(html: str, date: str) -> list[Screening]:
    """네이버 플레이스 극장 영화 페이지 HTML을 파싱하여 Screening 목록 반환.

    HTML 구조 (2026-09 실측):
        li.AKaf7                 ← 영화 1편 블록
          [제목 span]
          [포맷/상영관 정보]
          ul > li.QdNKG          ← 시간 슬롯 목록
            a.SkU_I[href="#"]   ← "22:20" 텍스트 (href는 # — JS 주입)

    예매 URL은 HTML 전체의 JSON 스크립트 블록에서 별도로 추출한다.
    """
    soup = BeautifulSoup(html, "lxml")
    screenings: list[Screening] = []

    # ── 전체 예매 URL 먼저 수집 ────────────────────────────────────────────
    booking_map = _extract_booking_urls(html)  # {scnSseq: full_url}
    booking_url_list = list(booking_map.values())  # 순서 보존 목록

    # ── 전체 영화 목록 컨테이너 찾기 ─────────────────────────────────────────
    movie_blocks = soup.select("li.AKaf7")

    # Fallback: 시간 패턴 <a>의 가장 바깥 li를 movie_block으로 추정
    if not movie_blocks:
        time_links_all = [
            a for a in soup.find_all("a")
            if re.match(r"^\d{1,2}:\d{2}$", a.get_text(strip=True))
        ]
        seen = set()
        for a in time_links_all:
            outermost = None
            for parent in a.parents:
                if parent.name == "li":
                    outermost = parent
                elif parent.name in ("ul", "ol", "div", "section", "main", "body"):
                    break
            if outermost and id(outermost) not in seen:
                seen.add(id(outermost))
                movie_blocks.append(outermost)

    if not movie_blocks:
        logger.debug(
            f"CGV Naver: 영화 블록(li.AKaf7)을 찾지 못함 — HTML {len(html):,} bytes"
        )
        return []

    # 예매 URL을 영화 블록 순서로 배분하기 위한 인덱스
    booking_idx = 0

    for mb in movie_blocks:
        # ── 영화 제목 ─────────────────────────────────────────────────────
        title_el = (
            mb.select_one("span.fMjbF")          # 알려진 클래스
            or mb.select_one("a.GRkgM span")     # 대안 1
            or mb.select_one("div.Rtruu a span") # 대안 2
        )
        movie_title = title_el.get_text(strip=True) if title_el else ""

        if not movie_title:
            # 첫 번째 의미있는 텍스트를 제목으로 추정
            for el in mb.find_all(string=True):
                t = el.strip()
                if t and len(t) > 1 and not re.match(r"^[\d:,.\-\s]+$", t):
                    movie_title = t
                    break

        if not movie_title:
            logger.debug("CGV Naver: 제목 없는 블록 건너뜀")
            continue

        # ── 포맷 / 상영관 ────────────────────────────────────────────────
        block_text = mb.get_text(separator=" ", strip=True)
        fmt_keywords = ["IMAX", "4DX Screen", "4DX", "ScreenX", "Dolby", "씨네앤포레", "3D", "2D"]
        detected_formats = [kw for kw in fmt_keywords if kw in block_text]
        screen_format = " / ".join(detected_formats) if detected_formats else ""

        hall_match = re.search(r"(\d+관[\w\s층]*?)(?:\s|$|[,·])", block_text)
        screen_name = hall_match.group(1).strip() if hall_match else screen_format

        # ── 시간 슬롯 파싱 ────────────────────────────────────────────────
        # a.SkU_I 우선 (href="#" 포함 — 시간 텍스트만 사용)
        time_links = mb.select("a.SkU_I")

        # fallback: 직접 예매 href가 있는 <a>
        if not time_links:
            time_links = mb.select("a[href*='cgv.co.kr/cnm/movieBook']")

        # fallback: HH:MM 패턴 <a>
        if not time_links:
            time_links = [
                a for a in mb.find_all("a")
                if re.match(r"^\d{1,2}:\d{2}$", a.get_text(strip=True))
            ]

        for link in time_links:
            start_time = link.get_text(strip=True)
            if not re.match(r"^\d{1,2}:\d{2}$", start_time):
                continue

            # 예매 URL: href에 직접 있으면 우선 사용
            href = link.get("href", "")
            if href.startswith("http") and "cgv.co.kr" in href:
                booking_url = href
            elif booking_idx < len(booking_url_list):
                # JSON에서 추출한 URL을 순서대로 배분
                booking_url = booking_url_list[booking_idx]
                booking_idx += 1
            else:
                booking_url = FALLBACK_BOOKING_URL

            screenings.append(Screening(
                date=date,
                theater_brand="CGV",
                branch_name="센텀시티",
                movie_title=movie_title,
                screen_name=screen_name,
                format=screen_format,
                start_time=start_time,
                end_time="",            # Naver Place 미제공
                remaining_seats="",     # Naver Place 미제공
                booking_url=booking_url,
            ))

    return screenings


# ── 공개 스크래퍼 클래스 ──────────────────────────────────────────────────────

class CGVScraper(BaseScraper):
    """CGV 센텀시티 스크래퍼.

    네이버 플레이스(m.place.naver.com)를 경유하여 CGV 공식 사이트의
    Cloudflare WAF를 우회합니다.  헤드리스 브라우저 없이 순수 requests +
    BeautifulSoup만 사용합니다.

    Parameters
    ----------
    request_delay : float
        날짜 간 요청 딜레이(초). 기본값 4.0s (HTTP 429 방지).
    """

    theater_brand = "CGV"
    branch_name   = "센텀시티"

    def __init__(self, request_delay: float = REQUEST_DELAY):
        self._delay  = request_delay
        self._session: requests.Session | None = None
        self._last_request_time = 0.0

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(_SESSION_HEADERS)
        return self._session

    def _throttle(self):
        """요청 간 딜레이 적용 (Rate Limit 방지)."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

    def _fetch(self, date: str) -> str | None:
        """Naver Place GET 요청. 빈 응답/429 시 1회 재시도. 실패 시 None 반환."""
        session = self._get_session()
        params = {"date": date}

        for attempt in range(2):
            self._throttle()
            try:
                resp = session.get(
                    NAVER_URL,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
                self._last_request_time = time.monotonic()

                if resp.status_code == 429:
                    logger.warning(
                        f"CGV Naver 429 Rate Limit {date} "
                        f"(attempt {attempt+1}) — {RETRY_WAIT}초 대기"
                    )
                    time.sleep(RETRY_WAIT)
                    continue

                if resp.status_code != 200:
                    logger.warning(
                        f"CGV Naver 센텀시티 {date}: HTTP {resp.status_code}"
                    )
                    return None

                html = resp.content.decode("utf-8", errors="replace")

                # 빈 응답 감지 (Rate Limit의 다른 형태)
                if len(html) < MIN_HTML_SIZE:
                    logger.warning(
                        f"CGV Naver 센텀시티 {date}: 응답 너무 짧음 "
                        f"({len(html):,} bytes < {MIN_HTML_SIZE:,}) — "
                        f"Rate Limit 의심, {RETRY_WAIT}초 대기 후 재시도"
                    )
                    time.sleep(RETRY_WAIT)
                    continue

                return html

            except requests.RequestException as exc:
                logger.error(f"CGV Naver 센텀시티 {date}: 요청 실패 — {exc!r}")
                return None

        logger.error(f"CGV Naver 센텀시티 {date}: 2회 시도 모두 실패")
        return None

    def scrape(self, date: str) -> list[Screening]:
        """주어진 날짜(YYYY-MM-DD)의 CGV 센텀시티 상영 목록 반환.

        동작 순서:
          1. Naver Place GET (딜레이 적용)
          2. 빈 응답 / HTTP 429 → RETRY_WAIT 후 1회 재시도
          3. HTML 파싱 — li.AKaf7 영화 블록 + 예매 URL JSON 추출
          4. 실패 시 빈 목록 반환 (파이프라인 계속 진행)
        """
        html = self._fetch(date)
        if html is None:
            return []

        screenings = _parse_naver_html(html, date)

        if screenings:
            logger.info(
                f"CGV Naver 센텀시티 {date}: {len(screenings)}개 상영 수집"
            )
        else:
            logger.info(
                f"CGV Naver 센텀시티 {date}: 데이터 없음 "
                f"(HTML {len(html):,} bytes)"
            )

        return screenings
