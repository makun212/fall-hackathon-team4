"""ロールの編集と教材セットの保存（#13）のテスト。backend フォルダで python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402

RANGE = {"material_id": "m001", "range_type": "paragraph", "start": "3", "end": "10"}


class MaterialSetTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.app = TestClient(main.app)

    def edit(self, candidate_id, name, features):
        data = {**RANGE, "name": name, "feature_1": features[0], "feature_2": features[1], "feature_3": features[2]}
        return self.app.post(f"/teacher/candidates/{candidate_id}/edit", data=data, follow_redirects=False)

    def test_edit_is_saved_in_db(self):
        response = self.edit("r001", "新しい立場", ["特徴A", "特徴B", "特徴C"])
        self.assertEqual(303, response.status_code)
        self.assertEqual({"r001": {"name": "新しい立場", "features": ["特徴A", "特徴B", "特徴C"]}}, db.read_role_edits("m001"))
        candidate = next(c for c in main.get_role_candidates("m001") if c["candidate_id"] == "r001")
        self.assertEqual("新しい立場", candidate["name"])
        self.assertTrue(candidate["edited"])

    def test_edit_back_to_original_is_saved_as_reset(self):
        original = next(c for c in main.get_role_candidates("m001") if c["candidate_id"] == "r002")["original"]
        self.edit("r002", "別の名前", ["1", "2", "3"])
        self.edit("r002", original["name"], original["features"])
        self.assertEqual({"r002": None}, db.read_role_edits("m001"))

    def test_confirm_saves_material_set(self):
        before = len(main.get_confirmed_material_sets())
        self.edit("r003", "編集した立場", ["特徴1", "特徴2", "特徴3"])
        response = self.app.post(
            "/teacher/candidates/confirm", data={**RANGE, "selected": ["r001", "r003"]}, follow_redirects=False
        )
        self.assertEqual(303, response.status_code)
        saved = db.read_material_sets()
        self.assertEqual(1, len(saved))
        self.assertEqual((3, 10), (saved[0]["range_start"], saved[0]["range_end"]))
        self.assertEqual(["r001", "r003"], [r["role_id"] for r in saved[0]["roles"]])
        self.assertEqual("編集した立場", saved[0]["roles"][1]["name"])
        self.assertTrue(saved[0]["roles"][1]["edited"])
        sets = main.get_confirmed_material_sets()
        self.assertEqual(before + 1, len(sets))
        self.assertEqual("第3〜10段落", sets[0]["range_label"])
        self.assertEqual(200, self.app.get("/teacher/material-sets").status_code)

    def test_invalid_confirm_is_not_saved(self):
        self.app.post("/teacher/candidates/confirm", data={**RANGE, "selected": []})
        self.assertEqual([], db.read_material_sets())

    def test_line_range_becomes_paragraph_range(self):
        paragraphs = next(m["paragraphs"] for m in main.SEED_DATA["materials"] if m["id"] == "m001")
        second = paragraphs[1]
        self.assertEqual((2, 2), main.paragraph_range("m001", "line", str(second["line_start"]), str(second["line_end"])))
        self.assertEqual((1, len(paragraphs)), main.paragraph_range("m001", "all", "", ""))


if __name__ == "__main__":
    unittest.main()
