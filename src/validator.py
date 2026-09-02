"""Regles de conformite d'un message : image + legende = numero(s) attendu(s).

Une legende peut lister plusieurs numeros a la suite ("658 659 660",
"181, 182, 183") : c'est autorise quand quelqu'un rattrape plusieurs bieres
d'un coup dans une seule photo. Pour etre valide, ces numeros doivent former
une sequence strictement consecutive qui demarre exactement au compteur
attendu.

Du texte ou des emojis peuvent entourer le(s) numero(s) ("651 🍻",
"la 651e", "17 rouge et vert ca fait jaune" -- vu sur les vraies donnees),
tant que ca reste court : au-dela de MAX_EXTRA_WORDS mots hors numeros,
la legende est consideree trop chargee et rejetee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.gateway import IncomingMessage

# Caracteres retires avant de chercher les numeros : espace insecable
# (U+00A0), espace fine insecable (U+202F) et un '#' devant le numero
# (courant sur les exports WhatsApp reels : "#651").
_NOISE_CHARS = (" ", " ", "#")

_DIGITS_RE = re.compile(r"\d+")

# Rattrapage en lot : « 829 (x4) » = 4 bières consécutives dont la dernière
# porte le numéro 829, donc 826,827,828,829. Tolère « 829x4 », « 829 x 4 »,
# « 829 [x4] ». Le lookahead évite de confondre avec une dimension ou un
# volume (« 6x9 », « 2x50cl ») ; le nombre de bières est borné plus bas.
_MULTIPLIER_RE = re.compile(
    r"(?P<end>\d+)\s*[(\[]?\s*x\s*(?P<count>\d{1,2})(?![A-Za-z\d])\s*[)\]]?",
    re.IGNORECASE,
)
# Une seule photo ne rattrape pas des dizaines de bières : au-delà, c'est
# sûrement autre chose qu'un multiplicateur.
_MULTIPLIER_MAX_COUNT = 12

# "quelques mots" de texte/emoji tolérés autour du/des numéro(s). Calibré sur
# les vraies légendes observées (1 à 6 mots, ex. "rouge et vert ça fait jaune").
MAX_EXTRA_WORDS = 6

# Au-delà, un numéro en avance n'est plus un trou plausible mais une faute de
# frappe (« 8760 » pour « 876 ») : le comblement créerait des milliers de
# lignes « - » et emporterait le compteur. Large exprès — le bot peut avoir
# raté une soirée entière de bières pendant une coupure.
MAX_GAP = 50


@dataclass
class Verdict:
    ok: bool
    reason: str | None = None
    number: int | None = None  # premier numero de la legende (compat / collision)
    numbers: tuple[int, ...] = field(default_factory=tuple)  # tous les numeros, dans l'ordre


def parse_numbers(caption: str) -> tuple[int, ...] | None:
    """Extrait le ou les numéros d'une légende, en tolérant du texte/emoji
    autour tant qu'il en reste peu. Retourne None si aucun numéro n'est
    trouvé, ou si trop de texte l'entoure."""

    cleaned = caption.strip()
    for ch in _NOISE_CHARS:
        cleaned = cleaned.replace(ch, "")

    mult = _MULTIPLIER_RE.search(cleaned)
    if mult:
        end, count = int(mult["end"]), int(mult["count"])
        remainder = cleaned[: mult.start()] + cleaned[mult.end() :]
        looks_like_batch = (
            2 <= count <= min(_MULTIPLIER_MAX_COUNT, end)
            and len([w for w in remainder.split() if w.strip(",.-")]) <= MAX_EXTRA_WORDS
        )
        if looks_like_batch:
            return tuple(range(end - count + 1, end + 1))
        # multiplicateur douteux (« 6x9 », « 2x50cl »…) : on retombe sur
        # l'extraction classique des chiffres ci-dessous.

    numbers = tuple(int(tok) for tok in _DIGITS_RE.findall(cleaned))
    if not numbers:
        return None

    remainder = _DIGITS_RE.sub(" ", cleaned)
    extra_words = [w for w in remainder.split() if w.strip(",.-")]
    if len(extra_words) > MAX_EXTRA_WORDS:
        return None

    return numbers


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

    if not is_consecutive:
        return Verdict(False, "WRONG_NUMBER", number=first, numbers=numbers)

    if expected < first <= expected + MAX_GAP:
        # Numéro sauté : la légende est bien formée, elle démarre juste trop
        # loin. Ce n'est pas une faute de la même nature qu'un numéro faux —
        # le moteur comble le trou et prévient, il ne sanctionne pas.
        return Verdict(False, "NUMBER_AHEAD", number=first, numbers=numbers)

    if first != expected:
        return Verdict(False, "WRONG_NUMBER", number=first, numbers=numbers)

    return Verdict(True, number=first, numbers=numbers)
