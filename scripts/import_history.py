#!/usr/bin/env python
"""Import one-shot de l'historique WhatsApp dans la base (§6 du plan).

Politique de réconciliation (§6.4) :
- Le nombre de référence, c'est MAX(number) atteint, pas le nombre de lignes.
- Doublons (même numéro posté plusieurs fois) : on garde le premier posté
  (ordre chronologique de l'export), on ignore les suivants.
- Trous dans la séquence : on les laisse, on ne renumérote jamais.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import Beer, Database, Member  # noqa: E402
from src.importer import parse_export  # noqa: E402

_THOUSANDS_SEP = (" ", " ", ".", "#")


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


def _extract_number(caption: str) -> int | None:
    cleaned = caption.strip()
    for ch in _THOUSANDS_SEP:
        cleaned = cleaned.replace(ch, "")
    return int(cleaned) if cleaned.isdigit() else None


def import_history(export_path: str, mapping_csv: str, db_path: str) -> None:
    entries = [e for e in parse_export(export_path) if e.has_image and e.caption]
    mapping = load_mapping(mapping_csv)
    db = Database(db_path)

    imported = duplicates = skipped = 0
    seen_authors: set[str] = set()

    for entry in entries:
        seen_authors.add(entry.author)
        number = _extract_number(entry.caption)
        if number is None:
            skipped += 1
            continue

        jid = mapping.get(entry.author) or f"{_slug(entry.author)}@unmapped.local"
        if db.get_member(jid) is None:
            db.save_member(Member(jid=jid, display_name=entry.author, joined_at=entry.ts))

        already_taken = db.conn.execute(
            "SELECT 1 FROM beers WHERE number = ?", (number,)
        ).fetchone()
        if already_taken:
            duplicates += 1
            continue

        db.insert_beer(Beer(number=number, jid=jid, posted_at=entry.ts, source="import"))
        imported += 1

    print(
        f"{imported} bières importées, {duplicates} doublons ignorés "
        f"(premier posté conservé), {skipped} légendes non numériques ignorées"
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
