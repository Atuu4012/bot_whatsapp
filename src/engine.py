"""Orchestration : reçoit un message, décide, agit.

C'est le seul module qui connaît la règle métier complète — validation,
fenêtre de grâce pour les collisions, exceptions (système/admin/bot),
idempotence, et le déclenchement de la modération / des paliers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto

from src import milestones, moderation
from src.db import Beer, Database, Member
from src.gateway import IncomingMessage, WhatsAppGateway

DEFAULT_GRACE_PERIOD = timedelta(seconds=90)


class Action(Enum):
    ACCEPTED = auto()
    IGNORED_SYSTEM = auto()
    IGNORED_BOT = auto()
    IGNORED_DUPLICATE = auto()
    IGNORED_COLLISION = auto()
    IGNORED_REVOKED = auto()
    ADMIN_EXEMPT = auto()
    SANCTIONED = auto()


class Clock:
    """Horloge par défaut, adossée à l'heure système."""

    def now(self) -> datetime:
        return datetime.now()


@dataclass
class Engine:
    db: Database
    gateway: WhatsAppGateway
    group: str
    dry_run: bool = True
    clock: Clock | None = None
    admin_jids: frozenset[str] = frozenset()
    bot_jid: str | None = None
    grace_period: timedelta = DEFAULT_GRACE_PERIOD
    tiers: dict[int, timedelta] | None = None
    prescription: timedelta | None = None

    def __post_init__(self) -> None:
        if self.clock is None:
            self.clock = Clock()

    def handle(self, msg: IncomingMessage) -> Action:
        if msg.is_system:
            return Action.IGNORED_SYSTEM

        if msg.is_revoked:
            # L'auteur a supprimé son propre message (ex: il a posté un
            # numéro en conflit puis corrige avec un nouveau message). Rien
            # à traiter : ce n'est ni une bière ni une infraction.
            return Action.IGNORED_REVOKED

        if self.bot_jid and msg.jid == self.bot_jid:
            return Action.IGNORED_BOT

        if msg.message_id and self.db.get_beer_by_message_id(msg.message_id):
            return Action.IGNORED_DUPLICATE

        self._ensure_member(msg)

        now = self.clock.now()
        expected = self.db.next_expected_number()

        from src.validator import validate  # import tardif : évite un cycle au chargement

        verdict = validate(msg, expected)

        if verdict.ok:
            # Une légende peut lister plusieurs numéros consécutifs
            # ("658 659 660") quand plusieurs bières sont rattrapées d'un
            # coup dans une seule photo : une ligne par numéro.
            for number in verdict.numbers:
                self.db.insert_beer(
                    Beer(
                        number=number,
                        jid=msg.jid,
                        message_id=msg.message_id,
                        posted_at=now,
                        source="live",
                    )
                )
                milestones.check_and_celebrate(number, msg.jid, self.db, self.gateway, self.group, now)
            return Action.ACCEPTED

        if self._is_collision(verdict, expected, now):
            return Action.IGNORED_COLLISION

        if msg.jid in self.admin_jids:
            # Les admins ont le droit de parler librement dans le groupe :
            # un message non conforme de leur part n'est même pas une
            # infraction, juste un message ignoré par le compteur.
            return Action.ADMIN_EXEMPT

        moderation.moderate(
            self.db, self.gateway, self.group, msg.jid, verdict.reason, msg.caption, now, self.dry_run,
            tiers=self.tiers, prescription=self.prescription,
        )
        return Action.SANCTIONED

    def _is_collision(self, verdict, expected: int, now: datetime) -> bool:
        """Deux personnes postent le même numéro à quelques secondes
        d'écart : la seconde ne doit pas être sanctionnée pour ça."""

        if verdict.reason != "WRONG_NUMBER" or verdict.number != expected - 1:
            return False
        last = self.db.last_beer()
        return bool(last and (now - last.posted_at) < self.grace_period)

    def _ensure_member(self, msg: IncomingMessage) -> Member:
        member = self.db.get_member(msg.jid)
        if member is None:
            member = Member(jid=msg.jid, push_name=msg.push_name, joined_at=self.clock.now())
            self.db.save_member(member)
        elif msg.push_name and member.push_name != msg.push_name:
            member.push_name = msg.push_name
            self.db.save_member(member)
        return member
