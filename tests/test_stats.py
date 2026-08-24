from datetime import datetime

import pytest

from src.db import Beer, Database, Member
from src.stats import leaderboard, longest_streak, weekly_recap


@pytest.fixture
def db():
    database = Database(":memory:")
    database.save_member(Member(jid="a@s.whatsapp.net", push_name="Arthur"))
    database.save_member(Member(jid="b@s.whatsapp.net", push_name="Marie"))
    return database


def _post(db, jid, number, day=1):
    db.insert_beer(Beer(number=number, jid=jid, posted_at=datetime(2026, 1, day), source="live"))


def test_leaderboard_orders_by_total_desc(db):
    _post(db, "a@s.whatsapp.net", 1)
    _post(db, "a@s.whatsapp.net", 2)
    _post(db, "b@s.whatsapp.net", 3)

    rows = leaderboard(db)

    assert rows[0]["name"] == "Arthur"
    assert rows[0]["total"] == 2
    assert rows[1]["name"] == "Marie"
    assert rows[1]["total"] == 1


def test_longest_streak_counts_consecutive_numbers(db):
    _post(db, "a@s.whatsapp.net", 1)
    _post(db, "a@s.whatsapp.net", 2)
    _post(db, "b@s.whatsapp.net", 3)
    _post(db, "a@s.whatsapp.net", 4)
    _post(db, "a@s.whatsapp.net", 5)
    _post(db, "a@s.whatsapp.net", 6)

    assert longest_streak(db, "a@s.whatsapp.net") == 3  # 4, 5, 6
    assert longest_streak(db, "b@s.whatsapp.net") == 1


def test_longest_streak_zero_for_unknown_member(db):
    assert longest_streak(db, "ghost@s.whatsapp.net") == 0


def test_weekly_recap_counts_only_since_given_date(db):
    _post(db, "a@s.whatsapp.net", 1, day=1)
    _post(db, "a@s.whatsapp.net", 2, day=10)

    recap = weekly_recap(db, since=datetime(2026, 1, 5))

    assert "1 bières" in recap
    assert "Arthur" in recap
