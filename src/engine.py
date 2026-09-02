"""Orchestration : reçoit un message, décide, agit.

C'est le seul module qui connaît la règle métier complète — validation,
fenêtre de grâce pour les collisions, photo dont le numéro ne correspond
pas (légende absente ou fausse) corrigée par le message suivant,
exceptions (système/admin/bot), idempotence, et le déclenchement de la
modération / des paliers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum, auto

from src import identity, milestones, moderation
from src.db import PLACEHOLDER_JID, Beer, Database, Member
from src.gateway import IncomingMessage, WhatsAppGateway

log = logging.getLogger(__name__)

DEFAULT_GRACE_PERIOD = timedelta(seconds=90)
DEFAULT_CAPTION_GRACE_PERIOD = timedelta(minutes=5)
# Un numéro sauté se remarque souvent tout seul : on laisse à l'auteur le
# temps de corriger sa légende avant de lui écrire quoi que ce soit.
DEFAULT_GAP_WARNING_DELAY = timedelta(seconds=30)


class Action(Enum):
    ACCEPTED = auto()
    IGNORED_SYSTEM = auto()
    IGNORED_BOT = auto()
    IGNORED_DUPLICATE = auto()
    IGNORED_COLLISION = auto()
    ACCEPTED_WITH_GAP = auto()
    CORRECTED = auto()
    IGNORED_REVOKED = auto()
    AWAITING_CAPTION = auto()
    ADMIN_EXEMPT = auto()
    SANCTIONED = auto()


class Clock:
    """Horloge par défaut, adossée à l'heure système."""

    def now(self) -> datetime:
        return datetime.now()


@dataclass
class _PendingWarning:
    jid: str
    missing: list[int]
    numbers: tuple[int, ...]
    caption: str | None
    posted_at: datetime


@dataclass
class _PendingPhoto:
    message_id: str | None
    posted_at: datetime
    raw_caption: str | None
    reason: str


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
    gap_warning_delay: timedelta = DEFAULT_GAP_WARNING_DELAY
    tiers: dict[int, timedelta] | None = None
    prescription: timedelta | None = None
    _pending: dict[str, _PendingPhoto] = field(default_factory=dict, init=False, repr=False)
    _consumed_followups: set[str] = field(default_factory=set, init=False, repr=False)
    _pending_warnings: dict[str, _PendingWarning] = field(
        default_factory=dict, init=False, repr=False
    )

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

        self._ensure_member(msg)
        now = self.clock.now()
        is_admin = msg.jid in self.admin_jids

        deja_comptees = self.db.beers_for_message(msg.message_id)
        if deja_comptees:
            return self._try_correction(msg, deja_comptees, now)

        # Photo dont le numéro ne correspond pas (légende absente, tapée de
        # travers, corrigée après coup...) : si le message suivant est un
        # texte du même auteur, on tente de raccrocher avant de suivre le
        # flux normal.
        if not msg.has_image and not is_admin and msg.jid in self._pending:
            completed = self._try_complete_pending(msg, now)
            if completed is not None:
                return completed

        from src.validator import validate  # import tardif : évite un cycle au chargement

        expected = self.db.next_expected_number()
        verdict = validate(msg, expected)

        if verdict.ok:
            self._clear_pending_for(msg)
            self._record_beers(verdict.numbers, msg.jid, msg.message_id, now)
            return Action.ACCEPTED

        if verdict.reason == "NUMBER_AHEAD":
            # Un numéro sauté ne casse pas la chaîne : on comble le trou par
            # un « - », la bière est comptée, et l'auteur est prévenu qu'il
            # peut encore corriger. Personne n'est sanctionné pour ça.
            self._clear_pending_for(msg)
            return self._accept_gap(msg, verdict.numbers, msg.message_id, expected, now)

        if self._is_collision(verdict, expected, now):
            return Action.IGNORED_COLLISION

        if is_admin:
            # Les admins ont le droit de parler librement dans le groupe :
            # un message non conforme de leur part n'est même pas une
            # infraction, juste un message ignoré par le compteur.
            return Action.ADMIN_EXEMPT

        if msg.has_image and msg.jid not in self._pending:
            # Légende absente ou incorrecte : on laisse une chance d'envoyer
            # le bon numéro dans un message séparé avant de sanctionner.
            self._pending[msg.jid] = _PendingPhoto(
                message_id=msg.message_id, posted_at=now, raw_caption=msg.caption, reason=verdict.reason
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
        dont la fenêtre de rattrapage a expiré sans correction valide.
        Retourne les jid sanctionnés."""

        expired_jids = [
            jid for jid, pending in self._pending.items()
            if now - pending.posted_at > self.caption_grace_period
        ]

        for jid in expired_jids:
            pending = self._pending.pop(jid)
            if jid in self.admin_jids:
                continue
            moderation.moderate(
                self.db, self.gateway, self.group, jid, pending.reason, pending.raw_caption, now, self.dry_run,
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
        if not verdict.ok and verdict.reason != "NUMBER_AHEAD":
            return None  # ne correspond pas (encore) : la photo reste en attente

        del self._pending[msg.jid]
        if msg.message_id:
            self._consumed_followups.add(msg.message_id)

        if verdict.reason == "NUMBER_AHEAD":
            # Même règle que pour une légende directe : le numéro envoyé
            # après coup a beau sauter un cran, il complète bien la photo.
            return self._accept_gap(msg, verdict.numbers, pending.message_id, expected, now)

        self._record_beers(verdict.numbers, msg.jid, pending.message_id, now)
        return Action.ACCEPTED

    def _record_beers(
        self, numbers: tuple[int, ...], jid: str, message_id: str | None, now: datetime
    ) -> None:
        """Enregistre une ou plusieurs bières nées du même message.

        Une légende peut lister plusieurs numéros consécutifs ("658 659 660")
        quand plusieurs bières sont rattrapées d'un coup dans une photo : une
        ligne par numéro, et un palier vérifié à chaque fois.
        """
        for number in numbers:
            self.db.insert_beer(
                Beer(
                    number=number,
                    jid=jid,
                    message_id=message_id,
                    posted_at=now,
                    source="live",
                )
            )
            milestones.check_and_celebrate(
                number, jid, self.db, self.gateway, self.group, now, self.dry_run
            )

    def _accept_gap(
        self,
        msg: IncomingMessage,
        numbers: tuple[int, ...],
        message_id: str | None,
        expected: int,
        now: datetime,
    ) -> Action:
        """Compte la bière, comble ce qui manque, prévient l'auteur.

        L'avertissement ne parle que du saut commis ici : `restore_sequence`
        peut par ailleurs reboucher de vieux trous, qui ne regardent pas
        l'auteur du jour.
        """
        sautes = list(range(expected, numbers[0]))
        self._record_beers(numbers, msg.jid, message_id, now)
        self.db.restore_sequence(now)
        # L'avertissement attend : beaucoup se rendent compte de leur saut
        # tout seuls et corrigent leur légende dans la foulée. Leur écrire
        # tout de suite reviendrait à sermonner quelqu'un déjà en train de
        # réparer. Le balayage périodique l'enverra si rien ne bouge.
        self._pending_warnings[message_id or f"sans-id-{numbers[0]}"] = _PendingWarning(
            jid=msg.jid, missing=sautes, numbers=numbers, caption=msg.caption, posted_at=now
        )
        return Action.ACCEPTED_WITH_GAP

    def sweep_pending_warnings(self, now: datetime) -> list[str]:
        """Envoie les avertissements « numéro sauté » dont le délai est passé.

        Ceux dont la légende a été corrigée entre-temps ont déjà quitté la
        file : c'est tout l'intérêt du délai. Retourne les message_id
        avertis.
        """
        dus = [
            mid
            for mid, warning in self._pending_warnings.items()
            if now - warning.posted_at >= self.gap_warning_delay
        ]
        for mid in dus:
            warning = self._pending_warnings.pop(mid)
            moderation.warn_skipped_numbers(
                self.db, self.gateway, warning.jid, warning.missing, warning.numbers,
                warning.caption, now, self.dry_run,
            )
        return dus

    def _clear_pending_for(self, msg: IncomingMessage) -> None:
        """Une légende corrigée arrive sous l'identité du message d'origine :
        la photo mise en attente n'est plus en faute, et le balayage
        périodique ne doit pas la sanctionner après coup."""
        pending = self._pending.get(msg.jid)
        if pending is not None and pending.message_id == msg.message_id:
            del self._pending[msg.jid]

    def _try_correction(self, msg: IncomingMessage, deja: list[Beer], now: datetime) -> Action:
        """Renumérote une bière déjà comptée dont la légende a été corrigée.

        C'est le droit à l'erreur promis dans l'avertissement « numéro
        sauté » : tant que la place visée est libre — ou seulement tenue par
        un « - » —, la bière s'y déplace, puis `restore_sequence` recolle la
        chaîne. Le tiret se décale donc tout seul vers le trou laissé
        derrière, et personne d'autre n'est prévenu : ceux qui ont posté
        après n'y sont pour rien.
        """
        from src.validator import parse_numbers  # import tardif : évite un cycle

        anciens = [beer.number for beer in deja]
        nouveaux = parse_numbers(msg.caption) if msg.caption else None
        if not nouveaux or list(nouveaux) == anciens:
            return Action.IGNORED_DUPLICATE

        if any(n != nouveaux[0] + i for i, n in enumerate(nouveaux)):
            return Action.IGNORED_DUPLICATE  # correction illisible : on garde l'existant

        a_liberer = []
        for number in nouveaux:
            occupant = self.db.get_beer(number)
            if occupant is None or number in anciens:
                continue
            if occupant.jid != PLACEHOLDER_JID:
                # La place est prise par la bière de quelqu'un d'autre :
                # corriger ici écraserait son travail.
                log.info(
                    "correction de %s refusée : le numéro %s est déjà pris par %s",
                    msg.message_id, number, occupant.jid,
                )
                return Action.IGNORED_DUPLICATE
            a_liberer.append(number)

        modele = deja[0]
        self.db.delete_beers(anciens + a_liberer)
        for number in nouveaux:
            self.db.insert_beer(
                Beer(
                    number=number,
                    jid=modele.jid,
                    message_id=msg.message_id,
                    posted_at=modele.posted_at,
                    source=modele.source,
                )
            )
        self.db.restore_sequence(now)
        # Corrigé avant que l'avertissement ne parte : il n'a plus lieu d'être.
        self._pending_warnings.pop(msg.message_id, None)
        log.info("bière(s) %s corrigée(s) en %s", anciens, list(nouveaux))
        return Action.CORRECTED

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
            # Première apparition de ce JID : peut-être la même personne que
            # celle que l'import ne connaît que par son nom d'export. Si le
            # rapprochement est sans ambiguïté, elle récupère son historique
            # au lieu de repartir de zéro (§6.3).
            identity.adopt_history(self.db, msg.jid, msg.push_name)
            member = self.db.get_member(msg.jid)

        if member is None:
            member = Member(jid=msg.jid, push_name=msg.push_name, joined_at=self.clock.now())
            self.db.save_member(member)
        elif msg.push_name and member.push_name != msg.push_name:
            member.push_name = msg.push_name
            self.db.save_member(member)
        return member
