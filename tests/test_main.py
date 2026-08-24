from datetime import timedelta

from src.config import Config
from src.db import Database
from src.main import build_engine, start_scheduler
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
        assert job_ids == {"process_returns", "sweep_pending_captions", "weekly_recap"}
    finally:
        scheduler.shutdown(wait=False)
