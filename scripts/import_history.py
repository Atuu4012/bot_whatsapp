#!/usr/bin/env python
"""Import one-shot de l'historique WhatsApp dans la base (§6 du plan).

Politique de réconciliation (§6.4) :
- Le nombre de référence, c'est MAX(number) atteint, pas le nombre de lignes.
- Doublons (même numéro posté plusieurs fois) : on garde le premier posté
  (ordre chronologique de l'export), on ignore les suivants.
- Trous dans la séquence : on les laisse, on ne renumérote jamais.

La vraie vie est plus désordonnée qu'« une photo, une légende » : sur les
vraies données, le numéro arrive parfois avant la pièce jointe, parfois
après ; plusieurs photos sans légende sont parfois suivies d'un seul message
qui liste tous les numéros ("563,564") ; un message sans rapport peut
s'intercaler ; l'auteur "attache" parfois une vidéo plutôt qu'une photo mais
le numéro qui l'accompagne compte quand même. `_reconcile` associe, pour
chaque auteur indépendamment, les pièces jointes en attente et les nombres
en attente dans les FOLLOWUP_WINDOW minutes qui suivent — même principe que
le rattrapage en direct (Engine._try_complete_pending), en plus général.

Le mapping nom_export -> jid vient d'un CSV généré par link_members.py.
Un auteur non mappé reçoit un jid provisoire "<nom>@unmapped.local", listé
en fin d'exécution — à corriger avant de rebrancher le bot en live.

Usage :
    python scripts/import_history.py export.txt membres.csv data/beerbot.db
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import Beer, Database, Member  # noqa: E402
from src.importer import FOLLOWUP_WINDOW, ImportedEntry, parse_export  # noqa: E402
from src.validator import parse_numbers  # noqa: E402


def load_mapping(csv_path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            name = (row.get("nom_export") or "").strip()
            jid = (row.get("jid") or "").strip()
            if name and jid:
                mapping[name] = jid
    return mapping


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "inconnu"


def _prune(queue: list, now: datetime, ts_of) -> None:
    while queue and now - ts_of(queue[0]) > FOLLOWUP_WINDOW:
        queue.pop(0)


def reconcile(
    entries: list[ImportedEntry], already_used: set[int] | None = None
) -> tuple[list[tuple[str, datetime, tuple[int, ...]]], int, int]:
    """Associe pièces jointes et numéros dispersés en soumissions résolues.

    Une légende qui se lit comme un ou plusieurs numéros valides est
    directement acceptée, même si elle ne suit pas immédiatement le dernier
    numéro connu : le plan autorise explicitement les trous dans la
    séquence (§6.4 — le compteur de référence, c'est MAX(number), pas une
    chaîne ininterrompue). On ne déclenche la réconciliation que quand la
    légende est absente/illisible, OU que son premier numéro est déjà pris
    — signe probable d'une faute de frappe (ex. "21 »" pour "210") plutôt
    que d'un trou légitime.

    `already_used` pré-remplit les numéros déjà en base (reprise d'un
    import partiel) ; par défaut, ensemble vide. Retourne (soumissions,
    nb_corrigées_par_reconciliation, nb_non_résolues). Une soumission est
    (auteur, horodatage, numéros) — plusieurs numéros si une légende en
    listait plusieurs ("658 659 660").
    """

    used = set(already_used) if already_used else set()
    pending_media: dict[str, list[datetime]] = defaultdict(list)
    pending_numbers: dict[str, list[tuple[datetime, tuple[int, ...]]]] = defaultdict(list)
    resolved: list[tuple[str, datetime, tuple[int, ...]]] = []
    corrected = 0

    for entry in entries:
        author = entry.author
        _prune(pending_media[author], entry.ts, ts_of=lambda x: x)
        _prune(pending_numbers[author], entry.ts, ts_of=lambda x: x[0])

        if entry.has_attachment:
            numbers = parse_numbers(entry.caption) if entry.caption else None

            if numbers is not None and numbers[0] not in used:
                resolved.append((author, entry.ts, numbers))
                used.update(numbers)
                continue

            # Légende absente, illisible, ou son numéro est déjà pris : un
            # numéro était peut-être déjà arrivé avant la pièce jointe
            # (l'auteur annonce, puis envoie), ou arrive juste après.
            if pending_numbers[author]:
                _, waiting_numbers = pending_numbers[author].pop(0)
                resolved.append((author, entry.ts, waiting_numbers))
                used.update(waiting_numbers)
                corrected += 1
                continue

            pending_media[author].append(entry.ts)
            continue

        text = (entry.body or "").strip()
        numbers = parse_numbers(text) if text else None
        if numbers is None:
            continue  # bavardage sans numéro : ignoré, ne casse pas une attente en cours

        if pending_media[author]:
            # Une liste de numéros peut couvrir plusieurs photos en attente
            # d'affilée ("563,564" pour deux photos sans légende).
            remaining = list(numbers)
            while remaining and pending_media[author]:
                media_ts = pending_media[author].pop(0)
                n = remaining.pop(0)
                resolved.append((author, media_ts, (n,)))
                used.add(n)
                corrected += 1
            continue

        # Pas de pièce jointe en attente : le numéro est peut-être annoncé
        # avant que la photo n'arrive.
        pending_numbers[author].append((entry.ts, numbers))

    unresolved = sum(len(q) for q in pending_media.values()) + sum(
        len(q) for q in pending_numbers.values()
    )
    return resolved, corrected, unresolved


def import_history(export_path: str, mapping_csv: str, db_path: str) -> None:
    entries = [e for e in parse_export(export_path) if not e.is_system]
    mapping = load_mapping(mapping_csv)
    db = Database(db_path)

    already_used = {r["number"] for r in db.conn.execute("SELECT number FROM beers").fetchall()}
    resolved, corrected, unresolved = reconcile(entries, already_used=already_used)

    imported = duplicates = 0
    seen_authors: set[str] = set()

    for author, ts, numbers in resolved:
        seen_authors.add(author)
        jid = mapping.get(author) or f"{_slug(author)}@unmapped.local"
        if db.get_member(jid) is None:
            db.save_member(Member(jid=jid, display_name=author, joined_at=ts))

        for number in numbers:
            already_taken = db.conn.execute(
                "SELECT 1 FROM beers WHERE number = ?", (number,)
            ).fetchone()
            if already_taken:
                duplicates += 1
                continue

            db.insert_beer(Beer(number=number, jid=jid, posted_at=ts, source="import"))
            imported += 1

    print(
        f"{imported} bières importées, {duplicates} doublons ignorés "
        f"(premier posté conservé), {corrected} résolues par réconciliation "
        f"(numéro avant/après la photo, ou liste sur plusieurs photos), "
        f"{unresolved} pièces jointes/numéros jamais réconciliés"
    )
    print(f"compteur de référence après import : {db.next_expected_number() - 1}")

    unmapped = seen_authors - set(mapping)
    if unmapped:
        print("\nAuteurs sans jid mappé (jid provisoire utilisé, à corriger avant le live) :")
        for name in sorted(unmapped):
            print(f"  - {name}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python scripts/import_history.py <export.txt> <membres.csv> <db_path>")
        sys.exit(1)
    import_history(sys.argv[1], sys.argv[2], sys.argv[3])
