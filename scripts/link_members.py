#!/usr/bin/env python
"""Génère un CSV nom_export;jid à compléter à la main (§6.3 du plan).

L'export donne des noms de répertoire ("Marie"), le bot en live ne
connaît que des JID (33612345678@s.whatsapp.net). Ce script liste les
auteurs distincts trouvés dans l'export pour que tu complètes la colonne
jid une fois le bot connecté et la liste des participants récupérée.

Usage :
    python scripts/link_members.py export.txt membres.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.importer import parse_export  # noqa: E402


def link_members(export_path: str, out_csv: str) -> None:
    entries = parse_export(export_path)
    authors = sorted({e.author for e in entries if not e.is_system})

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["nom_export", "jid"])
        for author in authors:
            writer.writerow([author, ""])

    print(f"{len(authors)} noms écrits dans {out_csv}")
    print("Complète la colonne jid à la main, puis passe ce fichier à import_history.py.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/link_members.py <export.txt> <membres.csv>")
        sys.exit(1)
    link_members(sys.argv[1], sys.argv[2])
