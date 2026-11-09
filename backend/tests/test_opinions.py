"""対話前の考えと感想の保存（#46）のテスト。backend フォルダで python -m unittest discover -s tests"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402

CONVERSATION_IN_URL = re.compile(r"conversation_id=([0-9a-f]{32})")


class OpinionTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.app = TestClient(main.app)

    def start(self, opinion="豊太郎は弱い人だと思う"):
        # 問いかけ画面から送って、チャット画面へのリダイレクト先から会話 ID を取り出す
        response = self.app.post("/student/question", data={"role_id": "r001", "opinion": opinion}, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        return CONVERSATION_IN_URL.search(response.headers["location"]).group(1)

    def test_pre_opinion_is_saved_and_shown(self):
        conversation_id = self.start()
        self.assertEqual("豊太郎は弱い人だと思う", db.get_conversation(conversation_id)["pre_opinion"])
        chat_page = self.app.get(f"/student/chat?role_id=r001&conversation_id={conversation_id}").text
        self.assertIn(f'name="conversation_id" value="{conversation_id}"', chat_page)
        self.assertIn(f"conversation_id={conversation_id}", chat_page)
        reflection_page = self.app.get(f"/student/reflection?role_id=r001&conversation_id={conversation_id}").text
        self.assertIn("豊太郎は弱い人だと思う", reflection_page)

    def test_reflection_is_saved_once(self):
        conversation_id = self.start()
        data = {"role_id": "r001", "conversation_id": conversation_id}
        self.app.post("/student/reflection", data={**data, "reflection": "考えが変わった"})
        self.app.post("/student/reflection", data={**data, "reflection": "書き換え"})
        self.assertEqual("考えが変わった", db.get_conversation(conversation_id)["reflection"])

    def test_other_role_cannot_use_the_conversation(self):
        conversation_id = self.start()
        chat_page = self.app.get(f"/student/chat?role_id=r002&conversation_id={conversation_id}").text
        self.assertNotIn(conversation_id, chat_page)
        response = self.app.post("/chat", data={"message": "x", "role_id": "r002", "conversation_id": conversation_id})
        self.assertEqual(400, response.status_code)
        self.app.post("/student/reflection", data={"role_id": "r002", "conversation_id": conversation_id, "reflection": "x"})
        self.assertIsNone(db.get_conversation(conversation_id)["reflection"])

    def test_unknown_conversation_falls_back(self):
        page = self.app.get("/student/reflection?role_id=r001&conversation_id=" + "0" * 32)
        self.assertEqual(200, page.status_code)
        self.assertNotIn('name="conversation_id"', page.text)


if __name__ == "__main__":
    unittest.main()
