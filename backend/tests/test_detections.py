"""乱暴な言葉の検知を、保存して教師の一覧に出す（#21）のテスト。backend フォルダで python -m unittest discover -s tests"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import ai  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from auth_helper import login  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402


class FakeModels:
    def generate_content(self, model, contents, config):
        return type("Response", (), {"text": json.dumps({"reply": "返事", "evidence": []})})()


class DetectionTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        for context in [
            patch.object(ai, "_client", type("Client", (), {"models": FakeModels()})()),
            patch.object(chat, "conversation_limiter", RateLimiter(1000, 60)),
            patch.object(chat, "global_limiter", RateLimiter(1000, 60)),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.app = TestClient(main.app)
        login(self.app)
        self.seed_detected = {s["conversation_id"] for s in main.get_conversation_summaries() if s["detections"]}

    def talk(self, message):
        page = self.app.get("/student/chat?role_id=r001").text
        conversation_id = re.search(r'name="conversation_id" value="([0-9a-f]{32})"', page).group(1)
        response = self.app.post("/chat", data={"message": message, "role_id": "r001", "conversation_id": conversation_id})
        # 検知しても、送信は止めない
        self.assertEqual(200, response.status_code)
        return conversation_id

    def test_rough_message_is_saved_and_listed(self):
        conversation_id = self.talk("お前は死ね")
        summary = main.find_conversation_summary(conversation_id)
        self.assertEqual("high", summary["max_level"])
        self.assertEqual("乱暴な言葉", summary["detections"][0]["type_label"])
        self.assertEqual(200, self.app.get("/teacher/chats?only_detected=1").status_code)
        detected = {s["conversation_id"] for s in main.get_conversation_summaries() if s["detections"]}
        self.assertEqual(self.seed_detected | {conversation_id}, detected)

    def test_reading_discussion_is_not_detected(self):
        conversation_id = self.talk("エリスは精神を病んだけど、豊太郎の行動は無責任だと思う")
        self.assertEqual([], main.find_conversation_summary(conversation_id)["detections"])
        self.assertEqual([], db.read_chat_records()["detections"])

    def test_only_student_messages_are_checked(self):
        with patch.object(chat.moderation, "detect", return_value=[]) as detect:
            self.talk("こんにちは")
        detect.assert_called_once_with("こんにちは")


if __name__ == "__main__":
    unittest.main()
