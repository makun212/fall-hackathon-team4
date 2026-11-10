"""/chat のテスト。本物の Gemini は呼ばず、偽物の client を使う。backend フォルダで python -m unittest discover -s tests"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from google.genai import errors  # noqa: E402

import ai  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self):
        self.calls = []
        self.error_code = None
        self.evidence = []

    def generate_content(self, model, contents, config):
        self.calls.append({"system": config.system_instruction, "contents": [(c.role, c.parts[0].text) for c in contents]})
        if self.error_code:
            raise errors.APIError(self.error_code, {"error": {"message": "test", "status": "TEST"}})
        return FakeResponse(json.dumps({"reply": f"返事{len(self.calls)}", "evidence": self.evidence}, ensure_ascii=False))


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


class ChatTests(unittest.TestCase):
    def setUp(self):
        # テストごとに、メモリ上の空のDBを使う
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        # 回数の制限は test_rate_limit.py で確かめるので、ここではゆるくする
        chat.conversation_limiter = RateLimiter(1000, 60)
        chat.global_limiter = RateLimiter(1000, 60)
        self.client = FakeClient()
        ai._client = self.client
        self.app = TestClient(main.app)
        page = self.app.get("/student/chat?role_id=r002").text
        self.conversation_id = re.search(r'name="conversation_id" value="([0-9a-f]{32})"', page).group(1)

    def tearDown(self):
        ai._client = None

    def send(self, message, **extra):
        data = {"message": message, "role_id": "r002", "conversation_id": self.conversation_id, **extra}
        return self.app.post("/chat", data=data)

    def test_reply_uses_role_prompt_and_history(self):
        self.assertIn("返事1", self.send("豊太郎は悪い人？").text)
        self.send("なぜ？")
        last = self.client.models.calls[-1]
        self.assertEqual([("user", "豊太郎は悪い人？"), ("model", "返事1"), ("user", "なぜ？")], last["contents"])
        self.assertIn("エリスとの約束を重んじる読者", last["system"])
        self.assertIn("[p046]", last["system"])

    def test_ai_errors_return_status_and_are_not_saved(self):
        self.client.models.error_code = 429
        self.assertEqual(429, self.send("こんにちは").status_code)
        self.client.models.error_code = 500
        self.assertEqual(502, self.send("こんにちは").status_code)
        self.assertEqual([], chat.read_history(self.conversation_id))

    def test_invalid_requests_are_rejected(self):
        self.assertEqual(400, self.send("x", conversation_id="abc").status_code)
        self.assertEqual(400, self.send("x", role_id="unknown").status_code)
        self.assertEqual(400, self.send("   ").status_code)
        self.assertEqual(422, self.send("あ" * 201).status_code)
        self.assertEqual([], self.client.models.calls)

    def test_history_is_capped(self):
        for i in range(15):
            self.send(f"発言{i}")
        self.assertEqual(chat.HISTORY_MAX, len(chat.read_history(self.conversation_id)))
        self.assertEqual(("user", "発言14"), self.client.models.calls[-1]["contents"][-1])
        self.assertEqual(chat.HISTORY_MAX + 1, len(self.client.models.calls[-1]["contents"]))

    def test_conversations_are_separate(self):
        self.send("A の発言")
        other = self.app.get("/student/chat?role_id=r002").text
        other_id = re.search(r'name="conversation_id" value="([0-9a-f]{32})"', other).group(1)
        self.send("B の発言", conversation_id=other_id)
        self.assertEqual([("user", "B の発言")], self.client.models.calls[-1]["contents"])

    def test_evidence_is_checked_shown_and_saved(self):
        self.client.models.evidence = [
            {"paragraph_id": "p046", "quote": "棄て難きはエリスが愛"},
            {"paragraph_id": "p046", "quote": "本文にない作り話"},
        ]
        html = self.send("豊太郎はエリスをどう思っていた？").text
        self.assertIn("根拠を表示する", html)
        self.assertIn("棄て難きはエリスが愛", html)
        self.assertIn("第46段落", html)
        self.assertNotIn("本文にない作り話", html)
        self.assertIn("【出力の形】", self.client.models.calls[-1]["system"])

    def test_no_evidence_hides_button(self):
        self.assertNotIn("根拠を表示する", self.send("こんにちは").text)

    def test_broken_json_is_an_error(self):
        self.client.models.generate_content = lambda model, contents, config: FakeResponse("JSONではない")
        self.assertEqual(502, self.send("こんにちは").status_code)
        self.assertEqual([], chat.read_history(self.conversation_id))

    def test_message_is_escaped(self):
        self.assertIn("&lt;script&gt;", self.send("<script>").text)


if __name__ == "__main__":
    unittest.main()
