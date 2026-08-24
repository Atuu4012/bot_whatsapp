"""Règles de conformité d'un message : image + légende = numéro attendu."""

from __future__ import annotations

from dataclasses import dataclass

from src.gateway import IncomingMessage

# Caractères tolérés autour du nombre : espace normal, espace insécable
# (U+00A0), espace fine insécable (U+202F), point (séparateur de milliers),
# et un '#' devant le numéro (courant sur les exports WhatsApp réels : "#651").
_STRIP_CHARS = (" ", " ", " ", ".", "#")


@dataclass
class Verdict:
    ok: bool
    reason: str | None = None
    number: int | None = None


def validate(msg: IncomingMessage, expected: int) -> Verdict:
    if not msg.has_image:
        return Verdict(False, "NOT_AN_IMAGE")

    caption = (msg.caption or "").strip()
    if not caption:
        return Verdict(False, "NO_CAPTION")

    cleaned = caption
    for ch in _STRIP_CHARS:
        cleaned = cleaned.replace(ch, "")

    if not cleaned.isdigit():
        return Verdict(False, "CAPTION_NOT_NUMERIC")

    n = int(cleaned)
    if n != expected:
        return Verdict(False, "WRONG_NUMBER", number=n)

    return Verdict(True, number=n)
