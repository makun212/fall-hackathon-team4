"""教師のチャット一覧に、DBの会話が出ることのテスト（#20）。backend フォルダで python -m unittest discover -s tests"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402
from auth_helper import login  # noqa: E402

CONVERSATION_ID = "a" * 32


class TeacherChatTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.app = TestClient(main.app)
        login(self.app)
        self.seed_count = len(main.get_conversation_summaries())

    def test_db_conversation_is_listed_with_seed_conversations(self):
        db.create_conversation(CONVERSATION_ID, "r003", "考え")
        db.save_messages(CONVERSATION_ID, "r003", [{"role": "user", "content": "質問"}, {"role": "assistant", "content": "返事"}])
        summaries = main.get_conversation_summaries()
        self.assertEqual(self.seed_count + 1, len(summaries))
        mine = main.find_conversation_summary(CONVERSATION_ID)
        self.assertEqual("ゲスト", mine["student_name"])
        self.assertEqual("返事", mine["last_message"])
        self.assertEqual(2, mine["message_count"])
        self.assertEqual(200, self.app.get("/teacher/chats").status_code)
        self.assertEqual(200, self.app.get(f"/teacher/chats/{CONVERSATION_ID}").status_code)

    def test_time_is_shown_in_japan_time(self):
        self.assertEqual("2026-11-09T09:00:00+09:00", db.to_jst_text(datetime(2026, 11, 9, 0, 0)))
        self.assertEqual("2026-11-09T09:00:00+09:00", db.to_jst_text(datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)))

    def test_selected_material_excludes_other_materials_and_seed_data(self):
        db.create_conversation(CONVERSATION_ID, "r003", "考え", material_set_id="rashomon")
        sets = main.get_confirmed_material_sets() + [{"id": "rashomon", "title": "羅生門"}]
        with patch.object(main, "get_confirmed_material_sets", return_value=sets):
            for suffix in ("", "&only_detected=1"):
                response = self.app.get("/teacher/chats?set=rashomon" + suffix)
                self.assertEqual(200, response.status_code)
                self.assertEqual(1, response.context["total_count"])
                self.assertEqual("羅生門", response.context["material_set_title"])
                self.assertTrue(all(c["material_set_id"] == "rashomon" for c in response.context["conversations"]))
            db.create_conversation("b" * 32, "r003", "別の考え", material_set_id="ms001")
            response = self.app.get("/teacher/chats?set=rashomon")
            self.assertEqual([CONVERSATION_ID], [c["conversation_id"] for c in response.context["conversations"]])

    def test_selected_material_without_chats_shows_empty_list(self):
        with patch.object(main, "get_confirmed_material_sets", return_value=[{"id": "rashomon", "title": "羅生門"}]):
            response = self.app.get("/teacher/chats?set=rashomon")
        self.assertEqual(0, response.context["total_count"])
        self.assertIn("まだ会話がありません", response.text)
        self.assertIn("羅生門", response.text)

    def test_unknown_material_returns_404(self):
        self.assertEqual(404, self.app.get("/teacher/chats?set=unknown").status_code)


if __name__ == "__main__":
    unittest.main()
