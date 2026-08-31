"""FastAPI web server for Busan theater timetable.

Startup behaviour
-----------------
On cold-start, if the database has no rows for today (KST), the server
automatically runs the scraper in a background thread so the first visitor
sees data within ~60 seconds rather than an empty page.

A manual refresh endpoint is also provided:  POST /api/refresh
"""
import os
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from database import init_db, get_screenings, get_branches, get_movies, get_last_scraped

logger = logging.getLogger(__name__)

# Korea Standard Time
KST = timezone(timedelta(hours=9))

# ── DB initialisation (runs at import time for Vercel cold-starts) ────────────
init_db()

# ── Background scrape state ───────────────────────────────────────────────────
_scrape_lock = threading.Lock()
_scraping = False   # True while a scrape is in progress


def _is_data_stale() -> bool:
    """Return True if today (KST) has zero rows in the DB."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    rows = get_screenings(today)
    return len(rows) == 0


def _run_scraper_background():
    """Run the full 7-day scraper in a background thread (non-blocking)."""
    global _scraping
    with _scrape_lock:
        if _scraping:
            logger.info("Scraper already running — skipping duplicate trigger")
            return
        _scraping = True

    try:
        logger.info("Auto-scrape triggered (background thread)")
        from scraper import run_scraper
        result = run_scraper()
        logger.info(
            f"Auto-scrape done: {result['total_screenings']} screenings, "
            f"{result['total_errors']} errors"
        )
    except Exception as exc:
        logger.error(f"Auto-scrape failed: {exc!r}")
    finally:
        _scraping = False


# ── Lifespan: auto-scrape on startup if DB is empty ──────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    if _is_data_stale():
        logger.info("DB is empty for today — launching background scrape")
        thread = threading.Thread(target=_run_scraper_background, daemon=True)
        thread.start()
    else:
        logger.info("DB has today's data — skipping startup scrape")

    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="부산 영화 상영시간표", version="2.0.0", lifespan=lifespan)

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    date: str = None,
    branch: str = None,
    movie: str = None,
):
    """Main timetable page."""
    now_kst = datetime.now(KST)
    if not date:
        date = now_kst.strftime("%Y-%m-%d")

    # 7-day date navigation bar
    date_nav = []
    for i in range(7):
        d = now_kst + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        day_label = "오늘" if i == 0 else d.strftime("%m/%d")
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        date_nav.append({
            "date": d_str,
            "label": f"{day_label}({weekday_kr})",
            "active": d_str == date,
            "is_weekend": d.weekday() >= 5,
        })

    screenings  = get_screenings(date, branch=branch, movie=movie)
    branches    = get_branches(date)
    movies      = get_movies(date)
    last_scraped = get_last_scraped()

    # Group by brand → branch name for display
    grouped: dict[str, list] = {}
    for s in screenings:
        key = f"{s['theater_brand']} {s['branch_name']}"
        grouped.setdefault(key, []).append(s)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "date": date,
            "date_nav": date_nav,
            "screenings": screenings,
            "grouped": grouped,
            "branches": branches,
            "movies": movies,
            "selected_branch": branch or "",
            "selected_movie": movie or "",
            "total_count": len(screenings),
            "last_scraped": last_scraped,
            "scraping_in_progress": _scraping,
        },
    )


@app.get("/api/screenings")
async def api_screenings(
    date: str = Query(default=None, description="YYYY-MM-DD"),
    branch: str = Query(default=None),
    movie: str = Query(default=None),
):
    """JSON API — screening data for a given date."""
    if not date:
        date = datetime.now(KST).strftime("%Y-%m-%d")
    screenings = get_screenings(date, branch=branch, movie=movie)
    return JSONResponse(content={
        "date": date,
        "count": len(screenings),
        "screenings": screenings,
    })


@app.post("/api/refresh")
async def api_refresh(background_tasks: BackgroundTasks):
    """Manually trigger a background scrape (idempotent — won't double-run)."""
    if _scraping:
        return JSONResponse(
            content={"status": "already_running", "message": "스크래핑이 이미 진행 중입니다."},
            status_code=202,
        )
    background_tasks.add_task(_run_scraper_background)
    return JSONResponse(
        content={
            "status": "started",
            "message": "스크래핑을 시작했습니다. 약 60~90초 후 데이터를 확인하세요.",
        }
    )


@app.get("/api/status")
async def api_status():
    """Health check: DB row counts and scrape status."""
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date, COUNT(*) as cnt FROM screenings GROUP BY date ORDER BY date"
        ).fetchall()
    finally:
        conn.close()

    today = datetime.now(KST).strftime("%Y-%m-%d")
    return JSONResponse(content={
        "today_kst": today,
        "scraping_in_progress": _scraping,
        "last_scraped": get_last_scraped(),
        "rows_by_date": {r["date"]: r["cnt"] for r in rows},
    })
