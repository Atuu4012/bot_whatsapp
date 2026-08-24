"""Chargement de la configuration depuis .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_jid_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(v.strip() for v in value.split(",") if v.strip())


@dataclass(frozen=True)
class Config:
    group_jid: str
    admin_jids: frozenset[str]
    db_path: str
    session_path: str
    dry_run: bool
    grace_period_seconds: int
    caption_grace_period_seconds: int
    tier1_hours: int
    tier2_days: int
    prescription_days: int


def load_config(env_path: str | Path | None = None) -> Config:
    load_dotenv(env_path)
    return Config(
        group_jid=os.environ.get("BOT_GROUP_JID", ""),
        admin_jids=_as_jid_set(os.environ.get("ADMIN_JIDS")),
        db_path=os.environ.get("DB_PATH", "data/beerbot.db"),
        session_path=os.environ.get("SESSION_PATH", "data/session.db"),
        dry_run=_as_bool(os.environ.get("DRY_RUN"), True),
        grace_period_seconds=int(os.environ.get("GRACE_PERIOD_SECONDS", "90")),
        caption_grace_period_seconds=int(os.environ.get("CAPTION_GRACE_PERIOD_SECONDS", "300")),
        tier1_hours=int(os.environ.get("TIER1_HOURS", "24")),
        tier2_days=int(os.environ.get("TIER2_DAYS", "7")),
        prescription_days=int(os.environ.get("PRESCRIPTION_DAYS", "90")),
    )
