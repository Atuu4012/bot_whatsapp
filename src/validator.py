"""Regles de conformite d'un message : image + legende = numero(s) attendu(s).

Une legende peut lister plusieurs numeros a la suite ("658 659 660",
"181, 182, 183") : c'est autorise quand quelqu'un rattrape plusieurs bieres
d'un coup dans une seule photo. Pour etre valide, ces numeros doivent former
une sequence strictement consecutive qui demarre exactement au compteur
attendu -- sinon c'est rejete comme n'importe quelle legende invalide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.gateway import IncomingMessage

# Caracteres retires avant de chercher les numeros : espace insecable
# (U+00A0), espace fine insecable (U+202F) et un '#' devant le numero
# (courant sur les exports WhatsApp reels : "#651").
_NOISE_CHARS = (" ", " ", "#")

# Un ou plusieurs nombres, separes par espaces/virgules/tirets -- les trois
# formes vues sur les vraies donnees pour poster plusieurs bieres d'un coup
# ("658 659 660", "181, 182, 183", "239-240-241").
_NUMBER_LIST_RE = re.compile(r"^\d+(?:[\s,\-]+\d+)*$")


@dataclass
class Verdict:
    ok: bool
    reason: str | None = None
    number: int | None = None  # premier numero de la legende (compat / collision)
    numbers: tuple[int, ...] = field(default_factory=tuple)  # tous les numeros, dans l'ordre


def parse_numbers(caption: str) -> tuple[int, ...] | None:
    cleaned = caption.strip()
    for ch in _NOISE_CHARS:
        cleaned = cleaned.replace(ch, "")
    cleaned = cleaned.strip()

    if not _NUMBER_LIST_RE.match(cleaned):
        return None

    return tuple(int(tok) for tok in re.findall(r"\d+", cleaned))


def validate(msg: IncomingMessage, expected: int) -> Verdict:
    if not msg.has_image:
        return Verdict(False, "NOT_AN_IMAGE")

    caption = (msg.caption or "").strip()
    if not caption:
        return Verdict(False, "NO_CAPTION")

    numbers = parse_numbers(caption)
    if numbers is None:
        return Verdict(False, "CAPTION_NOT_NUMERIC")

    first = numbers[0]
    is_consecutive = all(n == first + i for i, n in enumerate(numbers))

    if not is_consecutive or first != expected:
        return Verdict(False, "WRONG_NUMBER", number=first, numbers=numbers)

    return Verdict(True, number=first, numbers=numbers)
