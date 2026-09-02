from pathlib import Path

from src.importer import parse_export, to_message

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_dash_format_android():
    entries = parse_export(FIXTURES / "sample_dash.txt")

    assert [e.author for e in entries] == ["Arthur", "Marie", "Karl"]
    assert entries[0].caption is None  # pas d'<attached>, indissociable d'un texte
    assert entries[0].body == "651"
    assert entries[1].has_image is True  # "<Médias omis>" reconnu
    assert entries[1].caption is None


def test_parses_bracket_format_with_attachments():
    entries = parse_export(FIXTURES / "sample_bracket_attached.txt")

    # 2 messages système (chiffrement + création du groupe), puis
    # 2 photos numérotées, un message texte multi-lignes, une photo sans légende.
    system = [e for e in entries if e.is_system]
    assert len(system) == 2

    photos = [e for e in entries if e.has_image]
    assert len(photos) == 3

    first_photo = photos[0]
    assert first_photo.author == "Karl"
    assert first_photo.caption == "#1"

    second_photo = photos[1]
    assert second_photo.author == "Arthur Parizot"
    assert second_photo.caption == "2"

    last_photo = photos[2]
    assert last_photo.author == "Leon"
    assert last_photo.caption is None  # photo sans légende


def test_multiline_text_message_is_kept_together():
    entries = parse_export(FIXTURES / "sample_bracket_attached.txt")
    welcome = next(e for e in entries if e.author == "Karl" and "Bienvenue" in e.body)

    assert "Suite du message sur plusieurs lignes." in welcome.body
    assert welcome.has_image is False


def test_invisible_marks_are_stripped_from_author_and_body():
    entries = parse_export(FIXTURES / "sample_bracket_attached.txt")
    for entry in entries:
        assert "‎" not in entry.author
        assert "‎" not in entry.body


def test_empty_message_does_not_swallow_the_next_line():
    # Bug réel : "Barr:" (message vide, rien après les deux-points) était
    # traité comme une continuation du message précédent au lieu d'une
    # nouvelle entrée, ce qui cassait le <attached:...> de la photo "665".
    entries = parse_export(FIXTURES / "sample_empty_message.txt")

    photo_665 = next(e for e in entries if e.caption and "665" in e.caption)
    assert photo_665.has_image is True
    assert photo_665.caption == "665 bien fraiche"

    empty_entries = [e for e in entries if e.author == "Barr" and e.body.strip() == ""]
    assert len(empty_entries) == 1


def test_video_and_gif_attachments_are_not_treated_as_photos():
    entries = parse_export(FIXTURES / "sample_empty_message.txt")

    video = next(e for e in entries if "VIDEO" in e.body)
    gif = next(e for e in entries if "GIF" in e.body)

    assert video.has_image is False
    assert gif.has_image is False


def test_photo_without_caption_followed_by_bare_number():
    entries = parse_export(FIXTURES / "sample_empty_message.txt")

    photo = next(e for e in entries if "00003820" in e.body)
    assert photo.has_image is True
    assert photo.caption is None

    followup = next(e for e in entries if e.body.strip() == "667")
    assert followup.has_image is False
    assert followup.author == photo.author


def test_media_omitted_line_with_a_leading_caption_is_a_captioned_photo(tmp_path):
    # Export « sans médias » : la pièce jointe et sa légende sont sur la même
    # ligne (« 781 image omitted »). Le numéro doit être récupéré.
    export = tmp_path / "export.txt"
    export.write_text(
        "\u200E[30/07/2026, 21:12:16] Karl: 6 \u200Eimage omitted\n"
        "\u200E[30/07/2026, 21:13:07] Alix: 17 rouge et vert \u200Eimage omitted\n"
        "\u200E[30/07/2026, 21:14:00] Alix: \u200Eimage omitted\n"
        "\u200E[30/07/2026, 21:15:00] Karl: 42 \u200Evideo omitted\n",
        encoding="utf-8",
    )
    entries = parse_export(str(export))

    assert entries[0].has_image is True
    assert entries[0].caption == "6"
    assert entries[1].has_image is True
    assert entries[1].caption == "17 rouge et vert"
    assert entries[2].has_image is True  # légende absente
    assert entries[2].caption is None
    assert entries[3].has_image is False  # vidéo : pas une photo
    assert entries[3].has_attachment is True
    assert entries[3].caption is None


def test_to_message_carries_fields_through():
    entries = parse_export(FIXTURES / "sample_dash.txt")
    msg = to_message(entries[0], jid="arthur@s.whatsapp.net", message_id="abc")

    assert msg.jid == "arthur@s.whatsapp.net"
    assert msg.message_id == "abc"
    assert msg.push_name == "Arthur"
    assert msg.caption is None
    assert msg.timestamp == entries[0].ts
