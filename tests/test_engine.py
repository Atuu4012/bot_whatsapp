from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.db import PLACEHOLDER_JID, Beer, Database, Member
from src.engine import Action, Engine
from src.gateway import IncomingMessage
from tests.fakes import FakeClock, FakeGateway

GROUP = "group@g.us"


def bad_msg(jid, message_id="m1", caption="coucou les amis"):
    """Message non conforme quel que soit le compteur : légende illisible."""
    return IncomingMessage(
        message_id=message_id, jid=jid, push_name="X", has_image=True,
        caption=caption, timestamp=datetime(2026, 1, 1),
    )


def msg(jid, number, message_id="m1", has_image=True, is_system=False, push_name="X"):
    return IncomingMessage(
        message_id=message_id,
        jid=jid,
        push_name=push_name,
        has_image=has_image,
        caption=str(number) if number is not None else None,
        timestamp=datetime(2026, 1, 1),
        is_system=is_system,
    )


@pytest.fixture
def engine():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock)
    return eng, db, gw, clock


def test_accepts_first_correct_beer(engine):
    eng, db, gw, clock = engine

    result = eng.handle(msg("a@s.whatsapp.net", 1))

    assert result == Action.ACCEPTED
    assert db.next_expected_number() == 2


def test_wrong_number_awaits_a_correction_then_gets_kicked_if_none_comes(engine):
    eng, db, gw, clock = engine

    result = eng.handle(bad_msg("a@s.whatsapp.net", message_id="m1"))
    assert result == Action.AWAITING_CAPTION
    assert gw.kicked == []

    clock.advance(timedelta(minutes=10))
    swept = eng.sweep_pending_captions(clock.now())

    assert swept == ["a@s.whatsapp.net"]
    assert gw.kicked == ["a@s.whatsapp.net"]


def test_wrong_number_photo_corrected_right_after_is_accepted(engine):
    # Reproduit un cas réel : légende tapée de travers ("21 »" au lieu de
    # "210"), corrigée par un message texte ("210*") quelques secondes après.
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))  # compteur -> 2
    clock.advance(timedelta(seconds=5))
    result = eng.handle(bad_msg("a@s.whatsapp.net", message_id="m2"))  # légende illisible
    assert result == Action.AWAITING_CAPTION

    clock.advance(timedelta(seconds=5))
    correction = IncomingMessage(
        message_id="m3", jid="a@s.whatsapp.net", push_name="X",
        has_image=False, caption="2", timestamp=clock.now(),
    )
    result = eng.handle(correction)

    assert result == Action.ACCEPTED
    assert gw.kicked == []
    assert db.next_expected_number() == 3


def test_dry_run_sanctions_without_touching_the_group():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=True, clock=clock)

    result = eng.handle(bad_msg("a@s.whatsapp.net"))
    assert result == Action.AWAITING_CAPTION

    clock.advance(timedelta(minutes=10))
    eng.sweep_pending_captions(clock.now())

    assert gw.kicked == []
    assert gw.dms == []


def test_duplicate_message_id_counted_once(engine):
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="dup"))
    result = eng.handle(msg("a@s.whatsapp.net", 1, message_id="dup"))

    assert result == Action.IGNORED_DUPLICATE
    assert db.next_expected_number() == 2  # une seule bière comptée


def test_collision_within_grace_period_is_ignored_not_sanctioned(engine):
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))
    clock.advance(timedelta(seconds=10))
    # Quelqu'un d'autre republie le même numéro 1 juste après.
    result = eng.handle(msg("b@s.whatsapp.net", 1, message_id="m2"))

    assert result == Action.IGNORED_COLLISION
    assert gw.kicked == []


def test_collision_after_grace_period_awaits_correction_then_gets_kicked(engine):
    eng, db, gw, clock = engine

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))
    clock.advance(timedelta(seconds=200))
    result = eng.handle(msg("b@s.whatsapp.net", 1, message_id="m2"))
    assert result == Action.AWAITING_CAPTION

    clock.advance(timedelta(minutes=10))
    eng.sweep_pending_captions(clock.now())

    assert gw.kicked == ["b@s.whatsapp.net"]


def test_system_message_ignored():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    result = eng.handle(msg("system", None, has_image=False, is_system=True))

    assert result == Action.IGNORED_SYSTEM
    assert gw.kicked == []


def test_bot_own_messages_ignored():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False,
        clock=FakeClock(datetime(2026, 1, 1)), bot_jid="bot@s.whatsapp.net",
    )

    result = eng.handle(msg("bot@s.whatsapp.net", 1))

    assert result == Action.IGNORED_BOT


def test_admin_is_never_kicked_and_no_infraction_is_logged():
    # Les admins ont le droit de parler librement : un message non conforme
    # de leur part n'est ni sanctionné ni même journalisé comme infraction.
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False,
        clock=FakeClock(datetime(2026, 1, 1)), admin_jids=frozenset({"admin@s.whatsapp.net"}),
    )

    result = eng.handle(bad_msg("admin@s.whatsapp.net"))

    assert result == Action.ADMIN_EXEMPT
    assert gw.kicked == []
    assert gw.dms == []
    assert len(db.infractions_for("admin@s.whatsapp.net")) == 0


def test_admin_beer_photos_are_still_counted():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False,
        clock=FakeClock(datetime(2026, 1, 1)), admin_jids=frozenset({"admin@s.whatsapp.net"}),
    )

    result = eng.handle(msg("admin@s.whatsapp.net", 1))

    assert result == Action.ACCEPTED
    assert db.next_expected_number() == 2


def test_revoked_message_is_ignored_not_sanctioned():
    # Quelqu'un poste un numéro en conflit, supprime son message : ce n'est
    # ni une bière ni une infraction, juste un message qui n'a plus lieu d'être.
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    revoked = IncomingMessage(
        message_id="m1", jid="a@s.whatsapp.net", push_name="X",
        has_image=False, caption=None, timestamp=datetime(2026, 1, 1), is_revoked=True,
    )
    result = eng.handle(revoked)

    assert result == Action.IGNORED_REVOKED
    assert gw.kicked == []
    assert gw.dms == []
    assert db.infractions_for("a@s.whatsapp.net") == []


def test_collision_then_delete_and_repost_with_correct_number_is_accepted():
    """Reproduit le scénario réel : A poste 1, B poste 1 en même temps
    (collision, ignorée), B supprime son message puis reposte 2 (correct)."""
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock)

    eng.handle(msg("a@s.whatsapp.net", 1, message_id="a1"))
    clock.advance(timedelta(seconds=5))
    collision_result = eng.handle(msg("b@s.whatsapp.net", 1, message_id="b1"))
    assert collision_result == Action.IGNORED_COLLISION

    # B supprime son message "1" : événement de suppression, rien à faire.
    clock.advance(timedelta(seconds=2))
    revoke_result = eng.handle(IncomingMessage(
        message_id="b1", jid="b@s.whatsapp.net", push_name="B",
        has_image=False, caption=None, timestamp=clock.now(), is_revoked=True,
    ))
    assert revoke_result == Action.IGNORED_REVOKED

    # B reposte avec le bon numéro : accepté normalement.
    clock.advance(timedelta(seconds=3))
    repost_result = eng.handle(msg("b@s.whatsapp.net", 2, message_id="b2"))

    assert repost_result == Action.ACCEPTED
    assert gw.kicked == []
    assert db.next_expected_number() == 3


def test_multi_number_caption_inserts_one_beer_per_number():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    multi = IncomingMessage(
        message_id="m1", jid="a@s.whatsapp.net", push_name="Karl",
        has_image=True, caption="1 2 3", timestamp=datetime(2026, 1, 1),
    )
    result = eng.handle(multi)

    assert result == Action.ACCEPTED
    assert db.next_expected_number() == 4
    assert gw.kicked == []


def test_multi_number_caption_can_cross_a_milestone():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    for i in range(1, 499):
        eng.handle(msg("a@s.whatsapp.net", i, message_id=f"m{i}"))

    catch_up = IncomingMessage(
        message_id="catchup", jid="a@s.whatsapp.net", push_name="Karl",
        has_image=True, caption="499 500 501", timestamp=datetime(2026, 1, 1),
    )
    result = eng.handle(catch_up)

    assert result == Action.ACCEPTED
    assert db.next_expected_number() == 502
    assert len(gw.group_msgs) == 1
    assert "500" in gw.group_msgs[0]


def test_milestone_celebrated_on_acceptance():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    for i in range(1, 500):
        eng.handle(msg("a@s.whatsapp.net", i, message_id=f"m{i}"))

    assert gw.group_msgs == []
    eng.handle(msg("a@s.whatsapp.net", 500, message_id="m500"))
    assert len(gw.group_msgs) == 1
    assert "500" in gw.group_msgs[0]


def _photo_no_caption(jid, message_id, ts):
    return IncomingMessage(
        message_id=message_id, jid=jid, push_name="X",
        has_image=True, caption=None, timestamp=ts,
    )


def _text(jid, text, message_id, ts):
    return IncomingMessage(
        message_id=message_id, jid=jid, push_name="X",
        has_image=False, caption=text, timestamp=ts,
    )


def test_photo_without_caption_awaits_followup_instead_of_being_sanctioned():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)))

    result = eng.handle(_photo_no_caption("a@s.whatsapp.net", "p1", datetime(2026, 1, 1)))

    assert result == Action.AWAITING_CAPTION
    assert gw.kicked == []
    assert db.infractions_for("a@s.whatsapp.net") == []


def test_number_sent_right_after_photo_completes_it():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock)

    eng.handle(_photo_no_caption("a@s.whatsapp.net", "p1", clock.now()))
    clock.advance(timedelta(seconds=20))
    result = eng.handle(_text("a@s.whatsapp.net", "1", "t1", clock.now()))

    assert result == Action.ACCEPTED
    assert gw.kicked == []
    assert db.next_expected_number() == 2
    # La bière est rattachée à la photo d'origine, pas au message texte.
    beer = db.last_beer()
    assert beer.message_id == "p1"


def test_followup_outside_grace_period_does_not_complete_and_photo_gets_swept():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock,
        caption_grace_period=timedelta(minutes=5),
    )

    eng.handle(_photo_no_caption("a@s.whatsapp.net", "p1", clock.now()))
    clock.advance(timedelta(minutes=10))

    # Trop tard : le sweep périodique sanctionne la photo laissée sans suite.
    swept = eng.sweep_pending_captions(clock.now())
    assert swept == ["a@s.whatsapp.net"]
    assert gw.kicked == ["a@s.whatsapp.net"]


def test_non_matching_followup_text_is_sanctioned_like_any_other_message():
    # Le suivi ne donne pas un blanc-seing : un texte qui ne complète pas la
    # photo en attente reste un message hors-sujet, sanctionné comme un
    # autre. La photo en attente est nettoyée dans la foulée (pas de double
    # sanction au prochain sweep).
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock)

    eng.handle(_photo_no_caption("a@s.whatsapp.net", "p1", clock.now()))
    clock.advance(timedelta(seconds=5))
    result = eng.handle(_text("a@s.whatsapp.net", "999", "t1", clock.now()))

    assert result == Action.SANCTIONED
    assert gw.kicked == ["a@s.whatsapp.net"]

    swept = eng.sweep_pending_captions(clock.now() + timedelta(hours=1))
    assert swept == []


def test_admin_photo_without_caption_is_exempt_not_pending():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(
        db=db, gateway=gw, group=GROUP, dry_run=False, clock=FakeClock(datetime(2026, 1, 1)),
        admin_jids=frozenset({"admin@s.whatsapp.net"}),
    )

    result = eng.handle(_photo_no_caption("admin@s.whatsapp.net", "p1", datetime(2026, 1, 1)))

    assert result == Action.ADMIN_EXEMPT
    swept = eng.sweep_pending_captions(datetime(2026, 1, 1) + timedelta(hours=1))
    assert swept == []  # jamais mis en attente, donc rien à sanctionner plus tard


def test_second_uncaptioned_photo_while_one_already_pending_is_sanctioned():
    db = Database(":memory:")
    gw = FakeGateway()
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=False, clock=clock)

    eng.handle(_photo_no_caption("a@s.whatsapp.net", "p1", clock.now()))
    clock.advance(timedelta(seconds=5))
    result = eng.handle(_photo_no_caption("a@s.whatsapp.net", "p2", clock.now()))

    assert result == Action.SANCTIONED
    assert gw.kicked == ["a@s.whatsapp.net"]


def test_legende_corrigee_leve_la_photo_en_attente(engine):
    """Une édition arrive sous l'ID du message d'origine : la photo mise en
    attente n'est plus en faute et le balayage ne doit pas la sanctionner."""
    eng, db, gw, clock = engine

    photo = msg("a@s.whatsapp.net", None, message_id="M1")
    assert eng.handle(photo) == Action.AWAITING_CAPTION

    corrigee = msg("a@s.whatsapp.net", 1, message_id="M1")
    assert eng.handle(corrigee) == Action.ACCEPTED

    clock.advance(timedelta(minutes=10))
    assert eng.sweep_pending_captions(clock.now()) == []
    assert gw.kicked == []
    assert gw.dms == []


def test_numero_saute_est_accepte_comble_et_averti(engine):
    """Un trou ne casse plus la chaîne : « - » à la place, bière comptée,
    auteur prévenu — et surtout personne d'expulsé."""
    eng, db, gw, clock = engine

    result = eng.handle(msg("a@s.whatsapp.net", 3, message_id="m1"))

    assert result == Action.ACCEPTED_WITH_GAP
    assert db.get_beer(1).jid == PLACEHOLDER_JID
    assert db.get_beer(2).jid == PLACEHOLDER_JID
    assert db.get_beer(3).jid == "a@s.whatsapp.net"
    assert db.next_expected_number() == 4
    assert gw.kicked == []
    # Rien tout de suite : on laisse le temps de se corriger soi-même.
    assert gw.dms == []

    clock.advance(timedelta(seconds=31))
    assert eng.sweep_pending_warnings(clock.now()) == ["m1"]
    assert "1 à 2" in gw.dms[0][1]


def test_correction_quand_personne_na_poste_apres(engine):
    """« la personne pourra changer si personne n'a envoyé de bière après »."""
    eng, db, gw, clock = engine
    eng.handle(msg("a@s.whatsapp.net", 3, message_id="m1"))

    result = eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))

    assert result == Action.CORRECTED
    assert db.get_beer(1).jid == "a@s.whatsapp.net"
    assert db.get_beer(2) is None  # le « - » de tête ne tient pas le compteur
    assert db.get_beer(3) is None
    assert db.next_expected_number() == 2


def test_correction_apres_dautres_bieres_decale_le_tiret(engine):
    """Le trou se déplace vers le numéro devenu manquant, et ceux qui ont
    posté entre-temps ne sont prévenus de rien : ce n'est pas leur faute."""
    eng, db, gw, clock = engine
    eng.handle(msg("a@s.whatsapp.net", 3, message_id="m1"))
    eng.handle(msg("b@s.whatsapp.net", 4, message_id="m2"))
    dms_avant = len(gw.dms)

    result = eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1"))

    assert result == Action.CORRECTED
    assert db.get_beer(1).jid == "a@s.whatsapp.net"
    assert db.get_beer(3).jid == PLACEHOLDER_JID  # le tiret a suivi
    assert db.get_beer(4).jid == "b@s.whatsapp.net"
    assert len(gw.dms) == dms_avant
    assert gw.kicked == []


def test_correction_refusee_si_la_place_est_prise(engine):
    eng, db, gw, clock = engine
    eng.handle(msg("a@s.whatsapp.net", 3, message_id="m1"))
    eng.handle(msg("b@s.whatsapp.net", 4, message_id="m2"))

    result = eng.handle(msg("a@s.whatsapp.net", 4, message_id="m1"))

    assert result == Action.IGNORED_DUPLICATE
    assert db.get_beer(4).jid == "b@s.whatsapp.net"
    assert db.get_beer(3).jid == "a@s.whatsapp.net"


def test_numero_saute_en_dry_run_ne_dit_rien():
    db = Database(":memory:")
    gw = FakeGateway()
    eng = Engine(db=db, gateway=gw, group=GROUP, dry_run=True, clock=FakeClock(datetime(2026, 1, 1)))

    assert eng.handle(msg("a@s.whatsapp.net", 3)) == Action.ACCEPTED_WITH_GAP

    eng.sweep_pending_warnings(datetime(2026, 1, 1, 1))
    assert gw.dms == []
    assert [row["action"] for row in db.infractions_for("a@s.whatsapp.net")] == ["dry_run"]


def test_le_membre_adopte_son_historique_importe(engine):
    """Le JID réel remplace le bouchon de l'import dès le premier message."""
    eng, db, gw, clock = engine
    db.save_member(Member(jid="arthur-parizot@unmapped.local", display_name="Arthur Parizot"))
    db.insert_beer(
        Beer(number=1, jid="arthur-parizot@unmapped.local", posted_at=datetime(2026, 1, 1), source="import")
    )

    eng.handle(msg("33651422598@s.whatsapp.net", 2, message_id="m1", push_name="Arthur Parizot"))

    assert db.get_member("arthur-parizot@unmapped.local") is None
    assert db.get_beer(1).jid == "33651422598@s.whatsapp.net"
    assert db.get_beer(2).jid == "33651422598@s.whatsapp.net"


def test_numero_saute_envoye_apres_la_photo_est_traite_pareil(engine):
    """Photo sans légende, puis un numéro qui saute un cran : même règle."""
    eng, db, gw, clock = engine
    eng.handle(msg("a@s.whatsapp.net", None, message_id="m1"))

    clock.advance(timedelta(seconds=5))
    suite = IncomingMessage(
        message_id="m2", jid="a@s.whatsapp.net", push_name="X",
        has_image=False, caption="3", timestamp=clock.now(),
    )
    result = eng.handle(suite)

    assert result == Action.ACCEPTED_WITH_GAP
    assert db.get_beer(3).jid == "a@s.whatsapp.net"
    assert db.get_beer(1).jid == PLACEHOLDER_JID
    assert gw.kicked == []

    clock.advance(timedelta(seconds=31))
    eng.sweep_pending_warnings(clock.now())
    assert len(gw.dms) == 1


def test_corriger_dans_le_delai_evite_lavertissement(engine):
    """Se rendre compte de son saut tout seul ne vaut aucun message."""
    eng, db, gw, clock = engine
    eng.handle(msg("a@s.whatsapp.net", 3, message_id="m1"))

    clock.advance(timedelta(seconds=10))
    assert eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1")) == Action.CORRECTED

    clock.advance(timedelta(minutes=5))
    assert eng.sweep_pending_warnings(clock.now()) == []
    assert gw.dms == []
    assert db.infractions_for("a@s.whatsapp.net") == []


def test_lavertissement_part_si_rien_ne_bouge(engine):
    eng, db, gw, clock = engine
    eng.handle(msg("a@s.whatsapp.net", 3, message_id="m1"))

    clock.advance(timedelta(seconds=29))
    assert eng.sweep_pending_warnings(clock.now()) == []

    clock.advance(timedelta(seconds=2))
    assert eng.sweep_pending_warnings(clock.now()) == ["m1"]
    assert len(gw.dms) == 1
    assert [row["action"] for row in db.infractions_for("a@s.whatsapp.net")] == ["warned"]


def test_un_message_prive_nest_pas_juge(engine):
    """Le bot reçoit ses DM : les juger expulserait leur auteur du groupe."""
    eng, db, gw, clock = engine
    prive = replace(msg("a@s.whatsapp.net", None, message_id="dm1"), chat="a@s.whatsapp.net")

    assert eng.handle(prive) == Action.IGNORED_OTHER_CHAT
    assert gw.kicked == []
    assert eng.handle(replace(msg("a@s.whatsapp.net", 1, message_id="m1"), chat=GROUP)) == Action.ACCEPTED


def test_un_autre_groupe_est_ignore(engine):
    eng, db, gw, clock = engine
    ailleurs = replace(msg("a@s.whatsapp.net", 1, message_id="m1"), chat="999@g.us")

    assert eng.handle(ailleurs) == Action.IGNORED_OTHER_CHAT
    assert db.next_expected_number() == 1


def test_lhistorique_importe_na_pas_de_chat_et_reste_traite(engine):
    """`importer.to_message` ne renseigne pas le chat : il vient du groupe."""
    eng, db, gw, clock = engine

    assert eng.handle(msg("a@s.whatsapp.net", 1, message_id="m1")).name == "ACCEPTED"
