"""#62の原文を変えず、#70の仮行番号をデータに明示したことを検証する。"""

import hashlib
import json
from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from content import add_line_numbers, apply_maihime, LINE_LENGTH


class MaterialLineDataTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((BACKEND / "data/preparation/maihime_material.json").read_text(encoding="utf-8"))
        self.paragraphs = self.data["material"]["paragraphs"]

    def test_original_text_order_and_gaiji_placeholders_unchanged(self):
        digest = hashlib.sha256(json.dumps([(p["id"], p["text"]) for p in self.paragraphs], ensure_ascii=False).encode()).hexdigest()
        self.assertEqual("c0b46abd3065cbfef8ab312c08da962b1e3300caecd730fcfb3f992b94bedf39", digest)
        self.assertEqual([f"p{i:03d}" for i in range(1, 74)], [p["id"] for p in self.paragraphs])

    def test_explicit_numbers_match_existing_fallback(self):
        without_lines = [{k: v for k, v in p.items() if k not in {"line_start", "line_end"}} for p in self.paragraphs]
        self.assertEqual(40, LINE_LENGTH)
        self.assertEqual(add_line_numbers(without_lines), self.paragraphs)
        self.assertEqual(1, self.paragraphs[0]["line_start"])
        self.assertEqual(426, self.paragraphs[-1]["line_end"])

    def test_app_receives_same_data_and_required_metadata(self):
        seed = json.loads((BACKEND / "data/seed_dummy_data.json").read_text(encoding="utf-8"))
        material = next(m for m in apply_maihime(seed)["materials"] if m["id"] == "m001")
        self.assertEqual(self.data["material"], material)
        for key in ["title", "author", "opening_preview", "source"]:
            self.assertTrue(material[key])
        notes = json.loads((BACKEND / "data/preparation/maihime_source_notes.json").read_text(encoding="utf-8"))
        self.assertIn("仮番号", notes["extraction"]["line_number_definition"])


if __name__ == "__main__":
    unittest.main()
