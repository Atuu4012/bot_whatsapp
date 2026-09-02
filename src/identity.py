"""Réconciliation des identités : nom d'export <-> JID réel.

L'historique importé désigne les gens par le nom qu'ils portaient dans
l'export (`arthur-parizot@unmapped.local`), le live par leur numéro
(`33651422598@s.whatsapp.net`). Tant que les deux coexistent, la même
personne compte pour deux dans les classements.

Le rapprochement se fait sur le `push_name` : pour les non-contacts, le nom
de l'export **est** le push_name, précédé d'un `~`. Quand il est sans
ambiguïté, l'adoption est automatique — le membre bouchon disparaît et ses
bières passent au JID réel.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from src.db import PLACEHOLDER_JID, Database, Member

log = logging.getLogger(__name__)

# JID posés faute de mieux par l'import : ce sont eux qu'on cherche à adopter.
UNMAPPED_SUFFIX = "@unmapped.local"


def normalise_name(name: str) -> str:
    """Forme comparable d'un nom : sans `~`, sans accent, sans ponctuation.

    L'export préfixe les non-contacts d'un `~` et sépare avec des espaces
    fines insécables (U+202F) ; les push_names contiennent des emojis.
    """
    name = name.replace("~", " ")
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9]+", " ", name)
    return " ".join(name.lower().split())


def find_unmapped(db: Database, push_name: str | None) -> Member | None:
    """Membre bouchon portant ce nom, s'il n'y en a qu'un.

    Deux homonymes non mappés : on ne devine pas, ils resteront à trancher à
    la main avec `scripts/match_members.py`.
    """
    if not push_name or not normalise_name(push_name):
        return None

    cible = normalise_name(push_name)
    trouves = [
        Member.from_row(row)
        for row in db.conn.execute("SELECT * FROM members WHERE jid LIKE ?", (f"%{UNMAPPED_SUFFIX}",))
        if normalise_name(row["display_name"] or row["push_name"] or "") == cible
    ]
    if len(trouves) != 1:
        return None
    return trouves[0]


def adopt_history(db: Database, jid: str, push_name: str | None) -> str | None:
    """Rattache l'historique d'un membre bouchon au JID réel qui vient de
    poster. Retourne le JID adopté, ou None s'il n'y avait rien à adopter.
    """
    if jid.endswith(UNMAPPED_SUFFIX) or jid == PLACEHOLDER_JID:
        return None

    ancien = find_unmapped(db, push_name)
    if ancien is None:
        return None

    db.merge_member(ancien.jid, jid, push_name)
    log.info("historique de %s adopté par %s (%s)", ancien.jid, jid, push_name)
    return ancien.jid
