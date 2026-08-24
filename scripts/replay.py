#!/usr/bin/env python
"""Rejoue un export WhatsApp dans le moteur, sans connexion réelle.

Sert à vérifier les règles de validation contre l'historique réel avant
toute mise en prod (voir §13.3 du plan) : combien de kicks auraient eu
lieu, et pourquoi. N'écrit jamais dans data/beerbot.db — tout tourne en
mémoire avec un FakeGateway.

Usage :
    python scripts/replay.py chemin/vers/export.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import Database  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.importer import parse_export, to_message  # noqa: E402
from tests.fakes import FakeClock, FakeGateway  # noqa: E402


def replay(export_path: str) -> None:
    entries = [e for e in parse_export(export_path) if not e.is_system]
    if not entries:
        print("Aucune entrée exploitable dans cet export.")
        return

    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(entries[0].ts)
    # dry_run=False ici : c'est le seul moyen de voir les DM/kicks simulés
    # dans le FakeGateway (en dry run réel, moderation.moderate() ne les
    # déclenche jamais — voir §8.4). Comme le gateway est faux, rien de réel
    # ne se passe : c'est justement le but de ce script.
    engine = Engine(db=db, gateway=gw, group="replay@g.us", dry_run=False, clock=clock)

    for i, entry in enumerate(entries):
        clock.set_now(entry.ts)
        jid = f"{entry.author}@import.local"
        engine.handle(to_message(entry, jid=jid, message_id=f"import-{i}"))

    print(f"{len(entries)} messages rejoués (hors messages système)")
    print(f"{len(gw.kicked)} kicks auraient eu lieu")
    print(f"compteur final atteint : {db.next_expected_number() - 1}")

    if gw.dms:
        print("\nDétail des DM qui seraient partis :")
        for jid, text in gw.dms:
            print(f"  {jid} -> {text.splitlines()[0]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/replay.py <export.txt>")
        sys.exit(1)
    replay(sys.argv[1])
