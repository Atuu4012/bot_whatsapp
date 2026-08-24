import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from import_history import import_history, reconcile  # noqa: E402

from src.db import Database  # noqa: E402
from src.importer import parse_export  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _write_mapping(path: Path, names: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["nom_export", "jid"])
        for name in names:
            writer.writerow([name, f"{name.lower().replace(' ', '-')}@s.whatsapp.net"])


def _import(tmp_path, export_text, authors):
    export = tmp_path / "export.txt"
    export.write_text(export_text, encoding="utf-8")
    mapping = tmp_path / "membres.csv"
    _write_mapping(mapping, authors)
    db_path = tmp_path / "beerbot.db"

    import_history(str(export), str(mapping), str(db_path))

    db = Database(str(db_path))
    return {r["number"] for r in db.conn.execute("SELECT number FROM beers").fetchall()}


def test_photo_without_caption_completed_by_next_message(tmp_path):
    numbers = _import(
        tmp_path,
        "[1/1/26, 10:00:00] Tomas: 1 <attached: a.jpg>\n"
        "[1/1/26, 10:00:05] Tomas: <attached: b.jpg>\n"
        "[1/1/26, 10:00:10] Tomas: 2\n",
        ["Tomas"],
    )
    assert numbers == {1, 2}


def test_number_announced_before_the_photo_arrives(tmp_path):
    # Cas réel vu deux fois dans l'export ("284", "409") : le numéro est
    # envoyé en texte, la photo (sans légende) suit quelques secondes après.
    numbers = _import(
        tmp_path,
        "[1/1/26, 10:00:00] Coucs: 1 <attached: a.jpg>\n"
        "[1/1/26, 13:19:19] Coucs: 2\n"
        "[1/1/26, 13:19:38] Coucs: <attached: b.jpg>\n",
        ["Coucs"],
    )
    assert numbers == {1, 2}


def test_two_uncaptioned_photos_resolved_by_one_message_listing_both_numbers(tmp_path):
    # Cas réel : deux photos sans légende, un autre auteur interpelle entre
    # les deux ("les numéros"), puis "563,564" résout les deux d'un coup.
    numbers = _import(
        tmp_path,
        "[1/1/26, 10:00:00] Ines: 1 <attached: a.jpg>\n"
        "[1/1/26, 22:39:46] Ines: <attached: b.jpg>\n"
        "[1/1/26, 22:39:48] Ines: <attached: c.jpg>\n"
        "[1/1/26, 22:40:49] Zav: les numeros\n"
        "[1/1/26, 22:41:31] Ines: 2,3\n",
        ["Ines", "Zav"],
    )
    assert numbers == {1, 2, 3}


def test_number_paired_with_a_video_attachment_still_counts(tmp_path):
    # Cas réel ("76") : légende envoyée en texte, suivie d'une vidéo (pas
    # une photo) la même seconde. Compté quand même, sur demande explicite.
    numbers = _import(
        tmp_path,
        "[1/1/26, 10:00:00] Toma: 1 <attached: a.jpg>\n"
        "[1/1/26, 04:21:57] Toma: 2\n"
        "[1/1/26, 04:21:57] Toma: <attached: v-VIDEO-x.mp4>\n",
        ["Toma"],
    )
    assert numbers == {1, 2}


def test_irrelevant_chat_between_photo_and_followup_does_not_block_reconciliation(tmp_path):
    # Cas réel ("260") : une photo sans légende, un "video omitted" sans
    # rapport s'intercale, puis le vrai numéro arrive.
    numbers = _import(
        tmp_path,
        "[1/1/26, 10:00:00] Manon: 1 <attached: a.jpg>\n"
        "[1/1/26, 21:39:09] Manon: <attached: b.jpg>\n"
        "[1/1/26, 21:39:10] Manon: video omitted\n"
        "[1/1/26, 21:39:13] Manon: 2\n",
        ["Manon"],
    )
    assert numbers == {1, 2}


def test_wrong_caption_treated_as_typo_when_number_already_taken(tmp_path):
    # Cas réel ("21 »" pour "210") : ce qui rend "21" suspect, ce n'est pas
    # qu'il "ne suit pas" un compteur (les trous sont permis, §6.4) — c'est
    # que 21 a déjà été posté ailleurs. On le simule en le pré-important.
    export = tmp_path / "export.txt"
    export.write_text(
        "[1/1/26, 10:00:00] Alix: 1 <attached: a.jpg>\n"
        "[1/1/26, 10:00:05] Alix: 21 <attached: b.jpg>\n"
        "[1/1/26, 10:00:16] Alix: 2*\n",
        encoding="utf-8",
    )
    entries = parse_export(export)

    resolved, corrected, unresolved = reconcile(entries, already_used={21})

    resolved_numbers = {n for _, _, nums in resolved for n in nums}
    assert resolved_numbers == {1, 2}  # "21" corrigé en "2", jamais réinséré comme doublon
    assert corrected == 1


def test_followup_outside_window_is_not_merged(tmp_path):
    numbers = _import(
        tmp_path,
        "[1/1/26, 10:00:00] Alix: 1 <attached: a.jpg>\n"
        "[1/1/26, 10:00:05] Alix: <attached: b.jpg>\n"
        "[1/1/26, 10:20:00] Alix: 2\n",  # 20 min plus tard : hors fenêtre
        ["Alix"],
    )
    assert numbers == {1}  # la photo sans légende reste non comptée


def test_reconcile_resolves_665_and_667_from_the_real_shaped_fixture():
    entries = parse_export(FIXTURES / "sample_empty_message.txt")
    resolved, corrected, unresolved = reconcile(entries)

    resolved_numbers = {n for _, _, nums in resolved for n in nums}
    assert 665 in resolved_numbers
    assert 667 in resolved_numbers
    assert corrected >= 1


def test_reconcile_accepts_a_gap_in_the_sequence_without_reconciliation(tmp_path):
    # §6.4 : les trous sont permis. Un numéro qui ne suit pas immédiatement
    # le précédent doit être accepté directement, pas mis en attente.
    export = tmp_path / "export.txt"
    export.write_text(
        "[1/1/26, 10:00:00] Karl: 1 <attached: a.jpg>\n"
        "[1/1/26, 10:01:00] Karl: 5 <attached: b.jpg>\n",  # 2,3,4 manquent : trou toléré
        encoding="utf-8",
    )
    entries = parse_export(export)
    resolved, corrected, unresolved = reconcile(entries)

    resolved_numbers = {n for _, _, nums in resolved for n in nums}
    assert resolved_numbers == {1, 5}
    assert corrected == 0
    assert unresolved == 0


def test_reconcile_accepts_a_multi_number_caption_even_with_a_gap_before_it(tmp_path):
    # Le cas réel exact qui a régressé : "181 , 182 , 183" arrive après un
    # trou (177 manquant, 178 jamais résolu) et doit quand même être compté.
    export = tmp_path / "export.txt"
    export.write_text(
        "[1/1/26, 10:00:00] Hugo: 176 <attached: a.jpg>\n"
        "[1/1/26, 10:01:00] Hugo: 178 <attached: b.jpg>\n"  # 177 manque : trou
        "[1/1/26, 10:02:00] Hugo: 181 , 182 , 183 <attached: c.jpg>\n",
        encoding="utf-8",
    )
    entries = parse_export(export)
    resolved, corrected, unresolved = reconcile(entries)

    resolved_numbers = {n for _, _, nums in resolved for n in nums}
    assert resolved_numbers == {176, 178, 181, 182, 183}
    assert corrected == 0
