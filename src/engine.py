"""Orchestration : reçoit un message, décide, agit.

C'est le seul module qui connaît la règle métier complète — validation,
fenêtre de grâce pour les collisions, photo sans légende suivie du numéro
juste après, exceptions (système/admin/bot), idempotence, et le
déclenchement de la modération / des paliers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum, auto

from src import milestones, moderation
from src.db import Beer, Database, Member
from src.gateway import IncomingMessage, WhatsAppGateway

DEFAULT_GRACE_PERIOD = timedelta(seconds=90)
DEFAULT_CAPTION_GRACE_PERIOD = timedelta(minutes=5)


class Action(Enum):
    ACCEPTED = auto()
    IGNORED_SYSTEM = auto()
    IGNORED_BOT = auto()
    IGNORED_DUPLICATE = auto()
    IGNORED_COLLISION = auto()
    IGNORED_REVOKED = auto()
    AWAITING_CAPTION = auto()
    ADMIN_EXEMPT = auto()
    SANCTIONED = auto()


class Clock:
    """Horloge par défaut, adossée à l'heure système."""

    def now(self) -> datetime:
        return datetime.now()


@dataclass
class _PendingPhoto:
    message_id: str | None
    posted_at: datetime
    raw_caption: str | None


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
    caption_grace_period: timedelta = DEFAULT_CAPTION_GRACE_PERIOD
    tiers: dict[int, timedelta] | None = None
    prescription: timedelta | None = None
    _pending: dict[str, _PendingPhoto] = field(default_factory=dict, init=False, repr=False)
    _consumed_followups: set[str] = field(default_factory=set, init=False, repr=False)

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

        if msg.message_id and msg.message_id in self._consumed_followups:
            return Action.IGNORED_DUPLICATE

        if msg.message_id and self.db.get_beer_by_message_id(msg.message_id):
            return Action.IGNORED_DUPLICATE

        self._ensure_member(msg)
        now = self.clock.now()
        is_admin = msg.jid in self.admin_jids

        # Photo envoyée sans légende, puis le numéro arrive juste après dans
        # un message à part (oubli) : on tente de raccrocher ce texte à la
        # photo en attente avant de suivre le flux normal.
        if not msg.has_image and not is_admin and msg.jid in self._pending:
            completed = self._try_complete_pending(msg, now)
            if completed is not None:
                return completed

        from src.validator import validate  # import tardif : évite un cycle au chargement

        expected = self.db.next_expected_number()
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

        if is_admin:
            # Les admins ont le droit de parler librement dans le groupe :
            # un message non conforme de leur part n'est même pas une
            # infraction, juste un message ignoré par le compteur.
            return Action.ADMIN_EXEMPT

        if msg.has_image and verdict.reason == "NO_CAPTION" and msg.jid not in self._pending:
            # Oubli de légende : on laisse une chance d'envoyer le numéro
            # dans un message séparé avant de sanctionner.
            self._pending[msg.jid] = _PendingPhoto(
                message_id=msg.message_id, posted_at=now, raw_caption=msg.caption
            )
            return Action.AWAITING_CAPTION

        moderation.moderate(
            self.db, self.gateway, self.group, msg.jid, verdict.reason, msg.caption, now, self.dry_run,
            tiers=self.tiers, prescription=self.prescription,
        )
        # Déjà sanctionné pour ce tour : évite qu'une photo en attente plus
        # ancienne ne déclenche une seconde sanction au prochain sweep.
        self._pending.pop(msg.jid, None)
        return Action.SANCTIONED

    def sweep_pending_captions(self, now: datetime) -> list[str]:
        """À appeler périodiquement (job planifié) : sanctionne les photos
        sans légende dont la fenêtre de rattrapage a expiré sans qu'un
        numéro ne suive. Retourne les jid sanctionnés."""

        expired_jids = [
            jid for jid, pending in self._pending.items()
            if now - pending.posted_at > self.caption_grace_period
        ]

        for jid in expired_jids:
            pending = self._pending.pop(jid)
            if jid in self.admin_jids:
                continue
            moderation.moderate(
                self.db, self.gateway, self.group, jid, "NO_CAPTION", pending.raw_caption, now, self.dry_run,
                tiers=self.tiers, prescription=self.prescription,
            )

        return expired_jids

    def _try_complete_pending(self, msg: IncomingMessage, now: datetime) -> Action | None:
        pending = self._pending[msg.jid]

        if now - pending.posted_at > self.caption_grace_period:
            del self._pending[msg.jid]
            return None  # expiré : le sweep périodique s'en charge, ce message suit le flux normal

        from src.validator import validate

        expected = self.db.next_expected_number()
        # On valide ce texte comme s'il était la légende de la photo en
        # attente, sans toucher au message d'origine.
        as_caption = replace(msg, has_image=True)
        verdict = validate(as_caption, expected)
        if not verdict.ok:
            return None  # ne correspond pas (encore) : la photo reste en attente

        del self._pending[msg.jid]
        if msg.message_id:
            self._consumed_followups.add(msg.message_id)

        for number in verdict.numbers:
            self.db.insert_beer(
                Beer(
                    number=number,
                    jid=msg.jid,
                    message_id=pending.message_id,
                    posted_at=now,
                    source="live",
                )
            )
            milestones.check_and_celebrate(number, msg.jid, self.db, self.gateway, self.group, now)
        return Action.ACCEPTED

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
