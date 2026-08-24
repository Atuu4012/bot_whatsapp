"""Regles de conformite d'un message : image + legende = numero attendu."""

from __future__ import annotations

from dataclasses import dataclass

from src.gateway import IncomingMessage

# Caracteres tolerés a l'interieur du nombre : espace insecable (U+00A0),
# espace fine insecable (U+202F) et point, utilises comme separateurs de
# milliers ("1 000", "1.000"), et un '#' devant le numero (courant sur
# les exports WhatsApp reels : "#651").
#
# On ne retire volontairement PAS l'espace ASCII normal : sur les vraies
# donnees, des legendes comme "658 659 660" (plusieurs numeros colles par
# un humain qui rattrape son retard) existent bien. Les fusionner en un
# seul nombre serait un faux positif silencieux -- on prefere les rejeter
# comme CAPTION_NOT_NUMERIC.
_STRIP_CHARS = (" ", " ", ".", "#")


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
