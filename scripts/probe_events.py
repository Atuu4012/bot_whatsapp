#!/usr/bin/env python
"""Journalise les événements neonize bruts d'un groupe de test (§13.4).

Sert à constater la forme réelle des événements avant de câbler le TODO de
`src/main.py` : quel champ porte l'image, la légende, le JID de l'expéditeur,
ce qui arrive quand un message est supprimé ou quand quelqu'un rejoint le
groupe. Pour chaque message, le script affiche aussi l'`IncomingMessage` que
le gateway devrait construire — c'est la ligne à vérifier avant de l'écrire
dans le code.

Strictement passif : n'envoie rien, n'expulse personne, ne télécharge aucun
média, n'écrit pas dans `data/beerbot.db`. Le seul fichier écrit est le
journal `--dump`, s'il est demandé.

À faire tourner sur le groupe de test uniquement (§13.4), jamais sur le vrai
groupe. Nécessite une session déjà appairée : `python scripts/pair.py`.

Usage :
    python scripts/probe_events.py                          # tous les chats
    python scripts/probe_events.py --chat 120363...@g.us    # un seul groupe
    python scripts/probe_events.py --dump data/probe.log    # + protobuf brut
"""

from __future__ import annotations

import argparse
import functools
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neonize.client import NewClient  # noqa: E402
from neonize.proto.Neonize_pb2 import Connected as ConnectedEv  # noqa: E402
from neonize.proto.Neonize_pb2 import GroupInfoEvent as GroupInfoEv  # noqa: E402
from neonize.proto.Neonize_pb2 import JoinedGroup as JoinedGroupEv  # noqa: E402
from neonize.proto.Neonize_pb2 import Message as MessageEv  # noqa: E402
from neonize.utils.jid import Jid2String  # noqa: E402

from src.config import load_config  # noqa: E402

# Le terminal Windows tourne souvent en cp1252 : sans ça, le moindre emoji ou
# flèche fait planter le print. Et comme les handlers ci-dessous tournent sur
# le thread de callback de neonize, l'erreur y disparaît sans bruit — l'écran
# reste vide alors que tout fonctionne.
for _stream in (sys.stdout, sys.stderr):
    try:
        # line_buffering : sinon la sortie disparaît quand elle est redirigée
        # (pipe, journal systemd) et que le script est tué avant de vider son tampon.
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, OSError):  # flux redirigé, pas reconfigurable
        pass


def guard(handler):
    """Rend visible une exception levée dans un handler neonize."""

    @functools.wraps(handler)
    def wrapper(*args):
        try:
            handler(*args)
        except Exception:  # noqa: BLE001 — sinon l'erreur est avalée silencieusement
            traceback.print_exc()

    return wrapper

# Le type de protocolMessage (REVOKE = suppression par l'auteur) est un enum
# protobuf : on le relit par le descripteur plutôt que d'importer le module
# waE2E, dont le chemin est un détail interne de neonize.
_PROTOCOL_TYPE = (
    MessageEv.DESCRIPTOR.fields_by_name["Message"]
    .message_type.fields_by_name["protocolMessage"]
    .message_type.fields_by_name["type"]
    .enum_type
)


def to_datetime(value: int) -> tuple[datetime | None, str]:
    """Convertit un Timestamp neonize et renvoie l'unité retenue.

    `MessageInfo.Timestamp` arrive en millisecondes — neonize le passe tel
    quel à `oldestMsgTimestampMS` (builder.py) — mais d'autres champs de la
    même API sont en secondes. On déduit donc l'unité de l'ordre de grandeur
    au lieu de la supposer, et on l'affiche pour qu'elle soit vérifiable.
    """
    for unit, divisor in (("s", 1), ("ms", 10**3), ("µs", 10**6), ("ns", 10**9)):
        seconds = value / divisor
        if 10**9 < seconds < 4 * 10**9:  # ~2001 -> ~2096, toute date plausible
            return datetime.fromtimestamp(seconds), unit
    return None, "unité inconnue"


def protocol_type_name(value: int) -> str:
    entry = _PROTOCOL_TYPE.values_by_number.get(value)
    return entry.name if entry else str(value)


def scrub(event: MessageEv) -> MessageEv:
    """Copie de l'événement sans les vignettes binaires, pour le journal.

    Le `HasField` n'est pas une précaution de style : toucher un
    sous-message protobuf absent le crée. Sans lui, chaque événement
    journalisé se retrouvait affublé d'un `imageMessage {}` vide — de quoi
    faire croire qu'un texte ou une réaction portait une photo.
    """
    copy = MessageEv()
    copy.CopyFrom(event)
    for field in ("imageMessage", "videoMessage"):
        if copy.Message.HasField(field):
            getattr(copy.Message, field).ClearField("JPEGThumbnail")
    return copy


def describe(event: MessageEv) -> str:
    info = event.Info
    source = info.MessageSource
    message = event.Message

    posed = [field.name for field, _ in message.ListFields()]
    has_image = "imageMessage" in posed
    caption = message.imageMessage.caption or None
    text = message.conversation or message.extendedTextMessage.text or None
    timestamp, unit = to_datetime(info.Timestamp)
    is_revoked = (
        "protocolMessage" in posed and protocol_type_name(message.protocolMessage.type) == "REVOKE"
    )

    lines = [
        "=" * 72,
        f"  Info.ID            {info.ID}",
        f"  Info.Type          {info.Type!r}   MediaType={info.MediaType!r}",
        f"  Info.Pushname      {info.Pushname!r}",
        f"  Info.Timestamp     {info.Timestamp} ({unit}) -> {timestamp}",
        f"  chat               {Jid2String(source.Chat)}   IsGroup={source.IsGroup}",
        f"  sender             {Jid2String(source.Sender)}   IsFromMe={source.IsFromMe}",
        f"  senderAlt          {Jid2String(source.SenderAlt)}",
        f"  champs de Message  {', '.join(posed) or '(aucun)'}",
        f"  image.caption      {caption!r}",
        f"  image.mimetype     {message.imageMessage.mimetype!r}",
        f"  texte              {text!r}",
        f"  drapeaux           IsViewOnce={event.IsViewOnce} IsEdit={event.IsEdit!r} "
        f"IsEphemeral={event.IsEphemeral}",
    ]
    if "protocolMessage" in posed:
        lines.append(
            f"  protocolMessage    type={protocol_type_name(message.protocolMessage.type)} "
            f"cible={message.protocolMessage.key.ID!r}"
        )
    lines += [
        "",
        "  -> IncomingMessage(",
        f"        message_id={info.ID!r},",
        f"        jid={Jid2String(source.Sender)!r},",
        f"        push_name={info.Pushname or None!r},",
        f"        has_image={has_image},",
        f"        caption={caption if caption is not None else text!r},",
        f"        timestamp={timestamp!r},",
        f"        is_system=False,  # à confirmer : les entrées/sorties de groupe",
        f"                          # arrivent en GroupInfoEvent, pas en Message",
        f"        is_revoked={is_revoked},",
        "    )",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Journalise les événements neonize bruts.")
    parser.add_argument(
        "--chat",
        help="ne montrer que ce chat (JID complet, ex: 120363...@g.us, ou sa partie gauche)",
    )
    parser.add_argument(
        "--dump",
        metavar="CHEMIN",
        help="écrit aussi le protobuf complet de chaque événement dans ce fichier",
    )
    args = parser.parse_args()

    config = load_config()
    session = Path(config.session_path)
    if not session.exists():
        raise SystemExit(f"Aucune session dans {session} — lance d'abord scripts/pair.py")

    dump = Path(args.dump) if args.dump else None
    if dump:
        dump.parent.mkdir(parents=True, exist_ok=True)

    def record(header: str, event) -> None:
        if dump:
            with dump.open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== {header} @ {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
                handle.write(str(event))

    client = NewClient(str(session))

    @client.event(ConnectedEv)
    @guard
    def on_connected(_: NewClient, __: ConnectedEv) -> None:
        target = args.chat or "tous les chats"
        print(f"[OK] Connecté. À l'écoute de {target}. Ctrl+C pour arrêter.")
        print("Poste maintenant dans le groupe de test : une photo avec légende,")
        print("une photo sans légende, un texte seul, puis supprime un message.")

    @client.event(MessageEv)
    @guard
    def on_message(_: NewClient, event: MessageEv) -> None:
        chat = event.Info.MessageSource.Chat
        if args.chat and args.chat not in (Jid2String(chat), chat.User):
            # Le dire plutôt que de filtrer en silence : un écran vide ne
            # distingue pas « mauvais --chat » de « rien ne reçoit ».
            print(f"  (autre chat : {Jid2String(chat)} - filtré par --chat)", flush=True)
            return
        record("Message", scrub(event))
        print(describe(event), flush=True)

    @client.event(GroupInfoEv)
    @guard
    def on_group_info(_: NewClient, event: GroupInfoEv) -> None:
        print("=" * 72)
        print("  GroupInfoEvent (entrée/sortie/changement de groupe) :")
        print(str(event).strip() or "  (vide)", flush=True)
        record("GroupInfoEvent", event)

    @client.event(JoinedGroupEv)
    @guard
    def on_joined_group(_: NewClient, event: JoinedGroupEv) -> None:
        print("=" * 72)
        info = event.GroupInfo
        print(f"  JoinedGroup : {Jid2String(info.JID)} — {info.GroupName.Name}", flush=True)
        record("JoinedGroup", event)

    try:
        client.connect()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
