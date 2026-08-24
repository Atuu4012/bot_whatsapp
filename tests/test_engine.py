from datetime import datetime, timedelta

import pytest

from src.db import Database
from src.engine import Action, Engine
from src.gateway import IncomingMessage
from tests.fakes import FakeClock, FakeGateway

GROUP = "group@g.us"


def msg(jid, number, message_id="m1", has_image=True, is_system=False, push_name="X"):
    return IncomingMessage(
        message_id=message_id,
        jid=jid,
        push_name=push_name,
        has_image=has_image,
        caption=str(number) if number is not None else None,
        timestamp=datetime(2026, 1, 1),
        is_system=is_system,
    )


@pytest.fixture
def engine():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock)
    return eng, db, gw, clock


def test_accepts_first_correct_beer(engine):
    eng, db, gw, clock = engine

    result = eng.handle(msg("a@s.whatsapp.net", 1))

    assert result == Action.ACCEPTED
    assert db.next_expected_number() == 2


def test_rejects_wrong_number_and_kicks(engine):
    eng, db, gw, clock = engine

    result = eng.handle(msg("a@s.whatsapp.net", 5, message_id="m1"))

    assert result == Action.SANCTIONED
    assert gw.kicked == ["a@s.whatsapp.net"]


def test_dry_run_sanctions_without_touching_the_group():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=True, clock=clock)

    result = eng.handle(msg("a@s.whatsapp.net", 5))

    assert result == Action.SANCTIONED
    assert gw.kicked == []
    assert gw.dms == []


def test_duplicate_message_id_counted_once(engine):
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="dup"))
    result = eng.handle(msg("a@s.whatsapp.net", 1, message_id="dup"))

    assert result == Action.IGNORED_DUPLICATE
    assert db.next_expected_number() == 2  # une seule bière comptée


def test_collision_within_grace_period_is_ignored_not_sanctioned(engine):
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))
    clock.advance(timedelta(seconds=10))
    # Quelqu'un d'autre republie le même numéro 1 juste après.
    result = eng.handle(msg("b@s.whatsapp.net", 1, message_id="m2"))

    assert result == Action.IGNORED_COLLISION
    assert gw.kicked == []


def test_collision_after_grace_period_is_sanctioned(engine):
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))
    clock.advance(timedelta(seconds=200))
    result = eng.handle(msg("b@s.whatsapp.net", 1, message_id="m2"))

    assert result == Action.SANCTIONED
    assert gw.kicked == ["b@s.whatsapp.net"]


def test_system_message_ignored():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    result = eng.handle(msg("system", None, has_image=False, is_system=True))

    assert result == Action.IGNORED_SYSTEM
    assert gw.kicked == []


def test_bot_own_messages_ignored():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False,
        clock=FakeClock(datetime(2026, 1, 1)), bot_jid="bot@s.whatsapp.net",
    )

    result = eng.handle(msg("bot@s.whatsapp.net", 1))

    assert result == Action.IGNORED_BOT


def test_admin_is_never_kicked_but_infraction_is_logged():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False,
        clock=FakeClock(datetime(2026, 1, 1)), admin_jids=frozenset({"admin@s.whatsapp.net"}),
    )

    result = eng.handle(msg("admin@s.whatsapp.net", 5))

    assert result == Action.ADMIN_EXEMPT
    assert gw.kicked == []
    assert len(db.infractions_for("admin@s.whatsapp.net")) == 1


def test_milestone_celebrated_on_acceptance():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    for i in range(1, 500):
        eng.handle(msg("a@s.whatsapp.net", i, message_id=f"m{i}"))

    assert gw.group_msgs == []
    eng.handle(msg("a@s.whatsapp.net", 500, message_id="m500"))
    assert len(gw.group_msgs) == 1
    assert "500" in gw.group_msgs[0]
