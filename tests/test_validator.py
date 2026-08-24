from datetime import datetime

import pytest

from src.gateway import IncomingMessage
from src.validator import validate


def img_msg(caption, has_image=True):
    return IncomingMessage(
        message_id="m1",
        jid="a@s.whatsapp.net",
        push_name="Arthur",
        has_image=has_image,
        caption=caption,
        timestamp=datetime.now(),
    )


@pytest.mark.parametrize(
    "caption,expected,ok",
    [
        ("651", 651, True),
        (" 651 ", 651, True),
        ("0651", 651, True),
        ("#651", 651, True),
        ("651 ", 651, True),
        ("651 🍻", 651, False),
        ("la 651e", 651, False),
        ("650", 651, False),
        ("", 651, False),
    ],
)
def test_validate_caption(caption, expected, ok):
    assert validate(img_msg(caption), expected).ok is ok


def test_no_image_rejected_even_with_valid_caption():
    verdict = validate(img_msg("651", has_image=False), 651)
    assert verdict.ok is False
    assert verdict.reason == "NOT_AN_IMAGE"


def test_no_caption_rejected():
    verdict = validate(img_msg(None), 651)
    assert verdict.ok is False
    assert verdict.reason == "NO_CAPTION"


def test_wrong_number_carries_the_parsed_number():
    verdict = validate(img_msg("650"), 651)
    assert verdict.reason == "WRONG_NUMBER"
    assert verdict.number == 650


def test_correct_number_returns_ok_verdict():
    verdict = validate(img_msg("651"), 651)
    assert verdict.ok is True
    assert verdict.number == 651


@pytest.mark.parametrize(
    "caption",
    ["658 659 660", "658, 659, 660", "658-659-660", "658,659,660"],
)
def test_multiple_consecutive_numbers_starting_at_expected_are_accepted(caption):
    # Vu sur les vraies données : quelqu'un rattrape plusieurs bières d'un
    # coup dans une seule photo. C'est autorisé.
    verdict = validate(img_msg(caption), 658)
    assert verdict.ok is True
    assert verdict.numbers == (658, 659, 660)


def test_multiple_numbers_not_starting_at_expected_are_rejected():
    verdict = validate(img_msg("659 660 661"), 658)
    assert verdict.ok is False
    assert verdict.reason == "WRONG_NUMBER"
    assert verdict.number == 659


def test_multiple_numbers_with_a_gap_are_rejected():
    # "181, 183" (182 manquant) n'est pas une séquence valide.
    verdict = validate(img_msg("181, 183"), 181)
    assert verdict.ok is False
    assert verdict.reason == "WRONG_NUMBER"


def test_multiple_numbers_with_extra_text_are_rejected():
    verdict = validate(img_msg("177 - 180 la prochaine"), 177)
    assert verdict.ok is False
    assert verdict.reason == "CAPTION_NOT_NUMERIC"


def test_single_number_verdict_still_carries_numbers_tuple():
    verdict = validate(img_msg("651"), 651)
    assert verdict.numbers == (651,)
