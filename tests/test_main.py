import logging
from datetime import datetime, timedelta

from src.config import Config
from src.db import Database
from src.gateway import IncomingMessage
from src.main import build_engine, build_message_handler, start_scheduler
from tests.fakes import FakeGateway


def _config(**overrides) -> Config:
    defaults = dict(
        group_jid="group@g.us",
        admin_jids=frozenset({"admin@s.whatsapp.net"}),
        db_path=":memory:",
        session_path=":memory:",
        dry_run=True,
        grace_period_seconds=90,
        caption_grace_period_seconds=300,
        gap_warning_delay_seconds=30,
        tier1_hours=24,
        tier2_days=7,
        prescription_days=90,
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_build_engine_wires_config_into_engine():
    config = _config(
        grace_period_seconds=42, caption_grace_period_seconds=123,
        tier1_hours=1, tier2_days=2, prescription_days=3,
    )
    db = Database(":memory:")
    gw = FakeGateway()

    engine = build_engine(config, db, gw)

    assert engine.group == "group@g.us"
    assert engine.dry_run is True
    assert engine.admin_jids == frozenset({"admin@s.whatsapp.net"})
    assert engine.grace_period == timedelta(seconds=42)
    assert engine.caption_grace_period == timedelta(seconds=123)
    assert engine.gap_warning_delay == timedelta(seconds=30)
    assert engine.tiers == {1: timedelta(hours=1), 2: timedelta(days=2)}
    assert engine.prescription == timedelta(days=3)


def test_start_scheduler_registers_expected_jobs():
    config = _config()
    db = Database(":memory:")
    gw = FakeGateway()
    engine = build_engine(config, db, gw)

    scheduler = start_scheduler(db, gw, config, engine)
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert job_ids == {
            "process_returns",
            "sweep_pending_captions",
            "sweep_pending_warnings",
            "weekly_recap",
        }
    finally:
        scheduler.shutdown(wait=False)


def test_weekly_recap_ne_poste_rien_en_dry_run():
    """Une récap surgissant un dimanche soir trahirait un bot en observation."""
    config = _config(dry_run=True)
    db = Database(":memory:")
    gw = FakeGateway()
    engine = build_engine(config, db, gw)

    scheduler = start_scheduler(db, gw, config, engine)
    try:
        job = scheduler.get_job("weekly_recap")
        job.func()
    finally:
        scheduler.shutdown(wait=False)

    assert gw.group_msgs == []


def test_weekly_recap_poste_hors_dry_run():
    config = _config(dry_run=False)
    db = Database(":memory:")
    gw = FakeGateway()
    engine = build_engine(config, db, gw)

    scheduler = start_scheduler(db, gw, config, engine)
    try:
        scheduler.get_job("weekly_recap").func()
    finally:
        scheduler.shutdown(wait=False)

    assert len(gw.group_msgs) == 1


def test_le_handler_traite_et_journalise_la_decision(caplog):
    """Un bot en observation qui ne journalise rien ne s'observe pas."""
    db = Database(":memory:")
    gw = FakeGateway()
    engine = build_engine(_config(dry_run=True), db, gw)

    with caplog.at_level(logging.INFO, logger="beerbot"):
        build_message_handler(engine)(
            IncomingMessage(
                message_id="m1", jid="a@s.whatsapp.net", push_name="Alix",
                has_image=True, caption="1", timestamp=datetime(2026, 1, 1),
            )
        )

    assert db.next_expected_number() == 2
    assert "Alix" in caplog.text
    assert "ACCEPTED" in caplog.text


def test_apscheduler_ne_noie_pas_le_journal():
    """Deux lignes toutes les 15 s rendraient le journal du soir illisible."""
    import src.main  # noqa: F401 — l'import configure la journalisation

    assert logging.getLogger("apscheduler").level == logging.WARNING
