"""Abstraction du protocole WhatsApp.

Aucun autre module ne doit importer `neonize` directement : tout passe par
`WhatsAppGateway`. Ça permet de tester ~80% du bot (validation, modération,
paliers, import) sans connexion WhatsApp, via `tests/fakes.py::FakeGateway`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

log = logging.getLogger(__name__)

# WAWebProtobufsE2E.Message.ProtocolMessage.Type.REVOKE : un message supprimé
# par son auteur arrive sous cette forme. La valeur est figée par le proto
# WhatsApp — un test la revérifie contre neonize pour qu'une montée de
# version casse la suite de tests plutôt que la production.
REVOKE = 0

# Champs qui ne constituent pas un message visible dans le groupe : une
# réaction, un accusé de protocole ou un vote ne doivent pas être jugés par
# le moteur — sinon un simple pouce levé vaudrait une expulsion.
NON_MESSAGE_FIELDS = frozenset(
    {
        "messageContextInfo",
        "senderKeyDistributionMessage",
        "reactionMessage",
        "pollUpdateMessage",
        "protocolMessage",
        # Une édition est traitée à part (voir to_incoming) : quand elle est
        # déchiffrable, elle remplace la légende d'origine ; sinon elle est
        # ignorée comme le reste.
        "secretEncryptedMessage",
    }
)


class AddParticipantError(Exception):
    """Levée quand l'ajout direct d'un participant est refusé (confidentialité)."""


@dataclass
class Participant:
    jid: str
    push_name: str | None = None
    is_admin: bool = False


@dataclass
class IncomingMessage:
    message_id: str
    jid: str  # JID de l'expéditeur
    push_name: str | None
    has_image: bool
    caption: str | None
    timestamp: datetime
    is_system: bool = False
    is_revoked: bool = False  # message supprimé par son auteur (événement "revoke")
    # Conversation d'où vient le message. None pour l'historique importé, qui
    # vient forcément du groupe. Le moteur s'en sert pour ne juger que le
    # groupe surveillé : le bot reçoit aussi ses DM et ses autres groupes.
    chat: str | None = None


MESSAGE_EDIT = "Message Edit"  # useCase whatsmeow (msgsecret.go)

# secretEncType du proto WhatsApp : 2 = MESSAGE_EDIT. Un test le revérifie
# contre neonize.
SECRET_ENC_MESSAGE_EDIT = 2


class MessageSecrets:
    """Secrets des messages récents, de quoi déchiffrer leurs éditions.

    Bornée volontairement : une correction arrive dans la minute qui suit,
    garder tout l'historique ne servirait à rien. Le cache est perdu au
    redémarrage — l'édition d'un message antérieur au démarrage reste alors
    illisible, et le message est jugé sur sa légende d'origine.
    """

    def __init__(self, maxlen: int = 500):
        self.maxlen = maxlen
        self._store: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    def remember(self, message_id: str, secret: bytes, sender: str) -> None:
        if not message_id or not secret:
            return
        self._store[message_id] = (secret, sender)
        self._store.move_to_end(message_id)
        while len(self._store) > self.maxlen:
            self._store.popitem(last=False)

    def get(self, message_id: str) -> tuple[bytes, str] | None:
        return self._store.get(message_id)


def _hkdf_sha256(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 avec sel nul, comme le `hkdfutil.SHA256` de whatsmeow."""
    prk = hmac.new(bytes(hashlib.sha256().digest_size), ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def decrypt_edit(secret: bytes, orig_sender: str, editor: str, orig_id: str, encrypted):
    """Déchiffre une légende corrigée. Renvoie le message édité, ou None.

    Schéma repris de `msgsecret.go` (whatsmeow) et vérifié sur une vraie
    édition capturée dans le groupe : la clé dérive du secret du message
    d'origine, l'info HKDF concatène l'ID d'origine, l'expéditeur d'origine,
    l'éditeur et le libellé du cas d'usage ; pas de données authentifiées
    supplémentaires pour une édition. AES-GCM authentifie : un échec veut
    dire mauvaise clé, jamais un contenu douteux — d'où le None.
    """
    from cryptography.exceptions import InvalidTag  # imports différés : voir module
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as E2EMessage

    key = _hkdf_sha256(secret, (orig_id + orig_sender + editor + MESSAGE_EDIT).encode())
    try:
        plain = AESGCM(key).decrypt(encrypted.encIV, encrypted.encPayload, None)
    except InvalidTag:
        log.warning("édition non déchiffrable pour le message %s", orig_id)
        return None

    edited = E2EMessage()
    edited.ParseFromString(plain)
    return edited.protocolMessage.editedMessage


def canonical_jid(jid) -> str:
    """JID sous la forme `<user>@<serveur>`, sans suffixe d'appareil.

    C'est la forme utilisée partout ailleurs dans le bot (base, ADMIN_JIDS,
    historique importé) : `Jid2String` y ajouterait le `:12` de l'appareil
    émetteur, qui change d'un message à l'autre pour une même personne.
    """
    return f"{jid.User}@{jid.Server}" if jid.User else jid.Server


def sender_jid(source) -> str:
    """Identité de l'expéditeur d'un message de groupe.

    Constaté sur le groupe de test (§13.4) : le groupe est adressé en LID,
    `Sender` vaut donc `<lid>@lid` et le numéro réel n'apparaît que dans
    `SenderAlt`. On garde le numéro — c'est la forme qu'ont les JID de
    l'historique importé et des `ADMIN_JIDS` du `.env`.
    """
    sender = source.Sender
    if sender.Server == "lid" and source.SenderAlt.User:
        sender = source.SenderAlt
    return canonical_jid(sender)


def content_fields(message) -> set[str]:
    """Champs de `Message` réellement porteurs de contenu.

    Un sous-message protobuf vide compte comme « présent » : sans ce filtre,
    un `imageMessage {}` sans le moindre octet ferait passer une réaction ou
    un texte pour une photo — donc pour une bière sans légende, donc pour
    une infraction.
    """
    posed = set()
    for field, value in message.ListFields():
        if field.message_type is not None and hasattr(value, "ListFields") and not value.ListFields():
            continue
        posed.add(field.name)
    return posed


def _apply_edit(encrypted, editor: str, secrets: MessageSecrets | None):
    """Contenu corrigé d'un message édité, ou None si illisible.

    Sans le secret du message d'origine (bot redémarré depuis, message trop
    ancien pour le cache), il n'y a rien à lire : mieux vaut ignorer
    l'édition que juger une légende dont on sait qu'elle a été corrigée.
    """
    if secrets is None or encrypted.secretEncType != SECRET_ENC_MESSAGE_EDIT:
        return None

    target = encrypted.targetMessageKey.ID
    known = secrets.get(target)
    if known is None:
        log.warning("édition du message %s ignorée : secret inconnu", target)
        return None

    secret, orig_sender = known
    return decrypt_edit(secret, orig_sender, editor, target, encrypted)


def to_incoming(event, secrets: MessageSecrets | None = None) -> IncomingMessage | None:
    """Convertit un événement `Message` neonize en `IncomingMessage`.

    Renvoie `None` pour ce qui ne doit pas atteindre le moteur : les
    messages du bot lui-même (les félicitations de paliers reviennent en
    événement) et ce qui n'est pas un message visible dans le groupe.

    Formes constatées sur le groupe de test (§13.4) :
      - `Info.Timestamp` est en **millisecondes** ;
      - une photo porte `imageMessage`, sa légende `imageMessage.caption`
        (champ absent = photo sans légende) ;
      - un texte seul arrive en `conversation`, ou en
        `extendedTextMessage.text` quand il cite un autre message ;
      - une suppression arrive en `protocolMessage` de type REVOKE, avec un
        ID à elle : `protocolMessage.key.ID` désigne le message supprimé ;
      - les entrées/sorties de groupe ne passent pas par ici mais par
        `GroupInfoEvent`, d'où `is_system=False` systématique.
    """
    source = event.Info.MessageSource
    if source.IsFromMe:
        return None

    message = event.Message
    posed = content_fields(message)
    sender = canonical_jid(source.Sender)

    if secrets is not None and message.messageContextInfo.messageSecret:
        # À garder même pour un événement qu'on ignore par ailleurs : c'est
        # la copie porteuse de la clé de session qui apporte souvent le
        # secret, et c'est lui qui rendra l'édition lisible tout à l'heure.
        secrets.remember(event.Info.ID, message.messageContextInfo.messageSecret, sender)

    message_id = event.Info.ID
    if "secretEncryptedMessage" in posed:
        edited = _apply_edit(message.secretEncryptedMessage, sender, secrets)
        if edited is None:
            return None
        # L'édition parle du message d'origine : elle en prend l'identité,
        # pour que le moteur la voie comme une correction et non comme une
        # deuxième bière.
        message_id = message.secretEncryptedMessage.targetMessageKey.ID
        message, posed = edited, content_fields(edited)

    is_revoked = "protocolMessage" in posed and message.protocolMessage.type == REVOKE
    if not posed - NON_MESSAGE_FIELDS and not is_revoked:
        return None

    has_image = "imageMessage" in posed
    if has_image:
        caption = message.imageMessage.caption
    else:
        caption = message.conversation or message.extendedTextMessage.text

    return IncomingMessage(
        message_id=message_id,
        jid=sender_jid(source),
        push_name=event.Info.Pushname or None,
        has_image=has_image,
        caption=caption or None,
        timestamp=datetime.fromtimestamp(event.Info.Timestamp / 1000),
        is_system=False,
        is_revoked=is_revoked,
        chat=canonical_jid(source.Chat),
    )


def _parse_jid(value: str):
    """`<user>@<serveur>` -> JID protobuf, la forme qu'attend neonize."""
    from neonize.utils.jid import build_jid  # import différé, voir docstring module

    user, _, server = value.partition("@")
    user = user.split(":", 1)[0]  # un suffixe d'appareil ne s'adresse pas
    return build_jid(user, server or "s.whatsapp.net")


class WhatsAppGateway(Protocol):
    def send_group(self, group: str, text: str) -> None: ...

    def send_dm(self, jid: str, text: str) -> None: ...

    def remove_participant(self, group: str, jid: str) -> None: ...

    def add_participant(self, group: str, jid: str) -> None:
        """Doit lever AddParticipantError si l'ajout direct est refusé."""
        ...

    def group_invite_link(self, group: str) -> str: ...

    def revoke_invite_link(self, group: str) -> None: ...

    def list_participants(self, group: str) -> list[Participant]: ...


class NeonizeGateway:
    """Implémentation réelle, adossée à neonize/whatsmeow.

    Les appels neonize prennent des JID protobuf, pas des chaînes : la
    conversion se fait ici pour que le reste du bot ne manipule que des JID
    sous la forme `<user>@<serveur>`.
    """

    def __init__(self, session_path: str):
        from neonize.client import NewClient  # import différé, voir docstring module

        self.client = NewClient(session_path)
        self.secrets = MessageSecrets()

    def connect(self) -> None:
        """Se connecte et rend la main seulement à la déconnexion (bloquant)."""
        self.client.connect()

    def on_message(self, handler: Callable[[IncomingMessage], None]) -> None:
        """Branche `handler` sur les messages entrants du compte lié.

        Les handlers tournent sur le thread de callback de neonize, où une
        exception disparaît sans bruit : on la journalise ici, et un message
        illisible ne doit jamais interrompre la boucle du bot.
        """
        from neonize.proto.Neonize_pb2 import Message as MessageEv  # import différé

        @self.client.event(MessageEv)
        def _dispatch(_, event) -> None:
            try:
                msg = to_incoming(event, self.secrets)
                if msg is not None:
                    handler(msg)
            except Exception:  # noqa: BLE001 — sinon l'erreur est avalée par le callback
                log.exception("message ignoré, conversion ou traitement en échec")

    def send_group(self, group: str, text: str) -> None:
        self.client.send_message(_parse_jid(group), text)

    def send_dm(self, jid: str, text: str) -> None:
        self.client.send_message(_parse_jid(jid), text)

    def remove_participant(self, group: str, jid: str) -> None:
        from neonize.utils.enum import ParticipantChange  # import différé

        self.client.update_group_participants(
            _parse_jid(group), [_parse_jid(jid)], ParticipantChange.REMOVE
        )

    def add_participant(self, group: str, jid: str) -> None:
        from neonize.utils.enum import ParticipantChange  # import différé

        try:
            results = self.client.update_group_participants(
                _parse_jid(group), [_parse_jid(jid)], ParticipantChange.ADD
            )
        except Exception as exc:  # noqa: BLE001 — remappé en erreur de domaine
            raise AddParticipantError(str(exc)) from exc

        # whatsmeow ne lève pas quand la personne refuse les ajouts directs :
        # il renvoie un code d'erreur par participant (403 dans ce cas). Sans
        # cette lecture, la réintégration passerait pour un succès et le lien
        # d'invitation de secours ne partirait jamais (§8.5).
        for participant in results:
            if participant.Error:
                raise AddParticipantError(f"{jid} : code {participant.Error}")

    def group_invite_link(self, group: str) -> str:
        return self.client.get_group_invite_link(_parse_jid(group))

    def revoke_invite_link(self, group: str) -> None:
        # Pas de méthode dédiée côté neonize : la révocation est un drapeau
        # du même appel, qui renvoie le nouveau lien (dont on n'a que faire).
        self.client.get_group_invite_link(_parse_jid(group), revoke=True)

    def list_participants(self, group: str) -> list[Participant]:
        info = self.client.get_group_info(_parse_jid(group))
        return [
            Participant(
                # Même choix que sender_jid : le numéro plutôt que le LID,
                # pour rester raccord avec les JID du reste de la base.
                jid=canonical_jid(p.PhoneNumber if p.PhoneNumber.User else p.JID),
                push_name=p.DisplayName or None,
                is_admin=p.IsAdmin or p.IsSuperAdmin,
            )
            for p in info.Participants
        ]
