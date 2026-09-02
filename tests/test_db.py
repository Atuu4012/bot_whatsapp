"""Invariant de séquence : de 1 au MAX, sans trou, sans « - » en tête."""

from __future__ import annotations

from datetime import datetime

from src.db import PLACEHOLDER_JID, Beer, Database, Member

NOW = datetime(2026, 1, 1)


def _db(numeros: list[int]) -> Database:
    db = Database(":memory:")
    db.save_member(Member(jid="a@s.whatsapp.net"))
    for n in numeros:
        db.insert_beer(Beer(number=n, jid="a@s.whatsapp.net", posted_at=NOW, source="live"))
    return db


def test_les_trous_sont_combles_par_un_tiret():
    db = _db([1, 4])

    combles, retires = db.restore_sequence(NOW)

    assert combles == [2, 3]
    assert retires == []
    assert db.get_beer(2).jid == PLACEHOLDER_JID
    assert db.get_beer(2).source == "placeholder"


def test_un_tiret_ne_tient_jamais_le_compteur_tout_seul():
    """Sinon une correction vers le bas laisserait le compteur en l'air."""
    db = _db([1, 3])
    db.restore_sequence(NOW)  # 2 devient un « - »
    db.delete_beers([3])

    combles, retires = db.restore_sequence(NOW)

    assert retires == [2]
    assert db.next_expected_number() == 2


def test_restore_sequence_est_idempotent():
    db = _db([1, 5])
    db.restore_sequence(NOW)

    assert db.restore_sequence(NOW) == ([], [])


def test_base_vide_ne_fait_rien():
    assert Database(":memory:").restore_sequence(NOW) == ([], [])


def test_merge_member_deplace_tout_et_supprime_lancien():
    db = _db([1])
    db.insert_infraction("a@s.whatsapp.net", "NO_CAPTION", None, "warned", NOW)

    db.merge_member("a@s.whatsapp.net", "b@s.whatsapp.net", push_name="B")

    assert db.get_member("a@s.whatsapp.net") is None
    assert db.get_beer(1).jid == "b@s.whatsapp.net"
    assert len(db.infractions_for("b@s.whatsapp.net")) == 1
    assert db.get_member("b@s.whatsapp.net").push_name == "B"


def test_la_base_est_utilisable_depuis_un_autre_thread():
    """neonize livre les messages sur son propre thread, le planificateur
    balaie sur les siens : sqlite3 refusait la connexion ailleurs que là où
    elle avait été créée, et tout message reçu partait à l'erreur."""
    import threading

    db = _db([1])
    vu = {}

    fil = threading.Thread(target=lambda: vu.update(membre=db.get_member("a@s.whatsapp.net")))
    fil.start()
    fil.join()

    assert vu["membre"] is not None


def test_deux_threads_ninterferent_pas_sur_une_meme_operation():
    """Le verrou porte sur l'opération métier, pas sur la requête : deux
    insertions concurrentes ne doivent pas se marcher dessus."""
    import threading

    db = _db([])
    db.save_member(Member(jid="b@s.whatsapp.net"))
    erreurs = []

    def insere(debut: int) -> None:
        try:
            for n in range(debut, debut + 50):
                with db.lock:
                    db.insert_beer(
                        Beer(number=n, jid="b@s.whatsapp.net", posted_at=NOW, source="live")
                    )
        except Exception as exc:  # noqa: BLE001 — c'est ce qu'on veut voir échouer
            erreurs.append(exc)

    fils = [threading.Thread(target=insere, args=(d,)) for d in (1, 101)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()

    assert erreurs == []
    assert db.next_expected_number() - 1 == 150
