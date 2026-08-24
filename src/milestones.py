"""Paliers et messages de félicitations."""

from __future__ import annotations

from datetime import datetime

from src.db import Database

MILESTONES = [1000, 2000, 5000, 7500, 10000, 15000, 20000, 25000, 50000, 75000, 100000, 150000, 200000, 250000, 500000, 750000, 1000000]
ROUND_EVERY = 500  # célèbre aussi tous les 500, en plus des paliers ci-dessus


def is_milestone(n: int) -> bool:
    return n in MILESTONES or (ROUND_EVERY and n % ROUND_EVERY == 0)


def build_celebration_message(n: int, jid: str, db: Database) -> str:
    leaderboard = db.leaderboard()
    top3 = ", ".join(f"{row['name']} ({row['total']})" for row in leaderboard[:3])
    member = db.get_member(jid)
    name = (member.push_name or member.display_name or jid) if member else jid

    return (
        f"🎉 LA {n}e bière a été bu 🎉\n\n"
        f"Par {name}.\n Bravo à tous 🍻\n\n"
        f"Top 3 des plus gros buveurs : {top3}\n\n"
        "Santé à vous 🍻"
    )


def check_and_celebrate(n: int, jid: str, db: Database, gateway, group: str, now: datetime) -> bool:
    """Poste le message de palier si `n` en est un et n'a pas déjà été fêté.

    Retourne True si un message a été posté. Le contrôle en base
    (`milestones_hit`) évite le doublon après un redémarrage du bot.
    """

    if not is_milestone(n):
        return False
    if db.milestone_hit(n):
        return False

    gateway.send_group(group, build_celebration_message(n, jid, db))
    db.record_milestone(n, now, jid)
    return True
