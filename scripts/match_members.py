#!/usr/bin/env python
"""Remplit la colonne `jid` de membres.csv à partir des membres vus en live.

L'export WhatsApp ne donne que des noms, la connexion live ne donne que des
JID. Deux ponts, du plus fiable au moins fiable :

1. **Le numéro de bière** (option `--export`). Un numéro identifie un message
   de façon unique : s'il apparaît dans un export *plus récent que le début
   de l'écoute*, l'export en donne l'auteur et la base en donne le JID. Le
   rapprochement est exact, quel que soit le nom — c'est le seul qui marche
   pour les contacts que tu as renommés dans ton répertoire ("Paul Tauzin"
   n'est un nom que chez toi ; personne d'autre ne le connaît sous ce nom).
2. **Le push_name**, à défaut. Le bot l'enregistre dans `members` dès qu'une
   personne poste. Pour les non-contacts, le nom de l'export **est** ce
   push_name, précédé d'un `~` — ça couvre la majorité des lignes.

D'où la marche à suivre : laisser tourner le bot sur le groupe le temps que
chacun poste au moins une fois, réexporter la discussion, puis lancer ce
script avec `--export`. Il ne remplit que les lignes vides — un jid déjà
saisi à la main n'est jamais écrasé — et liste ce qu'il n'a pas su
rapprocher, à compléter à la main.

Ensuite seulement, réimporter l'historique avec le CSV complété (§6.3) :
    python scripts/import_history.py export.txt membres.csv data/beerbot.db

Usage :
    python scripts/match_members.py data/beerbot.db membres.csv
    python scripts/match_members.py data/beerbot.db membres.csv --export chat.txt
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from import_history import reconcile  # noqa: E402

from src.db import Database  # noqa: E402
from src.identity import UNMAPPED_SUFFIX, normalise_name  # noqa: E402

# Les JID posés faute de mieux par l'import : ce ne sont pas de vraies
# identités WhatsApp, il n'y a rien à en tirer ici.
FAUX_SERVEURS = (UNMAPPED_SUFFIX, "@placeholder.local")


# Même normalisation que l'adoption automatique en live : les deux chemins
# doivent rapprocher exactement les mêmes noms.
normalise = normalise_name


def live_members(db_path: str) -> dict[str, set[str]]:
    """Noms vus en live -> JID réels. Un nom peut en désigner plusieurs."""
    db = Database(db_path)
    par_nom: dict[str, set[str]] = defaultdict(set)
    for row in db.conn.execute("SELECT jid, push_name, display_name FROM members"):
        if row["jid"].endswith(FAUX_SERVEURS):
            continue
        for name in (row["push_name"], row["display_name"]):
            if name and normalise(name):
                par_nom[normalise(name)].add(row["jid"])
    return par_nom


def by_beer_number(db_path: str, export_path: str) -> dict[str, set[str]]:
    """Noms d'export -> JID, en recoupant le même numéro de bière des deux côtés.

    Le numéro est unique : la bière 863 désigne un message et un seul. Si
    l'export dit qu'elle vient de "Paul Tauzin" et que la base l'a
    enregistrée en live sous `336…@s.whatsapp.net`, les deux désignent la
    même personne — sans jamais comparer un nom à un autre nom.

    Seules les bières `source='live'` comptent : celles issues d'un import
    portent déjà un JID de mapping, elles ne prouveraient rien.
    """
    from src.importer import parse_export  # noqa: PLC0415 — coûteux, et inutile sans --export

    entries = [e for e in parse_export(export_path) if not e.is_system]
    resolved, _, _ = reconcile(entries)
    auteur_par_numero = {n: author for author, _, numbers in resolved for n in numbers}

    db = Database(db_path)
    par_nom: dict[str, set[str]] = defaultdict(set)
    for row in db.conn.execute("SELECT number, jid FROM beers WHERE source = 'live'"):
        auteur = auteur_par_numero.get(row["number"])
        if auteur and not row["jid"].endswith(FAUX_SERVEURS):
            par_nom[normalise(auteur)].add(row["jid"])
    return par_nom


def match_members(db_path: str, csv_path: str, export_path: str | None = None) -> None:
    par_nom = live_members(db_path)
    if export_path:
        # Preuve exacte : elle complète le rapprochement par nom, et le
        # remplace là où les deux ne disent pas la même chose.
        for nom, jids in by_beer_number(db_path, export_path).items():
            par_nom[nom] = jids
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))

    rempli, deja, ambigus, sans_match = 0, 0, [], []
    utilises: set[str] = set()

    for row in rows:
        nom = (row.get("nom_export") or "").strip()
        if (row.get("jid") or "").strip():
            deja += 1
            utilises.add(row["jid"].strip())
            continue

        candidats = par_nom.get(normalise(nom), set())
        if len(candidats) == 1:
            row["jid"] = next(iter(candidats))
            utilises.add(row["jid"])
            rempli += 1
        elif len(candidats) > 1:
            ambigus.append((nom, sorted(candidats)))
        else:
            sans_match.append(nom)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["nom_export", "jid"], delimiter=";")
        writer.writeheader()
        writer.writerows({"nom_export": r["nom_export"], "jid": r.get("jid", "")} for r in rows)

    print(f"{rempli} jid remplis, {deja} déjà renseignés, {len(sans_match)} sans correspondance")

    if ambigus:
        print("\nPlusieurs JID portent le même nom — à trancher à la main :")
        for nom, jids in ambigus:
            print(f"  {nom} -> {', '.join(jids)}")

    if sans_match:
        print("\nPas encore vus en live (ils n'ont rien posté depuis que le bot écoute) :")
        for nom in sans_match:
            print(f"  {nom}")

    inconnus = {jid for jids in par_nom.values() for jid in jids} - utilises
    if inconnus:
        print("\nVus en live mais absents du CSV (nouveaux membres ?) :")
        for jid in sorted(inconnus):
            print(f"  {jid}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remplit la colonne jid de membres.csv.")
    parser.add_argument("db_path", help="base du bot (data/beerbot.db)")
    parser.add_argument("csv_path", help="CSV à compléter (membres.csv)")
    parser.add_argument(
        "--export",
        help="export postérieur au début de l'écoute : rapproche par numéro de "
        "bière, seul moyen de retrouver un contact que tu as renommé",
    )
    args = parser.parse_args()
    match_members(args.db_path, args.csv_path, args.export)
