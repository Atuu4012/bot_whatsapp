"""Calculs de classements, rythmes et récap périodique."""

from __future__ import annotations

from datetime import datetime

from src.db import Database


def leaderboard(db: Database) -> list[dict]:
    return [dict(row) for row in db.leaderboard()]


def posts_by_weekday(db: Database) -> dict[int, int]:
    """0 = dimanche ... 6 = samedi (convention strftime %w de SQLite)."""
    rows = db.conn.execute(
        "SELECT strftime('%w', posted_at) AS jour, COUNT(*) AS n "
        "FROM beers GROUP BY jour"
    ).fetchall()
    return {int(row["jour"]): row["n"] for row in rows}


def posts_by_hour(db: Database) -> dict[int, int]:
    rows = db.conn.execute(
        "SELECT strftime('%H', posted_at) AS heure, COUNT(*) AS n "
        "FROM beers GROUP BY heure ORDER BY n DESC"
    ).fetchall()
    return {int(row["heure"]): row["n"] for row in rows}


def longest_streak(db: Database, jid: str) -> int:
    """Plus longue série de numéros consécutifs postés par ce membre."""
    rows = db.conn.execute(
        "SELECT number FROM beers WHERE jid = ? ORDER BY number", (jid,)
    ).fetchall()
    numbers = [r["number"] for r in rows]
    if not numbers:
        return 0

    best = current = 1
    for prev, curr in zip(numbers, numbers[1:]):
        current = current + 1 if curr == prev + 1 else 1
        best = max(best, current)
    return best


def weekly_recap(db: Database, since: datetime) -> str:
    row = db.conn.execute(
        "SELECT COUNT(*) AS n FROM beers WHERE posted_at >= ?", (since.isoformat(),)
    ).fetchone()
    count = row["n"]

    top = leaderboard(db)[:3]
    top_text = ", ".join(f"{row['name']} ({row['total']})" for row in top) or "personne"

    return f"📊 Cette semaine : {count} bières.\nTop général : {top_text}\n\nSanté 🍻"
