"""Point d'entrée : câble les dépendances et démarre le bot.

Le mapping événement neonize -> IncomingMessage vit dans
`gateway.to_incoming`, écrit d'après les événements réellement observés sur
un groupe de test (§13.4) — voir `scripts/probe_events.py` pour les
reconstater. Tout le reste (Engine, moderation, milestones, stats) est testé
indépendamment de neonize via FakeGateway (tests/fakes.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from src.config import Config, load_config
from src.db import Database
from src.engine import Engine
from src.gateway import IncomingMessage, NeonizeGateway, WhatsAppGateway
from src.moderation import process_returns
from src.stats import weekly_recap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# APScheduler écrit deux lignes à chaque exécution : avec un balayage toutes
# les 15 secondes, ça noie le journal des décisions qu'on relit le soir
# pendant l'observation. On ne veut de lui que ses erreurs.
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("beerbot")


def build_engine(config: Config, db: Database, gateway: WhatsAppGateway) -> Engine:
    return Engine(
        db=db,
        gateway=gateway,
        group=config.group_jid,
        dry_run=config.dry_run,
        admin_jids=config.admin_jids,
        grace_period=timedelta(seconds=config.grace_period_seconds),
        caption_grace_period=timedelta(seconds=config.caption_grace_period_seconds),
        gap_warning_delay=timedelta(seconds=config.gap_warning_delay_seconds),
        tiers={1: timedelta(hours=config.tier1_hours), 2: timedelta(days=config.tier2_days)},
        prescription=timedelta(days=config.prescription_days),
    )


def build_message_handler(engine: Engine) -> Callable[[IncomingMessage], None]:
    """Traite un message et journalise la décision prise.

    C'est ce journal qu'on relit chaque soir pendant les deux semaines
    d'observation (§14) : sans lui, un bot en DRY_RUN travaille en silence
    et il n'y a rien à relire. Il évite aussi d'avoir à faire tourner
    `scripts/probe_events.py` en parallèle — ce qui est impossible, les deux
    partageraient la même session WhatsApp.
    """

    def handle(msg: IncomingMessage) -> None:
        action = engine.handle(msg)
        log.info(
            "%s de %s | légende=%r -> %s",
            "photo" if msg.has_image else "texte",
            msg.push_name or msg.jid,
            msg.caption,
            action.name,
        )

    return handle


def start_scheduler(
    db: Database, gateway: WhatsAppGateway, config: Config, engine: Engine
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()

    # Balaie tous les banned_until expirés, pas seulement la dernière heure :
    # rattrape aussi ce qui a expiré pendant une coupure du bot (§11).
    scheduler.add_job(
        lambda: process_returns(db, gateway, config.group_jid, datetime.now()),
        "interval",
        hours=1,
        id="process_returns",
    )
    # Fréquent : la fenêtre de rattrapage "photo sans légende" ne dure que
    # quelques minutes, il faut la balayer bien plus souvent que les bans.
    scheduler.add_job(
        lambda: engine.sweep_pending_captions(datetime.now()),
        "interval",
        minutes=1,
        id="sweep_pending_captions",
    )
    def send_weekly_recap() -> None:
        # Même règle que les sanctions et les paliers : en mode observation,
        # le bot n'écrit rien dans le groupe (§8.4). Sinon une récap
        # surgirait un dimanche soir dans un groupe qui n'a pas encore été
        # prévenu que le bot existe.
        text = weekly_recap(db, datetime.now() - timedelta(days=7))
        if config.dry_run:
            log.info("DRY_RUN : récap hebdo non postée :\n%s", text)
            return
        gateway.send_group(config.group_jid, text)

    # Court : le délai de grâce avant d'avertir d'un numéro sauté se compte en
    # dizaines de secondes, un balayage à la minute le doublerait.
    scheduler.add_job(
        lambda: engine.sweep_pending_warnings(datetime.now()),
        "interval",
        seconds=15,
        id="sweep_pending_warnings",
    )
    scheduler.add_job(
        send_weekly_recap,
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
    start_scheduler(db, gateway, config, engine)

    gateway.on_message(build_message_handler(engine))

    log.info("BeerBot prêt (groupe=%s, dry_run=%s)", config.group_jid, config.dry_run)
    gateway.connect()


if __name__ == "__main__":
    main()
