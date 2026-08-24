"""SQLite database helpers for storing and querying screening data."""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timetable.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS screenings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    theater_brand TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    movie_title TEXT NOT NULL,
    screen_name TEXT DEFAULT '',
    format TEXT DEFAULT '',
    start_time TEXT DEFAULT '',
    end_time TEXT DEFAULT '',
    remaining_seats TEXT DEFAULT '',
    booking_url TEXT DEFAULT '',
    scraped_at TEXT NOT NULL
);
"""

CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_date ON screenings(date);",
    "CREATE INDEX IF NOT EXISTS idx_branch ON screenings(branch_name);",
    "CREATE INDEX IF NOT EXISTS idx_movie ON screenings(movie_title);",
    "CREATE INDEX IF NOT EXISTS idx_date_branch ON screenings(date, branch_name);",
]


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database schema."""
    conn = get_connection()
    try:
        conn.execute(CREATE_TABLE_SQL)
        for idx_sql in CREATE_INDEX_SQL:
            conn.execute(idx_sql)
        conn.commit()
    finally:
        conn.close()


def save_screenings(screenings: list, scraped_at: str = None):
    """Save a list of Screening objects to the database.

    Clears existing data for the same date+branch before inserting.
    """
    if not screenings:
        return

    if scraped_at is None:
        scraped_at = datetime.now().isoformat()

    conn = get_connection()
    try:
        # Group screenings by (date, branch_name) and clear old data per group
        groups = set()
        for s in screenings:
            groups.add((s.date, s.branch_name))

        for date, branch in groups:
            conn.execute(
                "DELETE FROM screenings WHERE date = ? AND branch_name = ?",
                (date, branch),
            )

        # Insert new screenings
        conn.executemany(
            """
            INSERT INTO screenings
                (date, theater_brand, branch_name, movie_title, screen_name,
                 format, start_time, end_time, remaining_seats, booking_url, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.date, s.theater_brand, s.branch_name, s.movie_title,
                    s.screen_name, s.format, s.start_time, s.end_time,
                    s.remaining_seats, s.booking_url, scraped_at,
                )
                for s in screenings
            ],
        )
        conn.commit()
    finally:
        conn.close()


def get_screenings(date: str, branch: str = None, movie: str = None) -> list[dict]:
    """Query screenings for a given date with optional filters."""
    conn = get_connection()
    try:
        query = "SELECT * FROM screenings WHERE date = ?"
        params: list = [date]

        if branch:
            query += " AND branch_name = ?"
            params.append(branch)

        if movie:
            query += " AND movie_title LIKE ?"
            params.append(f"%{movie}%")

        query += " ORDER BY theater_brand, branch_name, start_time"

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_branches(date: str = None) -> list[str]:
    """Get distinct branch names, optionally filtered by date."""
    conn = get_connection()
    try:
        if date:
            rows = conn.execute(
                "SELECT DISTINCT branch_name FROM screenings WHERE date = ? ORDER BY branch_name",
                (date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT branch_name FROM screenings ORDER BY branch_name"
            ).fetchall()
        return [row["branch_name"] for row in rows]
    finally:
        conn.close()


def get_movies(date: str = None) -> list[str]:
    """Get distinct movie titles, optionally filtered by date."""
    conn = get_connection()
    try:
        if date:
            rows = conn.execute(
                "SELECT DISTINCT movie_title FROM screenings WHERE date = ? ORDER BY movie_title",
                (date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT movie_title FROM screenings ORDER BY movie_title"
            ).fetchall()
        return [row["movie_title"] for row in rows]
    finally:
        conn.close()


def get_available_dates() -> list[str]:
    """Get all dates that have screening data."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM screenings ORDER BY date"
        ).fetchall()
        return [row["date"] for row in rows]
    finally:
        conn.close()


def get_last_scraped() -> str | None:
    """Get the most recent scrape timestamp."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(scraped_at) as last FROM screenings"
        ).fetchone()
        return row["last"] if row else None
    finally:
        conn.close()


def clear_old_data(before_date: str):
    """Delete screening data older than the given date."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM screenings WHERE date < ?", (before_date,))
        conn.commit()
    finally:
        conn.close()
