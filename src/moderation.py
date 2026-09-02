"""Escalade, DM d'explication, kick, réintégration."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.db import Database, Member
from src.gateway import AddParticipantError, WhatsAppGateway

DEFAULT_TIERS = {1: timedelta(hours=24), 2: timedelta(days=7)}  # 3+ : retour manuel (None)
DEFAULT_PRESCRIPTION = timedelta(days=90)

REASONS = {
    "NOT_AN_IMAGE": "ton message ne contenait pas de photo, il faut une preuve à l'appui !",
    "NO_CAPTION": "ta photo n'avait pas de légende, il faut le numéro avec !",
    "CAPTION_NOT_NUMERIC": (
        "Il ne faut pas écrire de texte, on ne raconte pas sa vie ici, juste le numéro de la bière. Pour parler il y a le groupe général."
    ),
    "WRONG_NUMBER": "le numéro ne suivait pas le compteur, on ne peut pas tricher !",
}

SKIPPED_NUMBER = "SKIPPED_NUMBER"

TIER_NOTICES = {
    1: "C'est ton premier avertissement. Tu seras réintégré automatiquement dans 24 h.",
    2: "Deuxième infraction : retour automatique dans 7 jours.",
    3: "Troisième infraction. Si tu veux revenir dans le groupe, il faudra faire une demande et attendre qu'un admin valide ton retour.",
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


def build_gap_warning(missing: list[int], posted: tuple[int, ...]) -> str:
    """DM d'avertissement : un numéro a été sauté, la chaîne tient quand même.

    Les numéros manquants forment toujours une plage contiguë : on l'écrit
    comme telle, sinon un trou d'une trentaine de bières donnerait un pavé
    illisible.
    """
    if len(missing) == 1:
        manquants = f"le numéro {missing[0]} manque"
    else:
        manquants = f"les numéros {missing[0]} à {missing[-1]} manquent"

    return (
        f"🍺 Tu as posté {posted[0]} alors qu'on attendait {missing[0]} : "
        f"{manquants} à l'appel." + chr(10) * 2 +
        "J'ai mis un « - » à la place pour ne pas casser le compteur, ta bière est comptée. "
        "Si personne n'a encore posté après toi, tu peux corriger : modifie la légende de ta "
        "photo et je remets tout d'aplomb." + chr(10) * 2 +
        "Sinon, ce n'est pas grave — mais fais attention la prochaine fois."
    )


def warn_skipped_numbers(
    db: Database,
    gateway: WhatsAppGateway,
    jid: str,
    missing: list[int],
    posted: tuple[int, ...],
    raw_content: str | None,
    now: datetime,
    dry_run: bool,
) -> None:
    """Prévient l'auteur d'un numéro sauté. Jamais une sanction : pas
    d'escalade, pas de `kick_count` — juste une trace et un DM."""

    if not missing:
        return

    db.insert_infraction(
        jid, SKIPPED_NUMBER, raw_content, "dry_run" if dry_run else "warned", now
    )
    if dry_run:
        return

    gateway.send_dm(jid, build_gap_warning(missing, posted))
