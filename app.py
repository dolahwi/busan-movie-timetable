"""FastAPI web server for Busan theater timetable."""
import os
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from database import init_db, get_screenings, get_branches, get_movies, get_last_scraped

app = FastAPI(title="부산 영화 상영시간표", version="1.0.0")

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    date: str = None,
    branch: str = None,
    movie: str = None,
):
    """Main timetable page."""
    today = datetime.now()
    if not date:
        date = today.strftime("%Y-%m-%d")

    # Generate 7-day date navigation
    date_nav = []
    for i in range(7):
        d = today + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        day_label = "오늘" if i == 0 else d.strftime("%m/%d")
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        date_nav.append({
            "date": d_str,
            "label": f"{day_label}({weekday_kr})",
            "active": d_str == date,
            "is_weekend": d.weekday() >= 5,
        })

    # Fetch data
    screenings = get_screenings(date, branch=branch, movie=movie)
    branches = get_branches(date)
    movies = get_movies(date)
    last_scraped = get_last_scraped()

    # Group screenings by brand → branch
    grouped = {}
    for s in screenings:
        key = f"{s['theater_brand']} {s['branch_name']}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(s)

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
        },
    )


@app.get("/api/screenings")
async def api_screenings(
    date: str = Query(default=None, description="YYYY-MM-DD"),
    branch: str = Query(default=None),
    movie: str = Query(default=None),
):
    """JSON API for screening data."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    screenings = get_screenings(date, branch=branch, movie=movie)
    return JSONResponse(content={
        "date": date,
        "count": len(screenings),
        "screenings": screenings,
    })
