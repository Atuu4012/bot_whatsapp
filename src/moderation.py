"""Escalade, DM d'explication, kick, réintégration."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.db import Database, Member
from src.gateway import AddParticipantError, WhatsAppGateway

DEFAULT_TIERS = {1: timedelta(hours=24), 2: timedelta(days=7)}  # 3+ : retour manuel (None)
DEFAULT_PRESCRIPTION = timedelta(days=90)

REASONS = {
    "NOT_AN_IMAGE": "ton message ne contenait pas de photo",
    "NO_CAPTION": "ta photo n'avait pas de légende",
    "CAPTION_NOT_NUMERIC": (
        "la légende doit contenir uniquement le numéro, "
        "pas de commentaire, pas d'emoji, pas de « la 700e ! »"
    ),
    "WRONG_NUMBER": "le numéro ne suivait pas le compteur",
}

TIER_NOTICES = {
    1: "C'est ton premier avertissement. Tu seras réintégré automatiquement dans 24 h.",
    2: "Deuxième infraction : retour automatique dans 7 jours.",
    3: "Troisième infraction. Le retour se fait désormais sur validation d'un admin.",
}


def _tier_notice(kick_count: int) -> str:
    return TIER_NOTICES.get(kick_count, TIER_NOTICES[3])


def build_kick_dm(reason: str, kick_count: int) -> str:
    return (
        f"🍺 Tu viens d'être retiré du groupe : {REASONS[reason]}.\n\n"
        "Règle unique : une photo de ta bière, en légende le numéro suivant. "
        "Rien d'autre.\n\n"
        f"{_tier_notice(kick_count)}"
    )


def apply_sanction(
    member: Member,
    now: datetime,
    dry_run: bool,
    tiers: dict[int, timedelta] | None = None,
    prescription: timedelta | None = None,
) -> Member:
    """Calcule l'escalade. En dry run, le compteur avance (pour tester la
    logique de tiers) mais `banned_until` n'est jamais posé : rien de réel
    ne doit restreindre le membre tant que le mode observation est actif.
    """

    tiers = DEFAULT_TIERS if tiers is None else tiers
    prescription = DEFAULT_PRESCRIPTION if prescription is None else prescription

    if member.last_infraction_at and now - member.last_infraction_at > prescription:
        member.kick_count = 0

    member.kick_count += 1
    member.last_infraction_at = now

    if dry_run:
        member.banned_until = None
    else:
        duration = tiers.get(member.kick_count)
        member.banned_until = now + duration if duration else None

    return member


def moderate(
    db: Database,
    gateway: WhatsAppGateway,
    group: str,
    jid: str,
    reason: str,
    raw_content: str | None,
    now: datetime,
    dry_run: bool,
    tiers: dict[int, timedelta] | None = None,
    prescription: timedelta | None = None,
) -> Member:
    """Ordre : (1) log l'infraction, (2) DM, (3) kick.

    Le DM part avant le kick : kicker d'abord ferait passer le DM pour du
    cold outreach vers un non-contact aux yeux de WhatsApp, ce qui augmente
    le risque de flag anti-spam sur le numéro du bot.
    """

    member = db.get_member(jid) or Member(jid=jid)
    apply_sanction(member, now, dry_run, tiers, prescription)

    action = "dry_run" if dry_run else "kicked"
    db.insert_infraction(jid, reason, raw_content, action, now)
    db.save_member(member)

    if dry_run:
        return member

    gateway.send_dm(jid, build_kick_dm(reason, member.kick_count))
    gateway.remove_participant(group, jid)
    return member


def process_returns(db: Database, gateway: WhatsAppGateway, group: str, now: datetime) -> None:
    """Job périodique : réintègre tout `banned_until` déjà expiré, y compris
    ceux accumulés pendant une coupure (pas seulement la dernière heure)."""

    for member in db.expired_bans(now):
        try:
            gateway.add_participant(group, member.jid)
            gateway.send_dm(
                member.jid,
                "🍺 Ta mise à l'écart est terminée, te revoilà. "
                "Attention à la légende cette fois.",
            )
        except AddParticipantError:
            member.readd_failed = True
            gateway.send_dm(
                member.jid,
                f"🍺 Tu peux revenir : {gateway.group_invite_link(group)}",
            )

        member.banned_until = None
        db.save_member(member)
