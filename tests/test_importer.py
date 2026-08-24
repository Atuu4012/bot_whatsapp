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


def test_to_message_carries_fields_through():
    entries = parse_export(FIXTURES / "sample_dash.txt")
    msg = to_message(entries[0], jid="arthur@s.whatsapp.net", message_id="abc")

    assert msg.jid == "arthur@s.whatsapp.net"
    assert msg.message_id == "abc"
    assert msg.push_name == "Arthur"
    assert msg.caption is None
    assert msg.timestamp == entries[0].ts
