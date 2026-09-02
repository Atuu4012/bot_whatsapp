"""Parsing des exports WhatsApp (.txt) vers des entrées structurées.

Deux formats réels à couvrir :

- « avec médias » (le nôtre) : chaque message avec pièce jointe se termine
  par ``<attached: nom_fichier.jpg>``. La légende est alors distinguable
  du texte brut avec certitude.
- « sans médias » : WhatsApp remplace la pièce jointe par un texte du
  genre « <Médias omis> » / « image omitted », et une légende éventuelle
  n'est pas préservée séparément — elle se confond avec un message texte.
  Cette ambiguïté est documentée au §13.3 du plan : dans ce cas on ne peut
  pas prouver qu'un message était une image, seulement qu'un nombre suit
  la séquence attendue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.gateway import IncomingMessage

_INVISIBLE_MARKS = re.compile("[‎‏]")

# Format « crochets » (desktop / iOS, FR ou EN selon la langue du téléphone) :
#   [7/30/26, 20:23:02] Karl: #1 <attached: 00003004-PHOTO-2026-07-30-20-23-21.jpg>
_BRACKET_RE = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\]\s"
    r"(?P<author>[^:]+):\s?(?P<body>.*)$"
)

# Format « tiret » (Android, FR) :
#   23/08/2026, 14:32 - Arthur: 651
_DASH_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s-\s"
    r"(?P<author>[^:]+):\s?(?P<body>.*)$"
)

_ATTACHED_RE = re.compile(r"\s*<attached:\s*([^>]*)>\s*$", re.IGNORECASE)
# WhatsApp nomme ses pièces jointes "<id>-TYPE-date-heure.ext" : le marqueur
# TYPE (PHOTO/VIDEO/GIF/AUDIO/DOCUMENT...) est plus fiable que l'extension —
# un GIF s'exporte par exemple en "...-GIF-....mp4", pas en .gif.
_IS_PHOTO_ATTACHMENT_RE = re.compile(r"-PHOTO-|\.(jpe?g|png|webp)$", re.IGNORECASE)
# Export « sans médias » : WhatsApp remplace la pièce jointe par un texte
# « image omitted » / « video omitted » / « <Médias omis> », souvent précédé
# de la légende sur la même ligne (« 781 image omitted »). Le mot avant
# « omitted » donne le type ; « <Médias omis> » (FR) ne le donne pas — on
# suppose alors une photo, cas ultra-dominant. Seule une image porte une
# légende-numéro exploitable ; une vidéo/GIF compte via un texte séparé (§6.4).
_MEDIA_OMITTED_RE = re.compile(
    r"(?:^|\s)"
    r"(?:<m[ée]dias?\s+omis>"
    r"|(?P<kind>image|photo|vid[ée]o|video|gif|audio|sticker|document)"
    r"\s+(?:omitted|omise?)"
    r"|image\s+absente)"
    r"\s*$",
    re.IGNORECASE,
)
_MEDIA_OMITTED_PHOTO_KINDS = {"", "image", "photo"}

# Fenêtre pendant laquelle un message texte juste après une photo sans
# légende est considéré comme le numéro oublié plutôt qu'un message à part.
FOLLOWUP_WINDOW = timedelta(minutes=5)

_SYSTEM_MARKERS = (
    "created group", "a créé le groupe",
    " added ", "a ajouté",
    " removed ", "a retiré",
    " left", "a quitté le groupe",
    "changed the subject", "a modifié le sujet",
    "changed this group", "a changé l'icône",
    "pinned a message", "a épinglé un message",
    "security code changed", "code de sécurité a changé",
    "messages and calls are end-to-end encrypted", "chiffrés de bout en bout",
    "this message was deleted", "ce message a été supprimé",
    "changed their phone number",
    "joined using this group",
    "changed to ",
)


@dataclass
class ImportedEntry:
    ts: datetime
    author: str
    body: str
    has_image: bool  # spécifiquement une photo (pas vidéo/gif/audio/doc)
    has_attachment: bool  # une pièce jointe quelconque, ou média-omis
    caption: str | None
    is_system: bool


def _parse_ts(date: str, time: str) -> datetime:
    d_str, m_str, y_str = date.split("/")
    d, m, year = int(d_str), int(m_str), int(y_str)
    # Ambigu par nature (JJ/MM vs MM/JJ) : si le 2e nombre dépasse 12, c'est
    # forcément le jour ; sinon on retient la convention FR jour/mois.
    day, month = (m, d) if m > 12 else (d, m)
    if year < 100:
        year += 2000
    time_parts = [int(p) for p in time.split(":")]
    hour, minute = time_parts[0], time_parts[1]
    second = time_parts[2] if len(time_parts) > 2 else 0
    return datetime(year, month, day, hour, minute, second)


def _is_system(author: str, body: str) -> bool:
    haystack = f"{author}: {body}".lower()
    return any(marker.lower() in haystack for marker in _SYSTEM_MARKERS)


def _clean_line(raw: str) -> str:
    return _INVISIBLE_MARKS.sub("", raw).rstrip("\n\r")


def parse_export(path: str) -> list[ImportedEntry]:
    entries: list[ImportedEntry] = []
    current: dict | None = None

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = _clean_line(raw)
            match = _BRACKET_RE.match(line) or _DASH_RE.match(line)
            if match:
                if current:
                    entries.append(_finalize(current))
                current = {
                    "ts": _parse_ts(match["date"], match["time"]),
                    "author": match["author"].strip(),
                    "body": match["body"],
                }
            elif current:
                current["body"] += "\n" + line

    if current:
        entries.append(_finalize(current))

    return entries


def _finalize(raw_entry: dict) -> ImportedEntry:
    author = raw_entry["author"]
    body = raw_entry["body"]
    is_system = _is_system(author, body)

    attached_match = _ATTACHED_RE.search(body)
    media_match = None if attached_match else _MEDIA_OMITTED_RE.search(body)
    if attached_match:
        filename = attached_match.group(1)
        has_attachment = True
        has_image = bool(_IS_PHOTO_ATTACHMENT_RE.search(filename))
        caption = (body[: attached_match.start()].strip() or None) if has_image else None
    elif media_match:
        has_attachment = True
        kind = (media_match.groupdict().get("kind") or "").lower()
        has_image = kind in _MEDIA_OMITTED_PHOTO_KINDS
        caption = (body[: media_match.start()].strip() or None) if has_image else None
    else:
        has_attachment = False
        has_image = False
        caption = None

    return ImportedEntry(
        ts=raw_entry["ts"],
        author=author,
        body=body,
        has_image=has_image,
        has_attachment=has_attachment,
        caption=caption,
        is_system=is_system,
    )


def to_message(entry: ImportedEntry, jid: str, message_id: str | None = None) -> IncomingMessage:
    """Convertit une entrée importée en message exploitable par le moteur."""

    return IncomingMessage(
        message_id=message_id,
        jid=jid,
        push_name=entry.author,
        has_image=entry.has_image,
        caption=entry.caption,
        timestamp=entry.ts,
        is_system=entry.is_system,
    )
