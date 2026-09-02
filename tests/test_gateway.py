"""Conversion des événements neonize, d'après ce qui a été observé en vrai.

Les événements sont construits avec les vrais protobuf neonize (aucune
connexion réseau) et reproduisent la sortie de `scripts/probe_events.py` sur
le groupe de test (§13.4) : expéditeur en LID, horodatage en millisecondes,
légende dans `imageMessage.caption`, suppression en `protocolMessage`.
"""

from __future__ import annotations

from datetime import datetime

from neonize.proto.Neonize_pb2 import Message as MessageEv

from src.gateway import (
    REVOKE,
    SECRET_ENC_MESSAGE_EDIT,
    MessageSecrets,
    _parse_jid,
    canonical_jid,
    sender_jid,
    to_incoming,
)

# Valeurs relevées telles quelles sur le groupe de test.
LID = "97779376472173"
NUMERO = "33651422598"
TS_MS = 1788373992000
TS = datetime(2026, 9, 2, 20, 33, 12)


def _event(
    message_id: str = "3A128BD6D85FC47AE178",
    push_name: str = "Arthur Parizot",
    from_me: bool = False,
    with_sender_alt: bool = True,
) -> MessageEv:
    event = MessageEv()
    event.Info.ID = message_id
    event.Info.Pushname = push_name
    event.Info.Timestamp = TS_MS
    source = event.Info.MessageSource
    source.Chat.User, source.Chat.Server = "120363430298528890", "g.us"
    source.IsGroup = True
    source.IsFromMe = from_me
    source.Sender.User, source.Sender.Server = LID, "lid"
    if with_sender_alt:
        source.SenderAlt.User, source.SenderAlt.Server = NUMERO, "s.whatsapp.net"
    return event


def test_photo_avec_legende():
    event = _event()
    event.Message.imageMessage.caption = "29"
    event.Message.imageMessage.mimetype = "image/jpeg"
    event.Message.messageContextInfo.SetInParent()

    msg = to_incoming(event)

    assert msg.message_id == "3A128BD6D85FC47AE178"
    assert msg.has_image is True
    assert msg.caption == "29"
    assert msg.push_name == "Arthur Parizot"
    assert msg.is_revoked is False
    assert msg.is_system is False


def test_photo_sans_legende():
    event = _event()
    event.Message.imageMessage.mimetype = "image/jpeg"

    msg = to_incoming(event)

    assert msg.has_image is True
    assert msg.caption is None  # la fenêtre de rattrapage du moteur s'en charge


def test_texte_seul():
    event = _event()
    event.Message.conversation = "28"

    msg = to_incoming(event)

    assert msg.has_image is False
    assert msg.caption == "28"


def test_texte_cite_arrive_en_extended_text():
    event = _event()
    event.Message.extendedTextMessage.text = "30"

    assert to_incoming(event).caption == "30"


def test_suppression_par_lauteur():
    event = _event(message_id="3A85B9AD1ACD59A7A355")
    event.Message.protocolMessage.type = REVOKE
    event.Message.protocolMessage.key.ID = "3A128BD6D85FC47AE178"

    msg = to_incoming(event)

    assert msg.is_revoked is True
    assert msg.message_id == "3A85B9AD1ACD59A7A355"  # l'ID supprimé est dans key.ID


def test_revoke_correspond_bien_a_lenum_neonize():
    """Garde-fou : une montée de version de neonize doit casser ici, pas en prod."""
    enum = (
        MessageEv.DESCRIPTOR.fields_by_name["Message"]
        .message_type.fields_by_name["protocolMessage"]
        .message_type.fields_by_name["type"]
        .enum_type
    )
    assert enum.values_by_name["REVOKE"].number == REVOKE


def test_horodatage_lu_en_millisecondes():
    event = _event()
    event.Message.conversation = "28"

    assert to_incoming(event).timestamp == TS


def test_identite_prend_le_numero_pas_le_lid():
    event = _event()
    event.Message.conversation = "28"

    assert to_incoming(event).jid == f"{NUMERO}@s.whatsapp.net"


def test_identite_retombe_sur_le_lid_sans_numero():
    event = _event(with_sender_alt=False)
    event.Message.conversation = "28"

    assert to_incoming(event).jid == f"{LID}@lid"


def test_message_du_bot_ignore():
    """Les félicitations de paliers reviennent en événement : à ne pas rejuger."""
    event = _event(from_me=True)
    event.Message.conversation = "1000 bières !"

    assert to_incoming(event) is None


def test_reaction_ignoree():
    """Un pouce levé n'est pas un message : le sanctionner serait un faux positif."""
    event = _event()
    event.Message.reactionMessage.text = "👍"
    event.Message.messageContextInfo.SetInParent()

    assert to_incoming(event) is None


def test_push_name_absent_devient_none():
    event = _event(push_name="")
    event.Message.conversation = "28"

    assert to_incoming(event).push_name is None


def test_parse_jid_retire_le_suffixe_dappareil():
    jid = _parse_jid("33651422598:12@s.whatsapp.net")

    assert (jid.User, jid.Server, jid.Device) == ("33651422598", "s.whatsapp.net", 0)


def test_parse_jid_groupe():
    jid = _parse_jid("120363430298528890@g.us")

    assert (jid.User, jid.Server) == ("120363430298528890", "g.us")


def test_canonical_jid_ignore_lappareil():
    assert canonical_jid(_parse_jid("33651422598:12@s.whatsapp.net")) == (
        "33651422598@s.whatsapp.net"
    )


def test_sender_jid_sur_un_message_direct():
    event = _event(with_sender_alt=False)
    event.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    event.Info.MessageSource.Sender.User = NUMERO

    assert sender_jid(event.Info.MessageSource) == f"{NUMERO}@s.whatsapp.net"


def test_copie_de_distribution_de_cle_ignoree():
    """Observé sur le vrai groupe : un même ID arrive deux fois, une première
    fois porteuse de la seule distribution de clé, puis avec le contenu."""
    event = _event(message_id="3A3311C950E003DF3A10")
    event.Message.senderKeyDistributionMessage.groupID = "120363410402168388@g.us"
    event.Message.messageContextInfo.SetInParent()

    assert to_incoming(event) is None


def test_modification_de_message_ignoree():
    """`secretEncryptedMessage` = édition d'un message déjà posté, chiffrée."""
    event = _event()
    event.Message.secretEncryptedMessage.remoteKeyID = "abc"
    event.Message.messageContextInfo.SetInParent()

    assert to_incoming(event) is None


def test_photo_reelle_du_vrai_groupe():
    """Le second exemplaire du même ID, celui qui porte la photo et « 869 »."""
    event = _event(message_id="3A3311C950E003DF3A10", push_name="Alix Peignon")
    event.Info.MessageSource.SenderAlt.User = "33695628824"
    event.Message.imageMessage.caption = "869"
    event.Message.imageMessage.mimetype = "image/jpeg"
    event.Message.messageContextInfo.SetInParent()

    msg = to_incoming(event)

    assert (msg.has_image, msg.caption) == (True, "869")
    assert msg.jid == "33695628824@s.whatsapp.net"


def _hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 à sel nul, réécrit ici pour figer la dérivation attendue."""
    import hashlib
    import hmac

    prk = hmac.new(bytes(32), ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _chiffre_edition(secret, orig_sender, editor, orig_id, caption, cle=None):
    """Chiffre une légende corrigée comme le fait WhatsApp (msgsecret.go)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as E2EMessage

    edite = E2EMessage()
    protocole = E2EMessage.DESCRIPTOR.fields_by_name["protocolMessage"].message_type
    edite.protocolMessage.type = protocole.fields_by_name["type"].enum_type.values_by_name[
        "MESSAGE_EDIT"
    ].number
    edite.protocolMessage.key.ID = orig_id
    edite.protocolMessage.editedMessage.imageMessage.caption = caption

    key = cle or _hkdf(secret, (orig_id + orig_sender + editor + "Message Edit").encode())
    iv = bytes(range(12))
    return iv, AESGCM(key).encrypt(iv, edite.SerializeToString(), None)


def _evenement_edition(orig_id: str, iv: bytes, payload: bytes):
    event = _event(message_id="3AF51EBC3920CAA6D5DC")
    chiffre = event.Message.secretEncryptedMessage
    chiffre.targetMessageKey.ID = orig_id
    chiffre.encIV, chiffre.encPayload = iv, payload
    chiffre.secretEncType = SECRET_ENC_MESSAGE_EDIT
    return event


def test_secret_enc_message_edit_correspond_a_lenum_neonize():
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as E2EMessage

    chiffre = E2EMessage.DESCRIPTOR.fields_by_name["secretEncryptedMessage"].message_type
    enum = chiffre.fields_by_name["secretEncType"].enum_type
    assert enum.values_by_name["MESSAGE_EDIT"].number == SECRET_ENC_MESSAGE_EDIT


def test_legende_corrigee_remplace_loriginale():
    """Cas réel : « 874 » corrigé en « 871-872-873-874 » 24 s plus tard."""
    secret, orig_id = bytes(range(32)), "3A43A96E411645BAA80A"
    secrets = MessageSecrets()
    secrets.remember(orig_id, secret, f"{LID}@lid")

    iv, payload = _chiffre_edition(
        secret, f"{LID}@lid", f"{LID}@lid", orig_id, "871-872-873-874"
    )
    msg = to_incoming(_evenement_edition(orig_id, iv, payload), secrets)

    assert msg.caption == "871-872-873-874"
    assert msg.has_image is True
    # L'édition prend l'identité du message d'origine, pas la sienne.
    assert msg.message_id == orig_id


def test_edition_sans_secret_connu_est_ignoree():
    """Bot redémarré depuis : rien à lire, et surtout pas de sanction."""
    iv, payload = _chiffre_edition(bytes(32), "x@lid", "x@lid", "ORIG", "871")

    assert to_incoming(_evenement_edition("ORIG", iv, payload), MessageSecrets()) is None


def test_edition_indechiffrable_est_ignoree():
    secrets = MessageSecrets()
    secrets.remember("ORIG", bytes(32), "x@lid")
    iv, payload = _chiffre_edition(None, None, None, "ORIG", "871", cle=bytes(range(32)))

    assert to_incoming(_evenement_edition("ORIG", iv, payload), secrets) is None


def test_le_secret_dun_message_est_retenu_pour_son_edition():
    event = _event()
    event.Message.imageMessage.caption = "874"
    event.Message.messageContextInfo.messageSecret = bytes(range(32))
    secrets = MessageSecrets()

    to_incoming(event, secrets)

    assert secrets.get("3A128BD6D85FC47AE178") == (bytes(range(32)), f"{LID}@lid")


def test_le_cache_de_secrets_est_borne():
    secrets = MessageSecrets(maxlen=2)
    for i in range(3):
        secrets.remember(f"m{i}", bytes(32), "x@lid")

    assert secrets.get("m0") is None
    assert secrets.get("m2") is not None


def test_un_image_message_vide_nest_pas_une_photo():
    """Un sous-message protobuf vide compte comme présent : sans filtre, une
    réaction passerait pour une photo sans légende, donc pour une faute."""
    event = _event()
    event.Message.reactionMessage.text = "👍"
    event.Message.imageMessage.SetInParent()  # présent mais vide

    assert to_incoming(event) is None


def test_le_chat_dorigine_est_conserve():
    event = _event()
    event.Message.conversation = "870"

    assert to_incoming(event).chat == "120363430298528890@g.us"
