"""Schéma SQLite et accès aux données.

Le compteur courant n'est jamais une variable en mémoire : il se recalcule
toujours depuis `MAX(number)` sur la table beers, pour ne jamais désynchroniser
après un redémarrage.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    jid                 TEXT PRIMARY KEY,
    display_name        TEXT,
    push_name           TEXT,
    joined_at           TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    kick_count          INTEGER NOT NULL DEFAULT 0,
    banned_until        TEXT,
    last_infraction_at  TEXT,
    readd_failed        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS beers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    number        INTEGER NOT NULL UNIQUE,
    jid           TEXT NOT NULL REFERENCES members(jid),
    message_id    TEXT,
    posted_at     TEXT NOT NULL,
    source        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS infractions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    jid           TEXT NOT NULL,
    reason        TEXT NOT NULL,
    raw_content   TEXT,
    action        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones_hit (
    value         INTEGER PRIMARY KEY,
    hit_at        TEXT NOT NULL,
    jid           TEXT
);

CREATE INDEX IF NOT EXISTS idx_beers_jid ON beers(jid);
CREATE INDEX IF NOT EXISTS idx_beers_posted ON beers(posted_at);
"""

# Ligne « bouche-trou » : un numéro jamais retrouvé dans l'historique mais que
# la suite de la séquence prouve avoir existé (§6.4). Elle est rattachée à un
# membre fictif « - » et exclue des classements et des rythmes.
PLACEHOLDER_JID = "-@placeholder.local"
PLACEHOLDER_SOURCE = "placeholder"


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


@dataclass
class Member:
    jid: str
    display_name: str | None = None
    push_name: str | None = None
    joined_at: datetime | None = None
    is_active: bool = True
    kick_count: int = 0
    banned_until: datetime | None = None
    last_infraction_at: datetime | None = None
    readd_failed: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Member":
        return cls(
            jid=row["jid"],
            display_name=row["display_name"],
            push_name=row["push_name"],
            joined_at=_str_to_dt(row["joined_at"]),
            is_active=bool(row["is_active"]),
            kick_count=row["kick_count"],
            banned_until=_str_to_dt(row["banned_until"]),
            last_infraction_at=_str_to_dt(row["last_infraction_at"]),
            readd_failed=bool(row["readd_failed"]),
        )


@dataclass
class Beer:
    number: int
    jid: str
    posted_at: datetime
    source: str
    message_id: str | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Beer":
        return cls(
            id=row["id"],
            number=row["number"],
            jid=row["jid"],
            message_id=row["message_id"],
            posted_at=_str_to_dt(row["posted_at"]),
            source=row["source"],
        )


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- members ---------------------------------------------------

    def get_member(self, jid: str) -> Member | None:
        row = self.conn.execute(
            "SELECT * FROM members WHERE jid = ?", (jid,)
        ).fetchone()
        return Member.from_row(row) if row else None

    def save_member(self, member: Member) -> None:
        self.conn.execute(
            """
            INSERT INTO members (
                jid, display_name, push_name, joined_at, is_active,
                kick_count, banned_until, last_infraction_at, readd_failed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                display_name = excluded.display_name,
                push_name = excluded.push_name,
                joined_at = excluded.joined_at,
                is_active = excluded.is_active,
                kick_count = excluded.kick_count,
                banned_until = excluded.banned_until,
                last_infraction_at = excluded.last_infraction_at,
                readd_failed = excluded.readd_failed
            """,
            (
                member.jid,
                member.display_name,
                member.push_name,
                _dt_to_str(member.joined_at),
                int(member.is_active),
                member.kick_count,
                _dt_to_str(member.banned_until),
                _dt_to_str(member.last_infraction_at),
                int(member.readd_failed),
            ),
        )
        self.conn.commit()

    def expired_bans(self, now: datetime) -> list[Member]:
        rows = self.conn.execute(
            "SELECT * FROM members WHERE banned_until IS NOT NULL AND banned_until <= ?",
            (_dt_to_str(now),),
        ).fetchall()
        return [Member.from_row(r) for r in rows]

    # --- beers ------------------------------------------------------

    def next_expected_number(self) -> int:
        row = self.conn.execute("SELECT MAX(number) AS m FROM beers").fetchone()
        return (row["m"] or 0) + 1

    def last_beer(self) -> Beer | None:
        row = self.conn.execute(
            "SELECT * FROM beers ORDER BY number DESC LIMIT 1"
        ).fetchone()
        return Beer.from_row(row) if row else None

    def get_beer_by_message_id(self, message_id: str) -> Beer | None:
        if message_id is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM beers WHERE message_id = ?", (message_id,)
        ).fetchone()
        return Beer.from_row(row) if row else None

    def insert_beer(self, beer: Beer) -> Beer:
        cur = self.conn.execute(
            """
            INSERT INTO beers (number, jid, message_id, posted_at, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                beer.number,
                beer.jid,
                beer.message_id,
                _dt_to_str(beer.posted_at),
                beer.source,
            ),
        )
        self.conn.commit()
        beer.id = cur.lastrowid
        return beer

    # --- infractions --------------------------------------------------

    def insert_infraction(
        self,
        jid: str,
        reason: str,
        raw_content: str | None,
        action: str,
        created_at: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO infractions (jid, reason, raw_content, action, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (jid, reason, raw_content, action, _dt_to_str(created_at)),
        )
        self.conn.commit()

    def infractions_for(self, jid: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM infractions WHERE jid = ? ORDER BY created_at", (jid,)
        ).fetchall()

    # --- milestones ------------------------------------------------

    def milestone_hit(self, value: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM milestones_hit WHERE value = ?", (value,)
        ).fetchone()
        return row is not None

    def record_milestone(self, value: int, hit_at: datetime, jid: str | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO milestones_hit (value, hit_at, jid) VALUES (?, ?, ?)",
            (value, _dt_to_str(hit_at), jid),
        )
        self.conn.commit()

    # --- stats -------------------------------------------------------

    def leaderboard(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT m.jid,
                   COALESCE(m.push_name, m.display_name, m.jid) AS name,
                   COUNT(*) AS total
            FROM beers b JOIN members m ON m.jid = b.jid
            WHERE b.source <> ?
            GROUP BY b.jid
            ORDER BY total DESC
            """,
            (PLACEHOLDER_SOURCE,),
        ).fetchall()
