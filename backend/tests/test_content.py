"""content.py のテスト。backend フォルダで python -m unittest discover -s tests"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import content  # noqa: E402

SEED = json.loads((BACKEND / "data" / "seed_dummy_data.json").read_text(encoding="utf-8"))


class ContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = content.apply_maihime(SEED)

    def test_material_is_replaced_and_lines_are_continuous(self):
        maihime = next(m for m in self.data["materials"] if m["id"] == "m001")
        paragraphs = maihime["paragraphs"]
        self.assertEqual(73, len(paragraphs))
        self.assertEqual(1, paragraphs[0]["line_start"])
        for before, after in zip(paragraphs, paragraphs[1:]):
            self.assertEqual(before["line_end"] + 1, after["line_start"])
        # ほかの教材はそのまま
        others = [m for m in self.data["materials"] if m["id"] != "m001"]
        self.assertEqual([m for m in SEED["materials"] if m["id"] != "m001"], others)

    def test_roles_keep_seed_ids_and_images(self):
        roles = self.data["preset_roles"]["roles"]
        seed_roles = SEED["preset_roles"]["roles"]
        self.assertEqual([r["id"] for r in seed_roles], [r["id"] for r in roles])
        self.assertEqual([r["image"] for r in seed_roles], [r["image"] for r in roles])
        self.assertEqual("豊太郎の葛藤を重く見る読者", roles[0]["name"])

    def test_seed_data_is_not_changed(self):
        self.assertEqual("r001", SEED["preset_roles"]["roles"][0]["id"])
        self.assertNotEqual("豊太郎の葛藤を重く見る読者", SEED["preset_roles"]["roles"][0]["name"])
        self.assertNotIn(73, [len(m["paragraphs"]) for m in SEED["materials"]])

    def test_existing_line_numbers_are_kept(self):
        paragraphs = content.add_line_numbers([{"text": "あ" * 10, "line_start": 5, "line_end": 7}, {"text": "い" * 41}])
        self.assertEqual((5, 7), (paragraphs[0]["line_start"], paragraphs[0]["line_end"]))
        self.assertEqual((8, 9), (paragraphs[1]["line_start"], paragraphs[1]["line_end"]))


if __name__ == "__main__":
    unittest.main()
