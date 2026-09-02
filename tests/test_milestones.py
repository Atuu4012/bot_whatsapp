from datetime import datetime

import pytest

from src.db import Database, Member, Beer
from src.milestones import check_and_celebrate, is_milestone
from tests.fakes import FakeGateway


@pytest.mark.parametrize(
    "n,expected",
    [
        (1000, True),
        (2500, True),
        (500, True),  # ROUND_EVERY
        (1500, True),  # ROUND_EVERY
        (999, False),
        (1001, False),
    ],
)
def test_is_milestone(n, expected):
    assert is_milestone(n) is expected


@pytest.fixture
def db():
    database = Database(":memory:")
    database.save_member(Member(jid="a@s.whatsapp.net", push_name="Arthur"))
    return database


def test_milestone_posts_once(db):
    gw = FakeGateway()
    now = datetime(2026, 1, 1)

    posted = check_and_celebrate(1000, "a@s.whatsapp.net", db, gw, "group", now)

    assert posted is True
    assert len(gw.group_msgs) == 1
    assert "1000" in gw.group_msgs[0]


def test_non_milestone_does_not_post(db):
    gw = FakeGateway()
    posted = check_and_celebrate(1001, "a@s.whatsapp.net", db, gw, "group", datetime(2026, 1, 1))

    assert posted is False
    assert gw.group_msgs == []


def test_milestone_not_repeated_after_restart(db):
    """Simule un redémarrage : milestones_hit doit empêcher le doublon."""
    gw1 = FakeGateway()
    check_and_celebrate(1000, "a@s.whatsapp.net", db, gw1, "group", datetime(2026, 1, 1))

    # Nouveau gateway = comme si le process avait redémarré, mais la même db.
    gw2 = FakeGateway()
    posted_again = check_and_celebrate(1000, "a@s.whatsapp.net", db, gw2, "group", datetime(2026, 1, 2))

    assert posted_again is False
    assert gw2.group_msgs == []


def test_celebration_message_includes_leaderboard(db):
    db.insert_beer(Beer(number=1, jid="a@s.whatsapp.net", posted_at=datetime(2026, 1, 1), source="live"))
    gw = FakeGateway()

    check_and_celebrate(1000, "a@s.whatsapp.net", db, gw, "group", datetime(2026, 1, 1))

    assert "Arthur" in gw.group_msgs[0]


def test_milestone_silencieux_en_dry_run(db):
    """Mode observation (§8.4) : le bot ne parle pas dans le groupe."""
    gw = FakeGateway()
    now = datetime(2026, 1, 1)

    posted = check_and_celebrate(1000, "a@s.whatsapp.net", db, gw, "group", now, dry_run=True)

    assert posted is False
    assert gw.group_msgs == []
    # Le palier n'est pas marqué comme fêté : il ne l'a pas été.
    assert db.milestone_hit(1000) is False
