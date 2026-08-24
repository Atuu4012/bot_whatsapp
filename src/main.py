"""Point d'entrée : câble les dépendances et démarre le bot.

Le mapping événement neonize -> IncomingMessage (juste avant `client.connect()`
ci-dessous) doit être validé contre un groupe de test avant tout usage réel —
voir §13.4 du plan. Tout le reste (Engine, moderation, milestones, stats) est
testé indépendamment de neonize via FakeGateway (tests/fakes.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from src.config import Config, load_config
from src.db import Database
from src.engine import Engine
from src.gateway import NeonizeGateway, WhatsAppGateway
from src.moderation import process_returns
from src.stats import weekly_recap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("beerbot")


def build_engine(config: Config, db: Database, gateway: WhatsAppGateway) -> Engine:
    return Engine(
        db=db,
        gateway=gateway,
        group=config.group_jid,
        dry_run=config.dry_run,
        admin_jids=config.admin_jids,
        grace_period=timedelta(seconds=config.grace_period_seconds),
        tiers={1: timedelta(hours=config.tier1_hours), 2: timedelta(days=config.tier2_days)},
        prescription=timedelta(days=config.prescription_days),
    )


def start_scheduler(db: Database, gateway: WhatsAppGateway, config: Config) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()

    # Balaie tous les banned_until expirés, pas seulement la dernière heure :
    # rattrape aussi ce qui a expiré pendant une coupure du bot (§11).
    scheduler.add_job(
        lambda: process_returns(db, gateway, config.group_jid, datetime.now()),
        "interval",
        hours=1,
        id="process_returns",
    )
    scheduler.add_job(
        lambda: gateway.send_group(
            config.group_jid, weekly_recap(db, datetime.now() - timedelta(days=7))
        ),
        "cron",
        day_of_week="sun",
        hour=20,
        id="weekly_recap",
    )

    scheduler.start()
    return scheduler


def main() -> None:
    config = load_config()
    if not config.group_jid:
        raise SystemExit("BOT_GROUP_JID manquant dans .env")
    if config.dry_run:
        log.warning("DRY_RUN actif : aucune sanction réelle ne sera appliquée.")

    db = Database(config.db_path)
    gateway = NeonizeGateway(config.session_path)
    engine = build_engine(config, db, gateway)
    start_scheduler(db, gateway, config)

    # TODO (§13.4, phase WhatsApp) : brancher ici le handler d'événements
    # neonize réel. Il doit construire un IncomingMessage à partir de
    # l'événement reçu (message_id, jid expéditeur, push_name, has_image,
    # caption, timestamp, is_system) puis appeler engine.handle(msg). La
    # forme exacte des événements neonize (noms des champs) doit être
    # validée sur un groupe de test avant tout branchement sur le vrai groupe.
    log.info("BeerBot prêt (groupe=%s, dry_run=%s)", config.group_jid, config.dry_run)
    gateway.client.connect()


if __name__ == "__main__":
    main()
