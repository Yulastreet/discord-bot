import sqlite3
import tempfile
import unittest
from pathlib import Path

from status_utils import create_db_backup, db_info, read_backup_meta


class StatusUtilsTests(unittest.TestCase):
    def test_create_db_backup_overwrites_previous_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "source.db"
            backup_path = root / "backups" / "bot_database_backup.db"
            meta_path = root / "backups" / "bot_database_backup.json"

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE items (name TEXT)")
            conn.execute("INSERT INTO items VALUES ('first')")
            conn.commit()
            conn.close()

            first = create_db_backup(db_path, backup_path, meta_path)

            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM items")
            conn.execute("INSERT INTO items VALUES ('second')")
            conn.commit()
            conn.close()

            second = create_db_backup(db_path, backup_path, meta_path)

            self.assertEqual(first["file"], second["file"])
            self.assertTrue(second["overwrites_previous"])
            conn = sqlite3.connect(backup_path)
            value = conn.execute("SELECT name FROM items").fetchone()[0]
            conn.close()
            self.assertEqual(value, "second")
            self.assertEqual(read_backup_meta(meta_path)["file"], second["file"])

    def test_db_info_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = db_info(Path(tmp) / "missing.db")

        self.assertFalse(info["exists"])
        self.assertIsNone(info["size_bytes"])


if __name__ == "__main__":
    unittest.main()
