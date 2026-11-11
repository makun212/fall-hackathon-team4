"""教師のチャット一覧に、DBの会話が出ることのテスト（#20）。backend フォルダで python -m unittest discover -s tests"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
