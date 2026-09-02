"""Rapprochement noms d'export <-> JID vus en live (scripts/match_members.py)."""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from match_members import match_members, normalise  # noqa: E402

from src.db import Beer, Database, Member  # noqa: E402


def _csv(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    path = tmp_path / "membres.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["nom_export", "jid"])
        writer.writerows(rows)
    return path


def _read(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8", newline="") as f:
        return {r["nom_export"]: r["jid"] for r in csv.DictReader(f, delimiter=";")}


def _db(tmp_path: Path, members: list[Member]) -> str:
    db = Database(str(tmp_path / "beerbot.db"))
    for member in members:
        db.save_member(member)
    return str(tmp_path / "beerbot.db")


def test_normalise_ignore_tilde_accents_et_espaces_fines():
    assert normalise("~ Téo") == normalise("Teo") == "teo"
    assert normalise("Paul Pitiot") == "paul pitiot"


def test_remplit_depuis_le_push_name(tmp_path):
    db_path = _db(tmp_path, [Member(jid="33600000001@s.whatsapp.net", push_name="Alan")])
    csv_path = _csv(tmp_path, [("~ Alan", "")])

    match_members(db_path, str(csv_path))

    assert _read(csv_path)["~ Alan"] == "33600000001@s.whatsapp.net"


def test_nécrase_jamais_un_jid_saisi_a_la_main(tmp_path):
    db_path = _db(tmp_path, [Member(jid="33600000009@s.whatsapp.net", push_name="Alan")])
    csv_path = _csv(tmp_path, [("~ Alan", "33600000001@s.whatsapp.net")])

    match_members(db_path, str(csv_path))

    assert _read(csv_path)["~ Alan"] == "33600000001@s.whatsapp.net"


def test_ignore_les_jid_bouchons_de_limport(tmp_path):
    db_path = _db(tmp_path, [Member(jid="karl@unmapped.local", display_name="Karl")])
    csv_path = _csv(tmp_path, [("Karl", "")])

    match_members(db_path, str(csv_path))

    assert _read(csv_path)["Karl"] == ""


def test_laisse_vide_quand_deux_personnes_portent_le_meme_nom(tmp_path):
    db_path = _db(
        tmp_path,
        [
            Member(jid="33600000001@s.whatsapp.net", push_name="Paul"),
            Member(jid="33600000002@s.whatsapp.net", push_name="Paul"),
        ],
    )
    csv_path = _csv(tmp_path, [("Paul", "")])

    match_members(db_path, str(csv_path))

    assert _read(csv_path)["Paul"] == ""


def test_rapproche_par_numero_de_biere_malgre_un_nom_personnalise(tmp_path):
    """Un contact renommé chez toi n'a aucun nom commun avec son push_name :
    seul le numéro de bière, identique des deux côtés, les relie."""
    db = Database(str(tmp_path / "beerbot.db"))
    db.save_member(Member(jid="33600000001@s.whatsapp.net", push_name="Poulo 🍻"))
    db.insert_beer(
        Beer(
            number=863,
            jid="33600000001@s.whatsapp.net",
            posted_at=datetime(2026, 9, 2, 21, 0),
            source="live",
        )
    )

    export = tmp_path / "chat.txt"
    export.write_text(
        "[02/09/2026, 21:00:00] Paul Tauzin: 863 ‎image omitted\n",
        encoding="utf-8",
    )
    csv_path = _csv(tmp_path, [("Paul Tauzin", "")])

    match_members(str(tmp_path / "beerbot.db"), str(csv_path), str(export))

    assert _read(csv_path)["Paul Tauzin"] == "33600000001@s.whatsapp.net"


def test_les_bieres_importees_ne_prouvent_rien(tmp_path):
    """Une bière `source='import'` porte déjà un jid de mapping : circulaire."""
    db = Database(str(tmp_path / "beerbot.db"))
    db.save_member(Member(jid="paul-tauzin@unmapped.local", display_name="Paul Tauzin"))
    db.insert_beer(
        Beer(
            number=863,
            jid="paul-tauzin@unmapped.local",
            posted_at=datetime(2026, 9, 2, 21, 0),
            source="import",
        )
    )

    export = tmp_path / "chat.txt"
    export.write_text(
        "[02/09/2026, 21:00:00] Paul Tauzin: 863 ‎image omitted\n",
        encoding="utf-8",
    )
    csv_path = _csv(tmp_path, [("Paul Tauzin", "")])

    match_members(str(tmp_path / "beerbot.db"), str(csv_path), str(export))

    assert _read(csv_path)["Paul Tauzin"] == ""
