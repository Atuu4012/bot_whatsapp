"""Doublures utilisées par les tests : aucune connexion WhatsApp requise."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.gateway import AddParticipantError, Participant


class FakeGateway:
    """Empile les appels au lieu de parler au réseau."""

    def __init__(self, participants: list[Participant] | None = None):
        self.group_msgs: list[str] = []
        self.dms: list[tuple[str, str]] = []
        self.kicked: list[str] = []
        self.added: list[str] = []
        self.invite_revoked = 0
        self._participants = participants or []
        self.add_participant_fails: set[str] = set()

    def send_group(self, group: str, text: str) -> None:
        self.group_msgs.append(text)

    def send_dm(self, jid: str, text: str) -> None:
        self.dms.append((jid, text))

    def remove_participant(self, group: str, jid: str) -> None:
        self.kicked.append(jid)

    def add_participant(self, group: str, jid: str) -> None:
        if jid in self.add_participant_fails:
            raise AddParticipantError(f"{jid} refuse les ajouts directs")
        self.added.append(jid)

    def group_invite_link(self, group: str) -> str:
        return "https://chat.whatsapp.com/FAKE"

    def revoke_invite_link(self, group: str) -> None:
        self.invite_revoked += 1

    def list_participants(self, group: str) -> list[Participant]:
        return self._participants


class FakeClock:
    """Le temps qu'on avance à la main — jamais de sleep() dans les tests."""

    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set_now(self, now: datetime) -> None:
        self._now = now
