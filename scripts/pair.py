#!/usr/bin/env python
"""Appaire le bot à WhatsApp et affiche le JID de ses groupes (§14, étape 5).

Affiche un QR code à scanner depuis l'iPhone du bot : WhatsApp → Réglages →
Appareils liés → Lier un appareil. La session est écrite dans SESSION_PATH
(`.env`, `data/session.db` par défaut) — c'est ce fichier qu'on recopiera
plus tard sur le Raspberry Pi. Il vaut un accès complet au compte : jamais
dans Git (`/data/` est déjà ignoré), jamais partagé.

Une fois connecté, le script liste les groupes du bot avec leur JID et dit
s'il y est admin : c'est la valeur à mettre dans BOT_GROUP_JID — le groupe
de test d'abord, jamais le vrai groupe tant que le protocole n'est pas
validé (§13.4).

Strictement passif : n'envoie aucun message, n'écrit pas dans
`data/beerbot.db`.

Usage :
    python scripts/pair.py                        # QR code dans le terminal
    python scripts/pair.py --phone 33612345678    # code à 8 caractères
    python scripts/pair.py --qr-png data/qr.png   # + le QR en image
"""

from __future__ import annotations

import argparse
import functools
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import segno  # noqa: E402
from neonize.client import NewClient  # noqa: E402
from neonize.proto.Neonize_pb2 import Connected as ConnectedEv  # noqa: E402
from neonize.proto.Neonize_pb2 import Device, GroupInfo  # noqa: E402
from neonize.proto.Neonize_pb2 import LoggedOut as LoggedOutEv  # noqa: E402
from neonize.proto.Neonize_pb2 import PairStatus as PairStatusEv  # noqa: E402
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


def bot_is_admin(group: GroupInfo, me: Device) -> bool | None:
    """True/False si le bot est admin du groupe, None s'il ne s'y trouve pas.

    Un participant est identifié par la partie « User » du JID : le compte
    apparaît tantôt en numéro (`@s.whatsapp.net`), tantôt en LID (`@lid`),
    et le JID du client porte en plus un suffixe d'appareil.
    """
    mine = {me.JID.User, me.LID.User} - {""}
    for participant in group.Participants:
        ids = {participant.JID.User, participant.LID.User, participant.PhoneNumber.User}
        if ids & mine:
            return participant.IsAdmin or participant.IsSuperAdmin
    return None


def print_groups(client: NewClient, me: Device) -> None:
    try:
        groups = client.get_joined_groups()
    except Exception as exc:  # noqa: BLE001 — outil de diagnostic, on affiche et on continue
        print(f"Impossible de lister les groupes : {exc}")
        return

    if not groups:
        print("\nLe bot n'est encore dans aucun groupe. Ajoute-le au groupe de")
        print("test, passe-le admin, puis relance ce script.")
        return

    print("\nGroupes du bot — copier le JID voulu dans BOT_GROUP_JID :")
    for group in groups:
        admin = {True: "admin", False: "PAS admin", None: "?"}[bot_is_admin(group, me)]
        print(f"  {Jid2String(group.JID):<34} [{admin:<9}] {group.GroupName.Name}")


def request_pair_code(client: NewClient, phone: str) -> None:
    """Demande le code d'appairage une fois la connexion établie.

    `PairPhone` doit être appelé pendant que la boucle réseau tourne, or
    `client.connect()` est bloquant — d'où ce thread.
    """
    for _ in range(60):
        if client.is_connected:
            break
        time.sleep(0.5)
    else:
        print("Pas de connexion aux serveurs WhatsApp : code non demandé.")
        return

    try:
        code = client.PairPhone(phone, show_push_notification=True)
    except Exception as exc:  # noqa: BLE001 — message d'erreur remonté tel quel
        print(f"Échec de la demande de code : {exc}")
        return

    print(f"\n>>> Code d'appairage : {code}")
    print("   Sur l'iPhone : WhatsApp → Réglages → Appareils liés → Lier un")
    print("   appareil → Lier avec le numéro de téléphone, puis saisis ce code.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Appaire le bot à WhatsApp.")
    parser.add_argument(
        "--phone",
        help="numéro du bot au format international sans + (ex: 33612345678) : "
        "appairage par code à 8 caractères au lieu du QR",
    )
    parser.add_argument(
        "--qr-png",
        metavar="CHEMIN",
        help="écrit aussi le QR dans un fichier PNG, si le terminal l'affiche mal",
    )
    args = parser.parse_args()

    config = load_config()
    session = Path(config.session_path)
    session.parent.mkdir(parents=True, exist_ok=True)
    print(f"Session : {session} ({'existante' if session.exists() else 'nouvelle'})")

    client = NewClient(str(session))

    if args.qr_png:

        def on_qr(_: NewClient, data: bytes) -> None:
            segno.make_qr(data).terminal(compact=True)
            segno.make_qr(data).save(args.qr_png, scale=8)
            print(f"QR également écrit dans {args.qr_png}")

        client.qr(on_qr)

    @client.event(ConnectedEv)
    @guard
    def on_connected(client: NewClient, _: ConnectedEv) -> None:
        me = client.get_me()
        print(f"\n[OK] Connecté en tant que {Jid2String(me.JID)} ({me.PushName or 'sans nom'})")
        print_groups(client, me)
        print("\nSession enregistrée. Ctrl+C pour quitter : elle reste valable.")

    @client.event(PairStatusEv)
    @guard
    def on_pair_status(_: NewClient, event: PairStatusEv) -> None:
        if event.Error:
            print(f"[ERREUR] Appairage refusé : {event.Error}")
        else:
            print(f"[OK] Appairé : {Jid2String(event.ID)}")

    @client.event(LoggedOutEv)
    @guard
    def on_logged_out(_: NewClient, event: LoggedOutEv) -> None:
        print(f"[ERREUR] WhatsApp a déconnecté ce compte lié ({str(event).strip() or 'sans détail'}).")
        print(f"   Supprime {session} et relance pour réappairer.")

    if args.phone:
        threading.Thread(target=request_pair_code, args=(client, args.phone), daemon=True).start()
    else:
        print("Scanne le QR ci-dessous depuis l'iPhone du bot :")
        print("WhatsApp → Réglages → Appareils liés → Lier un appareil\n")

    try:
        client.connect()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
