"""独立した判定と舞姫データ・既存の画面契約の互換性を確認する。"""

import json
from pathlib import Path
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from moderation import detect


class ModerationTests(unittest.TestCase):
    def test_explicit_threats(self):
        for text in ["死ね", "お前は死ね！", "あなたを殺す", "消えろ", "さっさと死ね", "シネ", "ｼﾈ", "死\u200bね"]:
            with self.subTest(text=text):
                self.assertEqual([{"type": "rough_language", "level": "high"}], detect(text))

    def test_personal_insults(self):
        for text in ["お前は馬鹿", "あなたは無能！", "君ってあほ", "バカ", "ｱﾎ", "こいつはくず", "お前はばかだ", "あなたは無能です"]:
            with self.subTest(text=text):
                self.assertEqual([{"type": "rough_language", "level": "medium"}], detect(text))

    def test_literary_discussion_and_disagreement_are_not_abuse(self):
        for text in ["", "  ", "豊太郎の行動は無責任だ", "その意見には反対です", "エリスは精神を病んだ", "死と貧困について考えたい", "馬鹿ではない", "あなたはばかではない", "しねない", "私ね、考えたんです"]:
            with self.subTest(text=text):
                self.assertEqual([], detect(text))

    def test_explicit_analysis_of_quotes(self):
        for text in ["「死ね」という言葉は乱暴です", "『消えろ』の意味を考えたい", "「お前は馬鹿」と書かれている場面を読む"]:
            with self.subTest(text=text):
                self.assertEqual([], detect(text))

    def test_quotes_do_not_hide_a_separate_attack(self):
        self.assertEqual("high", detect("「死ね」という言葉の意味。あなたを殺す")[0]["level"])
        self.assertEqual("high", detect("「死ね」")[0]["level"])

    def test_highest_level_only_and_no_shared_mutable_result(self):
        result = detect("お前はばか。死ね！")
        self.assertEqual([{"type": "rough_language", "level": "high"}], result)
        result[0]["level"] = "changed"
        self.assertEqual("high", detect("死ね")[0]["level"])

    def test_full_maihime_corpus_has_no_false_positive(self):
        data = json.loads((BACKEND / "data/preparation/maihime_material.json").read_text(encoding="utf-8"))
        for paragraph in data["material"]["paragraphs"]:
            with self.subTest(paragraph=paragraph["id"]):
                self.assertEqual([], detect(paragraph["text"]))

    def test_all_preset_role_materials_have_no_false_positive(self):
        data = json.loads((BACKEND / "data/preparation/maihime_preset_roles.json").read_text(encoding="utf-8"))
        for role in data["roles"]:
            for text in [role["name"], role["summary"], role["prompt_hint"], *role["features"], *(b["note"] for b in role["basis"])]:
                with self.subTest(role=role["id"], text=text):
                    self.assertEqual([], detect(text))

    def test_non_string_inputs_are_rejected(self):
        for value in [None, 1, [], {}]:
            with self.subTest(value=value), self.assertRaises(TypeError):
                detect(value)


if __name__ == "__main__":
    unittest.main()
