"""連続送信の制限（#35）のテスト。backend フォルダで python -m unittest discover -s tests"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import ai  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RateLimiterTests(unittest.TestCase):
    def test_limit_and_window(self):
        clock = FakeClock()
        limiter = RateLimiter(2, 60, clock=clock)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        # ほかのキーは別に数える
        self.assertTrue(limiter.allow("b"))
        # 60秒たてば、また送れる
        clock.now = 60.0
        self.assertTrue(limiter.allow("a"))


class FakeModels:
    def generate_content(self, model, contents, config):
        return type("Response", (), {"text": "返事"})()


class ChatRateLimitTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        ai._client = type("Client", (), {"models": FakeModels()})()
        chat.conversation_limiter = RateLimiter(2, 60)
        chat.global_limiter = RateLimiter(3, 60)
        self.app = TestClient(main.app)

    def tearDown(self):
        ai._client = None

    def new_conversation(self):
        page = self.app.get("/student/chat?role_id=r001").text
        return re.search(r'name="conversation_id" value="([0-9a-f]{32})"', page).group(1)

    def send(self, conversation_id):
        return self.app.post("/chat", data={"message": "こんにちは", "role_id": "r001", "conversation_id": conversation_id}).status_code

    def test_one_conversation_is_limited(self):
        first = self.new_conversation()
        self.assertEqual([200, 200, 429], [self.send(first) for _ in range(3)])

    def test_whole_app_is_limited(self):
        statuses = [self.send(self.new_conversation()) for _ in range(4)]
        self.assertEqual([200, 200, 200, 429], statuses)


if __name__ == "__main__":
    unittest.main()
