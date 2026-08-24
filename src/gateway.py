"""Abstraction du protocole WhatsApp.

Aucun autre module ne doit importer `neonize` directement : tout passe par
`WhatsAppGateway`. Ça permet de tester ~80% du bot (validation, modération,
paliers, import) sans connexion WhatsApp, via `tests/fakes.py::FakeGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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

    Squelette volontairement minimal : la forme exacte des appels neonize
    (noms de méthodes, structure des événements) doit être validée contre un
    groupe de test avant tout usage réel — voir §13.4 du plan. Ne pas
    connecter au vrai groupe sans être passé par cette étape.
    """

    def __init__(self, session_path: str):
        from neonize.client import NewClient  # import différé, voir docstring module

        self.client = NewClient(session_path)

    def send_group(self, group: str, text: str) -> None:
        self.client.send_message(group, text)

    def send_dm(self, jid: str, text: str) -> None:
        self.client.send_message(jid, text)

    def remove_participant(self, group: str, jid: str) -> None:
        self.client.update_group_participants(group, [jid], "remove")

    def add_participant(self, group: str, jid: str) -> None:
        try:
            self.client.update_group_participants(group, [jid], "add")
        except Exception as exc:  # noqa: BLE001 — remappé en erreur de domaine
            raise AddParticipantError(str(exc)) from exc

    def group_invite_link(self, group: str) -> str:
        return self.client.get_group_invite_link(group)

    def revoke_invite_link(self, group: str) -> None:
        self.client.revoke_group_invite_link(group)

    def list_participants(self, group: str) -> list[Participant]:
        info = self.client.get_group_info(group)
        return [
            Participant(jid=p.jid, push_name=getattr(p, "display_name", None), is_admin=p.is_admin)
            for p in info.participants
        ]
