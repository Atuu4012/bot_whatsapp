import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from import_history import import_history  # noqa: E402

from src.db import Database  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _write_mapping(path: Path, names: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["nom_export", "jid"])
        for name in names:
            writer.writerow([name, f"{name.lower().replace(' ', '-')}@s.whatsapp.net"])


def test_photo_without_caption_completed_by_next_message(tmp_path):
    mapping = tmp_path / "membres.csv"
    _write_mapping(mapping, ["Tomas", "Barr"])
    db_path = tmp_path / "beerbot.db"

    import_history(str(FIXTURES / "sample_empty_message.txt"), str(mapping), str(db_path))

    db = Database(str(db_path))
    numbers = {r["number"] for r in db.conn.execute("SELECT number FROM beers").fetchall()}
    assert 665 in numbers  # légende directe, correcte
    assert 667 in numbers  # photo sans légende + numéro dans le message suivant
    assert 664 in numbers


def test_wrong_caption_corrected_by_followup_message(tmp_path):
    export = tmp_path / "export.txt"
    export.write_text(
        "[1/1/26, 10:00:00] Alix: 1 <attached: a.jpg>\n"
        "[1/1/26, 10:00:05] Alix: 21 <attached: b.jpg>\n"
        "[1/1/26, 10:00:16] Alix: 2*\n",
        encoding="utf-8",
    )
    mapping = tmp_path / "membres.csv"
    _write_mapping(mapping, ["Alix"])
    db_path = tmp_path / "beerbot.db"

    import_history(str(export), str(mapping), str(db_path))

    db = Database(str(db_path))
    numbers = {r["number"] for r in db.conn.execute("SELECT number FROM beers").fetchall()}
    assert numbers == {1, 2}  # "21" corrigé en "2", jamais compté tel quel


def test_followup_outside_window_is_not_merged(tmp_path):
    export = tmp_path / "export.txt"
    export.write_text(
        "[1/1/26, 10:00:00] Alix: 1 <attached: a.jpg>\n"
        "[1/1/26, 10:00:05] Alix: <attached: b.jpg>\n"
        "[1/1/26, 10:20:00] Alix: 2\n",  # 20 min plus tard : hors fenêtre
        encoding="utf-8",
    )
    mapping = tmp_path / "membres.csv"
    _write_mapping(mapping, ["Alix"])
    db_path = tmp_path / "beerbot.db"

    import_history(str(export), str(mapping), str(db_path))

    db = Database(str(db_path))
    numbers = {r["number"] for r in db.conn.execute("SELECT number FROM beers").fetchall()}
    assert numbers == {1}  # la photo sans légende reste non comptée
