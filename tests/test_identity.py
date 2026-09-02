"""Adoption automatique de l'historique importé par le JID réel."""

from __future__ import annotations

from datetime import datetime

from src.db import PLACEHOLDER_JID, Beer, Database, Member
from src.identity import adopt_history, find_unmapped, normalise_name

REEL = "33651422598@s.whatsapp.net"


def _db_avec_bouchon(nom: str = "Arthur Parizot", jid: str = "arthur-parizot@unmapped.local"):
    db = Database(":memory:")
    db.save_member(Member(jid=jid, display_name=nom))
    db.insert_beer(Beer(number=1, jid=jid, posted_at=datetime(2026, 1, 1), source="import"))
    return db


def test_normalise_ignore_tilde_accents_et_casse():
    assert normalise_name("~ Téo") == normalise_name("TEO") == "teo"


def test_ladoption_transfere_bieres_et_infractions():
    db = _db_avec_bouchon()
    db.insert_infraction("arthur-parizot@unmapped.local", "NO_CAPTION", None, "warned", datetime(2026, 1, 1))

    adopte = adopt_history(db, REEL, "Arthur Parizot")

    assert adopte == "arthur-parizot@unmapped.local"
    assert db.get_member("arthur-parizot@unmapped.local") is None
    assert db.get_beer(1).jid == REEL
    assert len(db.infractions_for(REEL)) == 1


def test_le_push_name_de_lexport_est_prefixe_dun_tilde():
    """Les non-contacts apparaissent « ~ Alan » dans l'export, « Alan » en live."""
    db = _db_avec_bouchon(nom="~ Alan", jid="alan@unmapped.local")

    assert adopt_history(db, "33600000001@s.whatsapp.net", "Alan") == "alan@unmapped.local"


def test_deux_homonymes_ne_sont_jamais_devines():
    db = _db_avec_bouchon(nom="Paul", jid="paul-1@unmapped.local")
    db.save_member(Member(jid="paul-2@unmapped.local", display_name="Paul"))

    assert adopt_history(db, REEL, "Paul") is None
    assert db.get_member("paul-1@unmapped.local") is not None


def test_le_membre_bouche_trou_nest_jamais_adopte():
    db = Database(":memory:")
    db.save_member(Member(jid=PLACEHOLDER_JID, display_name="-"))

    assert find_unmapped(db, "-") is None
    assert adopt_history(db, REEL, "-") is None


def test_sans_push_name_il_ny_a_rien_a_rapprocher():
    db = _db_avec_bouchon()

    assert adopt_history(db, REEL, None) is None


def test_un_membre_deja_reel_garde_son_compteur_de_sanctions():
    db = _db_avec_bouchon()
    db.save_member(Member(jid=REEL, push_name="Arthur Parizot", kick_count=2))

    adopt_history(db, REEL, "Arthur Parizot")

    assert db.get_member(REEL).kick_count == 2
    assert db.get_beer(1).jid == REEL
