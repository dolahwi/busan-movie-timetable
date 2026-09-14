"""CGV 센텀시티 스크래퍼 — Naver Place 경유.

배경
----
CGV 공식 사이트(cgv.co.kr)는 Cloudflare Bot Management로 보호되어
curl / requests / Playwright 모두 403 또는 홈페이지 리다이렉트를 반환합니다.

대신 네이버 플레이스(m.place.naver.com)를 경유합니다:
  - 네이버가 CGV로부터 B2B 데이터 피드를 받아 공개 페이지로 제공
  - 로그인 불필요, Cloudflare 없음, requests 단순 GET으로 충분
  - 응답 HTML 내에 CGV 직접 예매 URL(cgv.co.kr/cnm/movieBook/…) 포함

실측 확인된 수치 (2026-09-14):
  - HTTP 200, SSR HTML ~787KB
  - 40개 상영 시간 슬롯, 30개 CGV 직접 예매 링크
  - IMAX, 4DX 포맷 정보 포함

제한사항:
  - 잔여석(remaining_seats) 미제공  → "" 처리
  - 종료시간(end_time) 미제공       → "" 처리
  - 날짜 간 Rate Limit(HTTP 429)    → REQUEST_DELAY 로 완화

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
CGV_SITE_NO     = "0089"              # CGV 내부 siteNo (예매 URL용)
NAVER_URL       = f"https://m.place.naver.com/theater/{NAVER_PLACE_ID}/movie"
FALLBACK_URL    = "https://www.cgv.co.kr/theaters/detail?theater=0089"

REQUEST_DELAY   = 3.0   # 날짜별 요청 간 딜레이(초) — 429 방지
REQUEST_TIMEOUT = 15    # 개별 HTTP 요청 타임아웃(초)

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


# ── HTML 파서 ─────────────────────────────────────────────────────────────────

def _parse_naver_html(html: str, date: str) -> list[Screening]:
    """네이버 플레이스 극장 영화 페이지 HTML을 파싱하여 Screening 목록 반환.

    HTML 구조 (2026-09 실측):
        ul.xdVmZ                   ← 전체 영화 목록 컨테이너
          li.AKaf7                 ← 영화 1편 블록
            span.fMjbF             ← 영화 제목
            [format/hall spans]    ← 상영 포맷, 상영관명 (위치 가변)
            ul > li.QdNKG          ← 시간 슬롯 목록
              a[href*=cgv.co.kr]   ← "22:20" 텍스트 + CGV 예매 URL

    CSS 클래스명은 배포 시 변경될 수 있으므로:
    - 클래스 우선 사용하되 href 패턴을 보조 셀렉터로 병행
    - 제목 파싱에는 클래스 외에 위치 기반 대안도 준비
    """
    soup = BeautifulSoup(html, "lxml")
    screenings: list[Screening] = []

    # ── 전체 영화 목록 컨테이너 찾기 ─────────────────────────────────────────
    # 1차: 알려진 클래스
    movie_blocks = soup.select("li.AKaf7")

    # 2차 fallback: CGV 예매 링크를 가진 <a>의 조상 <li> 묶음
    if not movie_blocks:
        cgv_links = soup.select("a[href*='cgv.co.kr/cnm/movieBook']")
        if cgv_links:
            # 각 링크의 공통 조상 li를 movie_block으로 취급
            seen_ids = set()
            for lnk in cgv_links:
                for parent in lnk.parents:
                    if parent.name == "li" and id(parent) not in seen_ids:
                        # 가장 바깥쪽 li (영화 단위) 탐색
                        outermost = parent
                        for pp in parent.parents:
                            if pp.name == "li":
                                outermost = pp
                            elif pp.name in ("ul", "div", "section", "main"):
                                break
                        if id(outermost) not in seen_ids:
                            seen_ids.add(id(outermost))
                            movie_blocks.append(outermost)
                        break

    if not movie_blocks:
        logger.debug(f"CGV Naver: 영화 블록(li.AKaf7)을 찾지 못함 — HTML {len(html)}bytes")
        return []

    for mb in movie_blocks:
        # ── 영화 제목 ─────────────────────────────────────────────────────
        title_el = (
            mb.select_one("span.fMjbF")             # 알려진 클래스
            or mb.select_one("a.GRkgM span")        # 대안 1
            or mb.select_one("div.Rtruu a span")    # 대안 2
        )
        movie_title = title_el.get_text(strip=True) if title_el else ""
        if not movie_title:
            # fallback: 첫 번째 긴 텍스트를 제목으로 추정
            for el in mb.find_all(string=True):
                t = el.strip()
                if t and len(t) > 2 and not re.match(r"^[\d:,.\-\s]+$", t):
                    movie_title = t
                    break

        if not movie_title:
            logger.debug("CGV Naver: 제목 없는 블록 건너뜀")
            continue

        # ── 상영관 & 포맷 정보 ────────────────────────────────────────────
        # 영화 블록 내 텍스트에서 포맷 키워드 추출
        block_text = mb.get_text(separator=" ", strip=True)
        fmt_keywords = ["IMAX", "4DX", "ScreenX", "Dolby", "씨네앤포레", "4DX Screen", "2D", "3D"]
        detected_formats: list[str] = [kw for kw in fmt_keywords if kw in block_text]
        screen_format = " / ".join(detected_formats) if detected_formats else ""

        # 상영관명: "n관" 패턴 추출
        hall_match = re.search(r"(\d+관[\w\s층]*?)(?:\s|$|[,·])", block_text)
        screen_name = hall_match.group(1).strip() if hall_match else screen_format

        # ── 시간 슬롯 파싱 ────────────────────────────────────────────────
        # 우선: 알려진 클래스 .SkU_I (시간 링크)
        time_links = mb.select("a.SkU_I")

        # fallback: CGV 예매 href를 가진 모든 <a>
        if not time_links:
            time_links = mb.select("a[href*='cgv.co.kr/cnm/movieBook']")

        # 추가 fallback: HH:MM 텍스트만 있는 <a> 모두
        if not time_links:
            time_links = [
                a for a in mb.find_all("a")
                if re.match(r"^\d{1,2}:\d{2}$", a.get_text(strip=True))
            ]

        for link in time_links:
            start_time = link.get_text(strip=True)
            if not re.match(r"^\d{1,2}:\d{2}$", start_time):
                continue

            href = link.get("href", "")
            # CGV 예매 URL 파라미터 추출
            # href 예: https://cgv.co.kr/cnm/movieBook/movie?movNo=79446&scnYmd=20260914&siteNo=0089&scnsNo=005&scnSseq=6
            booking_url = href if href.startswith("http") else FALLBACK_URL

            screenings.append(Screening(
                date=date,
                theater_brand="CGV",
                branch_name="센텀시티",
                movie_title=movie_title,
                screen_name=screen_name,
                format=screen_format,
                start_time=start_time,
                end_time="",           # Naver Place는 종료시간 미제공
                remaining_seats="",    # Naver Place는 잔여석 미제공
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
        날짜 간 요청 딜레이(초). 기본값 3.0s (HTTP 429 방지).
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

    def scrape(self, date: str) -> list[Screening]:
        """주어진 날짜(YYYY-MM-DD)의 CGV 센텀시티 상영 목록 반환.

        동작 순서:
          1. 네이버 플레이스 GET → 파싱
          2. HTTP 429(Rate Limit) 발생 시 5초 대기 후 1회 재시도
          3. 실패 시 빈 목록 반환 (파이프라인 계속 진행)
        """
        session = self._get_session()
        self._throttle()

        params = {"date": date}
        try:
            resp = session.get(
                NAVER_URL, params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            self._last_request_time = time.monotonic()

            # Rate Limit → 1회 재시도
            if resp.status_code == 429:
                logger.warning(f"CGV Naver 429 Rate Limit for {date} — 5초 후 재시도")
                time.sleep(5)
                resp = session.get(
                    NAVER_URL, params=params,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
                self._last_request_time = time.monotonic()

            if resp.status_code != 200:
                logger.warning(
                    f"CGV Naver 센텀시티 {date}: HTTP {resp.status_code} — 빈 목록 반환"
                )
                return []

        except requests.RequestException as exc:
            logger.error(f"CGV Naver 센텀시티 {date}: 요청 실패 — {exc!r}")
            return []

        # Naver Place는 Content-Type에 charset 생략 → requests가 ISO-8859-1로 오인식
        # 명시적으로 UTF-8 디코딩
        html = resp.content.decode("utf-8", errors="replace")
        screenings = _parse_naver_html(html, date)

        if screenings:
            logger.info(
                f"CGV Naver 센텀시티 {date}: {len(screenings)}개 상영 수집"
            )
        else:
            logger.info(
                f"CGV Naver 센텀시티 {date}: 데이터 없음 "
                f"(HTML {len(resp.text):,} bytes)"
            )

        return screenings
