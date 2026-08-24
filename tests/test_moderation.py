from datetime import datetime, timedelta

import pytest

from src.db import Database, Member
from src.gateway import AddParticipantError
from src.moderation import apply_sanction, moderate, process_returns
from tests.fakes import FakeGateway


@pytest.fixture
def db():
    return Database(":memory:")


def test_first_infraction_bans_24h():
    member = Member(jid="a@s.whatsapp.net")
    now = datetime(2026, 1, 1, 12, 0)

    apply_sanction(member, now, dry_run=False)

    assert member.kick_count == 1
    assert member.banned_until == now + timedelta(hours=24)


def test_second_infraction_bans_7_days():
    member = Member(jid="a@s.whatsapp.net", kick_count=1, last_infraction_at=datetime(2026, 1, 1))
    now = datetime(2026, 1, 2)

    apply_sanction(member, now, dry_run=False)

    assert member.kick_count == 2
    assert member.banned_until == now + timedelta(days=7)


def test_third_infraction_requires_manual_review():
    member = Member(jid="a@s.whatsapp.net", kick_count=2, last_infraction_at=datetime(2026, 1, 1))
    now = datetime(2026, 1, 2)

    apply_sanction(member, now, dry_run=False)

    assert member.kick_count == 3
    assert member.banned_until is None  # pas de retour automatique


def test_prescription_resets_kick_count_after_90_days():
    member = Member(
        jid="a@s.whatsapp.net",
        kick_count=2,
        last_infraction_at=datetime(2026, 1, 1),
    )
    now = datetime(2026, 1, 1) + timedelta(days=91)

    apply_sanction(member, now, dry_run=False)

    assert member.kick_count == 1  # remis à zéro puis incrémenté
    assert member.banned_until == now + timedelta(hours=24)


def test_prescription_not_triggered_before_90_days():
    member = Member(
        jid="a@s.whatsapp.net",
        kick_count=2,
        last_infraction_at=datetime(2026, 1, 1),
    )
    now = datetime(2026, 1, 1) + timedelta(days=89)

    apply_sanction(member, now, dry_run=False)

    assert member.kick_count == 3


def test_dry_run_never_sets_banned_until_but_still_tracks_tier():
    member = Member(jid="a@s.whatsapp.net")
    now = datetime(2026, 1, 1)

    apply_sanction(member, now, dry_run=True)

    assert member.kick_count == 1
    assert member.banned_until is None


def test_moderate_logs_dm_then_kick_in_order(db):
    gw = FakeGateway()
    now = datetime(2026, 1, 1)

    moderate(db, gw, "group", "a@s.whatsapp.net", "WRONG_NUMBER", "650", now, dry_run=False)

    assert len(gw.dms) == 1
    assert gw.dms[0][0] == "a@s.whatsapp.net"
    assert gw.kicked == ["a@s.whatsapp.net"]
    infractions = db.infractions_for("a@s.whatsapp.net")
    assert len(infractions) == 1
    assert infractions[0]["action"] == "kicked"


def test_moderate_dry_run_touches_nothing_real(db):
    gw = FakeGateway()
    now = datetime(2026, 1, 1)

    moderate(db, gw, "group", "a@s.whatsapp.net", "NO_CAPTION", None, now, dry_run=True)

    assert gw.dms == []
    assert gw.kicked == []
    infractions = db.infractions_for("a@s.whatsapp.net")
    assert infractions[0]["action"] == "dry_run"

    member = db.get_member("a@s.whatsapp.net")
    assert member.banned_until is None
    assert member.kick_count == 1


def test_process_returns_readds_expired_bans(db):
    gw = FakeGateway()
    db.save_member(
        Member(jid="a@s.whatsapp.net", banned_until=datetime(2026, 1, 1))
    )

    process_returns(db, gw, "group", now=datetime(2026, 1, 2))

    assert gw.added == ["a@s.whatsapp.net"]
    assert len(gw.dms) == 1
    member = db.get_member("a@s.whatsapp.net")
    assert member.banned_until is None


def test_process_returns_ignores_still_banned_members(db):
    gw = FakeGateway()
    db.save_member(
        Member(jid="a@s.whatsapp.net", banned_until=datetime(2026, 1, 10))
    )

    process_returns(db, gw, "group", now=datetime(2026, 1, 2))

    assert gw.added == []


def test_process_returns_falls_back_to_invite_link_on_add_failure(db):
    gw = FakeGateway()
    gw.add_participant_fails.add("a@s.whatsapp.net")
    db.save_member(
        Member(jid="a@s.whatsapp.net", banned_until=datetime(2026, 1, 1))
    )

    process_returns(db, gw, "group", now=datetime(2026, 1, 2))

    assert gw.added == []
    assert len(gw.dms) == 1
    assert "chat.whatsapp.com" in gw.dms[0][1]
    member = db.get_member("a@s.whatsapp.net")
    assert member.readd_failed is True
    assert member.banned_until is None
